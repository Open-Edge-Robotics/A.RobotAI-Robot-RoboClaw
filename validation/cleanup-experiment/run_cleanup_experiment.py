#!/usr/bin/env python3
"""정리 태스크 3조건 실험 러너 (정상 협업·기능 제약·실패 및 복구).

조건 파일(`conditions/*.json`)에 정의된 프롬프트를 N회 반복 전송하고, 매 회차마다
- gRPC 응답(성공 플래그·메시지·`result_json`),
- 에이전트 run log(JSONL)의 `cleanup_handoff`·`cleanup_delegate`·`cleanup_run` 레코드,
- (선택) 에이전트 로그의 재계획 발생 횟수
를 모아 판정하고 표 2 형식 리포트를 만든다.

run log 경로는 에이전트가 실행 중인 호스트의 파일이어야 한다. 컨테이너 안에서 돌고
있다면 마운트된 경로를 지정한다(예: `--run-log /workspace/log/cleanup_runs.jsonl`).

사용 예::

    # 실행 계획만 확인(로봇에 명령을 보내지 않음)
    python3 validation/cleanup-experiment/run_cleanup_experiment.py --dry-run

    # 정상 협업 10회
    python3 validation/cleanup-experiment/run_cleanup_experiment.py \\
        --condition normal --host 10.159.172.69 \\
        --run-log /workspace/log/cleanup_runs.jsonl \\
        --agent-log ~/.ros/log/latest/agent.log \\
        --output validation/cleanup-experiment/report-normal.md

    # 실패 유발 단계만
    python3 validation/cleanup-experiment/run_cleanup_experiment.py \\
        --condition recovery --phase fail_injected ...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CONDITIONS_DIR = _HERE / "conditions"
_TALK_DIR = _HERE.parent / "talk"
DEFAULT_PORT = 50052
DEFAULT_RUN_LOG = os.environ.get("RC_CLEANUP_RUN_LOG_PATH", "")
REPLAN_PATTERN = "attempting replanning"
REPLAN_EXHAUSTED_PATTERN = "Exceeded max replanning rounds"


# --------------------------------------------------------------------- 조건 정의


def load_conditions(directory: Path) -> dict[str, dict]:
    """조건 파일을 모두 읽어 condition 키로 정렬된 dict 로 돌려준다."""
    conditions: dict[str, dict] = {}
    for path in sorted(Path(directory).glob("*.json")):
        with open(path, encoding="utf-8") as handle:
            condition = json.load(handle)
        key = str(condition.get("condition") or path.stem)
        phases = condition.get("phases") or []
        if not phases:
            raise ValueError(f"조건 파일에 phases 가 없습니다: {path}")
        for phase in phases:
            if not phase.get("prompt"):
                raise ValueError(f"phase 에 prompt 가 없습니다: {path} {phase.get('key')}")
            phase.setdefault("key", "main")
            phase.setdefault("repeat", condition.get("repeat", 1))
            phase.setdefault("row", True)
            phase.setdefault("expect", {})
        condition["phases"] = phases
        condition["source"] = str(path)
        conditions[key] = condition
    return conditions


def apply_peer_override(condition: dict, peer: str) -> None:
    """기대 동료 이름을 덮어쓴다.

    동료 이름은 배포마다 다르다(예: AI Config Server 의 `agent_name` 이 `Cloid 2호`).
    조건 파일에 박힌 이름 대신 실제 등록명을 쓰려면 이 함수로 바꾼다.
    """
    if not peer:
        return
    for phase in condition.get("phases") or []:
        expect = phase.get("expect")
        if isinstance(expect, dict) and expect.get("peer"):
            expect["peer"] = peer


# --------------------------------------------------------------------- 응답 해석


def extract_run_id(result_json: str) -> str:
    """gRPC `result_json` 에서 tidy_home 실행 run_id 를 꺼낸다(없으면 빈 문자열)."""
    if not result_json:
        return ""
    try:
        payload = json.loads(result_json)
    except (TypeError, ValueError):
        return ""
    steps = payload.get("skill_results") if isinstance(payload, dict) else None
    for step in steps or []:
        data = step.get("result_data") if isinstance(step, dict) else None
        if isinstance(data, dict) and data.get("run_id"):
            return str(data["run_id"])
    return ""


def load_records(path: str) -> list[dict]:
    """JSONL run log 를 전부 읽는다(파일이 없으면 빈 목록)."""
    if not path or not os.path.exists(path):
        return []
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


def collect_run_evidence(
    path: str,
    run_id: str,
    timeout_sec: float,
    *,
    poll_sec: float = 1.0,
    start_index: int = 0,
    sleep=time.sleep,
) -> dict:
    """run_id 에 해당하는 레코드가 `cleanup_run` 까지 모일 때까지 기다린다.

    ``start_index`` 는 이번 회차 전에 이미 존재하던 레코드 수다. 같은 run log 를 재사용하는
    재실행에서 이전 회차 레코드가 섞이지 않게 하려고 이번 회차 이후 레코드만 본다.
    """
    evidence: dict = {"run": None, "handoffs": [], "delegates": []}
    if not run_id:
        return evidence
    deadline = time.monotonic() + max(0.0, timeout_sec)
    while True:
        for record in load_records(path)[start_index:]:
            if str(record.get("run_id", "")) != run_id:
                continue
            event = record.get("event")
            if event == "cleanup_handoff":
                evidence["handoffs"].append(record)
            elif event == "cleanup_delegate":
                evidence["delegates"].append(record)
            elif event == "cleanup_run":
                evidence["run"] = record
        if evidence["run"] is not None or time.monotonic() >= deadline:
            return evidence
        sleep(poll_sec)


def count_replan_events(text: str) -> dict[str, int]:
    """에이전트 로그 조각에서 재계획 시도·소진 횟수를 센다."""
    return {
        "attempts": text.count(REPLAN_PATTERN),
        "exhausted": text.count(REPLAN_EXHAUSTED_PATTERN),
    }


# --------------------------------------------------------------------- 회차 판정


def _place_statuses(run: dict | None) -> list[str]:
    if not run:
        return []
    return [str(result.get("status", "")) for result in run.get("results") or []]


def _delegate_successes(evidence: dict) -> list[bool]:
    return [bool(record.get("success")) for record in evidence.get("delegates") or []]


def _handoff_peer(evidence: dict) -> str:
    for record in evidence.get("handoffs") or []:
        if not record.get("blocked"):
            return str(record.get("peer", "") or "")
    return ""


def evaluate_attempt(expect: dict, response: dict, evidence: dict) -> list[str]:
    """회차 1건의 판정 사유 목록을 돌려준다(빈 목록이면 기대 충족)."""
    reasons: list[str] = []
    message = str(response.get("message", ""))
    run = evidence.get("run")

    for keyword in expect.get("expected_keywords") or []:
        if keyword not in message:
            reasons.append(f"필수 키워드 누락: '{keyword}'")
    for keyword in expect.get("fail_keywords") or []:
        if keyword in message:
            reasons.append(f"금지 키워드 포함: '{keyword}'")

    if expect.get("run_present") is not None:
        if bool(expect["run_present"]) and run is None:
            reasons.append("정리 실행 기록(cleanup_run)이 없습니다")
        if not bool(expect["run_present"]) and run is not None:
            reasons.append("정리 실행 기록이 남았습니다")

    if run is not None and expect.get("run_success") is not None:
        if bool(run.get("success")) is not bool(expect["run_success"]):
            reasons.append(f"실행 성공 판정 불일치(기대 {expect['run_success']})")

    allowed = expect.get("place_status") or []
    if allowed and run is not None:
        unexpected = [status for status in _place_statuses(run) if status not in allowed]
        if unexpected:
            reasons.append(f"장소 status 불일치: {', '.join(unexpected)} (기대 {allowed})")

    peer = str(expect.get("peer", "") or "")
    if peer and _handoff_peer(evidence).casefold() != peer.casefold():
        actual = _handoff_peer(evidence) or "없음"
        reasons.append(f"위임 동료 불일치(기대 {peer}, 실제 {actual})")

    if expect.get("peer_absent") and _handoff_peer(evidence):
        reasons.append(f"위임이 차단되지 않았습니다(선택 {_handoff_peer(evidence)})")

    if expect.get("delegate_success") is not None:
        successes = _delegate_successes(evidence)
        if successes and any(successes) is not bool(expect["delegate_success"]):
            reasons.append(f"위임 실행 결과 불일치(기대 성공={expect['delegate_success']})")

    return reasons


# --------------------------------------------------------------------- 결과 집계


def _rate(numerator: int, denominator: int) -> float:
    return (numerator / denominator * 100.0) if denominator else 0.0


def build_condition_row(attempts: list[dict], expect_peer: str = "") -> dict:
    """회차 목록을 표 2 한 행의 지표로 집계한다."""
    count = len(attempts)
    passed = sum(1 for attempt in attempts if attempt["ok"])
    peers = [attempt["peer"] for attempt in attempts if attempt.get("peer")]
    delegate = [flag for attempt in attempts for flag in attempt.get("delegate_successes") or []]
    elapsed = [
        attempt["run_elapsed_ms"]
        for attempt in attempts
        if attempt.get("run_elapsed_ms") is not None
    ]
    phase_totals: dict[str, float] = {}
    for attempt in attempts:
        for name, value in (attempt.get("phase_ms") or {}).items():
            phase_totals[name] = phase_totals.get(name, 0.0) + float(value)

    replanned = [attempt for attempt in attempts if attempt.get("replan_attempts", 0) > 0]
    has_replan_data = any(attempt.get("replan_tracked") for attempt in attempts)
    task_success = sum(1 for attempt in attempts if attempt.get("run_success") is True)

    return {
        "repeat": count,
        "run_record_count": sum(1 for attempt in attempts if attempt.get("run_present")),
        "verdict_pass": passed,
        # 표 2 의 전체 성공률은 태스크 성공 비율이다. 조건이 기대한 실패(기능 제약)를
        # 올바르게 처리한 경우와 구분해야 하므로 판정 통과율은 별도 지표로 둔다.
        "success_rate": _rate(task_success, count),
        "task_success_count": task_success,
        "verdict_pass_rate": _rate(passed, count),
        "allocation_accuracy": (
            _rate(
                sum(1 for peer in peers if peer.casefold() == expect_peer.casefold()),
                len(peers),
            )
            if expect_peer
            else None
        ),
        "delegation_success_rate": _rate(sum(delegate), len(delegate)) if delegate else None,
        "delegation_attempts": len(delegate),
        "mean_elapsed_ms": (sum(elapsed) / len(elapsed)) if elapsed else 0.0,
        "mean_phase_ms": (
            {
                name: (value / count if count else 0.0)
                for name, value in sorted(phase_totals.items())
            }
        ),
        "replan_success_rate": (
            _rate(sum(1 for attempt in replanned if attempt["ok"]), len(replanned))
            if has_replan_data
            else None
        ),
        "replan_attempts": len(replanned),
    }


def _fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _fmt_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value / 1000.0:.1f}s"


def render_report(condition: dict, attempts: list[dict], row: dict) -> str:
    """조건 1건의 마크다운 리포트(요약 표 + 회차 상세)를 만든다."""
    label = str(condition.get("label") or condition.get("condition"))
    expect_peer = ""
    for phase in condition.get("phases") or []:
        if phase.get("row") and phase.get("expect", {}).get("peer"):
            expect_peer = str(phase["expect"]["peer"])
            break

    lines = [
        f"# 정리 태스크 실험 결과 — {label}",
        "",
        f"- 조건 키: `{condition.get('condition')}`",
        f"- 대상: {condition.get('target', '-')}",
        f"- 측정 시각: {condition.get('measured_at', '-')}",
        f"- 실행 기록: {row['run_record_count']}건 / 시도 {row['repeat']}회",
        "",
        "## 표 2 행",
        "",
        "| 실험 조건 | 반복 횟수 | 전체 성공률 | 할당 정확도 | 위임 성공률 | 평균 시간 | 재계획 성공률 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
        (
            f"| {label} | {row['repeat']} | {_fmt_rate(row['success_rate'])} "
            f"| {_fmt_rate(row['allocation_accuracy'])} "
            f"| {_fmt_rate(row['delegation_success_rate'])} "
            f"| {_fmt_ms(row['mean_elapsed_ms'])} "
            f"| {_fmt_rate(row['replan_success_rate'])} |"
        ),
        "",
        f"기대 동작 준수: {row['verdict_pass']}/{row['repeat']}회 "
        f"({_fmt_rate(row['verdict_pass_rate'])})",
        "",
        "판정 기준: 전체 성공률은 태스크 실행 성공(`cleanup_run.success`) 비율입니다. 조건이",
        "기대한 실패(기능 제약)를 올바르게 처리한 경우는 '기대 동작 준수'로 따로 셉니다.",
        "할당 정확도는 위임이 실제로 이루어진 회차 중 기대 동료와 일치한 비율이고(기대 동료",
        "미지정 시 n/a), 위임 성공률은 `cleanup_delegate` 레코드의 성공 비율입니다. 재계획",
        "성공률은 `--agent-log` 를 준 경우에만 계산합니다.",
        "",
        "## 회차 상세",
        "",
        "| # | 단계 | 응답 | 응답 시간 | run_id | 장소 status | 위임 동료 | 판정 |",
        "| ---: | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for index, attempt in enumerate(attempts, start=1):
        statuses = ", ".join(attempt.get("statuses") or []) or "-"
        verdict = "PASS" if attempt["ok"] else "FAIL: " + "; ".join(attempt["reasons"])
        lines.append(
            f"| {index} | {attempt.get('phase_key', '-')} "
            f"| {_short(attempt.get('message', ''))} "
            f"| {_fmt_ms(attempt.get('latency_ms'))} "
            f"| {attempt.get('run_id') or '-'} | {statuses} "
            f"| {attempt.get('peer') or '-'} | {verdict} |"
        )

    if row["mean_phase_ms"]:
        lines += ["", "## 구간별 평균", ""]
        for name, value in row["mean_phase_ms"].items():
            lines.append(f"- {name}: {_fmt_ms(value)}")

    if expect_peer:
        lines += ["", f"기대 동료: `{expect_peer}`"]
    return "\n".join(lines) + "\n"


def _short(text: str, limit: int = 40) -> str:
    one_line = " ".join(str(text or "").split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


# --------------------------------------------------------------------- 실행


class LogTail:
    """에이전트 로그 파일에서 이번 회차에 새로 추가된 부분만 읽는다."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._offset = 0
        if path and os.path.exists(path):
            self._offset = os.path.getsize(path)

    def read_new(self) -> str:
        if not self.path or not os.path.exists(self.path):
            return ""
        size = os.path.getsize(self.path)
        if size < self._offset:  # 로그 로테이션
            self._offset = 0
        with open(self.path, encoding="utf-8", errors="replace") as handle:
            handle.seek(self._offset)
            chunk = handle.read()
        self._offset = size
        return chunk


def send_via_robotalk(host: str, port: int, sender_id: str):
    """`validation/talk/robo_talk.py` 의 RoboTalk 을 재사용한다(생성된 proto 필요)."""
    sys.path.insert(0, str(_TALK_DIR))
    import robo_talk  # noqa: PLC0415

    talk = robo_talk.RoboTalk(host, port, sender_id=sender_id)
    if not talk.wait_ready(5.0):
        raise RuntimeError(f"{talk.target} 연결 실패 — 에이전트 기동과 포트를 확인하세요")

    def _send(prompt: str, timeout_sec: float) -> dict:
        started = time.monotonic()
        success, message, result_json = talk.send(prompt, timeout_sec=timeout_sec)
        return {
            "success": bool(success),
            "message": str(message or ""),
            "result_json": str(result_json or ""),
            "latency_ms": (time.monotonic() - started) * 1000.0,
        }

    _send.close = talk.close  # type: ignore[attr-defined]
    return _send


def run_attempt(
    *,
    phase: dict,
    send,
    run_log: str,
    tail: LogTail,
    timeout_sec: float,
    poll_sec: float = 1.0,
    sleep=time.sleep,
) -> dict:
    """프롬프트 1회 전송 → 응답·run log·재계획 증거를 모아 판정한다."""
    tail.read_new()  # 이전 회차 잔여분 버리기
    start_index = len(load_records(run_log))  # 이전 회차 레코드와 섞이지 않게 기준점을 잡는다
    try:
        response = send(str(phase["prompt"]), timeout_sec)
    except Exception as exc:  # noqa: BLE001
        return {
            "phase_key": phase.get("key"),
            "ok": False,
            "reasons": [f"전송 실패: {exc}"],
            "run_success": None,
            "message": "",
            "latency_ms": None,
            "run_id": "",
            "statuses": [],
            "peer": "",
            "run_present": False,
            "run_elapsed_ms": None,
            "phase_ms": {},
            "delegate_successes": [],
            "replan_attempts": 0,
            "replan_tracked": bool(tail.path),
        }

    run_id = extract_run_id(response["result_json"])
    evidence = collect_run_evidence(
        run_log, run_id, timeout_sec, poll_sec=poll_sec, start_index=start_index, sleep=sleep
    )
    replan_text = tail.read_new()
    replan = count_replan_events(replan_text)
    run = evidence["run"]
    reasons = evaluate_attempt(phase.get("expect") or {}, response, evidence)

    return {
        "phase_key": phase.get("key"),
        "ok": not reasons,
        "reasons": reasons,
        "message": response["message"],
        "latency_ms": response["latency_ms"],
        "run_id": run_id,
        "statuses": _place_statuses(run),
        "peer": _handoff_peer(evidence),
        "run_present": run is not None,
        "run_success": bool(run.get("success")) if isinstance(run, dict) else None,
        "run_elapsed_ms": (float(run.get("elapsed_ms", 0.0)) if isinstance(run, dict) else None),
        "phase_ms": (run.get("phase_ms") or {}) if isinstance(run, dict) else {},
        "delegate_successes": _delegate_successes(evidence),
        "replan_attempts": replan["attempts"] + replan["exhausted"],
        "replan_tracked": bool(tail.path),
    }


def _print_setup(condition: dict) -> None:
    label = condition.get("label") or condition.get("condition")
    print(f"\n=== {label} ({condition.get('condition')}) ===")
    print(condition.get("description", ""))
    print("\n사전 준비:")
    for step in (condition.get("setup") or {}).get("operator_steps") or []:
        print(f"  - {step}")
    required = (condition.get("setup") or {}).get("required_env") or {}
    for robot, env in required.items():
        print(f"  * {robot}: " + ", ".join(f"{key}={value}" for key, value in env.items()))
    print("\n단계:")
    for phase in condition["phases"]:
        marker = "표 2 행" if phase.get("row") else "근거 단계"
        print(f"  - [{phase['key']}] '{phase['prompt']}' × {phase['repeat']}회 ({marker})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="정리 태스크 3조건 실험 러너")
    parser.add_argument("--condition", action="append", default=[], help="조건 키(반복 지정 가능)")
    parser.add_argument("--all", action="store_true", help="모든 조건 실행")
    parser.add_argument("--phase", default="", help="특정 phase 키만 실행")
    parser.add_argument("--conditions-dir", default=str(_CONDITIONS_DIR))
    parser.add_argument("--host", default="", help="에이전트 gRPC 호스트")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--sender-id", default="cleanup_experiment")
    parser.add_argument("--run-log", default=DEFAULT_RUN_LOG, help="에이전트 run log JSONL 경로")
    parser.add_argument("--agent-log", default="", help="재계획 횟수 산출용 에이전트 로그")
    parser.add_argument(
        "--expect-peer",
        default="",
        help="기대 동료 이름을 덮어쓴다(예: 'Cloid 2호'). 조건 파일의 expect.peer 보다 우선한다",
    )
    parser.add_argument("--output", default="", help="리포트 마크다운 경로")
    parser.add_argument("--repeat", type=int, default=0, help="조건 파일의 반복 횟수를 덮어쓴다")
    parser.add_argument("--settle-sec", type=float, default=None, help="회차 사이 대기 초")
    parser.add_argument(
        "--dry-run", action="store_true", help="계획만 출력하고 명령을 보내지 않는다"
    )
    parser.add_argument("--yes", action="store_true", help="사전 준비 확인 프롬프트를 생략한다")
    args = parser.parse_args(argv)

    conditions = load_conditions(Path(args.conditions_dir))
    selected = list(conditions) if args.all else args.condition
    if not selected:
        selected = ["normal"]
    unknown = [key for key in selected if key not in conditions]
    if unknown:
        print(
            f"알 수 없는 조건: {', '.join(unknown)} (사용 가능: {', '.join(conditions)})",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        for key in selected:
            _print_setup(conditions[key])
        print("\n[dry-run] 계획만 출력했습니다. 로봇에 명령을 보내지 않았습니다.")
        return 0

    if not args.host:
        print("--host 가 필요합니다.", file=sys.stderr)
        return 2
    if not args.run_log:
        print(
            "--run-log 또는 RC_CLEANUP_RUN_LOG_PATH 가 필요합니다(에이전트가 기록하는 경로).",
            file=sys.stderr,
        )
        return 2

    send = send_via_robotalk(args.host, args.port, args.sender_id)
    tail = LogTail(args.agent_log)
    reports: list[str] = []
    exit_code = 0
    try:
        for key in selected:
            condition = conditions[key]
            condition["target"] = f"{args.host}:{args.port}"
            apply_peer_override(condition, args.expect_peer)
            _print_setup(condition)
            if not args.yes and sys.stdin.isatty():
                input("사전 준비를 마쳤으면 Enter (중단: Ctrl+C) ")
            attempts: list[dict] = []
            for phase in condition["phases"]:
                if args.phase and phase["key"] != args.phase:
                    continue
                repeat = args.repeat or int(phase["repeat"])
                for index in range(1, repeat + 1):
                    print(f"  💬 [{phase['key']}] {index}/{repeat} …", end="", flush=True)
                    attempt = run_attempt(
                        phase=phase,
                        send=send,
                        run_log=args.run_log,
                        tail=tail,
                        timeout_sec=float(condition.get("run_timeout_sec", 300)),
                    )
                    attempts.append(attempt)
                    mark = "✅" if attempt["ok"] else "❌"
                    print(f" {mark} run_id={attempt['run_id'] or '-'}")
                    if not attempt["ok"]:
                        for reason in attempt["reasons"]:
                            print(f"      - {reason}")
                    settle = (
                        args.settle_sec
                        if args.settle_sec is not None
                        else float(condition.get("settle_sec", 10))
                    )
                    if index < repeat and settle > 0:
                        time.sleep(settle)
            if not attempts:
                continue
            row_attempts = [
                attempt
                for attempt in attempts
                if next(
                    phase for phase in condition["phases"] if phase["key"] == attempt["phase_key"]
                ).get("row")
            ] or attempts
            expect_peer = ""
            for phase in condition["phases"]:
                if phase.get("row") and phase.get("expect", {}).get("peer"):
                    expect_peer = str(phase["expect"]["peer"])
            row = build_condition_row(row_attempts, expect_peer=expect_peer)
            condition["measured_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            reports.append(render_report(condition, attempts, row))
            print(
                f"  → 표 2 행: 반복 {row['repeat']}회, 전체 성공률 {_fmt_rate(row['success_rate'])}, "
                f"할당 정확도 {_fmt_rate(row['allocation_accuracy'])}, "
                f"위임 성공률 {_fmt_rate(row['delegation_success_rate'])}, "
                f"평균 {_fmt_ms(row['mean_elapsed_ms'])}, "
                f"재계획 {_fmt_rate(row['replan_success_rate'])}, "
                f"기대 동작 준수 {row['verdict_pass']}/{row['repeat']}"
            )
            if row["repeat"] and row["verdict_pass"] < row["repeat"]:
                exit_code = 1
    finally:
        close = getattr(send, "close", None)
        if callable(close):
            close()

    if args.output and reports:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write("\n\n---\n\n".join(reports))
        print(f"\n리포트: {args.output}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
