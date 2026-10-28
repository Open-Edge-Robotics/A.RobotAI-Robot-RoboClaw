"""navigation skill navigate"""

import pytest

pytest.importorskip("rclpy")

from _navigation_helpers import (  # noqa: F401
    NavigateToSkill,
    _attach_fake_node,
    _attach_fake_node_with_limits,
    _patch_navigate_deps,
    _spin_time_allowance_sec,
    pytest,
)


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


# --- 동료 점유 진단·선제 자리 요청 -------------------------------------------


def _node_with_peers(occupants=(), *, precheck=True, has_yield_skill=True, mode=None):
    """동료 자세 레지스트리와 스킬 매니저를 가진 더미 노드."""
    import time

    from robo_claw_agent.domain.peer_occupancy import PeerPoseSample
    from robo_claw_agent.skills.peer_pose_sharing import PeerPoseRegistry

    registry = PeerPoseRegistry()
    now = time.monotonic()
    for agent_id, x, y in occupants:
        registry.update(PeerPoseSample(agent_id, x, y, 0.0, now))

    calls: list[tuple[str, dict]] = []

    class _Skills:
        def has_skill(self, name):
            return has_yield_skill and name == "request_yield_slot"

        def execute(self, name, params, timeout_sec=30.0):
            calls.append((name, params))
            return type("R", (), {"success": True, "message": "비켜섰습니다."})()

    class _Node:
        _agent_id = "Stretch3"
        _peer_pose_registry = registry
        _skills = _Skills()
        _memory = object()

        # 클래스 본문 스코프는 중첩 람다의 클로저가 아니므로, 함수 인자(mode,
        # precheck)를 직접 참조한다.
        def has_parameter(self, name):
            if name == "yield_precheck_mode":
                return mode is not None
            return name == "yield_precheck_enabled"

        def get_parameter(self, name):
            if name == "yield_precheck_mode":
                return type(
                    "P",
                    (),
                    {
                        "get_parameter_value": lambda self: type(
                            "V", (), {"string_value": mode}
                        )()
                    },
                )()
            return type(
                "P",
                (),
                {"get_parameter_value": lambda self: type("V", (), {"bool_value": precheck})()},
            )()

    return _Node(), calls


def test_navigate_failure_reports_peer_occupancy_as_cause(monkeypatch):
    skill = NavigateToSkill()
    skill.node, calls = _node_with_peers([("Former0047", 5.0, 5.0)], precheck=False)
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    _patch_navigate_deps(monkeypatch, (False, "내비게이션 실패(status=6/ABORTED)"))

    result = skill.execute({"x": 5.0, "y": 5.0})

    assert result["success"] is False
    assert result["blocked_by_peers"] == ["Former0047"]
    assert "Former0047" in result["blocked_by_peers_detail"]
    assert calls == []


def test_large_goal_offset_still_reports_peer_cause(monkeypatch):
    """목표 오프셋이 커서 도착 오차 구제가 적용되지 않아도 원인은 남는다."""
    skill = NavigateToSkill()
    skill.node, _ = _node_with_peers([("Former0047", 5.2, 5.0)], precheck=False)
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    _patch_navigate_deps(monkeypatch, (False, "내비게이션 실패(status=6/ABORTED)"))

    result = skill.execute({"x": 5.0, "y": 5.0})

    assert result["blocked_by_peers"] == ["Former0047"]


def test_precheck_requests_yield_before_sending_the_goal(monkeypatch):
    skill = NavigateToSkill()
    skill.node, calls = _node_with_peers([("Former0047", 5.0, 5.0)], precheck=True)
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    _patch_navigate_deps(monkeypatch, (True, "success"))

    result = skill.execute({"x": 5.0, "y": 5.0})

    assert result["success"] is True
    assert calls == [("request_yield_slot", {"target_x": 5.0, "target_y": 5.0})]


def test_precheck_is_skipped_when_disabled(monkeypatch):
    skill = NavigateToSkill()
    skill.node, calls = _node_with_peers([("Former0047", 5.0, 5.0)], precheck=False)
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    _patch_navigate_deps(monkeypatch, (True, "success"))

    assert skill.execute({"x": 5.0, "y": 5.0})["success"] is True
    assert calls == []


def test_precheck_is_skipped_when_goal_is_free(monkeypatch):
    skill = NavigateToSkill()
    skill.node, calls = _node_with_peers([], precheck=True)
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    _patch_navigate_deps(monkeypatch, (True, "success"))

    assert skill.execute({"x": 5.0, "y": 5.0})["success"] is True
    assert calls == []


def test_precheck_requires_the_skill_to_be_registered(monkeypatch):
    skill = NavigateToSkill()
    skill.node, calls = _node_with_peers(
        [("Former0047", 5.0, 5.0)], precheck=True, has_yield_skill=False
    )
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    _patch_navigate_deps(monkeypatch, (True, "success"))

    assert skill.execute({"x": 5.0, "y": 5.0})["success"] is True
    assert calls == []


def _patch_place_resolution(monkeypatch):
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._resolve_target_coordinates",
        lambda memory, name: {"position": {"x": 2.0, "y": 3.0}},
    )


def test_ask_mode_requests_yield_by_place_name_without_local_pose_data(monkeypatch):
    """맵·도메인을 공유하지 않아도 장소 이름으로 동료에게 직접 물어본다."""
    skill = NavigateToSkill()
    skill.node, calls = _node_with_peers([], precheck=True, mode="ask")
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    _patch_navigate_deps(monkeypatch, (True, "success"))
    _patch_place_resolution(monkeypatch)

    skill.execute({"target_name": "안방", "x": 2.0, "y": 3.0})

    assert calls == [("request_yield_slot", {"target_place": "안방"})]


def test_ask_mode_skips_coordinate_only_targets(monkeypatch):
    """좌표만 있고 장소 이름이 없으면 맵 불일치 위험이 있어 묻지 않는다."""
    skill = NavigateToSkill()
    skill.node, calls = _node_with_peers([], precheck=True, mode="ask")
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    _patch_navigate_deps(monkeypatch, (True, "success"))

    skill.execute({"x": 2.0, "y": 3.0})

    assert calls == []


def test_off_mode_never_requests_yield(monkeypatch):
    skill = NavigateToSkill()
    skill.node, calls = _node_with_peers([("Former0047", 5.0, 5.0)], precheck=True, mode="off")
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    _patch_navigate_deps(monkeypatch, (True, "success"))

    assert skill.execute({"x": 5.0, "y": 5.0})["success"] is True
    assert calls == []


def test_local_mode_uses_coordinates_when_occupancy_is_known(monkeypatch):
    skill = NavigateToSkill()
    skill.node, calls = _node_with_peers([("Former0047", 5.0, 5.0)], mode="local")
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    _patch_navigate_deps(monkeypatch, (True, "success"))

    skill.execute({"x": 5.0, "y": 5.0})

    assert calls == [("request_yield_slot", {"target_x": 5.0, "target_y": 5.0})]


def test_local_mode_prefers_place_name_when_available(monkeypatch):
    # 해석된 장소 좌표(2.0, 3.0)에 동료가 서 있는 상황.
    skill = NavigateToSkill()
    skill.node, calls = _node_with_peers([("Former0047", 2.0, 3.0)], mode="local")
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    _patch_navigate_deps(monkeypatch, (True, "success"))
    _patch_place_resolution(monkeypatch)

    skill.execute({"target_name": "안방", "x": 5.0, "y": 5.0})

    # 맵을 공유하지 않을 수 있으므로 좌표 대신 장소 이름으로 협상한다.
    assert calls == [("request_yield_slot", {"target_place": "안방"})]
