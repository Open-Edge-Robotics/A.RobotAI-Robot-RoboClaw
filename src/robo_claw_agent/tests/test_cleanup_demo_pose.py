"""신발·신발장 시연 전 자세 회전 계약 및 두 시연 경로 연동 테스트."""

from types import SimpleNamespace

import pytest
from robo_claw_agent.skills.cleanup import demo_pose
from robo_claw_agent.skills.cleanup.policy import is_shoe_related

pytestmark = pytest.mark.unit


def _node(**params):
    return SimpleNamespace(
        get_parameter=lambda name: SimpleNamespace(value=params.get(name, 90.0)),
        _robot_limits_dict=None,
        _emergency_stopped=False,
        _emergency_stop_latched=False,
        is_emergency_stopped=lambda: False,
    )


@pytest.mark.parametrize(
    "values,expected",
    [
        (("신발장",), True),
        (("신발",), True),
        (("안방", "신발 정리"), True),
        (("shoe",), True),
        (("Shoes",), True),
        (("냉장고",), False),
        (("거실", "컵"), False),
        ((), False),
    ],
)
def test_shoe_related_matching(values, expected):
    assert is_shoe_related(*values) is expected


def test_turn_deg_defaults_to_90_for_shoe_requests_only():
    node = _node()

    assert demo_pose.turn_deg_for(node, "신발장") == 90.0
    assert demo_pose.turn_deg_for(node, "신발", "신발 정리") == 90.0
    assert demo_pose.turn_deg_for(node, "냉장고") == 0.0


def test_turn_deg_reads_profile_override_and_clamps_invalid():
    assert demo_pose.turn_deg_for(_node(cloid_demo_shoe_turn_deg=-90), "신발장") == -90.0
    assert demo_pose.turn_deg_for(_node(cloid_demo_shoe_turn_deg=0), "신발장") == 0.0
    # 잘못된 값은 기본값으로 되돌린다.
    assert demo_pose.turn_deg_for(_node(cloid_demo_shoe_turn_deg=999), "신발장") == 90.0
    assert demo_pose.turn_deg_for(_node(cloid_demo_shoe_turn_deg="abc"), "신발장") == 90.0


def test_turn_before_demo_spins_and_waits():
    calls = {}

    def _spin(node, delta_rad):
        calls["delta_rad"] = delta_rad
        return True, "accepted", object()

    def _wait(handle, timeout_sec, label):
        calls["timeout"] = timeout_sec
        calls["label"] = label
        return True, "success"

    ok, error = demo_pose.turn_before_demo(_node(), 90.0, spin=_spin, wait=_wait)

    assert (ok, error) == (True, "")
    assert abs(calls["delta_rad"] - 1.5707963) < 1e-6
    assert calls["label"] == demo_pose.TURN_LABEL


def test_turn_before_demo_fails_closed_on_spin_failure():
    def _spin(_node, _delta):
        return False, "Nav2의 '/spin' 액션 서버를 찾을 수 없습니다.", None

    ok, error = demo_pose.turn_before_demo(_node(), 90.0, spin=_spin, wait=lambda *_a: (True, ""))

    assert ok is False
    assert "spin" in error or "회전" in error or "spin" in error.lower()


def test_turn_before_demo_fails_closed_on_unconfirmed_result():
    ok, error = demo_pose.turn_before_demo(
        _node(),
        90.0,
        spin=lambda *_a: (True, "accepted", object()),
        wait=lambda *_a: (False, "시연 전 자세 회전 결과를 받지 못했습니다 (타임아웃)."),
    )

    assert ok is False
    assert "타임아웃" in error


def test_turn_before_demo_skips_when_angle_is_zero():
    def _spin(*_a):
        raise AssertionError("회전 각도가 0이면 회전을 시도하지 않는다")

    assert demo_pose.turn_before_demo(_node(), 0.0, spin=_spin) == (True, "")


# ── 두 시연 경로 연동(스킬 대역) ──────────────────────────────────


class _DemoSkills:
    """`list_cloid_motions`/`execute_cloid_motion` 만 가진 시연 개체 대역(호출 순서 기록)."""

    def __init__(self):
        self.calls = []
        self.executed = []

    def has_skill(self, name):
        return name in {"cleanup_demo", "execute_cloid_motion", "list_cloid_motions"}

    def execute(self, name, params, **kwargs):
        self.calls.append(name)
        if name == "list_cloid_motions":
            return SimpleNamespace(
                success=True,
                message="ok",
                result_data={"motions": [{"motion_id": 22, "motion_name": "scan", "pre_id": None}]},
            )
        if name == "execute_cloid_motion":
            self.executed.append(params)
        return SimpleNamespace(success=True, message="ok")


def _demo_node(skills):
    return SimpleNamespace(
        _memory=None,
        _skills=skills,
        get_parameter=lambda _name: SimpleNamespace(value="[22]"),
    )


def test_cleanup_demo_turns_before_motion_for_shoe_requests(monkeypatch):
    from robo_claw_agent.skills.cleanup import demo_skill as demo_module
    from robo_claw_agent.skills.cleanup.demo_skill import CleanupDemoSkill
    from robo_claw_agent.skills.tidy_home_skill import TidyHomeSkill

    monkeypatch.setattr(
        TidyHomeSkill, "_collect_places", lambda _s, _scope, name: ([{"name": name}], None)
    )
    monkeypatch.setattr(TidyHomeSkill, "_visit_place", lambda _s, _name: (True, "arrived"))
    monkeypatch.setattr(
        TidyHomeSkill,
        "_analyze_current_scene",
        lambda _s, _name: {
            "success": True,
            "analysis": "신발장 앞에 신발이 보입니다.",
            "detected_objects": [{"name": "신발"}],
        },
    )
    monkeypatch.setattr(demo_module, "claim_autonomous", lambda _event: True)
    monkeypatch.setattr(demo_module, "release_autonomous", lambda: None)

    skills = _DemoSkills()
    skill = CleanupDemoSkill()
    skill.set_node(_demo_node(skills))
    skill.send_user_message = lambda _msg: None

    order = []
    monkeypatch.setattr(
        demo_module, "turn_deg_for", lambda _node, *_a: order.append("turn_deg") or 90.0
    )
    monkeypatch.setattr(
        demo_module,
        "turn_before_demo",
        lambda _node, angle, **_kw: order.append(("turn", angle)) or (True, ""),
    )
    original_execute = skills.execute

    def _spy(name, params, **kwargs):
        if name == "execute_cloid_motion":
            order.append("motion")
        return original_execute(name, params, **kwargs)

    skills.execute = _spy

    result = skill.execute(
        {
            "target_location": "신발장",
            "target_object": "신발",
            "scene_summary": "신발장 앞 신발",
            "request_id": "req-shoe",
        }
    )

    assert result["success"] is True
    assert order == ["turn_deg", ("turn", 90.0), "motion"], f"회전이 모션보다 먼저여야 함: {order}"


def test_cleanup_demo_blocks_motion_when_turn_fails(monkeypatch):
    from robo_claw_agent.skills.cleanup import demo_skill as demo_module
    from robo_claw_agent.skills.cleanup.demo_skill import CleanupDemoSkill
    from robo_claw_agent.skills.tidy_home_skill import TidyHomeSkill

    monkeypatch.setattr(
        TidyHomeSkill, "_collect_places", lambda _s, _scope, name: ([{"name": name}], None)
    )
    monkeypatch.setattr(TidyHomeSkill, "_visit_place", lambda _s, _name: (True, "arrived"))
    monkeypatch.setattr(
        TidyHomeSkill,
        "_analyze_current_scene",
        lambda _s, _name: {
            "success": True,
            "analysis": "신발장 앞에 신발이 보입니다.",
            "detected_objects": [{"name": "신발"}],
        },
    )
    monkeypatch.setattr(demo_module, "claim_autonomous", lambda _event: True)
    monkeypatch.setattr(demo_module, "release_autonomous", lambda: None)
    monkeypatch.setattr(demo_module, "turn_deg_for", lambda _node, *_a: 90.0)
    monkeypatch.setattr(
        demo_module, "turn_before_demo", lambda _node, _angle, **_kw: (False, "spin_timeout")
    )

    skills = _DemoSkills()
    skill = CleanupDemoSkill()
    skill.set_node(_demo_node(skills))
    skill.send_user_message = lambda _msg: None

    result = skill.execute(
        {
            "target_location": "신발장",
            "target_object": "신발",
            "scene_summary": "신발장 앞 신발",
            "request_id": "req-shoe-fail",
        }
    )

    assert result["success"] is False
    assert result["failure_reason"] == "demo_turn_failed"
    assert skills.executed == [], "회전 실패 시 시연 모션을 실행하지 않는다"


def _tidy_demo_skill(monkeypatch, *, turn_result=(True, "")):
    from robo_claw_agent.skills import tidy_home_skill as tidy_module

    calls = []
    monkeypatch.setattr(
        tidy_module, "turn_deg_for", lambda _node, *_texts: calls.append("turn_deg") or 90.0
    )
    monkeypatch.setattr(
        tidy_module,
        "turn_before_demo",
        lambda _node, angle, **_kw: calls.append(("turn", angle)) or turn_result,
    )
    monkeypatch.setattr(tidy_module, "emit_demo_signal", lambda *_a, **_kw: {"status": "sent"})
    skills = _DemoSkills()
    skill = tidy_module.TidyHomeSkill()
    skill.set_node(_demo_node(skills))
    skill.send_user_message = lambda _msg: None
    return skill, skills, calls


def test_tidy_home_turns_before_local_demo_motion(monkeypatch):
    skill, skills, calls = _tidy_demo_skill(monkeypatch)

    status = skill._run_local_demo_motion("신발장", None)

    assert status == "demo_motion_executed"
    assert calls == ["turn_deg", ("turn", 90.0)]
    assert len(skills.executed) == 1


def test_tidy_home_blocks_local_demo_when_turn_fails(monkeypatch):
    skill, skills, _calls = _tidy_demo_skill(monkeypatch, turn_result=(False, "spin_timeout"))

    status = skill._run_local_demo_motion("신발장", None)

    assert status == "demo_turn_failed"
    assert skills.executed == [], "회전 실패 시 시연 모션을 실행하지 않는다"
