import pytest

pytest.importorskip("cv2")
pytest.importorskip("nav_msgs")

import os
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest
from nav_msgs.msg import OccupancyGrid
from robo_claw_agent.skills.map_skill import CaptureMapSkill
from robo_claw_agent.skills.map_skill._grid_image import UNKNOWN_VALUE
from robo_claw_agent.skills.vision_skill import CaptureCameraImageSkill
from sensor_msgs.msg import Image as ROSImage


@pytest.fixture
def camera_skill():
    return CaptureCameraImageSkill()


@pytest.fixture
def map_skill():
    return CaptureMapSkill()


def test_capture_camera_image_success(camera_skill, tmp_path):
    msg = ROSImage()
    msg.width = 100
    msg.height = 100
    msg.encoding = "bgr8"
    msg.step = 300

    # 픽셀값이 120인 회색 이미지 생성
    img_data = np.full((100, 100, 3), 120, dtype=np.uint8)
    msg.data = img_data.tobytes()

    camera_skill.wait_for_message = MagicMock(return_value=msg)

    # execute 내부에서 /tmp 경로에 저장하지만, 테스트 시에도 /tmp에 파일이 잘 생성되는지 검증 가능함
    result = camera_skill.execute({})

    assert result["success"] is True
    assert "image_base64" in result
    assert "file_path" in result

    file_path = result["file_path"]
    assert os.path.exists(file_path)

    # 이미지 파일 내용 확인
    img = cv2.imread(file_path)
    assert img.shape == (100, 100, 3)
    assert np.all(img == 120)

    # 임시 파일 삭제
    if os.path.exists(file_path):
        os.remove(file_path)


def test_capture_camera_image_timeout(camera_skill):
    camera_skill.wait_for_message = MagicMock(return_value=None)

    result = camera_skill.execute({})

    assert result["success"] is False
    assert "수신하지 못했습니다" in result["message"]


def test_capture_map_success(map_skill, tmp_path):
    msg = OccupancyGrid()
    msg.info.width = 10
    msg.info.height = 10
    msg.info.resolution = 0.1
    msg.info.origin.position.x = -0.5
    msg.info.origin.position.y = -0.5

    # 데이터: 0(Free), 100(Occupied), -1(Unknown)
    data = [0] * 100
    data[0] = 100  # 좌하단 장애물
    data[99] = -1  # 우상단 미탐사
    msg.data = data

    map_skill.wait_for_message = MagicMock(return_value=msg)

    output_file = str(tmp_path / "test_capture_map.png")
    params = {"output_path": output_file}
    result = map_skill.execute(params)

    assert result["success"] is True
    assert "image_base64" in result
    assert os.path.exists(output_file)
    assert result["map_metadata"]["width"] == 10
    assert result["map_metadata"]["resolution"] == 0.1

    # 이미지 파일 확인 (opencv로 다시 읽기)
    img = cv2.imread(output_file, cv2.IMREAD_GRAYSCALE)
    assert img.shape == (10, 10)

    # 상하 반전 검증:
    # 원본 data[0] (좌하단) -> flip 후 [9, 0] (장애물=0)
    # 원본 data[99] (우상단) -> flip 후 [0, 9] (미탐사=UNKNOWN_VALUE)
    assert img[9, 0] == 0
    assert img[0, 9] == UNKNOWN_VALUE

    if os.path.exists(output_file):
        os.remove(output_file)


def test_capture_map_timeout(map_skill):
    map_skill.wait_for_message = MagicMock(return_value=None)

    result = map_skill.execute({})

    assert result["success"] is False
    assert "수신할 수 없습니다" in result["message"]


def test_capture_map_crops_to_explored_area(map_skill, tmp_path):
    # 30x30 맵에서 중앙의 6x6 영역만 탐사되고 나머지는 전부 미탐사(-1)인 경우,
    # 결과 이미지는 미탐사 여백을 제외하고 탐사 영역 근방으로 크롭되어야 한다.
    size = 30
    msg = OccupancyGrid()
    msg.info.width = size
    msg.info.height = size
    msg.info.resolution = 0.1
    msg.info.origin.position.x = -1.5
    msg.info.origin.position.y = -1.5

    data = [-1] * (size * size)
    for r in range(10, 16):
        for c in range(10, 16):
            data[r * size + c] = 0
    data[10 * size + 10] = 100  # 탐사 영역 안에 장애물 하나
    msg.data = data

    map_skill.wait_for_message = MagicMock(return_value=msg)

    output_file = str(tmp_path / "test_capture_map_crop.png")
    result = map_skill.execute({"output_path": output_file})

    assert result["success"] is True
    crop_meta = result["map_metadata"]["crop"]
    # 탐사 영역(6x6) + 여백만큼만 남고, 원본(30x30)보다 훨씬 작아야 한다
    assert crop_meta["width"] < size
    assert crop_meta["height"] < size

    img = cv2.imread(output_file, cv2.IMREAD_GRAYSCALE)
    assert img.shape == (crop_meta["height"], crop_meta["width"])

    if os.path.exists(output_file):
        os.remove(output_file)
