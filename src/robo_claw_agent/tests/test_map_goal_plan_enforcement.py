"""지도 목표 요청의 계획 검증/스코프/기록 계약 테스트.

대시보드의 지도 선택은 표준 문장 + 검증된 컨텍스트로만 들어오고, 에이전트는
LLM 이 고른 계획이 그 좌표/프레임과 정확히 같을 때만 실제 이동을 실행한다.
컨텍스트는 스킬 호출을 강제하지 않으며, 요청 스코프는 요청 밖으로 새지 않는다.
"""

import asyncio
import json
import logging
import math
import time
from types import SimpleNamespace

import pytest
from robo_claw_agent.agent_node.map_goal.contract import (
    CONTEXT_TYPE,
    MapGoalContext,
    canonical_goal_message,
)
from robo_claw_agent.agent_node.map_goal.enforcement import (
    apply_map_goal_safety,
    map_goal_system_context,
    validate_map_goal_chain,
)
from robo_claw_agent.agent_node.map_goal.scope import (
    MapGoalScope,
    activate_map_goal_scope,
    current_map_goal_scope,
    map_goal_scope,
)
from robo_claw_agent.agent_node.map_goal.tracker import (
    STATUS_FAILED,
    STATUS_REACHED,
    STATUS_UNKNOWN,
    MapGoalTracker,
    get_map_goal_tracker,
)
from robo_claw_agent.agent_node.planner import LLMPlanner
from robo_claw_agent.llm_bridge import ToolCall, ToolTurn
from robo_claw_agent.skill_manager import BaseSkill, SkillManager
from robo_claw_agent.skills.tool_specs import build_tool_specs

pytestmark = pytest.mark.unit

ROBOT_ID = "robot1"
FRAME = "map"
TARGET_X = 1.234568
TARGET_Y = -2.0
GEOMETRY_ID = "7566e0b34bb04228c0dc8850c6657f6dd941e70eb9f94189856ed903fa5bb8e2"


def _context(**overrides) -> MapGoalContext:
    now = time.time()
    values = {
        "request_id": "req-0001",
        "robot_id": ROBOT_ID,
        "x": TARGET_X,
        "y": TARGET_Y,
        "frame_id": FRAME,
        "geometry_id": GEOMETRY_ID,
        "map_topic": "/map",
        "created_at": now,
        "expires_at": now + 60.0,
    }
    values.update(overrides)
    return MapGoalContext(**values)


def _context_json(context: MapGoalContext | None = None) -> str:
    context = context or _context()
    return json.dumps(
        {
            "type": CONTEXT_TYPE,
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


def _scope(**overrides) -> MapGoalScope:
    return MapGoalScope(context=_context(**overrides), navigation_frame=FRAME)


# ── 스킬 더블 ──────────────────────────────────────────────────────────────


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


class _RotateSkill(BaseSkill):
    name = "rotate"
    side_effects = ("base_rotation",)
    exclusive_resources = ("base_control",)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    def execute(self, params):
        self.calls.append(dict(params))
        return self.success_result("회전 완료")


class _PoseSkill(BaseSkill):
    name = "cloid_navigation_pose"
    side_effects = ("arm_motion",)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    def execute(self, params):
        self.calls.append(dict(params))
        return self.success_result("자세 요청 완료")


class _StatusSkill(BaseSkill):
    name = "get_status"
    risk_level = "read"
    answer_mode = "informational"

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    def execute(self, params):
        self.calls.append(dict(params))
        return self.success_result("상태 조회 완료")


class _RuntimeMetrics:
    def record(self, *args, **kwargs) -> None:
        pass


class _Memory:
    def get_conversation_history(self, n):
        return []

    def add_event(self, *args, **kwargs) -> None:
        pass

    def summary(self):
        return {}

    def search_knowledge(self, *args, **kwargs):
        return []


class _PlannerNode:
    """LLMPlanner 가 요구하는 최소 노드 + 실제 SkillManager."""

    _llm_tool_calling = True
    _enable_skill_learning = False
    _enable_rag = False
    _multiturn_n = 5
    _agent_id = ROBOT_ID
    _map_frame = FRAME
    _state = 0
    _memory = _Memory()
    _manipulation_backend = None
    _enable_task_decomposition = False

    def __init__(self, turns, *, with_pose_prelude: bool = False) -> None:
        self.turns = iter(turns)
        self.requests: list[list[dict]] = []
        self._logger = logging.getLogger("map_goal_planner")
        self._rules: list[dict] = []
        self._llm = SimpleNamespace(
            supports_tools=lambda: True,
            chat_with_tools=self._chat,
            chat=self._chat_text,
        )
        self._skills = SkillManager()
        self._navigate = _NavigateSkill()
        self._rotate = _RotateSkill()
        self._status = _StatusSkill()
        self._pose = _PoseSkill()
        self._skills.register(self._navigate)
        self._skills.register(self._rotate)
        self._skills.register(self._status)
        if with_pose_prelude:
            self._skills.register(self._pose)

    def _chat(self, messages, **kwargs):
        self.requests.append(list(messages))
        return next(self.turns)

    def _chat_text(self, messages, **kwargs):
        return json.dumps({"skill": None, "response": "완료"})

    def get_logger(self):
        return self._logger

    def _set_state(self, *args, **kwargs):
        pass

    def _build_system_prompt(self):
        return "system"

    def _tool_specs(self):
        return build_tool_specs([{"name": "navigate_to"}, {"name": "get_status"}])

    def _validate_skill_chain(self, chain):
        from robo_claw_agent.agent_node.execution import ExecutionMixin

        return ExecutionMixin._validate_skill_chain(self, chain)

    async def _run_blocking(self, op, func, *args, **kwargs):
        return func(*args, **kwargs)


class _ScopeNode:
    """스코프/기록 단위 테스트용 노드 스텁."""

    def __init__(self, agent_id: str = ROBOT_ID, map_frame: str = FRAME) -> None:
        self._agent_id = agent_id
        self._map_frame = map_frame


def _goal_handle(context_json: str = ""):
    return SimpleNamespace(request=SimpleNamespace(context_json=context_json))


async def _run_native(node, instruction: str, scope: MapGoalScope | None):
    planner = LLMPlanner(node)
    messages = [{"role": "user", "content": instruction}]
    send_fb = lambda *args: None  # noqa: E731
    if scope is None:
        return await planner.run_llm_planning_loop(
            _goal_handle(), instruction, messages, {}, 5, send_fb
        )
    with map_goal_scope(scope):
        return await planner.run_llm_planning_loop(
            _goal_handle(_context_json(scope.context)),
            instruction,
            messages,
            {},
            5,
            send_fb,
        )


CANONICAL_INSTRUCTION = (
    "지도에서 선택한 목표로 이동해 주세요. 로봇: robot1. 기준 프레임: map. "
    "목표 좌표: x=1.234568 m, y=-2.000000 m. 필요한 주행 안전 절차를 먼저 적용하고, "
    "목표 좌표와 프레임을 변경하지 마세요."
)


# ── 스코프 활성화 (로봇 ID/프레임/만료) ─────────────────────────────────────


def test_activate_scope_accepts_valid_context():
    node = _ScopeNode()
    scope, error = activate_map_goal_scope(
        node, _goal_handle(_context_json()), CANONICAL_INSTRUCTION
    )

    assert error == ""
    assert scope is not None
    assert scope.request_id == "req-0001"
    assert scope.navigation_frame == FRAME
    assert 0.0 < scope.remaining_budget_sec() <= 180.0
    assert get_map_goal_tracker(node).get("req-0001")["status"] == "queued"


def test_activate_scope_leaves_ordinary_requests_untouched():
    scope, error = activate_map_goal_scope(
        _ScopeNode(), _goal_handle("센서를 먼저 점검하세요."), "센서를 먼저 점검하세요."
    )
    assert (scope, error) == (None, "")
    scope, error = activate_map_goal_scope(_ScopeNode(), _goal_handle(""), "안녕")
    assert (scope, error) == (None, "")


def test_activate_scope_rejects_other_robot():
    scope, error = activate_map_goal_scope(
        _ScopeNode(agent_id="robot2"), _goal_handle(_context_json()), CANONICAL_INSTRUCTION
    )
    assert scope is None
    assert "로봇 ID" in error


def test_activate_scope_rejects_other_navigation_frame():
    scope, error = activate_map_goal_scope(
        _ScopeNode(map_frame="odom"), _goal_handle(_context_json()), CANONICAL_INSTRUCTION
    )
    assert scope is None
    assert "프레임" in error


def test_activate_scope_rejects_expired_selection():
    expired = _context(created_at=1.0, expires_at=61.0)
    scope, error = activate_map_goal_scope(
        _ScopeNode(), _goal_handle(_context_json(expired)), CANONICAL_INSTRUCTION
    )
    assert scope is None
    assert "유효 시간" in error


def test_activate_scope_does_not_rerun_duplicate_request():
    node = _ScopeNode()
    scope, error = activate_map_goal_scope(
        node, _goal_handle(_context_json()), CANONICAL_INSTRUCTION
    )
    assert scope is not None
    get_map_goal_tracker(node).transition(scope.request_id, STATUS_REACHED, message="도착")

    replay_scope, replay_error = activate_map_goal_scope(
        node, _goal_handle(_context_json()), CANONICAL_INSTRUCTION
    )

    assert replay_scope is None
    assert "이미 접수된" in replay_error
    assert get_map_goal_tracker(node).get(scope.request_id)["status"] == STATUS_REACHED


def test_activate_scope_rejects_same_id_with_different_target():
    node = _ScopeNode()
    activate_map_goal_scope(node, _goal_handle(_context_json()), CANONICAL_INSTRUCTION)
    other = _context_json(_context(x=TARGET_X + 1.0))
    other_instruction = canonical_goal_message(ROBOT_ID, FRAME, TARGET_X + 1.0, TARGET_Y)

    scope, error = activate_map_goal_scope(node, _goal_handle(other), other_instruction)

    assert scope is None
    assert "다른 목표" in error


def test_activate_scope_rejects_message_that_does_not_match_context():
    mismatched = (
        "지도에서 선택한 목표로 이동해 주세요. 로봇: robot1. 기준 프레임: map. "
        "목표 좌표: x=9.000000 m, y=-2.000000 m. 필요한 주행 안전 절차를 먼저 적용하고, "
        "목표 좌표와 프레임을 변경하지 마세요."
    )

    scope, error = activate_map_goal_scope(_ScopeNode(), _goal_handle(_context_json()), mismatched)

    assert scope is None
    assert "일치하지 않습니다" in error


def test_canonical_message_matches_own_formatter():
    context = _context()
    assert canonical_goal_message(ROBOT_ID, FRAME, TARGET_X, TARGET_Y) == CANONICAL_INSTRUCTION
    assert canonical_goal_message(ROBOT_ID, FRAME, context.x, context.y) == CANONICAL_INSTRUCTION


def test_activate_scope_rejects_malformed_map_context_instead_of_falling_back():
    scope, error = activate_map_goal_scope(
        _ScopeNode(), _goal_handle('{"type": "dashboard_map_goal_v1",'), CANONICAL_INSTRUCTION
    )
    assert scope is None
    assert error


# ── 계획 검증 ──────────────────────────────────────────────────────────────


def _validate(chain, scope=None):
    return validate_map_goal_chain(chain, scope or _scope(), _FakeSkills())


class _FakeSkills:
    def __init__(self) -> None:
        self._registry = {
            "navigate_to": _NavigateSkill(),
            "rotate": _RotateSkill(),
            "get_status": _StatusSkill(),
        }

    def get_skill(self, name):
        return self._registry.get(name)


def test_planned_navigation_with_same_coordinates_is_accepted():
    chain = [{"skill": "navigate_to", "params": {"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME}}]
    kept, error = _validate(chain)
    assert error == ""
    assert kept == chain


def test_readonly_tool_is_allowed_before_navigation():
    chain = [
        {"skill": "get_status", "params": {}},
        {"skill": "navigate_to", "params": {"x": TARGET_X, "y": TARGET_Y}},
    ]
    _, error = _validate(chain)
    assert error == ""


@pytest.mark.parametrize(
    "params",
    [
        {"x": TARGET_X + 0.5, "y": TARGET_Y},
        {"x": TARGET_X, "y": TARGET_Y + 0.5},
        {"x": -TARGET_X, "y": TARGET_Y},
        {"x": TARGET_Y, "y": TARGET_X},
        {"x": TARGET_X, "y": TARGET_Y, "frame_id": "odom"},
        {"x": TARGET_X, "y": TARGET_Y, "target_name": "거실"},
        {"x": TARGET_X, "y": TARGET_Y, "yaw": 1.57},
        {"x": TARGET_X, "y": TARGET_Y, "align_heading_first": True},
        {"target_name": "거실"},
        {"y": TARGET_Y},
    ],
)
def test_planned_navigation_with_substituted_target_is_rejected(params):
    _, error = _validate([{"skill": "navigate_to", "params": params}])
    assert error


def test_unrequested_motion_skill_is_rejected():
    chain = [
        {"skill": "navigate_to", "params": {"x": TARGET_X, "y": TARGET_Y}},
        {"skill": "rotate", "params": {"angle_deg": 90.0}},
    ]
    _, error = _validate(chain)
    assert "rotate" in error


def test_two_navigations_are_rejected():
    chain = [
        {"skill": "navigate_to", "params": {"x": TARGET_X, "y": TARGET_Y}},
        {"skill": "navigate_to", "params": {"x": TARGET_X, "y": TARGET_Y}},
    ]
    _, error = _validate(chain)
    assert "한 번" in error


def test_plan_without_navigation_is_rejected():
    _, error = _validate([{"skill": "get_status", "params": {}}])
    assert "navigate_to" in error


def test_safety_prelude_is_inserted_after_validation_and_kept():
    skills = SkillManager()
    skills.register(_PoseSkill())
    skills.register(_NavigateSkill())
    chain = [{"skill": "navigate_to", "params": {"x": TARGET_X, "y": TARGET_Y}}]

    protected, error = apply_map_goal_safety(
        chain, _scope(), skills, logging.getLogger("map_goal_safety")
    )

    assert error == ""
    assert [item["skill"] for item in protected] == ["cloid_navigation_pose", "navigate_to"]
    assert protected[0].get("_policy_prelude") is True


def test_safety_prelude_is_not_inserted_for_rejected_plan():
    skills = SkillManager()
    skills.register(_PoseSkill())
    skills.register(_NavigateSkill())
    chain = [{"skill": "navigate_to", "params": {"x": TARGET_X + 1.0, "y": TARGET_Y}}]

    protected, error = apply_map_goal_safety(
        chain, _scope(), skills, logging.getLogger("map_goal_safety")
    )

    assert protected == []
    assert error


def test_system_context_carries_typed_data_only():
    context = map_goal_system_context(_scope())
    assert "x=1.234568" in context
    assert "frame_id=map" in context
    assert GEOMETRY_ID in context
    assert "[skill]" not in context


# ── 실제 planner + SkillManager 통합 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_planner_executes_selected_navigation_once_with_same_coordinates():
    node = _PlannerNode(
        [
            ToolTurn(
                tool_calls=[
                    ToolCall(
                        name="navigate_to",
                        arguments={"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME},
                    )
                ]
            ),
            ToolTurn(content="이동을 시작했습니다."),
        ]
    )

    result, results, success, _ = await _run_native(node, CANONICAL_INSTRUCTION, _scope())

    assert success is True
    assert node._navigate.calls == [{"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME}]
    assert [res.skill_name for res in results] == ["navigate_to"]
    assert result == "이동을 시작했습니다."


@pytest.mark.asyncio
async def test_planner_rejects_wrong_coordinates_before_dispatch():
    node = _PlannerNode(
        [
            ToolTurn(
                tool_calls=[
                    ToolCall(name="navigate_to", arguments={"x": TARGET_X + 0.4, "y": TARGET_Y})
                ]
            ),
            ToolTurn(content="좌표를 확인하지 못해 이동하지 않았습니다."),
        ]
    )

    _, results, _, _ = await _run_native(node, CANONICAL_INSTRUCTION, _scope())

    assert node._navigate.calls == []
    assert results == []
    followup = node.requests[1]
    tool_messages = [m for m in followup if m.get("role") == "tool"]
    assert tool_messages and "좌표" in tool_messages[0]["content"]


@pytest.mark.asyncio
async def test_planner_rejects_extra_motion_before_any_dispatch():
    node = _PlannerNode(
        [
            ToolTurn(
                tool_calls=[
                    ToolCall(name="navigate_to", arguments={"x": TARGET_X, "y": TARGET_Y}),
                    ToolCall(name="rotate", arguments={"angle_deg": 90.0}),
                ]
            ),
            ToolTurn(content="요청하지 않은 회전은 실행하지 않았습니다."),
        ]
    )

    await _run_native(node, CANONICAL_INSTRUCTION, _scope())

    assert node._navigate.calls == []
    assert node._rotate.calls == []


@pytest.mark.asyncio
async def test_planner_keeps_safety_prelude_before_map_navigation():
    node = _PlannerNode(
        [
            ToolTurn(
                tool_calls=[ToolCall(name="navigate_to", arguments={"x": TARGET_X, "y": TARGET_Y})]
            ),
            ToolTurn(content="이동을 시작했습니다."),
        ],
        with_pose_prelude=True,
    )

    await _run_native(node, CANONICAL_INSTRUCTION, _scope())

    assert node._pose.calls == [{}]
    assert node._navigate.calls == [{"x": TARGET_X, "y": TARGET_Y}]


@pytest.mark.asyncio
async def test_map_context_never_forces_navigation_skill():
    """컨텍스트만으로 이동을 강제하지 않는다(모델이 고르지 않으면 실행 없음)."""
    node = _PlannerNode([ToolTurn(content="목표가 불명확합니다. 지도에서 다시 선택해 주세요.")])

    result, results, _, _ = await _run_native(node, "거실로 이동해", _scope())

    assert node._navigate.calls == []
    assert results == []
    assert "다시 선택" in result


@pytest.mark.asyncio
async def test_ordinary_request_is_unaffected_by_map_goal_rules():
    node = _PlannerNode(
        [
            ToolTurn(tool_calls=[ToolCall(name="navigate_to", arguments={"target_name": "거실"})]),
            ToolTurn(content="이동했습니다."),
        ]
    )

    await _run_native(node, "거실로 이동해", None)

    assert node._navigate.calls == [{"target_name": "거실"}]


# ── 컨텍스트 주입/스레드 경계 전파 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_task_context_injects_typed_context_instead_of_raw_json():
    node = _PlannerNode([])
    node._llm = SimpleNamespace(supports_tools=lambda: True)
    node._get_robot_health_state = lambda summary: {}
    scope = _scope()

    with map_goal_scope(scope):
        messages, _ = await LLMPlanner(node).collect_task_context(
            CANONICAL_INSTRUCTION, _goal_handle(_context_json())
        )

    system_messages = [m["content"] for m in messages if m["role"] == "system"]
    assert any("지도 목표 컨텍스트" in text for text in system_messages)
    assert not any('"type": "dashboard_map_goal_v1"' in text for text in system_messages)


@pytest.mark.asyncio
async def test_legacy_context_json_is_injected_verbatim():
    node = _PlannerNode([])
    node._llm = SimpleNamespace(supports_tools=lambda: True)
    node._get_robot_health_state = lambda summary: {}

    messages, _ = await LLMPlanner(node).collect_task_context(
        "센서를 점검해", _goal_handle("센서를 먼저 점검하세요.")
    )

    assert any("센서를 먼저 점검하세요." in m["content"] for m in messages if m["role"] == "system")


@pytest.mark.asyncio
async def test_scope_propagates_into_worker_thread_and_not_outside():
    from robo_claw_agent.agent_node.execution import ExecutionMixin

    class _Node(ExecutionMixin):
        _runtime_metrics = _RuntimeMetrics()

        def get_logger(self):
            return logging.getLogger("map_goal_thread")

    node = _Node()
    scope = _scope()

    assert current_map_goal_scope() is None
    with map_goal_scope(scope):
        seen = await node._run_blocking("probe", current_map_goal_scope)
        assert seen is scope
    assert current_map_goal_scope() is None
    assert await node._run_blocking("probe", current_map_goal_scope) is None


@pytest.mark.asyncio
async def test_concurrent_requests_keep_isolated_scopes():
    from robo_claw_agent.agent_node.execution import ExecutionMixin

    class _Node(ExecutionMixin):
        _runtime_metrics = _RuntimeMetrics()

        def get_logger(self):
            return logging.getLogger("map_goal_thread")

    node = _Node()
    scope = _scope()

    async def scoped():
        with map_goal_scope(scope):
            return await node._run_blocking("probe", current_map_goal_scope)

    async def ordinary():
        await asyncio.sleep(0)
        return await node._run_blocking("probe", current_map_goal_scope)

    scoped_result, ordinary_result = await asyncio.gather(scoped(), ordinary())

    assert scoped_result is scope
    assert ordinary_result is None


# ── 기록기 멱등성/lease ────────────────────────────────────────────────────


def test_tracker_is_idempotent_for_same_request_id_and_payload():
    tracker = MapGoalTracker()
    context = _context()

    first, error = tracker.admit(context)
    tracker.transition(context.request_id, STATUS_REACHED, message="도착")

    replay, replay_error = tracker.admit(context)

    assert error == replay_error == ""
    assert first["request_id"] == replay["request_id"]
    assert replay["status"] == STATUS_REACHED


def test_tracker_rejects_same_request_id_with_different_payload():
    tracker = MapGoalTracker()
    tracker.admit(_context())

    record, error = tracker.admit(_context(x=TARGET_X + 1.0))

    assert record is None
    assert error == "same_request_id_different_payload"


def test_tracker_rejects_new_request_while_active():
    tracker = MapGoalTracker()
    tracker.admit(_context())

    record, error = tracker.admit(_context(request_id="req-0002"))

    assert record is None
    assert error == "active_request_in_progress"


def test_unknown_outcome_holds_lease_until_authoritative_evidence():
    tracker = MapGoalTracker()
    context = _context()
    tracker.admit(context)
    tracker.transition(context.request_id, STATUS_UNKNOWN, reason="navigation_result_unknown")

    record, error = tracker.admit(_context(request_id="req-0002"))
    assert record is None
    assert error == "previous_request_unconfirmed"

    # 같은 요청/같은 목표의 권위 있는 종료 증거만 lease 를 푼다.
    tracker.apply_evidence(
        {
            "request_id": context.request_id,
            "frame_id": FRAME,
            "x": TARGET_X,
            "y": TARGET_Y,
            "geometry_id": GEOMETRY_ID,
            "status": STATUS_REACHED,
        }
    )

    record, error = tracker.admit(_context(request_id="req-0003"))
    assert error == ""
    assert record["request_id"] == "req-0003"


def test_evidence_from_other_target_is_ignored():
    tracker = MapGoalTracker()
    context = _context()
    tracker.admit(context)

    applied = tracker.apply_evidence(
        {
            "request_id": context.request_id,
            "frame_id": FRAME,
            "x": TARGET_X + 5.0,
            "y": TARGET_Y,
            "status": STATUS_REACHED,
        }
    )

    assert applied is None
    assert tracker.get(context.request_id)["status"] == "queued"


def test_tracker_records_are_bounded():
    tracker = MapGoalTracker(max_records=2)
    for index in range(5):
        context = _context(request_id=f"req-{index:04d}")
        tracker.admit(context)
        tracker.transition(context.request_id, STATUS_FAILED, reason="testing")

    # 가장 최근 기록은 남아 있고, 오래된 종료 기록은 정리된다.
    assert tracker.get("req-0004") is not None
    assert tracker.get("req-0000") is None


def test_malformed_map_context_never_becomes_plain_text():
    from robo_claw_agent.agent_node.map_goal.contract import MapGoalError, parse_context_json

    for raw in (
        "",
        "{}",
    ):
        assert parse_context_json(raw) is None
    with pytest.raises(MapGoalError):
        parse_context_json('{"type": "dashboard_map_goal_v1", "x": NaN}')


def test_negative_zero_coordinate_is_normalized_in_context():
    from robo_claw_agent.agent_node.map_goal.contract import parse_context_json

    payload = json.loads(_context_json())
    payload["y"] = -0.0
    context = parse_context_json(json.dumps(payload))

    assert context is not None
    assert math.copysign(1.0, context.y) == 1.0
