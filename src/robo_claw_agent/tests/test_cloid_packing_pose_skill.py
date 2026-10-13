"""CLOiD 포장(최소 부피) boxing 자세 스킬 테스트 (ROS-free fakes).

이 스킬은 tofu 의 script_runner action(/script_runner/run_script)에
``cloid.move_boxing`` 실행을 요청한다. 실제 ROS 인터페이스는 지연 import 이므로
여기서는 action client 만 fake 로 바꿔 계약을 고정한다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.unit


class _BaseSkillStub:
    def __init__(self) -> None:
        self.node = None

    def set_node(self, node) -> None:
        self.node = node

    def require_node(self):
        if self.node is None:
            return self.fail_result("missing node", failure_reason="execution_failed")
        return None

    def fail_result(self, message, *, failure_reason="execution_failed", status="failed", **kwargs):
        return {
            "success": False,
            "message": message,
            "status": status,
            "failure_reason": failure_reason,
            **kwargs,
        }

    def success_result(self, message, *, status="completed", **kwargs):
        return {
            "success": True,
            "message": message,
            "status": status,
            **kwargs,
        }


# Import the skill file with a minimal BaseSkill stub so these unit tests don't
# depend on ROS packages or the multi-package colcon Python import path.
_skill_manager_stub = ModuleType("robo_claw_agent.skill_manager")
_skill_manager_stub.BaseSkill = _BaseSkillStub
_previous_skill_manager = sys.modules.get("robo_claw_agent.skill_manager")
sys.modules["robo_claw_agent.skill_manager"] = _skill_manager_stub

_MODULE_PATH = (
    Path(__file__).parents[1] / "robo_claw_agent" / "skills" / "cloid_packing_pose_skill.py"
)
_SPEC = importlib.util.spec_from_file_location("cloid_packing_pose_skill_under_test", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
try:
    _SPEC.loader.exec_module(_MOD)
finally:
    if _previous_skill_manager is None:
        sys.modules.pop("robo_claw_agent.skill_manager", None)
    else:
        sys.modules["robo_claw_agent.skill_manager"] = _previous_skill_manager

SetCloidPackingPoseSkill = _MOD.SetCloidPackingPoseSkill


class _FakeFuture:
    def __init__(self, value: Any = None, *, done: bool = True) -> None:
        self._value = value
        self._done = done

    def add_done_callback(self, callback) -> None:
        if self._done:
            callback(self)

    def result(self) -> Any:
        return self._value


class _FakeResult:
    def __init__(
        self,
        *,
        success: bool = True,
        exit_code: int = 0,
        message: str = "ok",
        output_tail: str = "",
    ) -> None:
        self.success = success
        self.exit_code = exit_code
        self.message = message
        self.output_tail = output_tail


class _FakeWrappedResult:
    def __init__(self, result: _FakeResult) -> None:
        self.result = result


class _FakeGoalHandle:
    def __init__(self, *, accepted: bool = True, result: _FakeResult | None = None) -> None:
        self.accepted = accepted
        self._result = result or _FakeResult()

    def get_result_async(self) -> _FakeFuture:
        return _FakeFuture(_FakeWrappedResult(self._result))


class _FakeGoal:
    def __init__(self) -> None:
        self.package = ""
        self.executable = ""
        self.args: list[str] = []
        self.timeout = 0.0
        self.stream_output = False


class _FakeRunScript:
    Goal = _FakeGoal


class _FakeActionClient:
    def __init__(self, node, message_type, action_name: str) -> None:
        self.node = node
        self.message_type = message_type
        self.action_name = action_name
        self.server_available = True
        self.goal_handle = _FakeGoalHandle()
        self.goals: list[Any] = []
        self.destroyed = False

    def wait_for_server(self, timeout_sec: float) -> bool:
        return self.server_available

    def send_goal_async(self, goal: Any) -> _FakeFuture:
        self.goals.append(goal)
        return _FakeFuture(self.goal_handle)

    def destroy(self) -> None:
        self.destroyed = True


class _FakeNode:
    pass


def _patch_client(monkeypatch, client: _FakeActionClient) -> None:
    monkeypatch.setattr(_MOD, "_create_run_script_client", lambda _node: (client, _FakeRunScript))


def _skill_with_node() -> Any:
    skill = SetCloidPackingPoseSkill()
    skill.set_node(_FakeNode())
    return skill


def test_command_contract_matches_tofu_script_runner() -> None:
    assert _MOD.SCRIPT_RUNNER_ACTION == "/script_runner/run_script"
    assert _MOD.TOFUPY_PACKAGE == "tofupy"
    assert _MOD.PACKING_POSE_EXECUTABLE == "cloid.move_boxing"


def test_requires_ros_node() -> None:
    result = SetCloidPackingPoseSkill().execute({"confirm": True})
    assert result["success"] is False
    assert result["failure_reason"] == "execution_failed"


def test_requires_explicit_confirmation(monkeypatch) -> None:
    client = _FakeActionClient(None, None, "")
    _patch_client(monkeypatch, client)
    skill = _skill_with_node()

    assert skill.execute({})["failure_reason"] == "safety_rejected"
    assert skill.execute({"confirm": False})["failure_reason"] == "safety_rejected"
    assert client.goals == []


@pytest.mark.parametrize("value", [0.0, 4.9, 180.1, 1000.0, True, "abc"])
def test_rejects_invalid_timeout(monkeypatch, value: Any) -> None:
    client = _FakeActionClient(None, None, "")
    _patch_client(monkeypatch, client)
    skill = _skill_with_node()

    result = skill.execute({"confirm": True, "timeout_sec": value})
    assert result["success"] is False
    assert result["failure_reason"] == "invalid_timeout"
    assert client.goals == []


def test_requests_packing_pose_through_script_runner(monkeypatch) -> None:
    client = _FakeActionClient(None, None, "")
    _patch_client(monkeypatch, client)
    skill = _skill_with_node()

    result = skill.execute({"confirm": True})

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["pose"] == "packing"
    assert result["executable"] == "cloid.move_boxing"
    assert result["completion_confirmed"] is False
    assert len(client.goals) == 1
    goal = client.goals[0]
    assert goal.package == "tofupy"
    assert goal.executable == "cloid.move_boxing"
    assert goal.args == []
    assert goal.timeout == _MOD._DEFAULT_TIMEOUT_SEC
    assert client.destroyed is True


def test_uses_requested_timeout(monkeypatch) -> None:
    client = _FakeActionClient(None, None, "")
    _patch_client(monkeypatch, client)
    skill = _skill_with_node()

    result = skill.execute({"confirm": True, "timeout_sec": 30.0})

    assert result["success"] is True
    assert client.goals[0].timeout == 30.0


def test_interface_unavailable_fails_closed(monkeypatch) -> None:
    def _raise(_node):
        raise ImportError("no tofu_msgs")

    monkeypatch.setattr(_MOD, "_create_run_script_client", _raise)
    skill = _skill_with_node()

    result = skill.execute({"confirm": True})
    assert result["success"] is False
    assert result["failure_reason"] == "motion_interface_unavailable"


def test_script_runner_unavailable_fails_closed(monkeypatch) -> None:
    client = _FakeActionClient(None, None, "")
    client.server_available = False
    _patch_client(monkeypatch, client)
    skill = _skill_with_node()

    result = skill.execute({"confirm": True})
    assert result["success"] is False
    assert result["failure_reason"] == "script_runner_unavailable"
    assert client.destroyed is True


def test_goal_rejected(monkeypatch) -> None:
    client = _FakeActionClient(None, None, "")
    client.goal_handle = _FakeGoalHandle(accepted=False)
    _patch_client(monkeypatch, client)
    skill = _skill_with_node()

    result = skill.execute({"confirm": True})
    assert result["success"] is False
    assert result["failure_reason"] == "goal_rejected"


def test_send_goal_timeout(monkeypatch) -> None:
    client = _FakeActionClient(None, None, "")
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(_MOD, "_wait_for_future", lambda _future, _timeout: (False, None))
    skill = _skill_with_node()

    result = skill.execute({"confirm": True})
    assert result["success"] is False
    assert result["failure_reason"] == "action_timeout"


def test_result_timeout_reports_unknown(monkeypatch) -> None:
    client = _FakeActionClient(None, None, "")
    _patch_client(monkeypatch, client)
    goal_handle = client.goal_handle
    responses = [(True, goal_handle), (False, None)]
    monkeypatch.setattr(_MOD, "_wait_for_future", lambda _future, _timeout: responses.pop(0))
    skill = _skill_with_node()

    result = skill.execute({"confirm": True})
    assert result["success"] is False
    assert result["status"] == "timeout"
    assert result["failure_reason"] == "action_timeout"
    assert result["recoverable"] is True
    assert result["completion_confirmed"] is False


def test_motion_failure_reports_exit_code(monkeypatch) -> None:
    client = _FakeActionClient(None, None, "")
    client.goal_handle = _FakeGoalHandle(
        result=_FakeResult(success=False, exit_code=1, message="spawn error", output_tail="boom")
    )
    _patch_client(monkeypatch, client)
    skill = _skill_with_node()

    result = skill.execute({"confirm": True})
    assert result["success"] is False
    assert result["failure_reason"] == "motion_failed"
    assert result["exit_code"] == 1
    assert result["output_tail"] == "boom"


def test_child_timeout_message_maps_to_timeout(monkeypatch) -> None:
    client = _FakeActionClient(None, None, "")
    client.goal_handle = _FakeGoalHandle(
        result=_FakeResult(success=False, exit_code=-1, message="timeout after 60.0s")
    )
    _patch_client(monkeypatch, client)
    skill = _skill_with_node()

    result = skill.execute({"confirm": True})
    assert result["success"] is False
    assert result["failure_reason"] == "action_timeout"


def test_concurrent_request_is_busy(monkeypatch) -> None:
    client = _FakeActionClient(None, None, "")
    _patch_client(monkeypatch, client)
    skill = _skill_with_node()

    assert skill._publish_lock.acquire(blocking=False) is True
    try:
        result = skill.execute({"confirm": True})
    finally:
        skill._publish_lock.release()

    assert result["success"] is False
    assert result["failure_reason"] == "motion_busy"
    assert result["recoverable"] is True
    assert client.goals == []


def test_lock_released_after_run(monkeypatch) -> None:
    client = _FakeActionClient(None, None, "")
    _patch_client(monkeypatch, client)
    skill = _skill_with_node()

    skill.execute({"confirm": True})
    assert skill._publish_lock.acquire(blocking=False) is True
    skill._publish_lock.release()
