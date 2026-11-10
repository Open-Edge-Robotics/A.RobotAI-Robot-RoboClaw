"""Former0045 + AgileX Nero(7-DOF) 팔 프로필의 named pose(프리셋) 데이터 검증.

``former_arm_config.yaml`` 의 ``manipulation_named_poses_json`` 은 LLM 이
``arm_pose(pose_name=...)`` 로 그대로 호출하는 실기 자세 값이다. 값이 조인트 한계를
벗어나거나 조인트가 빠지면 드라이버가 조용히 목표를 무시하고, 스킬은 "이동 완료" 로
보고할 수 있다. 이 테스트는 프리셋과 하드 한계를 함께 고정한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
_CONFIG = _CONFIG_DIR / "former_arm_config.yaml"
_LIMITS = _CONFIG_DIR / "ROBOT_LIMITS.former_arm.json"

_ARM_JOINTS = tuple(f"joint{i}" for i in range(1, 8))
_REQUIRED_POSES = (
    "home",
    "ready",
    "carry",
    "stow",
    "fold",
    "table",
    "reach",
    "side_left",
    "side_right",
    "wrist_down",
    "wrist_up",
    "roll_ccw",
    "roll_cw",
)


def _agent_params() -> dict:
    data = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))
    return data["robo_claw_agent_node"]["ros__parameters"]


def _named_poses() -> dict:
    return json.loads(_agent_params()["manipulation_named_poses_json"])


def _joint_limits() -> dict[str, list[float]]:
    return json.loads(_LIMITS.read_text(encoding="utf-8"))["manipulation"]["joint_limits"]


def test_arm_profile_enables_manipulation_with_agx_backend() -> None:
    params = _agent_params()
    assert params["manipulation_enabled"] is True
    assert params["manipulation_backend"] == "agx"
    assert params["manipulation_agx_arm_status_topic"] == "/feedback/arm_status"


def test_named_poses_define_every_required_preset() -> None:
    poses = _named_poses()
    missing = [name for name in _REQUIRED_POSES if name not in poses]
    assert missing == []


def test_home_uses_driver_home_service_not_joint_values() -> None:
    # 드라이버 /move_home 은 move_j([0]*7) 이며 접은 자세가 아니다.
    # AGX 백엔드는 조인트 맵이 없는 이름을 받으면 home 만 서비스로 처리한다.
    assert _named_poses()["home"] == "home"


def test_every_preset_covers_all_arm_joints() -> None:
    for name, value in _named_poses().items():
        if isinstance(value, str):
            continue
        assert tuple(value) == _ARM_JOINTS, f"{name}: {tuple(value)}"


def test_stow_uses_the_front_fold_pose() -> None:
    # 보관 자세: 로봇 앞쪽(TCP x<0)으로 접는다. fold 는 stow 의 별칭이다.
    poses = _named_poses()
    assert poses["stow"] == poses["fold"]
    assert poses["stow"]["joint2"] == 0.0
    assert poses["stow"]["joint4"] == 1.726


def test_presets_use_front_side_arm_frame() -> None:
    """arm base frame 의 +x 가 로봇 뒤쪽이므로, 앞쪽 자세는 joint2 > 0 이어야 한다.

    joint2 < 0 자세는 상완이 로봇 뒤로 넘어가 몸체와 충돌한다(실기 확인).
    """
    for name, value in _named_poses().items():
        if isinstance(value, str):
            continue
        assert value["joint2"] >= 0.0, f"{name}: joint2={value['joint2']} (뒤쪽 자세)"


def test_every_preset_stays_inside_robot_limits() -> None:
    limits = _joint_limits()
    for name, value in _named_poses().items():
        if isinstance(value, str):
            continue
        for joint, target in value.items():
            low, high = limits[joint]
            assert low <= target <= high, f"{name}.{joint}={target} outside {low}..{high}"


def test_joint_limits_match_sdk_nero_ranges() -> None:
    # pyAgxArm SDK ROBOT_JOINT_LIMIT_PRESET_RAD["nero"] 과 같은 값이어야 한다.
    limits = _joint_limits()
    assert limits["joint2"] == [-1.74533, 1.74533]
    assert limits["joint4"] == [-1.012291, 2.146755]
    assert limits["joint6"] == [-0.733039, 0.872665]
    assert limits["joint7"] == [-1.570797, 1.570797]
