"""지도 전환(위치추정 시작) 비동기 실행 서비스.

CLOiD LIO 의 `/switch_mode` 서비스는 3D 맵 로드까지 수 초~수 분이 걸릴 수 있으므로
HTTP 스레드는 202 만 돌려주고, 실제 호출은 단일 워커 스레드가 수행한다. 동시에 한
건만 허용하고, 결과는 `/api/localization` 폴링으로 노출한다.

rclpy / ROS 메시지 import 금지(backend protocol 로만 통신).
"""

import logging
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from ..domain.localization_mode import (
    MODE_LOCALIZATION,
    normalize_lio_status,
    normalize_map_listing,
    unavailable_lio_status,
    validate_switch_request,
)

logger = logging.getLogger(__name__)

#: 워커 종료 대기 상한(초).
JOIN_TIMEOUT_SEC = 5.0
#: 진행 중(중복 요청 차단)으로 보는 상태.
BUSY_STATUSES = frozenset({"queued", "running"})


class LocalizationSwitchService:
    """지도 선택/모드 전환 요청을 검증하고 단일 워커로 실행한다."""

    def __init__(
        self,
        backend: Any,
        *,
        map_names: Sequence[str] = (),
        map_root: str = "/ws/maps",
        timeout_sec: float = 200.0,
        wall_clock: Callable[[], float] | None = None,
        holds_lease: Callable[[], bool] | None = None,
    ) -> None:
        self._backend = backend
        self._map_names = [str(name) for name in map_names or []]
        self._map_root = str(map_root or "/ws/maps")
        self._timeout = max(1.0, float(timeout_sec))
        self._wall_clock = wall_clock or time.time
        self._holds_lease = holds_lease

        self._lock = threading.RLock()
        self._job: dict[str, Any] | None = None
        self._plan: dict[str, Any] | None = None
        self._worker: threading.Thread | None = None
        self._idle = threading.Event()
        self._idle.set()
        self._shutdown = False
        self._counter = 0

    # ---- 공개 API ---------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """지도 목록·LIO 상태·제어 가용성·마지막 전환 기록을 한 번에 돌려준다."""
        with self._lock:
            job = self._copy_job_locked()
            busy = self._busy_locked()
        status = self._lio_status()
        control = self._control()
        maps = normalize_map_listing(self._map_names, self._map_root)
        return {
            "available": bool(status.get("available")),
            "status": status,
            "maps": maps,
            "map_root": self._map_root,
            "maps_available": bool(maps),
            "current_map": self._current_map(job),
            "control": {
                "available": bool(control.get("available")),
                "reason": str(control.get("reason") or ""),
                "busy": busy,
            },
            "last_switch": job,
        }

    def submit(self, payload: Any) -> dict[str, Any]:
        """지도 전환 요청을 접수한다. HTTP 상태 코드가 담긴 dict 를 돌려준다."""
        ok, error, plan = validate_switch_request(
            payload, maps=self._map_names, map_root=self._map_root
        )
        if not ok or plan is None:
            return {"success": False, "error": error, "status_code": 400}

        control = self._control()
        if not bool(control.get("available")):
            return {
                "success": False,
                "error": str(control.get("reason") or "지도 전환 제어를 사용할 수 없습니다"),
                "status_code": 503,
            }
        if self._holds_lease is not None:
            try:
                moving = bool(self._holds_lease())
            except Exception:  # noqa: BLE001 - lease 판정 실패는 안전 측(이동 중)으로 본다
                logger.debug("holds_lease failed", exc_info=True)
                moving = True
            if moving:
                return {
                    "success": False,
                    "error": (
                        "지도 목표 이동이 진행 중이거나 결과를 확인하지 못했습니다. "
                        "이동이 끝난 뒤 지도를 바꾸세요."
                    ),
                    "status_code": 409,
                }

        with self._lock:
            if self._shutdown:
                return {"success": False, "error": "대시보드가 종료 중입니다", "status_code": 503}
            if self._busy_locked():
                return {
                    "success": False,
                    "error": "이미 지도 전환이 진행 중입니다. 끝난 뒤 다시 시도하세요.",
                    "status_code": 409,
                }
            self._counter += 1
            now = self._wall_clock()
            record = {
                "request_id": f"loc-{self._counter}",
                "mode": plan["mode"],
                "map": plan.get("map"),
                "map_path": plan["map_path"],
                "set_initial_pose": bool(plan["set_initial_pose"]),
                "pose": dict(plan["pose"]) if plan.get("pose") else None,
                "status": "queued",
                "message": "접수됨 — 진행 상황은 LIO 상태로 확인합니다",
                "created_at": now,
                "updated_at": now,
            }
            self._job = record
            self._plan = plan
            self._idle.clear()
            self._ensure_worker_locked()
            return {"success": True, "request": self._copy_job_locked(), "status_code": 202}

    def wait_idle(self, timeout_sec: float = JOIN_TIMEOUT_SEC) -> bool:
        """진행 중인 전환이 끝날 때까지 기다린다(테스트/종료 경로용)."""
        return self._idle.wait(timeout=timeout_sec)

    def shutdown(self) -> None:
        """새 접수를 막고 실행 중인 워커를 정리한다(서비스 호출 자체는 취소하지 않는다)."""
        with self._lock:
            self._shutdown = True
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=JOIN_TIMEOUT_SEC)

    # ---- 내부 헬퍼 ---------------------------------------------------------
    def _busy_locked(self) -> bool:
        job = self._job
        return bool(job and job.get("status") in BUSY_STATUSES)

    def _ensure_worker_locked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._run, name="dashboard-localization-switch", daemon=True
        )
        self._worker.start()

    def _run(self) -> None:
        with self._lock:
            plan = self._plan
            if plan is None:
                self._idle.set()
                return
            self._update_locked(status="running", message="요청을 전송했습니다")

        result: dict[str, Any]
        call = getattr(self._backend, "switch_localization_map", None)
        if call is None:
            result = {"success": False, "message": "지도 전환 백엔드가 구성되지 않았습니다"}
        else:
            try:
                result = call(plan) or {}
            except Exception as exc:  # noqa: BLE001 - 워커 스레드는 예외로 죽으면 안 된다
                logger.exception("switch_localization_map failed: %s", exc)
                result = {"success": False, "message": f"지도 전환 호출 실패: {exc}"}

        success = bool(result.get("success"))
        message = str(result.get("message") or result.get("error") or "").strip()
        with self._lock:
            self._plan = None
            self._update_locked(
                status="succeeded" if success else "failed",
                message=message or ("맵을 적용했습니다" if success else "지도 전환에 실패했습니다"),
            )
            self._worker = None
            self._idle.set()

    def _update_locked(self, **fields: Any) -> None:
        if self._job is None:
            return
        self._job.update(fields)
        self._job["updated_at"] = self._wall_clock()

    def _copy_job_locked(self) -> dict[str, Any] | None:
        if self._job is None:
            return None
        job = dict(self._job)
        job["pose"] = dict(job["pose"]) if isinstance(job.get("pose"), dict) else None
        return job

    def _current_map(self, job: dict[str, Any] | None) -> str | None:
        if not job:
            return None
        if job.get("mode") != MODE_LOCALIZATION:
            return None
        if job.get("status") in ("queued", "running", "succeeded"):
            return job.get("map") or None
        return None

    def _lio_status(self) -> dict[str, Any]:
        fetch = getattr(self._backend, "latest_lio_status", None)
        if fetch is None:
            return unavailable_lio_status("LIO 상태 소스가 구성되지 않았습니다")
        try:
            status = fetch()
        except Exception:  # noqa: BLE001 - 상태 조회 실패로 대시보드를 멈추지 않는다
            logger.debug("latest_lio_status failed", exc_info=True)
            return unavailable_lio_status("상태 조회 실패")
        if isinstance(status, dict):
            return status
        if isinstance(status, str):
            return normalize_lio_status(status)
        return unavailable_lio_status("상태 미수신")

    def _control(self) -> dict[str, Any]:
        fetch = getattr(self._backend, "localization_control_available", None)
        if fetch is None:
            return {"available": False, "reason": "지도 전환 제어가 구성되지 않았습니다"}
        try:
            control = fetch()
        except Exception:  # noqa: BLE001 - 제어 가용성 조회 실패는 '불가' 로 처리
            logger.debug("localization_control_available failed", exc_info=True)
            return {"available": False, "reason": "제어 가용성 조회 실패"}
        if not isinstance(control, dict):
            return {"available": False, "reason": "제어 가용성 응답 형식 오류"}
        return {
            "available": bool(control.get("available")),
            "reason": str(control.get("reason") or ""),
        }
