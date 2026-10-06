"""그리퍼 카메라로 대상을 관측하고 시각 서보로 정렬하는 스킬."""

from __future__ import annotations

import logging
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from ..core import _get_runtime
from .gripper_vision import _observe_gripper_target
from .head_assist import ArmBounds, HeadAssist, lateral_align
from .head_vision import _map_point_to_base
from .joint_state import _joint_positions
from .params import (
    _bool_param,
    _clamp,
    _clamp_int,
    _float_param,
    _is_cup_target,
    _is_deadline_exceeded,
    _resolve_deadline,
)

logger = logging.getLogger(__name__)


class ObserveGripperTargetSkill(BaseSkill):
    """그리퍼 카메라 RGB-D로 대상 물체의 위치 오차와 depth를 관측."""

    name = "observe_gripper_target"
    requires_manipulation_backend = "stretch"
    input_schema = {
        "type": "object",
        "properties": {
            "target_object": {"type": "string"},
            "camera": {"type": "string", "default": "gripper"},
        },
        "required": ["target_object"],
        "additionalProperties": False,
    }
    description = (
        "Stretch3 그리퍼 카메라 detection과 aligned depth로 target_object를 관측합니다. "
        "bbox 중심, 이미지 중심 오차, depth, ready_to_grasp 여부를 반환합니다."
    )

    def validate_params(self, params: dict[str, Any]) -> bool:
        return bool(str(params.get("target_object") or "").strip())

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None:
            return {"success": False, "message": "ROS 노드에 접근할 수 없습니다."}
        try:
            runtime = _get_runtime(self.node)
            if getattr(runtime.config, "backend", "") != "stretch":
                return {
                    "success": False,
                    "message": f"observe_gripper_target는 Stretch 백엔드 전용 스킬입니다 (현재 backend: {runtime.config.backend})",
                }
        except Exception:
            pass
        return _observe_gripper_target(self, params)


class ServoGripperToObjectSkill(BaseSkill):
    """그리퍼 카메라 관측 오차를 줄이도록 wrist_extension/joint_lift를 소폭 보정."""

    name = "servo_gripper_to_object"
    is_internal = True
    requires_manipulation_backend = "stretch"
    input_schema = {
        "type": "object",
        "properties": {
            "target_object": {"type": "string"},
            "max_steps": {"type": "integer"},
            "center_tolerance_px": {"type": "number"},
            "lift_tolerance_px": {"type": "number"},
            "target_depth_m": {"type": "number"},
            "use_head_assist": {"type": "boolean", "default": True},
            "allow_servo_base_rotation": {"type": "boolean", "default": False},
        },
        "required": ["target_object"],
        "additionalProperties": True,
    }
    description = (
        "Stretch3 그리퍼 카메라 RGB-D 관측을 반복하며 target_object가 집기 윈도우에 "
        "들어오도록 wrist_extension과 joint_lift를 작은 step으로 보정합니다. "
        "use_head_assist(기본 True)이면 그리퍼 카메라가 근접에서 대상을 놓쳤을 때 "
        "헤드 카메라 3D 관측으로 계속 유도하고, depth 소실 시에도 헤드로 파지 거리를 "
        "확인한 뒤에만 집기를 진행합니다."
    )
    allow_with_others = False

    def validate_params(self, params: dict[str, Any]) -> bool:
        return bool(str(params.get("target_object") or "").strip())

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None:
            return {"success": False, "message": "ROS 노드에 접근할 수 없습니다."}

        target_object = str(params.get("target_object") or "cup").strip()
        cup_target = _is_cup_target(target_object)
        default_max_steps = 36 if cup_target else 8
        max_steps = _clamp_int(
            params.get("max_steps", params.get("max_visual_servo_steps", default_max_steps)),
            1,
            50,
            default_max_steps,
        )
        center_tolerance_px = _float_param(
            params, "center_tolerance_px", 100.0 if cup_target else 60.0
        )
        lift_tolerance_px = _float_param(
            params, "lift_tolerance_px", 30.0 if cup_target else center_tolerance_px
        )
        target_depth_m = _float_param(params, "target_depth_m", 0.22 if cup_target else 0.25)
        depth_tolerance_m = _float_param(params, "depth_tolerance_m", 0.05 if cup_target else 0.04)
        extension_step_m = _float_param(params, "extension_step_m", 0.05 if cup_target else 0.03)
        lift_step_m = _float_param(params, "lift_step_m", 0.03 if cup_target else 0.02)
        max_lift_step_m = _float_param(
            params, "max_lift_step_m", 0.06 if cup_target else lift_step_m
        )
        lost_target_lift_recovery_steps = _clamp_int(
            params.get("lost_target_lift_recovery_steps", 5 if cup_target else 1),
            0,
            10,
            5 if cup_target else 1,
        )
        min_extension_m = _float_param(params, "min_extension_m", 0.0)
        max_extension_m = _float_param(params, "max_extension_m", 0.52)
        min_lift_m = _float_param(params, "min_lift_m", 0.0)
        max_lift_m = _float_param(params, "max_lift_m", 1.1)

        allow_servo_base_rotation = _bool_param(params.get("allow_servo_base_rotation"), False)
        max_base_rotations = _clamp_int(params.get("max_base_rotations", 1), 0, 5, 1)
        deadline, _ = _resolve_deadline(self, params, 60.0)

        runtime = _get_runtime(self.node)
        if getattr(runtime.config, "backend", "") != "stretch":
            return {
                "success": False,
                "message": f"servo_gripper_to_object는 Stretch 백엔드 전용 스킬입니다 (현재 backend: {runtime.config.backend})",
            }
        # 헤드 카메라 보조는 Stretch3 전용이다(head_pan_tilt 자체가 Stretch 전용이고,
        # gripper_side_head 자세/팔 작업축 분해가 Stretch 운동학에 묶여 있다).
        use_head_assist = (
            _bool_param(params.get("use_head_assist"), True)
            and runtime.config.backend == "stretch"
        )
        ros_logger = self.node.get_logger() if hasattr(self.node, "get_logger") else logger
        observations: list[dict[str, Any]] = []
        moves: list[dict[str, float]] = []
        saw_target = False
        lost_recovery_count = 0
        # 컵은 팔을 뻗을수록 화면 하단으로 빠지기 쉬워, 재획득 첫 방향은 lift down.
        last_lift_recovery_direction = -1.0 if cup_target else 0.0

        # 헤드 카메라 보조: 그리퍼 카메라가 근접에서 대상을 놓쳤을 때 눈감고 흔드는
        # 대신 실제 3D 오차 방향으로 유도한다. 비활성 시 아래 로직은 전부 우회되어
        # 기존 동작이 그대로 유지된다.
        head_assist = HeadAssist(self, params, enabled=use_head_assist)
        arm_bounds = ArmBounds(
            min_extension_m=min_extension_m,
            max_extension_m=max_extension_m,
            min_lift_m=min_lift_m,
            max_lift_m=max_lift_m,
            extension_step_m=extension_step_m,
            max_lift_step_m=max_lift_step_m,
        )
        head_steps: list[dict[str, Any]] = []
        base_rotation_count = 0
        if use_head_assist:
            head_steps.append({"stage": "ensure_pose", **head_assist.ensure_pose()})

        def _result(payload: dict[str, Any]) -> dict[str, Any]:
            """공통 진단 필드를 붙여 결과를 반환한다."""
            payload.setdefault("observations", observations)
            payload.setdefault("moves", moves)
            if use_head_assist:
                payload["head_assist"] = {
                    "guided_steps": head_assist.guided_steps,
                    "base_rotations": base_rotation_count,
                    "steps": head_steps,
                }
            return payload

        def _try_head_assist(*, move: bool) -> tuple[dict[str, Any] | None, bool]:
            """헤드 보조 1스텝.

            Returns:
                (즉시 반환할 서보 결과 또는 None, 헤드가 상황을 처리했는지).
                두 번째 값이 False 면 헤드가 도움이 되지 못한 것이므로 호출부는
                기존 폴백(눈감고 흔들기 / 직전 관측 기준 집기)으로 넘어가야 한다.
            """
            nonlocal base_rotation_count
            if not use_head_assist:
                return None, False
            assist = head_assist.assist_step(runtime, arm_bounds, move=move)
            head_steps.append(assist)
            outcome = assist.get("outcome")

            if outcome == "ready":
                return (
                    _result(
                        {
                            "success": True,
                            "message": assist.get("message", "헤드 카메라 기준 집기 준비 완료"),
                            "ready_to_grasp": True,
                            "ready_source": "head_camera",
                            "final_observation": assist.get("observation"),
                        }
                    ),
                    True,
                )

            if outcome == "guided":
                moved = assist.get("moved")
                if isinstance(moved, dict):
                    moves.append(moved)
                return None, True  # 다음 스텝에서 그리퍼 카메라로 재관측

            if outcome == "blocked":
                # 남은 오차가 좌우 성분뿐 = 팔 관절로는 못 줄인다. 베이스 회전은
                # 팔을 휘두르는 동작이라 명시적 옵트인일 때만 허용한다.
                obj_xyz = assist.get("object_base_xyz")
                if (
                    move
                    and allow_servo_base_rotation
                    and obj_xyz is not None
                    and base_rotation_count < max(0, max_base_rotations)
                ):
                    rotation_info = lateral_align(
                        self, runtime, params, obj_xyz, allow_retract=True
                    )
                    head_steps.append({"stage": "servo_lateral_align", **rotation_info})
                    if rotation_info.get("rotated", False):
                        base_rotation_count += 1
                        return None, True
                # 헤드는 대상을 보고 있지만 이번 스텝에 할 수 있는 일이 없다.
                # 눈감고 흔드는 것보다는 낫지 않으므로 폴백을 허용한다.
                return None, False

            return None, False

        def _try_lateral_correction(obs: dict[str, Any]) -> dict[str, Any] | None:
            """좌우(err_x) 오차를 베이스 미세회전으로 보정한다.

            물체의 base_link 좌표는 헤드 관측을 우선하고(팔에 가려지지 않아 더 안정적),
            없으면 그리퍼 관측이 이미 map 프레임으로 반환한 ``target_pose`` 를 쓴다.
            회전은 팔을 휘두르는 동작이므로 ``allow_servo_base_rotation`` 옵트인이
            필요하다. 비활성이면 진단 정보만 담아 반환한다.
            """
            nonlocal base_rotation_count
            obj_xyz: tuple[float, float, float] | None = None
            source = ""
            if use_head_assist:
                obj_xyz, head_obs = head_assist.observe()
                if obj_xyz is not None:
                    source = "head_camera"
                    if head_assist.disagrees_with(obs.get("target_pose"), obj_xyz):
                        obj_xyz = None
                        source = ""
                    else:
                        head_steps.append({"stage": "lateral_observe", **head_obs})
            if obj_xyz is None:
                obj_xyz = _map_point_to_base(self, obs.get("target_pose") or {})
                source = "gripper_camera" if obj_xyz is not None else ""
            if obj_xyz is None:
                return {
                    "rotated": False,
                    "reason": "물체의 base_link 3D 좌표를 얻지 못해 좌우 보정을 할 수 없습니다.",
                }

            if not allow_servo_base_rotation:
                return {
                    "rotated": False,
                    "source": source,
                    "object_base_xyz": obj_xyz,
                    "reason": (
                        "좌우 오차가 남았지만 allow_servo_base_rotation=False 라 "
                        "서보 중 베이스 회전을 하지 않습니다."
                    ),
                }
            if base_rotation_count >= max(0, max_base_rotations):
                return {
                    "rotated": False,
                    "source": source,
                    "object_base_xyz": obj_xyz,
                    "reason": f"서보 중 베이스 회전 횟수 상한({max_base_rotations})에 도달했습니다.",
                }

            info = lateral_align(self, runtime, params, obj_xyz, allow_retract=True)
            info["source"] = source
            head_steps.append({"stage": "servo_lateral_align", **info})
            if info.get("rotated", False):
                base_rotation_count += 1
            return info

        def _no_delta_message(obs: dict[str, Any], rotation_info: dict[str, Any] | None) -> str:
            err_x = obs.get("image_error_px", {}).get("x")
            base = "보정할 안전한 joint delta가 없습니다."
            if err_x is not None:
                base += (
                    f" 남은 오차는 좌우(err_x={err_x}px)이며 joint_lift/wrist_extension"
                    "으로는 줄일 수 없습니다."
                )
            if rotation_info and rotation_info.get("reason"):
                base += f" {rotation_info['reason']}"
            return base

        servo_params = dict(params)
        servo_params["camera"] = "gripper"
        servo_params["target_object"] = target_object
        servo_params["center_tolerance_px"] = center_tolerance_px
        servo_params["center_tolerance_y_px"] = lift_tolerance_px
        servo_params["target_depth_m"] = target_depth_m
        servo_params["depth_tolerance_m"] = depth_tolerance_m
        if cup_target:
            servo_params.setdefault("target_center_y_ratio", 0.58)

        for _step in range(max(1, max_steps)):
            if _is_deadline_exceeded(deadline):
                return _result(
                    {
                        "success": False,
                        "message": "시각 서보 제한 시간(deadline) 초과",
                    }
                )
            obs = _observe_gripper_target(self, servo_params)
            observations.append(obs)
            if not obs.get("success", False):
                # 1순위: 헤드 카메라. 그리퍼가 못 봐도 헤드는 대상을 보고 있을 수
                # 있으므로, 눈감고 흔들기 전에 실제 3D 오차로 유도를 시도한다.
                head_result, handled = _try_head_assist(move=True)
                if head_result is not None:
                    return head_result
                if handled:
                    continue
                # 2순위(폴백): 기존 눈감고 lift 흔들기.
                if saw_target and lost_recovery_count < max(0, lost_target_lift_recovery_steps):
                    joints = _joint_positions(self, ["joint_lift"])
                    if joints is not None and "joint_lift" in joints:
                        direction = last_lift_recovery_direction
                        if abs(direction) < 1e-6:
                            direction = 1.0 if lost_recovery_count % 2 == 0 else -1.0
                        elif lost_recovery_count % 2 == 1:
                            direction *= -1.0
                        magnitude = 1.0 + (lost_recovery_count // 2)
                        target = {
                            "joint_lift": _clamp(
                                joints["joint_lift"] + direction * lift_step_m * magnitude,
                                min_lift_m,
                                max_lift_m,
                            )
                        }
                        ros_logger.info(
                            f"Gripper servo lift re-search after target loss: "
                            f"step={_step} recovery={lost_recovery_count + 1} "
                            f"direction={'down' if direction < 0 else 'up'} "
                            f"target={target} reason={obs.get('message', '')}"
                        )
                        runtime.move_to_joint_target(target, group_name="arm")
                        moves.append(target)
                        lost_recovery_count += 1
                        continue
                return _result(
                    {
                        "success": False,
                        "message": obs.get("message", "그리퍼 관측 실패"),
                    }
                )
            saw_target = True
            lost_recovery_count = 0
            ros_logger.info(
                f"Gripper servo observation: step={_step} "
                f"ready={obs.get('ready_to_grasp')} "
                f"center={obs.get('bbox_center')} "
                f"target_center={obs.get('target_center_px')} "
                f"error={obs.get('image_error_px')} "
                f"depth={obs.get('depth_m')} "
                f"fallback={obs.get('detection_fallback')}"
            )
            if obs.get("ready_to_grasp"):
                return _result(
                    {
                        "success": True,
                        "message": "그리퍼 대상 정렬 완료",
                        "ready_to_grasp": True,
                        "ready_source": "gripper_camera",
                        "final_observation": obs,
                    }
                )

            depth_m = obs.get("depth_m")
            if depth_m is None:
                # depth가 사라진 경우: 컵이 카메라 최소 거리 이내이거나
                # 시야를 벗어난 것.
                # 1순위: 헤드 카메라로 실제 파지 거리를 확인한다. 눈감고 집기는
                # 마지막 수단이어야 한다.
                head_result, handled = _try_head_assist(move=True)
                if head_result is not None:
                    return head_result
                if handled:
                    continue
                # 2순위(폴백): 직전 관측에서 충분히 가까웠다면
                # ready로 간주하고 집기를 시도한다.
                if saw_target and len(observations) >= 2:
                    prev_obs = observations[-2]
                    prev_depth = prev_obs.get("depth_m")
                    prev_err_y = float(prev_obs.get("image_error_px", {}).get("y", 999.0))
                    if (
                        prev_depth is not None
                        and prev_depth < target_depth_m + depth_tolerance_m * 2
                        and abs(prev_err_y) <= lift_tolerance_px
                    ):
                        ros_logger.info(
                            f"Depth lost -> attempting pick based on previous observation: "
                            f"prev_depth={prev_depth} prev_err_y={prev_err_y:.1f}"
                        )
                        return _result(
                            {
                                "success": True,
                                "message": "depth 소실 — 직전 관측 기준 집기 시도",
                                "ready_to_grasp": True,
                                "ready_source": "previous_gripper_observation",
                                "final_observation": prev_obs,
                            }
                        )
                return _result(
                    {
                        "success": False,
                        "message": "대상 depth를 얻지 못해 안전하게 보정을 중단합니다.",
                    }
                )

            joints = _joint_positions(self, ["wrist_extension", "joint_lift"])
            if joints is None or "wrist_extension" not in joints or "joint_lift" not in joints:
                return _result(
                    {
                        "success": False,
                        "message": "joint_states에서 wrist_extension/joint_lift를 읽지 못했습니다.",
                    }
                )

            err = obs.get("image_error_px", {})
            err_y = float(err.get("y", 0.0))
            lift_delta_m = _clamp(
                abs(err_y) / 60.0 * lift_step_m,
                lift_step_m,
                max_lift_step_m,
            )
            target: dict[str, float] = {}

            if err_y < -lift_tolerance_px:
                last_lift_recovery_direction = 1.0
                target["joint_lift"] = _clamp(
                    joints["joint_lift"] + lift_delta_m,
                    min_lift_m,
                    max_lift_m,
                )
            elif err_y > lift_tolerance_px:
                last_lift_recovery_direction = -1.0
                target["joint_lift"] = _clamp(
                    joints["joint_lift"] - lift_delta_m,
                    min_lift_m,
                    max_lift_m,
                )

            if cup_target and "joint_lift" in target:
                # 컵이 target 위쪽(err_y < 0)에 있을 때는 lift up + extension 동시 수행:
                # 둘 다 컵을 중앙으로 가져오고 depth를 줄인다.
                # 컵이 target 아래쪽(err_y > 0)에 있을 때는 lift down만 수행:
                # extension은 컵을 더 아래로 밀어내므로 하지 않는다.
                if err_y < -lift_tolerance_px:
                    if depth_m > target_depth_m + depth_tolerance_m:
                        target["wrist_extension"] = _clamp(
                            joints["wrist_extension"] + extension_step_m,
                            min_extension_m,
                            max_extension_m,
                        )
                    ros_logger.info(
                        f"Gripper servo vertical+forward correction: step={_step} "
                        f"err_y={err_y:.1f} lift_delta={lift_delta_m:.3f} "
                        f"depth={depth_m} target={target}"
                    )
                else:
                    ros_logger.info(
                        f"Gripper servo vertical correction (no forward): step={_step} "
                        f"err_y={err_y:.1f} lift_delta={lift_delta_m:.3f} "
                        f"depth={depth_m} target={target}"
                    )
                runtime.move_to_joint_target(target, group_name="arm")
                moves.append(target)
                continue

            if depth_m > target_depth_m + depth_tolerance_m:
                target["wrist_extension"] = _clamp(
                    joints["wrist_extension"] + extension_step_m,
                    min_extension_m,
                    max_extension_m,
                )
            elif depth_m < target_depth_m - depth_tolerance_m:
                target["wrist_extension"] = _clamp(
                    joints["wrist_extension"] - extension_step_m,
                    min_extension_m,
                    max_extension_m,
                )

            if not target:
                # err_y 와 depth 는 허용 범위인데 ready 가 아니다 = 남은 것은 err_x(좌우).
                # joint_lift/wrist_extension 으로는 좌우를 못 줄이므로 기존 구현은 여기서
                # 즉시 실패했다. Stretch3 에서 이 자유도는 베이스 회전뿐이다.
                rotation_info = _try_lateral_correction(obs)
                if rotation_info is not None and rotation_info.get("rotated", False):
                    continue
                return _result(
                    {
                        "success": False,
                        "message": _no_delta_message(obs, rotation_info),
                        "final_observation": obs,
                        "lateral_correction": rotation_info,
                    }
                )

            ros_logger.info(f"Gripper servo correction command: step={_step} target={target}")
            runtime.move_to_joint_target(target, group_name="arm")
            moves.append(target)

        final_obs = _observe_gripper_target(self, servo_params)
        observations.append(final_obs)
        return _result(
            {
                "success": bool(final_obs.get("ready_to_grasp", False)),
                "message": "그리퍼 대상 정렬 완료"
                if final_obs.get("ready_to_grasp", False)
                else "최대 보정 횟수 내에 집기 준비 상태에 도달하지 못했습니다.",
                "ready_to_grasp": bool(final_obs.get("ready_to_grasp", False)),
                "final_observation": final_obs,
            }
        )
