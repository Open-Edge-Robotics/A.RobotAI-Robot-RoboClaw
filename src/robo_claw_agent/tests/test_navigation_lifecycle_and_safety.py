import time
from types import SimpleNamespace

import pytest
from robo_claw_agent.agent_node.nav_safety import NavigationSafetyGate
from robo_claw_agent.skills.navigation_skill.core import GoalRegistry
from robo_claw_agent.skills.navigation_skill.mark_virtual_obstacle import (
    _VIRTUAL_CLOUDS,
    ClearVirtualObstaclesSkill,
    MarkVirtualObstacleSkill,
)
from robo_claw_agent.skills.navigation_skill.navigate import FollowWaypointsSkill
from robo_claw_agent.skills.navigation_skill.patrol import (
    PatrolSkill,
    StopPatrolSkill,
)
from robo_claw_agent.skills.navigation_skill.set_initial_pose import SetInitialPoseSkill


class DummyGoalHandle:
    def __init__(self, accepted=True, status=4):
        self.accepted = accepted
        self.status = status
        self.cancel_called = False
        self.cancel_response = SimpleNamespace(goals_canceling=[1])

    def cancel_goal_async(self):
        self.cancel_called = True
        return SimpleNamespace(
            add_done_callback=lambda cb: cb(None),
            result=lambda: self.cancel_response,
        )

    def get_result_async(self):
        return SimpleNamespace(
            add_done_callback=lambda cb: None,
            result=lambda: SimpleNamespace(status=self.status),
        )


@pytest.mark.unit
def test_goal_registry_tracks_and_cancels_active_goals():
    registry = GoalRegistry()
    handle1 = DummyGoalHandle()
    handle2 = DummyGoalHandle()

    token1 = registry.register_goal(handle1, "navigate_to_pose")
    assert registry.get_active_token() == token1
    assert registry.is_active(token1) is True

    # Registering a new goal replaces the active token
    token2 = registry.register_goal(handle2, "spin")
    assert registry.get_active_token() == token2
    assert registry.is_active(token2) is True
    assert registry.is_active(token1) is False

    # Old token clear does not affect new token
    registry.clear_goal(token1)
    assert registry.is_active(token2) is True
    assert registry.get_active_token() == token2

    # Cancel active goal cancels handle2
    ok, msg = registry.cancel_active_goal()
    assert ok is True
    assert handle2.cancel_called is True
    assert registry.get_active_token() is None


@pytest.mark.unit
def test_late_goal_result_with_old_token_does_not_clear_newer_goal():
    registry = GoalRegistry()
    handle1 = DummyGoalHandle()
    handle2 = DummyGoalHandle()

    token1 = registry.register_goal(handle1, "navigate_to_pose")
    token2 = registry.register_goal(handle2, "navigate_to_pose")

    # Late result arrival for token1
    registry.clear_goal(token1)
    assert registry.is_active(token2) is True

    # Clearing token2 clears registry
    registry.clear_goal(token2)
    assert registry.is_active(token2) is False


@pytest.mark.unit
def test_follow_waypoints_timeout_cancels_goal(monkeypatch):
    skill = FollowWaypointsSkill()
    node = SimpleNamespace(
        get_clock=lambda: SimpleNamespace(
            now=lambda: SimpleNamespace(to_msg=lambda: SimpleNamespace(sec=0, nanosec=0))
        ),
        send_user_message=lambda msg: None,
    )
    skill.set_node(node)

    handle = DummyGoalHandle()

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._send_follow_waypoints_goal",
        lambda node, poses: (True, "accepted", handle),
    )
    # Simulate timeout in _wait_for_future
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._wait_for_future",
        lambda fut, timeout_sec, label: (False, None),
    )

    cancel_called = []
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._cancel_active_goal",
        lambda *args, **kwargs: cancel_called.append(True),
    )

    result = skill.execute({"waypoints": [{"x": 1.0, "y": 2.0}]})
    assert result["success"] is True

    # Let thread run or call completion wait
    time.sleep(0.05)


@pytest.mark.unit
def test_follow_waypoints_rejects_forbidden_zone(monkeypatch):
    skill = FollowWaypointsSkill()
    node = SimpleNamespace(
        _robot_limits_dict={
            "navigation": {
                "forbidden_zones": [
                    {
                        "name": "RestrictedLab",
                        "x_min": 5.0,
                        "x_max": 10.0,
                        "y_min": 5.0,
                        "y_max": 10.0,
                    }
                ]
            }
        }
    )
    skill.set_node(node)

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._get_nav2_dependency_error",
        lambda: None,
    )

    result = skill.execute({"waypoints": [{"x": 6.0, "y": 6.0}]})
    assert result["success"] is False
    assert "RestrictedLab" in result["message"]


@pytest.mark.unit
def test_patrol_skill_uses_immutable_validated_snapshot(monkeypatch):
    skill = PatrolSkill()
    coords = {
        "kitchen": {"position": {"x": 1.0, "y": 2.0}, "metadata": {"frame_id": "map"}},
        "living_room": {"position": {"x": 3.0, "y": 4.0}, "metadata": {"frame_id": "map"}},
    }
    node = SimpleNamespace(
        _memory=SimpleNamespace(get_object_location=lambda name: coords.get(name)),
    )
    skill.set_node(node)

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.patrol._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.patrol.visit_place",
        lambda node, wp_name: (True, "arrived"),
    )

    # Waypoints with one invalid place
    result = skill.execute({"waypoints": ["kitchen", "unknown_spot", "living_room"]})
    assert result["success"] is True
    # The validated list should only contain valid places
    assert result["waypoints"] == ["kitchen", "living_room"]

    # Stop patrol
    stop_skill = StopPatrolSkill()
    stop_skill.set_node(node)
    stop_res = stop_skill.execute({})
    assert stop_res["success"] is True


@pytest.mark.unit
def test_navigation_safety_gate_emergency_stop_blocks_motion():
    node = SimpleNamespace(_emergency_stopped=True)
    gate = NavigationSafetyGate(node)
    ok, reason = gate.check_safety(target_x=1.0, target_y=1.0)
    assert ok is False
    assert "비상 정지" in reason or "emergency" in reason.lower()


@pytest.mark.unit
def test_navigation_safety_gate_forbidden_zone():
    node = SimpleNamespace(
        _robot_limits_dict={
            "navigation": {
                "forbidden_zones": [
                    {"name": "DangerZone", "x_min": 1.0, "x_max": 2.0, "y_min": 1.0, "y_max": 2.0}
                ]
            }
        }
    )
    gate = NavigationSafetyGate(node)
    ok, reason = gate.check_safety(target_x=1.5, target_y=1.5)
    assert ok is False
    assert "DangerZone" in reason


@pytest.mark.unit
def test_navigation_safety_gate_non_finite_coords():
    node = SimpleNamespace()
    gate = NavigationSafetyGate(node)
    ok, reason = gate.check_safety(target_x=float("nan"), target_y=1.0)
    assert ok is False
    assert "유한" in reason or "finite" in reason.lower() or "올바르지" in reason


@pytest.mark.unit
def test_navigation_safety_gate_stretch_arm_stow_check():
    # Stretch robot where arm is not stowed
    node = SimpleNamespace(
        _robot_limits_dict={"robot_type": "stretch"},
        _manipulation_backend=SimpleNamespace(is_stowed=lambda: False),
    )
    gate = NavigationSafetyGate(node)
    ok, reason = gate.check_safety(target_x=1.0, target_y=1.0)
    assert ok is False
    assert "stow" in reason.lower() or "팔" in reason


@pytest.mark.unit
def test_set_initial_pose_rejects_non_finite_and_forbidden(monkeypatch):
    skill = SetInitialPoseSkill()
    node = SimpleNamespace(
        _robot_limits_dict={
            "navigation": {
                "forbidden_zones": [
                    {"name": "NoPoseZone", "x_min": 0.0, "x_max": 1.0, "y_min": 0.0, "y_max": 1.0}
                ]
            }
        },
        get_clock=lambda: SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: None)),
    )
    skill.set_node(node)

    # 1. Non-finite
    res1 = skill.execute({"x": float("inf"), "y": 0.0, "yaw": 0.0})
    assert res1["success"] is False

    # 2. Forbidden zone
    res2 = skill.execute({"x": 0.5, "y": 0.5, "yaw": 0.0})
    assert res2["success"] is False
    assert "NoPoseZone" in res2["message"]


class _PlaceMemory:
    """시맨틱 맵 최소 대역 — 이름→좌표 조회만 제공한다."""

    def __init__(self, places: dict[str, tuple[float, float]] | None = None):
        self._places = dict(places or {})

    def get_object_location(self, name: str):
        if name not in self._places:
            return None
        x, y = self._places[name]
        return {"name": name, "position": {"x": x, "y": y}}

    def get_all_objects(self):
        return [{"name": name, "metadata": {}} for name in self._places]

    def search_knowledge(self, *args, **kwargs):
        return []


def _initial_pose_node(published: list, memory=None):
    def _create_publisher(_msg_type, _topic, _qos):
        return SimpleNamespace(
            publish=published.append,
            wait_for_subscribers=lambda timeout_sec=0.5: True,
        )

    return SimpleNamespace(
        _robot_limits_dict={"robot_type": "former", "navigation": {"forbidden_zones": []}},
        _memory=memory,
        get_clock=lambda: SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: None)),
        create_publisher=_create_publisher,
        destroy_publisher=lambda pub: None,
    )


@pytest.mark.unit
def test_set_initial_pose_resolves_named_place():
    """'안방을 초기 위치로' 요청은 장소명 → 좌표 해석 후 /initialpose 로 발행한다."""
    published: list = []
    skill = SetInitialPoseSkill()
    skill.set_node(_initial_pose_node(published, _PlaceMemory({"안방": (3.2, -1.4)})))

    res = skill.execute({"target_name": "안방"})

    assert res["success"] is True
    assert res["target_name"] == "안방"
    assert res["x"] == pytest.approx(3.2)
    assert res["y"] == pytest.approx(-1.4)
    assert res["yaw"] == 0.0
    assert res["yaw_defaulted"] is True
    assert len(published) == 1
    pose = published[0].pose.pose
    assert pose.position.x == pytest.approx(3.2)
    assert pose.position.y == pytest.approx(-1.4)
    # yaw 미지정 → 정면(0rad)
    assert pose.orientation.w == pytest.approx(1.0)
    assert pose.orientation.z == pytest.approx(0.0)


@pytest.mark.unit
def test_set_initial_pose_keeps_explicit_yaw_with_named_place():
    published: list = []
    skill = SetInitialPoseSkill()
    skill.set_node(_initial_pose_node(published, _PlaceMemory({"안방": (3.2, -1.4)})))

    res = skill.execute({"target_name": "안방", "yaw": 1.5708})

    assert res["success"] is True
    assert res["yaw_defaulted"] is False
    assert published[0].pose.pose.orientation.z == pytest.approx(0.7071, abs=1e-3)


@pytest.mark.unit
def test_set_initial_pose_rejects_unknown_place_name():
    """기억되지 않은 장소명은 좌표를 추측하지 않고 실패로 보고한다."""
    published: list = []
    skill = SetInitialPoseSkill()
    skill.set_node(_initial_pose_node(published, _PlaceMemory({})))

    res = skill.execute({"target_name": "안방"})

    assert res["success"] is False
    assert "안방" in res["message"]
    assert published == []


@pytest.mark.unit
def test_set_initial_pose_requires_coordinates_or_place():
    published: list = []
    skill = SetInitialPoseSkill()
    skill.set_node(_initial_pose_node(published, _PlaceMemory({"안방": (3.2, -1.4)})))

    res = skill.execute({})

    assert res["success"] is False
    assert "target_name" in res["message"]
    assert published == []


@pytest.mark.unit
def test_set_initial_pose_explicit_coordinates_win_over_place():
    published: list = []
    skill = SetInitialPoseSkill()
    skill.set_node(_initial_pose_node(published, _PlaceMemory({"안방": (3.2, -1.4)})))

    res = skill.execute({"target_name": "안방", "x": 1.0, "y": 2.0})

    assert res["success"] is True
    assert (res["x"], res["y"]) == (pytest.approx(1.0), pytest.approx(2.0))
    assert published[0].pose.pose.position.x == pytest.approx(1.0)


class _LastPoseMemory(_PlaceMemory):
    """마지막 위치(last_pose) 레코드를 가진 메모리 대역."""

    def __init__(self, entries=None, places=None):
        super().__init__(places or {})
        self._vector_store = SimpleNamespace(list_entries=lambda with_vectors=False: list(entries or []))


@pytest.mark.unit
def test_set_initial_pose_restores_remembered_last_pose():
    """'마지막 위치를 초기 위치로' → RAG 마지막 pose(x, y, yaw) 복원."""
    published: list = []
    memory = _LastPoseMemory(
        entries=[
            {
                "id": "lp-1",
                "text": "마지막 위치: x=4.20, y=-3.10, yaw=1.57rad (90.0°)",
                "metadata": {
                    "type": "last_pose",
                    "x": 4.2,
                    "y": -3.1,
                    "yaw": 1.5708,
                    "frame": "map",
                    "near_place": "",
                },
            }
        ]
    )
    skill = SetInitialPoseSkill()
    skill.set_node(_initial_pose_node(published, memory))

    res = skill.execute({"target_name": "마지막 위치"})

    assert res["success"] is True
    assert res["pose_source"] == "last_pose"
    assert res["target_name"] == "마지막 위치"
    # yaw 를 복원했으므로 기본값(0rad)으로 처리하지 않는다.
    assert res["yaw_defaulted"] is False
    assert res["yaw"] == pytest.approx(1.5708)
    assert (res["x"], res["y"]) == (pytest.approx(4.2), pytest.approx(-3.1))

    pose = published[0].pose
    assert pose.pose.position.x == pytest.approx(4.2)
    assert pose.pose.orientation.z == pytest.approx(0.7071, abs=1e-3)
    # 스냅숏 지연을 감안해 공분산을 넓게 둔다(AMCL 이 라이다로 보정).
    assert pose.covariance[0] == pytest.approx(0.25)
    assert pose.covariance[35] == pytest.approx(0.25)


@pytest.mark.unit
def test_set_initial_pose_last_pose_uses_tracker_latest_value():
    """네비 재기동 중에는 트래커가 가진 메모리 최신값이 RAG 기록보다 우선한다."""
    published: list = []
    memory = _LastPoseMemory(
        entries=[
            {
                "id": "lp-old",
                "text": "마지막 위치: x=9.00, y=9.00, yaw=0.00rad (0.0°)",
                "metadata": {"type": "last_pose", "x": 9.0, "y": 9.0, "yaw": 0.0},
            }
        ]
    )
    node = _initial_pose_node(published, memory)
    node._last_pose_tracker = SimpleNamespace(
        latest={"x": 2.5, "y": 1.5, "yaw": -0.5, "frame": "map"}
    )
    skill = SetInitialPoseSkill()
    skill.set_node(node)

    res = skill.execute({"target_name": "직전 위치"})

    assert res["success"] is True
    assert (res["x"], res["y"], res["yaw"]) == (
        pytest.approx(2.5),
        pytest.approx(1.5),
        pytest.approx(-0.5),
    )


@pytest.mark.unit
def test_set_initial_pose_last_pose_explicit_yaw_wins():
    published: list = []
    memory = _LastPoseMemory(
        entries=[
            {
                "id": "lp-1",
                "text": "마지막 위치: x=4.20, y=-3.10, yaw=1.57rad (90.0°)",
                "metadata": {"type": "last_pose", "x": 4.2, "y": -3.1, "yaw": 1.5708},
            }
        ]
    )
    skill = SetInitialPoseSkill()
    skill.set_node(_initial_pose_node(published, memory))

    res = skill.execute({"target_name": "마지막 위치", "yaw": 0.0})

    assert res["success"] is True
    assert res["yaw"] == 0.0
    assert res["yaw_defaulted"] is False


@pytest.mark.unit
def test_set_initial_pose_last_pose_missing_fails_clearly():
    published: list = []
    skill = SetInitialPoseSkill()
    skill.set_node(_initial_pose_node(published, _LastPoseMemory(entries=[])))

    res = skill.execute({"target_name": "마지막 위치"})

    assert res["success"] is False
    assert "마지막 위치" in res["message"]
    assert published == []


@pytest.mark.unit
def test_virtual_obstacle_clamping_topic_allowlist_and_clear(monkeypatch):
    skill = MarkVirtualObstacleSkill()
    node = SimpleNamespace(
        get_clock=lambda: SimpleNamespace(
            now=lambda: SimpleNamespace(to_msg=lambda: SimpleNamespace(sec=0, nanosec=0))
        ),
        create_publisher=lambda *args, **kwargs: SimpleNamespace(publish=lambda msg: None),
    )
    skill.set_node(node)

    # 1. Invalid topic rejection
    res_bad_topic = skill.execute(
        {"target_object": "glass", "x": 1.0, "y": 1.0, "publish_topic": "/cmd_vel"}
    )
    assert res_bad_topic["success"] is False
    assert "토픽" in res_bad_topic["message"] or "topic" in res_bad_topic["message"].lower()

    # 2. Valid obstacle registration with radius clamping
    res_ok = skill.execute(
        {
            "target_object": "glass",
            "x": 1.0,
            "y": 1.0,
            "obstacle_radius_m": 50.0,  # Should clamp to max allowed (e.g. 5.0m)
            "publish_topic": "/virtual_obstacles_cloud",
        }
    )
    assert res_ok["success"] is True
    assert res_ok.get("obstacle_radius_m", 5.0) <= 5.0

    # 3. Clear obstacles
    clear_skill = ClearVirtualObstaclesSkill()
    clear_skill.set_node(node)
    clear_res = clear_skill.execute({})
    assert clear_res["success"] is True
    assert len(_VIRTUAL_CLOUDS) == 0


@pytest.mark.unit
def test_virtual_obstacle_handles_none_lidar_distance():
    import numpy as np

    skill = MarkVirtualObstacleSkill()
    node = SimpleNamespace(
        has_parameter=lambda name: False,
        get_clock=lambda: SimpleNamespace(
            now=lambda: SimpleNamespace(to_msg=lambda: SimpleNamespace(sec=0, nanosec=0))
        ),
        create_publisher=lambda *args, **kwargs: SimpleNamespace(publish=lambda msg: None),
    )
    skill.set_node(node)
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    dummy_scan = SimpleNamespace(
        angle_min=-3.14,
        angle_max=3.14,
        range_min=0.1,
        range_max=10.0,
        ranges=[float("inf")] * 360,
    )
    skill.wait_for_message = lambda msg_type, topic, timeout_sec=1.5: (
        dummy_scan if "scan" in topic else None
    )
    skill.get_opencv_image = lambda *args, **kwargs: (
        np.zeros((100, 100, 3), dtype=np.uint8),
        "/cam",
        False,
    )
    skill._vlm_locate = lambda *args, **kwargs: {
        "cx": 0.5,
        "height_ratio": 0.3,
        "label": "obstacle",
    }

    res = skill.execute({"target_object": "obstacle", "bearing_deg": 0.0})
    assert res["success"] is True
    assert res["distance_source"] == "vlm_estimate"
