"""ROS2 future 동기 대기 / 서비스 호출 공통 헬퍼 테스트.

기존에 cooperate_skill, cooperation_skill/cooperate_autonomous,
navigation_skill/core, cloid_packing_pose_skill 이 각자 복사해 갖고 있던
대기 루프를 ``robo_claw_agent.ros_future`` 로 통합하면서 계약을 고정한다.
"""

from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from robo_claw_agent import ros_future

pytestmark = pytest.mark.unit


def _future_with_result(result):
    future = Future()
    future.set_result(result)
    return future


def _pending_future():
    return Future()


class TestLocalWaitSec:
    def test_default_uses_generous_ceiling(self):
        assert ros_future.local_wait_sec(0.0) == ros_future.DEFAULT_PEER_CALL_LOCAL_WAIT_SEC

    def test_explicit_timeout_adds_margin(self):
        assert ros_future.local_wait_sec(10.0) == 10.0 + ros_future.SERVICE_CALL_MARGIN_SEC

    def test_custom_constants_override_defaults(self):
        assert ros_future.local_wait_sec(0.0, default_wait_sec=7.0, margin_sec=1.0) == 7.0
        assert ros_future.local_wait_sec(3.0, default_wait_sec=7.0, margin_sec=1.0) == 4.0


class TestWaitForFuture:
    def test_returns_result_when_future_completes(self):
        ok, value = ros_future.wait_for_future(_future_with_result("done"), 1.0)
        assert ok is True
        assert value == "done"

    def test_timeout_returns_failure_with_timeout_value(self):
        ok, value = ros_future.wait_for_future(
            _pending_future(), 0.01, timeout_value="시간 초과"
        )
        assert ok is False
        assert value == "시간 초과"

    def test_timeout_returns_none_by_default(self):
        ok, value = ros_future.wait_for_future(_pending_future(), 0.01)
        assert ok is False
        assert value is None

    def test_result_exception_propagates_by_default(self):
        future = Future()
        future.set_exception(RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            ros_future.wait_for_future(future, 1.0)

    def test_result_exception_is_absorbed_when_requested(self):
        future = Future()
        future.set_exception(RuntimeError("boom"))
        ok, value = ros_future.wait_for_future(
            future, 1.0, timeout_value="실패", return_timeout_value_on_error=True
        )
        assert ok is False
        assert value == "실패"


class TestCallService:
    def _client(self, ready=True, response=None, hang=False):
        client = MagicMock()
        client.wait_for_service.return_value = ready
        if hang:
            client.call_async.return_value = _pending_future()
        else:
            client.call_async.return_value = _future_with_result(response)
        return client

    def test_success_returns_response(self):
        response = SimpleNamespace(success=True)
        ok, value = ros_future.call_service(
            self._client(response=response),
            object(),
            call_timeout_sec=1.0,
            not_found_message="없음",
            timeout_message="시간 초과",
        )
        assert ok is True
        assert value is response

    def test_missing_service_returns_not_found_message(self):
        ok, value = ros_future.call_service(
            self._client(ready=False),
            object(),
            call_timeout_sec=1.0,
            not_found_message="서비스를 찾을 수 없습니다.",
            timeout_message="시간 초과",
        )
        assert ok is False
        assert value == "서비스를 찾을 수 없습니다."

    def test_timeout_returns_timeout_message(self):
        ok, value = ros_future.call_service(
            self._client(hang=True),
            object(),
            call_timeout_sec=0.01,
            not_found_message="없음",
            timeout_message="호출 시간 초과",
        )
        assert ok is False
        assert value == "호출 시간 초과"
