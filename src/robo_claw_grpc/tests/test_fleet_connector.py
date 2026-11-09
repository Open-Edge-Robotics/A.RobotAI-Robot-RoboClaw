import asyncio
from pathlib import Path

import pytest
from robo_claw_grpc.fleet_connector.capabilities import (
    CapabilityManifest,
    build_capabilities,
    capabilities_from_skills,
)
from robo_claw_grpc.fleet_connector.client import FleetConnector
from robo_claw_grpc.fleet_connector.config import DisconnectPolicy, FleetConnectorConfig
from robo_claw_grpc.fleet_connector.handlers import RoboClawCommandHandlers
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
        maestro_enabled=True,
        journal_path=str(tmp_path / "commands.db"),
        disconnect_policy=policy,
    )


def test_config_uses_required_environment_names(monkeypatch):
    monkeypatch.setenv("MAESTRO_ENABLED", "true")
    monkeypatch.setenv("MAESTRO_IP", "10.0.0.1")
    monkeypatch.setenv("ROBOT_PORT", "50053")
    monkeypatch.setenv("ROBOT_ID", "former-1")
    monkeypatch.setenv("ROBOT_MAP_FRAME_ID", "map")
    monkeypatch.setenv("MAESTRO_DISCONNECT_POLICY", "complete")

    value = FleetConnectorConfig.from_env()

    assert value.enabled is True
    assert value.target == "10.0.0.1:50053"
    assert value.robot_id == "former-1"
    assert value.map_frame_id == "map"
    assert value.disconnect_policy is DisconnectPolicy.COMPLETE


def test_config_is_disabled_without_maestro_enabled(monkeypatch):
    monkeypatch.setenv("MAESTRO_IP", "10.0.0.1")
    monkeypatch.setenv("ROBOT_ID", "former-1")
    monkeypatch.delenv("MAESTRO_ENABLED", raising=False)

    value = FleetConnectorConfig.from_env()

    assert value.maestro_enabled is False
    assert value.enabled is False


def test_config_requires_ip_when_enabled(monkeypatch):
    monkeypatch.setenv("MAESTRO_ENABLED", "true")
    monkeypatch.setenv("ROBOT_ID", "former-1")
    monkeypatch.delenv("MAESTRO_IP", raising=False)

    with pytest.raises(ValueError, match="MAESTRO_IP"):
        FleetConnectorConfig.from_env()


def test_config_requires_robot_id_when_enabled(monkeypatch):
    monkeypatch.setenv("MAESTRO_ENABLED", "true")
    monkeypatch.setenv("MAESTRO_IP", "10.0.0.1")
    monkeypatch.delenv("ROBOT_ID", raising=False)

    with pytest.raises(ValueError, match="ROBOT_ID"):
        FleetConnectorConfig.from_env()


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


def test_capabilities_use_only_the_skill_column_of_skill_tables(tmp_path):
    skills = tmp_path / "SKILLS.md"
    skills.write_text(
        "| 카테고리 | 스킬 | 공개 범위 | 위험도 |\n"
        "| --- | --- | --- | --- |\n"
        "| Navigation | `navigate_to` | Public | action |\n"
        "| System | `get_status` | Public | informational |\n"
        "\n"
        "## arm_pose 파라미터\n"
        "\n"
        "| 파라미터 | 값 | 설명 |\n"
        "| --- | --- | --- |\n"
        "| `pose_name` | `home` | 이름 있는 자세 |\n"
        "\n"
        "| 프레임 | 관절 |\n"
        "|:---|---:|\n"
        "| `base_link` | `joint1` |\n",
        encoding="utf-8",
    )

    manifest = build_capabilities(str(skills))

    assert [(item.name, item.risk_level) for item in manifest] == [
        ("get_status", "read"),
        ("navigate_to", "motion"),
    ]


def test_missing_skills_file_warns_and_reports_no_capabilities(tmp_path, caplog):
    missing = tmp_path / "config" / "SKILLS.md"

    with caplog.at_level("WARNING"):
        manifest = build_capabilities(str(missing))

    assert manifest == []
    assert str(missing) in caplog.text
    assert "capabilities" in caplog.text


def test_prose_skills_guide_without_tables_reports_no_capabilities(tmp_path):
    guide = tmp_path / "SKILLS.md"
    guide.write_text(
        "# CLOiD 2호 스킬 가이드\n\n"
        "`move_joints`, `grasp` 같은 일반 조작 스킬은 실행 단계에서 거부됩니다.\n"
        "`stow_for_navigation`은 카탈로그에서 제외됩니다.\n",
        encoding="utf-8",
    )

    assert build_capabilities(str(guide)) == []


def test_runtime_skills_become_capabilities_with_schema_and_risk():
    skills = [
        {"name": "get_status", "risk_level": "read", "input_schema": {"type": "object"}},
        {"name": "navigate_to", "risk_level": "action"},
        {"name": "tidy_home", "risk_level": "action"},
        {"name": "write_text_file", "risk_level": "write"},
        {"name": "run_butler_script", "risk_level": "dangerous"},
        {"name": "internal_helper", "risk_level": "action", "is_internal": True},
        {"name": "Bad Name", "risk_level": "action"},
    ]

    manifest = capabilities_from_skills(skills)

    assert [(item.name, item.risk_level) for item in manifest] == [
        ("get_status", "read"),
        ("navigate_to", "motion"),
        ("run_butler_script", "dangerous"),
        ("tidy_home", "normal"),
        ("write_text_file", "normal"),
    ]
    assert manifest[0].input_schema_json == '{"type": "object"}'
    assert manifest[1].input_schema_json == "{}"


class SkillListingHandlers(FakeHandlers):
    def __init__(self, responses):
        super().__init__()
        self.responses = list(responses)
        self.list_calls = 0

    async def list_skills(self):
        self.list_calls += 1
        return self.responses.pop(0) if self.responses else None


async def no_sleep(_seconds):
    return None


def test_connector_prefers_runtime_skills_once_agent_is_ready(tmp_path):
    async def scenario():
        handlers = SkillListingHandlers([None, [{"name": "get_status", "risk_level": "read"}]])
        connector = FleetConnector(
            config(tmp_path),
            handlers,
            [CapabilityManifest(name="stale_from_file")],
            lambda: {},
            sleep=no_sleep,
        )

        source = await connector.refresh_capabilities()

        assert source == "runtime"
        assert handlers.list_calls == 2
        assert [item.name for item in connector.capabilities] == ["get_status"]
        connector.journal.close()

    asyncio.run(scenario())


def test_connector_keeps_file_manifest_when_agent_never_answers(tmp_path, caplog):
    async def scenario():
        handlers = SkillListingHandlers([])
        connector = FleetConnector(
            config(tmp_path),
            handlers,
            [CapabilityManifest(name="from_file")],
            lambda: {},
            sleep=no_sleep,
            capability_wait_sec=4.0,
        )

        with caplog.at_level("WARNING"):
            source = await connector.refresh_capabilities()

        assert source == "file"
        assert handlers.list_calls >= 2
        assert [item.name for item in connector.capabilities] == ["from_file"]
        assert "query_state" in caplog.text
        connector.journal.close()

    asyncio.run(scenario())


class FakeQueryNode:
    def __init__(self, response):
        self.response = response

    async def call_query_state(self, timeout_sec=5.0):
        return self.response


class FakeQueryResponse:
    def __init__(self, success, state_json):
        self.success = success
        self.state_json = state_json


def test_handlers_read_skill_list_from_agent_query_state():
    ok = RoboClawCommandHandlers(
        FakeQueryNode(FakeQueryResponse(True, '{"skills": [{"name": "get_status"}]}'))
    )
    failed = RoboClawCommandHandlers(FakeQueryNode(FakeQueryResponse(False, "")))
    garbage = RoboClawCommandHandlers(FakeQueryNode(FakeQueryResponse(True, "not json")))
    unavailable = RoboClawCommandHandlers(FakeQueryNode(None))

    assert asyncio.run(ok.list_skills()) == [{"name": "get_status"}]
    for handlers in (failed, garbage, unavailable):
        assert asyncio.run(handlers.list_skills()) is None


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
