import logging
import threading
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .core import BTNode, NodeStatus

logger = logging.getLogger(__name__)


class SkillChainNode(BTNode):
    """스킬 체인을 순차 실행하는 BT 노드.

    처음 tick에서 daemon 스레드를 시작해 chain 내 스킬을 순차 실행한다.
    실행 완료 전까지 RUNNING, 모두 성공하면 SUCCESS, 하나라도 실패하면 FAILURE를 반환한다.
    """

    def __init__(self, skill: BaseSkill, chain: list[dict[str, Any]]) -> None:
        super().__init__("SkillChainNode")
        self._skill = skill
        self._chain = chain
        self._thread: Any = None
        self._done = threading.Event()
        self._success = False

    def tick(self) -> NodeStatus:
        if self._thread is None:
            self._done.clear()
            self._success = False
            self._thread = threading.Thread(target=self._run_chain, daemon=True)
            self._thread.start()

        if not self._done.is_set():
            return NodeStatus.RUNNING

        return NodeStatus.SUCCESS if self._success else NodeStatus.FAILURE

    def _run_chain(self) -> None:
        node = self._skill.node
        if node is None or not hasattr(node, "_skills"):
            logger.error("[BT] SkillChainNode: no node or skill manager found")
            self._success = False
            self._done.set()
            return

        from robo_claw_agent.agent_node.nav_safety import (
            _uses_stretch_backend,
            ensure_stretch_navigation_safety,
        )

        backend = getattr(node, "_manipulation_backend", None)
        chain = (
            ensure_stretch_navigation_safety(list(self._chain), node._skills, logger)
            if backend is not None and _uses_stretch_backend(node, backend)
            else list(self._chain)
        )
        for step in chain:
            skill_name = step.get("skill", "")
            params = step.get("params", {})
            if not skill_name:
                continue

            logger.info("[BT] SkillChainNode: executing — %s", skill_name)
            try:
                result = node._skills.execute(skill_name, params, timeout_sec=120.0)
                if not result.success:
                    logger.warning(
                        "[BT] SkillChainNode: '%s' failed — %s", skill_name, result.message
                    )
                    self._success = False
                    self._done.set()
                    return
                logger.info("[BT] SkillChainNode: '%s' completed", skill_name)
            except Exception as exc:
                logger.error("[BT] SkillChainNode: '%s' exception — %s", skill_name, exc)
                self._success = False
                self._done.set()
                return

        self._success = True
        self._done.set()

    def halt(self) -> None:
        self._thread = None
        self._done.set()
        self._success = False
