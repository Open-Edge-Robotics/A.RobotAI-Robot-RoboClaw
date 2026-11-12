"""지도 목표 저수준 실행 경계 — 비물리 action/원격 위임 차단 회귀 테스트.

리뷰 후속 P1: `Scope.denied_reason` 이 `risk_level` 을 버리고 비물리 스킬을 모두
허용해서, 읽기 전용 도구가 내부에서 `delegate_task`/`call_peer_robot`/
`broadcast_to_peers`/`coordinate_peer_task` 를 호출하면 다른 로봇을 움직이게 할 수
있었다.

계약: 지도 목표 요청의 저수준 경계는 `navigate_to`, 엔진이 삽입한 trusted prelude,
그리고 **등록된 `risk_level="read"` 이면서 비물리인 도구**만 허용한다. 비물리라도
쓰기/action 도구는 거부한다. 일반 작업(스코프 없음) 범위는 바꾸지 않는다.
"""

import json
import logging
from types import SimpleNamespace

import pytest
from robo_claw_agent.agent_node.map_goal.contract import MapGoalContext, canonical_goal_message
from robo_claw_agent.agent_node.map_goal.scope import MapGoalScope, map_goal_scope
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
DELEGATION_SKILLS = (
    "delegate_task",
    "call_peer_robot",
    "broadcast_to_peers",
    "coordinate_peer_task",
)


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


def _make_delegation_skill(name: str):
    class _Delegation(BaseSkill):
        # BaseSkill.__init_subclass__ 는 클래스 생성 시점에 name 을 요구한다.
        name = "delegate_task"
        risk_level = "action"

        def __init__(self) -> None:
            super().__init__()
            self.calls: list[dict] = []

        def execute(self, params):
            self.calls.append(dict(params))
            return self.success_result("전달 완료")

    _Delegation.name = name
    return _Delegation()


class _ReadonlyStatusSkill(BaseSkill):
    name = "get_status"
    risk_level = "read"

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    def execute(self, params):
        self.calls.append(dict(params))
        return self.success_result("상태 조회 완료")


class _ReadonlyDelegatorSkill(BaseSkill):
    """읽기 전용으로 등록되지만 내부에서 원격 위임 도구를 호출하는 우회 스킬."""

    name = "inspect_and_delegate"
    risk_level = "read"

    def __init__(self) -> None:
        super().__init__()
        self._skills: SkillManager | None = None
        self.nested: list = []

    def execute(self, params):
        assert self._skills is not None
        self.nested = [
            self._skills.execute(
                "delegate_task", {"agent_id": "robot2", "instruction": "주방으로 가"}, 5.0
            ),
            self._skills.execute(
                "call_peer_robot", {"peer_name": "peer-b", "instruction": "가"}, 5.0
            ),
            self._skills.execute("broadcast_to_peers", {"message": "모두 이동"}, 5.0),
            self._skills.execute(
                "coordinate_peer_task", {"peer_name": "robot3", "instruction": "가"}, 5.0
            ),
            self._skills.execute("get_status", {}, 5.0),
        ]
        return self.success_result("점검 완료")


class _NonPhysicalActionSkill(BaseSkill):
    name = "save_location"
    risk_level = "action"

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    def execute(self, params):
        self.calls.append(dict(params))
        return self.success_result("저장 완료")


class _NavigateSkill(BaseSkill):
    name = "navigate_to"
    side_effects = ("base_motion",)
    exclusive_resources = ("base_control",)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    def execute(self, params):
        self.calls.append(dict(params))
        return self.success_result("이동 완료")


class _StowSkill(BaseSkill):
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


def _build_skills() -> tuple[SkillManager, dict]:
    skills = SkillManager()
    registry = {name: _make_delegation_skill(name) for name in DELEGATION_SKILLS}
    registry["get_status"] = _ReadonlyStatusSkill()
    registry["save_location"] = _NonPhysicalActionSkill()
    registry["navigate_to"] = _NavigateSkill()
    registry["stow_for_navigation"] = _StowSkill()
    registry["cloid_navigation_pose"] = _PoseSkill()
    delegator = _ReadonlyDelegatorSkill()
    registry["inspect_and_delegate"] = delegator
    for skill in registry.values():
        skills.register(skill)
    delegator._skills = skills
    return skills, registry


class _RuntimeMetrics:
    def record(self, *args, **kwargs) -> None:
        pass


class _Memory:
    def get_conversation_history(self, n):
        return []

    def add_event(self, *args, **kwargs) -> None:
        pass


class _PlannerNode:
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

    def __init__(self, turns, skills, *, tool_names=()) -> None:
        self.turns = iter(turns)
        self.requests: list[list[dict]] = []
        self._logger = logging.getLogger("map_goal_low_boundary")
        self._llm = SimpleNamespace(supports_tools=lambda: True, chat_with_tools=self._chat)
        self._skills = skills
        self._tool_names = list(tool_names)

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
        return build_tool_specs([{"name": name} for name in self._tool_names])

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


# ── P1: nested 원격 위임 차단 ─────────────────────────────────────────────


def test_low_boundary_blocks_nested_remote_delegation_in_map_scope():
    skills, registry = _build_skills()

    with map_goal_scope(_scope()):
        wrapper = skills.execute("inspect_and_delegate", {}, 5.0)

    assert wrapper.success is True, "읽기 전용 껍데기 자체는 허용된다"
    for name in DELEGATION_SKILLS:
        assert registry[name].calls == [], f"{name} 이 지도 목표 요청에서 실행되면 안 된다"
    nested_results = registry["inspect_and_delegate"].nested
    assert [result.success for result in nested_results[:4]] == [False, False, False, False]
    # 읽기 전용/비물리 도구는 계속 허용된다.
    assert nested_results[4].success is True
    assert registry["get_status"].calls == [{}]


def test_low_boundary_blocks_nonphysical_action_skill():
    skills, registry = _build_skills()

    with map_goal_scope(_scope()):
        result = skills.execute("save_location", {"name": "거실"}, 5.0)

    assert result.success is False
    assert registry["save_location"].calls == []
    assert "읽기 전용" in result.message


def test_low_boundary_allows_readonly_and_navigation_and_trusted_prelude():
    from robo_claw_agent.domain.map_goal_guard import trusted_prelude

    skills, registry = _build_skills()

    with map_goal_scope(_scope()):
        status = skills.execute("get_status", {}, 5.0)
        navigation = skills.execute("navigate_to", {"x": TARGET_X, "y": TARGET_Y}, 5.0)
        blocked_stow = skills.execute("stow_for_navigation", {}, 5.0)

    assert status.success is True
    assert navigation.success is True
    assert blocked_stow.success is False

    with map_goal_scope(_scope()), trusted_prelude("stow_for_navigation"):
        allowed_stow = skills.execute("stow_for_navigation", {}, 5.0)

    assert allowed_stow.success is True
    assert registry["stow_for_navigation"].calls == [{}]


def test_nested_readonly_delegation_still_works_in_ordinary_requests():
    """일반 작업(지도 목표 스코프 없음)의 동작 범위는 바뀌지 않는다."""
    skills, registry = _build_skills()

    wrapper = skills.execute("inspect_and_delegate", {}, 5.0)

    assert wrapper.success is True
    for name in DELEGATION_SKILLS:
        assert registry[name].calls, f"일반 작업에서는 {name} 이 기존대로 실행돼야 한다"
    assert registry["get_status"].calls == [{}]


def test_ordinary_request_delegation_skill_is_allowed():
    skills, registry = _build_skills()

    result = skills.execute(
        "delegate_task", {"agent_id": "robot2", "instruction": "주방으로 가"}, 5.0
    )

    assert result.success is True
    assert registry["delegate_task"].calls


# ── 일반 native stow/CLOi prelude 동작 유지 ───────────────────────────────


async def test_ordinary_native_cloid_internal_prelude_still_runs_before_nav():
    skills, registry = _build_skills()
    node = _PlannerNode(
        [
            ToolTurn(
                tool_calls=[ToolCall(name="navigate_to", arguments={"x": TARGET_X, "y": TARGET_Y})]
            ),
            ToolTurn(content="이동했습니다."),
        ],
        skills,
        tool_names=("navigate_to", "get_status"),
    )
    planner = LLMPlanner(node)

    await planner.run_llm_planning_loop(
        _goal_handle(),
        "거실로 이동해",
        [{"role": "user", "content": "거실로 이동해"}],
        {},
        5,
        lambda *a: None,
    )

    assert registry["cloid_navigation_pose"].calls == [{}]
    assert registry["navigate_to"].calls == [{"x": TARGET_X, "y": TARGET_Y}]


def test_ordinary_navigation_preparation_helpers_still_execute_skills():
    """raw Nav2 경로가 쓰는 prepare_* helper 는 스코프가 없으면 그대로 동작한다."""
    from robo_claw_agent.agent_node.nav_safety import prepare_navigation

    skills, registry = _build_skills()
    node = SimpleNamespace(_skills=skills, _manipulation_backend=None)

    ok, error = prepare_navigation(node)

    assert (ok, error) == (True, "")
    assert registry["cloid_navigation_pose"].calls == [{"timeout_sec": 60.0}]
