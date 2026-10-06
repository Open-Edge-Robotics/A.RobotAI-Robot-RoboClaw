from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class AgentState(IntEnum):
    """에이전트 동작 상태 (robo_claw_msgs/AgentStatus와 동기화)"""

    IDLE = 0
    THINKING = 1
    EXECUTING = 2
    WAITING = 3
    ERROR = 4


class SkillResultCode(IntEnum):
    """스킬 실행 결과 코드"""

    SUCCESS = 0
    FAILURE = 1
    CANCELLED = 2
    TIMEOUT = 3


@dataclass(slots=True)
class RobotPose:
    """로봇 좌표 데이터 구조체"""

    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0
    frame: str = "map"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RobotPose | None":
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                x=float(data.get("x", 0.0)),
                y=float(data.get("y", 0.0)),
                yaw=float(data.get("yaw", 0.0)),
                frame=str(data.get("frame", "map")),
            )
        except (ValueError, TypeError):
            return None


@dataclass(slots=True)
class SkillChainItem:
    """스킬 체인 항목 구조체"""

    skill: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMPlanResult:
    """LLM 플래닝 수립 결과 구조체"""

    skills: list[SkillChainItem] = field(default_factory=list)
    response: str = ""
    reason: str = ""
    final: bool = False
    # 파싱 실패 신호. parse_llm_plan이 설정하며, 플래너가 폴백/재시도 판단에 사용한다.
    # (병합 시 파서는 이 인자를 넘기는데 데이터클래스엔 누락돼 매 파싱이 TypeError로 실패 →
    #  원시 LLM 출력이 그대로 노출되던 회귀를 유발했음)
    parse_failed: bool = False
