"""Fleet connector 의 보존·동시성·deadline·재연결 상한 검증."""

import asyncio
import time
from pathlib import Path

import pytest
from robo_claw_grpc.fleet_connector.client import (
    DEFAULT_COMMAND_DEADLINE_SEC,
    MAX_ACTIVE_COMMANDS,
    MAX_COMMAND_DEADLINE_SEC,
    MIN_COMMAND_DEADLINE_SEC,
    RECONNECT_INITIAL_DELAY_SEC,
    RECONNECT_MAX_DELAY_SEC,
    FleetConnector,
)
from robo_claw_grpc.fleet_connector.config import FleetConnectorConfig
from robo_claw_grpc.fleet_connector.journal import CommandJournal

from robo_claw_grpc import fleet_control_pb2 as pb2

pytestmark = pytest.mark.unit


class RecordingHandlers:
    """핸들러에 전달된 timeout 만 기록하고 즉시 성공을 돌려준다."""

    def __init__(self):
        self.skill_calls = 0
        self.timeouts: list[float] = []

    async def execute_task(self, instruction, timeout_sec):
        self.timeouts.append(timeout_sec)
        return {"success": True, "message": instruction}

    async def execute_skill(self, name, params, timeout_sec):
        self.skill_calls += 1
        self.timeouts.append(timeout_sec)
        return {"success": True, "message": name}

    async def emergency_stop(self, reason):
        return {"success": True, "message": reason}

    async def request_snapshot(self):
        return {"success": True, "image_base64": "abc"}

    async def cancel_task(self, task_id):
        return {"success": False, "error_code": "CANCEL_UNSUPPORTED"}


def config(tmp_path: Path) -> FleetConnectorConfig:
    return FleetConnectorConfig(
        maestro_ip="127.0.0.1",
        robot_port=50053,
        robot_id="robot-1",
        maestro_enabled=True,
        journal_path=str(tmp_path / "commands.db"),
    )


def skill_command(deadline_ms: int, command_id: str = "command-1") -> pb2.MaestroToRobot:
    return pb2.MaestroToRobot(
        protocol_version="1",
        command_id=command_id,
        execute_skill=pb2.ExecuteSkillCommand(
            task_id="task-1",
            idempotency_key=f"key-{command_id}",
            skill_name="get_status",
            params_json="{}",
            deadline_ms=deadline_ms,
        ),
    )


async def no_sleep(_seconds):
    return None


def test_command_journal_prunes_rows_beyond_retention(tmp_path):
    journal = CommandJournal(str(tmp_path / "commands.db"), retention_rows=2)
    for index in range(5):
        assert journal.begin(f"key-{index}", f"command-{index}", "task-1")
        journal.complete(f"key-{index}", status="SUCCEEDED", message="done", result={})

    assert journal.get("key-0") is None
    assert journal.get("key-4") is not None
    assert len(journal.undelivered()) == 2
    journal.close()


def test_command_journal_undelivered_is_bounded_to_newest(tmp_path):
    journal = CommandJournal(str(tmp_path / "commands.db"), undelivered_limit=2)
    for index in range(4):
        journal.begin(f"key-{index}", f"command-{index}", "task-1")
        journal.complete(f"key-{index}", status="SUCCEEDED", message="done", result={})

    keys = [item.idempotency_key for item in journal.undelivered()]

    assert keys == ["key-3", "key-2"]
    journal.close()


def test_missing_deadline_uses_explicit_default(tmp_path, caplog):
    async def scenario():
        handlers = RecordingHandlers()
        connector = FleetConnector(config(tmp_path), handlers, [], lambda: {})

        with caplog.at_level("WARNING"):
            await connector._handle_command(skill_command(deadline_ms=0))

        assert handlers.timeouts == [DEFAULT_COMMAND_DEADLINE_SEC]
        assert "default" in caplog.text
        connector.journal.close()

    asyncio.run(scenario())


def test_deadline_beyond_cap_is_clamped_to_maximum(tmp_path, caplog):
    async def scenario():
        handlers = RecordingHandlers()
        connector = FleetConnector(config(tmp_path), handlers, [], lambda: {})
        deadline_ms = int((time.time() + 10_000) * 1000)

        with caplog.at_level("WARNING"):
            await connector._handle_command(skill_command(deadline_ms=deadline_ms))

        assert handlers.timeouts == [MAX_COMMAND_DEADLINE_SEC]
        assert "clamp" in caplog.text
        connector.journal.close()

    asyncio.run(scenario())


def test_deadline_below_floor_is_clamped_to_minimum(tmp_path, caplog):
    async def scenario():
        handlers = RecordingHandlers()
        connector = FleetConnector(config(tmp_path), handlers, [], lambda: {})
        deadline_ms = int((time.time() + 0.1) * 1000)

        with caplog.at_level("WARNING"):
            await connector._handle_command(skill_command(deadline_ms=deadline_ms))

        assert handlers.timeouts == [MIN_COMMAND_DEADLINE_SEC]
        assert "clamp" in caplog.text
        connector.journal.close()

    asyncio.run(scenario())


def test_expired_deadline_is_rejected_without_execution(tmp_path, caplog):
    async def scenario():
        handlers = RecordingHandlers()
        connector = FleetConnector(config(tmp_path), handlers, [], lambda: {})
        deadline_ms = int((time.time() - 5) * 1000)

        with caplog.at_level("WARNING"):
            await connector._handle_command(skill_command(deadline_ms=deadline_ms))
            messages = [await connector._outbound.get() for _ in range(2)]

        assert handlers.skill_calls == 0
        assert [item.WhichOneof("payload") for item in messages] == [
            "command_ack",
            "task_update",
        ]
        update = messages[1].task_update
        assert update.status == "FAILED"
        assert update.error_code == "DEADLINE_EXPIRED"
        stored = connector.journal.get("key-command-1")
        assert stored is not None
        assert (stored.status, stored.error_code) == ("FAILED", "DEADLINE_EXPIRED")
        assert "DEADLINE_EXPIRED" in caplog.text
        connector.journal.close()

    asyncio.run(scenario())


def test_command_over_concurrency_limit_is_rejected(tmp_path, caplog):
    async def scenario():
        handlers = RecordingHandlers()
        connector = FleetConnector(
            config(tmp_path), handlers, [], lambda: {}, max_active_commands=1
        )
        blocker = asyncio.create_task(asyncio.sleep(3600))
        connector._active_commands.add(blocker)
        try:
            with caplog.at_level("WARNING"):
                await connector._start_command(skill_command(int(time.time() * 1000) + 60_000))
            message = await connector._outbound.get()
        finally:
            blocker.cancel()
            await asyncio.gather(blocker, return_exceptions=True)

        ack = message.command_ack
        assert ack.accepted is False
        assert ack.error_code == "COMMAND_LIMIT_REACHED"
        assert connector._active_commands == {blocker}
        assert "command limit" in caplog.text.lower()
        connector.journal.close()

    asyncio.run(scenario())


def test_default_concurrency_limit_is_positive():
    assert MAX_ACTIVE_COMMANDS > 1


def test_emergency_stop_bypasses_concurrency_limit(tmp_path):
    async def scenario():
        handlers = RecordingHandlers()
        connector = FleetConnector(
            config(tmp_path), handlers, [], lambda: {}, max_active_commands=1
        )
        blocker = asyncio.create_task(asyncio.sleep(3600))
        connector._active_commands.add(blocker)
        before = set(connector._active_commands)
        command = pb2.MaestroToRobot(
            protocol_version="1",
            command_id="stop-1",
            emergency_stop=pb2.EmergencyStopCommand(reason="operator"),
        )
        try:
            await connector._start_command(command)
            started = connector._active_commands - before
            assert len(started) == 1
            await asyncio.gather(*started)
        finally:
            blocker.cancel()
            await asyncio.gather(blocker, return_exceptions=True)

        payloads = [connector._outbound.get_nowait().WhichOneof("payload") for _ in range(2)]
        assert payloads == ["command_ack", "task_update"]
        connector.journal.close()

    asyncio.run(scenario())


def test_complete_disconnect_wait_is_bounded(tmp_path, caplog):
    async def scenario():
        handlers = RecordingHandlers()
        connector = FleetConnector(
            config(tmp_path), handlers, [], lambda: {}, disconnect_wait_sec=0.01
        )
        stuck = asyncio.create_task(asyncio.sleep(3600))
        connector._active_commands.add(stuck)
        connector._active_commands.add(asyncio.create_task(asyncio.sleep(3600)))
        try:
            with caplog.at_level("WARNING"):
                await asyncio.wait_for(connector._apply_disconnect_policy(), 1.0)
        finally:
            for task in tuple(connector._active_commands):
                task.cancel()
            await asyncio.gather(*tuple(connector._active_commands), return_exceptions=True)

        assert "timed out" in caplog.text
        connector.journal.close()

    asyncio.run(scenario())


def test_reconnect_backoff_is_capped_and_attempts_are_bounded(tmp_path, caplog):
    async def scenario():
        connector = FleetConnector(
            config(tmp_path),
            RecordingHandlers(),
            [],
            lambda: {},
            max_reconnect_attempts=8,
        )
        delays: list[float] = []

        async def sleeping(seconds):
            delays.append(seconds)

        async def always_failing():
            raise ConnectionError("maestro unreachable")

        connector._sleep = sleeping
        connector._connect_once = always_failing

        with caplog.at_level("WARNING"):
            await asyncio.wait_for(connector.run(), 5.0)
            log = caplog.text

        connector.journal.close()
        return connector, delays, log

    connector, delays, log = asyncio.run(scenario())

    assert delays == [
        RECONNECT_INITIAL_DELAY_SEC,
        2.0,
        4.0,
        8.0,
        16.0,
        RECONNECT_MAX_DELAY_SEC,
        RECONNECT_MAX_DELAY_SEC,
    ]
    assert connector._running is False
    assert "giving up" in log
