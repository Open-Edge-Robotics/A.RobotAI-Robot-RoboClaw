"""신발·신발장 시연 전 절대 방위 정렬 계약 및 시연 경로 연동 테스트."""

import math
from types import SimpleNamespace

import pytest
from robo_claw_agent.skills.cleanup import demo_pose
from robo_claw_agent.skills.cleanup.policy import is_shoe_related

pytestmark = pytest.mark.unit


def _node(**params):
    return SimpleNamespace(
        get_parameter=lambda name: SimpleNamespace(value=params.get(name)),
        _robot_limits_dict=None,
        _emergency_stopped=False,
        _emergency_stop_latched=False,
        is_emergency_stopped=lambda: False,
    )


def _pose(yaw_rad, frame="map"):
    return {"x": 0.0, "y": 0.0, "yaw": yaw_rad, "frame": frame}


@pytest.mark.parametrize(
    "values,expected",
    [
        (("신발장",), True),
        (("신발",), True),
        (("안방", "신발 정리"), True),
        (("shoe",), True),
        (("냉장고",), False),
        ((), False),
    ],
)
def test_shoe_related_matching(values, expected):
    assert is_shoe_related(*values) is expected


def test_heading_defaults_to_64_2_only_for_shoe_requests():
    node = _node()

    assert demo_pose.heading_deg_for(node, "신발장") == 64.2
    assert demo_pose.heading_deg_for(node, "신발", "신발 정리") == 64.2
    assert demo_pose.heading_deg_for(node, "냉장고") is None


def test_heading_overrides_and_invalid_values_fall_back_to_default():
    assert demo_pose.heading_deg_for(_node(cloid_demo_shoe_heading_deg=-30), "신발장") == -30.0
    # 빈 값은 회전하지 않는다.
    assert demo_pose.heading_deg_for(_node(cloid_demo_shoe_heading_deg=""), "신발장") is None
    # 범위 밖/비수치는 기본값으로 되돌린다.
    assert demo_pose.heading_deg_for(_node(cloid_demo_shoe_heading_deg=999), "신발장") == 64.2
    assert demo_pose.heading_deg_for(_node(cloid_demo_shoe_heading_deg="abc"), "신발장") == 64.2


def test_delta_is_shortest_rotation_to_absolute_heading():
    target = 64.2
    assert demo_pose.heading_delta_deg(0.0, target) == pytest.approx(target)
    assert demo_pose.heading_delta_deg(math.radians(90.0), target) == pytest.approx(-25.8)
    # 경계 넘김은 최단 회전으로 계산한다(180° vs -176.8°).
    assert demo_pose.heading_delta_deg(math.radians(-120.0), target) == pytest.approx(-175.8)
    assert demo_pose.heading_delta_deg(math.radians(350.0), target) == pytest.approx(74.2)


def test_plan_uses_current_map_yaw():
    turn = demo_pose.plan_demo_turn(_node(), "신발장", pose=_pose(math.radians(10.0)))

    assert turn is not None
    assert turn.heading_deg == 64.2
    assert turn.delta_deg == pytest.approx(54.2)


def test_plan_is_none_without_pose_or_for_non_shoe_requests():
    assert demo_pose.plan_demo_turn(_node(), "신발장", pose=None) is None
    assert demo_pose.plan_demo_turn(_node(), "신발장", pose=_pose(0.0, frame="odom")) is None
    assert demo_pose.plan_demo_turn(_node(), "냉장고", pose=_pose(0.0)) is None


def test_turn_before_demo_spins_by_computed_delta_and_waits():
    calls = {}

    def _spin(_node, delta_rad):
        calls["delta_rad"] = delta_rad
        return True, "accepted", object()

    def _wait(_handle, timeout_sec, label):
        calls["label"] = label
        return True, "success"

    turn = demo_pose.DemoTurn(heading_deg=64.2, delta_deg=64.2)
    ok, error = demo_pose.turn_before_demo(_node(), turn, spin=_spin, wait=_wait)

    assert (ok, error) == (True, "")
    assert calls["delta_rad"] == pytest.approx(math.radians(64.2))
    assert calls["label"] == demo_pose.TURN_LABEL


def test_turn_before_demo_skips_when_already_facing_heading():
    def _spin(*_args):
        raise AssertionError("이미 정렬된 경우 회전을 시도하지 않는다")

    turn = demo_pose.DemoTurn(heading_deg=64.2, delta_deg=0.2)
    assert demo_pose.turn_before_demo(_node(), turn, spin=_spin) == (True, "")


def test_turn_before_demo_fails_closed_on_spin_failure_and_unconfirmed_result():
    turn = demo_pose.DemoTurn(heading_deg=64.2, delta_deg=-25.8)

    ok, error = demo_pose.turn_before_demo(
        _node(), turn, spin=lambda *_a: (False, "spin action server 없음", None)
    )
    assert ok is False and error

    ok, error = demo_pose.turn_before_demo(
        _node(),
        turn,
        spin=lambda *_a: (True, "accepted", object()),
        wait=lambda *_a: (False, "시연 전 자세 회전 결과를 받지 못했습니다 (타임아웃)."),
    )
    assert ok is False
    assert "타임아웃" in error


# ── 시연 경로 연동 ─────────────────────────────────────────────


class _DemoSkills:
    """list/execute_cloid_motion 만 가진 시연 개체 대역(호출 순서 기록)."""

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
        _tf_buffer=None,
        _map_frame="map",
        _robot_base_frame="base_link",
    )


def _demo_skill(skills):
    from robo_claw_agent.skills.tidy_home_skill import TidyHomeSkill

    skill = TidyHomeSkill()
    skill.set_node(_demo_node(skills))
    skill.send_user_message = lambda _msg: None
    skill.get_map_pose = lambda: _pose(0.0)
    return skill


def test_tidy_home_turns_to_absolute_heading_before_motion(monkeypatch):
    from robo_claw_agent.skills import tidy_home_skill as tidy_module

    order = []
    monkeypatch.setattr(
        tidy_module,
        "apply_before_demo",
        lambda _node, _observer, **_kw: order.append(("turn", 64.2)) or (True, "", None),
    )
    monkeypatch.setattr(
        tidy_module,
        "stop_after_demo",
        lambda *_a, **_kw: order.append("stop") or {"status": "sent"},
    )
    monkeypatch.setattr(
        tidy_module,
        "emit_demo_signal",
        lambda *_a, **_kw: order.append("cue") or {"status": "sent"},
    )
    skills = _DemoSkills()
    skill = _demo_skill(skills)
    original_execute = skills.execute

    def _spy(name, params, **kwargs):
        if name == "execute_cloid_motion":
            order.append("motion")
        return original_execute(name, params, **kwargs)

    skills.execute = _spy

    assert skill._run_local_demo_motion("신발장", None) == "demo_motion_executed"
    assert order == [("turn", 64.2), "motion", "stop", "cue"]


def test_tidy_home_blocks_motion_when_heading_turn_fails(monkeypatch):
    from robo_claw_agent.skills import tidy_home_skill as tidy_module

    monkeypatch.setattr(
        tidy_module, "apply_before_demo", lambda *_a, **_kw: (False, "spin_timeout", None)
    )
    skills = _DemoSkills()
    skill = _demo_skill(skills)

    assert skill._run_local_demo_motion("신발장", None) == "demo_turn_failed"
    assert skills.executed == []


def test_cleanup_demo_turns_to_absolute_heading_before_motion(monkeypatch):
    from robo_claw_agent.skills.cleanup import demo_skill as demo_module
    from robo_claw_agent.skills.cleanup.demo_skill import CleanupDemoSkill
    from robo_claw_agent.skills.tidy_home_skill import TidyHomeSkill

    monkeypatch.setattr(
        TidyHomeSkill, "_collect_places", lambda _s, _scope, name: ([{"name": name}], None)
    )
    monkeypatch.setattr(TidyHomeSkill, "_visit_place", lambda _s, _name: (True, "arrived"))
    monkeypatch.setattr(demo_module, "claim_autonomous", lambda _event: True)
    monkeypatch.setattr(demo_module, "release_autonomous", lambda: None)
    order = []
    monkeypatch.setattr(
        demo_module,
        "apply_before_demo",
        lambda _node, _observer, **_kw: order.append(("turn", 64.2)) or (True, "", None),
    )
    monkeypatch.setattr(
        demo_module,
        "stop_after_demo",
        lambda *_a, **_kw: order.append("stop") or {"status": "sent"},
    )
    monkeypatch.setattr(
        demo_module,
        "emit_demo_signal",
        lambda *_a, **_kw: order.append("cue") or {"status": "sent", "bytes": 9},
    )
    skills = _DemoSkills()
    skill = CleanupDemoSkill()
    skill.set_node(_demo_node(skills))
    skill.send_user_message = lambda _msg: None
    skill.get_map_pose = lambda: _pose(0.0)
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
            "request_id": "req-heading",
        }
    )

    assert result["success"] is True
    assert order == [("turn", 64.2), "motion", "stop", "cue"]


def test_cleanup_demo_blocks_motion_when_heading_turn_fails(monkeypatch):
    from robo_claw_agent.skills.cleanup import demo_skill as demo_module
    from robo_claw_agent.skills.cleanup.demo_skill import CleanupDemoSkill
    from robo_claw_agent.skills.tidy_home_skill import TidyHomeSkill

    monkeypatch.setattr(
        TidyHomeSkill, "_collect_places", lambda _s, _scope, name: ([{"name": name}], None)
    )
    monkeypatch.setattr(TidyHomeSkill, "_visit_place", lambda _s, _name: (True, "arrived"))
    monkeypatch.setattr(demo_module, "claim_autonomous", lambda _event: True)
    monkeypatch.setattr(demo_module, "release_autonomous", lambda: None)
    monkeypatch.setattr(
        demo_module, "apply_before_demo", lambda *_a, **_kw: (False, "spin_timeout", None)
    )
    skills = _DemoSkills()
    skill = CleanupDemoSkill()
    skill.set_node(_demo_node(skills))
    skill.send_user_message = lambda _msg: None
    skill.get_map_pose = lambda: _pose(0.0)

    result = skill.execute(
        {
            "target_location": "신발장",
            "target_object": "신발",
            "scene_summary": "신발장 앞 신발",
            "request_id": "req-heading-fail",
        }
    )

    assert result["success"] is False
    assert result["failure_reason"] == "demo_turn_failed"
    assert skills.executed == []
