"""주행 전 안전 prelude 실패/타임아웃 시 결과 미확정(unknown) 보호 회귀 테스트.

리뷰 후속 P1: prelude 는 `dispatch_count=0`/`execution_started=False` 인 채로
실패할 수 있다. 그때 `apply_task_outcome` 이 증거 없음만 보고 `failed` 로 닫으면
lease 가 풀리고, native 루프 다음 라운드가 같은 준비 동작+이동을 다시 실행할 수
있었다(실제로 팔이 움직였을 수 있는데도).

계약: 준비 동작 시도도 요청의 실제 효과다. 실제 stop/no-effect 증거가 없는
prelude 실패·타임아웃·예외는 `unknown`(lease 유지)이며, 그 요청의 후속
physical/navigate/prelude 는 모두 거부한다. 명시적 no-effect 실패만 `failed` 로
닫을 수 있다(보수적 기본값 = hold).
"""

import asyncio
import json
import logging
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("rclpy")

from robo_claw_agent.agent_node.map_goal.contract import (
    MapGeometry,
    MapGoalContext,
    canonical_goal_message,
)
from robo_claw_agent.agent_node.map_goal.dispatch import apply_task_outcome
from robo_claw_agent.agent_node.map_goal.scope import (
    MapGoalScope,
    activate_map_goal_scope,
    map_goal_scope,
)
from robo_claw_agent.agent_node.map_goal.tracker import MapGoalTracker, get_map_goal_tracker
from robo_claw_agent.agent_node.planner import LLMPlanner
from robo_claw_agent.llm_bridge import ToolCall, ToolTurn
from robo_claw_agent.skill_manager import BaseSkill, SkillManager
from robo_claw_agent.skills.tool_specs import build_tool_specs

pytestmark = pytest.mark.unit

ROBOT_ID = "robot1"
FRAME = "map"
TARGET_X, TARGET_Y = 1.234568, -2.0
GEOMETRY_ID = "7566e0b34bb04228c0dc8850c6657f6dd941e70eb9f94189856ed903fa5bb8e2"
CANONICAL_INSTRUCTION = canonical_goal_message(ROBOT_ID, FRAME, TARGET_X, TARGET_Y)
PREP_TIMEOUT_SEC = 0.05
MAP_TOPIC = "/map"
WIDTH, HEIGHT, RESOLUTION = 100, 50, 0.05
ORIGIN_X, ORIGIN_Y = -2.5, -3.0


def _map_message():
    return SimpleNamespace(
        header=SimpleNamespace(frame_id=FRAME),
        info=SimpleNamespace(
            width=WIDTH,
            height=HEIGHT,
            resolution=RESOLUTION,
            origin=SimpleNamespace(
                position=SimpleNamespace(x=ORIGIN_X, y=ORIGIN_Y),
                orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
            ),
        ),
        data=[0] * (WIDTH * HEIGHT),
    )


def _map_geometry_id() -> str:
    return MapGeometry(
        map_topic=MAP_TOPIC,
        frame_id=FRAME,
        width=WIDTH,
        height=HEIGHT,
        resolution=RESOLUTION,
        origin_x=ORIGIN_X,
        origin_y=ORIGIN_Y,
        origin_yaw=0.0,
    ).geometry_id()


def _context(request_id: str = "req-0001", geometry_id: str = GEOMETRY_ID) -> MapGoalContext:
    now = time.time()
    return MapGoalContext(
        request_id=request_id,
        robot_id=ROBOT_ID,
        x=TARGET_X,
        y=TARGET_Y,
        frame_id=FRAME,
        geometry_id=geometry_id,
        map_topic="/map",
        created_at=now,
        expires_at=now + 60.0,
    )


def _scope(request_id: str = "req-0001", geometry_id: str = GEOMETRY_ID) -> MapGoalScope:
    return MapGoalScope(
        context=_context(request_id, geometry_id=geometry_id), navigation_frame=FRAME
    )


def _activated_scope(node, *, geometry_id: str = GEOMETRY_ID) -> MapGoalScope:
    scope, error = activate_map_goal_scope(
        node, _goal_handle(_context_json(geometry_id=geometry_id)), CANONICAL_INSTRUCTION
    )
    assert error == ""
    assert scope is not None
    return scope


class _BlockingStowSkill(BaseSkill):
    """public Stretch stow 더블: 실제로 오래 걸려 SkillManager 타임아웃을 만든다."""

    name = "stow_for_navigation"
    side_effects = ("arm_motion",)

    def __init__(self, delay_sec: float = 0.3) -> None:
        super().__init__()
        self.delay_sec = delay_sec
        self.calls: list[dict] = []

    def execute(self, params):
        self.calls.append(dict(params))
        time.sleep(self.delay_sec)
        return self.success_result("접음")


class _FailingStowSkill(BaseSkill):
    name = "stow_for_navigation"
    side_effects = ("arm_motion",)

    def __init__(self, result_data=None) -> None:
        super().__init__()
        self.calls: list[dict] = []
        self.result_data = result_data

    def execute(self, params):
        self.calls.append(dict(params))
        result = self.fail_result("접기 실패", failure_reason="service_error")
        if isinstance(self.result_data, dict):
            result.update(self.result_data)
        return result


class _NavigateSkill(BaseSkill):
    name = "navigate_to"
    side_effects = ("base_motion",)
    exclusive_resources = ("base_control",)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    def execute(self, params):
        self.calls.append(dict(params))
        return self.success_result("이동 완료", x=params.get("x"), y=params.get("y"))


class _RuntimeMetrics:
    def record(self, *args, **kwargs) -> None:
        pass


class _Memory:
    def get_conversation_history(self, n):
        return []

    def add_event(self, *args, **kwargs) -> None:
        pass


class _Node:
    _llm_tool_calling = True
    _enable_skill_learning = False
    _enable_rag = False
    _multiturn_n = 5
    _agent_id = ROBOT_ID
    _map_frame = FRAME
    _state = 0
    _manipulation_backend = None
    _memory = _Memory()
    _runtime_metrics = _RuntimeMetrics()

    def __init__(self, turns, stow) -> None:
        self.turns = iter(turns)
        self.requests: list[list[dict]] = []
        self._logger = logging.getLogger("map_goal_prep")
        self._llm = SimpleNamespace(supports_tools=lambda: True, chat_with_tools=self._chat)
        self._skills = SkillManager()
        self.stow = stow
        self.navigate = _NavigateSkill()
        self._skills.register(stow)
        self._skills.register(self.navigate)

    def _chat(self, messages, **kwargs):
        self.requests.append([dict(m) for m in messages])
        return next(self.turns)

    def get_logger(self):
        return self._logger

    def _set_state(self, *args, **kwargs):
        pass

    def _build_system_prompt(self):
        return "system"

    def _tool_specs(self):
        # Stretch 실제 배포와 같이 public stow 가 도구 목록에 노출된다.
        return build_tool_specs([{"name": "navigate_to"}, {"name": "stow_for_navigation"}])

    def _validate_skill_chain(self, chain):
        from robo_claw_agent.agent_node.execution import ExecutionMixin

        return ExecutionMixin._validate_skill_chain(self, chain)

    async def _run_blocking(self, op, func, *args, **kwargs):
        return func(*args, **kwargs)


def _goal_handle(context_json=""):
    return SimpleNamespace(request=SimpleNamespace(context_json=context_json))


def _context_json(geometry_id: str = GEOMETRY_ID) -> str:
    context = _context(geometry_id=geometry_id)
    return json.dumps(
        {
            "type": "dashboard_map_goal_v1",
            "request_id": context.request_id,
            "robot_id": context.robot_id,
            "x": context.x,
            "y": context.y,
            "frame_id": context.frame_id,
            "geometry_id": context.geometry_id,
            "map_topic": context.map_topic,
            "created_at": context.created_at,
            "expires_at": context.expires_at,
        }
    )


def _navigate_turn() -> ToolTurn:
    return ToolTurn(
        tool_calls=[ToolCall(name="navigate_to", arguments={"x": TARGET_X, "y": TARGET_Y})]
    )


async def _run_plan(node, scope, timeout=PREP_TIMEOUT_SEC):
    """`_execute_task_inner` 와 같은 순서(스코프 → planner → 작업 종료 확정)로 실행한다."""
    planner = LLMPlanner(node)
    messages = [{"role": "user", "content": CANONICAL_INSTRUCTION}]
    with map_goal_scope(scope):
        result_msg, skill_results, success, _ = await planner.run_llm_planning_loop(
            _goal_handle(_context_json()),
            CANONICAL_INSTRUCTION,
            messages,
            {},
            timeout,
            lambda *a: None,
        )
    apply_task_outcome(node, scope, skill_results, result_msg)
    return result_msg, skill_results, success


def _record(node, scope):
    return get_map_goal_tracker(node).get(scope.request_id)


# ── P1: prelude 타임아웃/실패 → unknown + lease 유지 ──────────────────────


async def test_prep_timeout_holds_unknown_and_blocks_retry():
    stow = _BlockingStowSkill()
    node = _Node([_navigate_turn(), _navigate_turn(), ToolTurn(content="결과 미확인")], stow)
    scope = _activated_scope(node)

    _, results, _ = await _run_plan(node, scope)

    assert stow.calls == [{}], "준비 동작은 한 번만 시도한다"
    assert node.navigate.calls == [], "준비 결과가 미확정이면 이동을 시작하지 않는다"
    assert [res.skill_name for res in results] == ["stow_for_navigation"]
    record = _record(node, scope)
    assert record["status"] == "unknown"
    assert record["reason"] == "prep_outcome_unknown"
    assert record["can_submit"] is False
    assert record["cancel_confirmed"] is False
    # 다음 라운드의 재계획도 거부된다.
    assert "결과가 확정되지 않아" in json.dumps(node.requests, ensure_ascii=False)


async def test_prep_failure_without_no_effect_evidence_holds_unknown():
    stow = _FailingStowSkill()
    node = _Node([_navigate_turn(), _navigate_turn(), ToolTurn(content="결과 미확인")], stow)
    scope = _activated_scope(node)

    await _run_plan(node, scope, timeout=5.0)

    assert stow.calls == [{}]
    assert node.navigate.calls == []
    record = _record(node, scope)
    assert record["status"] == "unknown"
    assert record["can_submit"] is False


async def test_prep_exception_holds_unknown(monkeypatch):
    stow = _FailingStowSkill()
    node = _Node([_navigate_turn(), ToolTurn(content="결과 미확인")], stow)
    scope = _activated_scope(node)

    original = node._run_blocking

    async def _boom(op, func, *args, **kwargs):
        if op == "skill:stow_for_navigation":
            raise asyncio.TimeoutError("prelude timeout")
        return await original(op, func, *args, **kwargs)

    monkeypatch.setattr(node, "_run_blocking", _boom)

    with pytest.raises(asyncio.TimeoutError):
        await _run_plan(node, scope, timeout=5.0)

    apply_task_outcome(node, scope, [], "준비 동작 예외")
    record = _record(node, scope)
    assert record["status"] == "unknown"
    assert record["can_submit"] is False


async def test_prep_confirmed_no_effect_failure_closes_as_failed():
    stow = _FailingStowSkill(result_data={"executed": False, "status": "failed_to_start"})
    node = _Node([_navigate_turn(), ToolTurn(content="준비 동작을 시작하지 못했습니다.")], stow)
    scope = _activated_scope(node)

    await _run_plan(node, scope, timeout=5.0)

    record = _record(node, scope)
    assert record["status"] == "failed"
    assert record["reason"] == "no_navigation_evidence"
    assert record["can_submit"] is True
    assert node.navigate.calls == []


async def test_prep_hold_blocks_direct_navigation_guard():
    from robo_claw_agent.agent_node.map_goal.dispatch import MapGoalNavigationGuard

    stow = _FailingStowSkill()
    node = _Node([], stow)
    scope = _activated_scope(node)
    scope.mark_prep_started()
    scope.mark_prelude_result(success=False, result_data={"success": False})

    with map_goal_scope(scope):
        guard, error = MapGoalNavigationGuard.begin(
            SimpleNamespace(node=node), x=TARGET_X, y=TARGET_Y, frame=FRAME
        )

    assert guard is None
    assert "준비" in error


async def test_prep_success_then_navigation_reached_releases_lease(monkeypatch):
    from robo_claw_agent.skills.navigation_skill import NavigateToSkill

    stow = _FailingStowSkill()

    def _stow_ok(params):
        stow.calls.append(dict(params))
        return stow.success_result("접음")

    stow.execute = _stow_ok
    node = _Node([_navigate_turn(), ToolTurn(content="이동 완료")], stow)
    node._skills.register(NavigateToSkill())
    node.navigate = node._skills.get_skill("navigate_to")
    node.navigate.set_node(node)
    scope = _activated_scope(node, geometry_id=_map_geometry_id())

    sent: list[tuple] = []
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._get_nav2_dependency_error", lambda: None
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._send_navigation_goal",
        lambda node_, x, y, frame, yaw=0.0: (
            sent.append((x, y, frame)) or (True, "accepted", SimpleNamespace(_goal_token=1))
        ),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: (True, "success"),
    )

    skill = node._skills.get_skill("navigate_to")
    skill.get_map_pose = lambda: {"x": 5.0, "y": 5.0, "yaw": 0.0, "frame": FRAME}
    skill.wait_for_map_message = lambda topic, timeout_sec=5.0, max_age_sec=10.0: _map_message()
    skill.validate_message_frame = lambda message, expected, label: (True, "")

    _, results, _ = await _run_plan(node, scope, timeout=5.0)

    assert stow.calls == [{}]
    assert len(sent) == 1
    names = [res.skill_name for res in results]
    assert names == ["stow_for_navigation", "navigate_to"]
    record = _record(node, scope)
    assert record["status"] == "reached"
    assert record["can_submit"] is True


# ── 스코프 상태 단위 계약 ─────────────────────────────────────────────────


def test_mark_prelude_result_classifies_no_effect_vs_uncertain():
    scope = _scope()

    assert scope.mark_prelude_result(success=True, result_data={"success": True}) is False
    assert scope.prep_hold() is False

    other = _scope()
    assert other.mark_prelude_result(success=False, result_data={"executed": False}) is False
    assert other.prep_hold() is False

    uncertain = _scope()
    assert uncertain.mark_prelude_result(success=False, result_data={"success": False}) is True
    assert uncertain.prep_hold() is True


def test_prep_hold_is_not_widened_by_name_whitelist():
    """trusted prelude 이름 목록에 추가해도 준비 결과 미확정은 그대로 유지된다."""
    scope = _scope()
    scope.mark_prep_started()
    scope.mark_prelude_result(
        success=False, result_data={"status": "timeout", "cancel_confirmed": False}
    )

    denied = scope.denied_reason(
        "navigate_to", risk_level="action", side_effects=("base_motion",), exclusive_resources=()
    )

    assert denied
    assert "준비" in denied


def test_worker_thread_prep_hold_is_visible_after_scope_reset():
    scope = _scope()
    seen: dict[str, object] = {}

    def _worker():
        with map_goal_scope(scope):
            scope.mark_prep_started()
            seen["hold"] = scope.mark_prelude_result(success=False, result_data={"success": False})
        seen["after"] = scope.prep_hold()

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join(timeout=10.0)

    assert seen["hold"] is True
    assert seen["after"] is True, "준비 미확정은 스코프 수명 이후에도 요청 상태로 남는다"
    assert scope.prep_hold() is True


def test_tracker_unknown_protection_is_unchanged_by_prep_hold():
    tracker = MapGoalTracker()
    context = _context()
    tracker.admit(context)
    tracker.transition(context.request_id, "unknown", reason="prep_outcome_unknown")

    tracker.transition(context.request_id, "failed", reason="no_navigation_evidence")

    assert tracker.get(context.request_id)["status"] == "unknown"
