from types import SimpleNamespace

import pytest
from robo_claw_agent.manipulation_runtime import ManipulationError
from robo_claw_agent.skills.manipulation_skill import (
    AdaptivePickObjectSkill,
    FixedPatternGraspSkill,
    PickFromRightSideZoneSkill,
    VLABasedPickFrontObjectSkill,
    VLABasedPickGripperObjectSkill,
)
from robo_claw_agent.skills.manipulation_skill.sequence import (
    grasp_verification,
    joint_state,
    recovery,
)


class DummyParameter:
    def __init__(self, value):
        self.value = value


class FakeBackend:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on or set()

    def move_to_named_pose(self, group_name, target_name):
        self.calls.append(("named", group_name, target_name))
        if f"named:{target_name}" in self.fail_on:
            raise ManipulationError(f"Failed to move to named pose: {target_name}")

    def move_to_joint_target(self, group_name, joint_values):
        self.calls.append(("joint", group_name, dict(joint_values)))
        if "joint" in self.fail_on:
            raise ManipulationError("Failed joint target")
        if "lift_only" in self.fail_on and list(joint_values.keys()) == ["joint_lift"]:
            raise ManipulationError("Failed lift target")
        for k in joint_values:
            if f"joint:{k}" in self.fail_on:
                raise ManipulationError(f"Failed joint target {k}")

    def move_to_pose_target(self, group_name, pose, end_effector_link="", cartesian=False):
        self.calls.append(("pose", group_name, pose))
        if "pose" in self.fail_on:
            raise ManipulationError("Failed pose target")

    def execute_gripper_preset(self, preset_name, group_name=None):
        self.calls.append(("gripper", group_name, preset_name))
        if f"gripper:{preset_name}" in self.fail_on:
            raise ManipulationError(f"Failed gripper preset: {preset_name}")


@pytest.fixture(autouse=True)
def mock_joint_state(monkeypatch):
    monkeypatch.setattr(
        joint_state,
        "_joint_positions",
        lambda skill, names: {name: 0.1 for name in names},
    )


def _make_stretch_node(params=None, backend=None):
    values = {
        "manipulation_backend": "stretch",
        "manipulation_gripper_presets_json": (
            '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
        ),
        "manipulation_named_poses_json": (
            '{"stow":{"joint_lift":0.2,"wrist_extension":0.0},'
            '"right_side_pick_ready":{"joint_lift":0.3,"wrist_extension":0.05},'
            '"right_side_pick_reach":{"joint_lift":0.3,"wrist_extension":0.3},'
            '"right_side_pick_lift":{"joint_lift":0.5},'
            '"carry":{"wrist_extension":0.05}}'
        ),
    }
    if params:
        values.update(params)
    node = SimpleNamespace(
        has_parameter=lambda name: name in values,
        get_parameter=lambda name: DummyParameter(values[name]),
        create_subscription=lambda *args, **kwargs: SimpleNamespace(destroy=lambda: None),
        destroy_subscription=lambda *args, **kwargs: None,
    )
    if backend is not None:
        node._manipulation_backend = backend
    return node


def _make_moveit_node(params=None):
    values = {"manipulation_backend": "moveit"}
    if params:
        values.update(params)
    backend = FakeBackend()
    node = SimpleNamespace(
        has_parameter=lambda name: name in values,
        get_parameter=lambda name: DummyParameter(values[name]),
    )
    node._manipulation_backend = backend
    return node


@pytest.mark.unit
def test_fixed_pattern_grasp_backend_mismatch_fails_closed():
    node = _make_moveit_node()
    skill = FixedPatternGraspSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup"})
    assert result["success"] is False
    assert result.get("failed_stage") == "backend_check"
    assert result.get("requires_recovery") is False


@pytest.mark.unit
def test_fixed_pattern_grasp_approach_failure_recovers_to_stow(monkeypatch):
    backend = FakeBackend(fail_on={"joint:wrist_extension"})
    node = _make_stretch_node(backend=backend)

    monkeypatch.setattr(
        grasp_verification,
        "_joint_positions",
        lambda skill, names: {
            name: (0.03 if name == "gripper_aperture" else 0.4) for name in names
        },
    )

    skill = FixedPatternGraspSkill()
    skill.set_node(node)
    result = skill.execute(
        {"target_object": "cup", "object_base_xyz": {"x": 0.0, "y": -0.6, "z": 0.12}}
    )

    assert result["success"] is False
    assert result["failed_stage"] == "approach"
    assert result["partial_execution"] is True
    assert result["recovery_attempted"] is True
    assert result["recovery_success"] is True
    assert result["requires_recovery"] is False
    assert any(call[0] == "named" and call[2] == "stow" for call in backend.calls)


@pytest.mark.unit
def test_fixed_pattern_grasp_approach_failure_recovery_fails(monkeypatch):
    backend = FakeBackend(fail_on={"joint:wrist_extension", "named:stow"})
    node = _make_stretch_node(backend=backend)

    monkeypatch.setattr(
        grasp_verification,
        "_joint_positions",
        lambda skill, names: {
            name: (0.03 if name == "gripper_aperture" else 0.4) for name in names
        },
    )

    skill = FixedPatternGraspSkill()
    skill.set_node(node)
    result = skill.execute(
        {"target_object": "cup", "object_base_xyz": {"x": 0.0, "y": -0.6, "z": 0.12}}
    )

    assert result["success"] is False
    assert result["recovery_attempted"] is True
    assert result["recovery_success"] is False
    assert result["requires_recovery"] is True


@pytest.mark.unit
def test_fixed_pattern_grasp_verification_none_forbids_lift_and_recovers(monkeypatch):
    backend = FakeBackend()
    node = _make_stretch_node(backend=backend)

    # Return None for aperture reading -> unverified grasp
    monkeypatch.setattr(
        grasp_verification,
        "_joint_positions",
        lambda skill, names: {name: None for name in names},
    )

    skill = FixedPatternGraspSkill()
    skill.set_node(node)
    result = skill.execute(
        {"target_object": "cup", "object_base_xyz": {"x": 0.0, "y": -0.6, "z": 0.12}}
    )

    assert result["success"] is False
    assert result["failed_stage"] == "grasp_verification"
    assert result["grasp_verified"] is None
    # Ensure lift and carry were NOT called
    for call in backend.calls:
        if call[0] == "joint" and "joint_lift" in call[2] and call[2]["joint_lift"] > 0.5:
            pytest.fail("Lift should not be called when grasp verification is None")
        if call[0] == "named" and call[2] == "carry":
            pytest.fail("Carry should not be called when grasp verification is None")
    # Recovery to stow should be executed
    assert any(call[0] == "named" and call[2] == "stow" for call in backend.calls)


@pytest.mark.unit
def test_fixed_pattern_grasp_lift_failure_forbids_carry_and_recovers(monkeypatch):
    backend = FakeBackend(fail_on={"lift_only"})
    node = _make_stretch_node(backend=backend)

    # Return valid aperture so grasp is verified, but lift will fail
    monkeypatch.setattr(
        grasp_verification,
        "_joint_positions",
        lambda skill, names: {
            name: (0.03 if name == "gripper_aperture" else 0.4) for name in names
        },
    )

    skill = FixedPatternGraspSkill()
    skill.set_node(node)
    result = skill.execute(
        {
            "target_object": "cup",
            "object_base_xyz": {"x": 0.0, "y": -0.6, "z": 0.12},
            "skip_carry": False,
        }
    )

    assert result["success"] is False
    assert result["failed_stage"] == "lift"
    # Ensure carry was NOT called
    for call in backend.calls:
        if call[0] == "named" and call[2] == "carry":
            pytest.fail("Carry should not be called when lift fails")
    # Recovery stow should be called
    assert any(call[0] == "named" and call[2] == "stow" for call in backend.calls)



@pytest.mark.unit
def test_vla_pick_front_object_verification_none_fails_closed(monkeypatch):
    backend = FakeBackend()
    node = _make_stretch_node(backend=backend)

    monkeypatch.setattr(
        grasp_verification,
        "_joint_positions",
        lambda skill, names: {name: None for name in names},
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.manipulation_skill.sequence.vla_pick_skills.ServoGripperToObjectSkill.execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.manipulation_skill.sequence.vla_pick_skills.AlignRightArmToFrontSkill.execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.manipulation_skill.StowForNavigationSkill.execute",
        lambda self, params: {"success": True},
    )

    skill = VLABasedPickFrontObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "skip_carry": False})

    assert result["success"] is False
    assert result["failed_stage"] == "grasp_verification"
    assert result["grasp_verified"] is None
    # Ensure carry was NOT called
    for call in backend.calls:
        if call[0] == "named" and call[2] == "carry":
            pytest.fail("Carry should not be called when grasp verification is None")


@pytest.mark.unit
def test_vla_pick_gripper_object_verification_none_fails_closed(monkeypatch):
    backend = FakeBackend()
    node = _make_stretch_node(backend=backend)

    monkeypatch.setattr(
        grasp_verification,
        "_joint_positions",
        lambda skill, names: {name: None for name in names},
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.manipulation_skill.sequence.vla_pick_skills.ServoGripperToObjectSkill.execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )

    skill = VLABasedPickGripperObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "skip_carry": False})

    assert result["success"] is False
    assert result["failed_stage"] == "grasp_verification"
    assert result["grasp_verified"] is None


@pytest.mark.unit
def test_pick_from_right_side_zone_verification_none_fails_closed(monkeypatch):
    backend = FakeBackend()
    node = _make_stretch_node(backend=backend)

    monkeypatch.setattr(
        grasp_verification,
        "_joint_positions",
        lambda skill, names: {name: None for name in names},
    )

    skill = PickFromRightSideZoneSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "skip_carry": False})

    assert result["success"] is False
    assert result["failed_stage"] == "grasp_verification"
    assert result["grasp_verified"] is None


@pytest.mark.unit
def test_adaptive_pick_cleans_up_state_between_retries(monkeypatch):
    cleanup_calls = []
    attempt_count = 0

    def fake_cleanup(skill, runtime, **kwargs):
        cleanup_calls.append("cleanup")
        return True, "cleaned up"

    monkeypatch.setattr(recovery, "cleanup_attempt_state", fake_cleanup)
    monkeypatch.setattr(
        "robo_claw_agent.skills.manipulation_skill.sequence.adaptive_pick_skill.cleanup_attempt_state",
        fake_cleanup,
    )

    def fake_search(self, params):
        nonlocal attempt_count
        attempt_count += 1
        return {"success": True, "align_angle_deg": 0.0, "body_rotation_applied_deg": 0.0}

    monkeypatch.setattr(
        "robo_claw_agent.skills.manipulation_skill.sequence.adaptive_pick_skill.SearchObjectSkill.execute",
        fake_search,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.manipulation_skill.sequence.adaptive_pick_skill.PrepareRightSidePickSkill.execute",
        lambda self, params: {"success": True},
    )

    # First attempt fails at grasp, second attempt succeeds
    pick_results = iter([{"success": False, "message": "grasp failed"}, {"success": True}])
    monkeypatch.setattr(
        "robo_claw_agent.skills.manipulation_skill.sequence.adaptive_pick_skill.FixedPatternGraspSkill.execute",
        lambda self, params: next(pick_results),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.manipulation_skill.HeadPanTiltSkill.execute",
        lambda self, params: {"success": True},
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_stretch_node(backend=FakeBackend()))
    result = skill.execute({"target_object": "cup", "max_attempts": 2})

    assert result["success"] is True
    assert attempt_count == 2
    assert len(cleanup_calls) == 1  # cleaned up between attempt 1 and 2


@pytest.mark.unit
def test_adaptive_pick_deadline_budget_passed_to_children(monkeypatch):
    child_timeouts = []

    def fake_search(self, params):
        child_timeouts.append(params.get("timeout_sec"))
        return {"success": True, "align_angle_deg": 0.0, "body_rotation_applied_deg": 0.0}

    monkeypatch.setattr(
        "robo_claw_agent.skills.manipulation_skill.sequence.adaptive_pick_skill.SearchObjectSkill.execute",
        fake_search,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.manipulation_skill.sequence.adaptive_pick_skill.PrepareRightSidePickSkill.execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.manipulation_skill.sequence.adaptive_pick_skill.FixedPatternGraspSkill.execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.manipulation_skill.HeadPanTiltSkill.execute",
        lambda self, params: {"success": True},
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_stretch_node(backend=FakeBackend()))
    result = skill.execute({"target_object": "cup", "timeout_sec": 45.0})

    assert result["success"] is True
    assert len(child_timeouts) == 1
    assert child_timeouts[0] is not None
    assert 40.0 <= child_timeouts[0] <= 45.0


# ── Tier 4: 능동 탐색의 뷰 재시도 / 접근 재스윕 ─────────────────────────────

def _search_node(monkeypatch):
    import robo_claw_agent.skills.manipulation_skill.sequence.search_skill as search_skill

    monkeypatch.setattr(
        search_skill,
        "_get_runtime",
        lambda node: SimpleNamespace(config=SimpleNamespace(backend="stretch")),
    )
    monkeypatch.setattr(
        search_skill.HeadPanTiltSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        search_skill.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    return search_skill


def test_search_retries_view_then_succeeds(monkeypatch):
    from robo_claw_agent.skills.manipulation_skill import SearchObjectSkill

    search_skill = _search_node(monkeypatch)
    calls = {"n": 0}

    def fake_localize(node, params, target):
        calls["n"] += 1
        if calls["n"] < 2:
            return None, {"success": False, "message": "miss"}
        return 0.1, {"success": True, "bearing_offset_deg": 0.0}

    monkeypatch.setattr(search_skill, "_localize_rotation_with_base_camera", fake_localize)

    skill = SearchObjectSkill()
    skill.set_node(SimpleNamespace())
    result = skill.execute(
        {"target_object": "cup", "max_body_rotations": 0, "use_head_camera": False}
    )

    assert result["success"] is True
    assert calls["n"] >= 2  # 같은 뷰에서 2회째에 성공


def test_search_approaches_and_rescans_after_failure(monkeypatch):
    from robo_claw_agent.skills.manipulation_skill import SearchObjectSkill

    search_skill = _search_node(monkeypatch)
    monkeypatch.setattr(
        search_skill,
        "_localize_rotation_with_base_camera",
        lambda node, params, target: (None, {"success": False, "message": "miss"}),
    )
    moves = []

    def fake_move(self, params):
        moves.append(params)
        return {"success": True}

    monkeypatch.setattr(search_skill.MoveRelativeSkill, "execute", fake_move)

    skill = SearchObjectSkill()
    skill.set_node(SimpleNamespace())
    result = skill.execute(
        {
            "target_object": "cup",
            "max_body_rotations": 0,
            "use_head_camera": False,
            "approach_search_distance_m": 0.5,
            "max_approach_scans": 1,
        }
    )

    assert result["success"] is False
    assert result.get("approach_scans", 0) == 1
    assert len(moves) == 1
    assert moves[0]["forward"] == pytest.approx(0.5)


def test_search_skips_approach_when_distance_zero(monkeypatch):
    from robo_claw_agent.skills.manipulation_skill import SearchObjectSkill

    search_skill = _search_node(monkeypatch)
    monkeypatch.setattr(
        search_skill,
        "_localize_rotation_with_base_camera",
        lambda node, params, target: (None, {"success": False, "message": "miss"}),
    )
    moves = []

    def fake_move(self, params):
        moves.append(params)
        return {"success": True}

    monkeypatch.setattr(search_skill.MoveRelativeSkill, "execute", fake_move)

    skill = SearchObjectSkill()
    skill.set_node(SimpleNamespace())
    result = skill.execute(
        {
            "target_object": "cup",
            "max_body_rotations": 0,
            "use_head_camera": False,
            "approach_search_distance_m": 0.0,
        }
    )

    assert result["success"] is False
    assert result.get("approach_scans", 0) == 0
    assert len(moves) == 0
