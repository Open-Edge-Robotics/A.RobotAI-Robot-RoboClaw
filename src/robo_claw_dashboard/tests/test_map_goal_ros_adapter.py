"""지도 목표 ExecuteTask 어댑터 테스트 — fake action client 로 액션 경계만 검증.

rclpy/robo_claw_msgs 가 없는 환경에서는 건너뛴다(대시보드의 다른 테스트는 ROS 비의존).
"""

import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("rclpy")
pytest.importorskip("robo_claw_msgs")

from robo_claw_dashboard.adapters import ros_adapter  # noqa: E402

pytestmark = pytest.mark.component


class FakeFuture:
    def __init__(self, value=None, *, done=True):
        self._value = value
        self._done = done

    def done(self) -> bool:
        return self._done

    def add_done_callback(self, callback):
        if self._done:
            callback(self)

    def result(self):
        return self._value


class FakeGoalHandle:
    def __init__(self, result_future, *, accepted=True):
        self.accepted = accepted
        self._result_future = result_future
        self.goal_id = SimpleNamespace(uuid=b"goal-1")
        self.cancelled = False

    def get_result_async(self):
        return self._result_future

    def cancel_goal_async(self):
        self.cancelled = True
        response = SimpleNamespace(goals_canceling=[SimpleNamespace(goal_id=self.goal_id)])
        return FakeFuture(response)


class FakeTaskClient:
    def __init__(self, handle, *, on_send=None):
        self._handle = handle
        self._on_send = on_send
        self.goals: list = []

    def wait_for_server(self, timeout_sec=None) -> bool:
        return True

    def send_goal_async(self, goal, feedback_callback=None):
        self.goals.append(goal)
        if self._on_send is not None:
            self._on_send(feedback_callback)
        return FakeFuture(self._handle)


def make_backend(client, *, timeout=0.3):
    backend = object.__new__(ros_adapter.RosBackend)
    backend._task_client = client
    backend._timeout = timeout
    return backend


def task_result(success=True):
    skill = SimpleNamespace(
        skill_name="navigate_to",
        code=0,
        message="이동",
        duration_sec=1.0,
        result_json='{"map_goal_request": {"request_id": "req-1"}}',
    )
    return SimpleNamespace(
        result=SimpleNamespace(success=success, result_message="완료", skill_results=[skill])
    )


class TestMapGoalActionBoundary:
    def test_forwards_context_timeout_and_feedback_events(self):
        events: list[dict] = []
        result_future = FakeFuture(task_result())
        handle = FakeGoalHandle(result_future)

        def on_send(feedback_callback):
            feedback_callback(
                SimpleNamespace(feedback=SimpleNamespace(current_step="이동 중", progress=42))
            )

        client = FakeTaskClient(handle, on_send=on_send)
        backend = make_backend(client, timeout=5.0)

        result = backend.send_chat(
            "지도에서 선택한 목표로 이동해 주세요.",
            context_json='{"type":"dashboard_map_goal_v1"}',
            timeout_sec=180.0,
            on_event=events.append,
            cancel_event=threading.Event(),
        )

        goal = client.goals[0]
        assert goal.context_json == '{"type":"dashboard_map_goal_v1"}'
        assert goal.timeout_sec == 180.0
        assert {event["type"] for event in events} == {"accepted", "feedback"}
        feedback = next(event for event in events if event["type"] == "feedback")
        assert feedback["progress"] == 42
        assert result["success"] is True
        assert result["skill_results"][0]["skill_name"] == "navigate_to"
        assert result["skill_results"][0]["result_json"].startswith('{"map_goal_request"')

    def test_timeout_cancels_only_own_goal_and_reports_unknown(self):
        pending = FakeFuture(None, done=False)
        handle = FakeGoalHandle(pending)
        backend = make_backend(FakeTaskClient(handle), timeout=5.0)

        result = backend.send_chat(
            "목표",
            context_json="{}",
            timeout_sec=0.15,
            on_event=lambda _event: None,
        )

        assert handle.cancelled is True
        assert result["success"] is False
        assert result["unknown"] is True
        assert result["cancel_requested"] is True
        # goals_canceling 은 요청 ACK 일 뿐 정지 확인이 아니다.
        assert result["cancel_confirmed"] is False

    def test_rejected_goal_is_not_dispatched(self):
        handle = FakeGoalHandle(FakeFuture(None, done=False), accepted=False)
        backend = make_backend(FakeTaskClient(handle), timeout=0.2)
        result = backend.send_chat("목표", context_json="{}", timeout_sec=0.2)
        assert result["success"] is False
        assert "거부" in result["error"]

    def test_legacy_call_keeps_synchronous_contract_without_cancel(self):
        pending = FakeFuture(None, done=False)
        handle = FakeGoalHandle(pending)
        client = FakeTaskClient(handle)
        backend = make_backend(client, timeout=0.1)

        result = backend.send_chat("상태 알려줘")

        assert client.goals[0].context_json == ""
        assert result == {"success": False, "error": "태스크 결과 대기 타임아웃"}
        assert handle.cancelled is False
