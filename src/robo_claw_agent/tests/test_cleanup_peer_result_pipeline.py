"""동료 시연 결과가 실제 gRPC/ROS 결과에서 BT 관측까지 전달되는지 검증.

가짜 dict 의 ``result_data.status`` 만 통과시키지 않는다. 실제
``build_task_result_json`` 문자열 → generated ``CallPeerRobot.Response`` →
실제 ``CallPeerRobotSkill.execute`` → ``SkillResult`` → 실제 ``ExecuteDecidedAction``
관측 경로를 통과시켜, 일반 성공/산문/큐 ack 가 시연 성공으로 승격되지 않는지 본다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from robo_claw_agent.skills.base import SkillResult
from robo_claw_agent.skills.cleanup.observation import (
    EVENT_CLEANUP_DELEGATE_FINISHED,
    open_cleanup_observation,
)
from robo_claw_agent.skills.cleanup.peer_result import (
    MAX_PEER_RESULT_JSON_CHARS,
    peer_result_from_grpc_json,
    peer_result_from_skill_results,
)
from robo_claw_agent.skills.cleanup.protocol import encode_cleanup_demo_request
from robo_claw_agent.skills.cooperation_skill.journal import (
    configure_cooperation_journal,
    reset_cooperation_journal,
)
from robo_claw_agent.skills.tidy_home_skill import TidyHomeSkill

pytestmark = pytest.mark.unit

REQUEST_ID = "demo-request-pipeline-0001"
ROOM = "거실"


@pytest.fixture(autouse=True)
def _journal():
    reset_cooperation_journal()
    try:
        yield
    finally:
        reset_cooperation_journal()


def _cleanup_result_data(**overrides):
    data = {
        "status": "motion_requested",
        "request_id": REQUEST_ID,
        "target_location": ROOM,
        "target_object": "포장지",
        "motion_id": 77,
        "physical_cleanup_confirmed": False,
        "motion_completion_confirmed": False,
    }
    data.update(overrides)
    return data


def _grpc_result_json(result_data=None, *, skill_name="cleanup_demo", message="motion requested"):
    from robo_claw_channel.task_result import build_task_result_json

    return build_task_result_json(
        {
            "skill_results": [
                {
                    "skill_name": skill_name,
                    "code": 0,
                    "message": message,
                    "duration_sec": 1.0,
                    "result_json": json.dumps(result_data or _cleanup_result_data()),
                }
            ]
        }
    )


def _call_peer_response(result_json: str, *, success=True, message="motion requested"):
    from robo_claw_msgs.srv import CallPeerRobot

    response = CallPeerRobot.Response()
    response.success = success
    response.result_message = message
    response.result_json = result_json
    return response


class _RealSkillManager:
    """BT 가 실제 스킬을 실행하고 SkillResult 로 감싸는 최소 매니저."""

    def __init__(self, skills):
        self._skills = skills
        self.calls = []

    def has_skill(self, name):
        return name in self._skills

    def get_skill(self, name):
        return SimpleNamespace(terminal_behavior="sync", validate_input_schema=None)

    def execute(self, name, params, **_kwargs):
        self.calls.append((name, dict(params)))
        result = self._skills[name].execute(params)
        return SkillResult(
            name, bool(result.get("success")), str(result.get("message", "")), 0.01, result
        )


def _run_delegate(monkeypatch, *, skill_name, response, result_json):
    """실제 스킬 실행 → SkillResult → 실제 BT delegate 관측까지 실행한다."""
    from robo_claw_agent.skills.autonomous_skill import execution_nodes
    from robo_claw_agent.skills.cooperate_skill import CallPeerRobotSkill, DelegateTaskSkill

    from robo_claw_agent import ros_future

    journal = configure_cooperation_journal(local_robot_id="former0047")
    observer = open_cleanup_observation(source="tidy_home", run_id="run-pipeline-1", goal=ROOM)
    observer.start()

    if skill_name == "call_peer_robot":
        monkeypatch.setattr(ros_future, "call_service", lambda *a, **k: (True, response))
        real_skill = CallPeerRobotSkill()
        params = {
            "peer_name": "cloid01",
            "instruction": encode_cleanup_demo_request(
                {
                    "request_id": REQUEST_ID,
                    "target_location": ROOM,
                    "target_object": "포장지",
                    "scene_summary": "거실 바닥에 포장지가 보입니다.",
                }
            ),
            "wait_for_result": True,
            "timeout_sec": 180.0,
        }
    else:
        from robo_claw_msgs.action import ExecuteTask
        from robo_claw_msgs.msg import SkillResult as SkillResultMsg

        task_result = ExecuteTask.Result()
        task_result.success = True
        task_result.result_message = "에이전트 응답"
        step = SkillResultMsg()
        step.skill_name = "cleanup_demo"
        step.code = 0
        step.message = "motion requested"
        step.result_json = json.dumps(_cleanup_result_data())
        task_result.skill_results = [step]

        class _GoalHandle:
            accepted = True

            def get_result_async(self):
                return object()

        class _FakeActionClient:
            def __init__(self, *_args, **_kwargs):
                pass

            def wait_for_server(self, timeout_sec=0.0):
                return True

            def send_goal_async(self, _goal):
                return object()

            def destroy(self):
                pass

        monkeypatch.setattr("rclpy.action.ActionClient", _FakeActionClient)
        responses = iter([(True, _GoalHandle()), (True, SimpleNamespace(result=task_result))])
        monkeypatch.setattr(ros_future, "wait_for_future", lambda *a, **k: next(responses))
        real_skill = DelegateTaskSkill()
        params = {
            "agent_id": "cloid01",
            "instruction": encode_cleanup_demo_request(
                {
                    "request_id": REQUEST_ID,
                    "target_location": ROOM,
                    "target_object": "포장지",
                    "scene_summary": "거실 바닥에 포장지가 보입니다.",
                }
            ),
            "timeout_sec": 180.0,
        }

    manager = _RealSkillManager({skill_name: real_skill})
    node = SimpleNamespace(
        _agent_id="former0047",
        _skills=manager,
        _call_peer_client=object(),
        _manipulation_backend=None,
    )
    real_skill.set_node(node)
    skill = TidyHomeSkill()
    skill.set_node(node)
    skill.send_user_message = lambda _message: None

    execute = execution_nodes.ExecuteDecidedAction(skill)
    execute._blackboard = {
        "cycle_count": 1,
        "cleanup_handoff_prepared": True,
        "cleanup_handoff_kind": "cleanup_demo",
        "cleanup_handoff_request_id": REQUEST_ID,
        "cleanup_handoff_transport": "grpc" if skill_name == "call_peer_robot" else "ros2",
        "cleanup_handoff_target": "포장지",
        "cleanup_observer": observer,
        "decided_skill": skill_name,
        "decided_params": params,
        "last_action_success": False,
    }
    execute.tick()

    snapshot = journal.snapshot()
    finished = [
        event
        for event in snapshot["events"]
        if event["event_type"] == EVENT_CLEANUP_DELEGATE_FINISHED
    ]
    return manager.calls[0][1], snapshot, finished, observer


def test_grpc_cleanup_demo_result_json_reaches_bt_observer(monkeypatch):
    result_json = _grpc_result_json()
    response = _call_peer_response(result_json)

    params, snapshot, finished, observer = _run_delegate(
        monkeypatch, skill_name="call_peer_robot", response=response, result_json=result_json
    )

    assert params["wait_for_result"] is True
    assert finished, "delegate finished event is missing"
    event = finished[0]
    assert event["details"]["observed"] is True
    assert event["details"]["motion_requested"] is True
    assert event["details"]["task_status"] == "motion_requested"
    assert event["details"]["request_id"] == REQUEST_ID
    assert event["details"]["physical_cleanup_confirmed"] is False
    assert event["details"]["motion_completion_confirmed"] is False
    activity = snapshot["activity"]
    assert activity["task_status"] == "motion_requested"
    assert activity["observed"] is True
    assert activity["physical_cleanup_confirmed"] is False
    assert activity["motion_completion_confirmed"] is False


def test_delegate_task_skill_results_result_json_reaches_bt_observer(monkeypatch):
    _, snapshot, finished, _observer = _run_delegate(
        monkeypatch,
        skill_name="delegate_task",
        response=None,
        result_json=_grpc_result_json(),
    )

    assert finished and finished[0]["details"]["task_status"] == "motion_requested"
    assert finished[0]["details"]["observed"] is True
    assert snapshot["activity"]["task_status"] == "motion_requested"


def test_generic_grpc_success_without_result_json_is_unknown(monkeypatch):
    response = _call_peer_response("", success=True, message="작업 완료")

    _, snapshot, finished, _observer = _run_delegate(
        monkeypatch, skill_name="call_peer_robot", response=response, result_json=""
    )

    event = finished[0]
    assert event["details"]["observed"] is False
    assert event["details"]["motion_requested"] is False
    assert event["details"]["task_status"] == "unknown"
    assert event["details"]["failure_reason"] == "result_unconfirmed"
    activity = snapshot["activity"]
    assert activity["task_status"] == "unknown"
    assert activity["failure_reason"] == "result_unconfirmed"
    assert activity["observed"] is False


def test_peer_result_with_mismatched_request_id_is_not_promoted(monkeypatch):
    result_json = _grpc_result_json(_cleanup_result_data(request_id="other-request"))
    response = _call_peer_response(result_json)

    _, snapshot, finished, _observer = _run_delegate(
        monkeypatch, skill_name="call_peer_robot", response=response, result_json=result_json
    )

    event = finished[0]
    assert event["details"]["observed"] is False
    assert event["details"]["motion_requested"] is False
    assert event["details"]["task_status"] == "unknown"
    assert snapshot["activity"]["observed"] is False


def test_prose_result_json_is_not_promoted():
    assert peer_result_from_grpc_json("모션 요청을 보냈습니다.") is None
    assert peer_result_from_grpc_json(json.dumps({"skill_results": []})) is None
    assert (
        peer_result_from_skill_results([SimpleNamespace(skill_name="say", result_json="{}")])
        is None
    )


def test_oversized_result_json_is_ignored_and_physical_claim_is_not_green():
    assert peer_result_from_grpc_json("x" * (MAX_PEER_RESULT_JSON_CHARS + 1)) is None

    claimed = peer_result_from_grpc_json(
        _grpc_result_json(_cleanup_result_data(physical_cleanup_confirmed=True))
    )
    assert claimed is not None
    assert claimed["status"] == ""
    assert claimed["physical_cleanup_confirmed"] is False
