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
