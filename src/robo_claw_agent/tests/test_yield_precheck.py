"""자리 양보 선확인 공용 헬퍼 테스트 (ROS2 불필요).

``navigate_to`` 내부에만 있던 선확인 규칙을 공용 모듈로 뺀 뒤, 동료 요청(자율협동
위임)이나 자율협동 장소 순회에서도 같은 규칙을 쓰는지 검증한다.
"""

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

from robo_claw_agent.skills.yield_precheck import (  # noqa: E402
    NAVIGATION_SKILLS,
    place_name_for_coordinates,
    place_name_from_move_params,
    precheck_yield_for_move,
    request_peer_yield,
    should_precheck_yield,
    yield_precheck_mode,
)


def test_navigation_skill_set_matches_the_nav_safety_gate():
    """선확인 대상 목록은 주행 안전 게이트의 베이스 이동 목록과 같아야 한다."""
    nav_safety = pytest.importorskip("robo_claw_agent.agent_node.nav_safety")
    assert set(NAVIGATION_SKILLS) == set(nav_safety.BASE_MOTION_SKILLS)


class _Skills:
    def __init__(self, registered=True):
        self.calls: list[tuple[str, dict]] = []
        self._registered = registered

    def has_skill(self, name):
        return self._registered and name == "request_yield_slot"

    def execute(self, name, params, timeout_sec=30.0):
        self.calls.append((name, dict(params)))
        return SimpleNamespace(success=True, message="비켜섰습니다.")


def _node(*, mode="ask", registered=True, objects=None):
    skills = _Skills(registered=registered)

    class _Node:
        _agent_id = "Former0047"
        _skills = skills
        _memory = SimpleNamespace(get_all_objects=lambda: list(objects or []))

        def has_parameter(self, name):
            return name == "yield_precheck_mode" and mode is not None

        def get_parameter(self, name):
            return SimpleNamespace(get_parameter_value=lambda: SimpleNamespace(string_value=mode))

    return _Node()


_PLACES = [
    {"name": "거실", "position": {"x": 2.0, "y": 3.0}, "metadata": {"kind": "place"}},
    {"name": "안방", "position": {"x": -4.0, "y": 1.0}, "metadata": {"kind": "place"}},
]


def test_mode_defaults_to_ask_and_follows_the_parameter():
    assert yield_precheck_mode(_node(mode="ask")) == "ask"
    assert yield_precheck_mode(_node(mode="off")) == "off"
    assert yield_precheck_mode(_node(mode="unsupported")) == "ask"
    assert yield_precheck_mode(_node(mode=None)) == "ask"
    assert yield_precheck_mode(None) == "off"


def test_should_precheck_requires_a_place_name():
    assert should_precheck_yield(_node(), "거실") is True
    assert should_precheck_yield(_node(), "   ") is False
    assert should_precheck_yield(_node(mode="off"), "거실") is False


def test_place_name_from_params_accepts_aliases():
    assert place_name_from_move_params({"target_name": " 거실 "}) == "거실"
    assert place_name_from_move_params({"destination": "안방"}) == "안방"
    assert place_name_from_move_params({"place": "안방"}) == "안방"
    assert place_name_from_move_params({"target": "안방"}) == "안방"
    assert place_name_from_move_params({"x": 1.0, "y": 2.0}) == ""


def test_place_name_for_coordinates_resolves_the_nearest_remembered_place():
    node = _node(objects=_PLACES)
    assert place_name_for_coordinates(node, 2.2, 3.1) == "거실"
    # 허용 반경(기본 1.0m) 밖이면 추측하지 않는다.
    assert place_name_for_coordinates(node, 20.0, 30.0) == ""
    assert place_name_for_coordinates(node, None, None) == ""
    assert place_name_for_coordinates(_node(), 2.0, 3.0) == ""


def test_request_peer_yield_is_skipped_when_the_skill_is_missing():
    node = _node(registered=False)
    assert request_peer_yield(node, place="거실") is False
    assert node._skills.calls == []


def test_request_peer_yield_is_fail_open_on_errors():
    node = _node()

    def _boom(name, params, timeout_sec=30.0):
        raise RuntimeError("service down")

    node._skills.execute = _boom  # type: ignore[method-assign]
    assert request_peer_yield(node, place="거실") is False


def test_navigate_to_with_a_place_name_leaves_the_precheck_to_the_skill():
    """navigate_to 는 같은 이름으로 직접 확인하므로 위임 경로에서 중복 요청하지 않는다."""
    node = _node()
    used = precheck_yield_for_move(node, "navigate_to", {"target_name": "거실"})
    assert used == ""
    assert node._skills.calls == []


def test_peer_move_with_coordinates_only_resolves_the_place_and_prechecks():
    node = _node(objects=_PLACES)
    used = precheck_yield_for_move(node, "navigate_to", {"x": 2.1, "y": 3.05})
    assert used == "거실"
    assert node._skills.calls == [("request_yield_slot", {"target_place": "거실"})]


def test_peer_move_with_unresolvable_coordinates_does_not_guess():
    node = _node(objects=_PLACES)
    assert precheck_yield_for_move(node, "navigate_to", {"x": 15.0, "y": 9.0}) == ""
    assert node._skills.calls == []


def test_peer_move_with_an_explicit_place_overrides_params():
    node = _node()
    used = precheck_yield_for_move(node, "navigate_to", {"x": 2.0, "y": 3.0}, place="안방")
    assert used == "안방"
    assert node._skills.calls == [("request_yield_slot", {"target_place": "안방"})]


def test_other_motion_skills_precheck_when_they_name_the_place():
    node = _node()
    used = precheck_yield_for_move(node, "move_relative", {"target_name": "안방"})
    assert used == "안방"
    assert node._skills.calls == [("request_yield_slot", {"target_place": "안방"})]


def test_non_motion_skills_never_precheck():
    node = _node()
    for skill in ("grasp", "analyze_scene", "get_status"):
        assert precheck_yield_for_move(node, skill, {"target_name": "거실"}) == ""
    assert node._skills.calls == []


def test_mode_off_disables_every_precheck_path():
    node = _node(mode="off", objects=_PLACES)
    assert precheck_yield_for_move(node, "navigate_to", {"x": 2.0, "y": 3.0}) == ""
    assert precheck_yield_for_move(node, "navigate_to", {"x": 2.0, "y": 3.0}, place="거실") == ""
    assert precheck_yield_for_move(node, "move_relative", {"target_name": "거실"}) == ""
    assert node._skills.calls == []
