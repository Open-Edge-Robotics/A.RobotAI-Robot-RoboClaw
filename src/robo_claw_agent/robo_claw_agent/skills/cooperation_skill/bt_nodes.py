"""자율협동 BT 노드 — autonomous_act 루프에 협동 메시지 처리를 통합한다.

`ProcessPeerMessages` 는 전역 인박스에서 동료가 보낸 메시지를 꺼내 대화 이력에 기록하고,
blackboard 의 ``pending_peer_messages`` 에 담아 LLM 판단 노드가 참고할 수 있게 한다.
"""

from __future__ import annotations

import logging
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills.autonomous_skill.core import BTNode, NodeStatus

from .conversation import get_peer_conversation
from .globals import _COOPERATE_ACTIVE
from .inbox import get_peer_message_inbox

logger = logging.getLogger(__name__)


class ProcessPeerMessages(BTNode):
    """autonomous_act 루프에서 동료 메시지를 수집하는 노드 (BTNode)."""

    def __init__(self, skill: BaseSkill) -> None:
        super().__init__("ProcessPeerMessages")
        self._skill = skill

    def halt(self) -> None:
        """노드 중단 처리."""
        pass

    def tick(self) -> Any:
        try:
            # 두 루프가 같은 인박스를 경쟁 소비하지 않도록 자율협동 루프가
            # 실행 중이면 해당 루프가 유일한 소비자가 된다.
            if _COOPERATE_ACTIVE.is_set():
                return NodeStatus.SUCCESS
            inbox = get_peer_message_inbox()
            consumer_id = "autonomous_act"
            if not inbox.claim_consumer(consumer_id):
                return NodeStatus.SUCCESS
            messages = inbox.drain(consumer=consumer_id)
            inbox.release_consumer(consumer_id)
            if not messages:
                return NodeStatus.SUCCESS

            conversation = get_peer_conversation()
            for peer_name, message in messages:
                conversation.add_message(peer_name, "peer", message)
                logger.info("[CoopBT] Received peer message from %s: %s", peer_name, message)

            # blackboard 에 담아 LLM 판단 노드가 참고하게 한다.
            # pending은 현재 BT 사이클에서만 유효하다. 누적하면 같은 메시지가
            # 매 사이클 LLM 프롬프트에 반복 주입된다.
            self._blackboard["pending_peer_messages"] = messages
            return NodeStatus.SUCCESS
        except Exception as exc:  # noqa: BLE001
            logger.debug("[CoopBT] ProcessPeerMessages error (ignored): %s", exc)
            return NodeStatus.SUCCESS


def build_peer_conversation_context(blackboard: dict[str, Any], limit: int = 6) -> str:
    """LLM 판단 프롬프트에 주입할 동료 대화 컨텍스트를 생성한다.

    blackboard 의 ``pending_peer_messages`` 와 전역 대화 이력을 함께 담는다.
    """
    conversation = get_peer_conversation()
    lines: list[str] = []

    pending = blackboard.get("pending_peer_messages", [])
    if pending:
        lines.append("[방금 동료가 보낸 메시지]")
        for peer_name, message in pending[-5:]:
            lines.append(f"  - {peer_name}: {message}")

    # 대화 이력이 있는 동료만 나열
    peers_with_history = set()
    for peer_name, history in conversation.snapshot()["history"].items():
        if history:
            peers_with_history.add(peer_name)
    if peers_with_history:
        lines.append("[동료와의 최근 대화 이력]")
        for peer_name in sorted(peers_with_history):
            ctx = conversation.get_context(peer_name, limit=limit)
            lines.append(f"  [{peer_name}]")
            lines.append(ctx)

    return "\n".join(lines) if lines else "  • 동료와의 대화 없음"
