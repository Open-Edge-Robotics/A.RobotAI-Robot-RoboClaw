"""VLM 오픈어휘 물체 로컬라이즈 (COCO 어휘 밖 / YOLO 미검출 물체용).

YOLO(COCO 80 클래스)는 어휘에 없는 물체(예: "물통")를 절대 검출하지 못한다.
이 모듈은 기존 LLM 브리지의 ``analyze_image``(map_skill/scan 이 이미 쓰는 경로)를
재사용해 대상의 **정규화된 bbox 중심**을 얻고, ``perception_skill.core`` 의
depth/TF 파이프라인으로 map 3D 좌표까지 이어지는 관측 결과를 만든다.

성능상 VLM 호출은 비싸므로 YOLO가 빨리 실패한 뒤의 **마지막 보루**로만 쓴다.
모든 실패는 ``None``/``success=False`` 로 흡수되어 호출부의 기존 폴백 경로를 막지
않는다.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Mapping
from typing import Any

from robo_claw_agent.agent_node.utils import extract_objects
from robo_claw_agent.skill_manager import BaseSkill

from .core import _fetch_depth_frame, _target_pose_metadata

logger = logging.getLogger(__name__)

_DEFAULT_VLM_IMAGE_TIMEOUT_SEC = 2.0
_DEFAULT_VLM_JPEG_QUALITY = 80
# VLM이 정규화 좌표를 0.0~1.0 범위 밖으로(픽셀 좌표로 오인 등) 줄 경우 폐기.
_NORM_EPS = 0.005


def _capture_head_image_base64(
    skill: BaseSkill,
    params: dict[str, Any],
    *,
    timeout_sec: float = _DEFAULT_VLM_IMAGE_TIMEOUT_SEC,
) -> tuple[Any, int, int] | None:
    """헤드(기본) 카메라 영상을 OpenCV 이미지로 받아 JPEG base64로 인코딩한다.

    Returns:
        (base64_string, width, height) 또는 None.
    """
    try:
        import cv2
    except ImportError:
        return None
    try:
        cv_img, _, _ = skill.get_opencv_image(dict(params), timeout_sec=timeout_sec)
    except Exception as exc:  # noqa: BLE001 - 보조 기능이므로 흡수
        logger.warning("VLM localize image capture failed: %s", exc)
        return None
    if cv_img is None:
        return None
    height, width = cv_img.shape[:2]
    try:
        _, buf = cv2.imencode(
            ".jpg", cv_img, [int(cv2.IMWRITE_JPEG_QUALITY), _DEFAULT_VLM_JPEG_QUALITY]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("VLM localize image encode failed: %s", exc)
        return None
    b64 = base64.b64encode(buf).decode("utf-8")
    return b64, width, height


def _vlm_normalized_center(
    skill: BaseSkill,
    target_object: str,
    image_base64: str,
) -> tuple[float, float, str] | None:
    """VLM에 대상의 정규화된 bbox 중심(x_rel, y_rel)을 물어 해석한다.

    Returns:
        (x_rel, y_rel, class_name) 또는 None. x_rel/y_rel 은 0.0~1.0.
    """
    llm = getattr(skill.node, "_llm", None)
    if llm is None:
        return None
    try:
        analyze = getattr(llm, "analyze_image", None)
        if analyze is None:
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("VLM localize unavailable: %s", exc)
        return None

    prompt = (
        f"다음 이미지에서 '{target_object}' 를 찾아 그 물체의 중심(bbox 중점) 위치를 "
        "정확히 짚어주세요. 물체가 보이지 않으면 OBJECTS 를 비워두세요.\n"
        "### 응답 형식 지침\n"
        "반드시 아래 JSON 블록 형식을 응답 끝에 포함하세요.\n"
        'OBJECTS: [{"name": "' + target_object + '", "x_rel": 0.000, "y_rel": 0.000}]\n'
        "(x_rel, y_rel 은 이미지 가로/세로 기준 0.0~1.0 사이의 정규화된 중심 좌표이며, "
        "물체 중심을 매우 정밀하게 추정해 소수점 3자리 이상으로 작성하세요. "
        "물체가 없으면 OBJECTS: [] 로 응답하세요.)"
    )
    try:
        text = analyze(prompt, image_base64)
    except Exception as exc:  # noqa: BLE001
        logger.warning("VLM analyze_image failed for '%s': %s", target_object, exc)
        return None

    for obj in extract_objects(text):
        if not isinstance(obj, Mapping):
            continue
        x_rel = _norm_coord(obj.get("x_rel"), obj.get("x"))
        y_rel = _norm_coord(obj.get("y_rel"), obj.get("y"))
        if x_rel is None or y_rel is None:
            continue
        name = str(obj.get("name") or obj.get("label") or target_object)
        return x_rel, y_rel, name
    logger.warning(
        "VLM did not locate '%s' (extracted %d objects)", target_object, len(extract_objects(text))
    )
    return None


def _norm_coord(*candidates: Any) -> float | None:
    """0.0~1.0 범위의 정규화 좌표 후보 중 유효한 첫 값을 반환."""
    for value in candidates:
        try:
            val = float(value)
        except (TypeError, ValueError):
            continue
        if _NORM_EPS <= val <= (1.0 - _NORM_EPS):
            return val
    return None


def _flag(value: Any, default: bool = True) -> bool:
    """params 값에서 불리언 플래그를 안전하게 읽는다."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off")
    return default


def _use_vlm_fallback(skill: BaseSkill, params: dict[str, Any], target_object: str) -> bool:
    """이 대상/요청에 VLM 오픈어휘 폴백을 시도할지 결정한다.

    - ``use_vlm_fallback``(기본 True)으로 전체 폴백을 끌 수 있다.
    - ``force_vlm_fallback=True`` 면 COCO 대상이라도 YOLO 미검출 시 시도한다.
    - 그 외에는 **COCO 어휘 밖 대상만** 시도해 VLM 호출 비용을 한정한다.
    """
    if not _flag(params.get("use_vlm_fallback"), True):
        return False
    if _flag(params.get("force_vlm_fallback"), False):
        return True
    from .core import target_is_coco

    return not target_is_coco(target_object)


def _vlm_object_center_px(
    skill: BaseSkill,
    params: dict[str, Any],
    target_object: str,
) -> tuple[float, float, str] | None:
    """헤드 카메라 영상에서 VLM으로 대상의 픽셀 중심 좌표를 얻는다.

    Returns:
        (cx_px, cy_px, class_name) 또는 None.
    """
    captured = _capture_head_image_base64(skill, params)
    if captured is None:
        return None
    image_base64, width, height = captured
    center = _vlm_normalized_center(skill, target_object, image_base64)
    if center is None:
        return None
    x_rel, y_rel, class_name = center
    cx = float(x_rel) * (width - 1)
    cy = float(y_rel) * (height - 1)
    return cx, cy, class_name


def _observe_target_vlm(skill: BaseSkill, params: dict[str, Any]) -> dict[str, Any]:
    """VLM 오픈어휘 관측: 대상 bbox 중심 → depth/TF 로 map 3D 좌표까지 산출.

    ``head_vision._observe_head_target`` 과 동일한 반환 형태를 갖추어 호출부가
    기존 폴백과 자연스럽게 이어갈 수 있다. 성공 시 ``success=True`` 와
    ``target_pose``(map 프레임) 및 base_link 변환 결과를 담는다.
    """
    from robo_claw_agent.manipulation_runtime import (
        ManipulationConfigError,
        ManipulationError,
        ManipulationRuntimeUnavailableError,
        pose_goal_from_params,
    )
    from robo_claw_agent.skills.manipulation_skill.core import (
        _get_runtime,
        _transform_pose_goal,
    )

    target_object = str(params.get("target_object") or "cup").strip()
    center = _vlm_object_center_px(skill, params, target_object)
    if center is None:
        return {
            "success": False,
            "message": f"VLM 오픈어휘 관측 실패: '{target_object}' 중심을 얻지 못했습니다.",
            "target_object": target_object,
            "camera": "head",
            "localize": "vlm",
        }
    cx, cy, class_name = center

    depth_frame = _fetch_depth_frame(skill, params)
    depth_point_map, depth_m = _depth_point_for_pixel_local(skill, depth_frame, params, cx, cy)
    if depth_point_map is None:
        return {
            "success": False,
            "message": (
                f"VLM이 '{class_name or target_object}'를 찾았으나 depth/TF로 "
                "3D 좌표를 얻지 못했습니다."
            ),
            "target_object": target_object,
            "class_name": class_name,
            "camera": "head",
            "localize": "vlm",
            "bbox_center": {"x": round(cx, 1), "y": round(cy, 1)},
            "depth_m": round(depth_m, 3) if depth_m is not None else None,
        }

    target_pose = _target_pose_metadata(skill, depth_point_map)
    object_base_xyz: tuple[float, float, float] | None = None
    try:
        runtime = _get_runtime(skill.node)
        pose = pose_goal_from_params(
            {"target_pose": target_pose}, default_frame=runtime.config.base_frame
        )
        pose = _transform_pose_goal(skill.node, pose, runtime.config.base_frame)
        object_base_xyz = (
            float(pose.position["x"]),
            float(pose.position["y"]),
            float(pose.position["z"]),
        )
    except (
        ManipulationConfigError,
        ManipulationRuntimeUnavailableError,
        ManipulationError,
        ValueError,
        TypeError,
    ) as exc:
        logger.debug("VLM localize base-frame transform failed: %s", exc)
        object_base_xyz = None

    result: dict[str, Any] = {
        "success": True,
        "message": f"VLM 오픈어휘 관측: '{class_name}' 3D 관측",
        "target_object": target_object,
        "class_name": class_name,
        "camera": "head",
        "localize": "vlm",
        "bbox_center": {"x": round(cx, 1), "y": round(cy, 1)},
        "target_pose": target_pose,
    }
    if depth_m is not None:
        result["depth_m"] = round(depth_m, 3)
    if object_base_xyz is not None:
        result["object_base_xyz"] = {
            "x": round(object_base_xyz[0], 4),
            "y": round(object_base_xyz[1], 4),
            "z": round(object_base_xyz[2], 4),
        }
    return result


def _depth_point_for_pixel_local(skill, depth_frame, params, cx, cy):
    """head_vision 의 upright 역회전 보정 없이 depth 지점만 계산(로컬 복제).

    VLM bbox 중심은 raw 픽셀 좌표를 그대로 쓰므로 헤드 upright 보정은 적용하지
    않는다. depth/CameraInfo 구독은 ``_fetch_depth_frame`` 한 번으로 끝난다.
    """
    from .core import _depth_point_for_pixel, _head_rotate_deg, _is_gripper_camera_params

    rotate_deg = _head_rotate_deg(skill, params)
    if rotate_deg != 0.0 and not _is_gripper_camera_params(params):
        from .depth3d import unrotate_pixel

        if depth_frame is not None:
            raw_h, raw_w = depth_frame.depth_img.shape[:2]
            up_w = int(params.get("upright_image_width", 0) or 0)
            up_h = int(params.get("upright_image_height", 0) or 0)
            cx, cy = unrotate_pixel(cx, cy, rotate_deg, raw_w, raw_h, up_w, up_h)
    return _depth_point_for_pixel(skill, depth_frame, params, cx, cy)
