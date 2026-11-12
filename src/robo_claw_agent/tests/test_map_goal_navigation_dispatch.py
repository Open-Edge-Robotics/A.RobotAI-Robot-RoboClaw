"""지도 목표 주행 직전 검증/결과 증거 테스트(``navigate_to`` 실제 경로).

지도에서 고른 좌표는 실제 Nav2 goal 로 한 번만 전달되고, 주행 직전 같은 지도
토픽을 다시 읽어 지오메트리 ID·확인된 자유 셀을 재검증한다. 결과를 확인하지
못하면 도착으로 확정하지 않고 ``unknown`` 으로 남겨 새 지도 목표를 막는다.
"""

import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip("rclpy")

from robo_claw_agent.agent_node.map_goal.contract import MapGeometry, canonical_goal_message
from robo_claw_agent.agent_node.map_goal.scope import activate_map_goal_scope, map_goal_scope
from robo_claw_agent.agent_node.map_goal.tracker import get_map_goal_tracker
from robo_claw_agent.skills.navigation_skill import NavigateToSkill

pytestmark = pytest.mark.unit

ROBOT_ID = "robot1"
FRAME = "map"
MAP_TOPIC = "/map"
WIDTH, HEIGHT, RESOLUTION = 100, 50, 0.05
ORIGIN_X, ORIGIN_Y = -2.5, -1.25
# (0, 0) 은 위 지오메트리의 자유 셀(50, 25) 안에 있다.
TARGET_X, TARGET_Y = 0.0, 0.0


def _geometry_id() -> str:
    return MapGeometry(
        map_topic=MAP_TOPIC,
        frame_id=FRAME,
        width=WIDTH,
        height=HEIGHT,
        resolution=RESOLUTION,
        origin_x=ORIGIN_X,
        origin_y=ORIGIN_Y,
        origin_yaw=0.0,
    ).geometry_id()


def _map_message(*, cells=None, resolution=RESOLUTION, frame_id=FRAME):
    return SimpleNamespace(
        header=SimpleNamespace(frame_id=frame_id),
        info=SimpleNamespace(
            width=WIDTH,
            height=HEIGHT,
            resolution=resolution,
            origin=SimpleNamespace(
                position=SimpleNamespace(x=ORIGIN_X, y=ORIGIN_Y),
                orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
            ),
        ),
        data=[0] * (WIDTH * HEIGHT) if cells is None else cells,
    )


class _FakeNode:
    def __init__(self, message=None) -> None:
        self._agent_id = ROBOT_ID
        self._map_frame = FRAME
        self._robot_limits_dict = None
        self._manipulation_backend = None
        self._skills = None
        self.message = message if message is not None else _map_message()
        self.map_reads: list[tuple[str, float, float]] = []

    def wait_for_map_message(self, topic, timeout_sec=5.0, max_age_sec=10.0):
        self.map_reads.append((topic, timeout_sec, max_age_sec))
        return self.message

    def validate_message_frame(self, message, expected_frame, label):
        frame_id = str(getattr(getattr(message, "header", None), "frame_id", "") or "")
        if not frame_id:
            return True, ""
        if frame_id != expected_frame:
            return False, f"{label} frame_id 불일치: {frame_id} (필요: {expected_frame})"
        return True, ""

    def get_logger(self):
        import logging

        return logging.getLogger("map_goal_dispatch")


def _context_json(*, now_offset=0.0, x=TARGET_X, y=TARGET_Y, geometry_id=None):
    now = time.time() + now_offset
    return json.dumps(
        {
            "type": "dashboard_map_goal_v1",
            "request_id": "req-0001",
            "robot_id": ROBOT_ID,
            "x": x,
            "y": y,
            "frame_id": FRAME,
            "geometry_id": geometry_id or _geometry_id(),
            "map_topic": MAP_TOPIC,
            "created_at": now,
            "expires_at": now + 60.0,
        }
    )


def _goal_handle(context_json: str):
    return SimpleNamespace(request=SimpleNamespace(context_json=context_json))


class _GuardHarness:
    """``navigate_to`` 실행에 필요한 ROS 경계만 대체한다."""

    def __init__(self, monkeypatch, *, wait_result=(True, "success"), pose=None):
        self.sent: list[tuple] = []
        self.timeouts: list[float] = []
        self.goal_handle = SimpleNamespace(_goal_token=7)
        monkeypatch.setattr(
            "robo_claw_agent.skills.navigation_skill.navigate._get_nav2_dependency_error",
            lambda: None,
        )

        def _send(node, x, y, frame, yaw=0.0):
            self.sent.append((x, y, frame))
            return True, "accepted", self.goal_handle

        def _wait(goal_handle, timeout_sec, label):
            self.timeouts.append(timeout_sec)
            return wait_result

        monkeypatch.setattr(
            "robo_claw_agent.skills.navigation_skill.navigate._send_navigation_goal", _send
        )
        monkeypatch.setattr(
            "robo_claw_agent.skills.navigation_skill.navigate._wait_for_goal_result", _wait
        )
        self.pose = pose if pose is not None else {"x": 5.0, "y": 5.0, "yaw": 0.0, "frame": FRAME}

    def skill(self, node):
        skill = NavigateToSkill()
        skill.node = node
        skill.get_map_pose = lambda: self.pose
        # BaseSkill.wait_for_map_message / validate_message_frame 은 노드의 ROS
        # 구독을 쓰므로, 테스트에서는 노드 더블의 동작을 그대로 연결한다.
        skill.wait_for_map_message = node.wait_for_map_message
        skill.validate_message_frame = node.validate_message_frame
        return skill


def _canonical_instruction(x=TARGET_X, y=TARGET_Y):
    return canonical_goal_message(ROBOT_ID, FRAME, x, y)


def _activate(node, context_json):
    scope, error = activate_map_goal_scope(
        node, _goal_handle(context_json), _canonical_instruction()
    )
    assert error == ""
    assert scope is not None
    return scope


# ── 지도/좌표 재검증 ───────────────────────────────────────────────────────


def test_dispatch_rejects_changed_map_geometry(monkeypatch):
    node = _FakeNode(message=_map_message(resolution=0.1))
    harness = _GuardHarness(monkeypatch)
    scope = _activate(node, _context_json())

    with map_goal_scope(scope):
        result = harness.skill(node).execute({"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME})

    assert result["success"] is False
    assert "지도" in result["message"]
    assert harness.sent == []
    record = get_map_goal_tracker(node).get("req-0001")
    assert record["status"] == "failed"
    assert record["reason"] == "map_goal_geometry_mismatch"


def test_dispatch_rejects_unconfirmed_cell(monkeypatch):
    node = _FakeNode(message=_map_message(cells=[100] * (WIDTH * HEIGHT)))
    harness = _GuardHarness(monkeypatch)
    scope = _activate(node, _context_json())

    with map_goal_scope(scope):
        result = harness.skill(node).execute({"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME})

    assert result["success"] is False
    assert harness.sent == []
    assert get_map_goal_tracker(node).get("req-0001")["reason"] == "map_goal_cell_unconfirmed"


def test_dispatch_fails_closed_when_map_is_unavailable(monkeypatch):
    node = _FakeNode()
    node.message = None
    harness = _GuardHarness(monkeypatch)
    scope = _activate(node, _context_json())

    with map_goal_scope(scope):
        result = harness.skill(node).execute({"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME})

    assert result["success"] is False
    assert harness.sent == []
    assert node.map_reads and node.map_reads[0][1] <= 2.0
    assert get_map_goal_tracker(node).get("req-0001")["reason"] == "map_goal_map_unavailable"


def test_dispatch_rejects_expired_selection(monkeypatch):
    node = _FakeNode()
    harness = _GuardHarness(monkeypatch)
    # 수락은 유효 시간 안이었지만, 첫 실제 주행 전에 선택 창이 지난 상황.
    scope = _activate(node, _context_json())
    scope.context = replace(
        scope.context,
        created_at=time.time() - 120.0,
        expires_at=time.time() - 60.0,
    )

    with map_goal_scope(scope):
        result = harness.skill(node).execute({"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME})

    assert result["success"] is False
    assert "유효 시간" in result["message"]
    assert harness.sent == []
    assert node.map_reads == []
    assert get_map_goal_tracker(node).get("req-0001")["reason"] == "map_goal_selection_expired"


def test_dispatch_refuses_wrong_coordinates_before_goal(monkeypatch):
    node = _FakeNode()
    harness = _GuardHarness(monkeypatch)
    scope = _activate(node, _context_json())

    with map_goal_scope(scope):
        result = harness.skill(node).execute(
            {"x": TARGET_X + 1.0, "y": TARGET_Y, "frame_id": FRAME}
        )

    assert result["success"] is False
    assert harness.sent == []


# ── 실제 주행 결과 증거 ────────────────────────────────────────────────────


def test_reached_goal_records_evidence_with_selected_coordinates(monkeypatch):
    node = _FakeNode()
    harness = _GuardHarness(monkeypatch)
    scope = _activate(node, _context_json())

    with map_goal_scope(scope):
        result = harness.skill(node).execute({"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME})

    assert result["success"] is True
    assert harness.sent == [(TARGET_X, TARGET_Y, FRAME)]
    assert harness.timeouts and harness.timeouts[0] <= 180.0
    proof = result["map_goal_request"]
    assert proof["request_id"] == "req-0001"
    assert proof["status"] == "reached"
    assert proof["goal_token"] == 7
    record = get_map_goal_tracker(node).get("req-0001")
    assert record["status"] == "reached"
    assert record["execution_started"] is True
    assert record["can_submit"] is True


def test_already_at_target_attaches_reached_evidence_without_goal(monkeypatch):
    node = _FakeNode()
    harness = _GuardHarness(monkeypatch, pose={"x": 0.05, "y": 0.05, "yaw": 0.0, "frame": FRAME})
    scope = _activate(node, _context_json())
    skill = harness.skill(node)

    with map_goal_scope(scope):
        result = skill.execute({"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME})
        second = skill.execute({"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME})

    assert harness.sent == []
    assert result["success"] is True
    assert result["map_goal_request"]["status"] == "reached"
    assert get_map_goal_tracker(node).get("req-0001")["status"] == "reached"
    assert second["success"] is False
    assert "한 번만" in second["message"]


def test_unconfirmed_timeout_stays_unknown_and_holds_lease(monkeypatch):
    node = _FakeNode()
    harness = _GuardHarness(
        monkeypatch, wait_result=(False, "내비게이션 결과를 받지 못했습니다 (타임아웃).")
    )
    scope = _activate(node, _context_json())

    with map_goal_scope(scope):
        result = harness.skill(node).execute({"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME})

    assert result["success"] is False
    proof = result["map_goal_request"]
    assert proof["status"] == "unknown"
    assert proof["cancel_confirmed"] is False
    record = get_map_goal_tracker(node).get("req-0001")
    assert record["status"] == "unknown"
    assert record["can_submit"] is False

    # 결과 미확정 상태에서는 새 지도 목표를 받지 않는다.
    scope2, error = activate_map_goal_scope(
        node,
        _goal_handle(_context_json().replace("req-0001", "req-0002")),
        _canonical_instruction(),
    )
    assert scope2 is None
    assert "확정되지 않았습니다" in error


def test_cancelled_result_is_confirmed_only_from_navigation_result(monkeypatch):
    node = _FakeNode()
    harness = _GuardHarness(monkeypatch, wait_result=(False, "취소되었습니다."))
    scope = _activate(node, _context_json())

    with map_goal_scope(scope):
        result = harness.skill(node).execute({"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME})

    assert result["success"] is False
    proof = result["map_goal_request"]
    assert proof["status"] == "cancelled"
    assert proof["cancel_confirmed"] is True
    assert get_map_goal_tracker(node).get("req-0001")["status"] == "cancelled"


def test_ordinary_navigation_is_unchanged_without_map_scope(monkeypatch):
    node = _FakeNode()
    harness = _GuardHarness(monkeypatch)
    skill = harness.skill(node)

    result = skill.execute({"x": 1.0, "y": 2.0})

    assert result["success"] is True
    assert harness.sent == [(1.0, 2.0, "map")]
    assert "map_goal_request" not in result
    assert node.map_reads == []
    assert harness.timeouts == [300.0]
