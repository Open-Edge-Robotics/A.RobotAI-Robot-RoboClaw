import logging
from typing import Any

from nav_msgs.msg import OccupancyGrid  # type: ignore[import]

from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)


class AnalyzeMapSkill(BaseSkill):
    """맵 데이터 분석 및 공간 인지 스킬"""

    name = "analyze_map"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "map_topic": {"type": "string", "default": "/map"},
            "target_x": {"type": "number"},
            "target_y": {"type": "number"},
        },
        "additionalProperties": False,
    }
    description = (
        "전역 맵(/map)을 분석하여 로봇 주변의 장애물 분포, 특정 위치의 통과 가능 여부 등을 파악합니다. "
        "LLM이 특정 위치(target_x, target_y)가 이동 가능한지 확인하거나, 맵의 통계를 얻을 때 사용합니다. "
        "이 분석 결과를 바탕으로 좌표를 획득하여 annotate_map 도구에 전달할 수 있습니다."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        map_topic = params.get("map_topic", "/map")
        target_x = params.get("target_x")
        target_y = params.get("target_y")

        logger.info("Waiting to receive map data: topic=%s", map_topic)

        map_msg = self.wait_for_message(
            OccupancyGrid,
            map_topic,
            timeout_sec=5.0,
            use_transient_local=True,
            max_age_sec=10.0,
        )
        if not map_msg:
            return {
                "success": False,
                "message": f"맵 데이터를 수신할 수 없습니다 ({map_topic}). 맵 서버가 실행 중인지 확인하세요.",
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
        data = getattr(map_msg, "data", [])

        if width <= 0 or height <= 0 or res <= 0.0 or len(data) != width * height:
            return {
                "success": False,
                "message": (
                    f"비정상적인 OccupancyGrid 데이터: {width}x{height} (res={res}), "
                    f"데이터 길이({len(data)})가 일치하지 않습니다."
                ),
            }

        origin_pos = getattr(getattr(info, "origin", None), "position", None)
        origin_x = getattr(origin_pos, "x", 0.0) if origin_pos else 0.0
        origin_y = getattr(origin_pos, "y", 0.0) if origin_pos else 0.0

        total_cells = len(data)
        unknown = data.count(-1)
        occupied = sum(1 for d in data if d > 50)
        free = total_cells - unknown - occupied

        exploration_ratio = (1.0 - (unknown / total_cells)) * 100
        occupied_ratio = (
            (occupied / (total_cells - unknown)) * 100 if (total_cells - unknown) > 0 else 0
        )

        insight_msg = (
            f"지도는 {exploration_ratio:.1f}% 탐사되었습니다. "
            f"탐사된 영역 중 장애물 비율은 {occupied_ratio:.1f}%입니다. "
        )

        analysis_detail = {}
        if target_x is not None and target_y is not None:
            status, value = self._get_cell_status(target_x, target_y, info, data)
            analysis_detail["target_status"] = status
            analysis_detail["target_value"] = value

            if status == "occupied":
                insight_msg += f"지정된 위치({target_x}, {target_y})는 장애물에 의해 차단되어 있습니다. (문이 닫혀 있을 가능성 있음)"
            elif status == "free":
                insight_msg += f"지정된 위치({target_x}, {target_y})는 비어 있어 이동 가능합니다. (문이 열려 있을 가능성 있음)"
            else:
                insight_msg += f"지정된 위치({target_x}, {target_y})는 아직 탐사되지 않은 회색 구역(Unknown)으로 진입 및 이동이 불가능합니다."

        result = {
            "success": True,
            "message": f"맵 분석 완료: {insight_msg}",
            "map_info": {
                "resolution": res,
                "width": width,
                "height": height,
                "origin": {"x": origin_x, "y": origin_y},
                "stats": {
                    "exploration_ratio": exploration_ratio,
                    "occupied_ratio": occupied_ratio,
                    "unknown_cells": unknown,
                    "occupied_cells": occupied,
                    "free_cells": free,
                },
            },
            "analysis_result": insight_msg,
            "details": analysis_detail,
        }

        # RAG 저장
        if self.node and hasattr(self.node, "_memory") and getattr(self.node, "_enable_rag", False):
            try:
                rag_entries = []

                rag_entries.append(
                    (
                        f"맵 분석: 탐사율 {exploration_ratio:.1f}%, 장애물 비율 {occupied_ratio:.1f}%",
                        {
                            "type": "map_stats",
                            "source": "analyze_map",
                            "exploration_ratio": round(exploration_ratio, 1),
                            "occupied_ratio": round(occupied_ratio, 1),
                        },
                    )
                )

                if (
                    target_x is not None
                    and target_y is not None
                    and analysis_detail.get("target_status") == "free"
                ):
                    rag_entries.append(
                        (
                            f"안전한 이동 가능 좌표: x={target_x}, y={target_y} (맵에서 free 확인됨)",
                            {
                                "type": "safe_coordinate",
                                "source": "analyze_map",
                                "x": float(target_x),
                                "y": float(target_y),
                            },
                        )
                    )

                for rag_text, rag_meta in rag_entries:
                    self.node._memory.add_knowledge(rag_text, rag_meta)  # type: ignore
                logger.info("[analyze_map] RAG save complete (%d entries)", len(rag_entries))
            except Exception as _rag_e:
                logger.debug("[analyze_map] RAG save failed: %s", _rag_e)

        return result

    def _get_cell_status(self, x: float, y: float, info: Any, data: Any) -> tuple[str, int]:
        import math

        res = getattr(info, "resolution", 0.05)
        origin_pos = getattr(getattr(info, "origin", None), "position", None)
        origin_x = getattr(origin_pos, "x", 0.0) if origin_pos else 0.0
        origin_y = getattr(origin_pos, "y", 0.0) if origin_pos else 0.0
        width = getattr(info, "width", 0)
        height = getattr(info, "height", 0)

        # 월드 좌표 -> 맵 그리드 인덱스
        grid_x = int(math.floor((x - origin_x) / res))
        grid_y = int(math.floor((y - origin_y) / res))

        if 0 <= grid_x < width and 0 <= grid_y < height:
            index = grid_x + (grid_y * width)
            val = data[index]
            if val == -1:
                return "unknown", val
            if val > 50:
                return "occupied", val
            return "free", val

        return "out_of_bounds", -2
