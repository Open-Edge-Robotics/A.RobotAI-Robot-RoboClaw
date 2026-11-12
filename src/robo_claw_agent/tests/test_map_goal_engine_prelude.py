"""지도 목표 + 엔진 삽입 안전 prelude(public Stretch stow) 회귀 테스트.

리뷰 P1-1: Stretch3 는 ``stow_for_navigation`` 이 public 스킬(도구 목록 노출)이라
기존 native 루프가 "준비 도구를 먼저 호출하세요"로 모델을 되돌리지만, 지도 목표
검증은 모델이 고른 stow 를 거부한다 → 5라운드 소진 교착.

계약: **원본 LLM 체인을 먼저 검증**하고, 검증을 통과한 체인에 엔진이 삽입한
안전 prelude 만 trusted/internal 로 표시해 엔진이 직접 실행한다. 모델이 스스로
stow/자세/표식을 넣는 것은 계속 거부한다.
"""

import json
import logging
from types import SimpleNamespace

import pytest
from robo_claw_agent.agent_node.map_goal.contract import MapGoalContext, canonical_goal_message
from robo_claw_agent.agent_node.map_goal.enforcement import (
    apply_map_goal_safety,
    validate_map_goal_chain,
)
from robo_claw_agent.agent_node.map_goal.scope import MapGoalScope, map_goal_scope
from robo_claw_agent.agent_node.nav_safety import POLICY_PRELUDE_FLAG
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


def _context() -> MapGoalContext:
    return MapGoalContext(
        request_id="req-0001",
        robot_id=ROBOT_ID,
        x=TARGET_X,
        y=TARGET_Y,
        frame_id=FRAME,
        geometry_id=GEOMETRY_ID,
        map_topic="/map",
        created_at=1_700_000_000.0,
        expires_at=1_700_000_060.0,
    )


def _scope() -> MapGoalScope:
    return MapGoalScope(context=_context(), navigation_frame=FRAME)


class _StowSkill(BaseSkill):
    """Stretch3 public stow 스킬(도구 목록에 노출되는 실제 이름)."""

    name = "stow_for_navigation"
    side_effects = ("arm_motion",)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    def execute(self, params):
        self.calls.append(dict(params))
        return self.success_result("접음")


class _PoseSkill(BaseSkill):
    name = "cloid_navigation_pose"
    side_effects = ("arm_motion",)
    is_internal = True

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    def execute(self, params):
        self.calls.append(dict(params))
        return self.success_result("자세 요청 완료")


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


class _MoveRelativeSkill(BaseSkill):
    name = "move_relative"
    side_effects = ("base_motion",)
    exclusive_resources = ("base_control",)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    def execute(self, params):
        self.calls.append(dict(params))
        return self.success_result("상대 이동 완료")


class _NestedMotionSkill(BaseSkill):
    """읽기 전용으로 등록되지만 내부에서 베이스 이동을 실행하는 스킬(우회 시도)."""

    name = "inspect_and_drive"
    risk_level = "read"

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []
        self.nested_results: list = []
        self._skills = None

    def execute(self, params):
        self.calls.append(dict(params))
        self.nested_results.append(self._skills.execute("move_relative", {"forward": 1.0}, 5.0))
        return self.success_result("점검 완료")


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

    def __init__(self, turns, *, tool_mode=True, with_stow=True, with_pose=False) -> None:
        self.turns = iter(turns)
        self.requests: list[list[dict]] = []
        self._logger = logging.getLogger("map_goal_prelude")
        self._llm = SimpleNamespace(
            supports_tools=lambda: tool_mode,
            chat_with_tools=self._chat,
            chat=self._chat_json,
        )
        self._llm_tool_calling = tool_mode
        self._skills = SkillManager()
        self.stow = _StowSkill()
        self.pose = _PoseSkill()
        self.navigate = _NavigateSkill()
        self.move_relative = _MoveRelativeSkill()
        self.nested = _NestedMotionSkill()
        self._skills.register(self.navigate)
        self._skills.register(self.move_relative)
        self._skills.register(self.nested)
        self.nested._skills = self._skills
        if with_stow:
            self._skills.register(self.stow)
        if with_pose:
            self._skills.register(self.pose)

    def _chat(self, messages, **kwargs):
        self.requests.append([dict(m) for m in messages])
        return next(self.turns)

    def _chat_json(self, messages, **kwargs):
        self.requests.append([dict(m) for m in messages])
        turn = next(self.turns)
        return turn if isinstance(turn, str) else json.dumps({"skill": None, "response": "완료"})

    def get_logger(self):
        return self._logger

    def _set_state(self, *args, **kwargs):
        pass

    def _build_system_prompt(self):
        return "system"

    def _tool_specs(self):
        names = ["navigate_to", "move_relative", "inspect_and_drive"]
        if self._skills.get_skill("stow_for_navigation") is not None:
            # Stretch3 실제 배포와 같다: public stow 가 도구 목록에 노출된다.
            names.append("stow_for_navigation")
        return build_tool_specs([{"name": name} for name in names])

    def _validate_skill_chain(self, chain):
        from robo_claw_agent.agent_node.execution import ExecutionMixin

        return ExecutionMixin._validate_skill_chain(self, chain)

    async def _run_blocking(self, op, func, *args, **kwargs):
        return func(*args, **kwargs)


def _goal_handle(context_json=""):
    return SimpleNamespace(request=SimpleNamespace(context_json=context_json))


def _context_json() -> str:
    context = _context()
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


async def _run_native(node, scope):
    planner = LLMPlanner(node)
    messages = [{"role": "user", "content": CANONICAL_INSTRUCTION}]
    with map_goal_scope(scope):
        return await planner.run_llm_planning_loop(
            _goal_handle(_context_json()), CANONICAL_INSTRUCTION, messages, {}, 5, lambda *a: None
        )


def _all_message_text(node) -> str:
    return json.dumps(node.requests, ensure_ascii=False)


def _validate(chain, scope=None):
    return validate_map_goal_chain(chain, scope or _scope(), _FakeSkillRegistry())


class _FakeSkillRegistry:
    def __init__(self) -> None:
        self._registry = {
            "navigate_to": _NavigateSkill(),
            "stow_for_navigation": _StowSkill(),
            "cloid_navigation_pose": _PoseSkill(),
            "inspect_and_drive": _NestedMotionSkill(),
            "move_relative": _MoveRelativeSkill(),
        }

    def get_skill(self, name):
        return self._registry.get(name)


# ── P1-1: public Stretch stow prelude 교착 ─────────────────────────────────


async def test_public_stow_prelude_is_executed_by_engine_without_prep_roundtrip():
    node = _Node(
        [
            # 모델은 이동만 고른다(준비 도구를 먼저 호출하라는 요구를 받지 않아야 한다).
            ToolTurn(
                tool_calls=[ToolCall(name="navigate_to", arguments={"x": TARGET_X, "y": TARGET_Y})]
            ),
            ToolTurn(content="이동을 시작했습니다."),
        ]
    )

    _, results, success, _ = await _run_native(node, _scope())

    assert success is True
    assert node.stow.calls == [{}]
    assert node.navigate.calls == [{"x": TARGET_X, "y": TARGET_Y}]
    assert [res.skill_name for res in results] == ["stow_for_navigation", "navigate_to"]
    # 첫 라운드에서 곧바로 실행되어야 한다(준비 도구 선행 요구로 되돌리지 않는다).
    assert "준비 도구를 먼저 호출" not in _all_message_text(node)
    assert "PlanValidationError" not in _all_message_text(node)


def test_engine_inserted_stretch_prelude_is_marked_trusted_and_order_kept():
    skills = SkillManager()
    skills.register(_StowSkill())
    skills.register(_NavigateSkill())
    chain = [{"skill": "navigate_to", "params": {"x": TARGET_X, "y": TARGET_Y}}]

    protected, error = apply_map_goal_safety(
        chain, _scope(), skills, logging.getLogger("map_goal_prelude")
    )

    assert error == ""
    assert [item["skill"] for item in protected] == ["stow_for_navigation", "navigate_to"]
    assert protected[0][POLICY_PRELUDE_FLAG] is True
    # 모델이 고른 항목에는 엔진 표식이 붙지 않는다.
    assert POLICY_PRELUDE_FLAG not in protected[1]


def test_engine_keeps_configured_cloid_prelude_order_after_stow():
    skills = SkillManager()
    skills.register(_StowSkill())
    skills.register(_PoseSkill())
    skills.register(_NavigateSkill())
    chain = [{"skill": "navigate_to", "params": {"x": TARGET_X, "y": TARGET_Y}}]

    protected, error = apply_map_goal_safety(
        chain, _scope(), skills, logging.getLogger("map_goal_prelude")
    )

    assert error == ""
    assert [item["skill"] for item in protected] == [
        "stow_for_navigation",
        "cloid_navigation_pose",
        "navigate_to",
    ]
    assert protected[0][POLICY_PRELUDE_FLAG] and protected[1][POLICY_PRELUDE_FLAG]


def test_model_supplied_policy_prelude_flag_is_rejected():
    chain = [
        {"skill": "navigate_to", "params": {"x": TARGET_X, "y": TARGET_Y}},
        {"skill": "stow_for_navigation", "params": {}, POLICY_PRELUDE_FLAG: True},
    ]

    kept, error = _validate(chain)

    assert kept == []
    assert error


def test_model_supplied_stow_is_rejected():
    chain = [
        {"skill": "stow_for_navigation", "params": {}},
        {"skill": "navigate_to", "params": {"x": TARGET_X, "y": TARGET_Y}},
    ]

    kept, error = _validate(chain)

    assert kept == []
    assert "stow_for_navigation" in error


def test_model_supplied_arm_pose_is_rejected():
    chain = [
        {"skill": "cloid_navigation_pose", "params": {}},
        {"skill": "navigate_to", "params": {"x": TARGET_X, "y": TARGET_Y}},
    ]

    kept, error = _validate(chain)

    assert kept == []
    assert error


def test_no_further_physical_plan_after_dispatch_attempt():
    scope = _scope()
    scope.dispatch_count = 1
    skills = SkillManager()
    skills.register(_StowSkill())
    skills.register(_NavigateSkill())
    chain = [
        {"skill": "stow_for_navigation", "params": {}},
        {"skill": "navigate_to", "params": {"x": TARGET_X, "y": TARGET_Y}},
    ]

    protected, error = apply_map_goal_safety(
        chain, scope, skills, logging.getLogger("map_goal_prelude")
    )

    assert protected == []
    assert error
    assert skills.get_skill("stow_for_navigation").calls == []


async def test_planner_loop_cannot_act_again_after_dispatch_attempt():
    """주행을 이미 시도한 요청은 같은/다른 라운드에서 추가 준비+이동을 실행하지 않는다."""
    node = _Node(
        [
            ToolTurn(
                tool_calls=[
                    ToolCall(name="stow_for_navigation", arguments={}),
                    ToolCall(name="navigate_to", arguments={"x": TARGET_X, "y": TARGET_Y}),
                ]
            ),
            ToolTurn(content="결과를 확인하지 못했습니다."),
        ]
    )
    scope = _scope()
    scope.dispatch_count = 1

    _, results, _, _ = await _run_native(node, scope)

    assert node.stow.calls == []
    assert node.navigate.calls == []
    assert results == []
    assert "결과가 확정되지 않아" in _all_message_text(node)


# ── P1-4: 저수준(SkillManager) 실행 경계 ───────────────────────────────────


def test_low_boundary_blocks_untrusted_stow_in_map_scope():
    skills = SkillManager()
    stow = _StowSkill()
    skills.register(stow)

    with map_goal_scope(_scope()):
        denied = skills.execute("stow_for_navigation", {}, 5.0)

    assert denied.success is False
    assert stow.calls == []


def test_low_boundary_allows_engine_trusted_prelude():
    from robo_claw_agent.domain.map_goal_guard import trusted_prelude as trusted_prelude_scope

    skills = SkillManager()
    stow = _StowSkill()
    skills.register(stow)

    with map_goal_scope(_scope()), trusted_prelude_scope("stow_for_navigation"):
        allowed = skills.execute("stow_for_navigation", {}, 5.0)

    assert allowed.success is True
    assert stow.calls == [{}]


def test_low_boundary_blocks_nested_physical_skill_from_readonly_tool():
    node = _Node([], tool_mode=False)
    skills = node._skills

    with map_goal_scope(_scope()):
        wrapper = skills.execute("inspect_and_drive", {}, 5.0)

    assert wrapper.success is True  # 읽기 전용 껍데기 자체는 허용
    assert node.move_relative.calls == []
    assert wrapper is not None
    nested = node.nested.nested_results[0]
    assert nested.success is False
    assert "물리 동작" in nested.message


def test_low_boundary_does_not_affect_ordinary_requests():
    node = _Node([], tool_mode=False)

    wrapper = node._skills.execute("inspect_and_drive", {}, 5.0)

    assert wrapper.success is True
    assert node.move_relative.calls == [{"forward": 1.0}]


async def test_ordinary_task_prelude_rule_is_unchanged():
    """일반 요청은 기존 규칙(모델이 준비 도구를 먼저 호출)을 그대로 유지한다."""
    node = _Node(
        [
            ToolTurn(
                tool_calls=[ToolCall(name="navigate_to", arguments={"x": TARGET_X, "y": TARGET_Y})]
            ),
            ToolTurn(content="이동할 수 없습니다."),
        ]
    )
    planner = LLMPlanner(node)
    messages = [{"role": "user", "content": "거실로 이동해"}]

    await planner.run_llm_planning_loop(
        _goal_handle(), "거실로 이동해", messages, {}, 5, lambda *a: None
    )

    assert node.stow.calls == []
    assert node.navigate.calls == []
    assert "준비 도구를 먼저 호출" in _all_message_text(node)
