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


def test_spin_time_allowance_has_safe_minimum_for_small_rotation():
    assert _spin_time_allowance_sec(0.28) >= 20.0


def test_navigate_to_skill_fails_fast_when_nav2_dependency_missing(monkeypatch):
    skill = NavigateToSkill()
    skill.node = object()

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._get_nav2_dependency_error",
        lambda: "nav2 dependency missing",
    )

    result = skill.execute({"x": 1.0, "y": 2.0})

    assert result["success"] is False
    assert result["message"] == "nav2 dependency missing"


def test_navigate_to_skill_returns_send_goal_failure(monkeypatch):
    skill = NavigateToSkill()
    skill.node = object()

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._send_navigation_goal",
        lambda node, x, y, frame, yaw=0.0: (False, "goal send failed", None),
    )

    result = skill.execute({"x": 1.0, "y": 2.0})

    assert result["success"] is False
    assert result["message"] == "goal send failed"


def test_navigate_to_skill_normalizes_blank_frame_id(monkeypatch):
    skill = NavigateToSkill()
    skill.node = object()

    captured = {}

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._get_nav2_dependency_error",
        lambda: None,
    )

    def fake_send_goal(node, x, y, frame, yaw=0.0):
        captured["frame"] = frame
        return False, "goal send failed", None

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._send_navigation_goal",
        fake_send_goal,
    )

    skill.execute({"x": 1.0, "y": 2.0, "frame_id": ""})

    assert captured["frame"] == "map"


# ---------------------------------------------------------------------------
# navigate_to 도착 판정 (Nav2 ABORTED 오탐 방지)
# ---------------------------------------------------------------------------
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


def test_navigate_to_skips_goal_when_already_within_arrival_tolerance(monkeypatch):
    """이미 목표 허용 오차 안이면 Nav2 goal을 보내지 않고 성공 처리한다."""
    skill = _attach_fake_node(NavigateToSkill())
    skill.get_map_pose = lambda: {"x": 1.69, "y": 0.38, "yaw": 0.0, "frame": "map"}

    _patch_navigate_deps(monkeypatch, (True, "success"))
    send_calls = []
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._send_navigation_goal",
        lambda node, x, y, frame, yaw=0.0: send_calls.append(True) or (True, "accepted", object()),
    )

    result = skill.execute({"x": 1.87, "y": 0.33})

    assert result["success"] is True
    assert send_calls == []
    assert "이미" in result["message"]


def test_navigate_to_treats_aborted_as_arrived_when_within_tolerance(monkeypatch):
    """Nav2가 ABORTED를 반환해도 최종 위치가 오차 안이면 도착으로 판정한다."""
    skill = _attach_fake_node(NavigateToSkill())
    poses = iter(
        [
            {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"},
            {"x": 1.69, "y": 0.38, "yaw": 0.0, "frame": "map"},
        ]
    )
    skill.get_map_pose = lambda: next(poses)

    _patch_navigate_deps(monkeypatch, (False, "내비게이션 실패(status=6/ABORTED)"))

    result = skill.execute({"x": 1.87, "y": 0.33})

    assert result["success"] is True


def test_navigate_to_still_fails_when_aborted_far_from_goal(monkeypatch):
    """오차 밖에서 ABORTED된 경우에는 기존처럼 실패를 보고한다."""
    skill = _attach_fake_node(NavigateToSkill())
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    _patch_navigate_deps(monkeypatch, (False, "내비게이션 실패(status=6/ABORTED)"))

    result = skill.execute({"x": 5.0, "y": 5.0})

    assert result["success"] is False
    assert "ABORTED" in result["message"]


def test_navigate_to_does_not_apply_tolerance_across_frames(monkeypatch):
    """pose frame이 목표 frame과 다르면 거리 비교를 적용하지 않는다."""
    skill = _attach_fake_node(NavigateToSkill())
    skill.get_map_pose = lambda: {"x": 1.87, "y": 0.33, "yaw": 0.0, "frame": "odom"}

    _patch_navigate_deps(monkeypatch, (False, "내비게이션 실패(status=6/ABORTED)"))

    result = skill.execute({"x": 1.87, "y": 0.33})

    assert result["success"] is False


def test_navigate_to_computes_yaw_from_map_pose(monkeypatch):
    """진행 방향 yaw는 목표와 같은 map frame의 pose에서 계산한다."""
    import math

    skill = _attach_fake_node(NavigateToSkill())
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    captured = {}
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._get_nav2_dependency_error",
        lambda: None,
    )

    def fake_send(node, x, y, frame, yaw=0.0):
        captured["yaw"] = yaw
        captured["frame"] = frame
        return False, "goal send failed", None

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._send_navigation_goal",
        fake_send,
    )

    skill.execute({"x": 1.0, "y": 1.0})

    assert captured["frame"] == "map"
    assert captured["yaw"] == pytest.approx(math.pi / 4.0)


def test_navigate_to_respects_configured_arrival_tolerance(monkeypatch):
    """ROBOT_LIMITS의 navigation.arrival_tolerance_m이 도착 판정에 반영된다."""
    _patch_navigate_deps(monkeypatch, (True, "success"))

    def _run(limits):
        skill = _attach_fake_node_with_limits(NavigateToSkill(), limits)
        # 목표 (1.87, 0.33)에서 0.30m 떨어진 위치
        skill.get_map_pose = lambda: {"x": 1.57, "y": 0.33, "yaw": 0.0, "frame": "map"}
        send_calls = []
        monkeypatch.setattr(
            "robo_claw_agent.skills.navigation_skill.navigate._send_navigation_goal",
            lambda node, x, y, frame, yaw=0.0: (
                send_calls.append(True) or (True, "accepted", object())
            ),
        )
        result = skill.execute({"x": 1.87, "y": 0.33})
        return result, send_calls

    # 기본값 0.25m: 0.30m는 오차 밖이므로 Nav2 goal을 전송한다.
    _, default_send = _run(None)
    assert default_send == [True]

    # 설정 0.40m: 0.30m는 오차 안이므로 goal을 생략하고 도착 처리한다.
    result, configured_send = _run({"navigation": {"arrival_tolerance_m": 0.4}})
    assert configured_send == []
    assert result["success"] is True


# ---------------------------------------------------------------------------
# RotateSkill
# ---------------------------------------------------------------------------
def test_rotate_skill_succeeds(monkeypatch):
    skill = RotateSkill()
    skill.node = object()
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_cmd_vel",
        lambda *args, **kwargs: (False, "fallback"),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._send_spin_goal",
        lambda node, angle_rad, time_allowance_sec=None: (True, "accepted", object()),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: (True, "success"),
    )

    result = skill.execute({"angle_deg": 90.0})

    assert result["success"] is True
    assert result["angle_deg"] == 90.0


def test_rotate_skill_reports_cancellation(monkeypatch):
    skill = RotateSkill()
    skill.node = object()
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_navigation",
        lambda *args, **kwargs: (False, "취소되었습니다."),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_cmd_vel",
        lambda *args, **kwargs: (False, "fallback"),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._send_spin_goal",
        lambda node, angle_rad, time_allowance_sec=None: (True, "accepted", object()),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: (False, "취소되었습니다."),
    )

    result = skill.execute({"angle_deg": 45.0})

    assert result["success"] is False
    assert result["message"] == "회전 작업 취소됨"


def test_rotate_skill_fails_when_goal_rejected(monkeypatch):
    skill = RotateSkill()
    skill.node = object()
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_navigation",
        lambda *args, **kwargs: (False, "rotate goal rejected"),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_cmd_vel",
        lambda *args, **kwargs: (False, "fallback"),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._send_spin_goal",
        lambda node, angle_rad, time_allowance_sec=None: (False, "rotate goal rejected", None),
    )

    result = skill.execute({"angle_deg": 30.0})

    assert result["success"] is False
    assert result["message"] == "회전 실패: rotate goal rejected"


# ---------------------------------------------------------------------------
# MoveRelativeSkill
# ---------------------------------------------------------------------------
def test_move_relative_skill_succeeds(monkeypatch):
    skill = MoveRelativeSkill()
    skill.node = object()

    monkeypatch.setattr(
        skill,
        "get_map_pose",
        lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"},
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.move_relative._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.move_relative._send_navigation_goal",
        lambda node, x, y, frame: (True, "accepted", object()),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.move_relative._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: (True, "success"),
    )

    result = skill.execute({"forward": 1.0})

    assert result["success"] is True
    assert result["to"]["x"] == 1.0


def test_move_relative_skill_rejects_zero_movement():
    skill = MoveRelativeSkill()
    skill.node = object()

    result = skill.execute({"forward": 0.0, "lateral": 0.0})

    assert result["success"] is False
    assert "0이 아니어야" in result["message"]


def test_move_relative_skill_fails_when_goal_rejected(monkeypatch):
    skill = MoveRelativeSkill()
    skill.node = object()

    monkeypatch.setattr(
        skill,
        "get_map_pose",
        lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"},
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.move_relative._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.move_relative._send_navigation_goal",
        lambda node, x, y, frame: (False, "move goal rejected", None),
    )

    result = skill.execute({"forward": 1.0})

    assert result["success"] is False
    assert "move goal rejected" in result["message"]


def test_face_direction_falls_back_to_navigation_when_spin_aborts(monkeypatch):
    skill = FaceDirectionSkill()
    skill.node = object()

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_cmd_vel",
        lambda *args, **kwargs: (False, "fallback"),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_navigation",
        lambda *args, **kwargs: (True, "success"),
    )
    monkeypatch.setattr(
        skill,
        "get_map_pose",
        lambda: {"x": 1.0, "y": 2.0, "yaw": 1.0, "frame": "map"},
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.face_direction._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.face_direction._send_spin_goal",
        lambda node, angle_rad, time_allowance_sec=None: (True, "accepted", object()),
    )

    wait_results = iter(
        [
            (False, "방향 정렬 회전 실패(status=6/ABORTED)"),
            (True, "success"),
        ]
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.face_direction._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: next(wait_results),
    )

    result = skill.execute({"direction": "east"})

    assert result["success"] is True
    assert result["method"] == "navigate_to_pose"


def test_face_direction_returns_failure_when_spin_and_fallback_fail(monkeypatch):
    skill = FaceDirectionSkill()
    skill.node = object()

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_cmd_vel",
        lambda *args, **kwargs: (False, "fallback"),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_navigation",
        lambda *args, **kwargs: (False, "nav rejected"),
    )
    monkeypatch.setattr(
        skill,
        "get_map_pose",
        lambda: {"x": 1.0, "y": 2.0, "yaw": 1.0, "frame": "map"},
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.face_direction._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.face_direction._send_spin_goal",
        lambda node, angle_rad, time_allowance_sec=None: (True, "accepted", object()),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.face_direction._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: (
            False,
            "방향 정렬 회전 실패(status=6/ABORTED)",
        ),
    )

    result = skill.execute({"direction": "east"})

    assert result["success"] is False
    assert "nav rejected" in result["message"]


# ---------------------------------------------------------------------------
# SelfLocalizeSkill (AMCL 글로벌 로컬라이제이션)
# ---------------------------------------------------------------------------
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


def test_self_localize_fails_fast_when_nav2_dependency_missing(monkeypatch):
    skill = SelfLocalizeSkill()
    skill.node = object()
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.self_localize._get_nav2_dependency_error",
        lambda: "nav2 dependency missing",
    )
    result = skill.execute({})
    assert result["success"] is False
    assert result["message"] == "nav2 dependency missing"


def test_self_localize_fails_when_amcl_service_unavailable(monkeypatch):
    skill = SelfLocalizeSkill()
    skill.node = object()
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.self_localize._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        skill, "_reinitialize_particles", lambda: (False, "AMCL 로컬라이제이션 모드가 아닙니다.")
    )
    result = skill.execute({})
    assert result["success"] is False
    assert "AMCL" in result["message"]


def test_self_localize_succeeds_when_covariance_converges(monkeypatch):
    skill = SelfLocalizeSkill()
    skill.node = _FakeNode(_make_amcl_pose(0.01, 0.01, 0.01, x=1.5, y=-2.0))
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.self_localize._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(skill, "_reinitialize_particles", lambda: (True, "ok"))
    monkeypatch.setattr(skill, "_spin_full_turn", lambda: None)
    monkeypatch.setattr(skill, "_nudge_forward", lambda step: None)

    result = skill.execute({"xy_std_threshold": 0.35, "yaw_std_threshold": 0.35})

    assert result["success"] is True
    assert result["pose"]["x"] == 1.5
    assert result["pose"]["y"] == -2.0


def test_self_localize_reports_nonzero_yaw_via_geo_utils(monkeypatch):
    """geo_utils.yaw_from_quaternion 재사용 회귀 테스트 (yaw != 0 케이스)."""
    skill = SelfLocalizeSkill()
    skill.node = _FakeNode(_make_amcl_pose(0.01, 0.01, 0.01, x=0.0, y=0.0, yaw_deg=90.0))
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.self_localize._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(skill, "_reinitialize_particles", lambda: (True, "ok"))
    monkeypatch.setattr(skill, "_spin_full_turn", lambda: None)
    monkeypatch.setattr(skill, "_nudge_forward", lambda step: None)

    result = skill.execute({"xy_std_threshold": 0.35, "yaw_std_threshold": 0.35})

    assert result["success"] is True
    assert result["pose"]["yaw_deg"] == pytest.approx(90.0)


def test_self_localize_fails_when_never_converges(monkeypatch):
    skill = SelfLocalizeSkill()
    skill.node = _FakeNode(_make_amcl_pose(1.0, 1.0, 1.0))
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.self_localize._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(skill, "_reinitialize_particles", lambda: (True, "ok"))
    monkeypatch.setattr(skill, "_spin_full_turn", lambda: None)
    nudges = []
    monkeypatch.setattr(skill, "_nudge_forward", lambda step: nudges.append(step))

    result = skill.execute({"max_attempts": 3})

    assert result["success"] is False
    # max_attempts=3 이면 마지막 시도 제외 2회 전진 유도
    assert len(nudges) == 2


def test_self_localize_check_converged_handles_none():
    skill = SelfLocalizeSkill()
    converged, xy_std, yaw_std = skill._check_converged(None, 0.35, 0.35)
    assert converged is False
    assert xy_std is None and yaw_std is None


def test_approach_object_handles_none_lidar_distance_with_depth_fallback(monkeypatch):
    from types import SimpleNamespace

    import numpy as np
    from robo_claw_agent.skills.navigation_skill.approach_object import ApproachObjectSkill

    skill = ApproachObjectSkill()
    skill.node = SimpleNamespace(has_parameter=lambda name: False)
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}
    skill.get_opencv_image = lambda *args, **kwargs: (
        np.zeros((100, 100, 3), dtype=np.uint8),
        "/cam",
        False,
    )
    monkeypatch.setattr(skill, "_vlm_locate", lambda target, img: (0.5, 0.5))
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.approach_object._get_nav2_dependency_error",
        lambda: None,
    )

    dummy_scan = SimpleNamespace(
        angle_min=-3.14, angle_max=3.14, range_min=0.1, range_max=10.0, ranges=[float("inf")] * 360
    )
    skill.wait_for_message = lambda msg_type, topic, timeout_sec=2.0: dummy_scan

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.approach_object._compute_depth_point_base",
        lambda *args, **kwargs: ((1.5, 0.0, 0.0), 1.5),
    )
    dummy_goal = SimpleNamespace(
        get_result_async=lambda: SimpleNamespace(
            add_done_callback=lambda cb: None,
            result=lambda: SimpleNamespace(status=4),
        )
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.approach_object._send_navigation_goal",
        lambda *args, **kwargs: (True, "success", dummy_goal),
    )

    result = skill.execute({"target_object": "cup", "stop_distance_m": 0.5})
    assert result["success"] is True
    assert result["measured_distance_m"] == 1.5


def test_approach_object_fails_gracefully_when_all_sensors_none(monkeypatch):
    from types import SimpleNamespace

    import numpy as np
    from robo_claw_agent.skills.navigation_skill.approach_object import ApproachObjectSkill

    skill = ApproachObjectSkill()
    skill.node = SimpleNamespace(has_parameter=lambda name: False)
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}
    skill.get_opencv_image = lambda *args, **kwargs: (
        np.zeros((100, 100, 3), dtype=np.uint8),
        "/cam",
        False,
    )
    monkeypatch.setattr(skill, "_vlm_locate", lambda target, img: (0.5, 0.5))
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.approach_object._get_nav2_dependency_error",
        lambda: None,
    )

    dummy_scan = SimpleNamespace(
        angle_min=-3.14, angle_max=3.14, range_min=0.1, range_max=10.0, ranges=[float("inf")] * 360
    )
    skill.wait_for_message = lambda msg_type, topic, timeout_sec=2.0: dummy_scan

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.approach_object._compute_depth_point_base",
        lambda *args, **kwargs: (None, None),
    )

    result = skill.execute({"target_object": "cup"})
    assert result["success"] is False
    assert "측정하지 못했습니다" in result["message"] or "실패" in result["message"]


# ---------------------------------------------------------------------------
# NavigateToSkill 개선 검증: explicit yaw, early abort 진단, align_heading_first
# ---------------------------------------------------------------------------
def test_navigate_to_supports_explicit_yaw(monkeypatch):
    """명시적 yaw 파라미터 전달 시 computed_yaw 대신 지정된 yaw가 전달된다."""
    skill = _attach_fake_node(NavigateToSkill())
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    captured = {}
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._get_nav2_dependency_error",
        lambda: None,
    )

    def fake_send(node, x, y, frame, yaw=0.0):
        captured["yaw"] = yaw
        captured["frame"] = frame
        return True, "accepted", object()

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._send_navigation_goal",
        fake_send,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: (True, "success"),
    )

    result = skill.execute({"x": 1.0, "y": 1.0, "yaw": 1.57})

    assert result["success"] is True
    assert captured["yaw"] == pytest.approx(1.57)


def test_navigate_to_early_abort_diagnostics_when_robot_barely_moved(monkeypatch):
    """출발 직후 로봇이 거의 이동하지 못한 상태에서 ABORTED 발생 시 진행검사(progress checker) 진단 메시지를 제공한다."""
    skill = _attach_fake_node(NavigateToSkill())
    # 출발 위치와 거의 동일한 위치(0.02m 이동)
    skill.get_map_pose = lambda: {"x": 0.02, "y": 0.0, "yaw": 0.5, "frame": "map"}

    _patch_navigate_deps(monkeypatch, (False, "내비게이션 실패(status=6/ABORTED)"))

    result = skill.execute({"x": 5.0, "y": 5.0})

    assert result["success"] is False
    assert (
        "진행 검사" in result["message"]
        or "progress checker" in result["message"].lower()
        or "회전" in result["message"]
    )
    assert result.get("failure_reason") in (
        "progress_checker_timeout",
        "early_rotation_abort",
    ) or "progress_checker" in str(result)


def test_navigate_to_align_heading_first_performs_spin(monkeypatch):
    """align_heading_first=True일 때 큰 회전각이 필요하면 _send_spin_goal로 사전 정렬 후 이동한다."""
    import math

    skill = _attach_fake_node(NavigateToSkill())
    # 현재 x=0, y=0, yaw=0 (동쪽), 목표는 x=0, y=2 (북쪽: 90도 회전 필요)
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    spin_calls = []
    nav_calls = []

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._send_spin_goal",
        lambda node, angle_rad, time_allowance_sec=None: (
            spin_calls.append(angle_rad) or (True, "accepted", object())
        ),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._send_navigation_goal",
        lambda node, x, y, frame, yaw=0.0: (
            nav_calls.append((x, y, yaw)) or (True, "accepted", object())
        ),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: (True, "success"),
    )

    result = skill.execute({"x": 0.0, "y": 2.0, "align_heading_first": True})

    assert result["success"] is True
    assert len(spin_calls) == 1
    assert spin_calls[0] == pytest.approx(math.pi / 2.0, abs=0.05)
    assert len(nav_calls) == 1
