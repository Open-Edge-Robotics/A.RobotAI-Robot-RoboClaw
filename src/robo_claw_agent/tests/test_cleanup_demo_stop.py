"""시연 모션 종료 후 STOP 계약 및 두 시연 경로 순서 테스트(STOP → cue)."""

from types import SimpleNamespace

import pytest
from robo_claw_agent.skills.cleanup import demo_stop

pytestmark = pytest.mark.unit


def _node(**params):
    return SimpleNamespace(get_parameter=lambda name: SimpleNamespace(value=params.get(name)))


def test_default_wait_is_eight_seconds():
    assert demo_stop.stop_after_sec_for(_node()) == 8.0
    assert demo_stop.stop_after_sec_for(_node(cloid_demo_stop_after_sec=3.5)) == 3.5
    assert demo_stop.stop_after_sec_for(_node(cloid_demo_stop_after_sec=0)) == 0.0


@pytest.mark.parametrize("value", [-1, 61, "abc", True, float("inf")])
def test_invalid_wait_is_rejected(value):
    node = _node(cloid_demo_stop_after_sec=value)
    assert demo_stop.stop_after_sec_for(node) is None

    waits = []
    outcome = demo_stop.stop_after_demo(node, sleep=waits.append, publish=lambda _node: (True, ""))
    assert outcome["status"] == "failed"
    assert outcome["reason"] == demo_stop.REASON_INVALID_WAIT
    assert waits == []


def test_waits_then_publishes_stop_once():
    waits = []
    published = []

    outcome = demo_stop.stop_after_demo(
        _node(cloid_demo_stop_after_sec=2.0),
        sleep=waits.append,
        publish=lambda _node: published.append(True) or (True, ""),
    )

    assert waits == [2.0]
    assert published == [True]
    assert outcome == {"status": "sent", "reason": "", "wait_sec": 2.0}


def test_zero_wait_publishes_without_sleeping():
    waits = []
    outcome = demo_stop.stop_after_demo(
        _node(cloid_demo_stop_after_sec=0),
        sleep=waits.append,
        publish=lambda _node: (True, ""),
    )

    assert waits == []
    assert outcome["status"] == "sent"


def test_publish_failure_is_reported_not_raised():
    outcome = demo_stop.stop_after_demo(
        _node(cloid_demo_stop_after_sec=0),
        sleep=lambda _w: None,
        publish=lambda _node: (False, "motion_player_unavailable"),
    )

    assert outcome["status"] == "failed"
    assert outcome["reason"] == "motion_player_unavailable"


def test_publish_exception_is_reported_not_raised():
    def _boom(_node):
        raise RuntimeError("publish exploded")

    outcome = demo_stop.stop_after_demo(
        _node(cloid_demo_stop_after_sec=0), sleep=lambda _w: None, publish=_boom
    )

    assert outcome["status"] == "failed"
    assert "stop_failed" in outcome["reason"]


# ── 두 시연 경로 순서: 모션 → STOP → cue ─────────────────────────


class _DemoSkills:
    def __init__(self):
        self.executed = []

    def has_skill(self, name):
        return name in {"cleanup_demo", "execute_cloid_motion", "list_cloid_motions"}

    def execute(self, name, params, **kwargs):
        if name == "list_cloid_motions":
            return SimpleNamespace(
                success=True,
                message="ok",
                result_data={"motions": [{"motion_id": 22, "motion_name": "scan", "pre_id": None}]},
            )
        if name == "execute_cloid_motion":
            self.executed.append(params)
        return SimpleNamespace(success=True, message="ok")


def _node_with(skills):
    return SimpleNamespace(
        _memory=None,
        _skills=skills,
        get_parameter=lambda _name: SimpleNamespace(value="[22]"),
    )


def test_tidy_home_order_is_motion_stop_then_cue(monkeypatch):
    from robo_claw_agent.skills import tidy_home_skill as tidy_module

    order = []
    monkeypatch.setattr(tidy_module, "turn_deg_for", lambda *_a: 0.0)
    monkeypatch.setattr(
        tidy_module,
        "stop_after_demo",
        lambda _node, **_kw: order.append("stop") or {"status": "sent"},
    )
    monkeypatch.setattr(
        tidy_module,
        "emit_demo_signal",
        lambda *_a, **_kw: order.append("cue") or {"status": "sent"},
    )
    skills = _DemoSkills()
    skill = tidy_module.TidyHomeSkill()
    skill.set_node(_node_with(skills))
    skill.send_user_message = lambda _msg: None
    original_execute = skills.execute

    def _spy(name, params, **kwargs):
        if name == "execute_cloid_motion":
            order.append("motion")
        return original_execute(name, params, **kwargs)

    skills.execute = _spy

    assert skill._run_local_demo_motion("냉장고", None) == "demo_motion_executed"
    assert order == ["motion", "stop", "cue"], f"STOP 이 cue 보다 먼저여야 함: {order}"


def test_cleanup_demo_order_is_motion_stop_then_cue(monkeypatch):
    from robo_claw_agent.skills.cleanup import demo_skill as demo_module
    from robo_claw_agent.skills.cleanup.demo_skill import CleanupDemoSkill
    from robo_claw_agent.skills.tidy_home_skill import TidyHomeSkill

    monkeypatch.setattr(
        TidyHomeSkill, "_collect_places", lambda _s, _scope, name: ([{"name": name}], None)
    )
    monkeypatch.setattr(TidyHomeSkill, "_visit_place", lambda _s, _name: (True, "arrived"))
    monkeypatch.setattr(demo_module, "claim_autonomous", lambda _event: True)
    monkeypatch.setattr(demo_module, "release_autonomous", lambda: None)
    monkeypatch.setattr(demo_module, "turn_deg_for", lambda *_texts: 0.0)

    order = []
    monkeypatch.setattr(
        demo_module,
        "stop_after_demo",
        lambda _node, **_kw: order.append("stop") or {"status": "sent"},
    )
    monkeypatch.setattr(
        demo_module,
        "emit_demo_signal",
        lambda *_a, **_kw: order.append("cue") or {"status": "sent", "bytes": 9},
    )
    skills = _DemoSkills()
    skill = CleanupDemoSkill()
    skill.set_node(_node_with(skills))
    skill.send_user_message = lambda _msg: None
    original_execute = skills.execute

    def _spy(name, params, **kwargs):
        if name == "execute_cloid_motion":
            order.append("motion")
        return original_execute(name, params, **kwargs)

    skills.execute = _spy

    result = skill.execute(
        {
            "target_location": "안방",
            "target_object": "포장지",
            "scene_summary": "바닥에 포장지가 있습니다.",
            "request_id": "req-order",
        }
    )

    assert result["success"] is True
    assert order == ["motion", "stop", "cue"], f"STOP 이 cue 보다 먼저여야 함: {order}"
    assert result["demo_stop"] == {"status": "sent"}


def test_cleanup_demo_reports_stop_failure_without_failing(monkeypatch):
    from robo_claw_agent.skills.cleanup import demo_skill as demo_module
    from robo_claw_agent.skills.cleanup.demo_skill import CleanupDemoSkill
    from robo_claw_agent.skills.tidy_home_skill import TidyHomeSkill

    monkeypatch.setattr(
        TidyHomeSkill, "_collect_places", lambda _s, _scope, name: ([{"name": name}], None)
    )
    monkeypatch.setattr(TidyHomeSkill, "_visit_place", lambda _s, _name: (True, "arrived"))
    monkeypatch.setattr(demo_module, "claim_autonomous", lambda _event: True)
    monkeypatch.setattr(demo_module, "release_autonomous", lambda: None)
    monkeypatch.setattr(demo_module, "turn_deg_for", lambda *_texts: 0.0)
    monkeypatch.setattr(
        demo_module,
        "stop_after_demo",
        lambda _node, **_kw: {"status": "failed", "reason": "motion_player_unavailable"},
    )
    cues = []
    monkeypatch.setattr(
        demo_module, "emit_demo_signal", lambda *_a, **_kw: cues.append(True) or {"status": "sent"}
    )
    skill = CleanupDemoSkill()
    skill.set_node(_node_with(_DemoSkills()))
    skill.send_user_message = lambda _msg: None

    result = skill.execute(
        {
            "target_location": "안방",
            "target_object": "포장지",
            "scene_summary": "바닥에 포장지가 있습니다.",
            "request_id": "req-stop-fail",
        }
    )

    assert result["success"] is True
    assert result["demo_stop"]["status"] == "failed"
    assert cues == [True], "STOP 실패에도 cue 는 전송한다"
