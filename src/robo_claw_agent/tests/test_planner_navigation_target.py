import importlib.util
from pathlib import Path


_PLANNER_PATH = (
    Path(__file__).resolve().parents[1]
    / "robo_claw_agent"
    / "agent_node"
    / "planner.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "robo_claw_agent.agent_node.planner", _PLANNER_PATH
)
_PLANNER = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_PLANNER)


class _Logger:
    def info(self, msg):
        assert isinstance(msg, str)
        pass


def test_preserve_user_navigation_target_when_llm_translates_place_name():
    chain = [{"skill": "navigate_to", "params": {"target_name": "거실"}}]

    result = _PLANNER._preserve_user_navigation_target(
        chain, "메인룸으로 이동해줘", _Logger()
    )

    assert result == [{"skill": "navigate_to", "params": {"target_name": "메인룸"}}]


def test_keep_navigation_target_when_it_matches_instruction():
    chain = [{"skill": "navigate_to", "params": {"target_name": "메인룸"}}]

    result = _PLANNER._preserve_user_navigation_target(
        chain, "메인룸으로 이동해줘", _Logger()
    )

    assert result == chain


# stow 선행 보장 로직은 agent_node/nav_safety.py 로 이동했다.
# 해당 테스트는 test_nav_safety.py 참조.


def test_extract_target_strips_leading_filler_words():
    """re.search 최좌측 매칭으로 호격/부사가 목적지에 딸려오면 안 된다."""
    extract = _PLANNER._extract_requested_navigation_target

    assert extract("로봇아 주방으로 가줘") == "주방"
    assert extract("야 지금 거실로 이동해") == "거실"
    assert extract("주방으로 이동해줘") == "주방"


def test_extract_target_keeps_multi_word_place_name():
    assert (
        _PLANNER._extract_requested_navigation_target("충전 스테이션까지 이동해줘")
        == "충전 스테이션"
    )


def test_extract_target_gives_up_on_over_capture():
    """어절이 과도하게 남으면 LLM 값을 덮어쓰지 않도록 빈 문자열을 반환한다."""
    assert (
        _PLANNER._extract_requested_navigation_target(
            "아까 얘기했던 그 커다란 창고 옆 방으로 이동해줘"
        )
        == ""
    )


def test_relative_move_instruction_has_no_navigation_target():
    assert _PLANNER._extract_requested_navigation_target("앞으로 2미터 이동해줘") == ""


def test_over_captured_target_does_not_overwrite_llm_value():
    chain = [{"skill": "navigate_to", "params": {"target_name": "창고"}}]

    result = _PLANNER._preserve_user_navigation_target(
        chain, "아까 얘기했던 그 커다란 창고 옆 방으로 이동해줘", _Logger()
    )

    assert result == chain
