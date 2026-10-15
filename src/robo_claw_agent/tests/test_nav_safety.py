import importlib.util
from pathlib import Path

_NAV_SAFETY_PATH = (
    Path(__file__).resolve().parents[1] / "robo_claw_agent" / "agent_node" / "nav_safety.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "robo_claw_agent.agent_node.nav_safety", _NAV_SAFETY_PATH
)
_NAV_SAFETY = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_NAV_SAFETY)

_ensure = _NAV_SAFETY.ensure_stretch_navigation_safety
_prepare = _NAV_SAFETY.prepare_stretch_navigation


class _Logger:
    def info(self, msg):
        assert isinstance(msg, str)


class _SkillsWithStow:
    def get_skill(self, name):
        return object() if name == "stow_for_navigation" else None


class _SkillsWithoutStow:
    def get_skill(self, _name):
        return None


class _Result:
    def __init__(self, success, message=""):
        self.success = success
        self.message = message


class _Backend:
    def __init__(self, stowed=False):
        self.stowed = stowed

    def is_stowed(self):
        return self.stowed


class _SkillsWithExecutableStow:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def get_skill(self, name):
        return object() if name == "stow_for_navigation" else None

    def execute(self, name, params, timeout_sec):
        self.calls.append((name, params, timeout_sec))
        return self.result


class _Node:
    def __init__(self, backend, skills):
        self._manipulation_backend = backend
        self._skills = skills


class _SelfContainedBaseMotionSkill:
    side_effects = ("base_motion", "arm_motion", "gripper_motion")
    allow_with_others = False


class _SkillsWithSelfContainedPick:
    def get_skill(self, name):
        if name == "stow_for_navigation":
            return object()
        if name == "adaptive_pick_object":
            return _SelfContainedBaseMotionSkill()
        return None


def test_prepends_stow_for_base_motion():
    chain = [{"skill": "navigate_to", "params": {"target_name": "메인룸"}}]

    result = _ensure(chain, _SkillsWithStow(), _Logger())

    assert result == [
        {"skill": "stow_for_navigation", "params": {}},
        {"skill": "navigate_to", "params": {"target_name": "메인룸"}},
    ]


def test_skips_non_stretch_robot():
    chain = [{"skill": "navigate_to", "params": {"target_name": "메인룸"}}]

    assert _ensure(chain, _SkillsWithoutStow(), _Logger()) == chain


def test_does_not_duplicate_stow():
    chain = [
        {"skill": "stow_for_navigation", "params": {}},
        {"skill": "navigate_to", "params": {"target_name": "메인룸"}},
    ]

    assert _ensure(chain, _SkillsWithStow(), _Logger()) == chain


def test_inserts_stow_immediately_before_base_motion_not_at_front():
    """조작 스킬이 이동보다 앞서면 stow는 이동 '직전'에 들어가야 한다.

    맨 앞에만 넣으면 조작으로 팔을 편 뒤 그대로 주행하게 된다.
    """
    chain = [
        {"skill": "pick_object", "params": {}},
        {"skill": "navigate_to", "params": {"target_name": "주방"}},
    ]

    result = _ensure(chain, _SkillsWithStow(), _Logger())

    assert [item["skill"] for item in result] == [
        "pick_object",
        "stow_for_navigation",
        "navigate_to",
    ]


def test_inserts_stow_before_every_base_motion_in_chain():
    """이동 → 조작 → 이동 체인에서는 stow가 두 번 들어가야 한다."""
    chain = [
        {"skill": "navigate_to", "params": {"target_name": "거실"}},
        {"skill": "grasp", "params": {}},
        {"skill": "navigate_to", "params": {"target_name": "주방"}},
    ]

    result = _ensure(chain, _SkillsWithStow(), _Logger())

    assert [item["skill"] for item in result] == [
        "stow_for_navigation",
        "navigate_to",
        "grasp",
        "stow_for_navigation",
        "navigate_to",
    ]


def test_leaves_non_motion_chain_untouched():
    chain = [{"skill": "rag_search", "params": {"query": "주방"}}]

    assert _ensure(chain, _SkillsWithStow(), _Logger()) == chain


def test_does_not_prepend_stow_to_self_contained_base_motion_skill():
    chain = [{"skill": "adaptive_pick_object", "params": {"target_object": "cup"}}]

    assert _ensure(chain, _SkillsWithSelfContainedPick(), _Logger()) == chain


def test_prepare_navigation_skips_stow_when_backend_is_already_stowed():
    skills = _SkillsWithExecutableStow(_Result(True))

    assert _prepare(_Node(_Backend(stowed=True), skills)) == (True, "")
    assert skills.calls == []


def test_prepare_navigation_rejects_when_stow_fails():
    skills = _SkillsWithExecutableStow(_Result(False, "service timeout"))

    ready, reason = _prepare(_Node(_Backend(), skills))

    assert ready is False
    assert "실패" in reason
    assert skills.calls[0][0] == "stow_for_navigation"


def test_prepare_navigation_requires_actual_stow_after_command():
    backend = _Backend(stowed=False)
    skills = _SkillsWithExecutableStow(_Result(True, "command accepted"))

    ready, reason = _prepare(_Node(backend, skills))

    assert ready is False
    assert "실제 팔 자세" in reason


# ── CLOiD 주행 전 boxing 자세 prelude ───────────────────────────────────

_ensure_nav = _NAV_SAFETY.ensure_navigation_safety
_ensure_cloid = _NAV_SAFETY.ensure_cloid_navigation_safety
_prepare_cloid = _NAV_SAFETY.prepare_cloid_navigation
_prepare_nav = _NAV_SAFETY.prepare_navigation
_ready = _NAV_SAFETY.is_cloid_navigation_pose_ready
_mark_ready = _NAV_SAFETY.mark_cloid_navigation_pose_ready
_on_skill_succeeded = _NAV_SAFETY.on_skill_succeeded
_CLOID_POSE_SKILL = _NAV_SAFETY.CLOID_NAV_POSE_SKILL
_POLICY_PRELUDE_FLAG = _NAV_SAFETY.POLICY_PRELUDE_FLAG


class _NamedSkill:
    """실제 등록 스킬처럼 name/side_effects 를 갖는 테스트 더블.

    nav_safety 는 테스트 더블(MagicMock)을 실제 등록 스킬로 오인하지 않으므로,
    이름과 메타데이터를 가진 가짜 스킬로 게이트 동작을 검증한다.
    """

    def __init__(self, name, side_effects=(), allow_with_others=True, terminal_behavior="terminal"):
        self.name = name
        self.side_effects = tuple(side_effects)
        self.allow_with_others = allow_with_others
        self.terminal_behavior = terminal_behavior


class _StretchDriverBackend(_Backend):
    """`_uses_stretch_backend` 가 Stretch 백엔드로 인식하는 더블(is_stowed 보유)."""


class _MoveItBackend:
    """팔 정책이 없는 moveit 로봇 백엔드 더블(is_stowed 없음)."""


def _cloid_robot_skill_manager(base_motion=("navigate_to",), other=(), result=None):
    """cloid_navigation_pose 가 등록된 CLOiD 스킬 매니저 더블.

    `base_motion` 은 베이스가 움직이는 스킬(자세 삽입 대상), `other` 는 조작 등
    삽입 대상이 아닌 스킬로 등록한다.
    """

    class _Skills:
        def __init__(self):
            self.result = result or _Result(True, "pose ok")
            self.calls = []
            self.registered = {
                _CLOID_POSE_SKILL: _NamedSkill(_CLOID_POSE_SKILL, ("arm_motion",)),
                "set_cloid_packing_pose": _NamedSkill("set_cloid_packing_pose", ("arm_motion",)),
            }
            for name in base_motion:
                self.registered[name] = _NamedSkill(name, ("base_motion",))
            for name in other:
                self.registered[name] = _NamedSkill(name, ("arm_motion",))

        def get_skill(self, name):
            return self.registered.get(name)

        def execute(self, name, params, timeout_sec=30.0):
            self.calls.append((name, params, timeout_sec))
            return self.result

    return _Skills()


def test_cloid_prepends_boxing_pose_for_base_motion():
    skills = _cloid_robot_skill_manager()
    chain = [{"skill": "navigate_to", "params": {"target_name": "메인룸"}}]

    result = _ensure_cloid(chain, skills, _Logger())

    assert [item["skill"] for item in result] == [_CLOID_POSE_SKILL, "navigate_to"]
    assert result[0][_POLICY_PRELUDE_FLAG] is True
    assert result[0]["params"] == {}


def test_cloid_skips_robot_without_prelude_skill():
    chain = [{"skill": "navigate_to", "params": {}}]

    assert _ensure_cloid(chain, _SkillsWithoutStow(), _Logger()) == chain


def test_cloid_does_not_duplicate_pose_before_pose_skill():
    skills = _cloid_robot_skill_manager()
    chain = [
        {"skill": "set_cloid_packing_pose", "params": {"confirm": True}},
        {"skill": "navigate_to", "params": {}},
    ]

    assert _ensure_cloid(chain, skills, _Logger()) == chain


def test_cloid_inserts_pose_immediately_before_base_motion():
    """조작 스킬이 이동보다 앞서면 prelude 는 이동 직전에 들어가야 한다."""
    skills = _cloid_robot_skill_manager(other=("pick_object",))
    chain = [
        {"skill": "pick_object", "params": {}},
        {"skill": "navigate_to", "params": {}},
    ]

    result = _ensure_cloid(chain, skills, _Logger())

    assert [item["skill"] for item in result] == [
        "pick_object",
        _CLOID_POSE_SKILL,
        "navigate_to",
    ]


def test_cloid_inserts_pose_before_every_base_motion():
    skills = _cloid_robot_skill_manager(other=("pick_object",))
    chain = [
        {"skill": "navigate_to", "params": {}},
        {"skill": "pick_object", "params": {}},
        {"skill": "navigate_to", "params": {}},
    ]

    result = _ensure_cloid(chain, skills, _Logger())

    assert [item["skill"] for item in result] == [
        _CLOID_POSE_SKILL,
        "navigate_to",
        "pick_object",
        _CLOID_POSE_SKILL,
        "navigate_to",
    ]


def test_cloid_leaves_non_motion_chain_untouched():
    skills = _cloid_robot_skill_manager()
    chain = [{"skill": "rag_search", "params": {"query": "주방"}}]

    assert _ensure_cloid(chain, skills, _Logger()) == chain


def test_ensure_navigation_safety_skips_robot_without_arm_policy():
    """팔 정책도 prelude 스킬도 없는 로봇(비-stretch/비-cloid)에는 아무것도 넣지 않는다."""
    node = _Node(_MoveItBackend(), _SkillsWithStow())
    chain = [{"skill": "navigate_to", "params": {}}]

    assert _ensure_nav(chain, node._skills, _Logger(), node) == chain


def test_ensure_navigation_safety_applies_cloid_policy_on_cloid_robot():
    skills = _cloid_robot_skill_manager()
    node = _Node(_MoveItBackend(), skills)
    chain = [{"skill": "navigate_to", "params": {}}]

    result = _ensure_nav(chain, skills, _Logger(), node)

    assert [item["skill"] for item in result] == [_CLOID_POSE_SKILL, "navigate_to"]


def test_prepare_cloid_navigation_requests_pose_once_and_latches():
    skills = _cloid_robot_skill_manager()
    node = _Node(_MoveItBackend(), skills)

    assert _prepare_cloid(node) == (True, "")
    assert len(skills.calls) == 1
    assert skills.calls[0][0] == _CLOID_POSE_SKILL
    assert skills.calls[0][1] == {"timeout_sec": _NAV_SAFETY._DEFAULT_CLOID_NAV_POSE_TIMEOUT_SEC}
    assert _ready(node) is True

    # 같은 이동 구간에서 반복 호출해도 자세를 다시 요청하지 않는다.
    assert _prepare_cloid(node) == (True, "")
    assert len(skills.calls) == 1


def test_prepare_cloid_navigation_fails_closed_when_request_fails():
    skills = _cloid_robot_skill_manager(result=_Result(False, "script_runner_unavailable"))
    node = _Node(_MoveItBackend(), skills)

    ready, reason = _prepare_cloid(node)

    assert ready is False
    assert "실패" in reason
    assert _ready(node) is False


def test_prepare_cloid_navigation_is_noop_without_prelude_skill():
    node = _Node(_MoveItBackend(), _SkillsWithoutStow())

    assert _prepare_cloid(node) == (True, "")


def test_prepare_navigation_routes_cloid_robot_to_boxing_pose():
    skills = _cloid_robot_skill_manager()
    node = _Node(_MoveItBackend(), skills)

    assert _prepare_nav(node) == (True, "")
    assert [call[0] for call in skills.calls] == [_CLOID_POSE_SKILL]


def test_prepare_navigation_routes_stretch_robot_to_stow():
    skills = _cloid_robot_skill_manager(result=_Result(True, "pose ok"))
    node = _Node(_StretchDriverBackend(stowed=True), skills)

    assert _prepare_nav(node) == (True, "")
    assert skills.calls == []


def test_arm_motion_skill_success_invalidates_pose_readiness():
    node = _Node(_MoveItBackend(), _SkillsWithoutStow())
    _mark_ready(node)

    _on_skill_succeeded(node, _NamedSkill("execute_cloid_motion", ("arm_motion", "gripper_motion")))

    assert _ready(node) is False


def test_base_motion_skill_success_keeps_pose_readiness():
    """이동만 한 경우에는 상체 자세가 그대로이므로 다시 자세를 요청하지 않는다."""
    node = _Node(_MoveItBackend(), _SkillsWithoutStow())
    _mark_ready(node)

    _on_skill_succeeded(node, _NamedSkill("navigate_to", ("base_motion",)))

    assert _ready(node) is True


def test_pose_skill_success_keeps_pose_readiness():
    """자세 스킬 자신은 준비 상태를 무효화하지 않는다."""
    node = _Node(_MoveItBackend(), _SkillsWithoutStow())
    _mark_ready(node)

    _on_skill_succeeded(node, _NamedSkill(_CLOID_POSE_SKILL, ("arm_motion",)))
    _on_skill_succeeded(node, _NamedSkill("set_cloid_packing_pose", ("arm_motion",)))

    assert _ready(node) is True
