import pytest

pytest.importorskip("cv2")
pytest.importorskip("nav_msgs")

import os
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest
from nav_msgs.msg import OccupancyGrid
from robo_claw_agent.skills.map_skill import AnnotateMapSkill
from robo_claw_agent.skills.map_skill._grid_image import CropInfo


@pytest.fixture
def annotate_skill():
    return AnnotateMapSkill()


def test_annotate_markers_coordinate_conversion(annotate_skill, tmp_path):
    msg = OccupancyGrid()
    msg.info.width = 10
    msg.info.height = 10
    msg.info.resolution = 0.1
    msg.info.origin.position.x = -0.5
    msg.info.origin.position.y = -0.5

    # 데이터: 전부 Free(0)
    msg.data = [0] * 100

    # wait_for_message 모킹
    annotate_skill.wait_for_message = MagicMock(return_value=msg)

    markers = [
        {"name": "BottomLeft", "x": -0.5, "y": -0.5},
        {"name": "TopRight", "x": 0.4, "y": 0.4},
    ]

    output_file = str(tmp_path / "test_annotated_map.png")
    params = {
        "markers": markers,
        "output_path": output_file,
    }

    result = annotate_skill.execute(params)

    assert result["success"] is True
    assert os.path.exists(output_file)

    detected = result["detected_places"]
    assert len(detected) == 2

    # BottomLeft: gx=0, gy=0 -> cx=0, cy=9
    # TopRight: gx=9, gy=9 -> cx=9, cy=0
    assert detected[0]["name"] == "BottomLeft"
    assert detected[0]["world_x"] == pytest.approx(-0.5)
    assert detected[0]["world_y"] == pytest.approx(-0.5)

    assert detected[1]["name"] == "TopRight"
    assert detected[1]["world_x"] == pytest.approx(0.4)
    assert detected[1]["world_y"] == pytest.approx(0.4)

    # 리사이징 및 플립이 적용된 이미지에서 원 색상 검증
    img = cv2.imread(output_file)
    # BottomLeft 마커는 (cx=0, cy=9) 위치에 그려짐
    # (0, 9) 근방 픽셀이 마킹 색상인 빨간색 (BGR: 0, 0, 255) 혹은 하얀색(255,255,255) 테두리인지 검증
    # _draw_marker에서 circle 반경이 12이므로 중심부는 빨간색
    assert np.any(img[9, 0] == [0, 0, 255]) or np.any(img[9, 0] == [255, 255, 255])
    # TopRight 마커는 (cx=9, cy=0) 위치에 그려짐
    assert np.any(img[0, 9] == [0, 0, 255]) or np.any(img[0, 9] == [255, 255, 255])

    if os.path.exists(output_file):
        os.remove(output_file)


def test_annotate_markers_accepts_label_alias(annotate_skill, tmp_path):
    """LLM이 'label' 키로 장소명을 전달해도 'name'처럼 처리되어야 한다.

    실제 로그에서 'label': '충전대'로 전달됐으나 name만 읽어 'Point'로
    저장되는 버그가 있었음.
    """
    msg = OccupancyGrid()
    msg.info.width = 10
    msg.info.height = 10
    msg.info.resolution = 0.1
    msg.info.origin.position.x = -0.5
    msg.info.origin.position.y = -0.5
    msg.data = [0] * 100

    annotate_skill.wait_for_message = MagicMock(return_value=msg)

    output_file = str(tmp_path / "test_label_map.png")
    params = {
        "markers": [{"label": "충전대", "x": -0.14, "y": -0.1}],
        "output_path": output_file,
    }

    result = annotate_skill.execute(params)

    assert result["success"] is True
    detected = result["detected_places"]
    assert len(detected) == 1
    assert detected[0]["name"] == "충전대"
    assert detected[0]["world_x"] == pytest.approx(-0.14)
    assert detected[0]["world_y"] == pytest.approx(-0.1)

    if os.path.exists(output_file):
        os.remove(output_file)


def test_vlm_parse_coordinate_reconstruction(annotate_skill):
    # 가상의 OccupancyGrid 메타데이터 준비 (10x10)
    msg = OccupancyGrid()
    msg.info.width = 10
    msg.info.height = 10
    msg.info.resolution = 0.1
    msg.info.origin.position.x = -0.5
    msg.info.origin.position.y = -0.5
    msg.data = [0] * 100

    # VLM이 예측한 객체들 JSON (상대적 위치 x_rel, y_rel)
    # 이미지 상에서 (x_rel=0.0, y_rel=1.0) -> 상하 반전된 이미지의 최하단 좌측 -> 실제 맵의 (0, 0) 픽셀 -> 월드 (-0.5, -0.5)
    # 이미지 상에서 (x_rel=0.9, y_rel=0.0) -> 상하 반전된 이미지의 최상단 우측 -> 실제 맵의 (9, 9) 픽셀 -> 월드 (0.4, 0.4)
    vlm_json_text = """
    OBJECTS: [
        {"name": "VlmBottomLeft", "x_rel": 0.0, "y_rel": 1.0},
        {"name": "VlmTopRight", "x_rel": 0.9, "y_rel": 0.1}
    ]
    """

    img = np.full((10, 10, 3), 127, dtype=np.uint8)
    free_mask = np.ones((10, 10), dtype=bool)  # 전부 free
    crop = CropInfo(
        row_offset=0, col_offset=0, crop_height=10, crop_width=10,
        scale=1.0, full_width=10, full_height=10,
    )

    detected = annotate_skill._parse_vlm_objects(
        vlm_json_text, img, msg.info, free_mask, crop
    )

    assert len(detected) == 2

    # VlmBottomLeft 검증: x_rel=0.0 -> cx=0 -> gx=0 -> wx=-0.5
    # y_rel=1.0 -> cy=9 -> cy_unflipped=0 -> gy=0 -> wy=-0.5
    assert detected[0]["name"] == "VlmBottomLeft"
    assert detected[0]["world_x"] == pytest.approx(-0.5)
    assert detected[0]["world_y"] == pytest.approx(-0.5)

    # VlmTopRight 검증: x_rel=0.9 -> cx=9 -> gx=9 -> wx=0.4
    # y_rel=0.1 -> cy=1 -> cy_unflipped=9 -> gy=9 -> wy=0.4
    assert detected[1]["name"] == "VlmTopRight"
    assert detected[1]["world_x"] == pytest.approx(0.4)
    assert detected[1]["world_y"] == pytest.approx(0.4)

    # free 영역이므로 스냅되지 않아야 한다
    assert detected[0]["snapped"] is False
    assert detected[1]["snapped"] is False


def test_vlm_point_on_obstacle_snaps_to_nearest_free_cell(annotate_skill):
    # 10x10 맵: (row=5, col=5)만 장애물, 나머지는 전부 free
    msg = OccupancyGrid()
    msg.info.width = 10
    msg.info.height = 10
    msg.info.resolution = 0.1
    msg.info.origin.position.x = -0.5
    msg.info.origin.position.y = -0.5

    free_mask = np.ones((10, 10), dtype=bool)
    free_mask[5, 5] = False

    crop = CropInfo(
        row_offset=0, col_offset=0, crop_height=10, crop_width=10,
        scale=1.0, full_width=10, full_height=10,
    )
    img = np.full((10, 10, 3), 127, dtype=np.uint8)

    # x_rel=0.55 -> orig_col=5.5 -> col=5 / y_rel=0.45 -> orig_row=(1-0.45)*10=5.5 -> row=5
    vlm_json_text = """
    OBJECTS: [{"name": "OnObstacle", "x_rel": 0.55, "y_rel": 0.45}]
    """

    detected = annotate_skill._parse_vlm_objects(
        vlm_json_text, img, msg.info, free_mask, crop
    )

    assert len(detected) == 1
    assert detected[0]["snapped"] is True
    # (row=5,col=5) 주변에서 가장 먼저 발견되는 free 셀은 (row=4,col=4)
    assert detected[0]["world_x"] == pytest.approx(-0.05)
    assert detected[0]["world_y"] == pytest.approx(-0.05)


def test_vlm_point_far_from_free_area_is_discarded(annotate_skill):
    # 10x10 맵 전체가 미탐사(unknown)라 free 셀이 하나도 없는 경우
    msg = OccupancyGrid()
    msg.info.width = 10
    msg.info.height = 10
    msg.info.resolution = 0.1
    msg.info.origin.position.x = -0.5
    msg.info.origin.position.y = -0.5

    free_mask = np.zeros((10, 10), dtype=bool)
    crop = CropInfo(
        row_offset=0, col_offset=0, crop_height=10, crop_width=10,
        scale=1.0, full_width=10, full_height=10,
    )
    img = np.full((10, 10, 3), 127, dtype=np.uint8)

    vlm_json_text = """
    OBJECTS: [{"name": "InUnknownVoid", "x_rel": 0.5, "y_rel": 0.5}]
    """

    detected = annotate_skill._parse_vlm_objects(
        vlm_json_text, img, msg.info, free_mask, crop
    )

    assert detected == []
