import json
from typing import Any

from .exceptions import ManipulationConfigError
from .types import ManipulationConfig


def _unwrap_parameter_value(param: Any) -> Any:
    if param is None:
        return None

    if hasattr(param, "value"):
        return param.value

    if hasattr(param, "get_parameter_value"):
        value = param.get_parameter_value()
    else:
        value = param

    if hasattr(value, "string_array_value") and value.string_array_value:
        return list(value.string_array_value)
    if hasattr(value, "double_array_value") and value.double_array_value:
        return list(value.double_array_value)
    if hasattr(value, "integer_array_value") and value.integer_array_value:
        return list(value.integer_array_value)
    if hasattr(value, "bool_array_value") and value.bool_array_value:
        return list(value.bool_array_value)

    for attr in [
        "string_value",
        "double_value",
        "integer_value",
        "bool_value",
    ]:
        if hasattr(value, attr):
            return getattr(value, attr)

    return value


def _read_node_param(node: Any, name: str, default: Any) -> Any:
    if (
        node is None
        or not hasattr(node, "has_parameter")
        or not node.has_parameter(name)
    ):
        return default

    try:
        value = _unwrap_parameter_value(node.get_parameter(name))
    except Exception:
        return default

    return default if value is None else value


def _read_string(node: Any, name: str, default: str) -> str:
    value = _read_node_param(node, name, default)
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _read_bool(node: Any, name: str, default: bool) -> bool:
    value = _read_node_param(node, name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return bool(value)


def _read_float(node: Any, name: str, default: float) -> float:
    value = _read_node_param(node, name, default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ManipulationConfigError(
            f"파라미터 '{name}' 값을 숫자로 해석할 수 없습니다: {value!r}"
        ) from exc


def _parse_mapping_json(raw_value: Any, name: str) -> dict[str, Any]:
    if raw_value in (None, "", {}):
        return {}

    if isinstance(raw_value, dict):
        return dict(raw_value)

    if not isinstance(raw_value, str):
        raise ManipulationConfigError(
            f"파라미터 '{name}' 는 JSON 객체 문자열이어야 합니다."
        )

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ManipulationConfigError(
            f"파라미터 '{name}' JSON 파싱 실패: {exc}"
        ) from exc

    if not isinstance(parsed, dict):
        raise ManipulationConfigError(f"파라미터 '{name}' 는 JSON 객체여야 합니다.")

    return parsed


def load_manipulation_config(node: Any) -> ManipulationConfig:
    """ROS 노드 파라미터에서 manipulation 설정 로드"""

    named_poses = _parse_mapping_json(
        _read_node_param(node, "manipulation_named_poses_json", "{}"),
        "manipulation_named_poses_json",
    )
    gripper_presets = _parse_mapping_json(
        _read_node_param(node, "manipulation_gripper_presets_json", "{}"),
        "manipulation_gripper_presets_json",
    )

    backend = _read_string(node, "manipulation_backend", "moveit")

    named_pose_map: dict[str, str] = {
        "home": "home",
        "ready": "ready",
        "carry": "carry",
        "stow": "stow",
    }
    named_pose_joint_values: dict[str, dict[str, float]] = {}
    for alias, target in named_poses.items():
        alias_key = str(alias).strip()
        if not alias_key:
            continue
        # 값이 객체(dict)면 해당 joint 값을 사전정의로 저장하고
        # alias는 자기 자신으로 매핑(백엔드가 joint map을 직접 사용).
        if isinstance(target, dict):
            joint_map = {
                str(name).strip(): float(value)
                for name, value in target.items()
                if str(name).strip()
            }
            named_pose_joint_values[alias_key] = joint_map
            named_pose_map[alias_key] = alias_key
        else:
            named_pose_map[alias_key] = str(target).strip()

    preset_map: dict[str, dict[str, float]] = {}
    for preset_name, joint_map in gripper_presets.items():
        if not isinstance(joint_map, dict):
            raise ManipulationConfigError(
                "manipulation_gripper_presets_json 의 각 preset 값은 joint 맵이어야 합니다."
            )
        preset_map[str(preset_name).strip()] = {
            str(joint_name).strip(): float(value)
            for joint_name, value in joint_map.items()
            if str(joint_name).strip()
        }

    return ManipulationConfig(
        enabled=_read_bool(node, "manipulation_enabled", True),
        backend=backend,
        arm_group=_read_string(node, "manipulation_arm_group", "arm"),
        gripper_group=_read_string(node, "manipulation_gripper_group", "gripper"),
        end_effector_link=_read_string(node, "manipulation_end_effector_link", ""),
        base_frame=_read_string(node, "manipulation_base_frame", "base_link"),
        tool_frame=_read_string(node, "manipulation_tool_frame", ""),
        named_poses=named_pose_map,
        named_pose_joint_values=named_pose_joint_values,
        gripper_presets=preset_map,
        planning_pipeline=_read_string(node, "manipulation_planning_pipeline", ""),
        planner_id=_read_string(node, "manipulation_planner_id", ""),
        cartesian_step=_read_float(node, "manipulation_cartesian_step", 0.01),
        velocity_scaling=_read_float(node, "manipulation_velocity_scaling", 0.2),
        acceleration_scaling=_read_float(
            node, "manipulation_acceleration_scaling", 0.2
        ),
        default_approach_distance_m=_read_float(
            node, "manipulation_default_approach_distance_m", 0.1
        ),
        default_retreat_distance_m=_read_float(
            node, "manipulation_default_retreat_distance_m", 0.1
        ),
        cmd_vel_topic=_read_string(node, "manipulation_cmd_vel_topic", ""),
    )
