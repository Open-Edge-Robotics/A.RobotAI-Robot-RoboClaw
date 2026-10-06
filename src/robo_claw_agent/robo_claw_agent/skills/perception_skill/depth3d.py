"""깊이 이미지(정렬됨) + CameraInfo를 이용한 픽셀→3D 포인트 역투영 및 TF 변환.

CameraInfo 미수신, depth 값 없음/NaN, TF lookup 실패 시 None을 반환하여
호출부(FindObjectSkill/ScanRoomSkill)가 기존 LiDAR-bearing 방식으로 폴백할 수 있게 한다.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

try:
    from image_geometry import PinholeCameraModel

    _IMAGE_GEOMETRY_AVAILABLE = True
except ImportError:
    PinholeCameraModel = None  # type: ignore
    _IMAGE_GEOMETRY_AVAILABLE = False

try:
    import tf2_geometry_msgs  # noqa: F401  (PointStamped 변환 등록용 side-effect import)

    _TF2_GEOMETRY_AVAILABLE = True
except ImportError:
    _TF2_GEOMETRY_AVAILABLE = False


def build_camera_model(camera_info: Any) -> Any:
    """CameraInfo로부터 PinholeCameraModel을 생성한다. image_geometry 미설치 시 None."""
    if not _IMAGE_GEOMETRY_AVAILABLE or camera_info is None:
        return None
    model = PinholeCameraModel()
    model.fromCameraInfo(camera_info)
    return model


def depth_value_to_meters(raw_value: float | None, encoding: str) -> float | None:
    """cv_bridge로 읽은 depth 픽셀 원값을 미터 단위로 변환.

    RealSense aligned depth 인코딩:
    - "16UC1": 정수, 밀리미터 단위 -> *0.001
    - "32FC1": 실수, 이미 미터 단위 -> 그대로
    0 또는 NaN/Inf는 무효 depth로 간주해 None 반환.
    """
    if raw_value is None:
        return None
    if encoding == "16UC1":
        if raw_value <= 0:
            return None
        return float(raw_value) * 0.001
    if encoding == "32FC1":
        if not math.isfinite(raw_value) or raw_value <= 0.0:
            return None
        return float(raw_value)
    logger.warning("[depth3d] Unknown depth encoding: %s (assuming meters)", encoding)
    if not math.isfinite(raw_value) or raw_value <= 0.0:
        return None
    return float(raw_value)


def sample_depth_at_bbox(
    depth_img: np.ndarray,
    encoding: str,
    u: float,
    v: float,
    window: int = 5,
) -> float | None:
    """bbox 중심 (u,v) 주변 window x window 픽셀에서 median depth를 미터 단위로 샘플링.

    중심 픽셀 하나만 쓰면 물체 가장자리/노이즈에 취약하므로, 작은 정사각형 창의
    유효(0/NaN 아님) 값들의 median을 사용한다.
    """
    h, w = depth_img.shape[:2]
    half = window // 2
    u_i, v_i = int(round(u)), int(round(v))
    u0, u1 = max(0, u_i - half), min(w, u_i + half + 1)
    v0, v1 = max(0, v_i - half), min(h, v_i + half + 1)
    if u0 >= u1 or v0 >= v1:
        return None
    patch = depth_img[v0:v1, u0:u1].astype(np.float64).flatten()

    valid = []
    for raw in patch:
        m = depth_value_to_meters(raw, encoding)
        if m is not None:
            valid.append(m)
    if not valid:
        return None
    return float(np.median(valid))


def deproject_pixel(
    camera_model: Any,
    u: float,
    v: float,
    depth_m: float,
) -> tuple[float, float, float]:
    """픽셀 (u,v) + depth(m)를 카메라 광학 프레임의 3D 포인트로 역투영.

    X = (u - cx) * Z / fx
    Y = (v - cy) * Z / fy
    Z = depth_m
    (depth 이미지 값은 광학축 수직거리(Z)이지 ray를 따른 거리(range)가 아니므로,
    image_geometry의 projectPixelTo3dRay가 반환하는 정규화된 ray는 쓰지 않고
    cx/cy/fx/fy 원값을 직접 사용한다.)
    """
    cx, cy = camera_model.cx(), camera_model.cy()
    fx, fy = camera_model.fx(), camera_model.fy()
    x = (u - cx) * depth_m / fx
    y = (v - cy) * depth_m / fy
    return (x, y, depth_m)


def unrotate_pixel(
    cx_u: float,
    cy_u: float,
    rotate_deg: float,
    raw_w: int,
    raw_h: int,
    up_w: int = 0,
    up_h: int = 0,
) -> tuple[float, float]:
    """upright(회전 보정된) 영상 픽셀 (cx_u, cy_u) → 원본 raw 영상 픽셀 (raw_x, raw_y).

    헤드 카메라(D435i)가 세로 마운트라 ``upright_rotater``(image_rotate)가 raw 영상을
    ``rotate_deg`` 만큼 회전해 upright 스트림을 만들고, ``object_detector_node``는 그
    upright 영상에서 bbox를 뽑는다. 반면 depth/camera_info는 raw 그대로이므로, depth
    샘플링/역투영 전에 upright 픽셀을 raw 픽셀로 되돌려야 좌표계가 일치한다.

    모델: raw 중심 (rcx,rcy) 기준 회전 → upright 캔버스 중심 (ucx,ucy) 기준 배치.
    OpenCV y-down 규약(양수 각 = 시각적 CCW = getRotationMatrix2D)의 중심 회전.

      정방향: p_u = up_center + R(a)·(p_r − raw_center)
      역방향: p_r = raw_center + R(−a)·(p_u − up_center)
        R(a)·(dx,dy) = (cos·dx + sin·dy, −sin·dx + cos·dy)

    ``rotate_deg`` = raw에 가한 회전각(도, 양수=CCW 시각적). 0이면 그대로.
    ``raw_w``,``raw_h`` = raw(depth) 폭/높이. ``up_w``,``up_h`` = upright 캔버스 폭/높이
    (0이면 90° 근처는 max(raw) 정사각, 그 외는 raw dims로 추정 — image_rotate가 정사각
    캔버스에 패딩하는 일반 케이스; raw 640×480 → upright 640×640).

    정확한 회전 방향(부호)은 ``upright_rotater`` 설정과 일치해야 하며, 로봇에서 알려진
    위치의 픽셀로 depth 거리가 실제와 맞는지 검증해 확정한다.
    """
    if rotate_deg == 0:
        return float(cx_u), float(cy_u)

    deg = float(rotate_deg)
    norm = ((deg + 180.0) % 360.0) - 180.0  # -180..180
    if up_w <= 0 or up_h <= 0:
        if 45.0 < abs(norm) < 135.0:
            m = max(int(raw_w), int(raw_h))
            up_w, up_h = m, m
        else:
            up_w, up_h = int(raw_w), int(raw_h)

    rcx, rcy = (float(raw_w) - 1.0) / 2.0, (float(raw_h) - 1.0) / 2.0
    ucx, ucy = (float(up_w) - 1.0) / 2.0, (float(up_h) - 1.0) / 2.0
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    dx, dy = float(cx_u) - ucx, float(cy_u) - ucy
    # 역회전 R(-a): (c·dx − s·dy, s·dx + c·dy)
    rx = rcx + c * dx - s * dy
    ry = rcy + s * dx + c * dy
    rx = max(0.0, min(float(raw_w) - 1.0, rx))
    ry = max(0.0, min(float(raw_h) - 1.0, ry))
    return rx, ry


def transform_point_to_frame(
    tf_buffer: Any,
    point_xyz: tuple[float, float, float],
    source_frame: str,
    target_frame: str,
    stamp: Any,
    timeout_sec: float = 0.5,
) -> tuple[float, float, float] | None:
    """카메라 광학 프레임의 3D 포인트를 target_frame(base_link 등)으로 TF 변환.

    tf2_geometry_msgs의 PointStamped 변환 등록을 이용해 tf_buffer.transform()을 호출한다.
    실패(예외/timeout) 시 None을 반환한다.
    """
    if tf_buffer is None or not _TF2_GEOMETRY_AVAILABLE:
        return None
    try:
        from geometry_msgs.msg import PointStamped
        from rclpy.duration import Duration

        ps = PointStamped()
        ps.header.frame_id = source_frame
        ps.header.stamp = stamp
        ps.point.x, ps.point.y, ps.point.z = point_xyz
        transformed = tf_buffer.transform(
            ps, target_frame, timeout=Duration(seconds=timeout_sec)
        )
        return (transformed.point.x, transformed.point.y, transformed.point.z)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[depth3d] TF transform failed (%s -> %s): %s", source_frame, target_frame, exc
        )
        return None
