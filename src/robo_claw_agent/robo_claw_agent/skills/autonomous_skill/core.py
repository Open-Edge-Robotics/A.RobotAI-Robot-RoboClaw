import enum
import logging
from typing import Any

logger = logging.getLogger(__name__)


class NodeStatus(enum.Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    RUNNING = "RUNNING"


class BTNode:
    def __init__(self, name: str) -> None:
        self.name = name
        self._blackboard: dict[str, Any] = {}

    def tick(self) -> NodeStatus:
        raise NotImplementedError

    def halt(self) -> None:
        """노드 중단 처리 (예: 실행 중인 액션 취소)"""
        pass


class SequenceNode(BTNode):
    """모든 자식이 SUCCESS여야 SUCCESS. 첫 FAILURE/RUNNING에서 즉시 반환."""

    def __init__(self, name: str, children: list[BTNode]) -> None:
        super().__init__(name)
        self.children = children
        self._current_index = 0

    def tick(self) -> NodeStatus:
        while self._current_index < len(self.children):
            child = self.children[self._current_index]
            status = child.tick()
            if status == NodeStatus.SUCCESS:
                self._current_index += 1
                continue
            if status == NodeStatus.RUNNING:
                return NodeStatus.RUNNING
            # FAILURE
            self.halt()
            return NodeStatus.FAILURE

        self._current_index = 0
        return NodeStatus.SUCCESS

    def halt(self) -> None:
        for child in self.children:
            if hasattr(child, "halt"):
                child.halt()
        self._current_index = 0


class FallbackNode(BTNode):
    """첫 SUCCESS에서 즉시 반환. 모두 FAILURE면 FAILURE."""

    def __init__(self, name: str, children: list[BTNode]) -> None:
        super().__init__(name)
        self.children = children
        self._current_index = 0

    def tick(self) -> NodeStatus:
        while self._current_index < len(self.children):
            child = self.children[self._current_index]
            status = child.tick()
            if status == NodeStatus.SUCCESS:
                self.halt()
                return NodeStatus.SUCCESS
            if status == NodeStatus.RUNNING:
                return NodeStatus.RUNNING
            # FAILURE
            self._current_index += 1

        self._current_index = 0
        return NodeStatus.FAILURE

    def halt(self) -> None:
        for child in self.children:
            if hasattr(child, "halt"):
                child.halt()
        self._current_index = 0


class ParallelNode(BTNode):
    """자식들을 병렬로 틱. 한 자식이라도 SUCCESS_ON_ONE 정책에 맞으면 SUCCESS 반환 후 나머지 halt."""

    def __init__(self, name: str, children: list[BTNode], success_count: int = 1) -> None:
        super().__init__(name)
        self.children = children
        self.success_count = success_count

    def tick(self) -> NodeStatus:
        successes = 0
        failures = 0

        for child in self.children:
            status = child.tick()
            if status == NodeStatus.SUCCESS:
                successes += 1
            elif status == NodeStatus.FAILURE:
                failures += 1

        if successes >= self.success_count:
            self.halt()
            return NodeStatus.SUCCESS

        if failures > (len(self.children) - self.success_count):
            self.halt()
            return NodeStatus.FAILURE

        return NodeStatus.RUNNING

    def halt(self) -> None:
        for child in self.children:
            if hasattr(child, "halt"):
                child.halt()
