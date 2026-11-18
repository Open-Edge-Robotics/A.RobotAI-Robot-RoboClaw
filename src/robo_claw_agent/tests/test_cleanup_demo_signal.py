"""cleanup_demo 의 데모 cue 전송 연동 테스트(best-effort, 모션 결과 불변)."""

from types import SimpleNamespace

import pytest
from robo_claw_agent.skills.cleanup import demo_skill as demo_module
from robo_claw_agent.skills.cleanup.demo_skill import CleanupDemoSkill

pytestmark = pytest.mark.unit

MOTION = {
    "motion_id": 77,
    "motion_name": "cleanup_demo",
    "pre_id": None,
    "intent_id": "cleanup_demo",
    "description": "검토된 임시 정리 시연 동작",
    "mask": "WH",
}
PARAMS = {
    "target_location": "안방",
    "target_object": "신발",
    "scene_summary": "안방 바닥에 신발이 있습니다.",
    "request_id": "req-cue",
}
SCENE = {
    "success": True,
    "analysis": "안방 바닥에 신발 한 켤레가 보입니다.",
    "detected_objects": [{"name": "신발"}],
}


class _FakeSkills:
    def __init__(self, *, motion_result_success=True):
        self.motion_result_success = motion_result_success
        self.calls = []

    def has_skill(self, name):
        return name in {"list_cloid_motions", "execute_cloid_motion"}

    def execute(self, name, params, **kwargs):
        self.calls.append(name)
        if name == "list_cloid_motions":
            return SimpleNamespace(success=True, message="ok", result_data={"motions": [MOTION]})
        return SimpleNamespace(
            success=self.motion_result_success, message="START command published"
        )


def _configure(monkeypatch, skill, *, motion_result_success=True):
    from robo_claw_agent.skills.tidy_home_skill import TidyHomeSkill

    monkeypatch.setattr(
        TidyHomeSkill,
        "_collect_places",
        lambda _self, _scope, location: ([{"name": location}], None),
    )
    monkeypatch.setattr(TidyHomeSkill, "_visit_place", lambda _self, _location: (True, "arrived"))
    monkeypatch.setattr(TidyHomeSkill, "_analyze_current_scene", lambda _self, _location: SCENE)
    monkeypatch.setattr(demo_module, "claim_autonomous", lambda _event: True)
    monkeypatch.setattr(demo_module, "release_autonomous", lambda: None)

    observed: list[dict] = []
    monkeypatch.setattr(
        demo_module,
        "safe_observe",
        lambda _observer, _method, *args, **kwargs: observed.append(kwargs),
    )
    cues: list[dict] = []

    def _cue(_node):
        cues.append({})
        return {
            "status": "sent",
            "reason": "",
            "protocol": "tcp",
            "host": "192.168.50.86",
            "port": 5005,
            "bytes": len(b"shoe pick"),
        }

    monkeypatch.setattr(demo_module, "send_demo_cue", _cue)

    skills = _FakeSkills(motion_result_success=motion_result_success)
    node = SimpleNamespace(
        _memory=object(),
        _skills=skills,
        get_parameter=lambda _name: SimpleNamespace(value="[77]"),
    )
    skill.set_node(node)
    skill.send_user_message = lambda _message: None
    return skills, cues, observed


def test_cleanup_demo_sends_one_cue_after_motion_request(monkeypatch):
    skill = CleanupDemoSkill()
    skills, cues, observed = _configure(monkeypatch, skill)

    result = skill.execute(dict(PARAMS))

    assert result["success"] is True
    assert result["status"] == "motion_requested"
    assert len(cues) == 1, "모션 요청 1회당 cue 는 1회만 전송한다"
    assert result["demo_signal"] == {
        "status": "sent",
        "reason": "",
        "protocol": "tcp",
        "host": "192.168.50.86",
        "port": 5005,
        "bytes": len(b"shoe pick"),
    }
    assert skills.calls == ["list_cloid_motions", "execute_cloid_motion"]
    phases = [item.get("phase") for item in observed if "phase" in item]
    assert "demo_signal" in phases
    signal_phase = next(item for item in observed if item.get("phase") == "demo_signal")
    assert signal_phase["observed"] == "sent"
    assert "shoe" not in str(signal_phase), "cue payload 원문을 관측에 남기지 않는다"


def test_cue_is_not_sent_when_motion_request_fails(monkeypatch):
    skill = CleanupDemoSkill()
    skills, cues, observed = _configure(monkeypatch, skill, motion_result_success=False)

    result = skill.execute(dict(PARAMS))

    assert result["success"] is False
    assert result["failure_reason"] == "motion_request_failed"
    assert cues == [], "모션 명령이 실패하면 외부 cue 를 보내지 않는다"
    assert all(item.get("phase") != "demo_signal" for item in observed)


def test_cue_failure_keeps_motion_result_success(monkeypatch):
    skill = CleanupDemoSkill()
    _skills, _cues, observed = _configure(monkeypatch, skill)

    def _failing_cue(_node):
        return {
            "status": "failed",
            "reason": "send_failed",
            "bytes": len(b"shoe pick"),
            "error": "ConnectionRefusedError",
        }

    monkeypatch.setattr(demo_module, "send_demo_cue", _failing_cue)

    result = skill.execute(dict(PARAMS))

    assert result["success"] is True
    assert result["status"] == "motion_requested"
    assert result["demo_signal"]["status"] == "failed"
    assert result["demo_signal"]["reason"] == "send_failed"
    assert result["physical_cleanup_confirmed"] is False
    assert result["motion_completion_confirmed"] is False
    signal_phase = next(item for item in observed if item.get("phase") == "demo_signal")
    assert signal_phase["observed"] == "failed"
    assert "cue" in signal_phase["content"]
    assert "failed" in signal_phase["content"]


def test_cue_exception_never_breaks_the_demo(monkeypatch):
    skill = CleanupDemoSkill()
    _skills, _cues, _observed = _configure(monkeypatch, skill)

    def _boom(_node):
        raise RuntimeError("socket layer exploded")

    monkeypatch.setattr(demo_module, "send_demo_cue", _boom)

    result = skill.execute(dict(PARAMS))

    assert result["success"] is True
    assert result["demo_signal"]["status"] == "failed"
    assert result["demo_signal"]["reason"] == "send_failed"


def test_disabled_cue_reports_skipped_without_failing(monkeypatch):
    skill = CleanupDemoSkill()
    _skills, _cues, _observed = _configure(monkeypatch, skill)
    monkeypatch.setattr(
        demo_module,
        "send_demo_cue",
        lambda _node: {"status": "skipped", "reason": "disabled", "bytes": 0},
    )

    result = skill.execute(dict(PARAMS))

    assert result["success"] is True
    assert result["demo_signal"] == {"status": "skipped", "reason": "disabled", "bytes": 0}
