"""지도 목표 워커/상태 전이 — MapGoalService 가 상속하는 실행·lease 반영 로직.

단일 워커 스레드가 ExecuteTask 위임을 순차 수행하고, 결과/이벤트/에이전트 기록을
보수적으로 반영한다. rclpy / ROS 메시지 import 금지.
"""

import logging
import queue
import threading
from collections.abc import Mapping
from typing import Any

from ..domain.map_goal import (
    LEASE_STATUSES,
    REASON_NAVIGATION_NOT_EXECUTED,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PLANNING,
    STATUS_REACHED,
    STATUS_RUNNING,
    STATUS_UNKNOWN,
    TERMINAL_STATUSES,
    adopt_external_record,
    can_submit,
    context_json_for,
    evidence_from_skill_results,
    records_compatible,
    validate_dispatch_target,
)

logger = logging.getLogger(__name__)

#: 지도 목표 모드 ExecuteTask 결과 대기 시간(초).
MAP_GOAL_TASK_TIMEOUT_SEC = 180.0
#: 워커 종료 시 취소/정리 대기 상한(초).
WORKER_JOIN_TIMEOUT_SEC = 5.0


class MapGoalWorkerMixin:
    """MapGoalService 의 _records/_jobs/_active_id/_queue/_lock 등을 사용하는 워커."""

    # ---- 워커 -------------------------------------------------------------
    def _ensure_worker_locked(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(
                target=self._worker_loop, name="dashboard-map-goal", daemon=True
            )
            self._worker.start()

    def _worker_loop(self) -> None:
        while True:
            try:
                job_id = self._queue.get(timeout=0.2)
            except queue.Empty:
                with self._lock:
                    if self._queue.empty():
                        self._worker = None
                        self._idle.set()
                        return
                continue
            if job_id is None:
                return
            try:
                self._run(job_id)
            except Exception:  # noqa: BLE001 - 워커 예외로 대시보드를 멈추지 않는다
                logger.exception("map goal worker failed")
                self._finish(job_id, STATUS_FAILED, reason="internal_error")
            finally:
                self._queue.task_done()
                with self._lock:
                    if self._queue.empty():
                        self._idle.set()

    def _run(self, job_id: str) -> None:
        with self._lock:
            record = self._records.get(job_id)
            job = self._jobs.get(job_id)
            shutting_down = self._shutdown
        if record is None or job is None:
            return
        if shutting_down:
            self._finish(job_id, STATUS_FAILED, reason="shutdown")
            return

        now = self._wall_clock()
        if now > float(job["expires_at"]):
            self._finish(
                job_id,
                STATUS_FAILED,
                reason="selection_expired",
                message="선택 유효 시간(60초)이 지나 목표를 보내지 않았습니다",
            )
            return

        snapshot = self._map_snapshot()
        revalidated, reason, message = validate_dispatch_target(job["goal"], snapshot)
        if not revalidated:
            self._finish(job_id, STATUS_FAILED, reason=reason, message=message)
            return

        context_json = context_json_for(
            job["goal"],
            map_topic=str(snapshot.get("topic") or "") or job["map_topic"],
            created_at=float(record["created_at"]),
        )
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_event = cancel_event
        self._update_record(
            job_id, status=STATUS_PLANNING, message="에이전트에 목표를 전달했습니다"
        )

        def on_event(event: Any) -> None:
            self._on_backend_event(job_id, event)

        try:
            result = self._backend.send_chat(
                job["goal"]["message"],
                context_json=context_json,
                timeout_sec=MAP_GOAL_TASK_TIMEOUT_SEC,
                on_event=on_event,
                cancel_event=cancel_event,
            )
        except Exception as exc:  # noqa: BLE001 - 위임 실패를 기록으로 정규화
            logger.exception("map goal dispatch failed: %s", exc)
            result = {"success": False, "error": f"명령 처리 실패: {exc}"}
        finally:
            with self._lock:
                self._cancel_event = None
        self._apply_result(job_id, result if isinstance(result, Mapping) else {})

    # ---- 상태/결과 반영 ---------------------------------------------------
    def _on_backend_event(self, job_id: str, event: Any) -> None:
        if not isinstance(event, Mapping):
            return
        kind = str(event.get("type") or "")
        if kind == "accepted":
            # goal 수락은 planning 단계일 뿐 실제 guarded 주행 효과의 증거가 아니다.
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job["accepted"] = True
            self._update_record(
                job_id,
                status=STATUS_PLANNING,
                message="에이전트가 목표를 수락했습니다",
            )
        elif kind == "feedback":
            progress = event.get("progress")
            progress_value = None
            if isinstance(progress, (int, float)) and not isinstance(progress, bool):
                progress_value = max(0, min(100, int(progress)))
            current_step = str(event.get("current_step") or "")
            # System1/planning 진행 문구만으로 running/execution_started 를 주장하지 않는다.
            self._update_record(
                job_id,
                progress=progress_value,
                message=current_step or None,
            )

    def _apply_result(self, job_id: str, result: Mapping[str, Any]) -> None:
        with self._lock:
            record = self._records.get(job_id)
            job = self._jobs.get(job_id)
            if record is None or not self._record_matches_job_locked(job_id):
                return
            accepted = bool((job or {}).get("accepted"))

        if result.get("unknown"):
            # cancel_goal_async ACK 는 정지 확인이 아니다. 증거 없이는 unknown + lease 유지.
            self._finish(
                job_id,
                STATUS_UNKNOWN,
                reason="navigation_result_unconfirmed",
                message=str(result.get("message") or result.get("error") or "이동 결과 미확인"),
                cancel_confirmed=False,
                keep_lease=True,
            )
            return

        evidence = evidence_from_skill_results(
            result.get("skill_results"),
            request_id=str(record["request_id"]),
            target=record.get("target"),
        )
        if evidence is not None and records_compatible(record, evidence):
            status = str(evidence.get("status") or STATUS_UNKNOWN)
            if status in TERMINAL_STATUSES:
                self._finish(
                    job_id,
                    status,
                    reason=str(evidence.get("reason") or ""),
                    message=str(evidence.get("message") or ""),
                    # 실제 nav 결과 증거가 cancelled 일 때만 정지 확인으로 인정한다.
                    cancel_confirmed=bool(evidence.get("cancel_confirmed"))
                    or status == STATUS_CANCELLED,
                    progress=100 if status == STATUS_REACHED else None,
                    # 실제 nav 결과 증거는 guarded 주행 효과의 증거다.
                    execution_started=evidence.get("execution_started") is True,
                )
                return
            self._finish(
                job_id,
                STATUS_UNKNOWN,
                reason="navigation_result_unconfirmed",
                message=str(evidence.get("message") or "이동 결과 미확인"),
                keep_lease=True,
            )
            return

        if bool(result.get("success")):
            # task 성공 bool 은 이동/도달 증거가 아니다. old/degraded agent 가 실제로
            # 움직였을 수 있으므로 failed 로 lease 를 풀지 않고 unknown 으로 유지한다.
            self._finish(
                job_id,
                STATUS_UNKNOWN,
                reason="navigation_result_unconfirmed",
                message="이동 결과 근거를 확인하지 못했습니다",
                keep_lease=True,
            )
            return

        not_executed = str(
            result.get("reason") or result.get("failure_reason") or ""
        ) == REASON_NAVIGATION_NOT_EXECUTED and not bool(result.get("execution_started"))
        if not_executed:
            self._finish(
                job_id,
                STATUS_FAILED,
                reason=REASON_NAVIGATION_NOT_EXECUTED,
                message=str(result.get("error") or result.get("message") or "실행되지 않음"),
            )
            return
        if not accepted:
            # goal 수락 전 실패는 실제 주행이 시작될 수 없으므로 안전하게 종료한다.
            self._finish(
                job_id,
                STATUS_FAILED,
                reason="dispatch_failed",
                message=str(result.get("error") or result.get("message") or "명령 처리 실패"),
            )
            return
        self._finish(
            job_id,
            STATUS_UNKNOWN,
            reason="navigation_result_unconfirmed",
            message=str(result.get("error") or result.get("message") or "이동 결과 미확인"),
            keep_lease=True,
        )

    def _update_record(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record is None or not self._record_matches_job_locked(job_id):
                return
            if record["status"] in TERMINAL_STATUSES:
                return
            record.update({key: value for key, value in fields.items() if value is not None})
            record["updated_at"] = self._wall_clock()
            if record["status"] in (STATUS_PLANNING, STATUS_RUNNING):
                record["can_submit"] = False

    def _finish(
        self,
        job_id: str,
        status: str,
        *,
        reason: str = "",
        message: str | None = None,
        cancel_confirmed: bool = False,
        progress: int | None = None,
        execution_started: bool | None = None,
        keep_lease: bool = False,
    ) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record is None or not self._record_matches_job_locked(job_id):
                return
            if record["status"] in TERMINAL_STATUSES and status != record["status"]:
                return
            record["status"] = status
            record["reason"] = str(reason or "")
            if message is not None:
                record["message"] = str(message)
            if cancel_confirmed:
                record["cancel_confirmed"] = True
            if progress is not None:
                record["progress"] = progress
            if execution_started is not None:
                record["execution_started"] = bool(execution_started)
            record["updated_at"] = self._wall_clock()
            if not keep_lease and self._active_id == job_id:
                self._active_id = None
            if status in TERMINAL_STATUSES and self._external_lease is not None:
                if records_compatible(self._external_lease, record):
                    self._external_lease = None
            record["can_submit"] = self._active_id is None and self._external_lease is None
            self._prune_locked()

    # ---- 조회/정리 헬퍼 ---------------------------------------------------
    def _current_record_locked(self) -> dict[str, Any] | None:
        if self._active_id is not None and self._active_id in self._records:
            return self._records[self._active_id]
        for key in reversed(self._records):
            return self._records[key]
        return None

    def _merge_authority_locked(
        self,
        local: dict[str, Any],
        agent: dict[str, Any] | None,
        merged: dict[str, Any],
    ) -> None:
        if agent is None or merged is local:
            return
        if not records_compatible(local, merged):
            return
        self._records[local["request_id"]] = merged
        if merged.get("status") in TERMINAL_STATUSES:
            if self._active_id == local["request_id"]:
                self._active_id = None
            if self._external_lease is not None and records_compatible(
                self._external_lease, merged
            ):
                # 같은 요청의 matching terminal 도달/실패/취소만 lease 를 해제한다.
                self._external_lease = None
            merged["can_submit"] = self._active_id is None and self._external_lease is None

    def _observe_agent_record_locked(self, record: dict[str, Any] | None) -> None:
        """에이전트 기록을 보수적으로 채택하고 matching terminal 에서만 해제한다."""
        if record is None:
            # 기록이 사라져도 진행 중이던 lease 를 자동 해제하지 않는다.
            return
        adopted = adopt_external_record(record, now=self._wall_clock())
        if adopted is None:
            return
        local = self._records.get(str(adopted.get("request_id") or ""))
        if (
            local is not None
            and local.get("status") in TERMINAL_STATUSES
            and adopted.get("status") not in TERMINAL_STATUSES
        ):
            # 로컬에서 이미 종결된 요청의 늦은 running 기록은 lease 를 되살리지 않는다.
            return
        if self._external_lease is None:
            if adopted.get("status") in LEASE_STATUSES:
                self._external_lease = adopted
            return
        if not records_compatible(self._external_lease, adopted):
            # id/target/geometry 가 다른 기록은 다른 요청이므로 건드리지 않는다.
            return
        if adopted.get("status") in TERMINAL_STATUSES:
            self._external_lease = None
        else:
            self._external_lease = adopted

    def _record_matches_job_locked(self, job_id: str) -> bool:
        record = self._records.get(job_id)
        job = self._jobs.get(job_id)
        if record is None or job is None:
            return False
        goal = job.get("goal") or {}
        if str(record.get("request_id") or "") != str(goal.get("request_id") or ""):
            return False
        return records_compatible(
            record,
            {
                "request_id": goal.get("request_id"),
                "robot_id": goal.get("robot_id"),
                "target": goal.get("target"),
                "geometry_id": goal.get("geometry_id"),
            },
        )

    def _copy_record(self, record: Mapping[str, Any]) -> dict[str, Any]:
        copied = dict(record)
        target = record.get("target")
        copied["target"] = dict(target) if isinstance(target, Mapping) else None
        copied["can_submit"] = can_submit(
            copied, self._active_id is not None or self._external_lease is not None
        )
        return copied

    def _prune_locked(self) -> None:
        while len(self._records) > self._max_records:
            removed = False
            for key in list(self._records):
                if key == self._active_id:
                    continue
                if self._records[key].get("status") in TERMINAL_STATUSES:
                    self._records.pop(key, None)
                    self._signatures.pop(key, None)
                    self._jobs.pop(key, None)
                    removed = True
                    break
            if not removed:
                return
