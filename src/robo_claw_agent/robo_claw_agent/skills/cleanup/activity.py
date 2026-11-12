"""정리 활동 상태의 bounded 저장소.

정리 실행(``tidy_home``/``cleanup_demo``)은 자율협동 세션과 별개로 실행될 수 있다.
이 모듈은 그 실행을 ``(source, run_id)`` 별 항목으로 제한된 개수만 유지해, 관측
API(QueryState)가 진행/대기/종료 상태를 표시할 수 있게 한다. 협동 저널의 세션
필드나 상대 태스크 상태는 건드리지 않는다.

설계 원칙:

- 문자열은 협동 저널과 같은 마스킹/상한을 적용한다(QueryState.activity 로 비밀값이
  새지 않게 한다).
- ``run_id``/``session_id``/``task_id`` 는 64자 관측 ID 로 정규화한다. 원본 ID 는
  이벤트 details 에만 남긴다(protocol 스키마는 줄이지 않는다).
- 상한에 도달하면 오래된 비활성 항목을 먼저 정리하고, 모두 진행 중이면 새 관측을
  거부한다(진행 중 항목을 밀어내지 않는다).
- 종료된 활동은 TTL(기본 1시간) 동안 마지막 상태를 유지한다.

ROS2/네트워크 의존이 없고, 순수 상태 컨테이너만 둔다.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any

DEFAULT_MAX_ACTIVITIES = 16
DEFAULT_ACTIVITY_TTL_SEC = 3600.0
MAX_SHORT_TEXT = 120
MAX_TEXT = 500

#: 단계 키 → 사람이 읽는 한국어 이름. 프론트는 ``phase_label`` 을 우선 사용한다.
PHASE_LABELS = {
    "start": "정리 시작",
    "resolve_place": "장소 확인",
    "navigate": "장소로 이동",
    "observe": "주변 관찰",
    "select_target": "정리 대상 선택",
    "select_peer": "동료 선택",
    "stage": "동료 접근 공간 확보",
    "wait_peer": "동료 응답 대기",
    "peer_result": "동료 결과 확인",
    "demo_reobserve": "요청 대상 재관찰",
    "demo_motion": "정리 시연 모션 요청",
    "local_motion": "로컬 정리 동작",
    "policy": "정책 확인",
    "cancel": "작업 취소",
    "finish": "정리 종료",
}


def phase_label(phase: object) -> str:
    """단계 키의 한국어 표시 이름(모르는 키는 그대로)."""
    key = str(phase or "")
    return PHASE_LABELS.get(key, key)


def observation_id(value: object) -> str:
    """관측/이벤트 상관 ID 를 64자로 정규화한다(긴 ID 는 stable hash)."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 64:
        return raw
    return "obs-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:60]


def sanitize_text(value: object, limit: int = MAX_TEXT) -> str:
    """협동 저널과 같은 마스킹/길이 제한을 적용한다(실패하면 단순 절단)."""
    text = "" if value is None else str(value)
    try:
        from ..cooperation_skill.sanitize import sanitize_text as mask

        return mask(text, limit)
    except Exception:  # noqa: BLE001 - 마스킹 모듈을 못 쓰면 길이만 제한한다
        return text[: max(0, int(limit))]


def clip_text(value: object, limit: int = MAX_TEXT) -> str:
    """활동 상태에 넣을 문자열을 마스킹하고 자른다."""
    return sanitize_text(value, limit)


def _new_activity(*, source: str, run_id: str, robot_id: str, goal: str, now: float) -> dict:
    return {
        "source": source,
        "run_id": run_id,
        "session_id": run_id,
        "robot_id": clip_text(robot_id, 64),
        "task_id": "",
        "goal": clip_text(goal),
        "phase": "",
        "phase_label": "",
        "place": "",
        "target": "",
        "peer": "",
        "transport": "",
        "status": "running",
        "task_status": "",
        "result": "",
        "failure_reason": "",
        # observed=True 는 상대 로봇이 실제 결과를 보고한 경우에만 쓴다.
        "observed": False,
        # 실제 물리 정리/모션 완료는 이 경로에서 확인할 수 없다.
        "physical_cleanup_confirmed": False,
        "motion_completion_confirmed": False,
        "active": True,
        "started_at": now,
        "updated_at": now,
        "finished_at": None,
        "duration_ms": 0.0,
    }


class CleanupActivityStore:
    """``(source, run_id)`` 별 정리 활동 상태를 제한된 개수만 유지한다."""

    def __init__(
        self,
        max_activities: int = DEFAULT_MAX_ACTIVITIES,
        ttl_sec: float = DEFAULT_ACTIVITY_TTL_SEC,
    ) -> None:
        self._lock = threading.RLock()
        self._max = max(1, int(max_activities))
        self._ttl = max(0.0, float(ttl_sec))
        self._activities: dict[tuple[str, str], dict[str, Any]] = {}

    def begin(
        self,
        *,
        source: object,
        run_id: object,
        robot_id: str = "",
        goal: str = "",
        phase: str = "",
        place: str = "",
        target: str = "",
        task_id: str = "",
        peer: str = "",
        now: float | None = None,
    ) -> dict[str, Any] | None:
        source_key = str(source or "")
        run_key = observation_id(run_id)
        if not run_key:
            return None
        started = time.time() if now is None else float(now)
        key = (source_key, run_key)
        with self._lock:
            self._purge_expired(started)
            existing = self._activities.get(key)
            if existing is not None and existing["active"]:
                # A repeated/busy request must not replace the current owner.
                return None
            if existing is None and len(self._activities) >= self._max:
                # 진행 중 항목은 밀어내지 않는다. 오래된 비활성 항목만 정리하고,
                # 모두 진행 중이면 이번 관측 등록을 거부한다(실행은 계속된다).
                if not self._drop_oldest_inactive():
                    return None
            activity = _new_activity(
                source=source_key, run_id=run_key, robot_id=robot_id, goal=goal, now=started
            )
            if existing is not None:
                # 같은 run 의 재시작은 시작 시각/소유 로봇을 보존한다.
                activity["started_at"] = existing["started_at"]
                activity["robot_id"] = existing["robot_id"] or activity["robot_id"]
            if phase:
                activity["phase"] = clip_text(phase, 64)
                activity["phase_label"] = phase_label(phase)
            activity["place"] = clip_text(place, MAX_SHORT_TEXT)
            activity["target"] = clip_text(target, MAX_SHORT_TEXT)
            activity["task_id"] = observation_id(task_id)
            activity["peer"] = clip_text(peer, 128)
            self._activities[key] = activity
            return dict(activity)

    def update(
        self,
        key: tuple[str, str],
        *,
        active: bool | None = None,
        status: str | None = None,
        phase: str | None = None,
        place: str | None = None,
        target: str | None = None,
        task_id: str | None = None,
        peer: str | None = None,
        transport: str | None = None,
        task_status: str | None = None,
        result: str | None = None,
        failure_reason: str | None = None,
        observed: bool | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        stamp = time.time() if now is None else float(now)
        with self._lock:
            activity = self._activities.get(key)
            if activity is None:
                return None
            if active is not None:
                activity["active"] = bool(active)
            if status is not None:
                activity["status"] = clip_text(status, 32)
            if phase is not None:
                activity["phase"] = clip_text(phase, 64)
                activity["phase_label"] = phase_label(phase)
            if place is not None:
                activity["place"] = clip_text(place, MAX_SHORT_TEXT)
            if target is not None:
                activity["target"] = clip_text(target, MAX_SHORT_TEXT)
            if task_id is not None:
                activity["task_id"] = observation_id(task_id) if task_id else ""
            if peer is not None:
                activity["peer"] = clip_text(peer, 128)
            if transport is not None:
                activity["transport"] = clip_text(transport, 32)
            if task_status is not None:
                activity["task_status"] = clip_text(task_status, 32)
            if result is not None:
                activity["result"] = clip_text(result)
            if failure_reason is not None:
                activity["failure_reason"] = clip_text(failure_reason, MAX_SHORT_TEXT)
            if observed is not None:
                activity["observed"] = bool(observed)
            activity["updated_at"] = stamp
            activity["duration_ms"] = round(
                max(0.0, (stamp - float(activity["started_at"])) * 1000.0), 1
            )
            return dict(activity)

    def finish(
        self,
        key: tuple[str, str],
        *,
        status: str = "finished",
        result: str = "",
        failure_reason: str = "",
        task_status: str | None = None,
        duration_ms: float | None = None,
        observed: bool | None = False,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        stamp = time.time() if now is None else float(now)
        activity = self.update(
            key,
            active=False,
            status=status,
            result=result,
            failure_reason=failure_reason,
            task_status=task_status,
            observed=observed,
            now=stamp,
        )
        if activity is None:
            return None
        with self._lock:
            stored = self._activities.get(key)
            if stored is None:
                return activity
            stored["finished_at"] = stamp
            if duration_ms is not None:
                stored["duration_ms"] = round(max(0.0, float(duration_ms)), 1)
            return dict(stored)

    def get(self, key: tuple[str, str], now: float | None = None) -> dict[str, Any] | None:
        stamp = time.time() if now is None else float(now)
        with self._lock:
            self._purge_expired(stamp)
            activity = self._activities.get(key)
            return dict(activity) if activity is not None else None

    def latest(self, now: float | None = None) -> dict[str, Any] | None:
        """가장 최근 활동. 진행 중 항목이 있으면 그중 최신을 우선한다."""
        stamp = time.time() if now is None else float(now)
        with self._lock:
            self._purge_expired(stamp)
            if not self._activities:
                return None
            active = [item for item in self._activities.values() if item["active"]]
            pool = active or list(self._activities.values())
            return dict(max(pool, key=lambda item: item["updated_at"]))

    def snapshot(self, now: float | None = None) -> dict[str, Any] | None:
        return self.latest(now)

    def _purge_expired(self, now: float) -> None:
        if self._ttl <= 0.0:
            return
        expired = [
            key
            for key, item in self._activities.items()
            if not item["active"] and now - float(item["updated_at"]) > self._ttl
        ]
        for key in expired:
            self._activities.pop(key, None)

    def _drop_oldest_inactive(self) -> bool:
        inactive = [(key, item) for key, item in self._activities.items() if not item["active"]]
        if not inactive:
            return False
        victim = min(inactive, key=lambda entry: entry[1]["updated_at"])[0]
        self._activities.pop(victim, None)
        return True
