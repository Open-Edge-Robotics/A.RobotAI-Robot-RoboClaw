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
    """시맨틱 맵/RAG에 이미 기억된 순찰 장소를 메모리에서 사용할 수 있게 준비한다."""

    def __init__(self, skill: BaseSkill) -> None:
        super().__init__("EnsurePlacesRegistered")
        self._skill = skill

    def tick(self) -> NodeStatus:
        if self._blackboard.get("places_registered", False):
            return NodeStatus.SUCCESS

        node = self._skill.node
        if not node:
            return NodeStatus.FAILURE

        now = time.monotonic()
        if now < self._blackboard.get("place_retry_after", 0.0):
            return NodeStatus.SUCCESS

        memory = getattr(node, "_memory", None)
        semantic_places: list[dict[str, Any]] = []
        map_generated_places: list[dict[str, Any]] = []
        if memory is not None:
            try:
                semantic_places, map_generated_places = _split_semantic_places(
                    memory.get_all_objects()
                )
            except Exception as exc:
                logger.warning("[BT] Failed to query remembered locations: %s", exc)

        known_names = {
            str(place.get("name", "")).strip().casefold()
            for place in semantic_places + map_generated_places
        }
        known_coordinates = {
            (round(float(place["position"]["x"]), 3), round(float(place["position"]["y"]), 3))
            for place in semantic_places + map_generated_places
        }
        rag_candidates = (
            _extract_rag_location_candidates(memory, limit=5) if memory is not None else []
        )
        new_rag_candidates = []
        for candidate in rag_candidates:
            name = str(candidate.get("name", "")).strip()
            pos = candidate["position"]
            coordinate = (round(float(pos["x"]), 3), round(float(pos["y"]), 3))
            if name.casefold() in known_names or coordinate in known_coordinates:
                continue
            new_rag_candidates.append(candidate)
            known_names.add(name.casefold())
            known_coordinates.add(coordinate)

        if memory is not None:
            for candidate in new_rag_candidates:
                pos = candidate["position"]
                source_metadata = candidate.get("metadata", {})
                memory.add_object_location(
                    candidate["name"],
                    float(pos["x"]),
                    float(pos["y"]),
                    metadata={
                        **source_metadata,
                        "source": "autonomous_rag_location",
                        "frame_id": source_metadata.get("frame_id", "map"),
                        "kind": "place",
                    },
                )

        if semantic_places or new_rag_candidates or map_generated_places:
            self._blackboard["places_registered"] = True
            sources = []
            if semantic_places:
                sources.append("semantic_map")
            if new_rag_candidates:
                sources.append("rag")
            if map_generated_places:
                sources.append("previously_saved_map_points")
            self._blackboard["place_source_priority"] = "+".join(sources)
            logger.info(
                "[BT] Remembered patrol locations ready: semantic=%d rag=%d saved_map=%d",
                len(semantic_places),
                len(new_rag_candidates),
                len(map_generated_places),
            )
            return NodeStatus.SUCCESS

        # Retry at a bounded rate because RAG may be large and new places can appear later.
        self._blackboard["places_registered"] = False
        self._blackboard["place_source_priority"] = ""
        self._blackboard["place_retry_after"] = now + 30.0
        logger.info("[BT] No remembered patrol locations available; using local observation.")
        return NodeStatus.SUCCESS
