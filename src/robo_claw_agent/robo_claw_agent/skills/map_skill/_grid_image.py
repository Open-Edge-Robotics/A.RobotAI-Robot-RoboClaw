"""OccupancyGrid -> 이미지 변환 및 좌표 매핑 공용 유틸리티.

visual.py / capture.py / annotate.py가 공통으로 사용하는 3단계(흑백) 맵 이미지 생성 로직과,
탐사 영역으로 크롭된 이미지와 원본 grid 좌표 간의 매핑을 제공한다.
"""

from dataclasses import dataclass

import cv2
import numpy as np

FREE_VALUE = 255
OCCUPIED_VALUE = 0
UNKNOWN_VALUE = 128  # 명확한 중립 회색: occupied(0, 검정) 및 free(255, 흰색)와 명확히 구분되는 "미탐사/이동 불가" 영역


@dataclass
class CropInfo:
    """크롭 전 원본 grid 기준 오프셋과 크롭/리사이즈 정보."""

    row_offset: int
    col_offset: int
    crop_height: int  # 크롭된(리사이즈 전) grid 높이
    crop_width: int  # 크롭된(리사이즈 전) grid 너비
    scale: float  # 크롭 이미지 -> 최종 이미지 리사이즈 배율
    full_width: int
    full_height: int


def build_map_image(
    data: np.ndarray, max_dim: int = 1024, margin_cells: int = 30
) -> tuple[np.ndarray, CropInfo]:
    """OccupancyGrid 2D 배열(-1=unknown, 0=free, >50=occupied)을 3단계 흑백 이미지로 변환.

    탐사된(free/occupied) 영역의 bounding box(+margin)로 크롭해 불필요한 미탐사 여백을
    제거한 뒤, ROS -> 이미지 좌표계 변환을 위해 상하 반전하고 필요 시 리사이즈한다.
    """
    height, width = data.shape
    data_int = data.astype(np.int16)

    img = np.full((height, width), UNKNOWN_VALUE, dtype=np.uint8)
    img[(data_int >= 0) & (data_int <= 25)] = FREE_VALUE
    img[data_int > 50] = OCCUPIED_VALUE
    img[(data_int < 0) | (data_int > 100)] = UNKNOWN_VALUE

    explored_rows, explored_cols = np.where((data_int >= 0) & (data_int <= 100))
    if len(explored_rows) == 0:
        row_offset, col_offset = 0, 0
        crop_height, crop_width = height, width
    else:
        # On small maps the nominal margin must not consume the entire map;
        # otherwise a sparse explored region cannot be cropped at all.
        effective_margin = min(margin_cells, max(1, min(height, width) // 4))
        r0 = max(0, int(explored_rows.min()) - effective_margin)
        r1 = min(height - 1, int(explored_rows.max()) + effective_margin)
        c0 = max(0, int(explored_cols.min()) - effective_margin)
        c1 = min(width - 1, int(explored_cols.max()) + effective_margin)
        img = img[r0 : r1 + 1, c0 : c1 + 1]
        row_offset, col_offset = r0, c0
        crop_height, crop_width = img.shape

    # ROS 맵 좌표계 -> 이미지 좌표계 변환을 위한 상하 반전
    img = cv2.flip(img, 0)

    scale = 1.0
    if max(crop_width, crop_height) > max_dim:
        scale = max_dim / max(crop_width, crop_height)
        new_size = (int(crop_width * scale), int(crop_height * scale))
        img = cv2.resize(img, new_size, interpolation=cv2.INTER_AREA)

    crop = CropInfo(
        row_offset=row_offset,
        col_offset=col_offset,
        crop_height=crop_height,
        crop_width=crop_width,
        scale=scale,
        full_width=width,
        full_height=height,
    )
    return img, crop


def nearest_free_cell(
    free_mask: np.ndarray, row: int, col: int, radius: int = 10
) -> "tuple[int, int] | None":
    """(row, col) 주변 radius 셀 내에서 가장 가까운 free 셀 좌표를 찾는다. 없으면 None."""
    height, width = free_mask.shape
    if 0 <= row < height and 0 <= col < width and free_mask[row, col]:
        return (row, col)
    for rad in range(1, radius + 1):
        r0, r1 = max(0, row - rad), min(height - 1, row + rad)
        c0, c1 = max(0, col - rad), min(width - 1, col + rad)
        sub = free_mask[r0 : r1 + 1, c0 : c1 + 1]
        hits = np.argwhere(sub)
        if len(hits) > 0:
            hr, hc = hits[0]
            return (r0 + int(hr), c0 + int(hc))
    return None


def grid_to_image_xy(row: float, col: float, crop: CropInfo) -> tuple[int, int]:
    """원본(크롭 전) grid 연속 좌표(row, col) -> 최종(크롭+리사이즈+flip 적용) 이미지 픽셀 좌표."""
    row_in_crop = row - crop.row_offset
    col_in_crop = col - crop.col_offset
    cx = int(col_in_crop * crop.scale)
    cy = int(((crop.crop_height - 1) - row_in_crop) * crop.scale)
    return cx, cy
