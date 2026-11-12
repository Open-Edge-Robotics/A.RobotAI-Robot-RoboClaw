"""대시보드 유스케이스 — 상태 취합과 에이전트 메시지 전송.

rclpy / ROS 메시지 import 금지. backend protocol 을 통해 실제 ROS 어댑터와 분리되어,
테스트에서는 fake backend 로 검증한다.
"""

import logging
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol

from ..domain.builder import build_snapshot
from ..domain.config_view import build_config_panel
from ..domain.cooperation import (
    cooperation_payload,
    format_cooperation_jsonl,
    normalize_cooperation,
)
from ..domain.localization import (
    INITIAL_POSE_COVARIANCE,
    build_initial_pose,
    ensure_geometry_id,
    unavailable_map,
    unavailable_pose,
    validate_initial_pose,
)
from ..domain.logs import format_log_jsonl, format_log_text, unavailable_logs
from ..domain.map_goal import agent_identity_from_state_json
from ..domain.peers import normalize_peers
from ..domain.validation import validate_chat_message
from .map_goal import MapGoalService
from .scenario_runner import ScenarioConfig, ScenarioRunnerService

logger = logging.getLogger(__name__)

#: 지도 목표 제출 시 재사용하는 에이전트 신원 캐시 유효 시간(초).
AGENT_IDENTITY_TTL_SEC = 15.0


class DashboardBackend(Protocol):
    """ROS 어댑터가 제공해야 하는 인터페이스.

    각 메서드는 안전한 기본값(fake/미수신 시 빈 값)을 반환해야 하며 예외를 유발하지 않는다.
    """

    def query_state(self) -> str | None: ...

    def latest_agent_status(self) -> dict[str, Any]: ...

    def latest_telemetry(self) -> dict[str, Any]: ...

    def latest_sensor_health(self) -> dict[str, Any]: ...

    def list_peers(self) -> list[dict[str, Any]]: ...

    def send_chat(
        self,
        message: str,
        *,
        context_json: str | None = None,
        timeout_sec: float | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]: ...

    def latest_map(self) -> dict[str, Any]: ...

    def latest_pose(self) -> dict[str, Any]: ...

    def set_initial_pose(self, pose: dict[str, Any]) -> dict[str, Any]: ...

    def describe_config(self) -> dict[str, Any] | None: ...

    def logs(
        self,
        *,
        min_level: str | None = None,
        query: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]: ...


class DashboardService:
    """대시보드 조회/전송 유스케이스를 구현한다."""

    def __init__(
        self,
        backend: DashboardBackend,
        *,
        scenario_runner: ScenarioRunnerService | None = None,
        scenario_config: ScenarioConfig | None = None,
        monotonic: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
        map_goals: MapGoalService | None = None,
    ) -> None:
        self._backend = backend
        self._scenario_runner = scenario_runner or ScenarioRunnerService(backend, scenario_config)
        self._monotonic = monotonic or time.monotonic
        self._cooperation_cache: dict[str, Any] | None = None
        #: (monotonic, 신원/진행 기록) — 15초가 지난 에이전트 신원은 재사용하지 않는다.
        self._agent_identity_cache: tuple[float, dict[str, Any]] | None = None
        self._map_goals = map_goals or MapGoalService(
            backend,
            map_snapshot=self._latest_map,
            agent_identity=self._agent_identity,
            wall_clock=wall_clock,
        )
        self._cooperation_cached_at = 0.0
        #: ThreadingHTTPServer 는 요청마다 스레드로 status/cooperation 을 동시에 부른다.
        #: (payload, cached_at) 을 한 쌍으로 읽고 써서 서로 다른 스냅샷이 섞이지 않게 한다.
        self._cooperation_lock = threading.Lock()

    def get_scenario(self, *, refresh: bool = False) -> dict[str, Any]:
        """현재 로드된 시나리오 또는 새로 고침된 시나리오를 반환한다."""
        return self._scenario_runner.get_scenario(refresh=refresh)

    def sync_scenario(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """대시보드에서 입력한 AI Config 서버 설정으로 시나리오를 동기화한다."""
        return self._scenario_runner.sync_scenario(payload)

    def start_scenario_run(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """시나리오 테스트 실행을 요청한다."""
        return self._scenario_runner.start_run(payload)

    def cancel_scenario_run(self) -> dict[str, Any]:
        """실행 중인 시나리오 테스트를 중단한다."""
        return self._scenario_runner.cancel_run()

    def scenario_status(self) -> dict[str, Any]:
        """현재 시나리오 실행 진행률 및 스텝별 결과를 반환한다."""
        return self._scenario_runner.status()

    def scenario_report(self, fmt: str = "md") -> dict[str, Any]:
        """완료된 시나리오 보고서를 조회한다."""
        return self._scenario_runner.get_report(fmt=fmt)

    def status(self, *, now: datetime | None = None) -> dict[str, Any]:
        snapshot = build_snapshot(
            state_json=self._backend.query_state(),
            agent_status=self._backend.latest_agent_status(),
            telemetry=self._backend.latest_telemetry(),
            sensor_health=self._backend.latest_sensor_health(),
            peers=normalize_peers(self._backend.list_peers()),
            robot_pose=self._latest_pose(),
            map_snapshot=self._latest_map(),
            now=now,
        )
        # 협동 관측은 이 응답의 state.cooperation 을 한 번만 캐시한다(추가 ROS 질의 없음).
        # raw 값을 그대로 내보내면 방어 정규화를 우회하므로 정규화된 사본으로 교체한다.
        state = snapshot.get("state")
        if not isinstance(state, dict):
            state = {}
            snapshot["state"] = state
        state["cooperation"] = self._store_cooperation(state)
        # 지도 선택 목표 진행 상태는 같은 /api/status 폴링 응답에 싣는다(추가 타이머 없음).
        snapshot["navigation_request"] = self._map_goals.status_record(state)
        return snapshot

    def cooperation_snapshot(
        self,
        *,
        after: int | None = None,
        stream_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """마지막 /api/status 응답에서 캐시한 협동 관측을 커서 조건으로 돌려준다."""
        payload, cached_at = self._read_cooperation()
        age_ms = None
        if payload is not None:
            age_ms = max(0, int(round((self._monotonic() - cached_at) * 1000)))
        return cooperation_payload(
            payload, after=after, stream_id=stream_id, limit=limit, age_ms=age_ms
        )

    def cooperation_export(self) -> tuple[str, str]:
        """캐시된 최근 협동 관측을 JSON Lines 파일 본문으로 직렬화한다."""
        payload, _ = self._read_cooperation()
        return "application/x-ndjson; charset=utf-8", format_cooperation_jsonl(payload)

    def config_snapshot(self) -> dict[str, Any]:
        """실행 중인 노드 파라미터를 마스킹해 반환한다(조회 실패는 정상 상태로 처리)."""
        describe = getattr(self._backend, "describe_config", None)
        sections: dict[str, Any] | None = None
        if describe is not None:
            try:
                sections = describe()
            except Exception:  # noqa: BLE001 - 파라미터 서비스 없음은 정상 상태
                logger.debug("describe_config failed", exc_info=True)
                sections = None
        if not isinstance(sections, dict):
            sections = {}
        return build_config_panel(
            agent_node=str(sections.get("agent_node") or "robo_claw_agent_node"),
            agent_parameters=sections.get("agent_parameters"),
            agent_defaults=sections.get("agent_defaults"),
            dashboard_node=str(sections.get("dashboard_node") or "robo_claw_dashboard_node"),
            dashboard_parameters=sections.get("dashboard_parameters"),
            dashboard_defaults=sections.get("dashboard_defaults"),
        )

    def map_snapshot(self) -> dict[str, Any]:
        """지도 셀 데이터를 포함한 스냅샷을 반환한다(미수신 시 available=false)."""
        return self._latest_map()

    def submit_map_goal(self, payload: Any) -> dict[str, Any]:
        """지도 선택 목표를 검증/접수한다. HTTP 코드가 담긴 dict 를 돌려준다."""
        return self._map_goals.submit(payload)

    def shutdown(self) -> None:
        """진행 중인 지도 목표 워커를 정리한다."""
        self._map_goals.shutdown()

    def logs_snapshot(
        self,
        *,
        level: str | None = None,
        query: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """최근 로그 스냅샷을 반환한다(버퍼 없음/조회 실패는 정상 상태로 처리)."""
        fetch = getattr(self._backend, "logs", None)
        if fetch is None:
            return unavailable_logs("로그 버퍼가 구성되지 않았습니다")
        try:
            payload = fetch(min_level=level, query=query, limit=limit)
        except Exception:  # noqa: BLE001 - 로그 조회 실패로 대시보드를 멈추지 않는다
            logger.debug("logs failed", exc_info=True)
            return unavailable_logs("로그 조회 실패")
        if not isinstance(payload, dict):
            return unavailable_logs("로그 응답 형식 오류")
        return payload

    def logs_export(
        self,
        *,
        level: str | None = None,
        query: str | None = None,
        limit: int | None = None,
        fmt: str = "txt",
    ) -> tuple[str, str]:
        """로그를 파일로 저장할 수 있는 텍스트/JSONL 과 content-type 으로 직렬화한다."""
        payload = self.logs_snapshot(level=level, query=query, limit=limit)
        entries = payload.get("entries") or []
        if str(fmt or "").strip().lower() == "jsonl":
            return "application/x-ndjson; charset=utf-8", format_log_jsonl(entries)
        header = (
            f"RoboClaw dashboard log export — {payload.get('timestamp')} "
            f"stored={payload.get('stored')} returned={payload.get('returned')} "
            f"level={payload.get('level') or 'all'} q={payload.get('query') or ''}"
        )
        return "text/plain; charset=utf-8", format_log_text(entries, header=header)

    def set_initial_pose(self, payload: Any) -> dict[str, Any]:
        """AMCL 초기 위치를 설정한다. 검증 실패는 발행 전에 거부한다."""
        # 대시보드 재시작 후에도 에이전트 측 진행/미확인 기록을 lease 로 채택하고,
        # matching terminal 이 관측될 때만 해제한다(사라진 캐시로 자동 해제 금지).
        identity = self._agent_identity()
        self._map_goals.observe_agent_record(
            identity.get("map_navigation_request") if isinstance(identity, Mapping) else None
        )
        if self._map_goals.holds_lease():
            # 이동 중 재위치추정은 실제 주행과 어긋난 좌표를 만들 수 있어 막는다.
            return {
                "success": False,
                "error": (
                    "지도 목표 이동이 진행 중이거나 결과를 확인하지 못했습니다. "
                    "이동이 끝난 뒤 초기 위치를 변경하세요."
                ),
            }
        map_snapshot = self._latest_map()
        ok, error, pose = validate_initial_pose(payload, map_meta=map_snapshot)
        if not ok:
            return {"success": False, "error": error}
        if not map_snapshot.get("available", False):
            # 지도 없이 좌표를 보내면 파티클이 엉뚱한 곳에 퍼진다. 지도 수신을 전제로 한다.
            return {
                "success": False,
                "error": "지도를 아직 수신하지 못해 초기 위치를 검증할 수 없습니다",
            }

        assert pose is not None  # validate_initial_pose 성공 시 항상 dict
        message = build_initial_pose(
            x=pose["x"],
            y=pose["y"],
            yaw=pose["yaw"],
            covariance=INITIAL_POSE_COVARIANCE,
            frame_id=pose["frame_id"],
        )
        try:
            result = self._backend.set_initial_pose(message)
        except Exception as exc:  # noqa: BLE001 - HTTP 경계에서 500 을 막기 위해 광범위 포착
            logger.exception("set_initial_pose failed: %s", exc)
            return {"success": False, "error": f"초기 위치 발행 실패: {exc}"}

        success = bool(result.get("success"))
        payload_out: dict[str, Any] = {
            "success": success,
            "frame_id": message["frame_id"],
            "x": message["x"],
            "y": message["y"],
            "yaw": message["yaw"],
            "yaw_deg": round(message["yaw"] * 180.0 / 3.141592653589793, 1),
        }
        if not success:
            payload_out["error"] = str(result.get("error") or "초기 위치 발행 실패")
        return payload_out

    # ---- 내부 헬퍼 ---------------------------------------------------------
    def _store_cooperation(self, state: Any) -> dict[str, Any]:
        raw = state.get("cooperation") if isinstance(state, dict) else None
        journal = normalize_cooperation(raw)
        with self._cooperation_lock:
            self._cooperation_cache = journal
            self._cooperation_cached_at = self._monotonic()
        return journal

    def _read_cooperation(self) -> tuple[dict[str, Any] | None, float]:
        with self._cooperation_lock:
            return self._cooperation_cache, self._cooperation_cached_at

    def _latest_map(self) -> dict[str, Any]:
        try:
            snapshot = self._backend.latest_map()
        except Exception:  # noqa: BLE001 - 지도 미수신은 정상 상태
            logger.debug("latest_map failed", exc_info=True)
            return unavailable_map()
        if not isinstance(snapshot, dict):
            return unavailable_map()
        return ensure_geometry_id(snapshot)

    def _agent_identity(self) -> dict[str, Any] | None:
        """제출 시점의 에이전트 로봇 ID/주행 프레임을 15초 이내 값으로만 사용한다."""
        now = self._monotonic()
        cached = self._agent_identity_cache
        if cached is not None and now - cached[0] <= AGENT_IDENTITY_TTL_SEC:
            return dict(cached[1])
        try:
            parsed = agent_identity_from_state_json(self._backend.query_state())
        except Exception:  # noqa: BLE001 - QueryState 실패는 미확인 상태
            logger.debug("agent identity query failed", exc_info=True)
            parsed = None
        if parsed is None:
            return None
        self._agent_identity_cache = (now, parsed)
        return dict(parsed)

    def _latest_pose(self) -> dict[str, Any]:
        try:
            pose = self._backend.latest_pose()
        except Exception:  # noqa: BLE001 - TF 미수신은 정상 상태
            logger.debug("latest_pose failed", exc_info=True)
            return unavailable_pose()
        return pose if isinstance(pose, dict) else unavailable_pose()

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
