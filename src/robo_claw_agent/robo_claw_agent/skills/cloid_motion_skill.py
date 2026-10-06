"""CLOi's registered motion catalog and guarded MotionCmd publisher."""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from pathlib import Path
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)

MOTION_MAP_TOPIC = "/task_manager/motion_map"
MOTION_COMMAND_TOPIC = "/task_manager/motion_cmd"
MOTION_COMMAND_START = 0
MOTION_COMMAND_STOP = 1
_MAX_CATALOG_BYTES = 262_144
_MAX_CATALOG_ENTRIES = 512
_DEFAULT_CATALOG_TIMEOUT_SEC = 3.0
_MAX_CATALOG_TIMEOUT_SEC = 10.0
_DEFAULT_CONNECTION_TIMEOUT_SEC = 2.0
_MIN_CONNECTION_TIMEOUT_SEC = 0.1
_MAX_CONNECTION_TIMEOUT_SEC = 10.0
_SUBSCRIPTION_POLL_SEC = 0.05


def _load_string_type() -> type[Any]:
    """Load std_msgs lazily so this module remains importable in non-ROS tests."""
    from std_msgs.msg import String

    return String


def _create_motion_publisher(node: Any) -> tuple[Any, type[Any]]:
    """Create a reliable, volatile publisher for CLOi's documented MotionCmd topic."""
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

    from task_manager_msgs.msg import MotionCmd

    qos = QoSProfile(
        depth=1,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.VOLATILE,
    )
    return node.create_publisher(MotionCmd, MOTION_COMMAND_TOPIC, qos), MotionCmd


def _bounded_timeout(value: Any, *, default: float, minimum: float, maximum: float) -> float | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(timeout) or timeout < minimum or timeout > maximum:
        return None
    return timeout


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _normalize_pre_id(value: Any) -> int | None:
    if value is None or value == "" or value == 0 or value == "0":
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    result = _positive_int(value)
    if result is None:
        raise ValueError("모션 카탈로그의 pre_id 형식이 올바르지 않습니다.")
    return result


def _parse_motion_catalog(message: Any) -> list[dict[str, Any]]:
    """Parse the JSON catalog published by ScenarioManager on motion_map."""
    raw = getattr(message, "data", None)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("빈 모션 카탈로그입니다.")
    if len(raw.encode("utf-8")) > _MAX_CATALOG_BYTES:
        raise ValueError("모션 카탈로그 크기가 제한을 초과했습니다.")

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("모션 카탈로그가 JSON 형식이 아닙니다.") from exc

    values = data.get("motions") if isinstance(data, dict) else None
    if not isinstance(values, list) or len(values) > _MAX_CATALOG_ENTRIES:
        raise ValueError("모션 카탈로그는 제한된 크기의 motions 배열이어야 합니다.")

    motions: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("모션 카탈로그 항목은 JSON 객체여야 합니다.")
        motion_id = _positive_int(value.get("id"))
        name = value.get("display_name")
        intent_id = value.get("intent_id", "")
        description = value.get("description", "")
        mask = value.get("mask", "")
        if motion_id is None or motion_id in seen_ids:
            raise ValueError("모션 카탈로그의 id는 중복 없는 양의 정수여야 합니다.")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("모션 카탈로그 항목에 display_name이 없습니다.")
        if not all(isinstance(item, str) for item in (intent_id, description, mask)):
            raise ValueError("모션 카탈로그의 설명 필드 형식이 올바르지 않습니다.")

        seen_ids.add(motion_id)
        motions.append(
            {
                "motion_id": motion_id,
                "motion_name": name.strip(),
                "intent_id": intent_id.strip(),
                "description": description.strip(),
                "mask": mask.strip(),
                "pre_id": _normalize_pre_id(value.get("pre_id", "")),
            }
        )
    return motions


def _catalog_file_path(node: Any) -> str:
    """Read the optional read-only catalog fallback parameter, if declared."""
    try:
        if node is None or not node.has_parameter("cloid_motion_catalog_file"):
            return ""
        value = node.get_parameter("cloid_motion_catalog_file").get_parameter_value().string_value
        return str(value or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read CLOi motion catalog file parameter: %s", exc)
        return ""


def _read_catalog_file(path_value: str) -> list[dict[str, Any]] | None:
    if not path_value:
        return None
    try:
        path = Path(path_value).expanduser()
        if not path.is_file():
            return None
        if path.stat().st_size > _MAX_CATALOG_BYTES:
            raise ValueError("모션 카탈로그 파일 크기가 제한을 초과했습니다.")
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        logger.warning("CLOi motion catalog file is unavailable (%s): %s", path_value, exc)
        return None
    return _parse_motion_catalog(type("CatalogMessage", (), {"data": raw})())


def _read_motion_catalog(
    skill: BaseSkill, timeout_sec: float
) -> tuple[list[dict[str, Any]] | None, str, str]:
    """Read the live transient-local catalog, then an optional mounted file fallback."""
    try:
        message = skill.wait_for_message(
            _load_string_type(),
            MOTION_MAP_TOPIC,
            timeout_sec=timeout_sec,
            use_transient_local=True,
        )
    except Exception as exc:  # ROS can fail while constructing a subscription.
        logger.warning("CLOi motion catalog subscription failed: %s", exc)
        message = None

    if message is not None:
        try:
            return _parse_motion_catalog(message), MOTION_MAP_TOPIC, ""
        except ValueError as exc:
            logger.warning("Invalid CLOi motion catalog: %s", exc)
            return None, "", "invalid_motion_catalog"

    fallback_path = _catalog_file_path(getattr(skill, "node", None))
    try:
        fallback = _read_catalog_file(fallback_path)
    except ValueError as exc:
        logger.warning("Invalid CLOi motion catalog file: %s", exc)
        return None, "", "invalid_motion_catalog"
    if fallback is not None:
        return fallback, fallback_path, ""
    return None, "", "motion_catalog_unavailable"


def _public_motion(motion: dict[str, Any]) -> dict[str, Any]:
    return {
        "motion_id": motion["motion_id"],
        "motion_name": motion["motion_name"],
        "intent_id": motion["intent_id"],
        "description": motion["description"],
        "mask": motion["mask"],
        "pre_id": motion["pre_id"],
    }


def _wait_for_subscription(publisher: Any, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            if publisher.get_subscription_count() > 0:
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not inspect CLOi MotionCmd subscribers: %s", exc)
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            return False
        time.sleep(min(_SUBSCRIPTION_POLL_SEC, remaining))


class ListCloidMotionsSkill(BaseSkill):
    """Read the concrete name/id catalog registered by CLOi's ScenarioManager."""

    name = "list_cloid_motions"
    risk_level = "read"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "timeout_sec": {"type": "number", "minimum": 0.1, "maximum": 10.0},
            "intent_id": {"type": "string"},
            "search": {"type": "string", "maxLength": 80},
        },
        "additionalProperties": False,
    }
    description = (
        "CLOi의 /task_manager/motion_map에 등록된 실제 모션 이름, ID, 카테고리, 설명과 "
        "필수 선행 동작을 조회합니다."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if (error := self.require_node()) is not None:
            return error

        timeout_sec = _bounded_timeout(
            params.get("timeout_sec"),
            default=_DEFAULT_CATALOG_TIMEOUT_SEC,
            minimum=0.1,
            maximum=_MAX_CATALOG_TIMEOUT_SEC,
        )
        if timeout_sec is None:
            return self.fail_result(
                "모션 목록 조회 타임아웃은 0.1~10초 범위여야 합니다.",
                failure_reason="invalid_timeout",
            )
        intent_filter = params.get("intent_id", "")
        search = params.get("search", "")
        if not isinstance(intent_filter, str) or not isinstance(search, str):
            return self.fail_result(
                "intent_id와 search는 문자열이어야 합니다.", failure_reason="invalid_filter"
            )

        motions, source, failure_reason = _read_motion_catalog(self, timeout_sec)
        if motions is None:
            message = (
                "CLOi의 실제 모션 목록을 수신하지 못했습니다. "
                f"{MOTION_MAP_TOPIC} 발행 상태를 확인하거나 읽기 전용 "
                "cloid_motion_catalog_file 경로를 설정하세요."
                if failure_reason == "motion_catalog_unavailable"
                else "CLOi 모션 목록 형식이 올바르지 않습니다."
            )
            return self.fail_result(message, failure_reason=failure_reason)

        search_key = search.strip().casefold()
        filtered = [
            motion
            for motion in motions
            if (not intent_filter or motion["intent_id"] == intent_filter.strip())
            and (
                not search_key
                or search_key
                in " ".join(
                    (
                        motion["motion_name"],
                        motion["intent_id"],
                        motion["description"],
                    )
                ).casefold()
            )
        ]
        source_key = "source_topic" if source == MOTION_MAP_TOPIC else "source_file"
        return self.success_result(
            f"CLOi에 등록된 실제 모션 {len(filtered)}개를 조회했습니다.",
            motion_count=len(filtered),
            motions=[_public_motion(motion) for motion in filtered],
            **{source_key: source},
        )


class ExecuteCloidMotionSkill(BaseSkill):
    """Publish one explicitly confirmed registered motion ID to CLOi Motion Player."""

    name = "execute_cloid_motion"
    risk_level = "dangerous"
    allow_with_others = False
    side_effects = ("arm_motion", "gripper_motion")
    exclusive_resources = ("cloid_motion",)
    input_schema = {
        "type": "object",
        "properties": {
            "motion_name": {"type": "string", "description": "목록에 표시된 정확한 이름"},
            "motion_id": {"type": "integer", "minimum": 1},
            "confirm": {"type": "boolean"},
            "connection_timeout_sec": {
                "type": "number",
                "minimum": _MIN_CONNECTION_TIMEOUT_SEC,
                "maximum": _MAX_CONNECTION_TIMEOUT_SEC,
            },
        },
        "required": ["motion_name", "confirm"],
        "additionalProperties": False,
    }
    description = (
        "list_cloid_motions 결과의 정확한 이름을 CLOi에 등록된 motion_id로 해석해 "
        "MotionCmd START 명령을 한 번 발행합니다. 동일 이름이 여러 개면 motion_id로 "
        "선택해야 합니다. 사용자의 명시적 실행 요청이 있을 때만 confirm=true로 호출합니다."
    )

    def __init__(self) -> None:
        super().__init__()
        self._publish_lock = threading.Lock()
        self._publisher: Any | None = None
        self._publisher_node: Any | None = None
        self._message_type: type[Any] | None = None

    def _get_publisher(self) -> tuple[Any, type[Any]]:
        if self._publisher is None or self._publisher_node is not self.node:
            self._publisher, self._message_type = _create_motion_publisher(self.node)
            self._publisher_node = self.node
        return self._publisher, self._message_type

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if (error := self.require_node()) is not None:
            return error
        if params.get("confirm") is not True:
            return self.fail_result(
                "실제 CLOi 모션 실행은 사용자의 명시적 요청이 필요합니다 (confirm=true).",
                failure_reason="safety_rejected",
            )

        motion_name = params.get("motion_name")
        if not isinstance(motion_name, str) or not motion_name.strip():
            return self.fail_result(
                "motion_name이 필요합니다.", failure_reason="invalid_motion_name"
            )
        motion_name = motion_name.strip()
        requested_id = params.get("motion_id")
        if requested_id is not None and _positive_int(requested_id) is None:
            return self.fail_result(
                "motion_id는 등록된 양의 정수여야 합니다.", failure_reason="invalid_motion_id"
            )

        connection_timeout_sec = _bounded_timeout(
            params.get("connection_timeout_sec"),
            default=_DEFAULT_CONNECTION_TIMEOUT_SEC,
            minimum=_MIN_CONNECTION_TIMEOUT_SEC,
            maximum=_MAX_CONNECTION_TIMEOUT_SEC,
        )
        if connection_timeout_sec is None:
            return self.fail_result(
                "MotionCmd 연결 타임아웃은 0.1~10초 범위여야 합니다.",
                failure_reason="invalid_timeout",
            )

        if not self._publish_lock.acquire(blocking=False):
            return self.fail_result(
                "다른 CLOi 모션 명령을 처리 중입니다.",
                failure_reason="motion_busy",
                recoverable=True,
            )

        try:
            motions, source, failure_reason = _read_motion_catalog(
                self, _DEFAULT_CATALOG_TIMEOUT_SEC
            )
            if motions is None:
                return self.fail_result(
                    "최신 CLOi 모션 목록을 확인할 수 없어 실행을 차단했습니다.",
                    failure_reason=failure_reason,
                    motion_name=motion_name,
                )

            candidates = [motion for motion in motions if motion["motion_name"] == motion_name]
            if not candidates:
                return self.fail_result(
                    f"CLOi 모션 목록에 등록되지 않은 이름입니다: {motion_name}",
                    failure_reason="motion_not_registered",
                    motion_name=motion_name,
                )
            if requested_id is not None:
                selected = next(
                    (motion for motion in candidates if motion["motion_id"] == requested_id), None
                )
                if selected is None:
                    return self.fail_result(
                        "motion_name과 motion_id가 현재 등록 목록의 같은 항목을 가리키지 않습니다.",
                        failure_reason="motion_selection_mismatch",
                        motion_name=motion_name,
                        motion_id=requested_id,
                        candidates=[_public_motion(motion) for motion in candidates],
                    )
            elif len(candidates) > 1:
                return self.fail_result(
                    f"'{motion_name}' 이름이 여러 모션에 등록되어 있어 motion_id 선택이 필요합니다.",
                    failure_reason="ambiguous_motion_name",
                    motion_name=motion_name,
                    candidates=[_public_motion(motion) for motion in candidates],
                )
            else:
                selected = candidates[0]

            pre_id = selected["pre_id"]
            if pre_id is not None:
                return self.fail_result(
                    f"이 모션은 선행 모션 ID {pre_id} 완료가 필요합니다. 자동 연속 실행은 하지 않습니다.",
                    failure_reason="motion_prerequisite_required",
                    motion_name=motion_name,
                    motion_id=selected["motion_id"],
                    required_pre_motion_id=pre_id,
                )

            try:
                publisher, message_type = self._get_publisher()
            except (ImportError, AttributeError) as exc:
                logger.error("CLOi MotionCmd interface is unavailable: %s", exc)
                return self.fail_result(
                    "task_manager_msgs/msg/MotionCmd 인터페이스를 사용할 수 없습니다. "
                    "CLOi 메시지 패키지를 RoboClaw 런타임에 설치해야 합니다.",
                    failure_reason="motion_interface_unavailable",
                    motion_name=motion_name,
                    motion_id=selected["motion_id"],
                )

            if not _wait_for_subscription(publisher, connection_timeout_sec):
                return self.fail_result(
                    f"Motion Player 구독자를 찾을 수 없습니다: {MOTION_COMMAND_TOPIC}",
                    failure_reason="motion_player_unavailable",
                    motion_name=motion_name,
                    motion_id=selected["motion_id"],
                )

            command = message_type()
            command.motion_id = selected["motion_id"]
            command.command_type = MOTION_COMMAND_START
            try:
                publisher.publish(command)
            except Exception as exc:  # noqa: BLE001
                logger.warning("CLOi MotionCmd publish failed: %s", exc)
                return self.fail_result(
                    f"CLOi 모션 명령을 발행하지 못했습니다: {exc}",
                    failure_reason="motion_publish_failed",
                    motion_name=motion_name,
                    motion_id=selected["motion_id"],
                )

            source_key = "source_topic" if source == MOTION_MAP_TOPIC else "source_file"
            return self.success_result(
                f"CLOi 모션 시작 명령을 발행했습니다: {motion_name} (ID {selected['motion_id']}). "
                "이 토픽은 실행 완료 응답을 제공하지 않으므로 동작 완료는 확인되지 않았습니다.",
                status="sent",
                motion_name=motion_name,
                motion_id=selected["motion_id"],
                command_type=MOTION_COMMAND_START,
                completion_confirmed=False,
                **{source_key: source},
            )
        finally:
            self._publish_lock.release()


class StopCloidMotionSkill(BaseSkill):
    """Publish CLOi MotionCmd STOP as a user-confirmed safety control."""

    name = "stop_cloid_motion"
    risk_level = "dangerous"
    allow_with_others = False
    side_effects = ("arm_motion", "gripper_motion")
    exclusive_resources = ("cloid_motion",)
    input_schema = {
        "type": "object",
        "properties": {
            "confirm": {"type": "boolean"},
            "connection_timeout_sec": {
                "type": "number",
                "minimum": _MIN_CONNECTION_TIMEOUT_SEC,
                "maximum": _MAX_CONNECTION_TIMEOUT_SEC,
            },
        },
        "required": ["confirm"],
        "additionalProperties": False,
    }
    description = (
        "사용자의 명시적인 정지 요청을 받아 CLOi Motion Player에 STOP 명령을 한 번 발행합니다. "
        "토픽이 정지 완료 응답을 제공하지 않으므로 로봇 상태를 별도로 확인해야 합니다."
    )

    def __init__(self) -> None:
        super().__init__()
        self._publish_lock = threading.Lock()
        self._publisher: Any | None = None
        self._publisher_node: Any | None = None
        self._message_type: type[Any] | None = None

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if (error := self.require_node()) is not None:
            return error
        if params.get("confirm") is not True:
            return self.fail_result(
                "모션 정지 명령은 사용자의 명시적 요청이 필요합니다 (confirm=true).",
                failure_reason="safety_rejected",
            )

        timeout_sec = _bounded_timeout(
            params.get("connection_timeout_sec"),
            default=_DEFAULT_CONNECTION_TIMEOUT_SEC,
            minimum=_MIN_CONNECTION_TIMEOUT_SEC,
            maximum=_MAX_CONNECTION_TIMEOUT_SEC,
        )
        if timeout_sec is None:
            return self.fail_result(
                "MotionCmd 연결 타임아웃은 0.1~10초 범위여야 합니다.",
                failure_reason="invalid_timeout",
            )
        if not self._publish_lock.acquire(blocking=False):
            return self.fail_result(
                "다른 CLOi 모션 명령을 처리 중입니다.",
                failure_reason="motion_busy",
                recoverable=True,
            )

        try:
            try:
                if self._publisher is None or self._publisher_node is not self.node:
                    self._publisher, self._message_type = _create_motion_publisher(self.node)
                    self._publisher_node = self.node
                publisher, message_type = self._publisher, self._message_type
            except (ImportError, AttributeError) as exc:
                logger.error("CLOi MotionCmd interface is unavailable: %s", exc)
                return self.fail_result(
                    "task_manager_msgs/msg/MotionCmd 인터페이스를 사용할 수 없습니다.",
                    failure_reason="motion_interface_unavailable",
                )

            if not _wait_for_subscription(publisher, timeout_sec):
                return self.fail_result(
                    f"Motion Player 구독자를 찾을 수 없습니다: {MOTION_COMMAND_TOPIC}",
                    failure_reason="motion_player_unavailable",
                )

            command = message_type()
            command.motion_id = 0
            command.command_type = MOTION_COMMAND_STOP
            try:
                publisher.publish(command)
            except Exception as exc:  # noqa: BLE001
                logger.warning("CLOi MotionCmd stop publish failed: %s", exc)
                return self.fail_result(
                    f"CLOi 모션 정지 명령을 발행하지 못했습니다: {exc}",
                    failure_reason="motion_publish_failed",
                )

            return self.success_result(
                "CLOi 모션 STOP 명령을 발행했습니다. 정지 완료 여부는 확인되지 않았습니다.",
                status="sent",
                motion_id=0,
                command_type=MOTION_COMMAND_STOP,
                completion_confirmed=False,
            )
        finally:
            self._publish_lock.release()
