import pytest

pytest.importorskip("cv2")
pytest.importorskip("nav_msgs")

"""
GetMapVisualSkill 단위 테스트
"""

import os
from unittest.mock import MagicMock

import cv2
import pytest
from nav_msgs.msg import OccupancyGrid
from robo_claw_agent.skills.map_skill import GetMapVisualSkill
from robo_claw_agent.skills.map_skill._grid_image import UNKNOWN_VALUE


@pytest.fixture
def skill():
    # BaseSkill.__init__은 인자를 받지 않음
    s = GetMapVisualSkill()
    return s


def test_get_map_visual_success(skill, tmp_path):
    msg = OccupancyGrid()
    msg.info.width = 10
    msg.info.height = 10
    msg.info.resolution = 0.1
    msg.info.origin.position.x = -0.5
    msg.info.origin.position.y = -0.5

    # 데이터: 0(Free), 100(Occupied), -1(Unknown) 혼합
    data = [0] * 100
    data[0] = 100  # 좌하단 장애물
    data[99] = -1  # 우상단 미탐사
    msg.data = data

    skill.wait_for_message = MagicMock(return_value=msg)

    output_file = str(tmp_path / "test_map.png")
    params = {"output_path": output_file}
    result = skill.execute(params)

    assert result["success"] is True
    assert "image_base64" in result
    assert os.path.exists(output_file)
    assert result["map_metadata"]["width"] == 10
    assert result["map_metadata"]["resolution"] == 0.1

    # 이미지 파일 확인 (opencv로 다시 읽기)
    img = cv2.imread(output_file, cv2.IMREAD_GRAYSCALE)
    assert img.shape == (10, 10)

    # 상하 반전되었으므로:
    # 원본 data[0] (좌하단) -> reshape 후 [0,0] -> flip(0) 후 [9,0]
    # 원본 data[99] (우상단) -> reshape 후 [9,9] -> flip(0) 후 [0,9]
    assert img[9, 0] == 0      # flip된 장애물 (좌하단)
    assert img[0, 9] == UNKNOWN_VALUE    # flip된 미탐사 (우상단)

def test_get_map_visual_timeout(skill):
    # wait_for_message가 None을 반환하도록 설정
    skill.wait_for_message = MagicMock(return_value=None)

    result = skill.execute({})

    assert result["success"] is False
    assert "수신할 수 없습니다" in result["message"]
