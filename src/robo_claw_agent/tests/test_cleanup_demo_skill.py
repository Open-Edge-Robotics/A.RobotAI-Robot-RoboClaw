from types import SimpleNamespace

import pytest
from robo_claw_agent.skills.cleanup_demo_skill import CleanupDemoSkill

pytestmark = pytest.mark.unit


def _motion(motion_id=77, name="cleanup_demo", *, pre_id=None):
    return {
        "motion_id": motion_id,
        "motion_name": name,
        "pre_id": pre_id,
        "intent_id": "cleanup_demo",
        "description": "검토된 임시 정리 시연 동작",
        "mask": "WH",
    }


class _FakeSkills:
    def __init__(self, motions):
        self.motions = motions
        self.calls = []

    def has_skill(self, name):
        return name in {"list_cloid_motions", "execute_cloid_motion"}

    def execute(self, name, params, **kwargs):
        self.calls.append((name, params, kwargs))
        if name == "list_cloid_motions":
            return SimpleNamespace(
                success=True,
                message="ok",
                result_data={"motions": self.motions},
            )
        return SimpleNamespace(success=True, message="START command published")


def _configure_demo(monkeypatch, skill, *, scene_result, motions=None):
    from robo_claw_agent.skills.tidy_home_skill import TidyHomeSkill

    events = []
    fake_tidy = TidyHomeSkill
    monkeypatch.setattr(
        fake_tidy,
        "_collect_places",
        lambda _self, _scope, location: ([{"name": location}], None),
    )
    monkeypatch.setattr(
        fake_tidy,
        "_visit_place",
        lambda _self, location: (events.append(("visit", location)) or True, "arrived"),
    )
    monkeypatch.setattr(
        fake_tidy,
        "_analyze_current_scene",
        lambda _self, location: events.append(("observe", location)) or scene_result,
    )
    from robo_claw_agent.skills import cleanup_demo_skill as demo_module

    monkeypatch.setattr(demo_module, "claim_autonomous", lambda _event: True)
    monkeypatch.setattr(demo_module, "release_autonomous", lambda: None)

    skills = _FakeSkills(motions or [_motion()])
    node = SimpleNamespace(
        _memory=object(),
        _skills=skills,
        get_parameter=lambda _name: SimpleNamespace(value="[77]"),
    )
    skill.set_node(node)
    skill.send_user_message = lambda _message: None
    return skills, events


def test_cleanup_demo_relocates_reobserves_target_then_requests_only_allowlisted_motion(
    monkeypatch,
):
    skill = CleanupDemoSkill()
    skills, events = _configure_demo(
        monkeypatch,
        skill,
        scene_result={
            "success": True,
            "analysis": "안방 바닥에 포장지가 보입니다.",
            "detected_objects": [{"name": "포장지"}],
        },
    )

    result = skill.execute(
        {
            "target_location": "안방",
            "target_object": "포장지",
            "scene_summary": "바닥에 포장지가 있습니다.",
            "request_id": "req-1",
        }
    )

    assert result["success"] is True
    assert result["status"] == "motion_requested"
    assert result["physical_cleanup_confirmed"] is False
    assert result["motion_completion_confirmed"] is False
    assert events == [("visit", "안방"), ("observe", "안방")]
    assert [name for name, _params, _kwargs in skills.calls] == [
        "list_cloid_motions",
        "execute_cloid_motion",
    ]
    assert skills.calls[-1][1]["motion_id"] == 77
    assert skills.calls[-1][1]["confirm"] is True
    assert skills.calls[-1][2]["timeout_sec"] == 60.0


def test_cleanup_demo_aborts_when_requested_target_is_not_reobserved(monkeypatch):
    skill = CleanupDemoSkill()
    skills, _events = _configure_demo(
        monkeypatch,
        skill,
        scene_result={
            "success": True,
            "analysis": "안방에 컵이 있습니다.",
            "detected_objects": [{"name": "컵"}],
        },
    )

    result = skill.execute(
        {
            "target_location": "안방",
            "target_object": "포장지",
            "scene_summary": "바닥에 포장지가 있습니다.",
            "request_id": "req-not-seen",
        }
    )

    assert result["success"] is False
    assert result["failure_reason"] == "target_not_reobserved"
    assert all(name != "execute_cloid_motion" for name, _params, _kwargs in skills.calls)


def test_cleanup_demo_rejects_unapproved_or_prerequisite_motion(monkeypatch):
    skill = CleanupDemoSkill()
    skills, _events = _configure_demo(
        monkeypatch,
        skill,
        scene_result={
            "success": True,
            "analysis": "바닥의 포장지가 보입니다.",
            "detected_objects": [{"name": "포장지"}],
        },
        motions=[_motion(77, pre_id=76)],
    )

    result = skill.execute(
        {
            "target_location": "안방",
            "target_object": "포장지",
            "scene_summary": "바닥에 포장지가 있습니다.",
            "request_id": "req-prerequisite",
        }
    )

    assert result["success"] is False
    assert result["failure_reason"] == "no_approved_cleanup_demo_motion"
    assert all(name != "execute_cloid_motion" for name, _params, _kwargs in skills.calls)


def test_cleanup_demo_reobserves_korean_target_with_english_detection(monkeypatch):
    skill = CleanupDemoSkill()
    skills, events = _configure_demo(
        monkeypatch,
        skill,
        scene_result={
            "success": True,
            "analysis": "시각 분석 완료. 인식된 객체: towel",
            "detected_objects": [{"name": "towel"}],
        },
    )

    result = skill.execute(
        {
            "target_location": "안방",
            "target_object": "수건",
            "scene_summary": "수건이 있습니다.",
            "request_id": "req-towel-bilingual",
        }
    )

    assert result["success"] is True
    assert result["status"] == "motion_requested"
    assert events == [("visit", "안방"), ("observe", "안방")]
    assert any(name == "execute_cloid_motion" for name, _params, _kwargs in skills.calls)


def test_cleanup_demo_reobserves_cushion_with_english_detection(monkeypatch):
    skill = CleanupDemoSkill()
    skills, events = _configure_demo(
        monkeypatch,
        skill,
        scene_result={
            "success": True,
            "analysis": "거실 바닥에 cushion이 놓여 있습니다.",
            "detected_objects": [{"name": "cushion"}],
        },
    )

    result = skill.execute(
        {
            "target_location": "거실",
            "target_object": "쿠션",
            "scene_summary": "쿠션이 있습니다.",
            "request_id": "req-cushion-bilingual",
        }
    )

    assert result["success"] is True
    assert result["status"] == "motion_requested"
    assert events == [("visit", "거실"), ("observe", "거실")]
    assert any(name == "execute_cloid_motion" for name, _params, _kwargs in skills.calls)


def test_cleanup_demo_executes_when_any_cleanup_candidate_is_reobserved(monkeypatch):
    """요청 대상이 '쓰레기'여도 현장에서 수건/쿠션 등 정리 가능한 항목이 관찰되면 시연 모션을 수행한다."""
    skill = CleanupDemoSkill()
    skills, events = _configure_demo(
        monkeypatch,
        skill,
        scene_result={
            "success": True,
            "analysis": "안방 바닥에 수건이 놓여 있습니다.",
            "detected_objects": [{"name": "수건"}],
        },
    )

    result = skill.execute(
        {
            "target_location": "안방",
            "target_object": "쓰레기",
            "scene_summary": "방에 정리 대상이 있습니다.",
            "request_id": "req-cross-category",
        }
    )

    assert result["success"] is True
    assert result["status"] == "motion_requested"
    assert events == [("visit", "안방"), ("observe", "안방")]
    assert any(name == "execute_cloid_motion" for name, _params, _kwargs in skills.calls)


def test_cleanup_demo_executes_even_with_ambiguous_objects_in_scene(monkeypatch):
    """현장에 컵이나 옷 등 모호한 물체가 함께 있어도 정리 대상이 확인되면 시연을 중단하지 않고 모션을 수행한다."""
    skill = CleanupDemoSkill()
    skills, events = _configure_demo(
        monkeypatch,
        skill,
        scene_result={
            "success": True,
            "analysis": "안방에 포장지, 쿠션, 방석과 함께 컵이 보입니다.",
            "detected_objects": [
                {"name": "포장지"},
                {"name": "쿠션"},
                {"name": "방석"},
                {"name": "cup"},
            ],
        },
    )

    result = skill.execute(
        {
            "target_location": "안방",
            "target_object": "쿠션",
            "scene_summary": "방에 쿠션과 포장지가 있습니다.",
            "request_id": "req-ambiguous-tolerant",
        }
    )

    assert result["success"] is True
    assert result["status"] == "motion_requested"
    assert any(name == "execute_cloid_motion" for name, _params, _kwargs in skills.calls)
