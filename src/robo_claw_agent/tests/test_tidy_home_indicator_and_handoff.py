"""tidy home indicator and handoff"""

import pytest

pytestmark = pytest.mark.unit

from _tidy_home_helpers import (  # noqa: F401
    TidyHomeSkill,
    _motion,
    _place,
    _select_cleanup_demo_motion,
    _select_cloid_indicator_motion,
    random,
    tidy_home_module,
)


def test_cleanup_demo_allows_operator_allowlisted_wipe_ids_without_prerequisite():
    motions = [_motion(59, "wipe_1"), _motion(60, "wipe_2")]

    selected_59 = _select_cleanup_demo_motion(motions, [59], rng=random.Random(1))
    selected_60 = _select_cleanup_demo_motion(motions, [60], rng=random.Random(1))

    assert selected_59 is not None and selected_59["motion_id"] == 59
    assert selected_60 is not None and selected_60["motion_id"] == 60
def test_cleanup_demo_still_rejects_motion_with_catalog_prerequisite():
    motions = [_motion(60, "wipe_2", pre_id=59)]

    assert _select_cleanup_demo_motion(motions, [60]) is None
def test_cloid_indicator_avoids_immediate_repeat_when_an_alternative_exists():
    motions = [
        _motion(22, "scan"),
        _motion(128, "task ready pose"),
    ]

    selected = _select_cloid_indicator_motion(
        motions, [22, 128], rng=random.Random(1), previous_motion_id=22
    )

    assert selected["motion_id"] == 128
def test_cloid_indicator_returns_none_when_allowlist_has_no_current_safe_motion():
    motions = [_motion(60, "wipe_2", pre_id=59)]

    assert _select_cloid_indicator_motion(motions, [60], rng=random.Random(0)) is None
def test_home_cleanup_visits_each_remembered_place_once_and_stops_on_cancel(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import globals as autonomous_globals

    skill = TidyHomeSkill()
    skill.node = object()
    messages = []
    visits = []
    monkeypatch.setattr(skill, "send_user_message", messages.append)
    monkeypatch.setattr(
        skill,
        "_analyze_current_scene",
        lambda _place: {
            "success": True,
            "analysis": "정리할 대상이 보이지 않습니다.",
            "detected_objects": [],
        },
    )
    monkeypatch.setattr(skill, "_cloid_indicator_enabled", lambda: False)
    original_event = autonomous_globals._AUTONOMOUS_ACTIVE
    original_event.set()

    def visit(place_name):
        visits.append(place_name)
        if place_name == "주방":
            original_event.clear()
        return True, "도착했습니다."

    monkeypatch.setattr(skill, "_visit_place", visit)
    skill._run_cleanup(
        "home",
        [
            _place("주방", 1, 2),
            _place("거실", 3, 4),
        ],
    )

    assert visits == ["주방"]
    assert any("정리 작업이 종료됐습니다" in message for message in messages)
    assert any("완료 여부를 확정할 수 없습니다" in message for message in messages)
    assert not original_event.is_set()
def test_cleanup_demo_handoff_stages_requester_and_skips_local_pick_place_guard(monkeypatch):
    import threading
    from types import SimpleNamespace

    from robo_claw_agent.skills.autonomous_skill import core, decision_nodes, execution_nodes
    from robo_claw_agent.skills.autonomous_skill import globals as autonomous_globals

    calls = []

    class FakeSkills:
        def has_skill(self, name):
            return name in {"move_relative", "call_peer_robot"}

        def execute(self, name, params, **_kwargs):
            calls.append((name, params))
            return SimpleNamespace(success=True, message="accepted")

    node = SimpleNamespace(_memory=None, _skills=FakeSkills())
    skill = TidyHomeSkill()
    skill.node = node
    skill.send_user_message = lambda _message: None
    active = threading.Event()
    active.set()
    monkeypatch.setattr(autonomous_globals, "_AUTONOMOUS_ACTIVE", active)

    def prepare_handoff(handoff):
        handoff._blackboard.update(
            {
                "cleanup_handoff_prepared": True,
                "cleanup_handoff_kind": "cleanup_demo",
                "decided_skill": "call_peer_robot",
                "decided_params": {"peer_name": "cloid1", "instruction": "demo request"},
            }
        )
        return core.NodeStatus.SUCCESS

    executed_skills = []

    def execute_peer(execute):
        executed_skills.append(execute._blackboard["decided_skill"])
        execute._blackboard["last_action_success"] = True
        return core.NodeStatus.SUCCESS

    monkeypatch.setattr(execution_nodes.ResolveCleanupHandoff, "tick", prepare_handoff)
    monkeypatch.setattr(
        decision_nodes.ValidateDecidedAction, "tick", lambda _self: core.NodeStatus.SUCCESS
    )
    monkeypatch.setattr(execution_nodes.ExecuteDecidedAction, "tick", execute_peer)

    status = skill._handle_cleanup_target(
        place_name="안방",
        scene="바닥에 포장지가 있습니다.",
        objects=[{"name": "포장지"}],
        cycle=1,
    )

    assert status == "delegated_demo"
    assert [name for name, _params in calls] == ["move_relative"]
    assert executed_skills == ["call_peer_robot"]
    assert calls[0][1]["forward"] == 0.0
    assert calls[0][1]["lateral"] > 0.0
def test_cleanup_demo_handoff_is_not_sent_if_requester_cannot_stage_safely(monkeypatch):
    import threading
    from types import SimpleNamespace

    from robo_claw_agent.skills.autonomous_skill import core, decision_nodes, execution_nodes
    from robo_claw_agent.skills.autonomous_skill import globals as autonomous_globals

    calls = []
    executed = []

    class FakeSkills:
        def has_skill(self, name):
            return name in {"move_relative", "call_peer_robot"}

        def execute(self, name, params, **_kwargs):
            calls.append((name, params))
            return SimpleNamespace(success=False, message="target blocked")

    skill = TidyHomeSkill()
    skill.node = SimpleNamespace(_memory=None, _skills=FakeSkills())
    skill.send_user_message = lambda _message: None
    active = threading.Event()
    active.set()
    monkeypatch.setattr(autonomous_globals, "_AUTONOMOUS_ACTIVE", active)

    def prepare_handoff(handoff):
        handoff._blackboard.update(
            {
                "cleanup_handoff_prepared": True,
                "cleanup_handoff_kind": "cleanup_demo",
                "decided_skill": "call_peer_robot",
                "decided_params": {"peer_name": "cloid1", "instruction": "demo request"},
            }
        )
        return core.NodeStatus.SUCCESS

    def execute_peer(execute):
        executed.append(execute._blackboard["decided_skill"])
        execute._blackboard["last_action_success"] = True
        return core.NodeStatus.SUCCESS

    monkeypatch.setattr(execution_nodes.ResolveCleanupHandoff, "tick", prepare_handoff)
    monkeypatch.setattr(
        decision_nodes.ValidateDecidedAction, "tick", lambda _self: core.NodeStatus.SUCCESS
    )
    monkeypatch.setattr(execution_nodes.ExecuteDecidedAction, "tick", execute_peer)

    status = skill._handle_cleanup_target(
        place_name="안방",
        scene="바닥에 포장지가 있습니다.",
        objects=[{"name": "포장지"}],
        cycle=1,
    )

    assert status == "handoff_staging_failed"
    assert [name for name, _params in calls] == ["move_relative"]
    assert executed == []
def test_cloid_activity_indicator_executes_one_allowlisted_motion_and_avoids_repeat():
    from types import SimpleNamespace

    skill = TidyHomeSkill()
    messages = []
    calls = []
    motions = [_motion(22, "scan"), _motion(128, "task ready pose")]

    class FakeSkills:
        def has_skill(self, name):
            return name in {"list_cloid_motions", "execute_cloid_motion"}

        def execute(self, name, params, **kwargs):
            calls.append((name, params, kwargs))
            if name == "list_cloid_motions":
                return SimpleNamespace(success=True, message="ok", result_data={"motions": motions})
            return SimpleNamespace(success=True, message="started")

    node = SimpleNamespace(
        _skills=FakeSkills(),
        get_parameter=lambda _name: SimpleNamespace(value="[22, 128]"),
    )
    skill.node = node
    skill.send_user_message = messages.append

    skill._run_cloid_activity_indicator()
    skill._run_cloid_activity_indicator()

    executed = [call for call in calls if call[0] == "execute_cloid_motion"]
    assert len(executed) == 2
    assert executed[0][1]["motion_id"] != executed[1][1]["motion_id"]
    assert all(call[1]["confirm"] is True for call in executed)
    assert all("실제 정리 완료는 확인되지 않았습니다" in message for message in messages)
def test_tidy_home_multiview_analysis_uses_only_confirmed_direction_observations(monkeypatch):
    calls = []

    class FakeObserver:
        def set_node(self, _node):
            pass

        def execute(self, params):
            calls.append(params)
            return {
                "success": True,
                "partial": True,
                "detected_objects": ["towel"],
                "per_direction": {
                    "정면": "바닥에 수건이 보입니다.",
                    "우측": "회전 실패 후 잘못 촬영된 이미지입니다.",
                    "후방": "정리 후보가 보이지 않습니다.",
                    "좌측": "VLM 분석 실패",
                },
                "per_direction_meta": {
                    "정면": {"rotate_ok": True, "image_received": True, "analysis_ok": True},
                    "우측": {"rotate_ok": False, "image_received": True, "analysis_ok": True},
                    "후방": {"rotate_ok": True, "image_received": True, "analysis_ok": True},
                    "좌측": {"rotate_ok": True, "image_received": True, "analysis_ok": False},
                },
                "return_to_start": {"success": True},
            }

    monkeypatch.setattr(
        tidy_home_module,
        "_create_surroundings_observer",
        lambda active_event: FakeObserver(),
        raising=False,
    )
    skill = TidyHomeSkill()
    skill.node = object()

    result = skill._analyze_current_scene("주방")

    assert result["success"] is True
    assert result["partial"] is True
    assert result["detected_objects"] == ["towel"]
    assert "정면 방향: 바닥에 수건이 보입니다." in result["analysis"]
    assert "후방 방향: 정리 후보가 보이지 않습니다." in result["analysis"]
    assert "회전 실패 후 잘못 촬영된 이미지" not in result["analysis"]
    assert "VLM 분석 실패" not in result["analysis"]
    assert calls[0]["capture_4way"] is True
    assert calls[0]["return_to_start"] is True
    assert "일반 옷" in calls[0]["observation_prompt"]
def test_tidy_home_multiview_analysis_fails_closed_without_valid_directions(monkeypatch):
    class FakeObserver:
        def set_node(self, _node):
            pass

        def execute(self, _params):
            return {
                "success": False,
                "message": "모든 방향에서 회전 또는 이미지 분석에 실패했습니다.",
                "per_direction": {"정면": "회전 실패"},
                "per_direction_meta": {
                    "정면": {"rotate_ok": False, "image_received": True, "analysis_ok": True}
                },
            }

    monkeypatch.setattr(
        tidy_home_module,
        "_create_surroundings_observer",
        lambda active_event: FakeObserver(),
        raising=False,
    )
    skill = TidyHomeSkill()
    skill.node = object()

    result = skill._analyze_current_scene("거실")

    assert result["success"] is False
    assert result["failure_reason"] == "surroundings_observation_failed"
    assert result["analysis"] == ""
