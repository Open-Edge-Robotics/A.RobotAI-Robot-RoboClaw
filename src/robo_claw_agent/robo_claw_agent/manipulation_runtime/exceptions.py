class ManipulationError(RuntimeError):
    """manipulation 런타임 공통 예외"""


class ManipulationConfigError(ManipulationError):
    """설정 오류"""


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
