"""그리퍼(손목) 카메라 기반 대상 탐지 및 RGB-D 관측 헬퍼."""

from __future__ import annotations

from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .detection_common import _best_detection
from .params import _float_param, _is_cup_target, _number_tuple_param


def _best_gripper_detection(
    skill: BaseSkill,
    target_object: str,
    *,
    detections_topic: str,
    min_score: float,
    timeout_sec: float,
) -> tuple[Any | None, str, float, str]:
    """그리퍼 카메라 detection 선택 (카메라 무관 ``_best_detection`` 의 얇은 래퍼).

    기존 호출부/테스트가 이 이름으로 monkeypatch 하고 있어 하위호환용으로 남긴다.
    """
    return _best_detection(
        skill,
        target_object,
        detections_topic=detections_topic,
        min_score=min_score,
        timeout_sec=timeout_sec,
    )


def _fallback_cup_center_from_gripper_image(
    skill: BaseSkill,
    params: dict[str, Any],
    *,
    target_center_x_ratio: float = 0.5,
    target_center_y_ratio: float = 0.58,
) -> tuple[float, float, float, float, str] | None:
    """YOLO가 ArUco 마커 등을 다른 클래스로 오인식해 컵을 놓칠 때 색/형상으로 컵 중심을 추정.

    기본 HSV/ROI/면적/종횡비 값은 개발 현장에서 쓰인 연한 녹색 컵 기준이며 다른 색
    컵/현장에서는 그대로 맞지 않는다. 코드를 고치지 않고도 `cup_fallback_hsv_lower`,
    `cup_fallback_hsv_upper`, `cup_fallback_roi_x_range`, `cup_fallback_roi_y_range`,
    `cup_fallback_area_range`, `cup_fallback_aspect_range` 파라미터로 현장별 재조정이
    가능하도록 값을 모두 파라미터화했다(미지정 시 기존 하드코딩 값과 동일하게 동작).
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    image_params = dict(params)
    image_params["camera"] = "gripper"
    cv_img, _, _ = skill.get_opencv_image(image_params, timeout_sec=1.0)
    if cv_img is None:
        return None

    height, width = cv_img.shape[:2]
    hsv = cv2.cvtColor(cv_img, cv2.COLOR_BGR2HSV)

    hsv_lower = _number_tuple_param(params.get("cup_fallback_hsv_lower"), (35.0, 18.0, 70.0))
    hsv_upper = _number_tuple_param(params.get("cup_fallback_hsv_upper"), (95.0, 220.0, 255.0))
    roi_x_range = _number_tuple_param(params.get("cup_fallback_roi_x_range"), (0.18, 0.82))
    roi_y_range = _number_tuple_param(params.get("cup_fallback_roi_y_range"), (0.2, 0.85))
    area_range = _number_tuple_param(params.get("cup_fallback_area_range"), (350.0, 20000.0))
    aspect_range = _number_tuple_param(params.get("cup_fallback_aspect_range"), (0.35, 1.8))

    mask = cv2.inRange(hsv, hsv_lower, hsv_upper)
    roi = np.zeros_like(mask)
    roi[
        int(height * roi_y_range[0]) : int(height * roi_y_range[1]),
        int(width * roi_x_range[0]) : int(width * roi_x_range[1]),
    ] = 255
    mask = cv2.bitwise_and(mask, roi)

    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    target_x = width * target_center_x_ratio
    target_y = height * target_center_y_ratio
    best: tuple[float, float, float, float, str] | None = None
    best_rank: tuple[float, float] | None = None
    for idx in range(1, count):
        x, y, w, h, area = stats[idx]
        if area < area_range[0] or area > area_range[1]:
            continue
        aspect = w / max(float(h), 1.0)
        if aspect < aspect_range[0] or aspect > aspect_range[1]:
            continue
        cx, cy = centroids[idx]
        if cy < height * roi_y_range[0] or cy > height * roi_y_range[1]:
            continue
        dist = ((cx - target_x) ** 2 + (cy - target_y) ** 2) ** 0.5
        rank = (dist, -float(area))
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best = (
                float(cx),
                float(cy),
                float(w),
                float(h),
                f"cup 색상 fallback(area={int(area)}, bbox={int(w)}x{int(h)})",
            )
    return best


def _observe_gripper_target(skill: BaseSkill, params: dict[str, Any]) -> dict[str, Any]:
    from robo_claw_agent.skills.perception_skill.core import (
        _depth_point_for_pixel,
        _fetch_depth_frame,
        _target_pose_metadata,
    )

    target_object = str(params.get("target_object") or "cup").strip()
    detections_topic = str(
        params.get("detections_topic") or "/gripper_object_detector_node/detections"
    )
    min_score = _float_param(params, "min_score", 0.1)
    timeout_sec = _float_param(params, "timeout_sec", 6.0)
    center_tolerance_px = _float_param(
        params, "center_tolerance_px", 100.0 if _is_cup_target(target_object) else 60.0
    )
    center_tolerance_y_px = _float_param(
        params,
        "center_tolerance_y_px",
        35.0 if _is_cup_target(target_object) else center_tolerance_px,
    )
    target_depth_m = _float_param(params, "target_depth_m", 0.25)
    depth_tolerance_m = _float_param(params, "depth_tolerance_m", 0.04)
    target_center_x_ratio = _float_param(params, "target_center_x_ratio", 0.5)
    target_center_y_ratio = _float_param(
        params,
        "target_center_y_ratio",
        0.58 if _is_cup_target(target_object) else 0.5,
    )

    det, class_name, score, error = _best_gripper_detection(
        skill,
        target_object,
        detections_topic=detections_topic,
        min_score=min_score,
        timeout_sec=timeout_sec,
    )
    if det is None:
        fallback = (
            _fallback_cup_center_from_gripper_image(
                skill,
                params,
                target_center_x_ratio=target_center_x_ratio,
                target_center_y_ratio=target_center_y_ratio,
            )
            if _is_cup_target(target_object)
            else None
        )
        if fallback is None:
            return {"success": False, "message": error, "target_object": target_object}
        cx, cy, size_x, size_y, fallback_message = fallback
        class_name = "cup"
        score = 0.0
    else:
        cx = float(det.bbox.center.position.x)
        cy = float(det.bbox.center.position.y)
        size_x = float(det.bbox.size_x)
        size_y = float(det.bbox.size_y)
        fallback_message = ""

    depth_params = dict(params)
    depth_params["camera"] = "gripper"
    depth_frame = _fetch_depth_frame(skill, depth_params)
    image_width = float(params.get("image_width", 640.0))
    image_height = float(params.get("image_height", 480.0))
    if depth_frame is not None:
        image_height = float(depth_frame.depth_img.shape[0])
        image_width = float(depth_frame.depth_img.shape[1])

    depth_point_map, depth_m = _depth_point_for_pixel(skill, depth_frame, depth_params, cx, cy)
    target_cx = image_width * target_center_x_ratio
    target_cy = image_height * target_center_y_ratio
    err_x = cx - target_cx
    err_y = cy - target_cy

    # 컵이 처음부터 화면 아래쪽에 있다면 target_y를 컵 위치로 보정해서
    # 불필요한 리프트 업→다운 움직임을 줄인다.
    # 컵 y가 이미 target 근처(±80px)면 그대로 두고,
    # 컵 y가 target보다 많이 아래(positive err_y > 80)면 target을 컵 위치로 이동.
    if _is_cup_target(target_object) and err_y > 80.0:
        target_cy = cy
        err_y = 0.0
    depth_error = None if depth_m is None else depth_m - target_depth_m
    centered = abs(err_x) <= center_tolerance_px and abs(err_y) <= center_tolerance_y_px
    depth_ready = depth_error is not None and abs(depth_error) <= depth_tolerance_m
    ready = centered and depth_ready

    result: dict[str, Any] = {
        "success": True,
        "message": fallback_message or f"그리퍼 카메라에서 '{class_name}' 관측",
        "target_object": target_object,
        "class_name": class_name,
        "score": round(score, 4),
        "detection_fallback": bool(fallback_message),
        "camera": "gripper",
        "detections_topic": detections_topic,
        "bbox_center": {"x": round(cx, 1), "y": round(cy, 1)},
        "bbox_size": {"x": round(size_x, 1), "y": round(size_y, 1)},
        "image_size": {"width": int(image_width), "height": int(image_height)},
        "image_error_px": {"x": round(err_x, 1), "y": round(err_y, 1)},
        "target_center_px": {"x": round(target_cx, 1), "y": round(target_cy, 1)},
        "center_tolerance_px": {
            "x": round(center_tolerance_px, 1),
            "y": round(center_tolerance_y_px, 1),
        },
        "centered": centered,
        "depth_m": round(depth_m, 3) if depth_m is not None else None,
        "depth_error_m": round(depth_error, 3) if depth_error is not None else None,
        "ready_to_grasp": ready,
    }
    if depth_point_map is not None:
        result["target_pose"] = _target_pose_metadata(skill, depth_point_map)
    return result
