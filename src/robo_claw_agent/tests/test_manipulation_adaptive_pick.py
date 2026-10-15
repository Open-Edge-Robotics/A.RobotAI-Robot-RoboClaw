"""적응형 집기(adaptive_pick_object) 흐름 테스트."""

import pytest

pytestmark = pytest.mark.ros2
pytest.importorskip("rclpy")

from _manip_helpers import (  # noqa: F401
    AdaptivePickObjectSkill,
    HeadPanTiltSkill,
    _make_node,
    _stub_head_observation,
    _stub_joint_positions,
    adaptive_pick_skill,
    pytest,
    sequence,
)


def test_adaptive_pick_approach_vector_moves_toward_distant_object():
    vector = adaptive_pick_skill._approach_vector_to_reach((0.0, -2.0, 0.1))

    assert vector == pytest.approx({"forward": 0.0, "lateral": -1.58})


def test_adaptive_pick_approach_vector_skips_already_reachable_object():
    assert adaptive_pick_skill._approach_vector_to_reach((0.0, -0.42, 0.1)) is None

def test_adaptive_pick_object_full_flow_uses_search_result_to_align(monkeypatch):
    calls = []

    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: (
            calls.append("search")
            or {"success": True, "align_angle_deg": 45.0, "body_rotation_applied_deg": 90.0}
        ),
    )
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: calls.append(("align", params["angle_deg"])) or {"success": True},
    )
    monkeypatch.setattr(
        sequence.PrepareRightSidePickSkill,
        "execute",
        lambda self, params: calls.append("prepare") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: calls.append("observe") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.VLABasedPickGripperObjectSkill,
        "execute",
        lambda self, params: calls.append("pick") or {"success": True},
    )
    monkeypatch.setattr(
        HeadPanTiltSkill,
        "execute",
        lambda self, params: calls.append(("head", params.get("pose_name"))) or {"success": True},
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute(
        {"target_object": "cup", "use_fixed_pattern_grasp": False, "use_head_assist": False}
    )

    assert result["success"] is True
    assert calls == [
        "search",
        ("align", 45.0),
        "prepare",
        "observe",
        "pick",
        ("head", "travel_head"),
    ]


def test_adaptive_pick_object_skips_align_when_search_angle_negligible(monkeypatch):
    calls = []

    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: (
            calls.append("search")
            or {"success": True, "align_angle_deg": 0.0, "body_rotation_applied_deg": 0.0}
        ),
    )

    def fail_if_called(self, params):
        raise AssertionError("회전각이 0에 가까울 때는 align을 호출하면 안 됩니다")

    monkeypatch.setattr(sequence.AlignRightArmToFrontSkill, "execute", fail_if_called)
    monkeypatch.setattr(
        sequence.PrepareRightSidePickSkill,
        "execute",
        lambda self, params: calls.append("prepare") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: calls.append("observe") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.VLABasedPickGripperObjectSkill,
        "execute",
        lambda self, params: calls.append("pick") or {"success": True},
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"target_object": "cup", "use_fixed_pattern_grasp": False, "restore_head_after": False})

    assert result["success"] is True
    assert calls == ["search", "prepare", "observe", "pick"]


def test_adaptive_pick_object_can_skip_search(monkeypatch):
    calls = []

    def fail_if_called(self, params):
        raise AssertionError("skip_search=True면 SearchObjectSkill이 호출되면 안 됩니다")

    monkeypatch.setattr(sequence.SearchObjectSkill, "execute", fail_if_called)
    monkeypatch.setattr(
        sequence.PrepareRightSidePickSkill,
        "execute",
        lambda self, params: calls.append("prepare") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: calls.append("observe") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.VLABasedPickGripperObjectSkill,
        "execute",
        lambda self, params: calls.append("pick") or {"success": True},
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute(
        {"target_object": "cup", "skip_search": True, "use_fixed_pattern_grasp": False, "restore_head_after": False}
    )

    assert result["success"] is True
    assert calls == ["prepare", "observe", "pick"]


def test_adaptive_pick_object_fails_when_search_fails(monkeypatch):
    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: {"success": False, "message": "못 찾음"},
    )

    def fail_if_called(self, params):
        raise AssertionError("탐색 실패 시 다음 단계가 호출되면 안 됩니다")

    monkeypatch.setattr(sequence.PrepareRightSidePickSkill, "execute", fail_if_called)
    monkeypatch.setattr(sequence.VLABasedPickGripperObjectSkill, "execute", fail_if_called)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"target_object": "cup", "use_fixed_pattern_grasp": False, "restore_head_after": False})

    assert result["success"] is False
    assert "탐색 실패" in result["message"]


def test_adaptive_pick_object_fails_when_observe_fails_and_restores_heading(monkeypatch):
    calls = []

    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: {
            "success": True,
            "align_angle_deg": 45.0,
            "body_rotation_applied_deg": 0.0,
        },
    )
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: calls.append(("align", params["angle_deg"])) or {"success": True},
    )
    monkeypatch.setattr(
        sequence.PrepareRightSidePickSkill,
        "execute",
        lambda self, params: calls.append("prepare") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: calls.append("observe") or {"success": False, "message": "not found"},
    )

    def fail_if_called(self, params):
        raise AssertionError("그리퍼 확인 실패 시 집기를 시도하면 안 됩니다")

    monkeypatch.setattr(sequence.VLABasedPickGripperObjectSkill, "execute", fail_if_called)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute(
        {"target_object": "cup", "return_to_start_on_fail": True, "use_fixed_pattern_grasp": False, "restore_head_after": False}
    )

    assert result["success"] is False
    assert calls == [
        ("align", 45.0),
        "prepare",
        "observe",
        ("align", -45.0),
    ]


def test_adaptive_pick_object_restores_head_even_on_pick_failure(monkeypatch):
    calls = []

    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: {
            "success": True,
            "align_angle_deg": None,
            "body_rotation_applied_deg": 0.0,
        },
    )
    monkeypatch.setattr(
        sequence.PrepareRightSidePickSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.VLABasedPickGripperObjectSkill,
        "execute",
        lambda self, params: {"success": False, "message": "grasp failed"},
    )
    monkeypatch.setattr(
        HeadPanTiltSkill,
        "execute",
        lambda self, params: calls.append(("head", params.get("pose_name"))) or {"success": True},
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute(
        {"target_object": "cup", "use_fixed_pattern_grasp": False, "use_head_assist": False}
    )

    assert result["success"] is False
    assert "grasp failed" in result["message"]
    assert calls == [("head", "travel_head")]


def test_adaptive_pick_object_default_max_attempts_does_not_retry(monkeypatch):
    """max_attempts 미지정 시 기존과 동일하게 1회 실패로 즉시 종료해야 한다."""
    calls = []

    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: calls.append("search") or {"success": False, "message": "못 찾음"},
    )

    def fail_if_called(self, params):
        raise AssertionError("max_attempts 미지정 시 재시도가 발생하면 안 됩니다")

    monkeypatch.setattr(sequence.PrepareRightSidePickSkill, "execute", fail_if_called)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"target_object": "cup", "use_fixed_pattern_grasp": False, "restore_head_after": False})

    assert result["success"] is False
    assert calls == ["search"]
    # 프로덕션이 attempts 를 결과에 포함한다. 기본 max_attempts 는 재시도하지 않으므로 1 이다.
    assert result.get("attempts", 1) == 1
    assert not any(step.get("skill") == "retry_pending" for step in result["steps"])


def test_adaptive_pick_object_retries_full_sequence_after_pick_failure(monkeypatch):
    """max_attempts=2 이면 파지 실패 후 탐색부터 다시 시도하고, 2번째 시도가 성공하면 종료한다."""
    calls = []
    search_results = iter(
        [
            {"success": True, "align_angle_deg": None, "body_rotation_applied_deg": 0.0},
            {"success": True, "align_angle_deg": None, "body_rotation_applied_deg": 0.0},
        ]
    )
    pick_results = iter(
        [
            {"success": False, "message": "grasp failed once"},
            {"success": True},
        ]
    )

    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: calls.append("search") or next(search_results),
    )
    monkeypatch.setattr(
        sequence.PrepareRightSidePickSkill,
        "execute",
        lambda self, params: calls.append("prepare") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: calls.append("observe") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.VLABasedPickGripperObjectSkill,
        "execute",
        lambda self, params: calls.append("pick") or next(pick_results),
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute(
        {"target_object": "cup", "restore_head_after": False, "use_fixed_pattern_grasp": False, "max_attempts": 2}
    )

    assert result["success"] is True
    assert calls == [
        "search",
        "prepare",
        "observe",
        "pick",
        "search",
        "prepare",
        "observe",
        "pick",
    ]
    retry_markers = [s for s in result["steps"] if s.get("skill") == "retry_pending"]
    assert len(retry_markers) == 1
    assert "grasp failed once" in retry_markers[0]["message"]


def test_adaptive_pick_object_reports_attempts_when_all_retries_exhausted(monkeypatch):
    """max_attempts 만큼 모두 실패하면 attempts 필드에 정확한 시도 횟수를 남긴다."""
    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: {"success": False, "message": "never found"},
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute(
        {"target_object": "cup", "restore_head_after": False, "use_fixed_pattern_grasp": False, "max_attempts": 3}
    )

    assert result["success"] is False
    assert result["attempts"] == 3
    retry_markers = [s for s in result["steps"] if s.get("skill") == "retry_pending"]
    assert len(retry_markers) == 2
