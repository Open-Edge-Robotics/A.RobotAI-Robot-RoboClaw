import logging
import threading
import time
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from . import globals
from .core import (
    _cancel_active_goal,
    _get_nav2_dependency_error,
    _resolve_target_coordinates,
    _send_navigation_goal,
    _wait_for_goal_result,
)

logger = logging.getLogger(__name__)


def visit_place(node: Any, place_name: str, *, timeout_sec: float = 120.0) -> tuple[bool, str]:
    """등록된 장소명으로 이동해 결과를 기다린다 (patrol/autonomous_act 공용 헬퍼).

    좌표 해석은 시맨틱 맵 → RAG 순으로 시도하는 ``_resolve_target_coordinates``를 그대로
    따르며, frame_id는 등록 당시 metadata를 신뢰한다(TF map lookup 실패로 odom 폴백된
    좌표를 map으로 잘못 단정하지 않기 위함).
    """
    memory = getattr(node, "_memory", None)
    if memory is None:
        return False, "memory 없음"

    obj_info = _resolve_target_coordinates(memory, place_name)
    if not obj_info:
        return False, f"'{place_name}' 위치를 맵에서 찾을 수 없습니다."

    x = obj_info["position"]["x"]
    y = obj_info["position"]["y"]
    metadata = obj_info.get("metadata", {})
    frame = metadata.get("frame_id", "map") if isinstance(metadata, dict) else "map"

    from robo_claw_agent.agent_node.nav_safety import prepare_stretch_navigation

    ready, reason = prepare_stretch_navigation(node)
    if not ready:
        return False, reason

    ok, msg, goal_handle = _send_navigation_goal(node, x, y, frame)
    if not ok:
        return False, msg

    return _wait_for_goal_result(goal_handle, timeout_sec, f"visit_place → {place_name}")


class PatrolSkill(BaseSkill):
    """시맨틱 맵의 위치 목록을 순서대로 순환 방문하는 순찰 스킬."""

    name = "patrol"
    terminal_behavior = "background"
    description = (
        "시맨틱 맵에 등록된 위치명 목록을 순서대로 방문하는 순찰을 백그라운드에서 실행합니다. "
        "파라미터: waypoints(['주방', '거실', '현관'] 형식의 리스트 또는 JSON 문자열), "
        "rounds(정수, 0=무제한, 기본 1). "
        "중단: stop_patrol 스킬을 사용하세요."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "waypoints": {"type": "array", "items": {"type": "string"}},
            "rounds": {"type": "integer", "default": 1, "minimum": 0, "description": "0=무제한"},
        },
        "required": ["waypoints"],
        "additionalProperties": False,
    }
    side_effects = ("base_motion", "background_loop")

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None:
            return {"success": False, "message": "ROS 노드에 접근할 수 없습니다."}
        if globals.PATROL_ACTIVE.is_set():
            return {
                "success": False,
                "message": "이미 순찰 중입니다. stop_patrol로 먼저 중단하세요.",
            }

        waypoints = params.get("waypoints", [])
        if isinstance(waypoints, str):
            import json as _json

            try:
                waypoints = _json.loads(waypoints)
            except Exception:
                waypoints = [w.strip() for w in waypoints.split(",") if w.strip()]
        if not waypoints:
            return {
                "success": False,
                "message": "waypoints 파라미터(위치명 목록)가 필요합니다.",
            }

        memory = getattr(self.node, "_memory", None)
        valid_waypoints = []
        if memory:
            for wp in waypoints:
                if _resolve_target_coordinates(memory, wp):
                    valid_waypoints.append(wp)

        if not valid_waypoints:
            return {
                "success": False,
                "message": (
                    f"순찰 위치 중 시맨틱 맵에 등록된 곳이 하나도 없습니다. "
                    f"(시도한 위치: {', '.join(waypoints)})"
                ),
            }

        rounds = int(params.get("rounds", 1))

        nav2_error = _get_nav2_dependency_error()
        if nav2_error:
            return {"success": False, "message": nav2_error}

        globals.PATROL_ACTIVE.set()
        patrol_snapshot = list(valid_waypoints)

        def _run_patrol() -> None:
            memory = getattr(self.node, "_memory", None)
            visited_total = 0
            round_num = 0

            self.send_user_message(
                f"순찰을 시작합니다: {', '.join(patrol_snapshot)}"
                + (f" ({rounds}라운드)" if rounds > 0 else " (무제한)")
            )

            while globals.PATROL_ACTIVE.is_set():
                round_num += 1
                if rounds > 0 and round_num > rounds:
                    break

                logger.info("[patrol] Starting round %d", round_num)

                for wp_name in patrol_snapshot:
                    if not globals.PATROL_ACTIVE.is_set():
                        break

                    if memory is None:
                        logger.error("[patrol] No memory available, aborting patrol")
                        globals.PATROL_ACTIVE.clear()
                        return

                    logger.info("[patrol] Round %d -> '%s'", round_num, wp_name)
                    success, result_msg = visit_place(self.node, wp_name)
                    if success:
                        visited_total += 1
                        logger.info("[patrol] Arrived at '%s'", wp_name)
                    else:
                        logger.warning("[patrol] '%s' failed: %s", wp_name, result_msg)

                if globals.PATROL_ACTIVE.is_set():
                    time.sleep(1.0)

            globals.PATROL_ACTIVE.clear()
            self.send_user_message(f"순찰 완료: {round_num}라운드, 총 {visited_total}회 방문.")
            logger.info("[patrol] Finished — %d rounds, %d visits", round_num, visited_total)

            # RAG 저장
            if (
                self.node
                and hasattr(self.node, "_memory")
                and getattr(self.node, "_enable_rag", False)
            ):
                try:
                    route_str = " → ".join(patrol_snapshot)
                    rag_text = (
                        f"순찰 완료: 루트 [{route_str}], "
                        f"{round_num}라운드, {visited_total}회 방문 성공"
                    )
                    self.node._memory.add_knowledge(  # type: ignore
                        rag_text,
                        {
                            "type": "patrol_result",
                            "waypoints": patrol_snapshot,
                            "rounds": round_num,
                            "visited_total": visited_total,
                            "source": "patrol",
                        },
                    )
                    logger.info("[patrol] RAG save complete")
                except Exception as _rag_e:
                    logger.debug("[patrol] RAG save failed: %s", _rag_e)

        thread = threading.Thread(target=_run_patrol, daemon=True)
        thread.start()

        return {
            "success": True,
            "message": (
                f"순찰 시작: {len(patrol_snapshot)}개 위치"
                + (f", {rounds}라운드" if rounds > 0 else ", 무제한")
            ),
            "waypoints": patrol_snapshot,
            "rounds": rounds,
        }


class StopPatrolSkill(BaseSkill):
    """진행 중인 순찰을 즉시 중단합니다."""

    name = "stop_patrol"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    description = "patrol로 시작한 순찰 루프를 즉시 중단하고 현재 이동도 취소합니다."

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if not globals.PATROL_ACTIVE.is_set():
            return {"success": False, "message": "현재 진행 중인 순찰이 없습니다."}

        globals.PATROL_ACTIVE.clear()
        try:
            _cancel_active_goal()
        except Exception as exc:
            logger.warning("[stop_patrol] Error while canceling move goal: %s", exc)

        return {"success": True, "message": "순찰을 중단했습니다."}
