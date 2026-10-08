#!/usr/bin/env python3
"""System 1 사전 라우터 평가 (설계 Step 3 PoC / Step 6 임계값 조정용).

같은 케이스 셋으로 RuleRouter 와 Laya(SystemOneRouter)를 각각 돌려 비교한다.
ROS 2 없이 실행된다.

    # 규칙 라우터만 (Laya 서버 불필요)
    python3 scripts/system1_eval.py --router rule

    # Laya (laya-serve 실행 필요)
    python3 scripts/system1_eval.py --router laya --endpoint http://localhost:8000

    # 둘 다 비교 + navigation scope + 결과 저장
    python3 scripts/system1_eval.py --router both --endpoint http://localhost:8000 \\
        --scope navigation --json-out /tmp/system1_eval.json

    # 인증을 켠 서버(LAYA_API_KEY). 키는 .env 에서 읽고 화면에 출력하지 않는다.
    python3 scripts/system1_eval.py --router laya --endpoint http://192.168.50.212:8000 --env-file .env

API 키 우선순위: ``--api-key`` > 환경변수 ``SYSTEM1_API_KEY`` > ``LAYA_API_KEY`` >
``--env-file`` 의 ``SYSTEM1_API_KEY`` > ``LAYA_API_KEY``.
endpoint 우선순위: ``--endpoint`` > ``SYSTEM1_ENDPOINT`` > ``LAYA_ENDPOINT`` > ``--env-file`` 의 같은 키.

판정 기준(케이스의 ``expected`` 와 비교):

* correct   : kind/skill/params 가 기대와 일치
* escalated : 기대는 direct/simple 인데 라우터가 System 2(llm)로 넘김 → 안전한 놓침
* wrong     : 그 외(기대와 다른 스킬 실행, llm 이어야 하는데 직접 처리 등) → **오실행**

운영 판단에서는 wrong(오실행)을 0에 가깝게 유지하는 것이 우선이다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AGENT_SRC = ROOT / "src" / "robo_claw_agent"
DEFAULT_CASES = ROOT / "validation" / "system1" / "router_cases.jsonl"
DEFAULT_CONTEXT = ROOT / "validation" / "system1" / "router_eval_context.json"


API_KEY_NAMES = ("SYSTEM1_API_KEY", "LAYA_API_KEY")
ENDPOINT_NAMES = ("SYSTEM1_ENDPOINT", "LAYA_ENDPOINT")


def read_env_file(path: Path | None) -> dict[str, str]:
    """dotenv 형식(KEY=VALUE) 파일을 읽는다. 없는 파일이면 빈 dict. 값은 출력하지 않는다."""
    if path is None or not Path(path).is_file():
        return {}
    values: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value
    return values


def resolve_setting(
    explicit: str | None, names: tuple[str, ...], env: Any, env_file: dict[str, str]
) -> tuple[str, str]:
    """(값, 출처)를 돌려준다. 출처는 로그용이며 값 자체는 출력하지 않는다."""
    if explicit:
        return explicit, "argument"
    for name in names:
        if env.get(name):
            return env[name], f"env {name}"
    for name in names:
        if env_file.get(name):
            return env_file[name], f"env-file {name}"
    return "", ""


def _import_routers():
    """robo_claw_agent.agent_node 의 __init__(rclpy 의존)을 우회해 라우터 모듈만 로드한다."""
    sys.path.insert(0, str(AGENT_SRC))
    if "robo_claw_agent.agent_node" not in sys.modules:
        import robo_claw_agent  # noqa: F401  (상위 패키지는 ROS 의존 없음)

        pkg = types.ModuleType("robo_claw_agent.agent_node")
        pkg.__path__ = [str(AGENT_SRC / "robo_claw_agent" / "agent_node")]
        sys.modules["robo_claw_agent.agent_node"] = pkg
    from robo_claw_agent.agent_node import fast_router, system1_router

    return fast_router, system1_router


class _Skills:
    def __init__(self, skills: list[dict[str, Any]]):
        self._skills = skills

    def list_skills(self, include_internal: bool = True) -> list[dict[str, Any]]:
        return list(self._skills)


class _Memory:
    def __init__(self, places: list[str]):
        self._places = places

    def get_all_objects(self) -> list[dict[str, Any]]:
        return [{"name": p, "metadata": {}} for p in self._places]


class _Node:
    def __init__(self, context: dict[str, Any]):
        self._skills = _Skills(context.get("skills", []))
        self._memory = _Memory(context.get("places", []))
        self._logger = logging.getLogger("system1_eval")

    def get_logger(self):
        return self._logger


def _load_cases(path: Path) -> list[dict[str, Any]]:
    cases = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc
    return cases


def _expected(case: dict[str, Any], scope: str) -> dict[str, Any]:
    if scope == "navigation" and "expected_navigation" in case:
        return case["expected_navigation"]
    return case["expected"]


def _judge(expected: dict[str, Any], decision: Any) -> str:
    kind = decision.kind
    if kind == expected.get("kind"):
        if kind != "direct_skill":
            return "correct"
        if decision.skill == expected.get("skill") and dict(decision.params) == dict(
            expected.get("params", {})
        ):
            return "correct"
        return "wrong"
    if kind == "llm":
        return "escalated"
    return "wrong"


async def _run_router(router: Any, node: _Node, cases: list[dict], scope: str) -> dict[str, Any]:
    rows = []
    latencies = []
    for case in cases:
        expected = _expected(case, scope)
        error = None
        try:
            decision = await router.decide(node, case["instruction"])
            verdict = _judge(expected, decision)
            latencies.append(decision.latency_ms)
            got = {
                "kind": decision.kind,
                "skill": decision.skill,
                "params": decision.params,
                "intent": decision.intent,
                "confidence": decision.confidence,
                "source": decision.source,
                "fallback": decision.fallback,
            }
        except Exception as exc:  # noqa: BLE001
            verdict, got, error = "error", {}, f"{type(exc).__name__}: {exc}"
        rows.append(
            {
                "id": case.get("id"),
                "instruction": case["instruction"],
                "expected": expected,
                "got": got,
                "verdict": verdict,
                "error": error,
            }
        )
    counts = {
        v: sum(1 for r in rows if r["verdict"] == v)
        for v in ("correct", "escalated", "wrong", "error")
    }
    total = len(rows) or 1
    summary = {
        "router": getattr(router, "name", "?"),
        "scope": scope,
        "total": len(rows),
        **counts,
        "accuracy": round(counts["correct"] / total, 3),
        "false_act_rate": round(counts["wrong"] / total, 3),
    }
    if latencies:
        ordered = sorted(latencies)
        summary["latency_ms_p50"] = round(statistics.median(ordered), 1)
        summary["latency_ms_p95"] = round(
            ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 1
        )
    return {"summary": summary, "rows": rows}


def _print_report(result: dict[str, Any], verbose: bool) -> None:
    s = result["summary"]
    print(f"\n=== router={s['router']} scope={s['scope']} ===")
    print(
        f"total={s['total']} correct={s['correct']} escalated={s['escalated']} "
        f"wrong={s['wrong']} error={s['error']}  accuracy={s['accuracy']:.1%} "
        f"false_act={s['false_act_rate']:.1%}"
    )
    if "latency_ms_p50" in s:
        print(f"latency p50={s['latency_ms_p50']}ms p95={s['latency_ms_p95']}ms")
    for row in result["rows"]:
        if row["verdict"] == "correct" and not verbose:
            continue
        got = row["got"]
        desc = (
            row["error"]
            or f"{got.get('kind')} {got.get('skill') or ''} {got.get('params') or ''}".strip()
        )
        conf = got.get("confidence")
        conf_s = f" conf={conf:.2f}" if isinstance(conf, float) else ""
        print(f"  [{row['verdict']:9}] {row['id']}: '{row['instruction']}' → {desc}{conf_s}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="System 1 사전 라우터 평가")
    parser.add_argument("--router", choices=["rule", "laya", "both"], default="rule")
    parser.add_argument(
        "--endpoint", default="", help="Laya 서버 주소 (예: http://192.168.50.212:8000)"
    )
    parser.add_argument(
        "--api-key", default="", help="Laya API 키. 가능하면 --env-file 이나 환경변수를 사용"
    )
    parser.add_argument(
        "--env-file", type=Path, help="SYSTEM1_/LAYA_ API_KEY·ENDPOINT 를 읽을 dotenv 파일"
    )
    parser.add_argument("--scope", choices=["readonly", "navigation"], default="readonly")
    parser.add_argument(
        "--timeout-ms", type=float, default=3000.0, help="평가 시에는 여유 있게(기본 3000)"
    )
    parser.add_argument("--thresholds", default="", help='JSON, 예: {"smalltalk":0.8}')
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("-v", "--verbose", action="store_true", help="correct 케이스도 출력")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    fast_router, system1_router = _import_routers()
    cases = _load_cases(args.cases)
    context = json.loads(args.context.read_text(encoding="utf-8"))
    node = _Node(context)

    routers = []
    if args.router in ("rule", "both"):
        routers.append(fast_router.RuleRouter())
    if args.router in ("laya", "both"):
        file_env = read_env_file(args.env_file)
        endpoint, _ = resolve_setting(args.endpoint, ENDPOINT_NAMES, os.environ, file_env)
        api_key, key_source = resolve_setting(args.api_key, API_KEY_NAMES, os.environ, file_env)
        if not endpoint:
            parser.error(
                "--router laya/both 에는 --endpoint, SYSTEM1_ENDPOINT 또는 LAYA_ENDPOINT 가 필요합니다"
            )
        print(f"Laya endpoint: {endpoint}  auth: {'on (' + key_source + ')' if api_key else 'off'}")
        env = {
            "SYSTEM1_ROUTER": "laya",
            "SYSTEM1_ENDPOINT": endpoint,
            "SYSTEM1_API_KEY": api_key,
            "SYSTEM1_SCOPE": args.scope,
            "SYSTEM1_TIMEOUT_MS": str(args.timeout_ms),
        }
        if args.thresholds:
            env["SYSTEM1_CONF_THRESHOLDS_JSON"] = args.thresholds
        cfg = system1_router.System1Config.from_env(env)
        # 평가 중에는 circuit breaker 로 호출이 막히지 않게 한다(오류는 error 로 집계).
        cfg.breaker_failures = 10**9
        routers.append(system1_router.SystemOneRouter(cfg))

    results = [asyncio.run(_run_router(r, node, cases, args.scope)) for r in routers]
    for result in results:
        _print_report(result, args.verbose)

    if args.json_out:
        args.json_out.write_text(
            json.dumps(
                {"cases": str(args.cases), "results": results}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        print(f"\nsaved: {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
