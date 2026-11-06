"""graph_query 헬퍼 단위 테스트"""

import pytest
from robo_claw_discovery.graph_query import call_with_retry

pytestmark = pytest.mark.unit


def test_call_with_retry_returns_first_success():
    """첫 호출이 성공하면 재시도 없이 결과만 돌려준다."""
    calls = []

    def ok():
        calls.append(1)
        return ["/node_a"]

    result, error = call_with_retry(ok, sleep=lambda _sec: None)

    assert result == ["/node_a"]
    assert error is None
    assert len(calls) == 1


def test_call_with_retry_recovers_after_transient_failure():
    """일시적 실패는 재시도로 회복하고 대기 시간을 한 번만 쓴다."""
    responses = [RuntimeError("empty node name returned by the RMW layer"), ["/node_a"]]

    def flaky():
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    slept: list[float] = []
    result, error = call_with_retry(flaky, retry_delay_sec=0.5, sleep=slept.append)

    assert result == ["/node_a"]
    assert error is None
    assert slept == [0.5]


def test_call_with_retry_returns_last_error_when_all_attempts_fail():
    """모든 시도가 실패하면 결과는 None, 예외는 마지막 것을 돌려준다."""
    attempts = []

    def always_fail():
        attempts.append(1)
        raise RuntimeError("Failed to get node names: empty node name")

    result, error = call_with_retry(always_fail, attempts=2, sleep=lambda _sec: None)

    assert result is None
    assert isinstance(error, RuntimeError)
    assert "empty node name" in str(error)
    assert len(attempts) == 2


def test_call_with_retry_treats_empty_result_as_success():
    """빈 목록은 실패가 아니라 정상 결과로 취급한다."""
    result, error = call_with_retry(list, sleep=lambda _sec: None)

    assert result == []
    assert error is None


def test_call_with_retry_clamps_attempts_to_at_least_one():
    """attempts 가 0 이하이면 최소 1회는 호출한다."""
    calls = []

    def ok():
        calls.append(1)
        return ["/node_a"]

    result, error = call_with_retry(ok, attempts=0)

    assert result == ["/node_a"]
    assert error is None
    assert len(calls) == 1
