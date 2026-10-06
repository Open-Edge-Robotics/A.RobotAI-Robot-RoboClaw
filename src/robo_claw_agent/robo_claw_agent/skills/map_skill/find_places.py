"""
이동 가능한 장소 탐색 스킬 — occupancy grid 전체를 수치 분석해
로봇에서 도달 가능한 개방 공간 후보를 찾아 시맨틱 맵에 등록하고 시각화한다.
"""

import logging
import math
from collections import deque
from typing import Any

import cv2
import numpy as np
from nav_msgs.msg import OccupancyGrid  # type: ignore[import]

from robo_claw_agent.skill_manager import BaseSkill

from ._grid_image import nearest_free_cell

logger = logging.getLogger(__name__)

# BFS/후보 정렬 비용을 제한하기 위한 셀 상한 (초과 시 다운샘플)
_MAX_CELLS = 1_000_000
# 정렬 대상 후보 셀 상한
_MAX_SORT_CANDIDATES = 8000


class FindReachablePlacesSkill(BaseSkill):
    """맵 전체를 분석해 도달 가능한 개방 공간 후보를 찾는다."""

    name = "find_reachable_places"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "map_topic": {"type": "string", "default": "/map"},
            "max_places": {"type": "integer", "default": 5},
            "min_openness_m": {"type": "number", "default": 0.3},
            "min_separation_m": {"type": "number", "default": 1.5},
            "max_openness_m": {"type": "number", "default": 3.0},
        },
        "additionalProperties": False,
    }
    description = (
        "전역 맵(/map)을 수치 분석하여 로봇이 실제로 도달 가능한 '넓고 개방된 이동 후보 장소'들을 "
        "찾아 시맨틱 맵에 등록하고, 맵 이미지에 마킹하여 반환합니다. "
        "각 후보는 등록된 이름(예: '이동가능지점1')으로 이후 navigate_to에 사용할 수 있습니다. "
        "파라미터: max_places(기본 5), min_openness_m(기본 0.3), min_separation_m(기본 1.5). "
        "특정 단일 좌표의 통과 가능 여부만 확인하려면 analyze_map을 사용하세요."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        map_topic = self.get_string_param(params, "map_topic", "/map")
        max_places = int(params.get("max_places", 5))
        min_openness_m = float(params.get("min_openness_m", 0.3))
        min_separation_m = float(params.get("min_separation_m", 1.5))
        max_openness_m = float(params.get("max_openness_m", 3.0))

        map_msg = self.wait_for_message(
            OccupancyGrid,
            map_topic,
            timeout_sec=10.0,
            use_transient_local=True,
            max_age_sec=10.0,
        )
        if not map_msg:
            return {
                "success": False,
                "message": f"맵 데이터를 수신하지 못했습니다. (topic={map_topic})",
            }
        frame_ok, frame_error = self.validate_message_frame(
            map_msg, getattr(self.node, "_map_frame", "map"), "OccupancyGrid"
        )
        if not frame_ok:
            return {"success": False, "message": frame_error}

        info = getattr(map_msg, "info", None)
        if not info:
            return {"success": False, "message": "OccupancyGrid info가 올바르지 않습니다."}

        width = getattr(info, "width", 0)
        height = getattr(info, "height", 0)
        res = getattr(info, "resolution", 0.0)
        data_raw = getattr(map_msg, "data", [])

        if width <= 0 or height <= 0 or res <= 0.0 or len(data_raw) != width * height:
            return {
                "success": False,
                "message": (
                    f"비정상적인 OccupancyGrid 데이터: {width}x{height} (res={res}), "
                    f"데이터 길이({len(data_raw)})가 일치하지 않습니다."
                ),
            }

        origin_pos = getattr(getattr(info, "origin", None), "position", None)
        origin_x = getattr(origin_pos, "x", 0.0) if origin_pos else 0.0
        origin_y = getattr(origin_pos, "y", 0.0) if origin_pos else 0.0
        data = np.array(data_raw, dtype=np.int16).reshape((height, width))

        # 큰 맵은 다운샘플하여 비용 제한 (월드 변환은 유효 해상도 res_eff 사용)
        factor = 1
        if width * height > _MAX_CELLS:
            factor = int(math.ceil(math.sqrt((width * height) / _MAX_CELLS)))
            data = data[::factor, ::factor]
            height, width = data.shape
        res_eff = res * factor

        free = data == 0
        if not free.any():
            return {
                "success": False,
                "message": "실내 건물 맵에 이동 가능한(free) 영역이 없습니다. 맵이 비어있거나 미탐사 상태입니다.",
            }

        # 탐사 영역(data >= 0 및 <= 100)의 바운딩 박스 제한 (맵 최외곽 미탐사/외곽 해자 영역 제외)
        explored_mask = (data >= 0) & (data <= 100)
        exp_rows, exp_cols = np.where(explored_mask)
        in_bbox = np.zeros((height, width), dtype=bool)
        if len(exp_rows) > 0:
            in_bbox[exp_rows.min() : exp_rows.max() + 1, exp_cols.min() : exp_cols.max() + 1] = True
        else:
            in_bbox[:] = True

        # free_u8 구성: 이미지 최외곽 테두리 픽셀 및 탐사 영역 밖은 non-free(0)로 간주
        free_u8 = np.zeros((height, width), dtype=np.uint8)
        free_u8[free & in_bbox] = 1
        border_w = max(5, min(width // 10, height // 10))
        free_u8[:border_w, :] = 0
        free_u8[-border_w:, :] = 0
        free_u8[:, :border_w] = 0
        free_u8[:, -border_w:] = 0

        # 개방도: 각 free 셀에서 가장 가까운 non-free(장애물/미탐사/경계)까지 거리(m)
        openness_m = cv2.distanceTransform(free_u8, cv2.DIST_L2, 5) * res_eff

        # 로봇에서 도달 가능한 free 영역 (map 프레임일 때만 BFS, 아니면 전체 free)
        pose = self.get_map_pose()
        reachable = self._reachable_mask(free & in_bbox, pose, origin_x, origin_y, res_eff)

        # 후보: 도달 가능 & 적정 개방도 범위(min_openness_m <= openness <= max_openness_m) & 탐사 바운딩 박스 내부
        cand_mask = (
            reachable & (openness_m >= min_openness_m) & (openness_m <= max_openness_m) & in_bbox
        )
        rows, cols = np.where(cand_mask)
        if len(rows) == 0:
            # max_openness_m 완화 후 재시도
            cand_mask = reachable & (openness_m >= min_openness_m) & in_bbox
            rows, cols = np.where(cand_mask)

        if len(rows) == 0:
            return {
                "success": False,
                "message": (
                    f"도달 가능한 개방 공간 후보를 찾지 못했습니다 "
                    f"(min_openness_m={min_openness_m}). 값을 낮춰 다시 시도해 보세요."
                ),
            }

        # 개방도 높은 순으로 정렬, 최소 간격을 두고 max_places개 선택
        scores = openness_m[rows, cols]
        order = np.argsort(-scores)[:_MAX_SORT_CANDIDATES]
        selected: list[tuple[float, float, float]] = []  # (wx, wy, openness)
        sep_sq = min_separation_m * min_separation_m
        for idx in order:
            r, c = int(rows[idx]), int(cols[idx])
            wx = origin_x + (c + 0.5) * res_eff
            wy = origin_y + (r + 0.5) * res_eff
            if all((wx - sx) ** 2 + (wy - sy) ** 2 >= sep_sq for sx, sy, _ in selected):
                selected.append((wx, wy, float(scores[idx])))
                if len(selected) >= max_places:
                    break

        # 시맨틱 맵 등록
        places: list[dict[str, Any]] = []
        memory = getattr(self.node, "_memory", None) if self.node else None
        for i, (wx, wy, openness) in enumerate(selected, start=1):
            place_name = f"이동가능지점{i}"
            places.append(
                {
                    "name": place_name,
                    "x": round(wx, 3),
                    "y": round(wy, 3),
                    "openness_m": round(openness, 2),
                }
            )
            if memory is not None:
                try:
                    memory.add_object_location(
                        place_name,
                        wx,
                        wy,
                        metadata={
                            "source": "find_reachable_places",
                            "kind": "place",
                            "frame_id": "map",
                            "openness_m": round(openness, 2),
                        },
                        aliases=[str(i), f"Place {i}", f"이동지점{i}"],
                    )
                except Exception as exc:
                    logger.warning(
                        "[find_reachable_places] Registration failed (%s): %s", place_name, exc
                    )

        # 맵에 마킹하여 시각화 (annotate_map 재사용)
        file_path = None
        try:
            from .annotate import AnnotateMapSkill

            annotator = AnnotateMapSkill()
            annotator.set_node(self.node)
            ann = annotator.execute(
                {
                    "map_topic": map_topic,
                    "markers": [{"name": p["name"], "x": p["x"], "y": p["y"]} for p in places],
                }
            )
            file_path = ann.get("file_path")
        except Exception as exc:
            logger.warning("[find_reachable_places] Visualization failed: %s", exc)

        names = ", ".join(p["name"] for p in places) or "없음"
        result = {
            "success": True,
            "message": f"이동 가능한 장소 {len(places)}곳을 찾았습니다: {names}",
            "places": places,
        }
        if file_path:
            result["file_path"] = file_path
        return result

    def _reachable_mask(
        self,
        free: np.ndarray,
        pose: Any,
        origin_x: float,
        origin_y: float,
        res_eff: float,
    ) -> np.ndarray:
        """로봇 위치에서 BFS로 연결된 free 영역 마스크. map 좌표를 못 쓰면 전체 free 반환."""
        height, width = free.shape

        if not pose or pose.get("frame") != "map":
            logger.info(
                "[find_reachable_places] map coordinates unavailable (frame=%s) -> using entire free area",
                pose.get("frame") if pose else None,
            )
            return free

        col = int(math.floor((pose["x"] - origin_x) / res_eff))
        row = int(math.floor((pose["y"] - origin_y) / res_eff))
        seed = nearest_free_cell(free, row, col, radius=12)
        if seed is None:
            logger.warning(
                "[find_reachable_places] Could not find a free cell near the robot; using entire free area"
            )
            return free

        visited = np.zeros_like(free, dtype=bool)
        sr, sc = seed
        visited[sr, sc] = True
        q = deque([(sr, sc)])
        while q:
            r, c = q.popleft()
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nr, nc = r + dr, c + dc
                if 0 <= nr < height and 0 <= nc < width and not visited[nr, nc] and free[nr, nc]:
                    visited[nr, nc] = True
                    q.append((nr, nc))
        return visited
