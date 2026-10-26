"""정리 태스크 실행 1회를 구조화 레코드(JSONL)로 남기는 실험 계측.

논문 표 2(전체 성공률·할당 정확도·위임 성공률·평균 시간)를 로그 문자열 파싱 없이
산출하기 위한 최소 계측이다. 정리 흐름의 판단을 바꾸지 않고, 이미 내려진 결정을
기록만 한다.

설계 원칙:

- ``RC_CLEANUP_RUN_LOG_PATH`` 가 비어 있으면 파일을 쓰지 않고 레코드만 반환한다.
  기본 운용과 단위 테스트에서 부작용이 없다.
- 기록 실패는 정리 동작을 막지 않는다. 파일 입출력 오류는 경고 1회만 남긴다.
- 시간은 밀리초 단위 ``phase_ms`` 로 남겨 구간별 평균을 바로 계산할 수 있게 한다.

레코드 종류:

``cleanup_handoff``
    정리 대상 1건에 대해 위임 여부를 결정한 시점. 선택된 동료, 전송 경로,
    후보 목록, 차단 사유, 탐색 소요 시간을 남긴다. → 할당 정확도·위임 성공률
``cleanup_delegate``
    동료에게 보낸 위임 요청 1건의 실행 결과. 성공 여부, 응답 메시지, 소요 시간을 남긴다.
    → 위임 성공률
``cleanup_run``
    정리 실행 1회가 끝난 시점. 장소별 status, 구간별 소요 시간, 오류를 남긴다.
    → 전체 성공률·평균 시간

``success`` 판정 규칙은 ``run_success`` 다. 방문한 모든 장소가
``HANDLED_STATUSES`` 에 속할 때만 성공으로 본다. 판정을 바꿔 다시 계산할 수 있도록
원본 ``results`` 와 ``outcomes`` 를 함께 남긴다.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

ENV_RUN_LOG_PATH = "RC_CLEANUP_RUN_LOG_PATH"

# 위임 또는 로컬 처리까지 완료된 상태. 이 상태들만 "정리 대상 1건이 처리됨"으로 본다.
HANDLED_STATUSES = frozenset({"handled", "delegated", "delegated_demo"})

_MAX_CANDIDATES = 20
_MAX_CANDIDATE_CAPABILITIES = 8

_WRITE_LOCK = threading.Lock()
_WRITE_WARNED = False


def run_log_path() -> str:
    """활성 run log 경로. 미설정이면 빈 문자열."""
    return os.environ.get(ENV_RUN_LOG_PATH, "").strip()


def new_run_id(*, agent_id: str = "", now: float | None = None) -> str:
    """실행 1회를 식별하는 run_id. 로그·JSONL·메시지를 잇는 키로 쓴다."""
    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime(time.time() if now is None else now))
    owner = "".join(ch for ch in str(agent_id) if ch.isalnum() or ch in "_-")
    return f"{owner or 'agent'}-{stamp}-{uuid.uuid4().hex[:6]}"


def outcome_counts(results: list[dict[str, Any]] | None) -> dict[str, int]:
    """장소별 status 를 집계한다."""
    counts: dict[str, int] = {}
    for result in results or []:
        status = str(result.get("status", ""))
        counts[status] = counts.get(status, 0) + 1
    return counts


def run_success(results: list[dict[str, Any]] | None) -> bool:
    """방문한 모든 장소가 처리 완료 상태일 때만 성공으로 판정한다."""
    statuses = [str(result.get("status", "")) for result in results or []]
    if not statuses:
        return False
    return all(status in HANDLED_STATUSES for status in statuses)


def peer_candidates(
    peers: dict[str, dict[str, Any]] | None, selected: str = ""
) -> list[dict[str, Any]]:
    """설정에 등록된 동료 목록을 후보 스냅샷으로 만든다(추가 RPC 없음)."""
    wanted = str(selected).casefold()
    candidates: list[dict[str, Any]] = []
    for name in sorted(peers or {}):
        info = peers.get(name) or {}
        capabilities = info.get("capabilities") or []
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        candidates.append(
            {
                "peer": str(name),
                "capabilities": [str(capability) for capability in capabilities][
                    :_MAX_CANDIDATE_CAPABILITIES
                ],
                "selected": str(name).casefold() == wanted,
            }
        )
    return candidates[:_MAX_CANDIDATES]


def record_cleanup_handoff(
    *,
    run_id: str = "",
    source: str = "",
    agent_id: str = "",
    place: str = "",
    handoff_kind: str = "",
    target: str = "",
    peer: str = "",
    transport: str = "",
    send_path: str = "",
    blocked: bool = False,
    reason: str = "",
    candidates: list[dict[str, Any]] | None = None,
    discovery_ms: float = 0.0,
) -> dict[str, Any]:
    """정리 대상 1건의 위임/차단 결정을 기록한다."""
    record = _base("cleanup_handoff", run_id=run_id, source=source, agent_id=agent_id)
    record.update(
        {
            "place": str(place),
            "handoff_kind": str(handoff_kind),
            "target": str(target),
            "peer": str(peer),
            "transport": str(transport),
            "send_path": str(send_path),
            "blocked": bool(blocked),
            "reason": str(reason),
            "candidates": list(candidates or []),
            "discovery_ms": round(max(0.0, float(discovery_ms)), 1),
        }
    )
    _write(record)
    return record


def record_cleanup_delegate(
    *,
    run_id: str = "",
    source: str = "",
    agent_id: str = "",
    place: str = "",
    peer: str = "",
    transport: str = "",
    send_path: str = "",
    success: bool = False,
    message: str = "",
    duration_ms: float = 0.0,
) -> dict[str, Any]:
    """동료에게 보낸 위임 요청 1건의 실행 결과를 기록한다.

    ``success`` 는 요청 스킬이 성공을 반환했다는 뜻이다. 수신 로봇이 정리를 거부하는
    문장을 돌려주면서 성공을 반환할 수 있으므로 ``message`` 를 함께 남긴다.
    """
    record = _base("cleanup_delegate", run_id=run_id, source=source, agent_id=agent_id)
    record.update(
        {
            "place": str(place),
            "peer": str(peer),
            "transport": str(transport),
            "send_path": str(send_path),
            "success": bool(success),
            "message": str(message)[:500],
            "duration_ms": round(max(0.0, float(duration_ms)), 1),
        }
    )
    _write(record)
    return record


def record_cleanup_run(
    *,
    run_id: str = "",
    agent_id: str = "",
    scope: str = "",
    target_location: str = "",
    places: list[str] | None = None,
    results: list[dict[str, Any]] | None = None,
    phase_ms: dict[str, float] | None = None,
    elapsed_ms: float = 0.0,
    error: str = "",
) -> dict[str, Any]:
    """정리 실행 1회의 최종 결과를 기록한다."""
    record = _base("cleanup_run", run_id=run_id, source="tidy_home", agent_id=agent_id)
    handled = run_success(results)
    record.update(
        {
            "scope": str(scope),
            "target_location": str(target_location),
            "place_count": len(places or []),
            "places": [str(place) for place in (places or [])][:_MAX_CANDIDATES],
            "results": [
                {
                    "place": str(result.get("place", "")),
                    "status": str(result.get("status", "")),
                    "partial_observation": bool(result.get("partial_observation", False)),
                }
                for result in results or []
            ],
            "outcomes": outcome_counts(results),
            "handled": handled,
            "success": handled and not error,
            "phase_ms": {
                str(name): round(max(0.0, float(value)), 1)
                for name, value in (phase_ms or {}).items()
            },
            "elapsed_ms": round(max(0.0, float(elapsed_ms)), 1),
            "error": str(error),
        }
    )
    _write(record)
    return record


def _base(event: str, *, run_id: str, source: str, agent_id: str) -> dict[str, Any]:
    now = time.time()
    return {
        "event": event,
        "ts": round(now, 3),
        "iso": datetime.fromtimestamp(now).astimezone().isoformat(timespec="milliseconds"),
        "run_id": str(run_id),
        "source": str(source),
        "agent_id": str(agent_id),
    }


def _write(record: dict[str, Any]) -> None:
    global _WRITE_WARNED
    path = run_log_path()
    if not path:
        return
    try:
        with _WRITE_LOCK:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        if not _WRITE_WARNED:
            _WRITE_WARNED = True
            logger.warning("[CleanupMetrics] run log write failed (%s): %s", path, exc)
