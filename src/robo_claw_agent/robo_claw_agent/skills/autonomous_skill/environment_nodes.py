import logging
import threading
import time
from typing import Any

import numpy as np

from robo_claw_agent.skill_manager import BaseSkill

from .core import BTNode, NodeStatus
from .helpers import (
    _discover_peer_agents,
    _extract_rag_location_candidates,
    _split_semantic_places,
)

logger = logging.getLogger(__name__)

_PEER_STATUS_REFRESH_INTERVAL_SEC = 60.0


class RefreshPeerStatusCache(BTNode):
    """등록된 동료 로봇들의 배터리/위치 상태를 주기적으로 백그라운드에서 조회해 캐싱한다."""

    def __init__(
        self, skill: BaseSkill, refresh_interval_sec: float = _PEER_STATUS_REFRESH_INTERVAL_SEC
    ) -> None:
        super().__init__("RefreshPeerStatusCache")
        self._skill = skill
        self._interval = refresh_interval_sec
        self._refreshing = False

    def tick(self) -> NodeStatus:
        node = self._skill.node
        if not node:
            return NodeStatus.SUCCESS

        cache = self._blackboard.setdefault("peer_status_cache", {})
        last_refresh = self._blackboard.get("peer_status_cache_ts", 0.0)
        now = time.monotonic()
        if self._refreshing or (now - last_refresh) < self._interval:
            return NodeStatus.SUCCESS

        try:
            peer_names = list(_discover_peer_agents(node).keys())
        except Exception as exc:
            logger.debug("[BT] Failed to retrieve peer list for status refresh (ignored): %s", exc)
            return NodeStatus.SUCCESS
        if not peer_names:
            return NodeStatus.SUCCESS

        self._blackboard["peer_status_cache_ts"] = now
        self._refreshing = True

        def _run() -> None:
            try:
                from robo_claw_agent.skills.cooperate_skill import (
                    QueryPeerStatusSkill,
                )

                query_skill = QueryPeerStatusSkill()
                query_skill.set_node(node)
                for peer_name in peer_names:
                    try:
                        result = query_skill.execute(
                            {"peer_name": peer_name, "timeout_sec": 10.0}
                        )
                        if result.get("success"):
                            cache[peer_name] = {
                                "summary": result.get("status_summary", ""),
                                "ts": time.monotonic(),
                            }
                        else:
                            cache[peer_name] = {
                                "summary": "상태 불명(응답 없음)",
                                "ts": time.monotonic(),
                            }
                    except Exception as exc:
                        logger.debug("[BT] Failed to query peer status (%s): %s", peer_name, exc)
                        cache[peer_name] = {
                            "summary": "상태 불명(조회 실패)",
                            "ts": time.monotonic(),
                        }
            finally:
                self._refreshing = False

        threading.Thread(target=_run, daemon=True, name="peer-status-refresh").start()
        return NodeStatus.SUCCESS


class AnalyzeCurrentScene(BTNode):
    """VLM으로 현재 장면을 분석하고 결과를 Blackboard에 저장."""

    def __init__(self, skill: BaseSkill) -> None:
        super().__init__("AnalyzeCurrentScene")
        self._skill = skill
        self._scene_skill = None

    def tick(self) -> NodeStatus:
        try:
            if self._scene_skill is None:
                from robo_claw_agent.skills.vision_skill import (
                    AnalyzeSceneSkill,
                )

                self._scene_skill = AnalyzeSceneSkill()
                self._scene_skill.set_node(self._skill.node)
            result = self._scene_skill.execute(
                {
                    "prompt": (
                        "자율 행동 모드입니다. "
                        "현재 환경에서 유용한 행동을 판단하기 위해 장면을 분석해주세요. "
                        "쓰레기, 어질러진 물건, 정리가 필요한 대상이 있으면 반드시 포함해주세요."
                    )
                }
            )
            self._blackboard["scene_analysis"] = result.get("analysis", "")
            self._blackboard["detected_objects"] = result.get("detected_objects", [])
            return NodeStatus.SUCCESS if result.get("success") else NodeStatus.FAILURE
        except Exception as exc:
            logger.error("[BT] Scene analysis error: %s", exc)
            return NodeStatus.FAILURE


class LogObservationNode(BTNode):
    """장면 분석 결과를 RAG에 자동 저장. 저장 실패해도 BT 루프는 계속."""

    def __init__(self, skill: BaseSkill) -> None:
        super().__init__("LogObservationNode")
        self._skill = skill

    def tick(self) -> NodeStatus:
        node = self._skill.node
        if not node or not hasattr(node, "_skills"):
            return NodeStatus.SUCCESS

        scene = self._blackboard.get("scene_analysis", "")
        try:
            node._skills.execute(
                "log_observation",
                {"scene_description": scene},
                timeout_sec=5.0,
            )
        except Exception as exc:
            logger.debug("[BT] log_observation failed (ignored): %s", exc)
        return NodeStatus.SUCCESS


class InitializeMapStatus(BTNode):
    """맵의 존재 여부(has_map)를 판별하여 blackboard에 세팅."""

    def __init__(self, skill: BaseSkill, param_has_map: Any = None) -> None:
        super().__init__("InitializeMapStatus")
        self._skill = skill
        self._param_has_map = param_has_map

    def tick(self) -> NodeStatus:
        if "has_map" in self._blackboard:
            return NodeStatus.SUCCESS

        if self._param_has_map is not None:
            self._blackboard["has_map"] = bool(self._param_has_map)
            logger.info("[BT] Applied has_map parameter: %s", self._blackboard["has_map"])
            return NodeStatus.SUCCESS

        try:
            from nav_msgs.msg import OccupancyGrid
            map_msg = self._skill.wait_for_message(
                OccupancyGrid, "/map", timeout_sec=3.0, use_transient_local=True
            )
            if map_msg is None:
                self._blackboard["has_map"] = False
                logger.info("[BT] Failed to receive /map topic — setting has_map = False")
                return NodeStatus.SUCCESS

            data = np.array(map_msg.data)
            total_cells = data.size
            if total_cells == 0:
                self._blackboard["has_map"] = False
                return NodeStatus.SUCCESS

            unknown_cells = np.sum(data == -1)
            unknown_ratio = (unknown_cells / total_cells) * 100.0

            if unknown_ratio <= 30.0:
                self._blackboard["has_map"] = True
                logger.info("[BT] Map unexplored ratio %.1f%% (<= 30%%) — setting has_map = True", unknown_ratio)
            else:
                self._blackboard["has_map"] = False
                logger.info("[BT] Map unexplored ratio %.1f%% (> 30%%) — setting has_map = False", unknown_ratio)
        except Exception as exc:
            logger.error("[BT] Map state determination error: %s", exc)
            self._blackboard["has_map"] = False

        return NodeStatus.SUCCESS


class EnsurePlacesRegistered(BTNode):
    """맵이 준비되었을 때, 로봇이 돌아다닐 수 있는 지점을 발굴하여 메모리에 등록."""

    def __init__(self, skill: BaseSkill) -> None:
        super().__init__("EnsurePlacesRegistered")
        self._skill = skill

    def tick(self) -> NodeStatus:
        if self._blackboard.get("places_registered", False):
            return NodeStatus.SUCCESS

        node = self._skill.node
        if not node:
            return NodeStatus.FAILURE

        has_semantic_places = False
        has_map_generated_places = False
        if hasattr(node, "_memory"):
            try:
                all_objs = node._memory.get_all_objects()
                semantic_places, map_generated_places = _split_semantic_places(all_objs)
                has_semantic_places = bool(semantic_places)
                has_map_generated_places = bool(map_generated_places)
            except Exception as e:
                logger.warning("[BT] Failed to query existing registered locations: %s", e)

        if has_semantic_places:
            logger.info("[BT] Semantic map locations already exist — skipping automatic map-based waypoint discovery.")
            self._blackboard["places_registered"] = True
            self._blackboard["place_source_priority"] = "semantic_map"
            return NodeStatus.SUCCESS

        if hasattr(node, "_memory"):
            rag_candidates = _extract_rag_location_candidates(node._memory, limit=5)
            if rag_candidates:
                for candidate in rag_candidates:
                    pos = candidate["position"]
                    node._memory.add_object_location(
                        candidate["name"],
                        float(pos["x"]),
                        float(pos["y"]),
                        metadata={
                            **candidate.get("metadata", {}),
                            "source": "autonomous_rag_location",
                            "frame_id": candidate.get("metadata", {}).get("frame_id", "map"),
                            "kind": "place",
                        },
                    )
                logger.info("[BT] Synced %d RAG location candidates to the semantic map.", len(rag_candidates))
                self._blackboard["places_registered"] = True
                self._blackboard["place_source_priority"] = "rag"
                return NodeStatus.SUCCESS

        if has_map_generated_places:
            logger.info("[BT] Reusing existing auto-discovered map waypoints.")
            self._blackboard["places_registered"] = True
            self._blackboard["place_source_priority"] = "map_generated"
            return NodeStatus.SUCCESS

        if not self._blackboard.get("has_map", False):
            # RAG/시맨틱 장소는 OccupancyGrid 없이도 navigate_to에 사용할 수 있다.
            logger.debug("[BT] No map available and no semantic/RAG places — skipping place discovery.")
            return NodeStatus.SUCCESS

        try:
            logger.info("[BT] No semantic/RAG location candidates found — starting automatic map-based waypoint discovery.")
            from nav_msgs.msg import OccupancyGrid

            from .place_discovery import find_reachable_places

            map_msg = self._skill.wait_for_message(
                OccupancyGrid, "/map", timeout_sec=5.0, use_transient_local=True
            )
            if not map_msg:
                logger.warning("[BT] Failed to acquire /map — cannot register location.")
                return NodeStatus.SUCCESS

            pose = self._skill.get_map_pose()
            selected = find_reachable_places(map_msg, pose)

            if not selected:
                logger.warning("[BT] No suitable navigable point found to register on the map.")
            elif hasattr(node, "_memory"):
                for i, (wx, wy, openness) in enumerate(selected, start=1):
                    place_name = f"이동가능지점{i}"
                    node._memory.add_object_location(
                        place_name,
                        wx,
                        wy,
                        metadata={
                            "source": "autonomous_ensure_places",
                            "frame_id": "map",
                            "openness_m": round(openness, 2),
                            "kind": "place",
                        },
                        aliases=[str(i), f"Place {i}", f"이동지점{i}"],
                    )
                self._skill.send_user_message(f"📍 지도를 바탕으로 이동 가능한 대표 장소 {len(selected)}곳을 자동으로 메모리에 등록했습니다.")

            self._blackboard["places_registered"] = True
            self._blackboard["place_source_priority"] = "map_generated"
        except Exception as exc:
            logger.exception("[BT] Failed to discover navigable point: %s", exc)
            self._blackboard["places_registered"] = True

        return NodeStatus.SUCCESS
