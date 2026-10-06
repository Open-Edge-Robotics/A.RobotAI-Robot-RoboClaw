import importlib.util
from pathlib import Path

_DIRECT_SKILLS_PATH = (
    Path(__file__).resolve().parents[1]
    / "robo_claw_agent"
    / "agent_node"
    / "direct_skills.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "robo_claw_agent.agent_node.direct_skills", _DIRECT_SKILLS_PATH
)
_DIRECT_SKILLS = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_DIRECT_SKILLS)


def test_direct_relative_move_forward_meter():
    result = _DIRECT_SKILLS.direct_relative_move_skill(None, "앞으로 1미터 이동해줘")

    assert result == {
        "skill": "move_relative",
        "params": {"forward": 1.0},
        "background": False,
    }


def test_direct_relative_move_backward_and_lateral():
    back = _DIRECT_SKILLS.direct_relative_move_skill(None, "뒤로 0.5m 이동")
    right = _DIRECT_SKILLS.direct_relative_move_skill(None, "1미터 오른쪽으로 이동")

    assert back["params"] == {"forward": -0.5}
    assert right["params"] == {"forward": 0.0, "lateral": -1.0}


def test_direct_relative_move_ignores_named_destination():
    assert _DIRECT_SKILLS.direct_relative_move_skill(None, "메인룸으로 이동해줘") is None


def test_direct_relative_move_accepts_direction_verb_without_extra_verb():
    assert _DIRECT_SKILLS.direct_relative_move_skill(None, "3m 전진해")["params"] == {
        "forward": 3.0
    }


def test_direct_relative_move_accepts_direction_noun_form():
    result = _DIRECT_SKILLS.direct_relative_move_skill(None, "왼쪽 방향으로 1미터 이동")

    assert result["params"] == {"forward": 0.0, "lateral": 1.0}


def test_direct_relative_move_ignores_manipulation_request():
    """거리·방향이 목적어를 수식하는 조작 요청은 상대 이동이 아니다.

    이 경로는 LLM을 우회하므로, 오라우팅되면 파지 대신 베이스가 주행한다.
    """
    assert _DIRECT_SKILLS.direct_relative_move_skill(None, "2미터 앞에 있는 컵 잡아줘") is None
    assert (
        _DIRECT_SKILLS.direct_relative_move_skill(None, "1미터 앞 테이블 위 컵 집어줘") is None
    )


def test_direct_relative_move_ignores_non_move_reference():
    assert (
        _DIRECT_SKILLS.direct_relative_move_skill(None, "2미터 앞에 있는 물체 보여줘") is None
    )


def test_check_direct_skill_prefers_pick_over_relative_move():
    """거리 표현이 섞인 컵 집기 요청은 파지 스킬로 라우팅되어야 한다."""
    result = _DIRECT_SKILLS.check_direct_skill(None, "2미터 앞에 있는 컵 잡아줘")

    assert result["skill"] == "adaptive_pick_object"


def test_check_direct_skill_routes_plain_relative_move():
    result = _DIRECT_SKILLS.check_direct_skill(None, "앞으로 2미터 가줘")

    assert result["skill"] == "move_relative"
    assert result["params"] == {"forward": 2.0}


def test_direct_camera_capture_routes_plain_request():
    result = _DIRECT_SKILLS.direct_camera_capture_skill(None, "카메라 사진 보내줘")

    assert result["skill"] == "capture_camera_image"


def test_direct_camera_capture_ignores_requests_with_movement():
    """이동이 섞인 요청을 촬영 단독으로 축약하면 이동 절이 조용히 사라진다.

    맵 캡처(direct_map_capture_skill)에는 이미 있던 제외 조건이 카메라 쪽에만
    빠져 있어 "주방으로 이동해서 카메라 사진 보내줘"가 제자리에서 촬영만 하고
    끝나던 회귀를 고정한다.
    """
    for text in (
        "주방으로 이동해서 카메라 사진 보내줘",
        "회의실로 가서 카메라 이미지 캡처해줘",
        "충전소로 가라 그리고 카메라 사진 보내줘",
    ):
        assert _DIRECT_SKILLS.direct_camera_capture_skill(None, text) is None, text


def test_direct_camera_capture_does_not_steal_topic_or_multi_camera_queries():
    """질문/복수 카메라 요청을 단일 기본 카메라 캡처로 축약하지 않는다."""
    for text in (
        "카메라 이미지 관련 토픽 정리해줘",
        "카메라 토픽 정리해서 보여줘",
        "카메라가 여러대인데 각 카메라 이미지 보내줘",
        "각 카메라 화면을 모두 보여줘",
    ):
        assert _DIRECT_SKILLS.direct_camera_capture_skill(None, text) is None, text
