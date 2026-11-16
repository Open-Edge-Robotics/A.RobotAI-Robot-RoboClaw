class ManipulationError(RuntimeError):
    """manipulation 런타임 공통 예외"""


class ManipulationConfigError(ManipulationError):
    """설정 오류"""


class ManipulationEmergencyStopError(ManipulationError):
    """비상 정지(E-stop) 래치 상태에서 매니퓰레이션 명령이 차단된 경우.

    AGENTS.md §8 은 emergency stop 이 모든 실행 경로보다 우선한다고 규정한다.
    이 예외는 팔/그리퍼 명령이 백엔드로 전달되기 전에 발생하며, 스킬 계층은
    ``stopped_by``/``failure_reason`` 값을 결과 payload 에 그대로 싣어
    "정지 때문에 거부됨"과 "실행 중 실패"를 구분해 보고한다.
    """

    stopped_by = "emergency_stop"
    reason = "emergency_stop_active"


class ManipulationLimitsUnavailableError(ManipulationConfigError):
    """조인트 한계 검증에 필요한 robot limits 를 찾지 못해 명령을 차단한 경우.

    안전 검증은 근거 데이터가 없을 때 통과가 아니라 차단(fail-closed)이어야 한다.
    """

    reason = "robot_limits_missing"


class ManipulationRuntimeUnavailableError(ManipulationError):
    """MoveIt 런타임 또는 기능 미사용 가능"""


class ManipulationContactError(ManipulationError):
    """guarded motion contact 감지로 trajectory가 중단된 경우.

    stretch_core joint_trajectory_server는 contact 감지 시 표준
    FollowJointTrajectory.Result 상수가 아닌 error_code=100 과
    "X contact detected." error_string을 반환한다. 이 예외는 접촉으로
    이동이 조기 종료된 것이지, goal 자체가 거부된 것은 아니다.

    준비자세(pre-grasp / ready pose) 이동처럼 "contact 시점의 현재 자세에서
    이후 단계(시각 서보 등)가 안전하게 시작될 수 있는" 컨텍스트에서는
    치명적 실패로 보지 않고 tolerate_contact 로 수용한다. 그 외 컨텍스트에서는
    일반 ManipulationError 와 동일하게 실패로 전파된다(서브클래스).
    """
