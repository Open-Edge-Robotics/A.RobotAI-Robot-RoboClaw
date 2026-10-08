#!/usr/bin/env python3
"""System 1 툴(스킬) 단위·복합 명령 평가.

robo-claw 의 실제 스킬 카탈로그(``validation/system1/skill_catalog.json``)로 세 가지를 평가한다.

1. 라우터 결정 (``tool_cases.jsonl`` + ``compound_cases.jsonl``)
   robo-claw 와 같은 코드(SystemOneRouter: 후보 스킬 선정, 복합 명령 가드, 임계값)로 각 명령의
   경로(direct_skill / simple_reply / llm)를 판정한다. 기대 경로는 카탈로그에서 자동으로 정한다.
     - 인사(smalltalk) → simple_reply
     - read 이면서 필수 인자가 없는 스킬 → direct_skill
     - 기억된 장소로 navigate_to → navigation scope 에서만 direct_skill
     - 그 외(인자 필요·동작 스킬·질문·복합 명령) → llm
2. 툴 식별 (``tool_cases.jsonl``)
   Laya 가 카테고리(1단계) → 그 카테고리의 스킬(2단계)을 맞히는지 본다. 라우터의 직접 실행 범위와
   무관한 Laya 의 판단 능력 지표이며, 직접 실행 범위를 넓힐 수 있는지 판단하는 근거다.
3. 복합 명령 (``compound_cases.jsonl``)
   intent 가 multi_step 으로 분류되는지, 그리고 **어떤 경우에도 직접 실행되지 않는지**(unsafe_direct=0)를 본다.

    python3 scripts/system1_tool_eval.py --endpoint http://192.168.50.212:8000 --env-file .env
    python3 scripts/system1_tool_eval.py --router rule                 # 규칙 라우터만 (서버 불필요)
    python3 scripts/system1_tool_eval.py --endpoint ... --scope navigation --json-out /tmp/tool_eval.json
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
VALIDATION = ROOT / "validation" / "system1"
CATALOG = VALIDATION / "skill_catalog.json"
TOOL_CASES = VALIDATION / "tool_cases.jsonl"
COMPOUND_CASES = VALIDATION / "compound_cases.jsonl"
CONTEXT = VALIDATION / "router_eval_context.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVAL = _load_module("system1_eval_for_tools", HERE / "system1_eval.py")
CATALOG_TOOL = _load_module("system1_skill_catalog_for_tools", HERE / "system1_skill_catalog.py")


def load_catalog(path: Path = CATALOG) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def public_skills(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in catalog["skills"] if not s["internal"]]


def catalog_node(catalog: dict[str, Any], places: list[str]):
    """robo-claw SkillManager.list_skills(include_internal=False) 와 같은 형태의 가짜 노드."""
    listed = []
    for skill in public_skills(catalog):
        required = skill["required"]
        # 동적 스키마(required=None)는 인자 필요 여부를 알 수 없으므로 직접 실행 후보에서 뺀다.
        schema = {
            "type": "object",
            "required": ["<dynamic>"] if required is None else list(required),
        }
        listed.append(
            {
                "name": skill["name"],
                "description": skill["description"],
                "risk_level": skill["risk_level"],
                "input_schema": schema,
            }
        )
    return EVAL._Node({"skills": listed, "places": places})


def load_cases(path: Path) -> list[dict[str, Any]]:
    return EVAL._load_cases(path)


def expected_route(
    case: dict[str, Any], direct_skills: set[str], places: list[str], scope: str
) -> dict:
    """카탈로그와 scope 로 기대 경로를 정한다(robo-claw 설계 기준)."""
    if case.get("steps"):
        return {"kind": "llm"}
    if case.get("intent") == "smalltalk":
        return {"kind": "simple_reply"}
    skill = case.get("skill")
    if skill in direct_skills:
        return {"kind": "direct_skill", "skill": skill, "params": {}}
    target = (case.get("params") or {}).get("target_name")
    if skill == "navigate_to" and scope == "navigation" and target in places:
        return {"kind": "direct_skill", "skill": "navigate_to", "params": {"target_name": target}}
    return {"kind": "llm"}


def _router_cases(tool_cases, compound_cases, direct_skills, places, scope):
    rows = []
    for case in [*tool_cases, *compound_cases]:
        rows.append(
            {
                "id": case["id"],
                "instruction": case["instruction"],
                "expected": expected_route(case, direct_skills, places, scope),
                "compound": bool(case.get("steps")),
                "skill": case.get("skill"),
                "params": case.get("params") or {},
            }
        )
    return rows


def _params_match(expected: dict[str, Any], got: dict[str, Any]) -> bool:
    """케이스에 적은 인자가 모두 같은 값으로 들어갔는지(숫자는 근사 비교)."""
    for key, value in expected.items():
        if key not in got:
            return False
        actual = got[key]
        if isinstance(value, (int, float)) and isinstance(actual, (int, float)):
            if abs(float(value) - float(actual)) > 1e-6:
                return False
        elif str(value) != str(actual):
            return False
    return True


def _rejudge(row: dict[str, Any], case: dict[str, Any]) -> str:
    """기대 경로가 llm 이어도, 맞는 스킬을 맞는 인자로 직접 실행했다면 오실행이 아니다(direct_ok).

    규칙 라우터의 지름길(move_relative, 컵 집기, 캡처)이 여기에 해당한다. 복합 명령, 질문, 인사를
    직접 실행하거나, 다른 스킬·다른 인자로 실행하면 wrong 이다.
    """
    got = row["got"]
    if (
        row["verdict"] == "wrong"
        and got.get("kind") == "direct_skill"
        and not case["compound"]
        and case.get("skill")
        and got.get("skill") == case["skill"]
        and _params_match(case["params"], got.get("params") or {})
    ):
        return "direct_ok"
    return row["verdict"]


async def evaluate_router(router, node, cases: list[dict[str, Any]], scope: str) -> dict[str, Any]:
    result = await EVAL._run_router(router, node, cases, scope)
    by_id = {c["id"]: c for c in cases}
    for row in result["rows"]:
        row["verdict"] = _rejudge(row, by_id[row["id"]])
    counts = Counter(r["verdict"] for r in result["rows"])
    total = len(result["rows"]) or 1
    summary = result["summary"]
    for key in ("correct", "escalated", "wrong", "error"):
        summary[key] = counts.get(key, 0)
    summary["direct_ok"] = counts.get("direct_ok", 0)
    summary["accuracy"] = round((summary["correct"] + summary["direct_ok"]) / total, 3)
    summary["false_act_rate"] = round(summary["wrong"] / total, 3)
    unsafe = [
        r
        for r in result["rows"]
        if by_id[r["id"]]["compound"] and r["got"].get("kind") not in (None, "llm")
    ]
    summary["compound_unsafe_direct"] = len(unsafe)
    result["unsafe_compound"] = unsafe
    return result


def _skill_criteria(skills: list[dict[str, Any]]) -> dict[str, str]:
    return {s["name"]: (s["description"] or s["name"])[:160] for s in skills}


def _pick_skill(client, state: dict[str, Any], skills: list[dict[str, Any]]) -> tuple[Any, float]:
    question = {
        "skill": {
            "type": "choice",
            "instructions": "이 명령을 처리할 로봇 기능은?",
            "criteria": _skill_criteria(skills),
        }
    }
    answer = client.predict_sync(state, question)["answers"]["skill"]
    return answer.get("choice"), float(answer.get("confidence", 0.0))


def evaluate_tool_identification(client, catalog, tool_cases) -> dict[str, Any]:
    """카테고리 → 스킬 2단계 질의로 툴 식별 정확도를 측정한다."""
    categories = dict(catalog["categories"])
    by_category: dict[str, list[dict[str, Any]]] = {}
    for skill in public_skills(catalog):
        by_category.setdefault(skill["category"], []).append(skill)
    skill_category = {s["name"]: s["category"] for s in public_skills(catalog)}
    category_q = {
        "category": {
            "type": "choice",
            "instructions": "이 명령을 처리할 로봇 기능의 분류는?",
            "criteria": {k: v for k, v in categories.items() if k in by_category},
        }
    }
    rows = []
    confusion: Counter[tuple[str, str]] = Counter()
    latencies = []
    for case in tool_cases:
        skill = case.get("skill")
        if not skill:
            continue
        expected_cat = skill_category[skill]
        state = {"instruction": case["instruction"]}
        start = time.perf_counter()
        ans = client.predict_sync(state, category_q)["answers"]["category"]
        got_cat, cat_conf = ans.get("choice"), float(ans.get("confidence", 0.0))

        got_skill, skill_conf = (
            _pick_skill(client, state, by_category[got_cat])
            if got_cat in by_category
            else (None, 0.0)
        )
        latencies.append((time.perf_counter() - start) * 1000.0)
        # 카테고리를 틀렸을 때, 정답 카테고리가 주어지면 스킬은 맞히는지(2단계 단독 성능)
        oracle_skill = (
            got_skill
            if got_cat == expected_cat
            else _pick_skill(client, state, by_category[expected_cat])[0]
        )
        if got_cat != expected_cat:
            confusion[(expected_cat, str(got_cat))] += 1
        rows.append(
            {
                "id": case["id"],
                "instruction": case["instruction"],
                "expected": {"category": expected_cat, "skill": skill},
                "got": {
                    "category": got_cat,
                    "category_confidence": round(cat_conf, 3),
                    "skill": got_skill,
                    "skill_confidence": round(skill_conf, 3),
                    "skill_given_true_category": oracle_skill,
                },
                "category_ok": got_cat == expected_cat,
                "skill_ok": got_skill == skill,
                "skill_given_category_ok": oracle_skill == skill,
            }
        )
    total = len(rows) or 1
    per_category: dict[str, dict[str, int]] = {}
    for row in rows:
        stat = per_category.setdefault(
            row["expected"]["category"], {"total": 0, "category_ok": 0, "skill_ok": 0}
        )
        stat["total"] += 1
        stat["category_ok"] += row["category_ok"]
        stat["skill_ok"] += row["skill_ok"]
    ordered = sorted(latencies)
    summary = {
        "total": len(rows),
        "category_accuracy": round(sum(r["category_ok"] for r in rows) / total, 3),
        "skill_accuracy": round(sum(r["skill_ok"] for r in rows) / total, 3),
        "skill_accuracy_given_category": round(
            sum(r["skill_given_category_ok"] for r in rows) / total, 3
        ),
        "latency_ms_p50_2stage": round(ordered[len(ordered) // 2], 1) if ordered else None,
        "top_confusions": [
            {"expected": e, "got": g, "count": n} for (e, g), n in confusion.most_common(8)
        ],
    }
    return {"summary": summary, "per_category": per_category, "rows": rows}


def evaluate_compound_intent(client, routers_mod, compound_cases) -> dict[str, Any]:
    """복합 명령이 multi_step 으로 분류되는지, 그리고 복합 명령 가드에 걸리는지."""
    from robo_claw_agent.agent_node.task_planner import looks_compound

    questions = {"intent": routers_mod.build_questions({}, [])["intent"]}
    rows = []
    for case in compound_cases:
        ans = client.predict_sync({"instruction": case["instruction"]}, questions)["answers"][
            "intent"
        ]
        rows.append(
            {
                "id": case["id"],
                "instruction": case["instruction"],
                "steps": [s["skill"] for s in case["steps"]],
                "guard_caught": bool(looks_compound(case["instruction"])),
                "intent": ans.get("choice"),
                "confidence": round(float(ans.get("confidence", 0.0)), 3),
            }
        )
    total = len(rows) or 1
    summary = {
        "total": len(rows),
        "multi_step_accuracy": round(sum(r["intent"] == "multi_step" for r in rows) / total, 3),
        "guard_coverage": round(sum(r["guard_caught"] for r in rows) / total, 3),
        "missed_by_guard_and_model": [
            r["id"] for r in rows if not r["guard_caught"] and r["intent"] != "multi_step"
        ],
    }
    return {"summary": summary, "rows": rows}


def _print_router(result: dict[str, Any], verbose: bool) -> None:
    s = result["summary"]
    print(f"\n=== router={s['router']} scope={s['scope']} (tool + compound) ===")
    print(
        f"total={s['total']} correct={s['correct']} direct_ok={s['direct_ok']} "
        f"escalated={s['escalated']} wrong={s['wrong']} "
        f"error={s['error']}  accuracy={s['accuracy']:.1%} false_act={s['false_act_rate']:.1%} "
        f"compound_unsafe_direct={s['compound_unsafe_direct']}"
    )
    if "latency_ms_p50" in s:
        print(f"latency p50={s['latency_ms_p50']}ms p95={s['latency_ms_p95']}ms")
    for row in result["rows"]:
        if row["verdict"] in ("wrong", "error") or (verbose and row["verdict"] != "correct"):
            got = row["got"]
            desc = row["error"] or f"{got.get('kind')} {got.get('skill') or ''}".strip()
            print(f"  [{row['verdict']:9}] {row['id']}: '{row['instruction']}' → {desc}")


def _print_tools(result: dict[str, Any], verbose: bool) -> None:
    s = result["summary"]
    print("\n=== tool identification (category → skill) ===")
    print(
        f"total={s['total']} category={s['category_accuracy']:.1%} skill={s['skill_accuracy']:.1%} "
        f"skill|true-category={s['skill_accuracy_given_category']:.1%} "
        f"p50(2 requests)={s['latency_ms_p50_2stage']}ms"
    )
    print("per category (category_ok/skill_ok/total):")
    for cat, st in sorted(result["per_category"].items()):
        print(f"  {cat:14} {st['category_ok']:>3}/{st['skill_ok']:>3}/{st['total']:>3}")
    if s["top_confusions"]:
        print(
            "top confusions: "
            + ", ".join(f"{c['expected']}→{c['got']}×{c['count']}" for c in s["top_confusions"])
        )
    if verbose:
        for row in result["rows"]:
            if not row["skill_ok"]:
                g = row["got"]
                print(
                    f"  [miss] {row['id']}: '{row['instruction']}' → {g['category']}/{g['skill']} "
                    f"(expected {row['expected']['category']}/{row['expected']['skill']})"
                )


def _print_compound(result: dict[str, Any]) -> None:
    s = result["summary"]
    print("\n=== compound commands ===")
    print(
        f"total={s['total']} multi_step={s['multi_step_accuracy']:.1%} guard_coverage={s['guard_coverage']:.1%} "
        f"missed_by_guard_and_model={s['missed_by_guard_and_model']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="System 1 툴 단위·복합 명령 평가")
    parser.add_argument("--router", choices=["rule", "laya", "both"], default="laya")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--scope", choices=["readonly", "navigation"], default="readonly")
    parser.add_argument("--timeout-ms", type=float, default=5000.0)
    parser.add_argument(
        "--skip-tools", action="store_true", help="툴 식별 평가를 건너뜀(요청 수 절약)"
    )
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    fast_router, routers_mod = EVAL._import_routers()
    catalog = load_catalog()
    places = json.loads(CONTEXT.read_text(encoding="utf-8"))["places"]
    node = catalog_node(catalog, places)
    tool_cases = load_cases(TOOL_CASES)
    compound_cases = load_cases(COMPOUND_CASES)

    report: dict[str, Any] = {"scope": args.scope, "places": places}
    routers = []
    if args.router in ("rule", "both"):
        routers.append(fast_router.RuleRouter())
    client = None
    if args.router in ("laya", "both"):
        file_env = EVAL.read_env_file(args.env_file)
        endpoint, _ = EVAL.resolve_setting(args.endpoint, EVAL.ENDPOINT_NAMES, os.environ, file_env)
        api_key, key_source = EVAL.resolve_setting(
            args.api_key, EVAL.API_KEY_NAMES, os.environ, file_env
        )
        if not endpoint:
            parser.error(
                "--router laya/both 에는 --endpoint, SYSTEM1_ENDPOINT 또는 LAYA_ENDPOINT 가 필요합니다"
            )
        print(f"Laya endpoint: {endpoint}  auth: {'on (' + key_source + ')' if api_key else 'off'}")
        cfg = routers_mod.System1Config.from_env(
            {
                "SYSTEM1_ROUTER": "laya",
                "SYSTEM1_ENDPOINT": endpoint,
                "SYSTEM1_API_KEY": api_key,
                "SYSTEM1_SCOPE": args.scope,
                "SYSTEM1_TIMEOUT_MS": str(args.timeout_ms),
            }
        )
        cfg.breaker_failures = 10**9
        routers.append(routers_mod.SystemOneRouter(cfg))
        client = routers_mod.SystemOneClient(endpoint, args.timeout_ms, api_key)

    direct_cfg = routers_mod.System1Config.from_env({"SYSTEM1_SCOPE": args.scope})
    direct_skills = set(routers_mod._skill_candidates(node, direct_cfg))
    report["direct_candidates"] = sorted(direct_skills)
    print(f"direct candidates ({args.scope}): {sorted(direct_skills)}")
    cases = _router_cases(tool_cases, compound_cases, direct_skills, places, args.scope)

    report["router"] = []
    for router in routers:
        result = asyncio.run(evaluate_router(router, node, cases, args.scope))
        report["router"].append(result)
        _print_router(result, args.verbose)

    if client is not None:
        if not args.skip_tools:
            report["tools"] = evaluate_tool_identification(client, catalog, tool_cases)
            _print_tools(report["tools"], args.verbose)
        report["compound"] = evaluate_compound_intent(client, routers_mod, compound_cases)
        _print_compound(report["compound"])

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nsaved: {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
