# 이 파일은 실제 AgentNode 대신 object/SimpleNamespace/_FakeNode 더블을
# BaseSkill.node에 주입해 검증하므로, 그 의도적인 duck-typing 할당에 대한
# reportAttributeAccessIssue 경고를 파일 단위로 끈다.
# pyright: reportAttributeAccessIssue=false
import pytest

pytest.importorskip("rclpy")

"""navigation_skill 단위 테스트"""

from robo_claw_agent.skills.navigation_skill import FaceDirectionSkill, NavigateToSkill
from robo_claw_agent.skills.navigation_skill.core import _spin_time_allowance_sec
from robo_claw_agent.skills.navigation_skill.move_relative import MoveRelativeSkill
from robo_claw_agent.skills.navigation_skill.rotate import RotateSkill


def _patch_navigate_deps(monkeypatch, wait_result):
    """navigate_to 테스트에서 공통으로 쓰는 Nav2 의존성 패치."""
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._send_navigation_goal",
        lambda node, x, y, frame, yaw=0.0: (True, "accepted", object()),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: wait_result,
    )
def _attach_fake_node(skill):
    """테스트용 더미 노드를 부착한다(BaseSkill.node는 AgentNode 타입이지만 테스트에선 불필요)."""
    skill.node = object()
    return skill
def _attach_fake_node_with_limits(skill, limits):
    """ROBOT_LIMITS dict를 가진 더미 노드를 부착한다."""
    from types import SimpleNamespace

    skill.node = SimpleNamespace(_robot_limits_dict=limits)
    return skill
from types import SimpleNamespace

from robo_claw_agent.skills.navigation_skill import SelfLocalizeSkill


def _make_amcl_pose(x_var, y_var, yaw_var, x=1.0, y=2.0, yaw_deg=0.0):
    import math

    cov = [0.0] * 36
    cov[0] = x_var
    cov[7] = y_var
    cov[35] = yaw_var
    yaw_rad = math.radians(yaw_deg)
    return SimpleNamespace(
        pose=SimpleNamespace(
            covariance=cov,
            pose=SimpleNamespace(
                position=SimpleNamespace(x=x, y=y, z=0.0),
                orientation=SimpleNamespace(
                    x=0.0, y=0.0, z=math.sin(yaw_rad / 2.0), w=math.cos(yaw_rad / 2.0)
                ),
            ),
        )
    )
class _FakeNode:
    """create_subscription 시 준비된 amcl_pose를 즉시 콜백으로 전달하는 노드 스텁."""

    def __init__(self, pose):
        self._pose = pose
        self._robot_limits_dict = None

    def create_subscription(self, msg_type, topic, cb, qos):
        if self._pose is not None:
            cb(self._pose)
        return object()

    def destroy_subscription(self, sub):
        pass


__all__ = [
    "FaceDirectionSkill",
    "MoveRelativeSkill",
    "NavigateToSkill",
    "RotateSkill",
    "SelfLocalizeSkill",
    "SimpleNamespace",
    "_FakeNode",
    "_attach_fake_node",
    "_attach_fake_node_with_limits",
    "_make_amcl_pose",
    "_patch_navigate_deps",
    "_spin_time_allowance_sec",
    "pytest",
]
