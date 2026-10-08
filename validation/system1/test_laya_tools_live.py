"""실제 Laya 서버 대상 툴 단위·복합 명령 라이브 테스트.

robo-claw 의 스킬 카탈로그 전체(``skill_catalog.json``)로 Laya 의 라우팅·툴 식별·복합 명령 판단을 검증한다.
엔드포인트가 없으면 전체 skip 한다(설정 방법은 ``test_laya_live.py`` 와 같다).

| 이름 | 기본값 | 설명 |
|---|---|---|
| ``LAYA_ENDPOINT`` / ``LAYA_API_KEY`` / ``LAYA_ENV_FILE`` | | ``test_laya_live.py`` 와 동일 |
| ``LAYA_SCOPE`` | ``readonly`` | 라우터 scope(``readonly`` / ``navigation``) |
| ``LAYA_MAX_FALSE_ACT`` | ``0.10`` | 툴·복합 케이스 전체의 오실행률 상한 |
| ``LAYA_MIN_CATEGORY_ACC`` / ``LAYA_MIN_TOOL_ACC`` / ``LAYA_MIN_MULTI_STEP_ACC`` | (없음) | 지정 시 하한 검사, 없으면 보고만 |
| ``LAYA_REPORT_DIR`` | (없음) | 지정 시 결과 JSON 저장 |

복합 명령이 직접 실행되는 경우(``compound_unsafe_direct``)는 기준과 무관하게 항상 실패로 본다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL_EVAL = _load("system1_tool_eval_live", "scripts/system1_tool_eval.py")
EVAL = TOOL_EVAL.EVAL
_FILE_ENV = EVAL.read_env_file(Path(os.environ.get("LAYA_ENV_FILE") or (ROOT / ".env")))
ENDPOINT, _ = EVAL.resolve_setting(
    None, ("LAYA_ENDPOINT", "SYSTEM1_ENDPOINT"), os.environ, _FILE_ENV
)
ENDPOINT = ENDPOINT.rstrip("/")
API_KEY, _ = EVAL.resolve_setting(None, ("LAYA_API_KEY", "SYSTEM1_API_KEY"), os.environ, _FILE_ENV)
TIMEOUT_MS = float(os.environ.get("LAYA_TIMEOUT_MS", "5000"))
SCOPE = os.environ.get("LAYA_SCOPE", "readonly")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not ENDPOINT, reason="LAYA_ENDPOINT(또는 SYSTEM1_ENDPOINT)가 없어 라이브 테스트를 건너뜀"
    ),
]


def _float_env(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else None


def _report(name: str, payload) -> None:
    print(
        f"\n[laya-tools] {name}: {json.dumps(payload.get('summary', payload), ensure_ascii=False)}"
    )
    out_dir = os.environ.get("LAYA_REPORT_DIR", "").strip()
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


@pytest.fixture(scope="module")
def env():
    fast, routers = EVAL._import_routers()
    catalog = TOOL_EVAL.load_catalog()
    places = json.loads(TOOL_EVAL.CONTEXT.read_text(encoding="utf-8"))["places"]
    node = TOOL_EVAL.catalog_node(catalog, places)
    client = routers.SystemOneClient(ENDPOINT, TIMEOUT_MS, API_KEY)
    return {
        "routers": routers,
        "catalog": catalog,
        "places": places,
        "node": node,
        "client": client,
        "tool_cases": TOOL_EVAL.load_cases(TOOL_EVAL.TOOL_CASES),
        "compound_cases": TOOL_EVAL.load_cases(TOOL_EVAL.COMPOUND_CASES),
    }


def test_router_on_every_tool_and_compound_command(env):
    routers = env["routers"]
    cfg = routers.System1Config.from_env(
        {
            "SYSTEM1_ROUTER": "laya",
            "SYSTEM1_ENDPOINT": ENDPOINT,
            "SYSTEM1_API_KEY": API_KEY,
            "SYSTEM1_SCOPE": SCOPE,
            "SYSTEM1_TIMEOUT_MS": str(TIMEOUT_MS),
        }
    )
    cfg.breaker_failures = 10**9
    direct = set(
        routers._skill_candidates(
            env["node"], routers.System1Config.from_env({"SYSTEM1_SCOPE": SCOPE})
        )
    )
    cases = TOOL_EVAL._router_cases(
        env["tool_cases"], env["compound_cases"], direct, env["places"], SCOPE
    )

    result = asyncio.run(
        TOOL_EVAL.evaluate_router(routers.SystemOneRouter(cfg), env["node"], cases, SCOPE)
    )
    _report("tool_router", result)
    summary = result["summary"]

    assert summary["error"] == 0, "Laya 요청 실패(연결/인증/timeout)가 있습니다"
    assert summary["compound_unsafe_direct"] == 0, result["unsafe_compound"]
    limit = float(os.environ.get("LAYA_MAX_FALSE_ACT", "0.10"))
    wrong = [r["id"] for r in result["rows"] if r["verdict"] == "wrong"]
    assert summary["false_act_rate"] <= limit, (
        f"false_act_rate {summary['false_act_rate']} > {limit}: {wrong}"
    )


def test_tool_identification_category_then_skill(env):
    result = TOOL_EVAL.evaluate_tool_identification(
        env["client"], env["catalog"], env["tool_cases"]
    )
    _report("tool_identification", result)
    summary = result["summary"]
    assert summary["total"] >= len(TOOL_EVAL.public_skills(env["catalog"]))
    for key, name in (
        ("category_accuracy", "LAYA_MIN_CATEGORY_ACC"),
        ("skill_accuracy", "LAYA_MIN_TOOL_ACC"),
    ):
        floor = _float_env(name)
        if floor is not None:
            assert summary[key] >= floor, f"{key} {summary[key]} < {name}={floor}"


def test_compound_commands_are_recognized(env):
    result = TOOL_EVAL.evaluate_compound_intent(
        env["client"], env["routers"], env["compound_cases"]
    )
    _report("compound_intent", result)
    floor = _float_env("LAYA_MIN_MULTI_STEP_ACC")
    if floor is not None:
        assert result["summary"]["multi_step_accuracy"] >= floor
