"""
SkillManager 단위 테스트
"""

import time
from typing import Any

import pytest
from robo_claw_agent.skill_manager import BaseSkill, SkillManager


class DummySkill(BaseSkill):
    name = "dummy"
    description = "테스트용 더미 스킬"

    def execute(self, params):
        return {"success": True, "message": "dummy 실행", "echo": params.get("value")}


class FailingSkill(BaseSkill):
    name = "failing"
    description = "항상 실패하는 스킬"

    def execute(self, params):
        raise RuntimeError("의도적 오류")


class InternalSkill(BaseSkill):
    name = "internal_dummy"
    description = "내부 시퀀스용 더미 스킬"
    is_internal = True

    def execute(self, params):
        return {"success": True}


class BackgroundDummySkill(BaseSkill):
    name = "background_dummy"
    description = "테스트용 백그라운드 스킬"
    terminal_behavior = "background"

    def execute(self, params):
        return self.success_result("백그라운드 시작")


class BackgroundFailingSkill(BaseSkill):
    name = "background_failing"
    description = "시작에 실패하는 백그라운드 스킬"
    terminal_behavior = "background"

    def execute(self, params):
        return self.fail_result("백그라운드 시작 실패")


@pytest.fixture
def manager():
    mgr = SkillManager()
    mgr.register(DummySkill())
    mgr.register(FailingSkill())
    return mgr


def test_register_and_list(manager):
    skills = manager.list_skills()
    names = [s["name"] for s in skills]
    assert "dummy" in names
    assert "failing" in names


def test_result_helpers_include_standard_execution_fields():
    skill = DummySkill()

    success = skill.success_result("완료")
    failure = skill.fail_result("안전 차단", failure_reason="safety_rejected", recoverable=False)

    assert success == {
        "success": True,
        "message": "완료",
        "status": "completed",
        "failure_reason": "",
        "recoverable": False,
    }
    assert failure == {
        "success": False,
        "message": "안전 차단",
        "status": "failed",
        "failure_reason": "safety_rejected",
        "recoverable": False,
    }


def test_internal_skill_is_hidden_only_from_public_list():
    manager = SkillManager()
    manager.register(InternalSkill())

    assert "internal_dummy" in {item["name"] for item in manager.list_skills()}
    assert "internal_dummy" not in {
        item["name"] for item in manager.list_skills(include_internal=False)
    }
    assert manager.get_skill("internal_dummy") is not None


def test_has_skill(manager):
    assert manager.has_skill("dummy")
    assert not manager.has_skill("nonexistent")


def test_execute_success(manager):
    result = manager.execute("dummy", {"value": 42})
    assert result.success is True
    assert result.skill_name == "dummy"
    assert result.result_data.get("echo") == 42
    assert result.result_data["status"] == "completed"
    assert result.result_data["failure_reason"] == ""
    assert result.result_data["recoverable"] is False
    runtime = manager.runtime_summary()
    assert runtime["metrics"]["dummy"]["success"] == 1


def test_execute_missing_skill(manager):
    result = manager.execute("no_such_skill")
    assert result.success is False
    assert "없음" in result.message


def test_execute_exception_handled(manager):
    result = manager.execute("failing")
    assert result.success is False
    assert "오류" in result.message
    runtime = manager.runtime_summary()
    assert runtime["metrics"]["failing"]["failure"] == 1


def test_execute_failure_result_is_recoverability_aware(manager):
    manager.register(InternalSkill())
    manager.set_skill_policy(blocked=["internal_dummy"])

    result = manager.execute("internal_dummy")

    assert result.success is False
    assert result.result_data["status"] == "failed"
    assert result.result_data["failure_reason"] == "safety_rejected"
    assert result.result_data["recoverable"] is False


def test_background_skill_success_reports_started_status():
    """success_result() 기본 status(completed)를 써도 background 는 started 로 확정된다."""
    mgr = SkillManager()
    mgr.register(BackgroundDummySkill())

    result = mgr.execute("background_dummy")

    assert result.success is True
    assert result.result_data["status"] == "started"


def test_background_skill_failure_reports_failed_to_start():
    mgr = SkillManager()
    mgr.register(BackgroundFailingSkill())

    result = mgr.execute("background_failing")

    assert result.success is False
    assert result.result_data["status"] == "failed_to_start"


def test_base_skill_requires_name():
    with pytest.raises(TypeError):

        class BadSkill(BaseSkill):
            name = ""  # 빈 name

            def execute(self, params):
                return {}


# ── timeout 동작 검증 ──


class SlowSkill(BaseSkill):
    name = "slow"
    description = "1.5초 대기 후 성공"

    def execute(self, params):
        time.sleep(1.5)
        return {"success": True, "message": "slow 완료"}


class BlockingSkill(BaseSkill):
    name = "blocking"
    description = "10초 대기 (타임아웃 유발용)"

    def execute(self, params):
        time.sleep(10)
        return {"success": True, "message": "blocking 완료"}


class CancellableBlockingSkill(BlockingSkill):
    name = "cancellable_blocking"

    def __init__(self):
        super().__init__()
        self.cancel_requested = False

    def cancel(self):
        self.cancel_requested = True
        return True


@pytest.fixture
def timeout_manager():
    mgr = SkillManager()
    mgr.register(SlowSkill())
    mgr.register(BlockingSkill())
    mgr.register(CancellableBlockingSkill())
    return mgr


def test_execute_timeout_returns_failure(timeout_manager):
    """timeout_sec 내 완료되지 않으면 실패 결과 반환"""
    result = timeout_manager.execute("blocking", timeout_sec=0.3)
    assert result.success is False
    assert "타임아웃" in result.message
    runtime = timeout_manager.runtime_summary()
    assert runtime["metrics"]["blocking"]["failure"] == 1


def test_execute_timeout_completes_in_time(timeout_manager):
    """timeout_sec 내 완료되면 정상 성공 결과 반환"""
    result = timeout_manager.execute("slow", timeout_sec=5.0)
    assert result.success is True
    assert result.message == "slow 완료"


def test_execute_zero_timeout_runs_directly(timeout_manager):
    """timeout_sec=0이면 무제한 대기 (직접 실행 경로)"""
    result = timeout_manager.execute("slow", timeout_sec=0)
    assert result.success is True
    assert result.message == "slow 완료"


def test_execute_timeout_records_metrics(timeout_manager):
    """타임아웃 발생 시 metrics에 failure로 기록"""
    timeout_manager.execute("blocking", timeout_sec=0.3)
    runtime = timeout_manager.runtime_summary()
    assert "blocking" in runtime["metrics"]
    assert runtime["metrics"]["blocking"]["failure"] == 1


def test_execute_timeout_returns_standard_failure_fields(timeout_manager):
    result = timeout_manager.execute("blocking", timeout_sec=0.01)

    assert result.result_data["status"] == "timeout"
    assert result.result_data["failure_reason"] == "timeout"
    assert result.result_data["recoverable"] is False
    assert result.result_data["cancel_requested"] is True


def test_execute_timeout_preserves_timeout_and_cancel_contract(timeout_manager):
    skill = timeout_manager.get_skill("cancellable_blocking")

    result = timeout_manager.execute("cancellable_blocking", timeout_sec=0.01)

    assert skill.cancel_requested is True
    assert result.status == "timeout"
    assert result.result_data["status"] == "timeout"
    assert result.result_data["failure_reason"] == "timeout"
    assert result.result_data["cancel_requested"] is True
    assert result.result_data["cancel_confirmed"] is True


# ── 스킬 정책 검증 ──


def test_execute_blocked_skill_rejected(manager):
    """blocked 목록에 있는 스킬은 실행 차단"""
    manager.set_skill_policy(blocked=["dummy"])
    result = manager.execute("dummy")
    assert result.success is False
    assert "차단" in result.message


def test_execute_allowed_list_permits(manager):
    """allowed 목록에 있는 스킬은 실행 허용"""
    manager.set_skill_policy(allowed=["dummy"])
    result = manager.execute("dummy", {"value": 1})
    assert result.success is True


def test_execute_allowed_list_rejects(manager):
    """allowed 목록에 없는 스킬은 실행 차단"""
    manager.set_skill_policy(allowed=["dummy"])
    result = manager.execute("failing")
    assert result.success is False
    assert "허용 목록에 없" in result.message


def test_execute_no_policy_allows_all(manager):
    """정책 미설정 시 모든 스킬 허용"""
    # set_skill_policy를 호출하지 않으면 기본 허용
    result = manager.execute("dummy", {"value": 99})
    assert result.success is True
    assert result.result_data.get("echo") == 99


def test_execute_blocked_overrides_allowed(manager):
    """blocked가 allowed보다 우선: allowed에 있어도 blocked면 차단"""
    manager.set_skill_policy(allowed=["dummy"], blocked=["dummy"])
    result = manager.execute("dummy")
    assert result.success is False
    assert "차단" in result.message


# ── set_timeout_hint 디스패치 검증 ──


class TimeoutHintRecordingSkill(BaseSkill):
    name = "timeout_hint_recorder"
    description = "set_timeout_hint 호출값을 기록하는 테스트용 스킬"

    def __init__(self):
        super().__init__()
        self.received_timeout_hint = None

    def set_timeout_hint(self, timeout_sec):
        self.received_timeout_hint = timeout_sec

    def execute(self, params):
        return {"success": True, "message": "ok"}


def test_execute_calls_set_timeout_hint_before_dispatch():
    mgr = SkillManager()
    skill = TimeoutHintRecordingSkill()
    mgr.register(skill)

    mgr.execute("timeout_hint_recorder", timeout_sec=12.5)
    assert skill.received_timeout_hint == 12.5


# ── 로컬 재시도 (max_retries) 및 recover 훅 검증 ──


class RetryableSkill(BaseSkill):
    name = "retryable"
    description = "재시도 테스트용 스킬"
    max_retries = 2
    retry_delay_sec = 0.05

    def __init__(self):
        super().__init__()
        self.attempts = 0

    def execute(self, params):
        self.attempts += 1
        if self.attempts < 3:
            return {"success": False, "message": f"일시적 오류 (시도 {self.attempts})"}
        return {"success": True, "message": "최종 성공"}


class RecoveringSkill(BaseSkill):
    name = "recovering"
    description = "복구 훅 테스트용 스킬"
    max_retries = 1
    retry_delay_sec = 0.01

    def __init__(self):
        super().__init__()
        self.recovered = False
        self.recover_msg = ""

    def execute(self, params):
        return {"success": False, "message": "지속적 에러"}

    def recover(self, params, error_msg):
        self.recovered = True
        self.recover_msg = error_msg


def test_execute_local_retry_succeeds():
    """max_retries 범위 내에서 재시도로 성공할 수 있음"""
    mgr = SkillManager()
    skill = RetryableSkill()
    mgr.register(skill)

    res = mgr.execute("retryable")
    assert res.success is True
    assert res.message == "최종 성공"
    assert skill.attempts == 3


def test_execute_failure_calls_recover():
    """스킬 최종 실패 시 recover 훅이 자동 호출됨"""
    mgr = SkillManager()
    skill = RecoveringSkill()
    mgr.register(skill)

    res = mgr.execute("recovering")
    assert res.success is False
    assert skill.recovered is True
    assert "지속적 에러" in skill.recover_msg


def test_execute_calls_set_timeout_hint_with_zero_when_unlimited():
    """timeout_sec<=0(무제한)이면 set_timeout_hint에는 0.0이 전달되어야 한다"""
    mgr = SkillManager()
    skill = TimeoutHintRecordingSkill()
    mgr.register(skill)

    mgr.execute("timeout_hint_recorder", timeout_sec=0)
    assert skill.received_timeout_hint == 0.0


class NestedCallerSkill(BaseSkill):
    name = "nested_caller"
    description = "다른 스킬을 호출하는 부모 스킬"

    def __init__(self, manager: SkillManager):
        super().__init__()
        self._manager = manager

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        child_res = self._manager.execute(
            params.get("child_name", "dummy"),
            params.get("child_params", {}),
            timeout_sec=params.get("child_timeout", 1.0),
        )
        return {
            "success": child_res.success,
            "child_status": child_res.status,
            "child_message": child_res.message,
            "child_data": child_res.result_data,
        }


def test_nested_skill_execution_does_not_deadlock():
    """부모 스킬 내부에서 SkillManager.execute로 자식 스킬을 호출할 때 단일 스레드 풀 데드락이 발생하지 않아야 한다."""
    mgr = SkillManager()
    mgr.register(DummySkill())
    mgr.register(NestedCallerSkill(mgr))

    result = mgr.execute(
        "nested_caller",
        {"child_name": "dummy", "child_params": {"value": 42}, "child_timeout": 0.5},
        timeout_sec=1.0,
    )
    assert result.success is True
    assert result.result_data["child_status"] == "completed"


def test_queued_skill_cancels_on_timeout_and_does_not_execute_later():
    """풀 포화로 큐에서 대기하다 타임아웃된 작업은 풀이 비워진 뒤에도 지연 실행되지 않아야 한다."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    mgr = SkillManager()
    mgr.shutdown(wait=False, cancel_futures=True)
    mgr._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-exec")

    class TrackerSkill(BaseSkill):
        name = "tracker"
        description = "실행 횟수를 추적하는 스킬"

        def __init__(self):
            super().__init__()
            self.executed = False

        def execute(self, params):
            self.executed = True
            return {"success": True}

    blocking_skill = BlockingSkill()
    tracker_skill = TrackerSkill()
    mgr.register(blocking_skill)
    mgr.register(tracker_skill)

    # 1) blocking_skill을 백그라운드 스레드로 실행하여 1개 워커를 점유
    t = threading.Thread(target=mgr.execute, args=("blocking",), kwargs={"timeout_sec": 0.3})
    t.start()
    time.sleep(0.05)

    # 2) 워커가 점유된 상태에서 tracker_skill 실행 -> 큐에서 대기하다가 0.1s 만에 타임아웃
    timeout_res = mgr.execute("tracker", timeout_sec=0.1)
    assert timeout_res.success is False
    assert timeout_res.result_data["status"] == "timeout"

    # 3) blocking_skill이 끝날 때까지 대기
    t.join()
    time.sleep(0.05)

    # 4) 워커가 비워졌더라도 큐에 있던 tracker_skill은 future.cancel()로 취소되어 실행되지 않아야 함!
    assert tracker_skill.executed is False
    mgr.shutdown(wait=False, cancel_futures=True)
