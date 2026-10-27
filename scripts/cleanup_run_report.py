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


def place_of_run(run: dict) -> str:
    """실행 1회가 향한 장소명을 고른다(단일 방 요청은 target_location)."""
    place = str(run.get("target_location") or "").strip()
    if place:
        return place
    places = [str(item) for item in run.get("places") or [] if str(item).strip()]
    return ",".join(places) if places else "unknown"


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


def render_place_table(rows: list[tuple[str, dict]]) -> str:
    """장소별 집계를 표 2 열 구성으로 출력한다."""
    lines = [
        "| 장소 | 반복 횟수 | 전체 성공률 | 할당 정확도 | 위임 성공률 | 평균 시간 | 재계획 성공률 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for place, summary in rows:
        lines.append(
            f"| {place} | {summary['repeat_count']} | {_fmt_rate(summary['success_rate'])} "
            f"| {_fmt_rate(summary['allocation_accuracy'])} "
            f"| {_fmt_rate(summary['delegation_success_rate'])} "
            f"| {_fmt_ms(summary['mean_elapsed_ms'])} | 해당 없음 |"
        )
    return "\n".join(lines)


def _fmt_rate(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"


def _fmt_ms(value: float) -> str:
    return f"{value / 1000.0:.1f}s"


def render_markdown(summary: dict) -> str:
    lines = [
        "| 실험 조건 | 반복 횟수 | 전체 성공률 | 할당 정확도 | 위임 성공률 | 평균 시간 | 재계획 성공률 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
        (
            f"| 정상 협업 | {summary['repeat_count']} | {_fmt_rate(summary['success_rate'])} "
            f"| {_fmt_rate(summary['allocation_accuracy'])} "
            f"| {_fmt_rate(summary['delegation_success_rate'])} "
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
        print(render_place_table(place_rows))
        for place, path in written.items():
            print(f"- {place}: {path}")
        if args.json:
            print(json.dumps(dict(place_rows), ensure_ascii=False, indent=2))
        return 0
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif args.markdown:
        print(render_markdown(summary))
    else:
        print(render_markdown(summary).replace("|", " "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
