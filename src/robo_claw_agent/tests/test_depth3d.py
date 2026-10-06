"""depth3d 순수 수학 함수 단위 테스트.

depth3d.py 자체는 numpy만 있으면 동작하도록 설계되었지만(image_geometry/
tf2_geometry_msgs는 try/except로 optional 처리), 이를 담고 있는
`perception_skill` 패키지의 `__init__.py`는 sensor_msgs에 의존하는 형제
모듈(detect.py 등)을 임포트하므로 일반적인 패키지 경로로 임포트하면 ROS2
환경(sensor_msgs)이 없는 곳에서 실패한다. 따라서 이 테스트는 패키지
초기화를 우회해 depth3d.py 파일을 직접 로드한다.
"""

import importlib.util
import os

import pytest

_DEPTH3D_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "robo_claw_agent",
    "skills",
    "perception_skill",
    "depth3d.py",
)

try:
    import numpy as np

    _spec = importlib.util.spec_from_file_location(
        "robo_claw_agent_test_depth3d", _DEPTH3D_PATH
    )
    depth3d = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(depth3d)

    DEPTH3D_OK = True
except Exception:  # noqa: BLE001
    depth3d = None
    np = None
    DEPTH3D_OK = False

skip_depth3d = pytest.mark.skipif(not DEPTH3D_OK, reason="numpy 가 필요합니다")

try:
    from image_geometry import PinholeCameraModel
    from sensor_msgs.msg import CameraInfo

    IMAGE_GEOMETRY_OK = True
except ImportError:
    PinholeCameraModel = None
    CameraInfo = None
    IMAGE_GEOMETRY_OK = False

skip_image_geometry = pytest.mark.skipif(
    not IMAGE_GEOMETRY_OK, reason="image_geometry/sensor_msgs 가 필요합니다"
)


class _FakeCameraModel:
    """image_geometry.PinholeCameraModel 없이 deproject_pixel을 테스트하기 위한 스텁."""

    def __init__(self, fx: float, fy: float, cx: float, cy: float):
        self._fx = fx
        self._fy = fy
        self._cx = cx
        self._cy = cy

    def fx(self) -> float:
        return self._fx

    def fy(self) -> float:
        return self._fy

    def cx(self) -> float:
        return self._cx

    def cy(self) -> float:
        return self._cy


class _FakeTfBuffer:
    """transform_point_to_frame의 성공/실패 분기를 검증하기 위한 스텁."""

    def __init__(self, result=None, raise_exc: Exception | None = None):
        self._result = result
        self._raise_exc = raise_exc

    def transform(self, point_stamped, target_frame, timeout=None):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._result


@skip_depth3d
def test_depth_value_to_meters_16uc1_converts_mm_to_m():
    assert depth3d.depth_value_to_meters(1500, "16UC1") == pytest.approx(1.5)


@skip_depth3d
def test_depth_value_to_meters_32fc1_passthrough():
    assert depth3d.depth_value_to_meters(1.5, "32FC1") == pytest.approx(1.5)


@skip_depth3d
def test_depth_value_to_meters_zero_or_nan_returns_none():
    assert depth3d.depth_value_to_meters(0, "16UC1") is None
    assert depth3d.depth_value_to_meters(0.0, "32FC1") is None
    assert depth3d.depth_value_to_meters(float("nan"), "32FC1") is None
    assert depth3d.depth_value_to_meters(float("inf"), "32FC1") is None
    assert depth3d.depth_value_to_meters(None, "32FC1") is None


@skip_depth3d
def test_sample_depth_at_bbox_median_of_window_ignores_invalid():
    # 5x5 depth 패치(16UC1, mm 단위): 유효값과 무효값(0) 혼합
    depth_img = np.array(
        [
            [1000, 1000, 1000, 1000, 1000],
            [1000, 1200, 1200, 1200, 1000],
            [1000, 1200, 0, 1200, 1000],
            [1000, 1200, 1200, 1200, 1000],
            [1000, 1000, 1000, 1000, 1000],
        ],
        dtype=np.uint16,
    )
    result = depth3d.sample_depth_at_bbox(depth_img, "16UC1", u=2, v=2, window=5)
    assert result is not None
    # 0(무효)을 제외한 나머지 값들의 median은 1.0m (1000mm 다수)
    assert result == pytest.approx(1.0, abs=0.01)


@skip_depth3d
def test_sample_depth_at_bbox_all_invalid_returns_none():
    depth_img = np.zeros((5, 5), dtype=np.uint16)
    result = depth3d.sample_depth_at_bbox(depth_img, "16UC1", u=2, v=2, window=5)
    assert result is None


@skip_depth3d
def test_sample_depth_at_bbox_out_of_bounds_returns_none():
    depth_img = np.full((5, 5), 1000, dtype=np.uint16)
    result = depth3d.sample_depth_at_bbox(depth_img, "16UC1", u=100, v=100, window=5)
    assert result is None


@skip_depth3d
def test_deproject_pixel_matches_pinhole_formula():
    model = _FakeCameraModel(fx=500.0, fy=500.0, cx=320.0, cy=240.0)
    x, y, z = depth3d.deproject_pixel(model, u=420.0, v=140.0, depth_m=2.0)
    assert x == pytest.approx((420.0 - 320.0) * 2.0 / 500.0)
    assert y == pytest.approx((140.0 - 240.0) * 2.0 / 500.0)
    assert z == pytest.approx(2.0)


@skip_depth3d
def test_deproject_pixel_at_principal_point_gives_zero_xy():
    model = _FakeCameraModel(fx=600.0, fy=610.0, cx=321.5, cy=241.5)
    x, y, z = depth3d.deproject_pixel(model, u=321.5, v=241.5, depth_m=1.3)
    assert x == pytest.approx(0.0)
    assert y == pytest.approx(0.0)
    assert z == pytest.approx(1.3)


@skip_depth3d
def test_transform_point_to_frame_returns_none_without_tf_buffer():
    result = depth3d.transform_point_to_frame(
        None, (1.0, 2.0, 3.0), "camera_frame", "base_link", stamp=None
    )
    assert result is None


@skip_depth3d
def test_unrotate_pixel_zero_is_identity():
    assert depth3d.unrotate_pixel(123.0, 456.0, 0, 640, 480) == (123.0, 456.0)


@skip_depth3d
def test_unrotate_pixel_90_ccw_swap_maps_top_left_to_raw_top_right():
    # 순수 치수교환(swap) 케이스: raw 640x480 → upright 480x640(W_u=raw_h, H_u=raw_w).
    # upright = CCW 90°(+90). upright (0,0) = raw top-right (639, 0).
    rx, ry = depth3d.unrotate_pixel(0.0, 0.0, 90, 640, 480, up_w=480, up_h=640)
    assert rx == pytest.approx(639.0)
    assert ry == pytest.approx(0.0)


@skip_depth3d
def test_unrotate_pixel_minus_90_cw_swap_maps_top_left_to_raw_bottom_left():
    # 순수 치수교환(swap): upright = CW 90°(-90). upright (0,0) = raw bottom-left (0, 479).
    rx, ry = depth3d.unrotate_pixel(0.0, 0.0, -90, 640, 480, up_w=480, up_h=640)
    assert rx == pytest.approx(0.0)
    assert ry == pytest.approx(479.0)


@skip_depth3d
def test_unrotate_pixel_180_flips_both_axes():
    rx, ry = depth3d.unrotate_pixel(10.0, 20.0, 180, 640, 480, up_w=640, up_h=480)
    assert rx == pytest.approx(639.0 - 10.0)
    assert ry == pytest.approx(479.0 - 20.0)


@skip_depth3d
def test_unrotate_pixel_swap_center_maps_to_raw_center():
    # 회전 불변인 영상 중심은 raw 중심으로 돌아와야 한다(swap 케이스).
    cx_u, cy_u = (480 - 1) / 2.0, (640 - 1) / 2.0
    rx, ry = depth3d.unrotate_pixel(cx_u, cy_u, 90, 640, 480, up_w=480, up_h=640)
    assert rx == pytest.approx((640 - 1) / 2.0)
    assert ry == pytest.approx((480 - 1) / 2.0)


@skip_depth3d
def test_unrotate_pixel_swap_minus_90_inverse_of_forward_cw():
    # raw (40, 30)을 CW 90°(swap)로 upright로 보내면 (x_u=H_r-1-y_r, y_u=x_r) = (449, 40).
    # 이를 역회전(-90)하면 원래 (40, 30)으로 돌아와야 한다.
    raw_x, raw_y = 40.0, 30.0
    x_u = (480 - 1) - raw_y  # 449
    y_u = raw_x              # 40
    rx, ry = depth3d.unrotate_pixel(x_u, y_u, -90, 640, 480, up_w=480, up_h=640)
    assert rx == pytest.approx(raw_x)
    assert ry == pytest.approx(raw_y)


@skip_depth3d
def test_unrotate_pixel_640square_minus_90_real_scenario():
    # 실제 image_rotate 케이스: raw 640x480 → upright 640x640(정사각 패딩).
    # CW 90°(-90). 공식: (rx,ry) = (cy_u, 559 - cx_u).
    # upright 중심 (319.5, 319.5)는 raw 중심 (319.5, 239.5)로 돌아와야 한다.
    rx, ry = depth3d.unrotate_pixel(319.5, 319.5, -90, 640, 480, up_w=640, up_h=640)
    assert rx == pytest.approx(319.5)
    assert ry == pytest.approx(239.5)
    # 컵 upright 픽셀 (360.3, 413.4) → raw (413.4, 198.7)
    rx, ry = depth3d.unrotate_pixel(360.3, 413.4, -90, 640, 480, up_w=640, up_h=640)
    assert rx == pytest.approx(413.4, abs=0.01)
    assert ry == pytest.approx(198.7, abs=0.01)


@skip_depth3d
def test_unrotate_pixel_640square_plus_90_real_scenario():
    # 동일 정사각 패딩, CCW 90°(+90). 공식: (rx,ry) = (639 - cy_u, cx_u - 80).
    # 컵 upright (360.3, 413.4) → raw (225.6, 280.3)
    rx, ry = depth3d.unrotate_pixel(360.3, 413.4, 90, 640, 480, up_w=640, up_h=640)
    assert rx == pytest.approx(225.6, abs=0.01)
    assert ry == pytest.approx(280.3, abs=0.01)


@skip_depth3d
def test_unrotate_pixel_640square_default_heuristic_matches_explicit():
    # up dims 생략 시 90° 휴리스틱 = max(raw) 정사각(640x640)이 명시 전달과 동일해야 한다.
    a = depth3d.unrotate_pixel(360.3, 413.4, -90, 640, 480)
    b = depth3d.unrotate_pixel(360.3, 413.4, -90, 640, 480, up_w=640, up_h=640)
    assert a[0] == pytest.approx(b[0], abs=1e-9)
    assert a[1] == pytest.approx(b[1], abs=1e-9)


@skip_depth3d
def test_unrotate_pixel_clamps_to_raw_bounds():
    # upright 캔버스의 패딩 영역(빈 픽셀)은 raw 범위 밖으로 사상되므로 clamp 돼야 한다.
    # 640x640, -90. upright 좌상단 (0,0) → raw (0, 559). 범위 내.
    rx, ry = depth3d.unrotate_pixel(0.0, 0.0, -90, 640, 480, up_w=640, up_h=640)
    assert 0.0 <= rx <= 639.0
    assert 0.0 <= ry <= 479.0


@skip_depth3d
def test_build_camera_model_returns_none_without_image_geometry(monkeypatch):
    monkeypatch.setattr(depth3d, "_IMAGE_GEOMETRY_AVAILABLE", False)
    assert depth3d.build_camera_model(object()) is None


@skip_depth3d
@skip_image_geometry
def test_build_camera_model_from_camera_info_roundtrip():
    info = CameraInfo()
    info.width = 640
    info.height = 480
    info.k = [500.0, 0.0, 320.0, 0.0, 500.0, 240.0, 0.0, 0.0, 1.0]
    info.p = [500.0, 0.0, 320.0, 0.0, 0.0, 500.0, 240.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    model = depth3d.build_camera_model(info)
    assert model is not None
    assert model.fx() == pytest.approx(500.0)
    assert model.cx() == pytest.approx(320.0)


@skip_depth3d
@skip_image_geometry
def test_deproject_pixel_with_real_pinhole_camera_model():
    info = CameraInfo()
    info.width = 640
    info.height = 480
    info.k = [500.0, 0.0, 320.0, 0.0, 500.0, 240.0, 0.0, 0.0, 1.0]
    info.p = [500.0, 0.0, 320.0, 0.0, 0.0, 500.0, 240.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    model = depth3d.build_camera_model(info)
    x, y, z = depth3d.deproject_pixel(model, u=320.0, v=240.0, depth_m=2.5)
    assert x == pytest.approx(0.0, abs=1e-6)
    assert y == pytest.approx(0.0, abs=1e-6)
    assert z == pytest.approx(2.5)
