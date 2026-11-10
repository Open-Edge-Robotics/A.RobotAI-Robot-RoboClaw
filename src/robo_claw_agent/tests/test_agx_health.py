"""AGX 드라이버 진단 해석 헬퍼 단위 테스트.

`agx_health` 는 ROS 메시지를 duck typing 으로만 다루므로 이 테스트는
`diagnostic_msgs` 없이 순수 객체 대역으로 검증한다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load the pure module without the ROS-dependent package initializer.
_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "robo_claw_agent/manipulation_runtime/agx_health.py"
)
_SPEC = importlib.util.spec_from_file_location("agx_health_unit", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
agx_health = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = agx_health
_SPEC.loader.exec_module(agx_health)
diagnose_arm_status = agx_health.diagnose_arm_status
diagnose_motion_faults = agx_health.diagnose_motion_faults
diagnose_motor_status = agx_health.diagnose_motor_status
parse_flag = agx_health.parse_flag
parse_status_code = agx_health.parse_status_code

pytestmark = pytest.mark.unit


class _Value:
    def __init__(self, key: str, value: object) -> None:
        self.key = key
        self.value = value


class _Status:
    def __init__(
        self, level: object, name: str = "arm", message: str = "", values: object = None
    ) -> None:
        self.level = level
        self.name = name
        self.message = message
        self.values = list(values or [])


class _Array:
    def __init__(self, *statuses: _Status) -> None:
        self.status = list(statuses)


def _status(
    level: object,
    *,
    name: str = "arm",
    message: str = "",
    arm_status: object = None,
    motion_status: object = None,
    err_status: object = None,
) -> _Status:
    values: list[_Value] = []
    if arm_status is not None:
        values.append(_Value("arm_status", arm_status))
    if motion_status is not None:
        values.append(_Value("motion_status", motion_status))
    if err_status is not None:
        values.append(_Value("err_status", err_status))
    return _Status(level, name=name, message=message, values=values)


def _motor(*values: tuple[str, object], level: object = 0, name: str = "motor") -> _Array:
    return _Array(_Status(level, name=name, values=[_Value(k, v) for k, v in values]))


# ── value 파싱 ──


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2", 2),
        ("02", 2),
        ("0x2", 2),
        ("0X02", 2),
        (" 0x05 ", 5),
        ("5", 5),
        (0x02, 2),
        ("", None),
        ("abc", None),
        (None, None),
    ],
)
def test_parse_status_code_accepts_decimal_leading_zero_and_hex(raw: object, expected: int | None):
    """드라이버는 '02'(10진 leading zero)와 '0x2'(16진) 표기를 모두 쓴다."""
    assert parse_status_code(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("FALSE", False),
        ("on", True),
        ("off", False),
        ("1", True),
        ("0", False),
        ("0x00", False),
        ("0x02", True),
        ("idle", None),
        (None, None),
    ],
)
def test_parse_flag_accepts_boolean_words_and_codes(raw: object, expected: bool | None):
    assert parse_flag(raw) is expected


# ── arm_status 배열 해석 ──


def test_arm_status_entries_reads_each_status_independently():
    """엔트리마다 자기 code/err_status 를 갖는다(배열 전체 공유 코드 없음)."""
    msg = _Array(
        _status(2, name="joint1", arm_status="02", err_status="0", motion_status="0"),
        _status(0, name="joint2", arm_status="0x5", err_status="0"),
    )

    entries = agx_health.arm_status_entries(msg)

    assert [entry.name for entry in entries] == ["joint1", "joint2"]
    assert [entry.arm_status for entry in entries] == [2, 5]
    assert entries[0].err_status == 0


def test_no_solution_with_zero_err_status_is_tolerated_for_joint_commands():
    diagnosis = diagnose_arm_status(
        _Array(_status(2, arm_status="02", err_status="0")), allow_no_solution=True
    )

    assert diagnosis.blocking is None
    assert diagnosis.tolerated_joint_no_solution is True


def test_no_solution_without_err_status_is_tolerated():
    diagnosis = diagnose_arm_status(_Array(_status(2, arm_status="0x02")), allow_no_solution=True)

    assert diagnosis.blocking is None
    assert diagnosis.tolerated_joint_no_solution is True


def test_no_solution_with_nonzero_err_status_blocks():
    """err_status 가 0 이 아니면 IK 흔적이 아니라 실제 오류다."""
    diagnosis = diagnose_arm_status(
        _Array(_status(2, arm_status="02", err_status="5")), allow_no_solution=True
    )

    assert diagnosis.blocking is not None
    assert "err_status=5" in diagnosis.blocking.summary()


def test_no_solution_blocks_pose_commands():
    diagnosis = diagnose_arm_status(
        _Array(_status(2, arm_status="02", err_status="0")), allow_no_solution=False
    )

    assert diagnosis.blocking is not None
    assert diagnosis.blocking.arm_status == agx_health.ARM_STATUS_NO_SOLUTION
    assert diagnosis.tolerated_joint_no_solution is False


@pytest.mark.parametrize("code", ["0x03", "0x04"])
def test_singularity_and_target_limit_are_not_tolerated(code: str):
    """0x03 SINGULARITY_POINT / 0x04 TARGET_POS_EXCEEDS_LIMIT 는 복구가 검증되지 않았다."""
    diagnosis = diagnose_arm_status(
        _Array(_status(2, arm_status=code, err_status="0")), allow_no_solution=True
    )

    assert diagnosis.blocking is not None
    assert diagnosis.blocking.arm_status == parse_status_code(code)


def test_no_solution_in_one_status_does_not_mask_hardware_error_in_another():
    """한 엔트리의 IK 코드가 다른 엔트리의 통신 오류를 가리면 안 된다."""
    msg = _Array(
        _status(2, name="arm", arm_status="02", err_status="0"),
        _status(
            2,
            name="joint7",
            message="arm_status=JOINT_COMMUNICATION_ERR(0x5)",
            arm_status="0x5",
            err_status="0",
        ),
    )

    diagnosis = diagnose_arm_status(msg, allow_no_solution=True)

    assert diagnosis.blocking is not None
    assert diagnosis.blocking.name == "joint7"
    assert "arm_status=5" in diagnosis.blocking.summary()


def test_ok_level_statuses_do_not_block_commands():
    diagnosis = diagnose_arm_status(
        _Array(_status(0, arm_status="0x5", motion_status="1")), allow_no_solution=True
    )

    assert diagnosis.blocking is None


def test_pre_existing_motion_failure_does_not_block_new_command():
    """명령 이전부터 남은 motion_status 실패는 새 조인트 이동 시도를 막지 않는다."""
    msg = _Array(_status(2, arm_status="0", err_status="0", motion_status="1"))

    assert diagnose_arm_status(msg, allow_no_solution=True).blocking is None


def test_bytes_level_is_normalized():
    """DiagnosticStatus.level 은 byte 로도 올 수 있다."""
    diagnosis = diagnose_arm_status(
        _Array(_status(b"\x02", arm_status="0x5")), allow_no_solution=True
    )

    assert diagnosis.blocking is not None


# ── 이동 시작 이후 새 샘플 검사 ──


def test_motion_failure_blocks_even_at_ok_level():
    """드라이버는 motion_status=1 을 level 0 으로도 발행한다."""
    msg = _Array(_status(0, arm_status="0", err_status="0", motion_status="1"))

    entry = diagnose_motion_faults(msg, allow_no_solution=True)

    assert entry is not None
    assert "arm_status=0" in entry.summary()
    assert "motion_status=1" in entry.summary()


def test_nonzero_err_status_blocks_before_dispatch_even_at_ok_level():
    result = diagnose_arm_status(
        _Array(_status(0, arm_status="0", err_status="3")), allow_no_solution=True
    )

    assert result.blocking is not None
    assert result.blocking.err_status == 3


def test_nonzero_err_status_in_new_sample_blocks():
    entry = diagnose_motion_faults(
        _Array(_status(0, arm_status="0x5", err_status="3")), allow_no_solution=True
    )

    assert entry is not None


def test_new_no_solution_error_is_tolerated_only_for_joint_commands():
    msg = _Array(_status(2, arm_status="02", err_status="0"))

    assert diagnose_motion_faults(msg, allow_no_solution=True) is None
    assert diagnose_motion_faults(msg, allow_no_solution=False) is not None


def test_new_hardware_error_blocks():
    entry = diagnose_motion_faults(
        _Array(_status(2, name="joint4", message="BRAKE_NOT_RELEASED", arm_status="0x07")),
        allow_no_solution=True,
    )

    assert entry is not None
    assert "arm_status=7" in entry.summary()


def test_healthy_new_sample_reports_nothing():
    msg = _Array(_status(0, arm_status="0", err_status="0", motion_status="0"))

    assert diagnose_motion_faults(msg, allow_no_solution=True) is None


# ── motor_status 해석 ──


@pytest.mark.parametrize(
    ("key", "value", "label"),
    [
        ("collision", "true", "collision"),
        ("collision_flag", "1", "collision"),
        ("overcurrent", "true", "overcurrent"),
        ("over_current", "1", "overcurrent"),
        ("stall", "true", "stall"),
        ("stalled", "1", "stall"),
        ("driver_error", "true", "driver_error"),
        ("driver_fault", "1", "driver_error"),
        ("collision_tripped", "true", "collision"),
        ("stall_tripped", "true", "stall"),
        ("driver_overcurrent", "true", "overcurrent"),
        ("motor_overtemp", "true", "overtemperature"),
        ("driver_overtemp", "true", "overtemperature"),
        ("undervoltage", "true", "undervoltage"),
    ],
)
def test_motor_status_fault_flags_are_reported(key: str, value: str, label: str):
    diagnosis = diagnose_motor_status(_motor((key, value)))

    assert diagnosis.faults == (label,)
    assert diagnosis.disabled is False


@pytest.mark.parametrize("key", ["err_status", "error_code", "motor_error"])
def test_nonzero_motor_error_code_is_reported_as_driver_error(key: str):
    assert diagnose_motor_status(_motor((key, "0x03"))).faults == ("driver_error",)
    assert diagnose_motor_status(_motor((key, "0"))).faults == ()


def test_cleared_fault_flag_is_not_reported():
    diagnosis = diagnose_motor_status(_motor(("collision", "false"), ("overcurrent", "0")))

    assert diagnosis.faults == ()


def test_disabled_motor_is_reported_from_enabled_flag():
    diagnosis = diagnose_motor_status(_motor(("enabled", "false")))

    assert diagnosis.disabled is True
    assert diagnosis.faults == ()


def test_disabled_motor_is_reported_from_disabled_flag():
    assert diagnose_motor_status(_motor(("motor_disabled", "true"))).disabled is True


def test_missing_motor_status_reports_nothing():
    """모터 진단을 한 번도 받지 못한 구 드라이버 환경은 기존처럼 동작해야 한다."""
    diagnosis = diagnose_motor_status(None)

    assert diagnosis.faults == ()
    assert diagnosis.disabled is False


def test_unknown_motor_keys_are_ignored():
    diagnosis = diagnose_motor_status(_motor(("temperature_c", "42")))

    assert diagnosis.faults == ()
    assert diagnosis.disabled is False


def test_motor_description_lists_faults_and_disabled():
    diagnosis = diagnose_motor_status(_motor(("collision", "true"), ("enabled", "0")))

    description = diagnosis.describe()

    assert "collision" in description
    assert "disabled" in description
