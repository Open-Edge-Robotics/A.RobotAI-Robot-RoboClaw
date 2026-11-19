"""tidy_home 로컬 시연(demo_motion_executed) 경로의 cue 전송 테스트."""

from types import SimpleNamespace

import pytest
from robo_claw_agent.skills import tidy_home_skill as tidy_module
from robo_claw_agent.skills.tidy_home_skill import TidyHomeSkill

pytestmark = pytest.mark.unit


class _Skills:
    """시연 가능 개체(execute_cloid_motion/cleanup_demo 보유)의 최소 스킬 대역."""

    def __init__(self, *, motion_success=True):
        self.motion_success = motion_success
        self.executed = []
        self.catalog = [{"motion_id": 22, "motion_name": "greeting", "pre_id": None}]

    def has_skill(self, name):
        return name in {
            "navigate_to",
            "execute_cloid_motion",
            "list_cloid_motions",
            "cleanup_demo",
        }

    def execute(self, name, params, **kwargs):
        if name == "list_cloid_motions":
            return SimpleNamespace(
                success=True,
                message="ok",
                result_data={"motions": list(self.catalog)},
            )
        if name == "execute_cloid_motion":
            self.executed.append(params)
            return SimpleNamespace(success=self.motion_success, message="executed")
        return SimpleNamespace(success=True, message="ok")


def _configure(monkeypatch, *, motion_success=True):
    emitted: list[dict] = []

    def _emit(_node, _observer, **kwargs):
        emitted.append(kwargs)
        return {
            "status": "sent",
            "reason": "",
            "bytes": 9,
            "protocol": "tcp",
            "host": "h",
            "port": 1,
        }

    monkeypatch.setattr(tidy_module, "emit_demo_signal", _emit)

    skills = _Skills(motion_success=motion_success)
    node = SimpleNamespace(
        _memory=None,
        _skills=skills,
        get_parameter=lambda _name: SimpleNamespace(value="[22]"),
    )
    skill = TidyHomeSkill()
    skill.set_node(node)
    skill.send_user_message = lambda _msg: None
    return skill, skills, emitted


SCENE = "안방 바닥에 포장지와 쿠션이 놓여 있습니다."
OBJECTS = [{"name": "포장지"}, {"name": "쿠션"}]


def test_local_demo_emits_cue_when_motion_executed(monkeypatch):
    skill, skills, emitted = _configure(monkeypatch)

    status = skill._handle_cleanup_target(place_name="안방", scene=SCENE, objects=OBJECTS, cycle=0)

    assert status == "demo_motion_executed"
    assert len(skills.executed) == 1
    assert len(emitted) == 1, "demo_motion_executed 경로에서 cue 를 1회 전송해야 한다"
    assert emitted[0]["place"] == "안방"


def test_local_demo_does_not_emit_cue_when_execution_node_fails(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import actions as actions_module
    from robo_claw_agent.skills.autonomous_skill.core import NodeStatus

    skill, _skills, emitted = _configure(monkeypatch)
    monkeypatch.setattr(
        actions_module.ExecuteDecidedAction, "tick", lambda _self: NodeStatus.FAILURE
    )

    status = skill._handle_cleanup_target(place_name="안방", scene=SCENE, objects=OBJECTS, cycle=0)

    assert status == "execution_failed"
    assert emitted == [], "모션 실행 노드가 실패하면 cue 를 보내지 않는다"


class _Observer:
    """관찰 스킬 대역 — 시연 경로에서는 호출되지 않아야 한다."""

    def __init__(self):
        self.calls = []

    def set_node(self, _node):
        return None

    def execute(self, params):
        self.calls.append(dict(params))
        return {
            "success": True,
            "message": "다방향 관찰 완료",
            "detected_objects": ["shoe(0.91)"],
            "per_direction": {"정면": "신발장 앞 바닥에 신발이 보입니다."},
            "per_direction_meta": {
                "정면": {"rotate_ok": True, "image_received": True, "analysis_ok": True}
            },
        }


class _RaisingSkills(_Skills):
    """execute_cloid_motion 이 예외로 끝나는 시연 스킬 대역."""

    def execute(self, name, params, **kwargs):
        if name == "execute_cloid_motion":
            self.executed.append(params)
            raise RuntimeError("motion boom")
        return super().execute(name, params, **kwargs)


def _node(*, allowlist="[22]", skills=None):
    return SimpleNamespace(
        _memory=None,
        _skills=skills if skills is not None else _Skills(),
        get_parameter=lambda _name: SimpleNamespace(value=allowlist),
    )


def test_local_demo_requests_allowlisted_motion_without_observation(monkeypatch):
    emitted = []

    def _emit(_node, _observer, **kwargs):
        emitted.append(kwargs)
        return {"status": "sent", "reason": "", "bytes": 9}

    monkeypatch.setattr(tidy_module, "emit_demo_signal", _emit)
    # cue/실행만 검증하므로 시연 전 회전과 시연 후 STOP 은 대역으로 둔다.
    monkeypatch.setattr(tidy_module, "apply_before_demo", lambda *_a, **_kw: (True, "", None))
    monkeypatch.setattr(
        tidy_module, "stop_after_demo", lambda *_a, **_kw: {"status": "sent", "reason": ""}
    )
    skill = TidyHomeSkill()
    skill.set_node(_node())
    skill.send_user_message = lambda _msg: None
    observer = _Observer()

    status = skill._run_local_demo_motion("신발장", observer)

    assert status == "demo_motion_executed"
    executed = skill.node._skills.executed
    assert len(executed) == 1, "시연 모션은 1회만 요청한다"
    assert executed[0]["motion_id"] == 22
    assert executed[0]["confirm"] is True
    assert observer.calls == [], "시연 경로에서는 사방 관찰을 하지 않는다"
    assert len(emitted) == 1, "모션 요청 직후 cue 를 1회 보낸다"


def test_local_demo_is_blocked_without_executable_allowlisted_motion(monkeypatch):
    emitted = []
    monkeypatch.setattr(tidy_module, "emit_demo_signal", lambda *_a, **_kw: emitted.append(True))
    skills = _Skills()
    skills.catalog = [{"motion_id": 22, "motion_name": "greeting", "pre_id": 59}]
    skill = TidyHomeSkill()
    skill.set_node(_node(skills=skills))
    skill.send_user_message = lambda _msg: None

    status = skill._run_local_demo_motion("신발장", _Observer())

    assert status == "no_approved_cleanup_demo_motion"
    assert skills.executed == []
    assert emitted == []


def test_local_demo_stops_motion_when_motion_execution_fails(monkeypatch):
    stops: list[float | None] = []
    emitted: list[bool] = []
    monkeypatch.setattr(tidy_module, "emit_demo_signal", lambda *_a, **_kw: emitted.append(True))
    monkeypatch.setattr(tidy_module, "apply_before_demo", lambda *_a, **_kw: (True, "", None))
    monkeypatch.setattr(
        tidy_module,
        "stop_after_demo",
        lambda _node, **kwargs: (
            stops.append(kwargs.get("wait_sec")) or {"status": "sent", "reason": ""}
        ),
    )
    skill = TidyHomeSkill()
    skill.set_node(_node(skills=_Skills(motion_success=False)))
    skill.send_user_message = lambda _msg: None

    status = skill._run_local_demo_motion("신발장", _Observer())

    assert status == "execution_failed"
    assert stops == [0.0], "START 가 발행됐을 수 있으므로 실패해도 STOP 을 보낸다"
    assert emitted == [], "모션 실행이 실패하면 cue 는 보내지 않는다"


def test_local_demo_stops_motion_when_motion_execution_raises(monkeypatch):
    stops: list[float | None] = []
    monkeypatch.setattr(tidy_module, "apply_before_demo", lambda *_a, **_kw: (True, "", None))
    monkeypatch.setattr(
        tidy_module,
        "stop_after_demo",
        lambda _node, **kwargs: (
            stops.append(kwargs.get("wait_sec")) or {"status": "sent", "reason": ""}
        ),
    )
    skill = TidyHomeSkill()
    skill.set_node(_node(skills=_RaisingSkills()))
    skill.send_user_message = lambda _msg: None

    status = skill._run_local_demo_motion("신발장", _Observer())

    assert status == "execution_failed"
    assert stops == [0.0], "예외로 끝나도 STOP 으로 joint 를 돌려준다"


def test_demo_decision_branch_stops_motion_when_execution_fails(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import actions as actions_module
    from robo_claw_agent.skills.autonomous_skill.core import NodeStatus

    skill, _skills, emitted = _configure(monkeypatch)
    monkeypatch.setattr(tidy_module, "apply_before_demo", lambda *_a, **_kw: (True, "", None))
    monkeypatch.setattr(
        actions_module.ExecuteDecidedAction, "tick", lambda _self: NodeStatus.FAILURE
    )
    stops: list[float | None] = []
    monkeypatch.setattr(
        tidy_module,
        "stop_after_demo",
        lambda _node, **kwargs: (
            stops.append(kwargs.get("wait_sec")) or {"status": "sent", "reason": ""}
        ),
    )

    status = skill._handle_cleanup_target(
        place_name="신발장", scene=SCENE, objects=OBJECTS, cycle=0
    )

    assert status == "execution_failed"
    assert stops == [0.0], "LLM 결정 분기가 실패해도 STOP 으로 joint 를 돌려준다"
    assert emitted == []


def test_local_demo_execution_failure_does_not_emit_cue(monkeypatch):
    emitted = []
    monkeypatch.setattr(tidy_module, "emit_demo_signal", lambda *_a, **_kw: emitted.append(True))
    # cue/실행만 검증하므로 시연 전 회전과 시연 후 STOP 은 대역으로 둔다.
    monkeypatch.setattr(tidy_module, "apply_before_demo", lambda *_a, **_kw: (True, "", None))
    monkeypatch.setattr(
        tidy_module, "stop_after_demo", lambda *_a, **_kw: {"status": "sent", "reason": ""}
    )
    skill = TidyHomeSkill()
    skill.set_node(_node(skills=_Skills(motion_success=False)))
    skill.send_user_message = lambda _msg: None

    status = skill._run_local_demo_motion("신발장", _Observer())

    assert status == "execution_failed"
    assert emitted == [], "모션 실행이 실패하면 cue 를 보내지 않는다"


def test_demo_only_robot_requires_allowlist_and_motion_skills():
    skill = TidyHomeSkill()
    skill.set_node(_node(allowlist="[]"))
    assert skill._local_demo_runs_motion_only() is False

    class _NoDemoSkills:
        def has_skill(self, _name):
            return False

    skill.set_node(_node(skills=_NoDemoSkills()))
    assert skill._local_demo_runs_motion_only() is False

    skill.set_node(_node())
    assert skill._local_demo_runs_motion_only() is True


def test_general_cleanup_robot_keeps_four_way_observation(monkeypatch):
    observer = _Observer()
    monkeypatch.setattr(
        tidy_module, "_create_surroundings_observer", lambda _event: observer, raising=False
    )
    skill = TidyHomeSkill()
    skill.set_node(_node(allowlist="[]"))

    result = skill._analyze_current_scene("안방")

    assert observer.calls[0]["capture_4way"] is True
    assert observer.calls[0]["return_to_start"] is True
    assert result["success"] is True
    assert result["successful_directions"] == ["정면"]


def test_demo_motion_executed_is_not_reported_as_failure():
    """시연 모션 실행은 실패가 아니며, 관측 문구로 '정리 완료 아님'을 구분한다."""
    status, reason = tidy_module._run_observation_status(
        [{"place": "신발장", "status": "demo_motion_executed"}], ""
    )

    assert status == "finished"
    assert reason == ""
