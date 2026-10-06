"""
탐험 스킬 — SLAM 모드에서 프론티어 기반 자율 탐험 수행
"""

import logging
import math
import threading
import time
from typing import Any

import numpy as np
from nav_msgs.msg import OccupancyGrid

from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills.navigation_skill import (  # type: ignore
    _cancel_active_goal,
    _send_navigation_goal,
    _wait_for_goal_result,
)

logger = logging.getLogger(__name__)

_EXPLORE_ACTIVE = threading.Event()


def _get_robot_pose(skill: BaseSkill) -> tuple[float, float] | None:
    """현재 로봇 위치를 map 프레임 기준으로 가져옴 (TF 실패 시 /odom 폴백)."""
    pose = skill.get_map_pose()
    if pose:
        return pose["x"], pose["y"]
    return None


def _extract_frontiers(
    data: np.ndarray,
    resolution: float,
    origin_x: float,
    origin_y: float,
) -> list[tuple[float, float]]:
    """
    OccupancyGrid에서 프론티어(free-unknown 경계) 셀을 추출.
    반환값: 월드 좌표 리스트 [(x, y), ...]
    """
    # free=0, unknown=-1, occupied>=50
    # 프론티어: free 셀인데 8-이웃 중 unknown 셀이 있는 경우
    free_mask = data == 0
    unknown_mask = data == -1

    # unknown 셀을 8방향으로 팽창하여 free 셀과 인접한 경계를 찾음 (scipy 불필요)
    u = unknown_mask
    unknown_dilated = (
        u
        | np.pad(u, ((1, 0), (0, 0)), mode="constant")[:-1, :]  # 위
        | np.pad(u, ((0, 1), (0, 0)), mode="constant")[1:, :]  # 아래
        | np.pad(u, ((0, 0), (1, 0)), mode="constant")[:, :-1]  # 왼쪽
        | np.pad(u, ((0, 0), (0, 1)), mode="constant")[:, 1:]  # 오른쪽
        | np.pad(u, ((1, 0), (1, 0)), mode="constant")[:-1, :-1]  # 좌상
        | np.pad(u, ((1, 0), (0, 1)), mode="constant")[:-1, 1:]  # 우상
        | np.pad(u, ((0, 1), (1, 0)), mode="constant")[1:, :-1]  # 좌하
        | np.pad(u, ((0, 1), (0, 1)), mode="constant")[1:, 1:]  # 우하
    )
    frontier_mask = free_mask & unknown_dilated

    frontier_indices = np.argwhere(frontier_mask)  # (row, col) = (y, x)
    if len(frontier_indices) == 0:
        return []

    frontiers = []
    for row, col in frontier_indices:
        wx = origin_x + (col + 0.5) * resolution
        wy = origin_y + (row + 0.5) * resolution
        frontiers.append((wx, wy))

    return frontiers


def _cluster_frontiers(
    frontiers: list[tuple[float, float]], cluster_dist: float = 0.5
) -> list[tuple[float, float]]:
    """
    가까운 프론티어들을 클러스터링하여 대표 지점으로 줄임.
    간단한 그리드 기반 클러스터링 사용.
    """
    if not frontiers:
        return []

    clusters: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for x, y in frontiers:
        key = (int(x / cluster_dist), int(y / cluster_dist))
        clusters.setdefault(key, []).append((x, y))

    result = []
    for pts in clusters.values():
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        result.append((cx, cy))

    return result


def _filter_frontiers_by_radius(
    frontiers: list[tuple[float, float]],
    robot_x: float,
    robot_y: float,
    radius: float,
) -> list[tuple[float, float]]:
    """탐험 반경 내 프론티어만 필터링"""
    return [
        (fx, fy)
        for fx, fy in frontiers
        if math.hypot(fx - robot_x, fy - robot_y) <= radius
    ]


def _select_nearest_frontier(
    frontiers: list[tuple[float, float]],
    robot_x: float,
    robot_y: float,
) -> tuple[float, float] | None:
    """로봇과 가장 가까운 프론티어 선택"""
    if not frontiers:
        return None
    return min(frontiers, key=lambda p: math.hypot(p[0] - robot_x, p[1] - robot_y))


class ExploreSkill(BaseSkill):
    """SLAM 모드 프론티어 기반 자율 탐험 스킬"""

    name = "explore"
    terminal_behavior = "background"
    input_schema = {"type": "object", "properties": {
        "exploration_radius": {"type": "number", "default": 5.0},
        "target_coverage": {"type": "number", "default": 80.0, "minimum": 0.0, "maximum": 100.0},
        "max_iterations": {"type": "integer", "default": 30}, "map_topic": {"type": "string", "default": "/map"}
    }, "additionalProperties": False}
    description = (
        "SLAM 모드에서 지정된 반경(exploration_radius, 기본 5.0m) 내의 미탐사 영역을 "
        "자율적으로 탐험하며 지도를 확장합니다. "
        "프론티어(자유-미탐사 경계)를 계산하여 가장 가까운 미탐사 지점으로 반복 이동합니다. "
        "탐사율이 목표치(target_coverage, 기본 80%)에 달하거나 반경 내 미탐사 지점이 없으면 종료됩니다. "
        "파라미터: exploration_radius(float), target_coverage(float 0~100), max_iterations(int), map_topic(str). "
        "이 스킬은 탐험을 시작하며 중단이 필요하면 `stop_explore`를 사용하세요."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None:
            return {"success": False, "message": "ROS 노드에 접근할 수 없습니다."}

        radius = float(params.get("exploration_radius", 5.0))
        target_coverage = float(params.get("target_coverage", 80.0))
        max_iterations = int(params.get("max_iterations", 30))
        map_topic = str(params.get("map_topic", "/map"))

        logger.info(
            "Starting exploration: radius=%.1fm, target_coverage=%.1f%%, max_iter=%d",
            radius,
            target_coverage,
            max_iterations,
        )

        _EXPLORE_ACTIVE.set()

        def run_exploration() -> None:
            visited: set = set()
            iteration = 0
            final_coverage = 0.0

            while _EXPLORE_ACTIVE.is_set() and iteration < max_iterations:
                iteration += 1

                map_msg = self._wait_for_map(map_topic)
                if not map_msg:
                    logger.error("Failed to receive map — stopping exploration")
                    break

                info = map_msg.info
                data = np.array(map_msg.data).reshape((info.height, info.width))

                total = data.size
                unknown_count = int(np.sum(data == -1))
                final_coverage = (1.0 - unknown_count / total) * 100.0

                logger.info(
                    "[Explore %d/%d] Current coverage: %.1f%%",
                    iteration,
                    max_iterations,
                    final_coverage,
                )

                if final_coverage >= target_coverage:
                    logger.info("Reached target coverage %.1f%% — exploration complete", target_coverage)
                    self.send_user_message(
                        f"탐사율 {final_coverage:.1f}%를 달성했습니다. 탐험을 완료합니다."
                    )
                    break

                pose = _get_robot_pose(self)
                if not pose:
                    logger.warning("Failed to determine robot position — retrying")
                    time.sleep(1.0)
                    continue
                robot_x, robot_y = pose

                raw_frontiers = _extract_frontiers(
                    data,
                    info.resolution,
                    info.origin.position.x,
                    info.origin.position.y,
                )
                clusters = _cluster_frontiers(raw_frontiers, cluster_dist=0.5)
                in_radius = _filter_frontiers_by_radius(
                    clusters, robot_x, robot_y, radius
                )

                def not_visited(p: tuple[float, float]) -> bool:
                    return all(
                        math.hypot(p[0] - vx, p[1] - vy) > 0.5 for vx, vy in visited
                    )

                candidates = [p for p in in_radius if not_visited(p)]

                if not candidates:
                    logger.info(
                        "No unexplored frontier within %.1fm radius — exploration complete", radius
                    )
                    self.send_user_message(
                        f"반경 {radius:.1f}m 내 탐험할 곳이 없습니다. "
                        f"현재 탐사율: {final_coverage:.1f}%"
                    )
                    break

                target = _select_nearest_frontier(candidates, robot_x, robot_y)
                if not target:
                    break

                tx, ty = target
                dist = math.hypot(tx - robot_x, ty - robot_y)
                logger.info(
                    "[Explore %d] Moving to frontier: (%.2f, %.2f), distance=%.2fm",
                    iteration,
                    tx,
                    ty,
                    dist,
                )
                self.send_user_message(
                    f"[탐험 {iteration}/{max_iterations}] "
                    f"프론티어 (x={tx:.2f}, y={ty:.2f})로 이동 중... "
                    f"(현재 탐사율: {final_coverage:.1f}%)"
                )

                accepted, msg, goal_handle = _send_navigation_goal(
                    self.node, tx, ty, "map"
                )
                if not accepted:
                    logger.warning("Frontier movement rejected: %s — trying next frontier", msg)
                    visited.add((tx, ty))
                    continue

                success, result_msg = _wait_for_goal_result(
                    goal_handle, timeout_sec=60.0, label="ExploreMove"
                )
                visited.add((tx, ty))

                if not _EXPLORE_ACTIVE.is_set():
                    logger.info("Exploration stop request received")
                    break

                if not success:
                    logger.warning("Movement failed: %s — trying next frontier", result_msg)

            _EXPLORE_ACTIVE.clear()
            logger.info(
                "Exploration ended (%d total moves, final coverage: %.1f%%)",
                iteration,
                final_coverage,
            )

            # RAG 저장 (탐험 결과 + 최종 위치)
            if (
                self.node
                and hasattr(self.node, "_memory")
                and getattr(self.node, "_enable_rag", False)
            ):
                try:
                    pose = _get_robot_pose(self)
                    loc_str = ""
                    if pose:
                        loc_str = f" [최종 위치 x={pose[0]:.2f} y={pose[1]:.2f}]"
                    rag_text = (
                        f"{loc_str} 자율 탐험 완료: "
                        f"총 {iteration}회 이동, 최종 탐사율 {final_coverage:.1f}%"
                    )
                    self.node._memory.add_knowledge(  # type: ignore
                        rag_text,
                        {
                            "type": "explore_result",
                            "iterations": iteration,
                            "final_coverage_pct": round(final_coverage, 1),
                            "source": "explore",
                        },
                    )
                    logger.info("[explore] RAG save complete")
                except Exception as _rag_e:
                    logger.debug("[explore] RAG save failed: %s", _rag_e)

        thread = threading.Thread(target=run_exploration, daemon=True)
        thread.start()

        return {
            "success": True,
            "message": (
                f"탐험을 시작합니다. "
                f"반경 {radius:.1f}m, 목표 탐사율 {target_coverage:.1f}%"
            ),
            "exploration_radius": radius,
            "target_coverage": target_coverage,
            "max_iterations": max_iterations,
        }

    def _wait_for_map(self, map_topic: str) -> Any:
        """맵 메시지 수신"""
        return self.wait_for_message(
            OccupancyGrid, map_topic, timeout_sec=10.0, use_transient_local=True
        )


class StopExploreSkill(BaseSkill):
    """탐험 중단 스킬"""

    name = "stop_explore"
    terminal_behavior = "terminal"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    description = "현재 진행 중인 자율 탐험만 즉시 중단합니다. 일반 이동 중단은 `stop`을 사용하세요."

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if not _EXPLORE_ACTIVE.is_set():
            return {"success": False, "message": "현재 진행 중인 탐험이 없습니다."}

        _EXPLORE_ACTIVE.clear()
        _cancel_active_goal()
        logger.info("Exploration stop requested")

        return {"success": True, "message": "탐험을 중단했습니다."}
