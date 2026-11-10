"""AGX 드라이버 진단(DiagnosticArray) 해석.

``agx_backend`` 는 드라이버가 발행하는 진단만 보고 명령 거부 여부를 결정한다. 이 모듈은
그 판단 규칙을 ROS import 없는 순수 함수로 모아 단위 테스트가 가능하게 한다. 메시지는
duck typing 으로만 다루므로(``.status``, ``.level``, ``.values``, ``.key``, ``.value``)
``diagnostic_msgs`` 가 없는 환경에서도 import 할 수 있다.

드라이버 value 표기:

- ``arm_status``·``motion_status``·``err_status`` 는 10진(``"2"``, ``"02"``) 또는
  16진(``"0x2"``) 문자열로 온다. 드라이버 버전에 따라 표기가 달라지므로 둘 다 해석한다.
- 한 DiagnosticArray 의 DiagnosticStatus 엔트리는 서로 독립이다. 한 엔트리의 IK 코드가
  다른 엔트리의 하드웨어 오류를 가리면 안 되므로 판정은 항상 엔트리 단위로 한다.

모터 진단(``/feedback/motor_status``)의 value 키 이름은 드라이버 버전에 따라 다르므로
알려진 후보를 모두 인식하고, 어떤 후보도 없으면 아무 것도 보고하지 않는다(모터 진단을
발행하지 않는 구 드라이버 호환). 실기 드라이버에서 확인되지 않은 키는 아래
``_MOTOR_FLAG_LABELS``·``_MOTOR_ERROR_CODE_KEYS`` 에 추가한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: ArmStatus.AGX_ARM_NORMAL. 드라이버가 팔 상태 정상을 명시한 엔트리.
ARM_STATUS_OK = 0x00
#: ArmStatus.NO_SOLUTION. pose(move_p) IK 실패의 흔적이며 move_j 는 계속 동작한다.
ARM_STATUS_NO_SOLUTION = 0x02
#: MotionStatus.REACH_TARGET_POS_FAILED. 드라이버가 목표 도달에 실패했다고 보고한 상태.
MOTION_STATUS_TARGET_FAILED = 0x01

#: diagnostic_msgs/DiagnosticStatus.OK, WARN, ERROR
DIAGNOSTIC_OK = 0
DIAGNOSTIC_ERROR = 2

#: 드라이버가 보내는 진단 key. 대소문자/공백 차이는 무시한다.
_ARM_STATUS_KEY = "arm_status"
_MOTION_STATUS_KEY = "motion_status"
_ERR_STATUS_KEY = "err_status"

#: 모터 진단 value 키 → 오류 분류. 드라이버 버전별 이름 후보를 모두 둔다.
_MOTOR_FLAG_LABELS: dict[str, str] = {
    "collision": "collision",
    "collision_flag": "collision",
    "collision_status": "collision",
    "overcurrent": "overcurrent",
    "over_current": "overcurrent",
    "overcurrent_flag": "overcurrent",
    "stall": "stall",
    "stall_flag": "stall",
    "stalled": "stall",
    "driver_error": "driver_error",
    "driver_err": "driver_error",
    "driver_fault": "driver_error",
    "collision_tripped": "collision",
    "stall_tripped": "stall",
    "driver_overcurrent": "overcurrent",
    "motor_overtemp": "overtemperature",
    "driver_overtemp": "overtemperature",
    "undervoltage": "undervoltage",
}
#: 값이 거짓이면 모터가 꺼진 상태다.
_MOTOR_ENABLED_KEYS = frozenset({"enabled", "motor_enabled", "motors_enabled", "servo_enabled"})
#: 값이 참이면 모터가 꺼진 상태다.
_MOTOR_DISABLED_KEYS = frozenset({"disabled", "motor_disabled", "motors_disabled"})
#: 값이 0 이 아니면 드라이버/모터 오류 코드로 본다.
_MOTOR_ERROR_CODE_KEYS = frozenset(
    {
        _ARM_STATUS_KEY,
        _ERR_STATUS_KEY,
        "error_status",
        "error_code",
        "motor_error",
        "motor_err",
        "fault_code",
    }
)

_TRUE_WORDS = frozenset({"true", "yes", "on"})
_FALSE_WORDS = frozenset({"false", "no", "off"})


def parse_status_code(value: Any) -> int | None:
    """진단 value 를 정수 코드로 해석한다(해석할 수 없으면 None).

    ``"0x2"``/``"0X02"`` 는 16진, 그 밖의 숫자 문자열은 10진으로 읽는다. Python 의
    ``int(value, 0)`` 은 ``"02"`` 같은 leading zero 표기를 거부하므로 직접 처리한다.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    sign = 1
    if text[0] in "+-":
        sign = -1 if text[0] == "-" else 1
        text = text[1:]
    base = 16 if text.lower().startswith("0x") else 10
    try:
        return sign * int(text, base)
    except ValueError:
        return None


def parse_flag(value: Any) -> bool | None:
    """진단 value 를 참/거짓으로 해석한다(판단할 수 없으면 None)."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower() if value is not None else ""
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    code = parse_status_code(value)
    if code is None:
        return None
    return code != 0


def _status_level(status: Any) -> int:
    """DiagnosticStatus.level 을 int 로 정규화한다(byte 표기 허용)."""
    level = getattr(status, "level", 0)
    if isinstance(level, (bytes, bytearray)):
        level = level[0] if level else 0
    try:
        return int(level)
    except (TypeError, ValueError):
        return DIAGNOSTIC_OK


def _status_fields(status: Any) -> dict[str, Any]:
    """DiagnosticStatus.values 를 소문자 key 로 정규화한 매핑으로 만든다."""
    fields: dict[str, Any] = {}
    for item in getattr(status, "values", None) or []:
        key = str(getattr(item, "key", "") or "").strip().lower()
        if key:
            fields[key] = getattr(item, "value", "")
    return fields


def _format_code(code: int | None) -> str:
    return "없음" if code is None else str(code)


@dataclass(frozen=True)
class ArmStatusEntry:
    """DiagnosticStatus 한 엔트리에서 읽은 팔 상태(키가 없으면 None)."""

    level: int
    name: str
    message: str
    arm_status: int | None = None
    motion_status: int | None = None
    err_status: int | None = None

    def summary(self) -> str:
        """오류 메시지에 넣을 필드 요약."""
        return (
            f"arm_status={_format_code(self.arm_status)}, "
            f"motion_status={_format_code(self.motion_status)}, "
            f"err_status={_format_code(self.err_status)}"
        )


@dataclass(frozen=True)
class ArmStatusDiagnosis:
    """명령 발행 전 팔 상태 검사 결과."""

    #: 거부 사유가 된 엔트리(없으면 None).
    blocking: ArmStatusEntry | None = None
    #: 조인트 이동이 무시해도 되는 NO_SOLUTION 흔적을 봤는지(경고 로그용).
    tolerated_joint_no_solution: bool = False


@dataclass(frozen=True)
class MotorStatusDiagnosis:
    """모터 진단 검사 결과."""

    #: 실제 하드웨어 오류 분류(collision/overcurrent/stall/driver_error).
    faults: tuple[str, ...] = ()
    #: 모터가 꺼진 상태로 보고되었는지.
    disabled: bool = False
    #: 드라이버가 ERROR 로 보고한 status 요약(메시지 전달용).
    detail: str = ""

    def describe(self) -> str:
        parts: list[str] = list(self.faults)
        if self.disabled:
            parts.append("disabled")
        text = ", ".join(parts) if parts else "이상 없음"
        return f"{text}; {self.detail}" if self.detail else text


def arm_status_entries(msg: Any) -> list[ArmStatusEntry]:
    """DiagnosticArray 를 엔트리별 상태 목록으로 변환한다."""
    entries: list[ArmStatusEntry] = []
    for status in getattr(msg, "status", None) or []:
        fields = _status_fields(status)
        entries.append(
            ArmStatusEntry(
                level=_status_level(status),
                name=str(getattr(status, "name", "") or "arm"),
                message=str(getattr(status, "message", "") or "").strip(),
                arm_status=parse_status_code(fields.get(_ARM_STATUS_KEY)),
                motion_status=parse_status_code(fields.get(_MOTION_STATUS_KEY)),
                err_status=parse_status_code(fields.get(_ERR_STATUS_KEY)),
            )
        )
    return entries


def diagnose_arm_status(msg: Any, *, allow_no_solution: bool) -> ArmStatusDiagnosis:
    """명령 발행 전 팔 상태를 검사한다(ERROR 레벨 엔트리만 본다).

    ``allow_no_solution`` 은 조인트/홈 이동처럼 IK 를 쓰지 않는 명령에서만 True 다.
    이때 ``arm_status=NO_SOLUTION(0x02)`` 이고 ``err_status`` 가 0/없음인 엔트리는 직전
    pose 명령의 흔적이므로 경고만 남기고 넘어간다. 같은 배열의 다른 엔트리는 계속
    검사하므로, 한 엔트리의 IK 코드가 다른 엔트리의 하드웨어 오류를 가리지 않는다.

    ``motion_status`` 는 보지 않는다. 명령 이전부터 남아 있던 목표 도달 실패는 이번
    이동 시도를 막지 않는다(드라이버 재기동 없이 조인트 목표를 한 번 시도하기 위함).
    """
    blocking: ArmStatusEntry | None = None
    tolerated = False
    for entry in arm_status_entries(msg):
        err_clean = entry.err_status in (None, 0)
        if not err_clean:
            if blocking is None:
                blocking = entry
            continue
        if entry.level < DIAGNOSTIC_ERROR:
            continue
        if allow_no_solution and entry.arm_status == ARM_STATUS_NO_SOLUTION and err_clean:
            tolerated = True
            continue
        if entry.arm_status == ARM_STATUS_OK and err_clean:
            # 드라이버가 팔 상태 정상을 보고한 엔트리(motion_status 실패와 분리해서 본다).
            continue
        if blocking is None:
            blocking = entry
    return ArmStatusDiagnosis(blocking=blocking, tolerated_joint_no_solution=tolerated)


def diagnose_motion_faults(msg: Any, *, allow_no_solution: bool) -> ArmStatusEntry | None:
    """이동 시작 이후 **새로 도착한** 팔 진단에서 이번 명령의 실패를 찾는다.

    드라이버는 ``motion_status=1``(REACH_TARGET_POS_FAILED)을 level 0 으로도 발행하므로
    레벨과 무관하게 확인한다. ``err_status`` 가 0 이 아니거나 ERROR 레벨의 ``arm_status``
    가 남아 있으면 같은 명령을 반복 발행하지 않고 즉시 실패로 알린다.
    """
    for entry in arm_status_entries(msg):
        if entry.motion_status == MOTION_STATUS_TARGET_FAILED:
            return entry
        if entry.err_status not in (None, 0):
            return entry
        if entry.level < DIAGNOSTIC_ERROR:
            continue
        if allow_no_solution and entry.arm_status == ARM_STATUS_NO_SOLUTION:
            continue
        if entry.arm_status == ARM_STATUS_OK:
            continue
        return entry
    return None


def diagnose_motor_status(msg: Any) -> MotorStatusDiagnosis:
    """모터 진단(DiagnosticArray)에서 하드웨어 오류와 모터 꺼짐 상태를 읽는다.

    모터 진단 샘플이 없으면(``msg`` 가 None) 아무 것도 보고하지 않는다.
    """
    faults: list[str] = []
    disabled = False
    details: list[str] = []
    for status in getattr(msg, "status", None) or []:
        name = str(getattr(status, "name", "") or "motor")
        message = str(getattr(status, "message", "") or "").strip()
        if _status_level(status) >= DIAGNOSTIC_ERROR:
            details.append(f"{name}: {message}" if message else name)
        for key, value in _status_fields(status).items():
            label = _MOTOR_FLAG_LABELS.get(key)
            if label is not None and parse_flag(value) is True and label not in faults:
                faults.append(label)
            if key in _MOTOR_ENABLED_KEYS and parse_flag(value) is False:
                disabled = True
            if key in _MOTOR_DISABLED_KEYS and parse_flag(value) is True:
                disabled = True
            if key in _MOTOR_ERROR_CODE_KEYS:
                code = parse_status_code(value)
                if code and "driver_error" not in faults:
                    faults.append("driver_error")
    return MotorStatusDiagnosis(
        faults=tuple(faults),
        disabled=disabled,
        detail="; ".join(details),
    )
