"""System 1 툴 테스트 데이터(카탈로그·툴 케이스·복합 명령 케이스) 정합성 테스트.

스킬을 추가·삭제하거나 이름을 바꾸면 여기서 드러난다.

* ``skill_catalog.json`` 이 소스와 같아야 한다 → ``python3 scripts/system1_skill_catalog.py`` 로 갱신
* 모든 공개 스킬에 툴 케이스가 1개 이상 있어야 한다 → ``validation/system1/tool_cases.jsonl`` 에 추가
"""

import asyncio
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CATALOG_TOOL = _load("system1_skill_catalog_t", "scripts/system1_skill_catalog.py")
TOOL_EVAL = _load("system1_tool_eval_t", "scripts/system1_tool_eval.py")


@pytest.fixture(scope="module")
def catalog():
    return TOOL_EVAL.load_catalog()


@pytest.fixture(scope="module")
def tool_cases():
    return TOOL_EVAL.load_cases(TOOL_EVAL.TOOL_CASES)


@pytest.fixture(scope="module")
def compound_cases():
    return TOOL_EVAL.load_cases(TOOL_EVAL.COMPOUND_CASES)


def test_catalog_is_up_to_date(capsys):
    assert CATALOG_TOOL.main(["--check"]) == 0, capsys.readouterr().out


def test_every_public_skill_has_a_tool_case(catalog, tool_cases):
    covered = {c["skill"] for c in tool_cases if c.get("skill")}
    missing = sorted(
        s["name"] for s in TOOL_EVAL.public_skills(catalog) if s["name"] not in covered
    )
    assert missing == [], f"tool_cases.jsonl 에 케이스가 없는 스킬: {missing}"


def test_cases_reference_existing_public_skills(catalog, tool_cases, compound_cases):
    public = {s["name"] for s in TOOL_EVAL.public_skills(catalog)}
    unknown = sorted(
        {c["skill"] for c in tool_cases if c.get("skill")} - public
        | {s["skill"] for c in compound_cases for s in c["steps"]} - public
    )
    assert unknown == [], f"카탈로그에 없는(또는 내부) 스킬: {unknown}"


def test_case_ids_are_unique_and_well_formed(tool_cases, compound_cases):
    ids = [c["id"] for c in [*tool_cases, *compound_cases]]
    assert len(ids) == len(set(ids))
    for case in tool_cases:
        assert case["instruction"].strip()
        assert ("skill" in case) != ("intent" in case), case["id"]
        assert case.get("intent") in (None, "smalltalk", "question"), case["id"]
    for case in compound_cases:
        assert len(case["steps"]) >= 2, case["id"]


def test_direct_candidates_are_read_skills_without_required_args(catalog):
    _fast, routers = TOOL_EVAL.EVAL._import_routers()
    node = TOOL_EVAL.catalog_node(catalog, ["주방"])
    cfg = routers.System1Config.from_env({"SYSTEM1_SCOPE": "readonly"})
    direct = set(routers._skill_candidates(node, cfg))
    expected = {
        s["name"] for s in TOOL_EVAL.public_skills(catalog) if CATALOG_TOOL.is_direct_candidate(s)
    }
    assert direct == expected
    assert len(direct) <= cfg.max_options, "직접 실행 후보가 SYSTEM1_MAX_OPTIONS 를 넘으면 잘린다"


def test_expected_route_rules():
    direct, places = {"get_status"}, ["주방"]
    route = TOOL_EVAL.expected_route
    assert route({"intent": "smalltalk"}, direct, places, "readonly") == {"kind": "simple_reply"}
    assert route({"intent": "question"}, direct, places, "readonly") == {"kind": "llm"}
    assert route({"skill": "get_status"}, direct, places, "readonly")["kind"] == "direct_skill"
    nav = {"skill": "navigate_to", "params": {"target_name": "주방"}}
    assert route(nav, direct, places, "readonly") == {"kind": "llm"}
    assert route(nav, direct, places, "navigation")["params"] == {"target_name": "주방"}
    assert route({"skill": "move_relative"}, direct, places, "readonly") == {"kind": "llm"}
    assert route({"steps": [{}, {}], "skill": None}, direct, places, "readonly") == {"kind": "llm"}


def test_rule_router_never_executes_compound_commands(catalog, tool_cases, compound_cases):
    fast, routers = TOOL_EVAL.EVAL._import_routers()
    node = TOOL_EVAL.catalog_node(catalog, ["주방", "거실", "충전대", "회의실", "현관"])
    cfg = routers.System1Config.from_env({"SYSTEM1_SCOPE": "readonly"})
    direct = set(routers._skill_candidates(node, cfg))
    cases = TOOL_EVAL._router_cases(tool_cases, compound_cases, direct, ["주방"], "readonly")

    result = asyncio.run(TOOL_EVAL.evaluate_router(fast.RuleRouter(), node, cases, "readonly"))

    assert result["summary"]["compound_unsafe_direct"] == 0, result["unsafe_compound"]
    assert result["summary"]["error"] == 0


def test_rejudge_accepts_correct_direct_shortcut_only():
    case = {"compound": False, "skill": "move_relative", "params": {"forward": 2.0}}
    ok = {
        "verdict": "wrong",
        "got": {"kind": "direct_skill", "skill": "move_relative", "params": {"forward": 2}},
    }
    bad_param = {
        "verdict": "wrong",
        "got": {"kind": "direct_skill", "skill": "move_relative", "params": {"forward": 3}},
    }
    other = {"verdict": "wrong", "got": {"kind": "direct_skill", "skill": "rotate", "params": {}}}
    assert TOOL_EVAL._rejudge(ok, case) == "direct_ok"
    assert TOOL_EVAL._rejudge(bad_param, case) == "wrong"
    assert TOOL_EVAL._rejudge(other, case) == "wrong"
    compound = {"compound": True, "skill": None, "params": {}}
    assert TOOL_EVAL._rejudge(ok, compound) == "wrong"
