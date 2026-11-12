"""지도 목표 요청의 ExecuteTask 진입/관측 통합 테스트.

- 지도 목표 컨텍스트가 깨졌으면 어떤 스킬도 실행하지 않고 요청을 거부한다.
- 검증된 컨텍스트는 결정론적 사전 라우터를 건너뛰고 기존 LLM planner 로만 간다.
- 실제 이동 증거가 없으면 도착으로 확정하지 않는다.
- QueryState 는 navigation_frame 과 map_navigation_request 를 노출한다.
"""

import asyncio
import json
import logging
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("rclpy")

from robo_claw_agent.agent_node.execution import ExecutionMixin
from robo_claw_agent.agent_node.fast_router import ROUTE_DIRECT_SKILL, RouteDecision
from robo_claw_agent.agent_node.map_goal.contract import MapGeometry, canonical_goal_message
from robo_claw_agent.agent_node.map_goal.tracker import get_map_goal_tracker
from robo_claw_agent.skill_manager import SkillResult
from robo_claw_agent.types import AgentState

pytestmark = pytest.mark.unit

ROBOT_ID = "robot1"
FRAME = "map"
TARGET_X, TARGET_Y = 0.0, 0.0


def _geometry_id() -> str:
    return MapGeometry(
        map_topic="/map",
        frame_id=FRAME,
        width=100,
        height=50,
        resolution=0.05,
        origin_x=-2.5,
        origin_y=-1.25,
        origin_yaw=0.0,
    ).geometry_id()


def _context_json(*, request_id="req-0001"):
    now = time.time()
    return json.dumps(
        {
            "type": "dashboard_map_goal_v1",
            "request_id": request_id,
            "robot_id": ROBOT_ID,
            "x": TARGET_X,
            "y": TARGET_Y,
            "frame_id": FRAME,
            "geometry_id": _geometry_id(),
            "map_topic": "/map",
            "created_at": now,
            "expires_at": now + 60.0,
        }
    )


def _canonical_instruction(x=TARGET_X, y=TARGET_Y):
    return canonical_goal_message(ROBOT_ID, FRAME, x, y)


def _evidence(*, status="reached", geometry_id=None):
    return {
        "request_id": "req-0001",
        "robot_id": ROBOT_ID,
        "status": status,
        "message": "이동 완료",
        "reason": "",
        "frame_id": FRAME,
        "x": TARGET_X,
        "y": TARGET_Y,
        "geometry_id": geometry_id or _geometry_id(),
        "execution_started": True,
        "cancel_confirmed": False,
        "goal_token": 3,
    }


class _Skills:
    mock_robot = False

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    def get_skill(self, name):
        return None

    def execute(self, name, params, timeout_sec=0.0):
        self.executed.append((name, params))
        return SkillResult(name, True, f"{name} 완료", 0.01, {})

    def list_skills(self):
        return []

    def runtime_summary(self):
        return {}


class _Memory:
    def add_event(self, *args, **kwargs) -> None:
        pass

    def summary(self):
        return {}


class _Metrics:
    def record(self, *args, **kwargs) -> None:
        pass

    def snapshot(self):
        return {}


class _Planner:
    def __init__(self, result) -> None:
        self.result = result
        self.loop_calls: list[str] = []
        self.collect_calls: list[str] = []

    async def collect_task_context(self, instruction, goal_handle):
        self.collect_calls.append(instruction)
        return [{"role": "user", "content": instruction}], {}

    async def run_llm_planning_loop(
        self, goal_handle, instruction, messages, summary, timeout, send_fb
    ):
        self.loop_calls.append(instruction)
        return self.result


class _Decomposer:
    def __init__(self, result=("", [], True, [])) -> None:
        self.result = result
        self.calls: list[str] = []

    def looks_compound(self, instruction):
        return False

    async def run(self, goal_handle, instruction, messages, summary, timeout, send_fb):
        self.calls.append(instruction)
        return self.result


class _Router:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def decide(self, node, instruction):
        self.calls.append(instruction)
        return RouteDecision(
            kind=ROUTE_DIRECT_SKILL, skill="move_relative", params={"forward": 1.0}
        )


class _GoalHandle:
    def __init__(self, context_json: str = "") -> None:
        self.request = SimpleNamespace(context_json=context_json)
        self.feedback: list = []

    def publish_feedback(self, fb) -> None:
        self.feedback.append(fb)


class _Node(ExecutionMixin):
    _llm_tool_calling = False
    _enable_skill_learning = False
    _enable_task_decomposition = True
    _agent_id = ROBOT_ID
    _map_frame = FRAME

    def __init__(self, planner, router=None) -> None:
        self._planner = planner
        self._task_decomposer = _Decomposer()
        self._skills = _Skills()
        self._memory = _Memory()
        self._runtime_metrics = _Metrics()
        self._llm = SimpleNamespace(supports_tools=lambda: False)
        self._state = AgentState.IDLE
        self._router = router or _Router()
        self._logger = logging.getLogger("map_goal_execution")

    def get_logger(self):
        return self._logger

    def _set_state(self, state, skill: str = "", msg: str = "") -> None:
        self._state = state


def _run(node, instruction, context_json=""):
    return asyncio.run(node._execute_task_inner(_GoalHandle(context_json), instruction, 30.0))


def test_runtime_error_in_blocking_operation_is_not_executed_twice():
    node = _Node(_Planner(("", [], False, [])))
    calls = []

    def fail_after_side_effect():
        calls.append("effect")
        raise RuntimeError("fake failure after a side effect")

    with pytest.raises(RuntimeError, match="fake failure"):
        asyncio.run(node._run_blocking("fake-operation", fail_after_side_effect))
    assert calls == ["effect"]


def test_expired_selected_goal_returns_correlated_nonexecution_validation():
    planner = _Planner(("", [], True, []))
    node = _Node(planner)
    expired = json.loads(_context_json())
    expired["created_at"] = time.time() - 65.0
    expired["expires_at"] = expired["created_at"] + 60.0
    message, results, success = _run(node, _canonical_instruction(), json.dumps(expired))
    assert success is False
    assert planner.loop_calls == [] and node._skills.executed == []
    assert len(results) == 1
    assert results[0].skill_name == "map_goal_validation"
    rejection = results[0].result_data["map_goal_rejection"]
    assert rejection["request_id"] == expired["request_id"]
    assert rejection["reason"] == "navigation_not_executed"
    assert rejection["execution_started"] is False


def test_malformed_map_context_is_rejected_before_any_execution():
    planner = _Planner(("", [], True, []))
    node = _Node(planner)

    result_msg, results, success = _run(
        node, _canonical_instruction(), '{"type": "dashboard_map_goal_v1",'
    )

    assert success is False
    assert results == []
    assert planner.loop_calls == []
    assert node._skills.executed == []
    assert node._router.calls == []
    assert "지도 목표" in result_msg


def test_map_goal_skips_deterministic_router_and_uses_planner():
    planner = _Planner(("이동을 시작했습니다.", [], True, []))
    node = _Node(planner)
    instruction = _canonical_instruction()

    _run(node, instruction, _context_json())

    assert node._router.calls == []
    assert planner.loop_calls == [instruction]
    assert node._skills.executed == []


def test_message_context_mismatch_is_rejected_before_execution():
    planner = _Planner(("이동을 시작했습니다.", [], True, []))
    node = _Node(planner)

    result_msg, results, success = _run(
        node, "지도에서 선택한 목표로 이동해 주세요.", _context_json()
    )

    assert success is False
    assert len(results) == 1 and results[0].skill_name == "map_goal_validation"
    assert results[0].result_data["executed"] is False
    assert planner.loop_calls == []
    assert node._skills.executed == []
    assert "일치하지 않습니다" in result_msg


def test_ordinary_request_still_uses_deterministic_router():
    planner = _Planner(("", [], True, []))
    node = _Node(planner)

    result_msg, _, success = _run(node, "앞으로 2미터 가줘")

    assert success is True
    assert node._router.calls == ["앞으로 2미터 가줘"]
    assert node._skills.executed == [("move_relative", {"forward": 1.0})]
    assert planner.loop_calls == []
    assert result_msg == "move_relative 완료"


def test_map_goal_records_failure_when_no_navigation_evidence():
    planner = _Planner(("이동할 수 없습니다.", [], True, []))
    node = _Node(planner)

    _run(node, _canonical_instruction(), _context_json())

    record = get_map_goal_tracker(node).get("req-0001")
    assert record["status"] == "failed"
    assert record["reason"] == "no_navigation_evidence"
    assert record["can_submit"] is True


def test_map_goal_marks_reached_only_from_navigation_evidence():
    result = SkillResult("navigate_to", True, "이동 완료", 0.2, {"map_goal_request": _evidence()})
    planner = _Planner(("이동 완료", [result], True, []))
    node = _Node(planner)

    _run(node, _canonical_instruction(), _context_json())

    record = get_map_goal_tracker(node).get("req-0001")
    assert record["status"] == "reached"
    assert record["execution_started"] is True


def test_map_goal_evidence_for_other_target_is_not_reached():
    result = SkillResult(
        "navigate_to",
        True,
        "이동 완료",
        0.2,
        {"map_goal_request": {**_evidence(), "x": TARGET_X + 3.0}},
    )
    planner = _Planner(("이동 완료", [result], True, []))
    node = _Node(planner)

    _run(node, _canonical_instruction(), _context_json())

    record = get_map_goal_tracker(node).get("req-0001")
    assert record["status"] == "failed"
    assert record["reason"] == "no_navigation_evidence"


def test_query_state_exposes_navigation_frame_and_request_record():
    from robo_claw_agent.agent_node.node import AgentNode

    planner = _Planner(("이동 완료", [], True, []))
    node = _Node(planner)
    node._cooperation_snapshot = lambda: None
    node._task_queue = None

    response = SimpleNamespace()
    AgentNode._handle_query_state(node, SimpleNamespace(query=""), response)

    assert response.success is True
    state = json.loads(response.state_json)
    assert state["navigation_frame"] == FRAME
    assert state["map_navigation_request"] is None

    _run(node, _canonical_instruction(), _context_json())
    response = SimpleNamespace()
    AgentNode._handle_query_state(node, SimpleNamespace(query=""), response)

    state = json.loads(response.state_json)
    assert state["map_navigation_request"]["request_id"] == "req-0001"
    assert state["map_navigation_request"]["status"] == "failed"
    assert state["map_navigation_request"]["target"] == {
        "x": TARGET_X,
        "y": TARGET_Y,
        "frame_id": FRAME,
    }
