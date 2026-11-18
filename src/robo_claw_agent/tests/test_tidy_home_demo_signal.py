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
                result_data={
                    "motions": [
                        {"motion_id": 22, "motion_name": "greeting", "pre_id": None},
                    ]
                },
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
    """관찰 스킬 대역 — 마지막 호출 파라미터와 정면 결과를 기록한다."""

    def __init__(self, *, scene="신발장 앞 바닥에 신발이 보입니다.", objects=None):
        self.calls = []
        self._scene = scene
        self._objects = objects if objects is not None else ["shoe(0.91)"]

    def set_node(self, _node):
        return None

    def execute(self, params):
        self.calls.append(dict(params))
        if params.get("capture_4way") is True:
            return {
                "success": True,
                "message": "다방향 관찰 완료",
                "detected_objects": self._objects,
                "per_direction": {"정면": self._scene},
                "per_direction_meta": {
                    "정면": {"rotate_ok": True, "image_received": True, "analysis_ok": True}
                },
            }
        return {
            "success": True,
            "message": "상황 분석 완료",
            "detected_objects": self._objects,
            "scene_description": self._scene,
        }


def _scene_skill(monkeypatch, *, observer, demo_capable):
    calls = []
    skills = _Skills() if demo_capable else None

    class _PlainSkills:
        def has_skill(self, name):
            return name in {"navigate_to", "pick_object", "place_object"}

    node = SimpleNamespace(
        _memory=None,
        _skills=skills if demo_capable else _PlainSkills(),
        get_parameter=lambda _name: SimpleNamespace(value="[22]" if demo_capable else "[]"),
    )
    monkeypatch.setattr(
        tidy_module, "_create_surroundings_observer", lambda _event: observer, raising=False
    )
    skill = TidyHomeSkill()
    skill.set_node(node)
    calls.append(observer)
    return skill


def test_local_demo_scene_observation_skips_four_way_rotation(monkeypatch):
    observer = _Observer()
    skill = _scene_skill(monkeypatch, observer=observer, demo_capable=True)

    result = skill._analyze_current_scene("신발장")

    assert observer.calls[0]["capture_4way"] is False, "시연 개체는 사방 회전을 하지 않는다"
    assert observer.calls[0]["return_to_start"] is False
    assert result["success"] is True
    assert result["capture_4way"] is False
    assert result["orientation_restored"] is True
    assert result["detected_objects"] == ["shoe(0.91)"]
    assert "신발이 보입니다" in result["analysis"]


def test_general_cleanup_robot_keeps_four_way_observation(monkeypatch):
    observer = _Observer()
    skill = _scene_skill(monkeypatch, observer=observer, demo_capable=False)

    result = skill._analyze_current_scene("안방")

    assert observer.calls[0]["capture_4way"] is True
    assert observer.calls[0]["return_to_start"] is True
    assert result["success"] is True
    assert result["successful_directions"] == ["정면"]


def test_local_demo_scene_observation_fails_closed_without_front_scene(monkeypatch):
    observer = _Observer(scene="", objects=[])
    skill = _scene_skill(monkeypatch, observer=observer, demo_capable=True)

    result = skill._analyze_current_scene("신발장")

    assert result["success"] is False
    assert result["failure_reason"] == "surroundings_observation_failed"


def test_demo_motion_executed_is_not_reported_as_failure():
    """시연 모션 실행은 실패가 아니며, 관측 문구로 '정리 완료 아님'을 구분한다."""
    status, reason = tidy_module._run_observation_status(
        [{"place": "신발장", "status": "demo_motion_executed"}], ""
    )

    assert status == "finished"
    assert reason == ""
