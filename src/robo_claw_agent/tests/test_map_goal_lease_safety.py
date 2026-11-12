"""지도 목표 결과 확정/lease 안전 회귀 테스트(리뷰 P1-2, P2-2).

원칙:
- ``unknown``/``cancel_requested`` 는 권위 있는 실제 주행 결과(증거)로만 종료 상태가
  된다. 중복 주행 시도, 빈 계획, 늦은 LLM 오류, 작업 종료 정리는 이 상태를 덮지 않는다.
- ``cancel_confirmed`` 는 실제 Nav2 결과로만 True 다(취소 접수/ACK 로는 True 가 아니다).
- ``execution_started`` 는 실제 실행 효과(goal 전송 여부)를 반영한다.
"""

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("rclpy")

from robo_claw_agent.agent_node.map_goal.contract import MapGeometry, MapGoalContext
from robo_claw_agent.agent_node.map_goal.dispatch import apply_task_outcome
from robo_claw_agent.agent_node.map_goal.scope import (
    MapGoalScope,
    activate_map_goal_scope,
    current_map_goal_scope,
    map_goal_scope,
)
from robo_claw_agent.agent_node.map_goal.tracker import (
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_REACHED,
    STATUS_RUNNING,
    STATUS_UNKNOWN,
    MapGoalTracker,
    get_map_goal_tracker,
)
from robo_claw_agent.skills.navigation_skill import NavigateToSkill

pytestmark = pytest.mark.unit

ROBOT_ID = "robot1"
FRAME = "map"
MAP_TOPIC = "/map"
WIDTH, HEIGHT, RESOLUTION = 100, 50, 0.05
ORIGIN_X, ORIGIN_Y = -2.5, -1.25
TARGET_X, TARGET_Y = 0.0, 0.0
# 교차 레인 골든 해시(대시보드 레인과 동일 값). 계약 변경 시 반드시 함께 갱신한다.
GEOMETRY_ID = "7566e0b34bb04228c0dc8850c6657f6dd941e70eb9f94189856ed903fa5bb8e2"
CANONICAL_INSTRUCTION = (
    "지도에서 선택한 목표로 이동해 주세요. 로봇: robot1. 기준 프레임: map. "
    "목표 좌표: x=0.000000 m, y=0.000000 m. 필요한 주행 안전 절차를 먼저 적용하고, "
    "목표 좌표와 프레임을 변경하지 마세요."
)


def _context(**overrides) -> MapGoalContext:
    now = time.time()
    values = {
        "request_id": "req-0001",
        "robot_id": ROBOT_ID,
        "x": TARGET_X,
        "y": TARGET_Y,
        "frame_id": FRAME,
        "geometry_id": GEOMETRY_ID,
        "map_topic": MAP_TOPIC,
        "created_at": now,
        "expires_at": now + 60.0,
    }
    values.update(overrides)
    return MapGoalContext(**values)


def _scope(**overrides) -> MapGoalScope:
    return MapGoalScope(context=_context(**overrides), navigation_frame=FRAME)


def _admitted_scope(node, **overrides) -> MapGoalScope:
    scope, error = activate_map_goal_scope(
        node, _goal_handle(_context_json(**overrides)), CANONICAL_INSTRUCTION
    )
    assert error == ""
    assert scope is not None
    return scope


class _FakeNode:
    _agent_id = ROBOT_ID
    _map_frame = FRAME
    _robot_limits_dict = None
    _manipulation_backend = None
    _skills = None

    def get_logger(self):
        import logging

        return logging.getLogger("map_goal_lease")

    def wait_for_map_message(self, topic, timeout_sec=5.0, max_age_sec=10.0):
        return _map_message()

    def validate_message_frame(self, message, expected_frame, label):
        return True, ""


def _goal_handle(context_json: str):
    return SimpleNamespace(request=SimpleNamespace(context_json=context_json))


def _context_json(**overrides) -> str:
    context = _context(**overrides)
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


def _map_message():
    return SimpleNamespace(
        header=SimpleNamespace(frame_id=FRAME),
        info=SimpleNamespace(
            width=WIDTH,
            height=HEIGHT,
            resolution=RESOLUTION,
            origin=SimpleNamespace(
                position=SimpleNamespace(x=ORIGIN_X, y=ORIGIN_Y),
                orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
            ),
        ),
        data=[0] * (WIDTH * HEIGHT),
    )


class _Harness:
    def __init__(self, monkeypatch, *, wait_result=(True, "success"), pose=None):
        self.sent: list[tuple] = []
        self.goal_handle = SimpleNamespace(_goal_token=11)
        monkeypatch.setattr(
            "robo_claw_agent.skills.navigation_skill.navigate._get_nav2_dependency_error",
            lambda: None,
        )
        monkeypatch.setattr(
            "robo_claw_agent.skills.navigation_skill.navigate._send_navigation_goal",
            lambda node, x, y, frame, yaw=0.0: (
                self.sent.append((x, y, frame)) or (True, "accepted", self.goal_handle)
            ),
        )
        monkeypatch.setattr(
            "robo_claw_agent.skills.navigation_skill.navigate._wait_for_goal_result",
            lambda goal_handle, timeout_sec, label: wait_result,
        )
        self.pose = pose if pose is not None else {"x": 5.0, "y": 5.0, "yaw": 0.0, "frame": FRAME}

    def skill(self, node):
        skill = NavigateToSkill()
        skill.node = node
        skill.get_map_pose = lambda: self.pose
        skill.wait_for_map_message = node.wait_for_map_message
        skill.validate_message_frame = node.validate_message_frame
        return skill


# ── P1-2: unknown / cancel_requested 보호 ─────────────────────────────────


def test_unknown_is_not_demoted_by_duplicate_dispatch(monkeypatch):
    node = _FakeNode()
    harness = _Harness(
        monkeypatch, wait_result=(False, "내비게이션 결과를 받지 못했습니다 (타임아웃).")
    )
    scope = _admitted_scope(node)
    skill = harness.skill(node)

    with map_goal_scope(scope):
        first = skill.execute({"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME})
        second = skill.execute({"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME})

    assert first["map_goal_request"]["status"] == STATUS_UNKNOWN
    assert second["success"] is False
    assert len(harness.sent) == 1, "두 번째 시도는 Nav2 goal 을 보내지 않아야 한다"
    record = get_map_goal_tracker(node).get(scope.request_id)
    assert record["status"] == STATUS_UNKNOWN
    assert record["can_submit"] is False
    assert get_map_goal_tracker(node).get(scope.request_id)["reason"] == "navigation_result_unknown"


def test_unknown_is_not_demoted_by_task_outcome():
    node = _FakeNode()
    scope = _admitted_scope(node)
    tracker = get_map_goal_tracker(node)
    tracker.transition(scope.request_id, STATUS_UNKNOWN, reason="navigation_result_unknown")

    apply_task_outcome(node, scope, [], "이동할 수 없습니다.")

    record = tracker.get(scope.request_id)
    assert record["status"] == STATUS_UNKNOWN
    assert record["reason"] == "navigation_result_unknown"


def test_unknown_is_not_demoted_by_validation_failure_transition():
    tracker = MapGoalTracker()
    context = _context()
    tracker.admit(context)
    tracker.transition(context.request_id, STATUS_UNKNOWN, reason="navigation_result_unknown")

    tracker.transition(context.request_id, STATUS_FAILED, reason="no_navigation_evidence")

    assert tracker.get(context.request_id)["status"] == STATUS_UNKNOWN


def test_cancel_requested_is_protected_from_non_authoritative_failure():
    tracker = MapGoalTracker()
    context = _context()
    tracker.admit(context)
    tracker.transition(context.request_id, STATUS_CANCEL_REQUESTED, reason="cancel_requested")

    tracker.transition(context.request_id, STATUS_FAILED, reason="late_error")
    assert tracker.get(context.request_id)["status"] == STATUS_CANCEL_REQUESTED

    tracker.transition(context.request_id, STATUS_FAILED, reason="late_error", authoritative=True)
    assert tracker.get(context.request_id)["status"] == STATUS_FAILED


def test_authoritative_navigation_evidence_closes_unknown_lease():
    node = _FakeNode()
    context = _context()
    tracker = get_map_goal_tracker(node)
    tracker.admit(context)
    tracker.transition(context.request_id, STATUS_UNKNOWN, reason="navigation_result_unknown")

    applied = tracker.apply_evidence(
        {
            "request_id": context.request_id,
            "frame_id": FRAME,
            "x": TARGET_X,
            "y": TARGET_Y,
            "geometry_id": GEOMETRY_ID,
            "status": STATUS_REACHED,
            "execution_started": True,
            "cancel_confirmed": False,
        }
    )

    assert applied["status"] == STATUS_REACHED
    assert applied["can_submit"] is True


def test_cancel_ack_does_not_confirm_cancel_but_actual_result_does():
    tracker = MapGoalTracker()
    context = _context()
    tracker.admit(context)

    # 취소 접수/ACK 단계: 실제 Nav2 결과가 아니므로 cancel_confirmed 는 False.
    tracker.transition(
        context.request_id,
        STATUS_CANCEL_REQUESTED,
        reason="cancel_requested",
        cancel_confirmed=True,
    )
    assert tracker.get(context.request_id)["cancel_confirmed"] is False

    tracker.apply_evidence(
        {
            "request_id": context.request_id,
            "frame_id": FRAME,
            "x": TARGET_X,
            "y": TARGET_Y,
            "geometry_id": GEOMETRY_ID,
            "status": STATUS_CANCELLED,
            "cancel_confirmed": True,
        }
    )
    record = tracker.get(context.request_id)
    assert record["status"] == STATUS_CANCELLED
    assert record["cancel_confirmed"] is True


def test_unknown_lease_blocks_new_map_request_but_not_ordinary_tasks():
    node = _FakeNode()
    scope = _admitted_scope(node)
    get_map_goal_tracker(node).transition(
        scope.request_id, STATUS_UNKNOWN, reason="navigation_result_unknown"
    )

    blocked_scope, error = activate_map_goal_scope(
        node, _goal_handle(_context_json(request_id="req-0002")), CANONICAL_INSTRUCTION
    )
    assert blocked_scope is None
    assert "확정되지 않았습니다" in error

    # 일반(지도 목표가 아닌) 요청은 lease 와 무관하게 통과한다.
    ordinary_scope, ordinary_error = activate_map_goal_scope(
        node, _goal_handle("센서를 먼저 점검하세요."), "센서를 점검해"
    )
    assert (ordinary_scope, ordinary_error) == (None, "")


# ── P2-2: already-within-tolerance 증거/실행 표시 ─────────────────────────


def test_already_within_tolerance_keeps_goal_not_sent_tag(monkeypatch):
    node = _FakeNode()
    harness = _Harness(monkeypatch, pose={"x": 0.02, "y": 0.02, "yaw": 0.0, "frame": FRAME})
    scope = _admitted_scope(node)

    with map_goal_scope(scope):
        result = harness.skill(node).execute({"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME})

    assert harness.sent == []
    proof = result["map_goal_request"]
    assert proof["status"] == STATUS_REACHED
    assert proof["execution_started"] is False
    assert proof["goal_sent"] is False
    record = get_map_goal_tracker(node).get(scope.request_id)
    assert record["status"] == STATUS_REACHED
    assert record["execution_started"] is False


def test_sent_goal_evidence_reports_real_execution_start(monkeypatch):
    node = _FakeNode()
    harness = _Harness(monkeypatch)
    scope = _admitted_scope(node)

    with map_goal_scope(scope):
        result = harness.skill(node).execute({"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME})

    proof = result["map_goal_request"]
    assert proof["execution_started"] is True
    assert proof["goal_sent"] is True
    assert get_map_goal_tracker(node).get(scope.request_id)["execution_started"] is True


# ── 요구사항 6: 골든 해시/첫 주행 expiry 유지 ─────────────────────────────


def test_geometry_golden_hash_is_unchanged():
    geometry = MapGeometry(
        map_topic=MAP_TOPIC,
        frame_id=FRAME,
        width=WIDTH,
        height=HEIGHT,
        resolution=RESOLUTION,
        origin_x=ORIGIN_X,
        origin_y=ORIGIN_Y,
        origin_yaw=0.0,
    )
    assert geometry.geometry_id() == GEOMETRY_ID


def test_first_dispatch_still_rejects_expired_selection(monkeypatch):
    from dataclasses import replace

    node = _FakeNode()
    harness = _Harness(monkeypatch)
    scope = _admitted_scope(node)
    scope.context = replace(
        scope.context, created_at=time.time() - 120.0, expires_at=time.time() - 60.0
    )

    with map_goal_scope(scope):
        result = harness.skill(node).execute({"x": TARGET_X, "y": TARGET_Y, "frame_id": FRAME})

    assert result["success"] is False
    assert harness.sent == []
    assert (
        get_map_goal_tracker(node).get(scope.request_id)["reason"] == "map_goal_selection_expired"
    )


# ── P1-5: 큐/워커 스레드 스코프 격리와 unknown 보호 ───────────────────────


def test_worker_thread_scope_is_reset_and_unknown_lease_survives():
    node = _FakeNode()
    scope = _admitted_scope(node)
    tracker = get_map_goal_tracker(node)
    seen: dict[str, object] = {}

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:

            async def _run() -> None:
                from robo_claw_agent.agent_node.execution import ExecutionMixin

                class _Exec(ExecutionMixin):
                    _runtime_metrics = SimpleNamespace(record=lambda *a, **k: None)

                    def get_logger(self):
                        return node.get_logger()

                executor = _Exec()
                with map_goal_scope(scope):
                    seen["inside"] = await executor._run_blocking("probe", current_map_goal_scope)
                    tracker.transition(
                        scope.request_id, STATUS_UNKNOWN, reason="navigation_result_unknown"
                    )
                seen["after"] = await executor._run_blocking("probe", current_map_goal_scope)

            loop.run_until_complete(_run())
        finally:
            loop.close()

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join(timeout=10.0)

    assert seen["inside"] is scope
    assert seen["after"] is None
    assert current_map_goal_scope() is None, "메인 스레드로 스코프가 새면 안 된다"
    assert tracker.get(scope.request_id)["status"] == STATUS_UNKNOWN

    blocked, error = activate_map_goal_scope(
        node, _goal_handle(_context_json(request_id="req-0002")), CANONICAL_INSTRUCTION
    )
    assert blocked is None
    assert "확정되지 않았습니다" in error


def test_running_transition_does_not_release_lease():
    tracker = MapGoalTracker()
    context = _context()
    tracker.admit(context)
    tracker.transition(context.request_id, STATUS_RUNNING, execution_started=True)

    assert tracker.get(context.request_id)["can_submit"] is False
