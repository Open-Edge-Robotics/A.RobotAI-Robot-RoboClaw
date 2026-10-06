"""자율 행동의 결정적 순찰 계층 — 매 사이클 LLM에게 목적지를 맡기는 대신,
등록된 장소(kind=="place")를 마지막 방문 시각이 오래된 순으로 결정적으로 순회한다.

LLM 호출은 InterestingSceneGate가 특이사항을 감지했을 때만 발동해, 정상적인
순찰 이동 자체에서는 VLM 장면분석 이후 곧바로 다음 장소로 넘어가도록 한다.
"""

import logging
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .actions import _last_seen_epoch, _split_semantic_places
from .core import BTNode, NodeStatus

logger = logging.getLogger(__name__)

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
    """이동 후보(kind=="place")를 시맨틱 맵 우선, 없으면 맵 자동 발굴 지점 순으로 모은다."""
    all_objs = memory.get_all_objects()
    semantic_places, map_generated_places = _split_semantic_places(all_objs)
    return semantic_places or map_generated_places


class PatrolNextPlaceNode(BTNode):
    """등록된 장소 중 가장 오래전에 방문(등록)한 곳으로 결정적으로 이동한다.

    방문 시도 후에는 성공/실패와 무관하게 해당 장소의 last_seen을 갱신해 다음
    사이클에는 자연히 다른(더 오래된) 장소가 선택되도록 순환시킨다.
    """

    def __init__(self, skill: BaseSkill) -> None:
        super().__init__("PatrolNextPlaceNode")
        self._skill = skill

    def tick(self) -> NodeStatus:
        node = self._skill.node
        memory = getattr(node, "_memory", None) if node else None
        if memory is None:
            logger.debug("[BT] PatrolNextPlaceNode: no memory — skipping")
            return NodeStatus.SUCCESS

        try:
            candidates = _gather_place_candidates(memory)
        except Exception as exc:
            logger.warning("[BT] PatrolNextPlaceNode: failed to query candidates: %s", exc)
            return NodeStatus.SUCCESS

        if not candidates:
            logger.debug("[BT] PatrolNextPlaceNode: no registered places — skipping")
            self._blackboard["patrol_last_place"] = ""
            self._blackboard["patrol_last_success"] = False
            return NodeStatus.SUCCESS

        target = min(candidates, key=_last_seen_epoch)
        name = target.get("name", "")

        from robo_claw_agent.skills.navigation_skill.patrol import (  # type: ignore[import]
            visit_place,
        )

        logger.info("[BT] PatrolNextPlaceNode: patrolling to '%s'", name)
        success, message = visit_place(node, name)

        self._blackboard["patrol_last_place"] = name
        self._blackboard["patrol_last_success"] = success
        if success:
            logger.info("[BT] PatrolNextPlaceNode: arrived at '%s'", name)
        else:
            logger.warning("[BT] PatrolNextPlaceNode: '%s' failed: %s", name, message)

        # last_seen 갱신 (add_object_location은 항상 현재 시각으로 갱신하므로
        # 좌표/메타데이터는 그대로 유지한 채 재등록해 "마지막 방문 시각"만 갱신한다).
        try:
            pos = target.get("position", {})
            memory.add_object_location(
                name,
                float(pos["x"]),
                float(pos["y"]),
                metadata=target.get("metadata", {}),
            )
        except Exception as exc:
            logger.debug("[BT] PatrolNextPlaceNode: failed to update last_seen (ignored): %s", exc)

        return NodeStatus.SUCCESS


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

        objects_lower = [str(o).lower() for o in objects]
        if any(
            any(kw in o for kw in _INTERESTING_OBJECT_KEYWORDS) for o in objects_lower
        ):
            return NodeStatus.SUCCESS
        if any(kw in scene for kw in _INTERESTING_TEXT_KEYWORDS):
            return NodeStatus.SUCCESS
        return NodeStatus.FAILURE
