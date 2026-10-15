"""자율협동 전역 상태 API(claim/release) 계약 테스트.

시작/중지 판정과 스레드 등록·반납을 모듈 밖에서 Event/Lock 을 직접 조작하지 않고
``globals`` 의 함수 API 로만 수행하도록 통일했다. 점유의 원자성과 반납 동작을
고정한다.
"""

import threading

import pytest
from robo_claw_agent.skills.cooperation_skill import globals as coop_globals

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_cooperation_state():
    coop_globals.release_cooperation()
    coop_globals.set_cooperation_thread(None)
    yield
    coop_globals.release_cooperation()
    coop_globals.set_cooperation_thread(None)


def test_claim_when_idle_succeeds():
    assert coop_globals.is_cooperation_active() is False

    assert coop_globals.claim_cooperation() is True
    assert coop_globals.is_cooperation_active() is True


def test_second_claim_is_rejected_while_active():
    assert coop_globals.claim_cooperation() is True

    assert coop_globals.claim_cooperation() is False


def test_claim_rejected_while_registered_thread_is_alive():
    stop = threading.Event()
    worker = threading.Thread(target=stop.wait, daemon=True)
    worker.start()
    try:
        coop_globals.set_cooperation_thread(worker)
        assert coop_globals.claim_cooperation() is False
    finally:
        stop.set()
        worker.join(timeout=1.0)


def test_release_clears_active_and_wakes_loop():
    coop_globals.claim_cooperation()
    coop_globals._COOPERATE_WAKE.clear()

    coop_globals.release_cooperation()

    assert coop_globals.is_cooperation_active() is False
    assert coop_globals._COOPERATE_WAKE.is_set() is True


def test_release_without_wake_does_not_set_wake_event():
    coop_globals.claim_cooperation()
    coop_globals._COOPERATE_WAKE.clear()

    coop_globals.release_cooperation(wake=False)

    assert coop_globals.is_cooperation_active() is False
    assert coop_globals._COOPERATE_WAKE.is_set() is False


def test_clear_thread_only_for_current_thread():
    other = threading.Thread(target=lambda: None, daemon=True)
    coop_globals.set_cooperation_thread(other)

    coop_globals.clear_cooperation_thread_if_current()
    assert coop_globals.get_cooperation_thread() is other

    coop_globals.set_cooperation_thread(threading.current_thread())
    coop_globals.clear_cooperation_thread_if_current()
    assert coop_globals.get_cooperation_thread() is None
