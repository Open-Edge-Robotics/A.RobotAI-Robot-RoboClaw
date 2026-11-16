"""Outbound FleetControl client for a Robo-Claw runtime."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import grpc

from robo_claw_grpc import fleet_control_pb2 as pb2
from robo_claw_grpc import fleet_control_pb2_grpc as pb2_grpc

from .capabilities import CapabilityManifest, capabilities_from_skills
from .config import DisconnectPolicy, FleetConnectorConfig
from .journal import CommandJournal, JournalResult

logger = logging.getLogger(__name__)

# Maestro 가 deadline 을 비우거나(0) 비정상 값을 보낼 때의 실행 timeout 정책.
# 예전에는 조용히 1초로 깎아 정상 명령까지 즉시 잘렸으므로, 명시적 기본값을 쓰고
# 계산된 남은 시간은 [MIN, MAX] 로 클램프하며 클램프 이유를 로그로 남긴다.
DEFAULT_COMMAND_DEADLINE_SEC = 60.0
MIN_COMMAND_DEADLINE_SEC = 1.0
MAX_COMMAND_DEADLINE_SEC = 600.0

# 동시에 실행 중인 command 상한. Maestro 가 몰아서 보낼 때 로봇측 task 무한 증식을
# 막는다. emergency_stop/cancel_task 는 안전 경로이므로 이 상한을 우회한다.
MAX_ACTIVE_COMMANDS = 32

# disconnect policy COMPLETE 에서 활성 command 완료를 기다리는 최대 시간.
DISCONNECT_WAIT_SEC = 30.0

# 재연결 backoff: 시작 1초, 2배씩 증가, 최대 30초, 연속 실패 상한까지 시도한다.
RECONNECT_INITIAL_DELAY_SEC = 1.0
RECONNECT_MAX_DELAY_SEC = 30.0
MAX_RECONNECT_ATTEMPTS = 20

_DEADLINE_PAYLOADS = frozenset({"execute_task", "execute_skill"})
_UNCAPPED_COMMAND_PAYLOADS = frozenset({"emergency_stop", "cancel_task"})


class RobotCommandHandlers(Protocol):
    async def execute_task(self, instruction: str, timeout_sec: float) -> dict[str, Any]: ...
    async def execute_skill(
        self, name: str, params: dict, timeout_sec: float
    ) -> dict[str, Any]: ...
    async def emergency_stop(self, reason: str) -> dict[str, Any]: ...
    async def request_snapshot(self) -> dict[str, Any]: ...
    async def cancel_task(self, task_id: str) -> dict[str, Any]: ...


class FleetConnector:
    def __init__(
        self,
        config: FleetConnectorConfig,
        handlers: RobotCommandHandlers,
        capabilities: list[CapabilityManifest],
        telemetry_provider,
        *,
        sleep=asyncio.sleep,
        capability_wait_sec: float = 60.0,
        capability_poll_sec: float = 2.0,
        max_active_commands: int = MAX_ACTIVE_COMMANDS,
        disconnect_wait_sec: float = DISCONNECT_WAIT_SEC,
        max_reconnect_attempts: int = MAX_RECONNECT_ATTEMPTS,
    ) -> None:
        self.config = config
        self.handlers = handlers
        self.capabilities = capabilities
        self.telemetry_provider = telemetry_provider
        self.journal = CommandJournal(config.journal_path)
        self._sleep = sleep
        self._capability_wait_sec = capability_wait_sec
        self._capability_poll_sec = capability_poll_sec
        self._max_active_commands = max(1, int(max_active_commands))
        self._disconnect_wait_sec = max(0.0, float(disconnect_wait_sec))
        self._max_reconnect_attempts = max(1, int(max_reconnect_attempts))
        self._outbound: asyncio.Queue = asyncio.Queue(maxsize=128)
        self._running = False
        self._active_commands: set[asyncio.Task] = set()
        self.session_id = uuid4().hex

    def _channel(self):
        if not self.config.tls:
            logger.warning("FleetControl is using an insecure development connection")
            return grpc.aio.insecure_channel(self.config.target)
        root = Path(self.config.ca_cert).read_bytes()
        cert = Path(self.config.client_cert).read_bytes()
        key = Path(self.config.client_key).read_bytes()
        return grpc.aio.secure_channel(
            self.config.target,
            grpc.ssl_channel_credentials(root, key, cert),
        )

    async def run(self) -> None:
        if not self.config.enabled:
            return
        self._running = True
        backoff = RECONNECT_INITIAL_DELAY_SEC
        failures = 0
        while self._running:
            try:
                await self._connect_once()
                backoff = RECONNECT_INITIAL_DELAY_SEC
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                await self._apply_disconnect_policy()
                if failures >= self._max_reconnect_attempts:
                    logger.error(
                        "FleetControl giving up after %d reconnect attempts: %s", failures, exc
                    )
                    self._running = False
                    return
                logger.warning("FleetControl disconnected: %s; retrying in %.1fs", exc, backoff)
                await self._sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_DELAY_SEC)

    async def stop(self) -> None:
        self._running = False
        await self._apply_disconnect_policy()
        self.journal.close()

    async def refresh_capabilities(self) -> str:
        """Replace the manifest with the agent's registered skills when available.

        The agent node may start after this connector, so poll for a bounded time
        before falling back to the SKILLS.md manifest. Returns "runtime" or "file".
        """
        list_skills = getattr(self.handlers, "list_skills", None)
        if list_skills is None:
            return "file"
        attempts = max(1, int(self._capability_wait_sec / self._capability_poll_sec) + 1)
        for attempt in range(attempts):
            skills = await list_skills()
            if skills:
                self.capabilities = capabilities_from_skills(skills)
                logger.info(
                    "FleetControl capabilities from agent query_state: %d skills",
                    len(self.capabilities),
                )
                return "runtime"
            if attempt < attempts - 1:
                await self._sleep(self._capability_poll_sec)
        logger.warning(
            "Agent query_state unavailable after %.0fs; using SKILLS.md manifest (%d capabilities)",
            self._capability_wait_sec,
            len(self.capabilities),
        )
        return "file"

    async def _connect_once(self) -> None:
        await self.refresh_capabilities()
        channel = self._channel()
        producer = asyncio.create_task(self._telemetry_loop())
        try:
            stub = pb2_grpc.FleetControlStub(channel)
            call = stub.Connect(self._requests())
            async for message in call:
                payload = message.WhichOneof("payload")
                if payload == "registration_result":
                    if not message.registration_result.accepted:
                        raise RuntimeError(message.registration_result.message)
                    await self._replay_results()
                elif payload in {
                    "execute_task",
                    "execute_skill",
                    "emergency_stop",
                    "request_snapshot",
                    "cancel_task",
                }:
                    await self._start_command(message)
            raise ConnectionError("FleetControl stream ended")
        finally:
            producer.cancel()
            await channel.close()

    async def _requests(self):
        yield self._envelope(
            register=pb2.Register(
                session_id=self.session_id,
                descriptor=pb2.RobotDescriptor(
                    robot_id=self.config.robot_id,
                    display_name=self.config.robot_id,
                    robot_type="robo-claw",
                    protocol_version="1",
                    site_id=self.config.site_id,
                    map_id=self.config.map_id,
                    map_version=self.config.map_version,
                    frame_id=self.config.map_frame_id,
                    capabilities=[
                        pb2.Capability(
                            name=item.name,
                            version=item.version,
                            risk_level=item.risk_level,
                            input_schema_json=item.input_schema_json,
                            requirements=item.requirements,
                        )
                        for item in self.capabilities
                    ],
                ),
            )
        )
        while self._running:
            yield await self._outbound.get()

    def _envelope(self, **payload):
        return pb2.RobotToMaestro(
            protocol_version="1",
            robot_id=self.config.robot_id,
            session_id=self.session_id,
            **payload,
        )

    async def _telemetry_loop(self) -> None:
        while self._running:
            now_ms = int(time.time() * 1000)
            await self._outbound.put(self._envelope(heartbeat=pb2.Heartbeat(timestamp_ms=now_ms)))
            data = self.telemetry_provider() or {}
            await self._outbound.put(
                self._envelope(
                    telemetry=pb2.Telemetry(
                        battery_percentage=float(data.get("battery_percentage", 0)),
                        x=float(data.get("x", 0)),
                        y=float(data.get("y", 0)),
                        z=float(data.get("z", 0)),
                        yaw=float(data.get("yaw", 0)),
                        frame_id=str(data.get("frame_id", self.config.map_frame_id)),
                        map_id=str(data.get("map_id", self.config.map_id)),
                        operational_state=str(data.get("operational_state", "UNKNOWN")),
                        current_task_id=str(data.get("current_task_id", "")),
                        current_skill=str(data.get("current_skill", "")),
                        queue_depth=int(data.get("queue_depth", 0)),
                        health_issues=list(data.get("health_issues", [])),
                        observed_at_ms=now_ms,
                    )
                )
            )
            await self._sleep(self.config.heartbeat_sec)

    async def _start_command(self, message) -> None:
        """동시 실행 상한 안에서만 새 command task 를 띄운다.

        emergency_stop/cancel_task 는 안전 경로이므로 상한에 걸려도 거부하지 않는다.
        상한 초과는 accepted=False ack 로 Maestro 에게 명시적으로 알린다.
        """
        payload = message.WhichOneof("payload")
        if (
            payload not in _UNCAPPED_COMMAND_PAYLOADS
            and len(self._active_commands) >= self._max_active_commands
        ):
            logger.warning(
                "FleetControl rejecting command %s: command limit %d reached (%d running)",
                message.command_id,
                self._max_active_commands,
                len(self._active_commands),
            )
            await self._outbound.put(
                self._envelope(
                    command_ack=pb2.CommandAck(
                        command_id=message.command_id,
                        accepted=False,
                        error_code="COMMAND_LIMIT_REACHED",
                        message=f"command limit {self._max_active_commands} reached",
                    )
                )
            )
            return
        task = asyncio.create_task(self._handle_command(message))
        self._active_commands.add(task)
        task.add_done_callback(self._active_commands.discard)

    def _command_timeout(self, deadline_ms: int) -> tuple[float, str]:
        """Maestro deadline 을 실행 timeout 과 거부 사유로 바꾼다.

        반환하는 거부 사유가 비어 있지 않으면 호출자는 명령을 실행하지 않고 실패
        결과로 남긴다. deadline 은 지난 시점이면 거부하고, 범위를 벗어난 값은
        클램프하면서 이유를 로그로 남긴다.
        """
        if deadline_ms <= 0:
            logger.warning(
                "FleetControl command has no deadline; using default %.0fs",
                DEFAULT_COMMAND_DEADLINE_SEC,
            )
            return DEFAULT_COMMAND_DEADLINE_SEC, ""
        remaining = (deadline_ms / 1000.0) - time.time()
        if remaining <= 0:
            logger.warning(
                "FleetControl command rejected: DEADLINE_EXPIRED (%.1fs past deadline)",
                -remaining,
            )
            return 0.0, "DEADLINE_EXPIRED"
        if remaining > MAX_COMMAND_DEADLINE_SEC:
            logger.warning(
                "FleetControl command deadline %.0fs clamped to %.0fs",
                remaining,
                MAX_COMMAND_DEADLINE_SEC,
            )
            return MAX_COMMAND_DEADLINE_SEC, ""
        if remaining < MIN_COMMAND_DEADLINE_SEC:
            logger.warning(
                "FleetControl command deadline %.2fs clamped to %.0fs",
                remaining,
                MIN_COMMAND_DEADLINE_SEC,
            )
            return MIN_COMMAND_DEADLINE_SEC, ""
        return remaining, ""

    async def _reject_command(self, key: str, command_id: str, reason: str) -> None:
        """계약을 위반한 명령을 실행하지 않고 사유가 남은 실패 결과로 기록한다."""
        self.journal.complete(
            key,
            status="FAILED",
            message=f"command rejected: {reason}",
            result={"success": False, "error_code": reason, "message": reason},
            error_code=reason,
        )
        rejected = self.journal.get(key)
        if rejected:
            await self._send_result(rejected, command_id=command_id)

    async def _handle_command(self, message) -> None:
        payload = message.WhichOneof("payload")
        command = getattr(message, payload)
        key = getattr(command, "idempotency_key", "") or message.command_id
        task_id = getattr(command, "task_id", "") or payload
        existing = self.journal.get(key)
        await self._outbound.put(
            self._envelope(command_ack=pb2.CommandAck(command_id=message.command_id, accepted=True))
        )
        if existing and existing.status != "RUNNING":
            await self._send_result(existing, command_id=message.command_id)
            return
        if not self.journal.begin(key, message.command_id, task_id):
            return
        timeout = 0.0
        if payload in _DEADLINE_PAYLOADS:
            timeout, rejection = self._command_timeout(command.deadline_ms)
            if rejection:
                await self._reject_command(key, message.command_id, rejection)
                return
        try:
            if payload == "execute_task":
                result = await self.handlers.execute_task(command.instruction, timeout)
            elif payload == "execute_skill":
                result = await self.handlers.execute_skill(
                    command.skill_name, json.loads(command.params_json or "{}"), timeout
                )
            elif payload == "emergency_stop":
                result = await self.handlers.emergency_stop(command.reason)
            elif payload == "request_snapshot":
                result = await self.handlers.request_snapshot()
            else:
                result = await self.handlers.cancel_task(command.task_id)
            success = bool(result.get("success", False))
            status = "SUCCEEDED" if success else "FAILED"
            message_text = str(result.get("message", status.lower()))
            error_code = str(result.get("error_code", ""))
            self.journal.complete(
                key,
                status=status,
                message=message_text,
                result=result,
                error_code=error_code,
            )
        except Exception as exc:
            self.journal.complete(
                key, status="FAILED", message=str(exc), result={}, error_code="LOCAL_ERROR"
            )
        completed = self.journal.get(key)
        if completed:
            await self._send_result(completed, command_id=message.command_id)

    async def _send_result(self, result: JournalResult, command_id: str | None = None) -> None:
        await self._outbound.put(
            self._envelope(
                task_update=pb2.TaskUpdate(
                    command_id=command_id or result.command_id,
                    task_id=result.task_id,
                    status=result.status,
                    progress=100 if result.status == "SUCCEEDED" else 0,
                    message=result.message,
                    result_json=result.result_json,
                    error_code=result.error_code,
                )
            )
        )
        # Keep terminal results replayable across reconnects. Maestro de-duplicates
        # TaskUpdate by command/task identity; an explicit receipt can mark these
        # delivered in a future protocol revision.

    async def _replay_results(self) -> None:
        for result in self.journal.undelivered():
            await self._send_result(result)

    async def _apply_disconnect_policy(self) -> None:
        if not self._active_commands:
            return
        if self.config.disconnect_policy in {
            DisconnectPolicy.COMPLETE,
            DisconnectPolicy.FINISH_ATOMIC,
        }:
            # A FleetControl command is the connector's atomic execution unit.
            # Local multi-step policies remain owned by the Robo-Claw agent.
            await self._wait_for_active_commands()
        else:
            await self.handlers.emergency_stop("Maestro disconnected")
            for task in tuple(self._active_commands):
                task.cancel()

    async def _wait_for_active_commands(self) -> None:
        """활성 command 완료를 유한 시간까지만 기다리고, 남은 task 는 취소한다.

        무제한 대기는 Maestro 가 사라진 뒤 connector 종료를 영원히 막으므로 상한을 둔다.
        """
        tasks = tuple(self._active_commands)
        if not tasks:
            return
        completed, pending = await asyncio.wait(tasks, timeout=self._disconnect_wait_sec)
        # 완료된 task 의 예외를 회수해 asyncio 의 "never retrieved" 경고를 막는다.
        await asyncio.gather(*completed, return_exceptions=True)
        if not pending:
            return
        logger.warning(
            "FleetControl disconnect wait timed out after %.0fs; cancelling %d command(s)",
            self._disconnect_wait_sec,
            len(pending),
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
