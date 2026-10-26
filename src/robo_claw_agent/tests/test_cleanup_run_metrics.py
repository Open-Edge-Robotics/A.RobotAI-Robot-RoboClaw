"""정리 태스크 실험 계측(JSONL run record) 검증"""

import json
import os

import pytest

pytestmark = pytest.mark.unit

from _autonomous_cleanup_helpers import (  # noqa: F401
    MagicMock,
    NodeStatus,
    ResolveCleanupHandoff,
    _node_with_skills,
)
from _tidy_home_helpers import TidyHomeSkill, _place  # noqa: F401


def _read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------------------------------------------------------- 기록 자체


def test_run_log_appends_one_jsonl_record_per_event(tmp_path, monkeypatch):
    from robo_claw_agent.skills import cleanup_run_metrics

    path = tmp_path / "cleanup_runs.jsonl"
    monkeypatch.setenv(cleanup_run_metrics.ENV_RUN_LOG_PATH, str(path))

    cleanup_run_metrics.record_cleanup_handoff(run_id="r1", place="안방", peer="cloid2")
    cleanup_run_metrics.record_cleanup_run(
        run_id="r1", results=[{"place": "안방", "status": "delegated_demo"}]
    )

    records = _read_jsonl(path)
    assert [record["event"] for record in records] == ["cleanup_handoff", "cleanup_run"]
    assert records[0]["run_id"] == "r1"
    assert records[1]["run_id"] == "r1"
    assert records[0]["iso"] and records[0]["ts"] > 0


def test_run_log_stays_quiet_without_path(tmp_path, monkeypatch):
    from robo_claw_agent.skills import cleanup_run_metrics

    monkeypatch.delenv(cleanup_run_metrics.ENV_RUN_LOG_PATH, raising=False)

    record = cleanup_run_metrics.record_cleanup_run(run_id="r1", results=[])

    assert record["event"] == "cleanup_run"
    assert cleanup_run_metrics.run_log_path() == ""
    assert list(tmp_path.iterdir()) == []


def test_run_log_write_failure_does_not_raise(monkeypatch):
    from robo_claw_agent.skills import cleanup_run_metrics

    # 파일이 올 수 없는 경로(디렉터리 대신 일반 파일)를 지정한다.
    monkeypatch.setenv(cleanup_run_metrics.ENV_RUN_LOG_PATH, "/proc/definitely/not/writable.jsonl")

    record = cleanup_run_metrics.record_cleanup_handoff(run_id="r1")

    assert record["event"] == "cleanup_handoff"


# ---------------------------------------------------------------- 판정 규칙


def test_run_success_requires_every_visited_place_to_be_handled():
    from robo_claw_agent.skills.cleanup_run_metrics import run_success

    assert run_success([{"place": "안방", "status": "delegated_demo"}]) is True
    assert run_success([{"place": "안방", "status": "handled"}]) is True
    assert run_success([{"place": "안방", "status": "delegated"}]) is True

    assert run_success([{"place": "안방", "status": "no_target"}]) is False
    assert run_success([{"place": "안방", "status": "unreachable"}]) is False
    assert run_success([{"place": "안방", "status": "unhandled"}]) is False
    # 한 곳이라도 처리되지 않았으면 전체 성공이 아니다.
    assert (
        run_success(
            [
                {"place": "안방", "status": "delegated_demo"},
                {"place": "거실", "status": "observation_failed"},
            ]
        )
        is False
    )
    assert run_success([]) is False


def test_outcome_counts_groups_place_statuses():
    from robo_claw_agent.skills.cleanup_run_metrics import outcome_counts

    counts = outcome_counts(
        [
            {"place": "안방", "status": "delegated_demo"},
            {"place": "거실", "status": "delegated_demo"},
            {"place": "주방", "status": "no_target"},
        ]
    )

    assert counts == {"delegated_demo": 2, "no_target": 1}


def test_peer_candidates_marks_selected_peer_and_keeps_capabilities():
    from robo_claw_agent.skills.cleanup_run_metrics import peer_candidates

    candidates = peer_candidates(
        {
            "cloid2": {"capabilities": ["cleanup_demo"], "role": "humanoid"},
            "former0047": {"capabilities": [], "role": "agv"},
        },
        selected="CLOID2",
    )

    assert [candidate["peer"] for candidate in candidates] == ["cloid2", "former0047"]
    assert candidates[0]["selected"] is True
    assert candidates[0]["capabilities"] == ["cleanup_demo"]
    assert candidates[1]["selected"] is False


# ---------------------------------------------------------------- 위임 결정 계측


def _enable_run_log(tmp_path, monkeypatch):
    from robo_claw_agent.skills import cleanup_run_metrics

    path = tmp_path / "cleanup_runs.jsonl"
    monkeypatch.setenv(cleanup_run_metrics.ENV_RUN_LOG_PATH, str(path))
    return path


def _events(path, event):
    return [record for record in _read_jsonl(path) if record["event"] == event]


def test_handoff_decision_records_selected_peer_and_candidates(tmp_path, monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes

    path = _enable_run_log(tmp_path, monkeypatch)
    monkeypatch.setattr(
        execution_nodes,
        "_find_connected_peer_with_capability",
        lambda _node, cap: (
            {"peer_name": "cloid2", "transport": "grpc"} if cap == "cleanup_demo" else None
        ),
    )
    monkeypatch.setattr(
        execution_nodes,
        "_discover_peer_agents",
        lambda _node: {"cloid2": {"capabilities": ["cleanup_demo"]}},
    )
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = {"x": 1.0, "y": 2.0}
    phase = {}

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "cleanup_run_id": "run-42",
        "cleanup_phase_ms": phase,
        "detected_objects": [{"name": "포장지"}],
        "scene_analysis": "안방 바닥에 포장지가 있습니다.",
        "patrol_last_place": "안방",
    }

    assert handoff.tick() == NodeStatus.SUCCESS

    records = _events(path, "cleanup_handoff")
    assert len(records) == 1
    record = records[0]
    assert record["run_id"] == "run-42"
    assert record["place"] == "안방"
    assert record["peer"] == "cloid2"
    assert record["transport"] == "grpc"
    assert record["send_path"] == "call_peer_robot"
    assert record["handoff_kind"] == "cleanup_demo"
    assert record["blocked"] is False
    assert record["candidates"][0]["selected"] is True
    assert phase["discovery"] >= 0.0


def test_handoff_decision_records_blocked_reason_when_no_peer(tmp_path, monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes

    path = _enable_run_log(tmp_path, monkeypatch)
    monkeypatch.setattr(
        execution_nodes, "_find_connected_peer_with_capability", lambda _node, _cap: None
    )
    monkeypatch.setattr(execution_nodes, "_discover_peer_agents", lambda _node: {})
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = None

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "cleanup_run_id": "run-43",
        "detected_objects": [{"name": "포장지"}],
        "scene_analysis": "안방 바닥에 포장지가 있습니다.",
        "patrol_last_place": "안방",
    }

    assert handoff.tick() == NodeStatus.SUCCESS

    assert handoff._blackboard["cleanup_handoff_blocked"] is True
    records = _events(path, "cleanup_handoff")
    assert len(records) == 1
    assert records[0]["run_id"] == "run-43"
    assert records[0]["blocked"] is True
    assert records[0]["peer"] == ""
    assert records[0]["reason"]


# ---------------------------------------------------------------- 실행 1회 계측


def test_tidy_home_run_records_outcomes_and_phase_ms(tmp_path, monkeypatch):
    from robo_claw_agent.skills.autonomous_skill.globals import _AUTONOMOUS_ACTIVE

    path = _enable_run_log(tmp_path, monkeypatch)
    skill = TidyHomeSkill()
    skill.node = MagicMock()
    skill.node.get_name.return_value = "robo_claw_agent_node"
    skill.send_user_message = lambda _message: None
    skill._visit_place = lambda _place_name: (True, "arrived")
    skill._analyze_current_scene = lambda _place_name: {
        "success": True,
        "partial": False,
        "analysis": "바닥에 수건이 있습니다.",
        "detected_objects": ["towel"],
    }
    skill._handle_cleanup_target = lambda **kwargs: "delegated_demo"
    _AUTONOMOUS_ACTIVE.set()

    skill._run_cleanup("room", [_place("안방", 1.0, 2.0)], "run-44", "안방")

    records = _events(path, "cleanup_run")
    assert len(records) == 1
    record = records[0]
    assert record["run_id"] == "run-44"
    assert record["target_location"] == "안방"
    assert record["results"] == [
        {"place": "안방", "status": "delegated_demo", "partial_observation": False}
    ]
    assert record["outcomes"] == {"delegated_demo": 1}
    assert record["success"] is True
    assert record["error"] == ""
    assert set(record["phase_ms"]) >= {"navigate", "observe", "handle"}
    assert record["elapsed_ms"] >= 0.0


def test_tidy_home_run_records_error_and_failed_success(tmp_path, monkeypatch):
    from robo_claw_agent.skills.autonomous_skill.globals import _AUTONOMOUS_ACTIVE

    path = _enable_run_log(tmp_path, monkeypatch)
    skill = TidyHomeSkill()
    skill.node = MagicMock()
    skill.send_user_message = lambda _message: None

    skill._visit_place = lambda _place_name: (True, "arrived")

    def explode(_place_name):
        raise RuntimeError("observe boom")

    skill._analyze_current_scene = explode
    _AUTONOMOUS_ACTIVE.set()

    skill._run_cleanup("room", [_place("안방", 1.0, 2.0)], "run-45", "안방")

    record = _events(path, "cleanup_run")[0]
    assert record["success"] is False
    assert "observe boom" in record["error"]


def test_handoff_place_passes_run_id_and_phase_ms_into_blackboard(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import decision_nodes, execution_nodes

    captured = {}

    def prepare_handoff(handoff):
        captured.update(handoff._blackboard)
        return NodeStatus.SUCCESS

    monkeypatch.setattr(execution_nodes.ResolveCleanupHandoff, "tick", prepare_handoff)
    monkeypatch.setattr(decision_nodes.LLMDecideAction, "tick", lambda _self: NodeStatus.SUCCESS)
    monkeypatch.setattr(
        decision_nodes.ValidateDecidedAction, "tick", lambda _self: NodeStatus.SUCCESS
    )
    monkeypatch.setattr(
        execution_nodes.ExecuteDecidedAction, "tick", lambda _self: NodeStatus.SUCCESS
    )
    skill = TidyHomeSkill()
    skill.node = MagicMock()
    skill.node._memory = None
    skill.send_user_message = lambda _message: None
    skill._move_aside_for_cleanup_demo_handoff = lambda: True

    phase = {"navigate": 1.0}
    skill._handle_cleanup_target(
        place_name="안방",
        scene="바닥에 수건이 있습니다.",
        objects=[{"name": "수건"}],
        cycle=1,
        run_id="run-46",
        phase_ms=phase,
    )

    assert captured["cleanup_run_id"] == "run-46"
    assert captured["cleanup_phase_ms"] is phase


def test_execute_decided_action_adds_delegate_phase_ms(monkeypatch):
    from types import SimpleNamespace

    from robo_claw_agent.agent_node import nav_safety
    from robo_claw_agent.skills.autonomous_skill import execution_nodes

    monkeypatch.setattr(
        nav_safety,
        "ensure_navigation_safety",
        lambda chain, *_args, **_kwargs: chain,
    )
    skill = MagicMock()
    skill.node._skills.execute.return_value = SimpleNamespace(
        success=True, message="ok", duration_sec=12.5, result_data={}
    )
    execute = execution_nodes.ExecuteDecidedAction(skill)
    phase = {}
    execute._blackboard = {
        "decided_skill": "call_peer_robot",
        "decided_params": {"peer_name": "cloid2", "instruction": "demo"},
        "cleanup_handoff_prepared": True,
        "cleanup_phase_ms": phase,
    }

    assert execute.tick() == NodeStatus.SUCCESS
    assert phase["delegate"] == pytest.approx(12.5)


def test_run_log_path_defaults_to_empty_string(monkeypatch):
    from robo_claw_agent.skills import cleanup_run_metrics

    monkeypatch.delenv(cleanup_run_metrics.ENV_RUN_LOG_PATH, raising=False)
    assert os.environ.get(cleanup_run_metrics.ENV_RUN_LOG_PATH) is None
    assert cleanup_run_metrics.run_log_path() == ""


def test_handoff_recording_failure_does_not_block_decision(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes

    def explode(**_fields):
        raise RuntimeError("record boom")

    monkeypatch.setattr(execution_nodes, "record_cleanup_handoff", explode)
    monkeypatch.setattr(
        execution_nodes,
        "_find_connected_peer_with_capability",
        lambda _node, cap: (
            {"peer_name": "cloid2", "transport": "grpc"} if cap == "cleanup_demo" else None
        ),
    )
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = None

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "cleanup_run_id": "run-47",
        "detected_objects": [{"name": "포장지"}],
        "scene_analysis": "안방 바닥에 포장지가 있습니다.",
        "patrol_last_place": "안방",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    assert handoff._blackboard["cleanup_handoff_prepared"] is True
    assert handoff._blackboard["decided_skill"] == "call_peer_robot"
    assert handoff._blackboard["decided_params"]["peer_name"] == "cloid2"


def test_run_recording_failure_still_releases_autonomous(monkeypatch):
    from robo_claw_agent.skills import tidy_home_skill
    from robo_claw_agent.skills.autonomous_skill import globals as autonomous_globals

    def explode(**_fields):
        raise RuntimeError("record boom")

    monkeypatch.setattr(tidy_home_skill, "record_cleanup_run", explode)
    skill = TidyHomeSkill()
    skill.node = MagicMock()
    skill.send_user_message = lambda _message: None
    skill._visit_place = lambda _place_name: (True, "arrived")
    skill._analyze_current_scene = lambda _place_name: {
        "success": True,
        "partial": False,
        "analysis": "바닥에 수건이 있습니다.",
        "detected_objects": ["towel"],
    }
    skill._handle_cleanup_target = lambda **kwargs: "delegated_demo"
    autonomous_globals._AUTONOMOUS_ACTIVE.set()

    skill._run_cleanup("room", [_place("안방", 1.0, 2.0)], "run-48", "안방")

    assert autonomous_globals._AUTONOMOUS_ACTIVE.is_set() is False


def test_cleanup_delegate_record_keeps_message_and_duration(tmp_path, monkeypatch):
    from robo_claw_agent.skills import cleanup_run_metrics

    path = _enable_run_log(tmp_path, monkeypatch)

    cleanup_run_metrics.record_cleanup_delegate(
        run_id="r1",
        place="안방",
        peer="cloid2",
        send_path="call_peer_robot",
        success=False,
        message="정리 시연 모션 실행이 거부되거나 실패했습니다.",
        duration_ms=12345.6,
    )

    record = _events(path, "cleanup_delegate")[0]
    assert record["run_id"] == "r1"
    assert record["peer"] == "cloid2"
    assert record["success"] is False
    assert record["message"].startswith("정리 시연 모션")
    assert record["duration_ms"] == pytest.approx(12345.6)


def test_execute_decided_action_records_delegate_result(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from robo_claw_agent.agent_node import nav_safety
    from robo_claw_agent.skills.autonomous_skill import execution_nodes

    path = _enable_run_log(tmp_path, monkeypatch)
    monkeypatch.setattr(
        nav_safety, "ensure_navigation_safety", lambda chain, *_args, **_kwargs: chain
    )
    skill = MagicMock()
    skill.node._skills.execute.return_value = SimpleNamespace(
        success=True,
        message="에이전트 [cloid2] 응답: 정리 시연 모션 실행을 요청했습니다.",
        duration_sec=7.5,
        result_data={},
    )
    execute = execution_nodes.ExecuteDecidedAction(skill)
    execute._blackboard = {
        "cleanup_run_id": "run-49",
        "patrol_last_place": "안방",
        "decided_skill": "call_peer_robot",
        "decided_params": {"peer_name": "cloid2", "instruction": "demo"},
        "cleanup_handoff_prepared": True,
        "cleanup_phase_ms": {},
    }

    assert execute.tick() == NodeStatus.SUCCESS

    record = _events(path, "cleanup_delegate")[0]
    assert record["run_id"] == "run-49"
    assert record["peer"] == "cloid2"
    assert record["place"] == "안방"
    assert record["send_path"] == "call_peer_robot"
    assert record["success"] is True
    assert record["duration_ms"] == pytest.approx(7500.0)
