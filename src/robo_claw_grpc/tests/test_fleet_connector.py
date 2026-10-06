import asyncio
from pathlib import Path

from robo_claw_grpc.fleet_connector.capabilities import build_capabilities
from robo_claw_grpc.fleet_connector.client import FleetConnector
from robo_claw_grpc.fleet_connector.config import DisconnectPolicy, FleetConnectorConfig
from robo_claw_grpc.fleet_connector.journal import CommandJournal

from robo_claw_grpc import fleet_control_pb2 as pb2


class FakeHandlers:
    def __init__(self):
        self.skill_calls = 0
        self.release = asyncio.Event()

    async def execute_task(self, instruction, timeout_sec):
        await self.release.wait()
        return {"success": True, "message": instruction}

    async def execute_skill(self, name, params, timeout_sec):
        self.skill_calls += 1
        return {"success": True, "message": name, "params": params}

    async def emergency_stop(self, reason):
        return {"success": True, "message": reason}

    async def request_snapshot(self):
        return {"success": True, "image_base64": "abc"}

    async def cancel_task(self, task_id):
        return {"success": False, "error_code": "CANCEL_UNSUPPORTED"}


def config(tmp_path: Path, policy=DisconnectPolicy.COMPLETE):
    return FleetConnectorConfig(
        maestro_ip="127.0.0.1",
        robot_port=50053,
        robot_id="robot-1",
        journal_path=str(tmp_path / "commands.db"),
        disconnect_policy=policy,
    )


def test_config_uses_required_environment_names(monkeypatch):
    monkeypatch.setenv("MAESTRO_IP", "10.0.0.1")
    monkeypatch.setenv("ROBOT_PORT", "50053")
    monkeypatch.setenv("ROBOT_ID", "former-1")
    monkeypatch.setenv("ROBOT_MAP_FRAME_ID", "map")
    monkeypatch.setenv("MAESTRO_DISCONNECT_POLICY", "complete")

    value = FleetConnectorConfig.from_env()

    assert value.target == "10.0.0.1:50053"
    assert value.robot_id == "former-1"
    assert value.map_frame_id == "map"
    assert value.disconnect_policy is DisconnectPolicy.COMPLETE


def test_capabilities_are_intersected_with_runtime_skills(tmp_path):
    skills = tmp_path / "SKILLS.md"
    skills.write_text(
        "| Navigation | `navigate_to` | Public | action | common | go |\n"
        "| System | `get_status` | Public | informational | common | status |\n"
        "| System | `not_installed` | Public | action | common | missing |\n",
        encoding="utf-8",
    )

    manifest = build_capabilities(str(skills), {"navigate_to", "get_status"})

    assert [(item.name, item.risk_level) for item in manifest] == [
        ("get_status", "read"),
        ("navigate_to", "motion"),
    ]


def test_command_journal_blocks_duplicates_after_restart(tmp_path):
    path = str(tmp_path / "commands.db")
    first = CommandJournal(path)
    assert first.begin("key-1", "command-1", "task-1")
    first.complete("key-1", status="SUCCEEDED", message="done", result={"x": 1})
    first.close()

    reopened = CommandJournal(path)
    assert not reopened.begin("key-1", "command-2", "task-1")
    assert reopened.get("key-1").status == "SUCCEEDED"
    assert reopened.undelivered()[0].result_json == '{"x": 1}'
    reopened.close()


def test_duplicate_skill_command_replays_result_without_execution(tmp_path):
    async def scenario():
        handlers = FakeHandlers()
        connector = FleetConnector(config(tmp_path), handlers, [], lambda: {})
        command = pb2.MaestroToRobot(
            protocol_version="1",
            command_id="command-1",
            execute_skill=pb2.ExecuteSkillCommand(
                task_id="task-1",
                idempotency_key="stable-key",
                skill_name="get_status",
                params_json="{}",
                deadline_ms=9999999999999,
            ),
        )

        await connector._handle_command(command)
        await connector._handle_command(command)

        messages = [await connector._outbound.get() for _ in range(4)]
        assert handlers.skill_calls == 1
        assert [item.WhichOneof("payload") for item in messages] == [
            "command_ack",
            "task_update",
            "command_ack",
            "task_update",
        ]
        connector.journal.close()

    asyncio.run(scenario())


def test_complete_disconnect_policy_waits_for_active_command(tmp_path):
    async def scenario():
        handlers = FakeHandlers()
        connector = FleetConnector(config(tmp_path), handlers, [], lambda: {})
        task = asyncio.create_task(handlers.execute_task("inspect", 10))
        connector._active_commands.add(task)
        waiting = asyncio.create_task(connector._apply_disconnect_policy())
        await asyncio.sleep(0)
        assert not waiting.done()
        handlers.release.set()
        await waiting
        connector.journal.close()

    asyncio.run(scenario())
