"""헤드 카메라 기반 대상 관측 (파지 보조용).

그리퍼 카메라(D405)는 손목에 고정되어 있어 ``wrist_extension`` 이 늘수록 대상이
화면 밖으로 밀리고, 근접하면 YOLO 가 물체 전체를 못 봐 탐지에 실패한다. 헤드
카메라(D435i)는 마스트 상단에 있어 팔이 뻗어도 프레이밍이 무너지지 않고,
파지점까지 0.8~1.0m 로 유효 측정 범위 안이다.

``gripper_vision._observe_gripper_target`` 과 대칭 구조이나, 산출물이 2D 이미지
오차가 아니라 **base_link 기준 3D 좌표**라는 점이 다르다. 서보 루프가 그리퍼의
현재 3D 위치(``ee_state._grasp_center_base_xyz``)와 직접 빼서 오차 벡터를 얻을 수
있어야 하기 때문이다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from robo_claw_agent.manipulation_runtime import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
    pose_goal_from_params,
)
from robo_claw_agent.skill_manager import BaseSkill

from ..core import _get_runtime, _transform_pose_goal
from .detection_common import _best_detection
from .params import _float_param

logger = logging.getLogger(__name__)

# 헤드 카메라 검출기(robo_claw.launch.py 의 object_detector_node) 기본 토픽.
_DEFAULT_HEAD_DETECTIONS_TOPIC = "/object_detector_node/detections"
# 헤드 검출기는 confidence_threshold=0.25 로 launch 되므로 그리퍼(0.1)보다 높다.
# C++ 노드가 0.25 미만을 버리므로 Python min_score 도 0.25 로 정합시킨다.
_DEFAULT_HEAD_MIN_SCORE = 0.25
_DEFAULT_HEAD_TIMEOUT_SEC = 3.0


def _head_camera_params(params: dict[str, Any]) -> dict[str, Any]:
    """params 를 헤드 카메라 경로로 라우팅되도록 정규화한다.

    ``perception_skill.core._fetch_depth_frame`` 은 ``camera``/``camera_source`` 가
    그리퍼 alias 이면 gripper_* 토픽을 쓰고, 아니면 depth_topic/camera_info_topic
    (=헤드)을 쓴다. 호출부 params 에 그리퍼용 값이 섞여 들어와도 헤드로 강제되도록
    관련 키를 제거하고, 명시적 헤드 오버라이드만 다시 채운다.
    """
    head_params = dict(params)
    for key in ("camera", "camera_source", "depth_topic", "camera_info_topic"):
        head_params.pop(key, None)

    depth_override = str(params.get("head_depth_topic") or "").strip()
    if depth_override:
        head_params["depth_topic"] = depth_override
    info_override = str(params.get("head_camera_info_topic") or "").strip()
    if info_override:
        head_params["camera_info_topic"] = info_override
    return head_params


def _map_point_to_base(
    skill: BaseSkill, target_pose_meta: Mapping[str, Any]
) -> tuple[float, float, float] | None:
    """map 프레임 target_pose metadata 를 base_link 좌표로 변환한다.

    ``rotation._localize_rotation_with_base_camera`` 와 동일한 방식으로,
    저장 시점이 아니라 **지금 시점의 최신 TF**(``_transform_pose_goal``)를 사용한다.
    """
    try:
        runtime = _get_runtime(skill.node)
        pose = pose_goal_from_params(
            {"target_pose": target_pose_meta}, default_frame=runtime.config.base_frame
        )
        pose = _transform_pose_goal(skill.node, pose, runtime.config.base_frame)
    except (
        ManipulationConfigError,
        ManipulationRuntimeUnavailableError,
        ManipulationError,
        ValueError,
        TypeError,
    ) as exc:
        logger.debug("Failed to convert head observation to base frame: %s", exc)
        return None
    return (
        float(pose.position["x"]),
        float(pose.position["y"]),
        float(pose.position["z"]),
    )


def _observe_head_target(skill: BaseSkill, params: dict[str, Any]) -> dict[str, Any]:
    """헤드 카메라 RGB-D 로 target_object 를 관측해 base_link 3D 좌표를 반환한다.

    3D 좌표를 얻지 못하면(검출 실패, depth 무효, TF 실패) ``success=False`` 를
    반환한다. 헤드 보조의 목적 자체가 3D 오차 계산이므로 2D 만으로는 쓸모가 없다.
    """
    from robo_claw_agent.skills.perception_skill.core import (
        _depth_point_for_pixel,
        _fetch_depth_frame,
        _target_pose_metadata,
    )

    target_object = str(params.get("target_object") or "cup").strip()
    detections_topic = skill.get_string_param(
        params, "head_detections_topic", _DEFAULT_HEAD_DETECTIONS_TOPIC
    )
    min_score = _float_param(params, "head_min_score", _DEFAULT_HEAD_MIN_SCORE)
    timeout_sec = _float_param(params, "head_timeout_sec", _DEFAULT_HEAD_TIMEOUT_SEC)

    det, class_name, score, error = _best_detection(
        skill,
        target_object,
        detections_topic=detections_topic,
        min_score=min_score,
        timeout_sec=timeout_sec,
    )
    if det is None:
        # COCO 어휘 밖 물체(예: 물통)는 YOLO로 절대 못 잡으므로, VLM 오픈어휘
        # 관측을 폴백으로 시도한다. 실패하면 기존 실패 반환에 폴백 사유를 붙인다.
        from robo_claw_agent.skills.perception_skill.vlm_localize import (
            _observe_target_vlm,
            _use_vlm_fallback,
        )

        vlm_note = ""
        if _use_vlm_fallback(skill, params, target_object):
            vlm_obs = _observe_target_vlm(skill, params)
            if vlm_obs.get("success", False):
                return vlm_obs
            vlm_note = vlm_obs.get("message", "")
        return {
            "success": False,
            "message": (f"헤드 카메라 {error}" + (f" | {vlm_note}" if vlm_note else "")),
            "target_object": target_object,
            "camera": "head",
            "detections_topic": detections_topic,
        }

    cx = float(det.bbox.center.position.x)
    cy = float(det.bbox.center.position.y)
    size_x = float(det.bbox.size_x)
    size_y = float(det.bbox.size_y)

    head_params = _head_camera_params(params)
    depth_frame = _fetch_depth_frame(skill, head_params)
    depth_point_map, depth_m = _depth_point_for_pixel(skill, depth_frame, head_params, cx, cy)
    if depth_point_map is None:
        return {
            "success": False,
            "message": (
                f"헤드 카메라에서 '{class_name or target_object}'는 탐지했으나 "
                "depth/TF 로 3D 좌표를 얻지 못했습니다."
            ),
            "target_object": target_object,
            "class_name": class_name,
            "camera": "head",
            "detections_topic": detections_topic,
            "bbox_center": {"x": round(cx, 1), "y": round(cy, 1)},
            "depth_m": round(depth_m, 3) if depth_m is not None else None,
        }

    target_pose = _target_pose_metadata(skill, depth_point_map)
    object_base_xyz = _map_point_to_base(skill, target_pose)
    if object_base_xyz is None:
        return {
            "success": False,
            "message": "헤드 카메라 관측을 base_link 로 변환하지 못했습니다(TF 확인 필요).",
            "target_object": target_object,
            "class_name": class_name,
            "camera": "head",
            "detections_topic": detections_topic,
            "target_pose": target_pose,
        }

    image_size: dict[str, int] = {}
    if depth_frame is not None:
        image_size = {
            "width": int(depth_frame.depth_img.shape[1]),
            "height": int(depth_frame.depth_img.shape[0]),
        }

    return {
        "success": True,
        "message": f"헤드 카메라에서 '{class_name}' 3D 관측",
        "target_object": target_object,
        "class_name": class_name,
        "score": round(score, 4),
        "camera": "head",
        "detections_topic": detections_topic,
        "bbox_center": {"x": round(cx, 1), "y": round(cy, 1)},
        "bbox_size": {"x": round(size_x, 1), "y": round(size_y, 1)},
        "image_size": image_size,
        "depth_m": round(depth_m, 3) if depth_m is not None else None,
        "object_base_xyz": {
            "x": round(object_base_xyz[0], 4),
            "y": round(object_base_xyz[1], 4),
            "z": round(object_base_xyz[2], 4),
        },
        "target_pose": target_pose,
    }


def _object_base_xyz(observation: Mapping[str, Any]) -> tuple[float, float, float] | None:
    """``_observe_head_target`` 결과에서 base_link 3D 좌표 튜플을 꺼낸다."""
    if not observation.get("success", False):
        return None
    raw = observation.get("object_base_xyz")
    if not isinstance(raw, Mapping):
        return None
    try:
        return float(raw["x"]), float(raw["y"]), float(raw["z"])
    except (KeyError, TypeError, ValueError):
        return None


class ObserveHeadTargetSkill(BaseSkill):
    """헤드 카메라 RGB-D 로 대상의 base_link 3D 좌표를 관측(진단/디버깅용)."""

    name = "observe_head_target"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "target_object": {"type": "string"},
            "camera": {"type": "string", "default": "base"},
        },
        "required": ["target_object"],
        "additionalProperties": False,
    }
    description = (
        "Stretch3 헤드 카메라 detection 과 aligned depth 로 target_object 의 "
        "base_link 기준 3D 좌표를 관측합니다. 그리퍼 카메라가 근접에서 대상을 놓칠 때 "
        "헤드 카메라가 대상을 보고 있는지 확인하는 진단 용도로 사용합니다."
    )

    def validate_params(self, params: dict[str, Any]) -> bool:
        return bool(str(params.get("target_object") or "").strip())

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None:
            return {"success": False, "message": "ROS 노드에 접근할 수 없습니다."}
        return _observe_head_target(self, params)
