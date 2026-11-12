"""지도 목표 비동기 서비스 테스트 — fake backend 로 ROS2 없이 검증."""

import json
import threading
import time

import pytest
from robo_claw_dashboard.application.service import DashboardService
from robo_claw_dashboard.domain.localization import build_map_snapshot
from robo_claw_dashboard.domain.map_goal import (
    canonical_message,
    new_record,
)

pytestmark = pytest.mark.component

WIDTH, HEIGHT, RES = 4, 3, 0.5
CELLS = [0] * (WIDTH * HEIGHT)
CELLS[5] = 100
CELLS[8] = -1


def make_map(*, topic="/map"):
    return build_map_snapshot(
        width=WIDTH,
        height=HEIGHT,
        resolution=RES,
        origin={"x": 1.0, "y": -2.0, "yaw": 1.5707963267948966},
        frame_id="map",
        cells=list(CELLS),
        stamp_ns=1,
        topic=topic,
    )


MAP = make_map()
STATE = json.dumps({"agent_id": "former0047", "navigation_frame": "map"})


def record_payload(
    status="reached", request_id="req-1", target=(0.75, -1.75), updated_at=None, **extra
):
    now = time.time()
    record = new_record(
        request_id=request_id,
        robot_id="former0047",
        target={"x": target[0], "y": target[1], "frame_id": "map"},
        geometry_id=MAP["geometry_id"],
        created_at=now - 5.0,
    )
    record.update(
        status=status, message="이동 결과", updated_at=now if updated_at is None else updated_at
    )
    record.update(extra)
    return record


def nav_result(status="reached", request_id="req-1", target=(0.75, -1.75), success=True):
    return {
        "success": success,
        "message": "이동 완료",
        "skill_results": [
            {
                "skill_name": "navigate_to",
                "code": 0 if success else 1,
                "message": "이동",
                "result_json": json.dumps(
                    {
                        "map_goal_request": record_payload(
                            status, request_id, target, execution_started=True
                        )
                    }
                ),
            }
        ],
    }


def body(request_id="req-1", x=0.75, y=-1.75, overrides=None):
    goal = {
        "x": x,
        "y": y,
        "frame_id": "map",
        "robot_id": "former0047",
        "geometry_id": MAP["geometry_id"],
    }
    goal.update(overrides or {})
    return {
        "message": canonical_message("former0047", "map", x, y),
        "request_id": request_id,
        "map_goal": goal,
    }


def test_owned_long_navigation_uses_fresh_query_sample_not_transition_age():
    gate = threading.Event()
    backend = FakeBackend(gate=gate)
    service = service_for(backend)
    try:
        assert service.submit_map_goal(body())["status_code"] == 202
        now = time.time()
        running = record_payload(
            "running",
            "req-1",
            updated_at=now - 30,
            created_at=now - 45,
            execution_started=True,
        )
        backend.state_json = json.dumps(
            {
                "agent_id": "former0047",
                "navigation_frame": "map",
                "map_navigation_request": running,
            }
        )
        state = service.status()["navigation_request"]
        assert state["status"] == "running"
        assert state["execution_started"] is True
        assert state["can_submit"] is False
    finally:
        gate.set()
        service.shutdown()


class FakeBackend:
    def __init__(self, *, result=None, state_json=STATE, map_snapshot=None, gate=None):
        self.result = result if result is not None else nav_result()
        self.state_json = state_json
        self.map = dict(map_snapshot if map_snapshot is not None else MAP)
        self.gate = gate
        self.calls: list[dict] = []
        self.initial_pose_calls: list[dict] = []

    def query_state(self):
        return self.state_json

    def latest_agent_status(self):
        return {}

    def latest_telemetry(self):
        return {}

    def latest_sensor_health(self):
        return {}

    def list_peers(self):
        return []

    def latest_map(self):
        return dict(self.map)

    def latest_pose(self):
        return {"available": False}

    def describe_config(self):
        return None

    def logs(self, **_kwargs):
        return {"available": True, "entries": []}

    def send_chat(
        self, message, *, context_json=None, timeout_sec=None, on_event=None, cancel_event=None
    ):
        self.calls.append(
            {
                "message": message,
                "context_json": context_json,
                "timeout_sec": timeout_sec,
                "on_event": on_event,
                "cancel_event": cancel_event,
            }
        )
        if self.gate is not None:
            self.gate.wait(timeout=5.0)
        if on_event is not None:
            on_event({"type": "accepted"})
        return dict(self.result)

    def set_initial_pose(self, pose):
        self.initial_pose_calls.append(dict(pose))
        return {"success": True}


def service_for(backend, **kwargs):
    return DashboardService(backend, **kwargs)


def wait_idle(service, timeout=3.0):
    assert service._map_goals.wait_for_idle(timeout), "map goal worker did not become idle"


class TestSubmit:
    def test_accepts_with_202_and_fixed_context_without_waiting(self):
        gate = threading.Event()
        backend = FakeBackend(gate=gate)
        service = service_for(backend)
        result = service.submit_map_goal(body())

        assert result["status_code"] == 202
        assert result["success"] is True
        assert result["request"]["status"] == "queued"
        assert result["request"]["can_submit"] is False
        # 워커는 아직 backend 호출 전이거나 호출 중이다(HTTP 응답은 대기하지 않는다).
        gate.set()
        wait_idle(service)

        call = backend.calls[0]
        expected = body()
        assert call["message"] == expected["message"]
        assert "[skill]" not in call["message"]
        assert call["timeout_sec"] == 180.0
        context = json.loads(call["context_json"])
        assert set(context) == {
            "type",
            "request_id",
            "robot_id",
            "x",
            "y",
            "frame_id",
            "geometry_id",
            "map_topic",
            "created_at",
            "expires_at",
        }
        assert context["type"] == "dashboard_map_goal_v1"
        assert context["request_id"] == "req-1"
        assert context["robot_id"] == "former0047"
        assert context["x"] == 0.75
        assert context["y"] == -1.75
        assert context["frame_id"] == "map"
        assert context["geometry_id"] == MAP["geometry_id"]
        assert context["map_topic"] == "/map"
        assert context["expires_at"] == context["created_at"] + 60

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda p: p.update({"context_json": "{}"}),
            lambda p: p["map_goal"].update({"z": 1.0}),
            lambda p: p["map_goal"].update({"x": True}),
            lambda p: p["map_goal"].update({"geometry_id": "0" * 64}),
            lambda p: p["map_goal"].update({"frame_id": "odom"}),
            lambda p: p["map_goal"].update({"robot_id": "other"}),
            lambda p: p.update({"message": "지도로 이동"}),
        ],
    )
    def test_invalid_input_is_400_and_never_dispatched(self, mutate):
        backend = FakeBackend()
        service = service_for(backend)
        payload = body()
        mutate(payload)
        result = service.submit_map_goal(payload)
        assert result["status_code"] == 400
        assert result["success"] is False
        assert backend.calls == []

    def test_occupied_cell_is_rejected(self):
        service = service_for(FakeBackend())
        result = service.submit_map_goal(body(x=0.25, y=-1.25))
        assert result["status_code"] == 400
        assert "자유" in result["error"] or "셀" in result["error"]

    def test_missing_map_is_503(self):
        backend = FakeBackend(map_snapshot={"available": False})
        result = service_for(backend).submit_map_goal(body())
        assert result["status_code"] == 503
        assert backend.calls == []

    def test_missing_agent_identity_is_503(self):
        backend = FakeBackend(state_json=None)
        result = service_for(backend).submit_map_goal(body())
        assert result["status_code"] == 503
        assert backend.calls == []

    def test_stale_agent_identity_is_not_reused_past_15s(self):
        clock = [0.0]
        backend = FakeBackend()
        service = service_for(backend, monotonic=lambda: clock[0])
        assert service.submit_map_goal(body(request_id="req-1"))["status_code"] in (202, 409)
        service._map_goals.wait_for_idle(3.0)
        backend.state_json = None
        clock[0] = 16.0
        result = service.submit_map_goal(body(request_id="req-2"))
        assert result["status_code"] == 503


class TestIdempotencyAndBusy:
    def test_duplicate_same_payload_returns_existing_record(self):
        backend = FakeBackend()
        service = service_for(backend)
        first = service.submit_map_goal(body())
        wait_idle(service)
        second = service.submit_map_goal(body())
        assert second["status_code"] == 202
        assert second["request"]["request_id"] == first["request"]["request_id"]
        assert len(backend.calls) == 1

    def test_same_id_different_payload_is_409(self):
        backend = FakeBackend()
        service = service_for(backend)
        service.submit_map_goal(body())
        wait_idle(service)
        result = service.submit_map_goal(body(x=0.25, y=-1.75))
        assert result["status_code"] == 409
        assert len(backend.calls) == 1

    def test_active_new_id_is_409(self):
        gate = threading.Event()
        backend = FakeBackend(gate=gate)
        service = service_for(backend)
        service.submit_map_goal(body(request_id="req-1"))
        assert service.submit_map_goal(body(request_id="req-2"))["status_code"] == 409
        gate.set()
        wait_idle(service)

    def test_duplicate_same_payload_while_active_returns_existing(self):
        gate = threading.Event()
        backend = FakeBackend(gate=gate)
        service = service_for(backend)
        first = service.submit_map_goal(body())
        duplicate = service.submit_map_goal(body())
        assert duplicate["status_code"] == 202
        assert duplicate["request"]["request_id"] == first["request"]["request_id"]
        gate.set()
        wait_idle(service)
        assert len(backend.calls) == 1


class TestDispatchGuards:
    def test_changed_geometry_before_dispatch_fails_without_sending(self):
        maps = [MAP, make_map(topic="/other_map")]
        calls = {"n": 0}

        class ChangingBackend(FakeBackend):
            def latest_map(self):
                snapshot = maps[min(calls["n"], 1)]
                calls["n"] += 1
                return dict(snapshot)

        backend = ChangingBackend()
        service = service_for(backend)
        assert service.submit_map_goal(body())["status_code"] == 202
        wait_idle(service)
        record = service.status()["navigation_request"]
        assert record["status"] == "failed"
        assert record["reason"] == "geometry_changed"
        assert backend.calls == []

    def test_expired_selection_is_not_dispatched(self):
        calls = {"n": 0}

        def clock():
            calls["n"] += 1
            return 100.0 if calls["n"] == 1 else 200.0

        backend = FakeBackend()
        service = service_for(backend, wall_clock=clock)
        assert service.submit_map_goal(body())["status_code"] == 202
        wait_idle(service)
        record = service.status()["navigation_request"]
        assert record["status"] == "failed"
        assert record["reason"] == "selection_expired"
        assert backend.calls == []


class TestEvidenceAndStates:
    def test_navigation_evidence_marks_reached(self):
        backend = FakeBackend(result=nav_result("reached"))
        service = service_for(backend)
        service.submit_map_goal(body())
        wait_idle(service)
        record = service.status()["navigation_request"]
        assert record["status"] == "reached"
        assert record["execution_started"] is True
        assert record["can_submit"] is True

    def test_task_success_without_navigation_evidence_is_not_reached(self):
        backend = FakeBackend(result={"success": True, "message": "완료", "skill_results": []})
        service = service_for(backend)
        service.submit_map_goal(body())
        wait_idle(service)
        record = service.status()["navigation_request"]
        assert record["status"] == "unknown"
        assert record["reason"] == "navigation_result_unconfirmed"
        assert record["can_submit"] is False

    def test_evidence_for_other_request_or_target_is_ignored(self):
        backend = FakeBackend(result=nav_result("reached", request_id="other"))
        service = service_for(backend)
        service.submit_map_goal(body())
        wait_idle(service)
        record = service.status()["navigation_request"]
        assert record["status"] != "reached"


class TestUnknownLease:
    def test_unconfirmed_timeout_holds_lease_until_agent_terminal_evidence(self):
        unknown = {
            "success": False,
            "unknown": True,
            "cancel_requested": True,
            "cancel_confirmed": False,
            "message": "결과 미확인",
        }
        backend = FakeBackend(result=unknown)
        service = service_for(backend)
        service.submit_map_goal(body())
        wait_idle(service)
        record = service.status()["navigation_request"]
        assert record["status"] == "unknown"
        assert record["can_submit"] is False

        assert service.submit_map_goal(body(request_id="req-2"))["status_code"] == 409

        backend.state_json = json.dumps(
            {
                "agent_id": "former0047",
                "navigation_frame": "map",
                "map_navigation_request": record_payload("reached", "req-1"),
            }
        )
        recovered = service.status()["navigation_request"]
        assert recovered["status"] == "reached"
        assert recovered["can_submit"] is True
        assert service.submit_map_goal(body(request_id="req-2"))["status_code"] == 202
        wait_idle(service)

    def test_agent_running_record_blocks_new_submit_after_restart(self):
        backend = FakeBackend()
        backend.state_json = json.dumps(
            {
                "agent_id": "former0047",
                "navigation_frame": "map",
                "map_navigation_request": record_payload("running", "req-old"),
            }
        )
        service = service_for(backend)
        result = service.submit_map_goal(body(request_id="req-new"))
        assert result["status_code"] == 409
        record = service.status()["navigation_request"]
        assert record["request_id"] == "req-old"
        assert record["can_submit"] is False

    def test_agent_record_merges_into_queued_request(self):
        gate = threading.Event()
        backend = FakeBackend(gate=gate)
        service = service_for(backend)
        service.submit_map_goal(body())
        backend.state_json = json.dumps(
            {
                "agent_id": "former0047",
                "navigation_frame": "map",
                "map_navigation_request": record_payload("running", "req-1", progress=30),
            }
        )
        record = service.status()["navigation_request"]
        assert record["status"] == "running"
        assert record["progress"] == 30
        gate.set()
        wait_idle(service)

    def test_non_matching_agent_record_does_not_override(self):
        gate = threading.Event()
        backend = FakeBackend(gate=gate)
        service = service_for(backend)
        service.submit_map_goal(body(request_id="req-1"))
        backend.state_json = json.dumps(
            {
                "agent_id": "former0047",
                "navigation_frame": "map",
                "map_navigation_request": record_payload("reached", "other"),
            }
        )
        record = service.status()["navigation_request"]
        assert record["request_id"] == "req-1"
        assert record["status"] != "reached"
        gate.set()
        wait_idle(service)


class TestInitialPoseAndLifecycle:
    def test_initial_pose_is_blocked_while_goal_is_active(self):
        gate = threading.Event()
        backend = FakeBackend(gate=gate)
        service = service_for(backend)
        service.submit_map_goal(body())
        result = service.set_initial_pose({"x": 0.75, "y": -1.75})
        assert result["success"] is False
        assert backend.initial_pose_calls == []
        gate.set()
        wait_idle(service)

        # 종료(도달) 뒤에는 기존 초기 위치 동작이 그대로 유지된다.
        allowed = service.set_initial_pose({"x": 0.75, "y": -1.75})
        assert allowed["success"] is True
        assert len(backend.initial_pose_calls) == 1

    def test_initial_pose_without_map_goal_unchanged(self):
        backend = FakeBackend()
        service = service_for(backend)
        result = service.set_initial_pose({"x": 0.75, "y": -1.75})
        assert result["success"] is True
        assert len(backend.initial_pose_calls) == 1

    def test_retained_records_are_bounded(self):
        backend = FakeBackend(result=nav_result("failed"))
        service = service_for(backend)
        for index in range(25):
            request_id = f"req-{index}"
            backend.result = nav_result("failed", request_id=request_id)
            assert service.submit_map_goal(body(request_id=request_id))["status_code"] == 202
            wait_idle(service)
        assert len(service._map_goals._records) <= 20

    def test_shutdown_stops_accepting_new_requests(self):
        backend = FakeBackend()
        service = service_for(backend)
        service.submit_map_goal(body())
        wait_idle(service)
        service.shutdown()
        result = service.submit_map_goal(body(request_id="req-2"))
        assert result["status_code"] == 503


def agent_state_with(record):
    state = {"agent_id": "former0047", "navigation_frame": "map"}
    if record is not None:
        state["map_navigation_request"] = record
    return json.dumps(state)


class TestBusyResponseCarriesAuthoritativeRequest:
    def test_active_new_id_409_includes_active_record(self):
        gate = threading.Event()
        backend = FakeBackend(gate=gate)
        service = service_for(backend)
        first = service.submit_map_goal(body(request_id="req-1"))
        result = service.submit_map_goal(body(request_id="req-2"))
        assert result["status_code"] == 409
        assert result["request"]["request_id"] == first["request"]["request_id"]
        assert result["request"]["status"] in ("queued", "planning", "running")
        gate.set()
        wait_idle(service)

    def test_same_id_different_payload_409_includes_existing_record(self):
        backend = FakeBackend()
        service = service_for(backend)
        first = service.submit_map_goal(body())
        wait_idle(service)
        result = service.submit_map_goal(body(x=0.25, y=-1.75))
        assert result["status_code"] == 409
        assert result["request"]["request_id"] == first["request"]["request_id"]
        assert result["request"]["target"]["x"] == 0.75

    def test_stale_external_status_is_unknown_without_movement_promise(self):
        backend = FakeBackend()
        backend.state_json = agent_state_with(record_payload("running", "req-ext", updated_at=1.0))
        service = service_for(backend)
        record = service.status()["navigation_request"]
        assert record["status"] == "unknown"
        assert record["reason"] == "stale_agent_record"
        assert record["execution_started"] is False
        assert record["can_submit"] is False

    def test_external_agent_lease_409_includes_agent_record(self):
        backend = FakeBackend()
        external = record_payload("running", "req-ext", progress=20)
        backend.state_json = agent_state_with(external)
        service = service_for(backend)
        result = service.submit_map_goal(body(request_id="req-new"))
        assert result["status_code"] == 409
        assert result["request"]["request_id"] == "req-ext"
        assert result["request"]["can_submit"] is False

    def test_stale_external_nonterminal_lease_is_not_dropped(self):
        backend = FakeBackend()
        stale = record_payload("running", "req-ext", updated_at=1.0)
        backend.state_json = agent_state_with(stale)
        service = service_for(backend)
        result = service.submit_map_goal(body(request_id="req-new"))
        assert result["status_code"] == 409
        assert result["request"]["request_id"] == "req-ext"


class TestExternalLeaseInitialPose:
    def test_initial_pose_blocked_by_agent_running_after_dashboard_restart(self):
        backend = FakeBackend()
        backend.state_json = agent_state_with(record_payload("running", "req-ext"))
        service = service_for(backend)
        # 대시보드 로컬 제출 기록이 전혀 없어도 에이전트 진행 기록이 있으면 차단한다.
        result = service.set_initial_pose({"x": 0.75, "y": -1.75})
        assert result["success"] is False
        assert backend.initial_pose_calls == []

    def test_initial_pose_stays_blocked_when_agent_record_disappears(self):
        clock = [0.0]
        backend = FakeBackend()
        backend.state_json = agent_state_with(record_payload("unknown", "req-ext"))
        service = service_for(backend, monotonic=lambda: clock[0])
        assert service.set_initial_pose({"x": 0.75, "y": -1.75})["success"] is False

        backend.state_json = agent_state_with(None)
        clock[0] = 30.0  # 신원 캐시 만료 후에도 사라진 기록으로 lease 를 풀지 않는다.
        assert service.set_initial_pose({"x": 0.75, "y": -1.75})["success"] is False
        assert backend.initial_pose_calls == []

    def test_initial_pose_released_only_by_matching_terminal_agent_record(self):
        clock = [0.0]
        backend = FakeBackend()
        backend.state_json = agent_state_with(record_payload("running", "req-ext"))
        service = service_for(backend, monotonic=lambda: clock[0])
        assert service.set_initial_pose({"x": 0.75, "y": -1.75})["success"] is False

        # 다른 request 의 terminal 기록으로는 풀리지 않는다.
        clock[0] = 30.0
        backend.state_json = agent_state_with(record_payload("reached", "req-other"))
        assert service.set_initial_pose({"x": 0.75, "y": -1.75})["success"] is False

        clock[0] = 60.0
        backend.state_json = agent_state_with(record_payload("reached", "req-ext"))
        assert service.set_initial_pose({"x": 0.75, "y": -1.75})["success"] is True
        assert len(backend.initial_pose_calls) == 1

    def test_ordinary_initial_pose_without_agent_record_unchanged(self):
        backend = FakeBackend()
        service = service_for(backend)
        assert service.set_initial_pose({"x": 0.75, "y": -1.75})["success"] is True


class TestAcceptedAndFeedbackAuthority:
    def test_accepted_is_planning_without_execution_started(self):
        gate = threading.Event()

        class AcceptedBackend(FakeBackend):
            def send_chat(
                self,
                message,
                *,
                context_json=None,
                timeout_sec=None,
                on_event=None,
                cancel_event=None,
            ):
                self.calls.append({"message": message})
                on_event({"type": "accepted"})
                gate.wait(timeout=5.0)
                return dict(self.result)

        backend = AcceptedBackend()
        service = service_for(backend)
        service.submit_map_goal(body())
        record = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            record = service.status()["navigation_request"]
            if record and record["status"] == "planning":
                break
            time.sleep(0.02)
        gate.set()
        wait_idle(service)
        assert record["status"] == "planning"
        assert record["execution_started"] is False

    def test_plain_feedback_does_not_claim_running_or_execution(self):
        gate = threading.Event()

        class FeedbackBackend(FakeBackend):
            def send_chat(
                self,
                message,
                *,
                context_json=None,
                timeout_sec=None,
                on_event=None,
                cancel_event=None,
            ):
                self.calls.append({"message": message})
                on_event({"type": "accepted"})
                on_event({"type": "feedback", "current_step": "System1 planning", "progress": 40})
                gate.wait(timeout=5.0)
                return dict(self.result)

        backend = FeedbackBackend()
        service = service_for(backend)
        service.submit_map_goal(body())
        deadline = time.monotonic() + 3.0
        record = None
        while time.monotonic() < deadline:
            record = service.status()["navigation_request"]
            if record and record["progress"] == 40:
                break
            time.sleep(0.02)
        gate.set()
        wait_idle(service)
        assert record["status"] == "planning"
        assert record["execution_started"] is False
        assert record["message"] == "System1 planning"

    def test_authoritative_agent_running_marks_running_and_execution_started(self):
        gate = threading.Event()
        backend = FakeBackend(gate=gate)
        service = service_for(backend)
        service.submit_map_goal(body())
        backend.state_json = agent_state_with(
            record_payload("running", "req-1", execution_started=True, progress=10)
        )
        record = service.status()["navigation_request"]
        assert record["status"] == "running"
        assert record["execution_started"] is True
        gate.set()
        wait_idle(service)


class TestConservativeResultEvidence:
    def test_task_success_without_evidence_keeps_unknown_lease(self):
        backend = FakeBackend(result={"success": True, "message": "완료", "skill_results": []})
        service = service_for(backend)
        service.submit_map_goal(body())
        wait_idle(service)
        record = service.status()["navigation_request"]
        assert record["status"] == "unknown"
        assert record["reason"] == "navigation_result_unconfirmed"
        assert record["can_submit"] is False
        assert service.submit_map_goal(body(request_id="req-2"))["status_code"] == 409

    def test_unconfirmed_timeout_ack_is_not_cancel_confirmation(self):
        backend = FakeBackend(
            result={
                "success": False,
                "unknown": True,
                "cancel_requested": True,
                "cancel_confirmed": True,
            }
        )
        service = service_for(backend)
        service.submit_map_goal(body())
        wait_idle(service)
        record = service.status()["navigation_request"]
        assert record["status"] == "unknown"
        assert record["cancel_confirmed"] is False

    def test_matching_cancelled_nav_evidence_confirms_cancel(self):
        backend = FakeBackend(result=nav_result("cancelled"))
        service = service_for(backend)
        service.submit_map_goal(body())
        wait_idle(service)
        record = service.status()["navigation_request"]
        assert record["status"] == "cancelled"
        assert record["cancel_confirmed"] is True
        assert record["execution_started"] is True
        assert record["can_submit"] is True

    def test_verified_navigation_not_executed_failure_releases_lease(self):
        backend = FakeBackend(
            result={
                "success": False,
                "error": "실행되지 않음",
                "reason": "navigation_not_executed",
                "execution_started": False,
            }
        )
        service = service_for(backend)
        service.submit_map_goal(body())
        wait_idle(service)
        record = service.status()["navigation_request"]
        assert record["status"] == "failed"
        assert record["reason"] == "navigation_not_executed"
        assert record["can_submit"] is True

    def test_matching_agent_failed_not_executed_evidence_releases_lease(self):
        backend = FakeBackend(
            result={
                "success": False,
                "message": "실행 없음",
                "skill_results": [
                    {
                        "skill_name": "navigate_to",
                        "result_json": json.dumps(
                            {
                                "map_goal_request": record_payload(
                                    "failed",
                                    "req-1",
                                    reason="navigation_not_executed",
                                    execution_started=False,
                                )
                            }
                        ),
                    }
                ],
            }
        )
        service = service_for(backend)
        service.submit_map_goal(body())
        wait_idle(service)
        record = service.status()["navigation_request"]
        assert record["status"] == "failed"
        assert record["reason"] == "navigation_not_executed"
        assert record["can_submit"] is True
