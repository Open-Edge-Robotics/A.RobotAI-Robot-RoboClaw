"""CLOiD 주행 전 boxing 자세 prelude 스킬 테스트.

이 스킬은 ``cloid_navigation_pose_enabled=true`` 배포에서만 등록되며, 주행 안전 게이트가
베이스 이동 직전에 내부적으로 호출한다. 실행 계약(confirm 없음, is_internal, 위임)과
실패 시 fail closed 동작을 고정한다. tofu 실행 자체의 계약은
``test_cloid_packing_pose_skill.py`` 가 검증하므로 여기서는 위임 여부만 본다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from robo_claw_agent.skills import cloid_packing_pose_skill as _packing_module

pytestmark = pytest.mark.unit

_MODULE_PATH = (
    Path(__file__).parents[1] / "robo_claw_agent" / "skills" / "cloid_navigation_pose_skill.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "cloid_navigation_pose_skill_under_test", _MODULE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)

CloidNavigationPoseSkill = _MOD.CloidNavigationPoseSkill


class _FakeNode:
    """스킬이 참조하는 최소 노드 더블."""


def _skill_with_node() -> Any:
    skill = CloidNavigationPoseSkill()
    skill.set_node(_FakeNode())
    return skill


@pytest.fixture
def shared_request(monkeypatch):
    """공유 tofu 실행 헬퍼를 가로채 호출 인자와 결과를 기록한다."""

    calls: list[tuple[str, float]] = []
    result: dict[str, Any] = {"success": True, "status": "completed", "message": "ok"}

    def _fake(skill: Any, timeout_sec: float) -> dict[str, Any]:
        calls.append((skill.name, timeout_sec))
        return result

    monkeypatch.setattr(_packing_module, "request_packing_pose", _fake)
    return calls, result


def test_skill_contract_is_internal_and_confirm_free() -> None:
    skill = CloidNavigationPoseSkill()

    assert skill.name == "cloid_navigation_pose"
    assert skill.is_internal is True
    assert skill.risk_level == "dangerous"
    assert skill.side_effects == ("arm_motion",)
    assert skill.exclusive_resources == ("cloid_motion",)
    assert skill.allow_with_others is True
    assert skill.requires_manipulation_backend is None
    # 사용자 승인 게이트가 있는 공개 스킬과 달리 confirm 파라미터를 요구하지 않는다.
    assert "confirm" not in skill.input_schema["properties"]


def test_requires_ros_node(shared_request) -> None:
    result = CloidNavigationPoseSkill().execute({})

    assert result["success"] is False
    assert result["failure_reason"] == "execution_failed"
    assert shared_request[0] == []


def test_delegates_to_shared_tofu_request(shared_request) -> None:
    calls, expected = shared_request
    skill = _skill_with_node()

    result = skill.execute({})

    assert calls == [("cloid_navigation_pose", _MOD._DEFAULT_TIMEOUT_SEC)]
    assert result is expected


def test_uses_requested_timeout(shared_request) -> None:
    calls, _ = shared_request
    skill = _skill_with_node()

    skill.execute({"timeout_sec": 30.0})

    assert calls == [("cloid_navigation_pose", 30.0)]


@pytest.mark.parametrize("value", [0.0, 4.9, 180.1, 1000.0, True, "abc"])
def test_rejects_invalid_timeout(shared_request, value: Any) -> None:
    calls, _ = shared_request
    skill = _skill_with_node()

    result = skill.execute({"timeout_sec": value})

    assert result["success"] is False
    assert result["failure_reason"] == "invalid_timeout"
    assert calls == []


def test_propagates_failure_result_unchanged(monkeypatch, shared_request) -> None:
    failure = _packing_module.SetCloidPackingPoseSkill().fail_result(
        "CLOiD 포장 자세 실행이 실패했습니다: spawn error",
        failure_reason="motion_failed",
    )
    monkeypatch.setattr(_packing_module, "request_packing_pose", lambda _skill, _timeout: failure)
    skill = _skill_with_node()

    assert skill.execute({}) is failure


def test_rejects_concurrent_request_when_lock_is_held(shared_request) -> None:
    calls, _ = shared_request
    skill = _skill_with_node()
    assert skill._publish_lock.acquire(blocking=False)
    try:
        result = skill.execute({})
    finally:
        skill._publish_lock.release()

    assert result["success"] is False
    assert result["failure_reason"] == "motion_busy"
    assert result["recoverable"] is True
    assert calls == []


def test_skips_tofu_request_when_pose_is_already_prepared(shared_request, monkeypatch) -> None:
    """이미 준비된 상태라면 60초 스크립트를 다시 실행하지 않는다."""
    calls, _ = shared_request
    monkeypatch.setattr(_MOD, "_is_navigation_pose_ready", lambda _node: True)
    skill = _skill_with_node()

    result = skill.execute({})

    assert result["success"] is True
    assert result["status"] == "skipped"
    assert calls == []


def test_runs_tofu_request_when_pose_state_is_unknown(shared_request, monkeypatch) -> None:
    """상태를 읽지 못하면 건너뛰지 않고 실행한다(fail closed 방향)."""
    calls, _ = shared_request
    monkeypatch.setattr(_MOD, "_is_navigation_pose_ready", lambda _node: False)
    skill = _skill_with_node()

    result = skill.execute({})

    assert result["success"] is True
    assert calls == [("cloid_navigation_pose", _MOD._DEFAULT_TIMEOUT_SEC)]


def test_nav_pose_skill_name_matches_nav_safety_gate() -> None:
    """스킬 이름은 주행 안전 게이트가 쓰는 상수와 일치해야 한다."""
    from robo_claw_agent.agent_node.nav_safety import CLOID_NAV_POSE_SKILL

    assert CLOID_NAV_POSE_SKILL == _MOD.NAVIGATION_POSE_SKILL_NAME
    assert CloidNavigationPoseSkill.name == CLOID_NAV_POSE_SKILL


def test_lock_is_released_after_execution(shared_request) -> None:
    skill = _skill_with_node()

    skill.execute({})

    assert skill._publish_lock.acquire(blocking=False) is True
    skill._publish_lock.release()


def test_second_call_is_not_blocked_by_the_first(shared_request) -> None:
    calls, _ = shared_request
    skill = _skill_with_node()

    skill.execute({})
    skill.execute({})

    assert [call[1] for call in calls] == [
        _MOD._DEFAULT_TIMEOUT_SEC,
        _MOD._DEFAULT_TIMEOUT_SEC,
    ]


def test_module_registers_single_skill_for_launch_gating() -> None:
    """launch 는 이 모듈을 통째로 등록/비등록하므로 클래스는 하나만 있어야 한다."""
    import inspect

    from robo_claw_agent.skill_manager import BaseSkill

    classes = [
        cls
        for _name, cls in inspect.getmembers(_MOD, inspect.isclass)
        if issubclass(cls, BaseSkill) and cls is not BaseSkill
    ]

    assert [cls.name for cls in classes] == ["cloid_navigation_pose"]
