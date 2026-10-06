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

from .capabilities import CapabilityManifest
from .config import DisconnectPolicy, FleetConnectorConfig
from .journal import CommandJournal, JournalResult

logger = logging.getLogger(__name__)


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
    ) -> None:
        self.config = config
        self.handlers = handlers
        self.capabilities = capabilities
        self.telemetry_provider = telemetry_provider
        self.journal = CommandJournal(config.journal_path)
        self._sleep = sleep
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
        backoff = 1.0
        while self._running:
            try:
                await self._connect_once()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("FleetControl disconnected: %s; retrying in %.1fs", exc, backoff)
                await self._apply_disconnect_policy()
                await self._sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def stop(self) -> None:
        self._running = False
        await self._apply_disconnect_policy()
        self.journal.close()

    async def _connect_once(self) -> None:
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
                    task = asyncio.create_task(self._handle_command(message))
                    self._active_commands.add(task)
                    task.add_done_callback(self._active_commands.discard)
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
        try:
            if payload == "execute_task":
                timeout = max(1.0, (command.deadline_ms / 1000) - time.time())
                result = await self.handlers.execute_task(command.instruction, timeout)
            elif payload == "execute_skill":
                timeout = max(1.0, (command.deadline_ms / 1000) - time.time())
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
            await asyncio.gather(*tuple(self._active_commands), return_exceptions=True)
        else:
            await self.handlers.emergency_stop("Maestro disconnected")
            for task in tuple(self._active_commands):
                task.cancel()
