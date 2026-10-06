"""대시보드 유스케이스 — 상태 취합과 에이전트 메시지 전송.

rclpy / ROS 메시지 import 금지. backend protocol 을 통해 실제 ROS 어댑터와 분리되어,
테스트에서는 fake backend 로 검증한다.
"""

import logging
from datetime import datetime
from typing import Any, Protocol

from ..domain.builder import build_snapshot
from ..domain.validation import validate_chat_message

logger = logging.getLogger(__name__)


class DashboardBackend(Protocol):
    """ROS 어댑터가 제공해야 하는 인터페이스.

    각 메서드는 안전한 기본값(fake/미수신 시 빈 값)을 반환해야 하며 예외를 유발하지 않는다.
    """

    def query_state(self) -> str | None: ...

    def latest_agent_status(self) -> dict[str, Any]: ...

    def latest_telemetry(self) -> dict[str, Any]: ...

    def latest_sensor_health(self) -> dict[str, Any]: ...

    def list_peers(self) -> list[dict[str, Any]]: ...

    def send_chat(self, message: str) -> dict[str, Any]: ...


class DashboardService:
    """대시보드 조회/전송 유스케이스를 구현한다."""

    def __init__(self, backend: DashboardBackend) -> None:
        self._backend = backend

    def status(self, *, now: datetime | None = None) -> dict[str, Any]:
        return build_snapshot(
            state_json=self._backend.query_state(),
            agent_status=self._backend.latest_agent_status(),
            telemetry=self._backend.latest_telemetry(),
            sensor_health=self._backend.latest_sensor_health(),
            peers=self._backend.list_peers(),
            now=now,
        )

    def send_message(self, message: str | None) -> dict[str, Any]:
        """에이전트에 채팅 메시지를 전달한다. 검증/예외를 결과 dict 로 정규화한다."""
        ok, err = validate_chat_message(message)
        if not ok:
            return {"success": False, "error": err}

        # validate_chat_message 가 ok 인 경우 message 는 반드시 str (myPy narrowing 용)
        assert isinstance(message, str)

        try:
            result = self._backend.send_chat(message)
        except Exception as exc:  # noqa: BLE001 - HTTP 경계에서 500 을 막기 위해 광범위 포착
            logger.exception("send_chat failed: %s", exc)
            return {"success": False, "error": f"명령 처리 실패: {exc}"}

        success = bool(result.get("success"))
        payload: dict[str, Any] = {
            "success": success,
            "message": str(result.get("message") or result.get("error") or "완료"),
            "skill_results": list(result.get("skill_results") or []),
        }
        if not success:
            payload["error"] = str(result.get("error") or result.get("message") or "명령 처리 실패")
        return payload
