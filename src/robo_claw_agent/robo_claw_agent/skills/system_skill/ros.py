import logging
import re
import shlex
import subprocess
import time
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)


class RosCommandSkill(BaseSkill):
    """허용된 읽기 전용 ROS2 CLI 명령 실행 스킬"""

    name = "ros_command"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}, "timeout_sec": {"type": "number"}},
        "required": ["command"],
        "additionalProperties": False,
    }
    risk_level = "write"
    allow_with_others = False
    description = "허용된 읽기 전용 ROS2 CLI 명령의 결과를 반환합니다. 시스템 셸과 제어 명령은 실행하지 않습니다."

    def validate_params(self, params: dict[str, Any]) -> bool:
        command = str(params.get("command", "")).strip()
        if not command:
            logger.warning("Invalid command: empty command string.")
            return False

        try:
            argv = shlex.split(command)
        except ValueError:
            return False
        if not argv or argv[0] != "ros2":
            return False
        if len(argv) < 3 or argv[1] not in {"topic", "node", "service", "action", "param"}:
            return False
        return argv[2] in {"list", "info", "type", "find", "get"}

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        command = str(params.get("command", "")).strip()
        timeout_sec = float(params.get("timeout_sec", 10.0))

        if not self.validate_params({"command": command}):
            return {
                "success": False,
                "message": "허용되지 않은 ROS2 읽기 명령입니다.",
                "command": command,
            }

        logger.info("Executing ROS command: %s", command)

        try:
            result = subprocess.run(
                shlex.split(command),
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )

            stdout_str = result.stdout.strip()[:2000]
            stderr_str = result.stderr.strip()[:2000]

            success = result.returncode == 0
            return {
                "success": success,
                "message": "실행 완료"
                if success
                else f"실행 실패 (exit code: {result.returncode})",
                "command": command,
                "stdout": stdout_str,
                "stderr": stderr_str,
                "returncode": result.returncode,
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "message": f"실행 시간 초과 ({timeout_sec}초)",
                "command": command,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"실행 중 오류: {e}",
                "command": command,
            }


# 메시지에 미리 보여줄 토픽 수. 전체 목록은 result_data 로 전달한다.
_TOPIC_PREVIEW_LIMIT = 12
# 빈 그래프일 때 timeout_sec 까지 재탐색하는 간격(초).
_TOPIC_POLL_INTERVAL_SEC = 0.2


def _is_hidden_topic(topic_name: str) -> bool:
    """ROS 2 숨김 토픽(토픽명의 마지막 네임스페이스가 `_`로 시작)을 판별한다."""
    return any(segment.startswith("_") for segment in str(topic_name).split("/") if segment)


def _is_camera_topic(topic_name: str) -> bool:
    """카메라 네임스페이스(camera, cam, rgb_cam, depth_cam)를 가진 토픽인지 판별한다."""
    return (
        re.search(
            r"(?:^|[/_])(?:rgb_|depth_)?cam(?:era)?(?:[/_]|$)", str(topic_name).lower()
        )
        is not None
    )


class ListTopicsSkill(BaseSkill):
    """ROS 그래프에서 수신 가능한 토픽 목록과 메시지 타입을 조회하는 읽기 전용 스킬."""

    name = "list_topics"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "timeout_sec": {"type": "number", "default": 3.0},
            "include_hidden": {"type": "boolean", "default": False},
            "category": {"type": "string", "enum": ["all", "camera"], "default": "all"},
        },
        "additionalProperties": False,
    }
    risk_level = "read"
    description = (
        "ROS 그래프에서 현재 발행/구독 가능한 토픽 목록과 메시지 타입을 조회합니다. "
        "'토픽 목록 보여줘', '수신 가능한 토픽', '어떤 토픽이 있어?' 같은 요청에 사용하세요. "
        "그래프 탐색이 비어 있으면 timeout_sec 동안 재시도하며, 기본값으로 숨김 토픽"
        "(`_`로 시작하는 세그먼트)은 제외합니다. 카메라 토픽만 필요하면 category='camera'를 지정합니다."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        error = self.require_node()
        if error is not None:
            return error

        timeout_sec = max(0.0, self.get_float_param(params, "timeout_sec", 3.0))
        include_hidden = self.get_bool_param(params, "include_hidden", False)
        category = str(params.get("category", "all") or "all").strip().lower()

        # require_node() 가 None 여부를 이미 확인했다. 지역 변수로 받아 그래프 API를
        # 호출하는 헬퍼에 넘긴다(타입 검사기 좁히기 목적).
        topics = self._collect_topics(self.node, timeout_sec)
        if topics is None:
            return self.fail_result(
                "ROS 그래프에서 토픽 목록을 조회하지 못했습니다.",
                failure_reason="graph_unavailable",
            )

        if not include_hidden:
            topics = [(name, type_name) for name, type_name in topics if not _is_hidden_topic(name)]
        if category == "camera":
            topics = [(name, type_name) for name, type_name in topics if _is_camera_topic(name)]

        names = [name for name, _ in topics]
        if not names:
            return self.success_result(
                "현재 ROS 그래프에 수신 가능한 토픽이 없습니다.",
                topics=[],
                topic_count=0,
                topic_names=[],
            )

        preview = ", ".join(names[:_TOPIC_PREVIEW_LIMIT])
        if len(names) > _TOPIC_PREVIEW_LIMIT:
            preview += f" 외 {len(names) - _TOPIC_PREVIEW_LIMIT}개"
        return self.success_result(
            f"수신 가능한 토픽 {len(names)}개: {preview}",
            topics=[{"name": name, "type": type_name} for name, type_name in topics],
            topic_count=len(names),
            topic_names=names,
        )

    def _collect_topics(self, node: Any, timeout_sec: float) -> list[tuple[str, str]] | None:
        """노드 API로 (토픽명, 메시지 타입) 목록을 수집한다.

        그래프 탐색은 노드 디스커버리 직후나 DDS 지연으로 잠시 비어 있을 수 있으므로,
        결과가 비어 있으면 ``timeout_sec`` 까지 간격을 두고 재탐색한다. 탐색 자체가
        예외로 실패하면 조용히 빈 목록을 반환하지 않고 ``None`` 을 돌려 실패를 드러낸다.
        """
        deadline = time.monotonic() + timeout_sec
        while True:
            try:
                pairs = node.get_topic_names_and_types()
            except Exception as exc:  # noqa: BLE001 - 그래프 API 실패는 스킬 실패로 보고
                logger.warning("Topic discovery failed: %s", exc)
                return None

            topics: list[tuple[str, str]] = []
            for name, types in pairs:
                if isinstance(types, (list, tuple)) and types:
                    type_name = str(types[0])
                else:
                    type_name = ""
                topics.append((str(name), type_name))

            if topics or time.monotonic() >= deadline:
                return topics
            time.sleep(_TOPIC_POLL_INTERVAL_SEC)
