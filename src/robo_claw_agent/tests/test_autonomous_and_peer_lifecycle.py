import math
from types import SimpleNamespace

import pytest
from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills.autonomous_skill.conditions import (
    CheckBatterySufficient,
)
from robo_claw_agent.skills.autonomous_skill.core import NodeStatus
from robo_claw_agent.skills.autonomous_skill.decision_nodes import (
    ValidateDecidedAction,
)
from robo_claw_agent.skills.autonomous_skill.execution_nodes import (
    CheckGoalAchieved,
    EmergencyLowBattery,
    ExecuteDecidedAction,
)
from robo_claw_agent.skills.autonomous_skill.globals import (
    _AUTONOMOUS_ACTIVE,
    _SLEEP_WAKE,
)
from robo_claw_agent.skills.autonomous_skill.skill import (
    AutonomousActSkill,
    StopAutonomousSkill,
)
from robo_claw_agent.skills.cooperate_skill import DelegateTaskSkill


class DummySkill(BaseSkill):
    name = "dummy"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": True}

    def execute(self, params):
        return {"success": True}


@pytest.mark.unit
def test_check_battery_sufficient_unknown_battery_fail_closed(monkeypatch):
    skill = DummySkill()
    node = SimpleNamespace()
    skill.set_node(node)

    # Battery topic returns None and sysfs battery is empty (0.0)
    monkeypatch.setattr(skill, "wait_for_message", lambda *args, **kwargs: None)

    checker = CheckBatterySufficient(skill, min_pct=20.0, allow_unknown=False, check_only=False)
    monkeypatch.setattr(checker, "_read_sysfs_battery", lambda: 0.0)

    blackboard = {}
    checker._blackboard = blackboard

    status = checker.tick()
    assert status == NodeStatus.FAILURE
    assert blackboard.get("low_battery_pct") == 0.0


@pytest.mark.unit
def test_check_battery_sufficient_defaults_to_check_only_when_unknown(monkeypatch):
    skill = DummySkill()
    node = SimpleNamespace()
    skill.set_node(node)

    # 시험용 로봇: 배터리 토픽이 전혀 들어오지 않음
    monkeypatch.setattr(skill, "wait_for_message", lambda *args, **kwargs: None)

    checker = CheckBatterySufficient(skill, min_pct=20.0)
    monkeypatch.setattr(checker, "_read_sysfs_battery", lambda: 0.0)

    blackboard = {}
    checker._blackboard = blackboard

    status = checker.tick()
    # 체크는 수행(상태 기록)하되 루프 종료를 유발하지 않도록 SUCCESS 반환
    assert status == NodeStatus.SUCCESS
    assert "battery_pct" in blackboard


@pytest.mark.unit
def test_check_battery_sufficient_low_battery_in_check_only_mode(monkeypatch):
    skill = DummySkill()
    node = SimpleNamespace()
    skill.set_node(node)

    # 배터리가 낮게 들어옴 (10%)
    mock_battery = SimpleNamespace(percentage=10.0)
    monkeypatch.setattr(skill, "wait_for_message", lambda *args, **kwargs: mock_battery)

    checker = CheckBatterySufficient(skill, min_pct=20.0, check_only=True)

    blackboard = {}
    checker._blackboard = blackboard

    status = checker.tick()
    assert status == NodeStatus.SUCCESS
    assert blackboard.get("low_battery_pct") == 10.0


@pytest.mark.unit
@pytest.mark.parametrize("percentage", [-1.0, math.nan, 101.0])
def test_check_battery_sufficient_treats_invalid_reading_as_unknown(monkeypatch, percentage):
    skill = DummySkill()
    skill.set_node(SimpleNamespace())
    monkeypatch.setattr(
        skill,
        "wait_for_message",
        lambda *args, **kwargs: SimpleNamespace(percentage=percentage),
    )

    checker = CheckBatterySufficient(skill, min_pct=20.0)
    blackboard = {}
    checker._blackboard = blackboard

    assert checker.tick() == NodeStatus.SUCCESS
    assert blackboard["battery_unknown"] is True
    assert blackboard["battery_pct"] == 0.0
    assert blackboard["low_battery_pct"] == 0.0


@pytest.mark.unit
def test_check_battery_sufficient_treats_explicit_zero_as_low_battery(monkeypatch):
    skill = DummySkill()
    skill.set_node(SimpleNamespace())
    monkeypatch.setattr(
        skill,
        "wait_for_message",
        lambda *args, **kwargs: SimpleNamespace(percentage=0.0),
    )

    checker = CheckBatterySufficient(skill, min_pct=20.0, allow_unknown=True, check_only=False)
    blackboard = {}
    checker._blackboard = blackboard

    assert checker.tick() == NodeStatus.FAILURE
    assert blackboard["battery_unknown"] is False
    assert blackboard["battery_pct"] == 0.0
    assert blackboard["low_battery_pct"] == 0.0


@pytest.mark.unit
def test_emergency_low_battery_check_only_does_not_clear_active_flag():
    skill = DummySkill()
    user_msgs = []
    skill.send_user_message = lambda msg: user_msgs.append(msg)

    emergency = EmergencyLowBattery(skill, check_only=True)
    emergency._blackboard = {"low_battery_pct": 5.0}

    _AUTONOMOUS_ACTIVE.set()
    try:
        status = emergency.tick()
        # 자율 루프 플래그가 꺼지지 않아야 함
        assert _AUTONOMOUS_ACTIVE.is_set() is True
        assert status == NodeStatus.SUCCESS
        assert len(user_msgs) == 1
        assert "체크" in user_msgs[0] or "계속" in user_msgs[0] or "5%" in user_msgs[0]
    finally:
        _AUTONOMOUS_ACTIVE.clear()


@pytest.mark.unit
def test_validate_decided_action_blocks_dangerous_and_recursive_skills():
    skill = DummySkill()
    node = SimpleNamespace(
        _skills=SimpleNamespace(
            has_skill=lambda name: True,
            get_skill=lambda name: SimpleNamespace(
                terminal_behavior="background",
                validate_params=lambda p: True,
            ),
        )
    )
    skill.set_node(node)

    validator = ValidateDecidedAction(skill)
    blackboard = {"decided_skill": "autonomous_act", "decided_params": {}}
    validator._blackboard = blackboard

    status = validator.tick()
    assert status == NodeStatus.FAILURE
    assert (
        "차단" in blackboard.get("decision_invalid_reason", "")
        or "금지" in blackboard.get("decision_invalid_reason", "")
        or "recursive" in blackboard.get("decision_invalid_reason", "").lower()
    )


@pytest.mark.unit
def test_validate_decided_action_validates_params():
    skill = DummySkill()
    dummy_target_skill = SimpleNamespace(
        terminal_behavior="sync",
        validate_params=lambda p: False,  # Params invalid!
    )
    node = SimpleNamespace(
        _skills=SimpleNamespace(
            has_skill=lambda name: True,
            get_skill=lambda name: dummy_target_skill,
        )
    )
    skill.set_node(node)

    validator = ValidateDecidedAction(skill)
    blackboard = {"decided_skill": "rotate", "decided_params": {"angle_deg": "invalid"}}
    validator._blackboard = blackboard

    status = validator.tick()
    assert status == NodeStatus.FAILURE
    assert "파라미터" in blackboard.get("decision_invalid_reason", "") or "유효" in blackboard.get(
        "decision_invalid_reason", ""
    )


@pytest.mark.unit
def test_check_goal_achieved_separates_task_queue_empty_and_goal_failure():
    skill = DummySkill()
    checker = CheckGoalAchieved(skill)

    # Empty task queue, but a step previously failed
    blackboard = {
        "task_queue": [],
        "last_action_success": False,
        "queue_execution_failed": True,
    }
    checker._blackboard = blackboard

    checker.tick()
    assert blackboard.get("goal_achieved") is not True


@pytest.mark.unit
def test_stop_autonomous_cancels_motion_and_wakes_sleep(monkeypatch):
    skill = StopAutonomousSkill()
    node = SimpleNamespace()
    skill.set_node(node)

    _AUTONOMOUS_ACTIVE.set()
    _SLEEP_WAKE.clear()

    canceled = []
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill._cancel_active_goal",
        lambda *args, **kwargs: canceled.append(True),
    )

    res = skill.execute({})
    assert res["success"] is True
    assert not _AUTONOMOUS_ACTIVE.is_set()
    assert _SLEEP_WAKE.is_set()
    assert len(canceled) > 0


@pytest.mark.unit
def test_execute_decided_action_tracks_repeat_failures(monkeypatch):
    skill = DummySkill()
    node = SimpleNamespace(
        _skills=SimpleNamespace(
            execute=lambda name, params, timeout_sec: SimpleNamespace(success=False, result_data={})
        )
    )
    skill.set_node(node)

    action_node = ExecuteDecidedAction(skill)
    blackboard = {
        "decided_skill": "navigate_to",
        "decided_params": {"target_name": "kitchen"},
        "decided_reason": "visit kitchen",
        "cycle_count": 1,
    }
    action_node._blackboard = blackboard

    action_node.tick()
    assert blackboard.get("repeat_failure_count") == 1

    action_node.tick()
    assert blackboard.get("repeat_failure_count") == 2


@pytest.mark.unit
def test_delegate_task_timeout_reports_unknown_remote_state(monkeypatch):
    skill = DelegateTaskSkill()
    node = SimpleNamespace()
    skill.set_node(node)

    class FakeActionClient:
        def __init__(self, *args, **kwargs):
            pass

        def wait_for_server(self, timeout_sec):
            return True

        def send_goal_async(self, goal):
            return object()

        def destroy(self):
            pass

    # Mock action client and _wait_for_future
    monkeypatch.setattr("rclpy.action.ActionClient", FakeActionClient)
    # First call (send_goal_async) succeeds with accepted handle
    # Second call (get_result_async) times out
    call_count = [0]

    def fake_wait_for_future(future, timeout_sec, message):
        call_count[0] += 1
        if call_count[0] == 1:
            return True, SimpleNamespace(accepted=True, get_result_async=lambda: object())
        return False, "에이전트 작업 실행 중 시간 초과"

    monkeypatch.setattr(
        "robo_claw_agent.skills.cooperate_skill._wait_for_future",
        fake_wait_for_future,
    )

    res = skill.execute({"agent_id": "stretch_2", "instruction": "pick up cup"})
    assert res["success"] is False
    assert res.get("remote_state") == "unknown" or "시간 초과" in res["message"]


@pytest.mark.unit
def test_autonomous_act_defaults_to_check_only_battery(monkeypatch):
    act_skill = AutonomousActSkill()
    node = SimpleNamespace()
    act_skill.set_node(node)

    # start_bt_thread를 mock하여 백그라운드 루프 실행 방지
    started_target = []
    monkeypatch.setattr(
        "robo_claw_agent.skills.autonomous_skill.skill.start_bt_thread",
        lambda target: started_target.append(target),
    )

    try:
        res = act_skill.execute({"mode": "patrol"})
        assert res["success"] is True
        assert res.get("check_only_battery") is True
        assert len(started_target) == 1
    finally:
        _AUTONOMOUS_ACTIVE.clear()
