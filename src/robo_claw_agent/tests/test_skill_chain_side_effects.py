import pytest

pytest.importorskip("robo_claw_msgs.action")

from robo_claw_agent.agent_node.execution import ExecutionMixin
from robo_claw_agent.skill_manager import BaseSkill


class _MotionSkill(BaseSkill):
    name = "motion_test"
    side_effects = ("base_motion",)
    exclusive_resources = ("base_control",)

    def execute(self, params):
        return self.success_result("ok")


class _BackgroundSkill(BaseSkill):
    name = "background_test"
    terminal_behavior = "background"

    def execute(self, params):
        return self.success_result("started")


class _OtherSkill(BaseSkill):
    name = "other_test"
    exclusive_resources = ("base_control",)

    def execute(self, params):
        return self.success_result("ok")


class _SendMessageSkill(BaseSkill):
    name = "send_message"

    def execute(self, params):
        return self.success_result("sent")


class _DelegateTaskSkill(BaseSkill):
    name = "delegate_task"

    def execute(self, params):
        return self.success_result("delegated")


class _CallPeerSkill(BaseSkill):
    name = "call_peer_robot"

    def execute(self, params):
        return self.success_result("called")


class _PolicyPreludeSkill(BaseSkill):
    """nav_safety 주행 안전 게이트가 체인에 삽입하는 prelude 더블."""

    name = "cloid_navigation_pose"
    side_effects = ("arm_motion",)

    def execute(self, params):
        return self.success_result("pose requested")


class _SelfContainedSkill(BaseSkill):
    name = "adaptive_pick_object"
    allow_with_others = False
    side_effects = ("arm_motion", "gripper_motion")

    def execute(self, params):
        return self.success_result("picked")


class _Skills:
    def __init__(self):
        self.skills = {
            s.name: s()
            for s in (
                _MotionSkill,
                _BackgroundSkill,
                _OtherSkill,
                _SendMessageSkill,
                _DelegateTaskSkill,
                _CallPeerSkill,
                _PolicyPreludeSkill,
                _SelfContainedSkill,
            )
        }

    def get_skill(self, name):
        return self.skills.get(name)


class _Node(ExecutionMixin):
    def __init__(self):
        self._skills = _Skills()
        self._agent_id = "test_agent"

    def get_namespace(self):
        return "/robot1"


def test_sequential_reuse_of_side_effect_is_allowed():
    error = _Node()._validate_skill_chain(
        [
            {"skill": "motion_test", "params": {}},
            {"skill": "motion_test", "params": {}},
        ]
    )
    assert error is None


def test_background_skill_cannot_be_chained():
    error = _Node()._validate_skill_chain(
        [
            {"skill": "background_test", "params": {}},
            {"skill": "other_test", "params": {}},
        ]
    )
    assert error is not None
    assert "백그라운드" in error


def test_background_skill_allowed_with_prior_informational_skills():
    error = _Node()._validate_skill_chain(
        [
            {"skill": "send_message", "params": {"message": "자율협동 시작"}},
            {"skill": "background_test", "params": {}},
        ]
    )
    assert error is None


def test_background_skill_rejected_when_not_last():
    error = _Node()._validate_skill_chain(
        [
            {"skill": "background_test", "params": {}},
            {"skill": "send_message", "params": {}},
        ]
    )
    assert error is not None
    assert "백그라운드" in error


def test_background_skill_rejected_with_motion_skill():
    error = _Node()._validate_skill_chain(
        [
            {"skill": "motion_test", "params": {}},
            {"skill": "background_test", "params": {}},
        ]
    )
    assert error is not None
    assert "백그라운드" in error


def test_cannot_delegate_to_self():
    error = _Node()._validate_skill_chain(
        [
            {"skill": "delegate_task", "params": {"agent_id": "test_agent"}},
        ]
    )
    assert error == "같은 에이전트에게 자기 자신을 위임할 수 없습니다."


def test_cannot_call_peer_robot_to_self():
    error = _Node()._validate_skill_chain(
        [
            {"skill": "call_peer_robot", "params": {"peer_name": "robot1"}},
        ]
    )
    assert error == "같은 로봇에게 자기 자신을 위임할 수 없습니다."


def test_policy_prelude_is_exempt_from_self_contained_chaining_rule():
    """게이트가 삽입한 주행 전 자세 prelude 는 자체완결형 스킬과 체이닝할 수 있다."""
    error = _Node()._validate_skill_chain(
        [
            {"skill": "cloid_navigation_pose", "params": {}, "_policy_prelude": True},
            {"skill": "adaptive_pick_object", "params": {}},
        ]
    )

    assert error is None


def test_policy_prelude_does_not_block_background_skill():
    """백그라운드 스킬 앞에 삽입된 prelude 는 백그라운드 체이닝 규칙 위반이 아니다."""
    error = _Node()._validate_skill_chain(
        [
            {"skill": "cloid_navigation_pose", "params": {}, "_policy_prelude": True},
            {"skill": "background_test", "params": {}},
        ]
    )

    assert error is None


def test_user_planned_prelude_is_still_rejected_when_chained():
    """LLM/사용자가 직접 계획한 자세 스킬은 기존 체이닝 규칙을 그대로 따른다."""
    error = _Node()._validate_skill_chain(
        [
            {"skill": "cloid_navigation_pose", "params": {}},
            {"skill": "adaptive_pick_object", "params": {}},
        ]
    )

    assert error is not None
    assert "체이닝" in error
