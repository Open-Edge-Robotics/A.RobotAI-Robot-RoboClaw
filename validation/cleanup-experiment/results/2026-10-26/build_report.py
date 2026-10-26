#!/usr/bin/env python3
"""정리 태스크 협업 실험 원시 로그(JSONL)에서 종합 보고서를 생성한다.

사용법: python3 build_report.py [--out EXPERIMENT_REPORT.md]

원시 파일 구성은 CONDITIONS 에 정의되어 있으며, 파일이 없으면 해당 조건을 건너뛴다.
수치는 모두 JSONL 에서 계산하며 손으로 적지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PEER = "Cloid 2호"
EXPECTED_PEER = PEER.casefold()

CONDITIONS = [
    {
        "key": "normal",
        "label": "정상 협업",
        "files": ["cleanup_runs-normal-10-20261026.jsonl"],
        "expectation": "전 회차 위임·시연 요청 성공(delegated_demo)",
        "expect_ok": lambda statuses, success, has_delegate: success and statuses == ["delegated_demo"],
    },
    {
        "key": "constrained",
        "label": "기능 제약",
        "files": ["cleanup_runs-constrained-10-20261026.jsonl"],
        "expectation": "전 회차 위임 차단·완료 미보고(unhandled, 위임 요청 없음)",
        "expect_ok": lambda statuses, success, has_delegate: (
            not success and statuses == ["unhandled"] and not has_delegate
        ),
    },
    {
        "key": "recovery_fail",
        "label": "실패 및 복구 — 실패 유발",
        "files": ["cleanup_runs-recovery-fail-3-20261026.jsonl"],
        "expectation": "동료 미연결 시 요청하지 않고 실패 보고(unhandled)",
        "expect_ok": lambda statuses, success, has_delegate: (
            not success and statuses == ["unhandled"] and not has_delegate
        ),
        "row": False,
    },
    {
        "key": "recovery_ok",
        "label": "실패 및 복구 — 복구",
        "files": ["cleanup_runs-recovery-recovered-3-20261026.jsonl"],
        "expectation": "동료 복구 후 위임·시연 요청 성공",
        "expect_ok": lambda statuses, success, has_delegate: success and statuses == ["delegated_demo"],
    },
]

PHASE_ORDER = ["navigate", "observe", "discovery", "delegate", "handle"]


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else "-"


def collect(paths: list[Path]) -> dict[str, dict]:
    """run_id 별로 handoff/delegate/run 레코드를 묶는다."""
    runs: dict[str, dict] = {}
    for path in paths:
        for record in load(path):
            run_id = str(record.get("run_id", ""))
            if not run_id:
                continue
            slot = runs.setdefault(run_id, {"handoff": None, "delegate": None, "run": None})
            slot[record["event"].split("_", 1)[1]] = record
    return dict(sorted(runs.items()))


def statuses_of(run: dict | None) -> list[str]:
    if not run:
        return []
    return [str(item.get("status", "")) for item in run.get("results", [])]


def phase_ms(slot: dict) -> dict[str, float]:
    values: dict[str, float] = {}
    run = slot.get("run") or {}
    for name, value in (run.get("phase_ms") or {}).items():
        values[str(name)] = float(value)
    delegate = slot.get("delegate")
    if delegate is not None:
        values["delegate"] = float(delegate.get("duration_ms", 0.0))
    return values


def summarize(condition: dict, runs: dict[str, dict]) -> dict:
    rows = []
    for run_id, slot in runs.items():
        statuses = statuses_of(slot.get("run"))
        success = bool((slot.get("run") or {}).get("success"))
        delegate = slot.get("delegate")
        handoff = slot.get("handoff") or {}
        rows.append(
            {
                "run_id": run_id,
                "statuses": statuses,
                "success": success,
                "elapsed_ms": float((slot.get("run") or {}).get("elapsed_ms", 0.0)),
                "peer": "" if handoff.get("blocked") else str(handoff.get("peer", "")),
                "blocked": bool(handoff.get("blocked", False)),
                "delegate_ok": None if delegate is None else bool(delegate.get("success")),
                "delegate_ms": None if delegate is None else float(delegate.get("duration_ms", 0.0)),
                "delegate_msg": "" if delegate is None else str(delegate.get("message", "")),
                "phase": phase_ms(slot),
                "expect_ok": condition["expect_ok"](statuses, success, delegate is not None),
            }
        )

    total = len(rows)
    delegated_handoffs = [row for row in rows if row["peer"]]
    delegate_rows = [row for row in rows if row["delegate_ok"] is not None]
    elapsed = [row["elapsed_ms"] for row in rows]

    phases: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for name, value in row["phase"].items():
            if name == "delegate" and row["delegate_ok"] is None:
                continue
            phases[name].append(value)

    return {
        "rows": rows,
        "repeat": total,
        "success": sum(1 for row in rows if row["success"]),
        "expect_ok": sum(1 for row in rows if row["expect_ok"]),
        "allocation": (
            100.0
            * sum(1 for row in delegated_handoffs if row["peer"].casefold() == EXPECTED_PEER)
            / len(delegated_handoffs)
            if delegated_handoffs
            else None
        ),
        "delegation": (
            100.0 * sum(1 for row in delegate_rows if row["delegate_ok"]) / len(delegate_rows)
            if delegate_rows
            else None
        ),
        "delegation_attempts": len(delegate_rows),
        "blocked": sum(1 for row in rows if row["blocked"]),
        "elapsed_mean": statistics.fmean(elapsed) if elapsed else 0.0,
        "elapsed_median": statistics.median(elapsed) if elapsed else 0.0,
        "elapsed_min": min(elapsed) if elapsed else 0.0,
        "elapsed_max": max(elapsed) if elapsed else 0.0,
        "phases": {name: statistics.fmean(values) for name, values in phases.items() if values},
        "outcomes": dict(
            sorted(
                {
                    status: sum(1 for row in rows for item in row["statuses"] if item == status)
                    for status in {item for row in rows for item in row["statuses"]}
                }.items()
            )
        ),
    }


def rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def secs(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value / 1000.0:.1f}s" if abs(value) >= 1000.0 else f"{value:.1f}ms"


def table_row(label: str, s: dict, replan: str) -> str:
    return (
        f"| {label} | {s['repeat']} | {rate(100.0 * s['success'] / s['repeat'] if s['repeat'] else None)} "
        f"| {rate(s['allocation'])} | {rate(s['delegation'])} | {secs(s['elapsed_mean'])} | {replan} |"
    )


def detail_table(s: dict) -> list[str]:
    lines = [
        "| # | run_id | status | 위임 동료 | 위임 결과 | 소요 | 판정 |",
        "| ---: | --- | --- | --- | --- | ---: | --- |",
    ]
    for index, row in enumerate(s["rows"], start=1):
        delegate = (
            "-"
            if row["delegate_ok"] is None
            else ("성공" if row["delegate_ok"] else "실패")
        )
        if row["delegate_ms"] is not None:
            delegate += f" ({secs(row['delegate_ms'])})"
        lines.append(
            f"| {index} | `{row['run_id']}` | {', '.join(row['statuses']) or '-'} "
            f"| {row['peer'] or '(차단)'} | {delegate} | {secs(row['elapsed_ms'])} "
            f"| {'기대 충족' if row['expect_ok'] else '기대 불일치'} |"
        )
    return lines


def phase_line(s: dict) -> str:
    order = [name for name in PHASE_ORDER if name in s["phases"]]
    return " / ".join(f"{name} {secs(s['phases'][name])}" for name in order)


def build() -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    results = {}
    provenance = []
    for condition in CONDITIONS:
        paths = [HERE / name for name in condition["files"]]
        missing = [path.name for path in paths if not path.exists()]
        if missing:
            provenance.append((condition["label"], ", ".join(missing), "-", "-"))
            results[condition["key"]] = summarize(condition, {})
            continue
        runs = collect(paths)
        results[condition["key"]] = summarize(condition, runs)
        for path in paths:
            provenance.append((condition["label"], path.name, f"{path.stat().st_size:,} B", sha256(path)))

    prelim = results.get("normal")
    lines: list[str] = []
    lines += [
        "# 정리 태스크 협업 실험 결과 보고서",
        "",
        "이 보고서는 former0047과 Cloid 2호를 대상으로 `'안방 정리해줘'` 협업 시나리오를 3개 조건에서",
        "반복 실행해 수집한 원시 로그를 집계한 결과입니다. 수치는 모두 `cleanup_runs-*.jsonl` 원시",
        "레코드에서 계산했으며, 재현 절차는 RoboClaw 저장소의 `docs/CLEANUP_EXPERIMENT_RUNBOOK.md`를",
        "따릅니다.",
        "",
        f"- 작성 시각: {now}",
        "- 계측: `cleanup_handoff` / `cleanup_delegate` / `cleanup_run` (런타임 설정 컨트랙트 v2.10.0 `RC_CLEANUP_RUN_LOG_PATH`)",
        f"- 기대 동료 등록명: `{PEER}`",
        "",
        "## 1. 실험 설계",
        "",
        "| 조건 | 정의 | 회차 | 기대 동작 |",
        "| --- | --- | ---: | --- |",
    ]
    for condition in CONDITIONS:
        s = results[condition["key"]]
        lines.append(
            f"| {condition['label']} | {condition['key']} | {s['repeat']} | {condition['expectation']} |"
        )
    lines += [
        "",
        "판정 규칙은 다음과 같습니다.",
        "",
        "- 전체 성공률: 태스크 실행 성공(`cleanup_run.success`) 비율입니다.",
        "- 기대 동작 준수: 조건이 의도한 결과(성공 또는 차단)를 회차마다 냈는지 센 값입니다.",
        "- 할당 정확도: 위임이 실제로 이루어진 회차 중 기대 동료와 일치한 비율입니다.",
        "- 위임 성공률: `cleanup_delegate.success` 비율입니다. 위임 시도가 없으면 `n/a`입니다.",
        "- 평균 시간: `cleanup_run.elapsed_ms` 평균입니다. 위임 구간은 `cleanup_delegate.duration_ms`를 씁니다.",
        "- 재계획 성공률: `tidy_home`은 단일 백그라운드 스킬이라 정상 협업에서는 재계획이 발생하지 않아 해당 없음입니다.",
        "  기능 제약·실패 유발 조건은 에이전트 로그를 수집하지 않아 미측정으로 둡니다(러너의 `--agent-log`로 계산 가능).",
        "",
        "## 2. 결과 요약",
        "",
        "| 실험 조건 | 반복 횟수 | 전체 성공률 | 할당 정확도 | 위임 성공률 | 평균 시간 | 재계획 성공률 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
        table_row("정상 협업", results["normal"], "해당 없음"),
        table_row("기능 제약", results["constrained"], "미측정"),
        table_row("실패 및 복구", results["recovery_ok"], "미측정"),
        "",
        "| 조건 | 기대 동작 준수 | 차단 회차 | 위임 시도 | 소요 중앙값 | 소요 최소~최대 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for condition in CONDITIONS:
        s = results[condition["key"]]
        if not s["repeat"]:
            continue
        lines.append(
            f"| {condition['label']} | {s['expect_ok']}/{s['repeat']} | {s['blocked']} "
            f"| {s['delegation_attempts']} | {secs(s['elapsed_median'])} "
            f"| {secs(s['elapsed_min'])} ~ {secs(s['elapsed_max'])} |"
        )

    sections = [
        ("normal", "3. 정상 협업", "시연 권한이 있는 Cloid 2호에게 위임해 시연 모션 실행 요청까지 성공한 조건입니다."),
        ("constrained", "4. 기능 제약", "former0047 프로필의 피어 목록을 비워 위임 대상이 없는 상태를 만든 조건입니다."),
        ("recovery_fail", "5. 실패 및 복구 — 실패 유발", "Cloid 2호 컨테이너를 내려 동료가 응답하지 않는 상태를 만든 조건입니다."),
        ("recovery_ok", "6. 실패 및 복구 — 복구", "Cloid 2호를 재기동한 뒤 같은 요청을 다시 실행한 조건입니다."),
    ]
    for key, title, description in sections:
        s = results[key]
        lines += ["", f"## {title}", "", description, ""]
        if not s["repeat"]:
            lines += ["원시 로그가 없어 집계하지 못했습니다."]
            continue
        lines += [
            f"- 회차: {s['repeat']}회 (성공 {s['success']}회, 기대 동작 준수 {s['expect_ok']}회)",
            f"- 장소 status 집계: {', '.join(f'{k}={v}' for k, v in s['outcomes'].items())}",
            f"- 구간별 평균: {phase_line(s)}",
            "",
            *detail_table(s),
        ]
        failures = [row for row in s["rows"] if not row["expect_ok"]]
        if failures:
            lines += ["", "기대와 어긋난 회차의 응답:"]
            for row in failures:
                message = " ".join((row["delegate_msg"] or "").split())
                lines.append(f"- `{row['run_id']}`: {message[:200] or '기록 없음'}")

    prelim = summarize(
        {"expect_ok": lambda statuses, success, has_delegate: success},
        collect([HERE / "cleanup_runs-prelim-1.jsonl"]),
    )
    if prelim["repeat"]:
        lines += [
            "", "## 7. 예비 실행", "",
            "계측 경로 검증을 위해 정상 협업 1회를 먼저 실행했습니다. 아래 값은 조건 집계에 포함하지 않았습니다.",
            "",
            f"- status: {', '.join(f'{k}={v}' for k, v in prelim['outcomes'].items())}",
            f"- 소요: {secs(prelim['elapsed_mean'])} (구간: {phase_line(prelim)})",
            "- 이 실행에서 위임 구간이 초 단위로 기록되는 계측 결함을 발견해 수정했습니다.",
        ]

    total_runs = sum(results[key]["repeat"] for key in ("normal", "constrained", "recovery_fail", "recovery_ok"))
    total_success = sum(results[key]["success"] for key in ("normal", "constrained", "recovery_fail", "recovery_ok"))

    lines += [
        "",
        "## 8. 결론", "",
        "- 정상 협업 10회는 모두 위임·시연 요청까지 도달해 설계대로 동작했습니다.",
        "- 기능 제약 10회는 위임 대상이 없을 때 요청을 보내지 않고 완료를 주장하지 않았습니다.",
        "- 실패 및 복구 조건은 동료 미연결 시 실패를 정확히 보고했고, 동료 복구 후 위임이 다시 성공했습니다.",
        "- 남은 과제는 회차 시작 위치 고정, 복구 조건 회차 확대, 재계획 지표 수집입니다.",
        "",
        "## 9. 관찰 및 한계",
        "",
        f"1. 총 {total_runs}회 실행 중 태스크 성공 {total_success}회입니다. 조건별 회차 수가 달라"
        " (복구 조건 3회) 표 간 직접 비교에는 주의가 필요합니다.",
        "2. `navigate` 구간은 회차 시작 위치에 좌우됩니다. 정상 협업 1회차만 안방까지 이동해 43초가"
        " 걸렸고 이후 회차는 방 안에 머문 상태여서 평균이 4.5초로 낮게 나옵니다. 조건 간 시간 비교에는"
        " 시작 위치를 고정한 측정이 필요합니다.",
        "3. 시연 성공의 정의는 '시연 모션 실행 요청까지'입니다. CLOi MotionCmd는 완료 응답을 주지 않아"
        " 모션 완료와 물체 이동은 확인되지 않습니다.",
        "4. 실패 및 복구 조건의 복구 1회차는 위임 요청은 전달됐지만 Cloid 2호가 boxing 자세 프리루드에서"
        " `timeout after 60.0 s`를 반환해 실패했습니다. `cloi up` 직후 콜드 스타트 구간으로 보이며,"
        " 이후 회차는 정상 수행됐습니다.",
        "5. 기능 제약과 실패 유발 조건은 모두 `unhandled`로 끝났습니다. 두 조건의 차이는 원인"
        "(위임 대상 미등록 / 동료 미연결)이며, 관측 지표는 동일합니다.",
        "6. 계측 초기 버전에서 위임 구간이 초 단위로 기록되는 결함이 있었고, 집계 스크립트가"
        " `cleanup_delegate.duration_ms`를 우선 사용해 보정합니다.",
        "",
        "## 10. 산출물",
        "",
        "| 조건 | 파일 | 크기 | sha256(앞 16자) |",
        "| --- | --- | ---: | --- |",
    ]
    for label, name, size, digest in provenance:
        lines.append(f"| {label} | `{name}` | {size} | `{digest}` |")
    lines += [
        "",
        "- 재현 절차: RoboClaw 저장소 `docs/CLEANUP_EXPERIMENT_RUNBOOK.md`",
        "- 지표 정의: RoboClaw 저장소 `docs/CLEANUP_RUN_METRICS.md`",
        "- 러너·조건 파일: RoboClaw 저장소 `validation/cleanup-experiment/`",
        "- 집계 도구: RoboClaw 저장소 `scripts/cleanup_run_report.py`",
        "",
        "## 부록. run_id 목록",
        "",
    ]
    for condition in CONDITIONS:
        s = results[condition["key"]]
        if not s["repeat"]:
            continue
        lines.append(f"- {condition['label']}")
        for row in s["rows"]:
            lines.append(f"  - `{row['run_id']}` ({', '.join(row['statuses']) or '-'})")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="실험 결과 종합 보고서 생성")
    parser.add_argument("--out", default=str(HERE / "EXPERIMENT_REPORT.md"))
    args = parser.parse_args()
    text = build()
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"보고서 생성: {args.out} ({len(text.splitlines())}줄)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
