"""실제 Laya 서버(laya-serve) 대상 라이브 테스트.

robo-claw 없이 Laya 서버만 검증한다. 엔드포인트가 지정되지 않으면 전체가 skip 된다.

    LAYA_ENDPOINT=http://192.168.50.212:8000 LAYA_ENV_FILE=.env \\
        python3 -m pytest -s validation/system1/test_laya_live.py

또는 ``validation/system1/run_laya_tests.sh`` 를 사용한다.

환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| ``LAYA_ENDPOINT`` | (없음 → skip) | Laya 서버 주소. ``SYSTEM1_ENDPOINT`` 도 인식 |
| ``LAYA_API_KEY`` | (없음) | Bearer 키. ``SYSTEM1_API_KEY`` 도 인식 |
| ``LAYA_ENV_FILE`` | 저장소 루트 ``.env`` | 위 두 값을 읽을 dotenv 파일(키는 출력하지 않음) |
| ``LAYA_TIMEOUT_MS`` | ``5000`` | 요청당 대기 시간 |
| ``LAYA_LATENCY_BUDGET_MS`` | ``1000`` | robo-claw 질문 세트 warm p95 상한 |
| ``LAYA_LATENCY_SAMPLES`` | ``10`` | 지연 측정 횟수 |
| ``LAYA_MAX_FALSE_ACT`` | ``0.10`` | 라우터 오실행률(wrong/total) 상한 |
| ``LAYA_MIN_INTENT_ACC`` / ``LAYA_MIN_SKILL_ACC`` / ``LAYA_MIN_AMBIGUOUS_ACC`` | (없음) | 지정하면 질문 단위 정확도 하한을 검사. 없으면 보고만 함 |
| ``LAYA_REPORT_DIR`` | (없음) | 지정하면 정확도·지연 결과를 JSON 으로 저장 |
"""

from __future__ import annotations

import importlib.util
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
EVAL_SCRIPT = ROOT / "scripts" / "system1_eval.py"
LAYA_CASES = HERE / "laya_cases.jsonl"
ROUTER_CASES = HERE / "router_cases.jsonl"
CONTEXT_FILE = HERE / "router_eval_context.json"


def _load_eval():
    spec = importlib.util.spec_from_file_location("system1_eval_live", EVAL_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVAL = _load_eval()
_ENV_FILE = Path(os.environ.get("LAYA_ENV_FILE") or (ROOT / ".env"))
_FILE_ENV = EVAL.read_env_file(_ENV_FILE)
ENDPOINT, _ = EVAL.resolve_setting(
    None, ("LAYA_ENDPOINT", "SYSTEM1_ENDPOINT"), os.environ, _FILE_ENV
)
ENDPOINT = ENDPOINT.rstrip("/")
API_KEY, API_KEY_SOURCE = EVAL.resolve_setting(
    None, ("LAYA_API_KEY", "SYSTEM1_API_KEY"), os.environ, _FILE_ENV
)
TIMEOUT_S = float(os.environ.get("LAYA_TIMEOUT_MS", "5000")) / 1000.0

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not ENDPOINT, reason="LAYA_ENDPOINT(또는 SYSTEM1_ENDPOINT)가 없어 라이브 테스트를 건너뜀"
    ),
]


def _float_env(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else None


def _request(
    method: str, path: str, body: Any = None, *, auth: str | None = "default"
) -> tuple[int, Any]:
    """(status, json 또는 text). auth="default" 는 설정된 키, None 은 헤더 없음, 그 외 문자열은 그 키."""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{ENDPOINT}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    token = API_KEY if auth == "default" else auth
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw.decode("utf-8"))
    except ValueError:
        return status, raw.decode("utf-8", "replace")


def _predict(state: Any, questions: dict[str, Any]) -> dict[str, Any]:
    status, data = _request("POST", "/v1/systemone", {"state": state, "questions": questions})
    assert status == 200, f"HTTP {status}: {data}"
    assert isinstance(data, dict) and isinstance(data.get("answers"), dict), data
    return data


def _report(name: str, payload: dict[str, Any]) -> None:
    print(f"\n[laya-live] {name}: {json.dumps(payload, ensure_ascii=False)}")
    out_dir = os.environ.get("LAYA_REPORT_DIR", "").strip()
    if out_dir:
        path = Path(out_dir)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


@pytest.fixture(scope="module")
def routers():
    _fast_router, system1_router = EVAL._import_routers()
    return system1_router


@pytest.fixture(scope="module")
def node():
    return EVAL._Node(json.loads(CONTEXT_FILE.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def robo_claw_questions(routers, node):
    """robo-claw 가 실제로 보내는 질문 세트(readonly scope)."""
    cfg = routers.System1Config.from_env({"SYSTEM1_SCOPE": "readonly"})
    skills = routers._skill_candidates(node, cfg)
    return skills, routers.build_questions(skills, [])


INTENT_ONLY = {
    "intent": {
        "type": "choice",
        "instructions": "사용자가 로봇에게 한 말의 종류는?",
        "criteria": {
            "smalltalk": "인사, 감사, 잡담",
            "single_skill": "로봇 기능 하나로 처리되는 명령이나 조회",
            "multi_step": "여러 단계가 필요한 명령",
        },
    }
}


# ── 1. 연결 / 인증 ─────────────────────────────────────────────────────────


def test_health():
    status, data = _request("GET", "/health")
    _report("health", {"status": status, "body": data, "auth": API_KEY_SOURCE or "off"})
    assert status == 200, data


@pytest.mark.skipif(not API_KEY, reason="API 키가 없어 인증 검사를 건너뜀")
def test_predict_without_key_is_rejected():
    status, _ = _request(
        "POST",
        "/v1/systemone",
        {"state": {"instruction": "안녕"}, "questions": INTENT_ONLY},
        auth=None,
    )
    assert status == 401


@pytest.mark.skipif(not API_KEY, reason="API 키가 없어 인증 검사를 건너뜀")
def test_predict_with_wrong_key_is_rejected():
    status, _ = _request(
        "POST",
        "/v1/systemone",
        {"state": {"instruction": "안녕"}, "questions": INTENT_ONLY},
        auth="wrong-" + "x" * 16,
    )
    assert status == 401


# ── 2. 스키마 / 체크포인트 ──────────────────────────────────────────────────


def test_choice_answer_schema():
    data = _predict({"instruction": "지금 배터리 얼마 남았어?"}, INTENT_ONLY)
    ans = data["answers"]["intent"]
    assert ans["choice"] in INTENT_ONLY["intent"]["criteria"]
    assert 0.0 <= float(ans["confidence"]) <= 1.0


def test_noul_answer_schema():
    data = _predict(
        {"instruction": "그거 좀 해줘"},
        {"ambiguous": {"type": "noul", "instructions": "대상이나 장소가 불분명한가?"}},
    )
    assert 0.0 <= float(data["answers"]["ambiguous"]["noul"]) <= 1.0


def test_korean_routes_to_multilingual_checkpoint():
    data = _predict({"instruction": "지금 배터리 얼마 남았어?"}, INTENT_ONLY)
    routing = data.get("routing") or {}
    _report("routing", routing)
    assert "multilingual" in json.dumps(routing).lower(), (
        "한국어 요청이 multilingual 체크포인트로 가지 않았습니다. "
        "서버에 LAYA_DEFAULT_MODEL=multilingual / LAYA_MODELS=multilingual 을 확인하세요."
    )


def test_robo_claw_question_set_is_answered(robo_claw_questions):
    skills, questions = robo_claw_questions
    data = _predict({"instruction": "지금 배터리 얼마 남았어?"}, questions)
    answers = data["answers"]
    assert set(questions) <= set(answers), f"missing answers: {set(questions) - set(answers)}"
    assert answers["intent"]["choice"] in questions["intent"]["criteria"]
    if "skill" in questions:
        assert answers["skill"]["choice"] in questions["skill"]["criteria"]
    assert 0.0 <= float(answers["ambiguous"]["noul"]) <= 1.0


def test_too_many_options_is_rejected():
    """서버 상한(choice 선택지 100개)을 확인한다. robo-claw 는 SYSTEM1_MAX_OPTIONS(기본 12)로 훨씬 적게 보낸다."""
    criteria = {f"opt{i}": f"option {i}" for i in range(101)}
    status, _ = _request(
        "POST",
        "/v1/systemone",
        {
            "state": {"instruction": "안녕"},
            "questions": {
                "q": {"type": "choice", "instructions": "고르시오", "criteria": criteria}
            },
        },
    )
    assert status == 413


# ── 3. 지연 ────────────────────────────────────────────────────────────────


def test_warm_latency_within_budget(robo_claw_questions):
    _skills, questions = robo_claw_questions
    budget = float(os.environ.get("LAYA_LATENCY_BUDGET_MS", "1000"))
    samples = max(3, int(os.environ.get("LAYA_LATENCY_SAMPLES", "10")))
    state = {"instruction": "지금 배터리 얼마 남았어?"}
    for _ in range(2):  # warm-up (CUDA 초기화, 캐시)
        _predict(state, questions)
    latencies = []
    for _ in range(samples):
        start = time.perf_counter()
        _predict(state, questions)
        latencies.append((time.perf_counter() - start) * 1000.0)
    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    result = {
        "samples": samples,
        "p50_ms": round(statistics.median(ordered), 1),
        "p95_ms": round(p95, 1),
        "max_ms": round(ordered[-1], 1),
        "budget_ms": budget,
        "suggested_SYSTEM1_TIMEOUT_MS": int(p95 * 1.5),
    }
    _report("latency", result)
    assert p95 <= budget, f"warm p95 {p95:.0f}ms > budget {budget:.0f}ms"


# ── 4. 질문 단위 정확도 (laya_cases.jsonl) ───────────────────────────────────


def test_question_level_accuracy(robo_claw_questions):
    skills, questions = robo_claw_questions
    cases = EVAL._load_cases(LAYA_CASES)
    stats = {k: {"correct": 0, "total": 0} for k in ("intent", "skill", "ambiguous")}
    misses: list[dict[str, Any]] = []
    for case in cases:
        answers = _predict({"instruction": case["instruction"]}, questions)["answers"]
        checks = []
        if "intent" in case:
            checks.append(("intent", case["intent"], answers["intent"]["choice"]))
        if "skill" in case and "skill" in answers:
            checks.append(("skill", case["skill"], answers["skill"]["choice"]))
        if "ambiguous" in case:
            got = float(answers["ambiguous"]["noul"]) >= 0.5
            checks.append(("ambiguous", case["ambiguous"], got))
        for key, expected, got in checks:
            stats[key]["total"] += 1
            if expected == got:
                stats[key]["correct"] += 1
            else:
                misses.append(
                    {
                        "id": case["id"],
                        "instruction": case["instruction"],
                        key: {"expected": expected, "got": got},
                    }
                )
    accuracy = {
        k: round(v["correct"] / v["total"], 3) if v["total"] else None for k, v in stats.items()
    }
    _report("question_accuracy", {"accuracy": accuracy, "counts": stats, "misses": misses})

    for key, env_name in (
        ("intent", "LAYA_MIN_INTENT_ACC"),
        ("skill", "LAYA_MIN_SKILL_ACC"),
        ("ambiguous", "LAYA_MIN_AMBIGUOUS_ACC"),
    ):
        floor = _float_env(env_name)
        if floor is not None and accuracy[key] is not None:
            assert accuracy[key] >= floor, f"{key} accuracy {accuracy[key]} < {env_name}={floor}"


# ── 5. 라우터 수준 오실행률 (router_cases.jsonl, robo-claw 와 같은 판정) ─────────


def test_router_false_act_rate(routers, node):
    env = {
        "SYSTEM1_ROUTER": "laya",
        "SYSTEM1_ENDPOINT": ENDPOINT,
        "SYSTEM1_API_KEY": API_KEY,
        "SYSTEM1_SCOPE": "readonly",
        "SYSTEM1_TIMEOUT_MS": str(TIMEOUT_S * 1000.0),
    }
    cfg = routers.System1Config.from_env(env)
    cfg.breaker_failures = 10**9
    router = routers.SystemOneRouter(cfg)
    cases = EVAL._load_cases(ROUTER_CASES)
    import asyncio

    result = asyncio.run(EVAL._run_router(router, node, cases, "readonly"))
    summary = result["summary"]
    wrong = [r for r in result["rows"] if r["verdict"] in ("wrong", "error")]
    _report("router_eval", {"summary": summary, "wrong_or_error": wrong})

    assert summary["error"] == 0, f"{summary['error']} requests failed (connection/auth/timeout)"
    max_false_act = float(os.environ.get("LAYA_MAX_FALSE_ACT", "0.10"))
    assert summary["false_act_rate"] <= max_false_act, (
        f"false_act_rate {summary['false_act_rate']} > {max_false_act}: "
        + ", ".join(f"{r['id']}" for r in wrong)
    )
