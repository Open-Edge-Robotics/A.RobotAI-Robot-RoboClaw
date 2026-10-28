#!/usr/bin/env python3
"""정리 태스크 run log(JSONL)를 논문 표 2 형식으로 집계한다.

``RC_CLEANUP_RUN_LOG_PATH`` 가 가리키는 파일(또는 인자로 준 파일)을 읽어
반복 횟수·전체 성공률·할당 정확도·위임 성공률·평균 시간을 계산한다.

집계 규칙(레코드에 원본이 남아 있으므로 다르게 재계산할 수 있다):

- 반복 횟수: ``cleanup_run`` 레코드 수
- 전체 성공률: ``success=true`` 비율. 방문한 모든 장소가 ``handled``/
  ``delegated``/``delegated_demo`` 일 때만 성공으로 기록된다
- 할당 정확도: 차단되지 않은 위임 결정 중 ``--expect-peer`` 와 일치한 비율.
  ``--expect-peer`` 를 주지 않으면 계산하지 않는다(정답 라벨이 없으므로)
- 위임 성공률: ``cleanup_delegate`` 레코드의 ``success`` 비율. 레코드가 없는 이전
  로그에서는 실행 레코드의 장소 status 가 ``delegated``/``delegated_demo`` 인 실행의 비율로
  대신 계산한다(``delegation_source`` 로 구분)
- 평균 시간: ``phase_ms`` 구간별 평균과 ``elapsed_ms`` 평균

사용 예::

    RC_CLEANUP_RUN_LOG_PATH=~/runs.jsonl python3 scripts/cleanup_run_report.py \\
        --expect-peer cloid2 --markdown
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

ENV_RUN_LOG_PATH = "RC_CLEANUP_RUN_LOG_PATH"
DELEGATED_STATUSES = frozenset({"delegated", "delegated_demo"})
PHASE_ORDER = ("navigate", "observe", "discovery", "delegate", "handle")

# 실패 원인 분류 표지. 네트워크 단절·네비게이션 타임아웃은 로봇 사이 협업이 아니라
# 환경/주행 문제이므로 집계 횟수에서 제외하고 예외로 기록한다. 커뮤니케이션 실패
# (전달·응답 자체가 실패)는 실패로 계산한다.
NETWORK_FAILURE_MARKERS = (
    "no route to host",
    "network is unreachable",
    "unreachable",
    "네트워크 단절",
    "네트워크 이탈",
    "통신 두절",
)
COMMUNICATION_FAILURE_MARKERS = (
    "grpc",
    "connection",
    "connect",
    "unavailable",
    "socket",
    "timeout after",
    "인증",
    "연결",
    "응답 없음",
    "응답을 받지 못",
)
NAVIGATION_FAILURE_MARKERS = (
    "visit_place",
    "도착하지 못",
    "도달하지 못",
    "주행",
    "타임아웃",
    "timeout",
)
# 집계에서 빼는 예외 범주(환경·주행 문제).
EXCEPTION_CATEGORIES = frozenset({"network", "navigation"})
CATEGORY_LABELS = {
    "network": "네트워크 단절",
    "navigation": "네비게이션 타임아웃",
    "communication": "커뮤니케이션 실패",
    "other": "그 외 실패",
}


def load_records(path: str) -> list[dict]:
    records: list[dict] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _rate(numerator: int, denominator: int) -> float:
    return (numerator / denominator * 100.0) if denominator else 0.0


def summarize(records: list[dict], expect_peer: str = "") -> dict:
    runs = [record for record in records if record.get("event") == "cleanup_run"]
    handoffs = [record for record in records if record.get("event") == "cleanup_handoff"]
    delegates = [record for record in records if record.get("event") == "cleanup_delegate"]

    runs_by_id: dict[str, dict] = {}
    for run in runs:
        runs_by_id.setdefault(str(run.get("run_id", "")), run)

    delegated_runs: set[str] = set()
    for run_id, run in runs_by_id.items():
        statuses = {str(result.get("status", "")) for result in run.get("results", [])}
        if statuses & DELEGATED_STATUSES:
            delegated_runs.add(run_id)

    prepared_handoffs = [handoff for handoff in handoffs if not handoff.get("blocked")]
    prepared_runs = {str(handoff.get("run_id", "")) for handoff in prepared_handoffs}
    prepared_runs.discard("")

    # 위임 구간은 위임 레코드의 소요 시간을 우선 사용한다. 실행 레코드의 delegate 값은
    # 계측 단위가 바뀐 과거 기록이 섞일 수 있어, 레코드가 있으면 그쪽이 정확하다.
    delegate_ms_by_run: dict[str, float] = defaultdict(float)
    for record in delegates:
        run_id = str(record.get("run_id", ""))
        delegate_ms_by_run[run_id] += float(record.get("duration_ms", 0.0))

    delegates_by_run: dict[str, list[dict]] = defaultdict(list)
    for record in delegates:
        delegates_by_run[str(record.get("run_id", ""))].append(record)

    failures = classify_failures(runs, delegates_by_run)
    exceptions = [item for item in failures if item["category"] in EXCEPTION_CATEGORIES]
    counted_failures = [item for item in failures if item["category"] not in EXCEPTION_CATEGORIES]
    exception_run_ids = {item["run_id"] for item in exceptions}
    effective_delegates = [
        record for record in delegates if str(record.get("run_id", "")) not in exception_run_ids
    ]

    phase_totals: dict[str, list[float]] = defaultdict(list)
    elapsed: list[float] = []
    for run_id, run in runs_by_id.items():
        for name, value in (run.get("phase_ms") or {}).items():
            if str(name) == "delegate" and run_id in delegate_ms_by_run:
                continue
            phase_totals[str(name)].append(float(value))
        if run_id in delegate_ms_by_run:
            phase_totals["delegate"].append(delegate_ms_by_run[run_id])
        elapsed.append(float(run.get("elapsed_ms", 0.0)))

    if delegates:
        delegation_success_rate = _rate(
            sum(record.get("success") is True for record in delegates), len(delegates)
        )
        delegation_source = "cleanup_delegate"
    elif prepared_runs:
        delegation_success_rate = _rate(len(delegated_runs & prepared_runs), len(prepared_runs))
        delegation_source = "run_status"
    else:
        # 위임을 시도한 실행이 없으면(기능 제약 등) 비율 대신 n/a 로 둔다.
        delegation_success_rate = None
        delegation_source = "run_status"

    summary = {
        "repeat_count": len(runs),
        "success_rate": _rate(sum(run.get("success") is True for run in runs), len(runs)),
        "handoff_prepared": len(prepared_handoffs),
        "handoff_blocked": len(handoffs) - len(prepared_handoffs),
        "delegation_attempts": len(delegates),
        "delegation_success_rate": delegation_success_rate,
        "delegation_source": delegation_source,
        "mean_elapsed_ms": (sum(elapsed) / len(elapsed)) if elapsed else 0.0,
        "mean_phase_ms": {
            name: (sum(values) / len(values))
            for name, values in sorted(
                phase_totals.items(),
                key=lambda item: (
                    PHASE_ORDER.index(item[0]) if item[0] in PHASE_ORDER else len(PHASE_ORDER),
                    item[0],
                ),
            )
        },
        "outcomes": {},
        "allocation_accuracy": None,
    }

    # 예외 처리 지표: 네트워크 단절·네비게이션 타임아웃 회차는 반복 횟수에서 빼고
    # 따로 기록한다. 커뮤니케이션 실패는 그대로 실패로 센다.
    summary["failures"] = failures
    summary["exceptions"] = exceptions
    summary["exception_count"] = len(exceptions)
    summary["exception_counts_by_category"] = {
        label: sum(1 for item in exceptions if item["category"] == category)
        for category, label in CATEGORY_LABELS.items()
        if any(item["category"] == category for item in exceptions)
    }
    summary["counted_failure_count"] = len(counted_failures)
    summary["effective_repeat_count"] = len(runs) - len(exceptions)
    summary["effective_success_rate"] = _rate(
        sum(
            1
            for run in runs
            if run.get("success") is True and str(run.get("run_id", "")) not in exception_run_ids
        ),
        len(runs) - len(exceptions),
    )
    if delegation_success_rate is None:
        summary["effective_delegation_success_rate"] = None
    else:
        summary["effective_delegation_success_rate"] = _rate(
            sum(record.get("success") is True for record in effective_delegates),
            len(effective_delegates),
        )

    outcomes: dict[str, int] = {}
    for run in runs:
        for status, count in (run.get("outcomes") or {}).items():
            outcomes[str(status)] = outcomes.get(str(status), 0) + int(count)
    summary["outcomes"] = dict(sorted(outcomes.items()))

    if expect_peer:
        wanted = expect_peer.casefold()
        matched = sum(
            1 for handoff in prepared_handoffs if str(handoff.get("peer", "")).casefold() == wanted
        )
        summary["allocation_accuracy"] = _rate(matched, len(prepared_handoffs))
        summary["expected_peer"] = expect_peer

    return summary


def classify_failure_message(message: str) -> str:
    """실패 메시지의 원인 범주를 고른다.

    네트워크 단절·네비게이션 타임아웃은 협업 실패가 아니라 환경/주행 문제이므로
    집계에서 빼는 대상(``EXCEPTION_CATEGORIES``)이고, 전달·응답 자체가 실패한
    커뮤니케이션 실패는 실패로 센다.
    """
    text = str(message or "").casefold()
    if any(marker in text for marker in NETWORK_FAILURE_MARKERS):
        return "network"
    if any(marker in text for marker in COMMUNICATION_FAILURE_MARKERS):
        return "communication"
    if any(marker in text for marker in NAVIGATION_FAILURE_MARKERS):
        return "navigation"
    return "other"


def classify_failures(runs: list[dict], delegates_by_run: dict[str, list[dict]]) -> list[dict]:
    """실패한 실행을 원인 범주별로 나눈다(재계산 가능한 감사 내역)."""
    failures: list[dict] = []
    for run in runs:
        if run.get("success") is True:
            continue
        run_id = str(run.get("run_id", ""))
        place = place_of_run(run)
        records = delegates_by_run.get(run_id, [])
        if not records:
            failures.append(
                {
                    "run_id": run_id,
                    "place": place,
                    "category": "other",
                    "reason": "위임 요청 이전 단계에서 종료(delegate 레코드 없음)",
                }
            )
            continue
        failed = [record for record in records if record.get("success") is not True]
        if not failed:
            failures.append(
                {
                    "run_id": run_id,
                    "place": place,
                    "category": "other",
                    "reason": "위임은 성공, 이후 단계에서 실패",
                }
            )
            continue
        message = str(failed[0].get("message", "")).strip()
        failures.append(
            {
                "run_id": run_id,
                "place": place,
                "category": classify_failure_message(message),
                "reason": message or "메시지 없음",
            }
        )
    return failures


def place_of_run(run: dict) -> str:
    """실행 1회가 향한 장소명을 고른다(단일 방 요청은 target_location)."""
    place = str(run.get("target_location") or "").strip()
    if place:
        return place
    places = [str(item) for item in run.get("places") or [] if str(item).strip()]
    if places:
        return ",".join(places)
    result_places = [
        str(item.get("place", "")).strip()
        for item in run.get("results") or []
        if str(item.get("place", "")).strip()
    ]
    return ",".join(dict.fromkeys(result_places)) if result_places else "unknown"


def split_by_place(records: list[dict], directory: str) -> dict[str, str]:
    """run log 를 장소별 JSONL 로 나누어 쓴다.

    4장소 순환 실험처럼 한 파일에 여러 장소가 섞일 때 장소별로 따로 집계할 수 있도록
    ``cleanup_runs_<장소>.jsonl`` 을 만든다. 반환값은 장소 → 파일 경로다.
    """
    place_by_run: dict[str, str] = {}
    for record in records:
        if record.get("event") == "cleanup_run":
            place_by_run.setdefault(str(record.get("run_id", "")), place_of_run(record))

    buckets: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        run_id = str(record.get("run_id", ""))
        place = place_by_run.get(run_id)
        if place is None:
            # 실행 기록 없이 남은 handoff/delegate 는 레코드 자체의 장소를 쓴다.
            place = str(record.get("place") or "").strip() or "unknown"
        buckets[place].append(record)

    os.makedirs(directory, exist_ok=True)
    written: dict[str, str] = {}
    for place in sorted(buckets):
        safe = re.sub(r"[^\w가-힣.-]+", "_", place).strip("_") or "unknown"
        path = os.path.join(directory, f"cleanup_runs_{safe}.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for record in buckets[place]:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        written[place] = path
    return written


def render_place_table(rows: list[tuple[str, dict]], exclude_exceptions: bool = False) -> str:
    """장소별 집계를 표 2 열 구성으로 출력한다."""
    lines = [
        "| 장소 | 반복 횟수 | 전체 성공률 | 할당 정확도 | 위임 성공률 | 평균 시간 | 재계획 성공률 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for place, summary in rows:
        repeat_count = (
            summary["effective_repeat_count"] if exclude_exceptions else summary["repeat_count"]
        )
        success_rate = (
            summary["effective_success_rate"] if exclude_exceptions else summary["success_rate"]
        )
        delegation_rate = (
            summary["effective_delegation_success_rate"]
            if exclude_exceptions
            else summary["delegation_success_rate"]
        )
        lines.append(
            f"| {place} | {repeat_count} | {_fmt_rate(success_rate)} "
            f"| {_fmt_rate(summary['allocation_accuracy'])} "
            f"| {_fmt_rate(delegation_rate)} "
            f"| {_fmt_ms(summary['mean_elapsed_ms'])} | 해당 없음 |"
        )
    return "\n".join(lines)


def _fmt_rate(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"


def _fmt_ms(value: float) -> str:
    return f"{value / 1000.0:.1f}s"


def render_markdown(summary: dict, exclude_exceptions: bool = False) -> str:
    repeat_count = (
        summary["effective_repeat_count"] if exclude_exceptions else summary["repeat_count"]
    )
    success_rate = (
        summary["effective_success_rate"] if exclude_exceptions else summary["success_rate"]
    )
    delegation_rate = (
        summary["effective_delegation_success_rate"]
        if exclude_exceptions
        else summary["delegation_success_rate"]
    )
    lines = [
        "| 실험 조건 | 반복 횟수 | 전체 성공률 | 할당 정확도 | 위임 성공률 | 평균 시간 | 재계획 성공률 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
        (
            f"| 정상 협업 | {repeat_count} | {_fmt_rate(success_rate)} "
            f"| {_fmt_rate(summary['allocation_accuracy'])} "
            f"| {_fmt_rate(delegation_rate)} "
            f"| {_fmt_ms(summary['mean_elapsed_ms'])} | 해당 없음 |"
        ),
    ]
    if summary["mean_phase_ms"]:
        lines += ["", "구간별 평균:", ""]
        for name, value in summary["mean_phase_ms"].items():
            lines.append(f"- {name}: {_fmt_ms(value)}")
    if summary["outcomes"]:
        lines += [
            "",
            "장소 status 집계: "
            + ", ".join(f"{name}={count}" for name, count in summary["outcomes"].items()),
        ]
    lines.append(f"위임 준비 {summary['handoff_prepared']}건 / 차단 {summary['handoff_blocked']}건")
    if exclude_exceptions:
        exceptions = summary.get("exceptions") or []
        lines += [
            "",
            f"원래 반복 {summary['repeat_count']}회 중 예외 {summary['exception_count']}회 제외"
            f" → 유효 반복 {summary['effective_repeat_count']}회"
            f" (전체 기준 성공률 {_fmt_rate(summary['success_rate'])})",
        ]
        if exceptions:
            lines.append("예외 기록(집계 횟수에서 제외):")
            for item in exceptions:
                label = CATEGORY_LABELS.get(item["category"], item["category"])
                lines.append(f"- [{label}] {item['place']} `{item['run_id']}` — {item['reason']}")
        counted = [item for item in summary.get("failures") or [] if item not in exceptions]
        lines.append(f"실패로 집계한 회차: {len(counted)}건")
        for item in counted:
            label = CATEGORY_LABELS.get(item["category"], item["category"])
            lines.append(f"- [{label}] {item['place']} `{item['run_id']}` — {item['reason']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="정리 태스크 run log 집계")
    parser.add_argument(
        "path",
        nargs="?",
        default=os.environ.get(ENV_RUN_LOG_PATH, ""),
        help=f"JSONL 경로(기본: ${ENV_RUN_LOG_PATH})",
    )
    parser.add_argument("--expect-peer", default="", help="할당 정답으로 기대하는 동료 이름")
    parser.add_argument(
        "--split-dir",
        default="",
        help="지정하면 장소별 JSONL(cleanup_runs_<장소>.jsonl)을 쓰고 장소별 표를 출력한다",
    )
    parser.add_argument("--markdown", action="store_true", help="표 2 형식 마크다운으로 출력")
    parser.add_argument(
        "--exclude-exceptions",
        action="store_true",
        help=(
            "네트워크 단절·네비게이션 타임아웃으로 실패한 회차를 반복 횟수에서 제외하고"
            " 예외로 따로 출력한다(커뮤니케이션 실패는 그대로 실패로 집계)"
        ),
    )
    parser.add_argument("--json", action="store_true", help="집계 결과를 JSON으로 출력")
    args = parser.parse_args(argv)

    if not args.path:
        print(
            f"run log 경로가 없습니다. 인자 또는 ${ENV_RUN_LOG_PATH} 로 지정하세요.",
            file=sys.stderr,
        )
        return 2
    if not os.path.exists(args.path):
        print(f"run log 파일을 찾을 수 없습니다: {args.path}", file=sys.stderr)
        return 2

    records = load_records(args.path)
    if not records:
        print(f"기록이 없습니다: {args.path}", file=sys.stderr)
        return 1

    summary = summarize(records, expect_peer=args.expect_peer)
    if args.split_dir:
        written = split_by_place(records, args.split_dir)
        place_rows = [
            (place, summarize(load_records(path), expect_peer=args.expect_peer))
            for place, path in written.items()
        ]
        print(render_place_table(place_rows, exclude_exceptions=args.exclude_exceptions))
        for place, path in written.items():
            print(f"- {place}: {path}")
        if args.json:
            print(json.dumps(dict(place_rows), ensure_ascii=False, indent=2))
        return 0
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif args.markdown:
        print(render_markdown(summary, exclude_exceptions=args.exclude_exceptions))
    else:
        print(
            render_markdown(summary, exclude_exceptions=args.exclude_exceptions).replace("|", " ")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
