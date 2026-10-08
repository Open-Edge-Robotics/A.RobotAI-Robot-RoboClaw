"""자율 행동의 기억 기반 순찰 계층.

시맨틱 맵과 RAG에 저장된 좌표만 순찰하고, 이동 실패 목적지는 일정 시간 제외한다.
기억된 목적지가 없으면 이동하지 않고 제자리 관찰 경로로 전환한다. LLM 판단은 새 특이사항에만 허용한다.
"""

import logging
import time
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .actions import (
    _extract_rag_location_candidates,
    _last_seen_epoch,
    _split_semantic_places,
)
from .core import BTNode, NodeStatus

logger = logging.getLogger(__name__)
_PATROL_FAILURE_COOLDOWN_SEC = 60.0

_INTERESTING_OBJECT_KEYWORDS = (
    "trash",
    "garbage",
    "waste",
    "litter",
    "bottle",
    "can",
    "cup",
    "wrapper",
    "bag",
    "cigarette",
    "spill",
    "mess",
    "person",
    "human",
)
_INTERESTING_TEXT_KEYWORDS = (
    "쓰레기",
    "어질러",
    "정리",
    "치워",
    "사람",
    "위험",
    "넘어",
    "쏟아",
    "고장",
)


def _gather_place_candidates(memory: Any) -> list[dict[str, Any]]:
    """기억된 시맨틱/RAG 장소와 과거 저장된 맵 지점을 모은다."""
    all_objs = memory.get_all_objects() if hasattr(memory, "get_all_objects") else []
    semantic_places, map_generated_places = _split_semantic_places(all_objs)
    rag_places = _extract_rag_location_candidates(memory, limit=20)
    return semantic_places + map_generated_places + rag_places


class PatrolNextPlaceNode(BTNode):
    """기억된 장소 중 가장 오래전에 방문한 곳으로 이동한다.

    성공한 방문만 last_seen을 갱신하고, 실패 목적지는 일시적으로 제외한다.
    기억된 목적지가 없으면 blackboard에 제자리 관찰 모드를 표시한다.
    """

    def __init__(self, skill: BaseSkill) -> None:
        super().__init__("PatrolNextPlaceNode")
        self._skill = skill

    def tick(self) -> NodeStatus:
        node = self._skill.node
        memory = getattr(node, "_memory", None) if node else None
        if memory is None:
            logger.info("[BT] PatrolNextPlaceNode: no memory — switching to local observation")
            self._mark_no_destination()
            return NodeStatus.SUCCESS

        try:
            candidates = _gather_place_candidates(memory)
        except Exception as exc:
            logger.warning("[BT] PatrolNextPlaceNode: failed to query candidates: %s", exc)
            self._mark_no_destination()
            return NodeStatus.SUCCESS

        now = time.monotonic()
        blocked_until = self._blackboard.setdefault("patrol_blocked_until", {})
        candidates = [
            candidate
            for candidate in candidates
            if str(candidate.get("name", "")).strip()
            and float(blocked_until.get(candidate.get("name", ""), 0.0)) <= now
        ]
        if not candidates:
            self._mark_no_destination()
            return NodeStatus.SUCCESS

        target = min(candidates, key=_last_seen_epoch)
        name = str(target.get("name", "")).strip()
        if not name:
            self._mark_no_destination()
            return NodeStatus.SUCCESS

        from robo_claw_agent.skills.navigation_skill.patrol import (  # type: ignore[import]
            visit_place,
        )

        self._blackboard["patrol_has_destination"] = True
        self._blackboard["stationary_observation_only"] = False
        logger.info("[BT] PatrolNextPlaceNode: patrolling to remembered place '%s'", name)
        try:
            success, message = visit_place(node, name)
        except Exception as exc:
            success, message = False, str(exc)
            logger.exception("[BT] PatrolNextPlaceNode: visit failed for '%s'", name)

        self._blackboard["patrol_last_place"] = name
        self._blackboard["patrol_last_success"] = success
        if success:
            blocked_until.pop(name, None)
            logger.info("[BT] PatrolNextPlaceNode: arrived at '%s'", name)
            # 방문에 성공했을 때만 last_seen을 갱신해 최근 방문 후보로 이동시킨다.
            try:
                pos = target.get("position", {})
                memory.add_object_location(
                    name,
                    float(pos["x"]),
                    float(pos["y"]),
                    metadata=target.get("metadata", {}),
                )
            except Exception as exc:
                logger.debug(
                    "[BT] PatrolNextPlaceNode: failed to update last_seen (ignored): %s", exc
                )
        else:
            blocked_until[name] = now + _PATROL_FAILURE_COOLDOWN_SEC
            logger.warning("[BT] PatrolNextPlaceNode: '%s' failed: %s", name, message)

        return NodeStatus.SUCCESS

    def _mark_no_destination(self) -> None:
        self._blackboard["patrol_last_place"] = ""
        self._blackboard["patrol_last_success"] = False
        self._blackboard["patrol_has_destination"] = False
        self._blackboard["stationary_observation_only"] = True


class InterestingSceneGate(BTNode):
    """씬 분석 결과에 대응이 필요한 특이사항이 있을 때만 SUCCESS를 반환해 LLM 개입 경로를 연다.

    정상적인 순찰/이동 자체는 PatrolNextPlaceNode가 결정적으로 처리하므로, 여기서는
    "이 장면에서 LLM이 개입해 별도 행동을 계획할 필요가 있는가"만 값싸게 휴리스틱으로
    판단해 불필요한 LLM 풀 호출을 줄인다.
    """

    def __init__(self, skill: BaseSkill) -> None:
        super().__init__("InterestingSceneGate")
        self._skill = skill

    def tick(self) -> NodeStatus:
        objects = self._blackboard.get("detected_objects", []) or []
        scene = str(self._blackboard.get("scene_analysis", "") or "")

        object_matches = {
            str(obj).lower()
            for obj in objects
            if any(keyword in str(obj).lower() for keyword in _INTERESTING_OBJECT_KEYWORDS)
        }
        text_matches = {keyword for keyword in _INTERESTING_TEXT_KEYWORDS if keyword in scene}
        if not object_matches and not text_matches:
            self._blackboard["last_interesting_signature"] = None
            return NodeStatus.FAILURE

        signature = (tuple(sorted(object_matches)), tuple(sorted(text_matches)))
        if signature == self._blackboard.get("last_interesting_signature"):
            logger.debug("[BT] InterestingSceneGate: unchanged event already handled")
            return NodeStatus.FAILURE

        self._blackboard["last_interesting_signature"] = signature
        return NodeStatus.SUCCESS
