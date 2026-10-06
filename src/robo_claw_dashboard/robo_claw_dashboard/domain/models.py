"""대시보드 도메인 모델 — 에이전트 상태 코드 매핑 등 순수 규칙."""

# robo_claw_msgs/msg/AgentStatus.msg 상태 코드 매핑
AGENT_STATE_NAMES = {
    0: "IDLE",
    1: "THINKING",
    2: "EXECUTING",
    3: "WAITING",
    4: "ERROR",
}

UNKNOWN_STATE = "UNKNOWN"


def normalize_state_code(code: int | None) -> str:
    """AgentStatus.state 정수 코드를 사람이 읽을 수 있는 문자열로 변환한다."""
    if code is None:
        return UNKNOWN_STATE
    return AGENT_STATE_NAMES.get(code, UNKNOWN_STATE)
