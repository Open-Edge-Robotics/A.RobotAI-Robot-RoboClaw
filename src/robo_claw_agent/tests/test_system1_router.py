"""System 1(Laya/Jev) 사전 라우터 테스트.

* 배타 선택: ``SYSTEM1_ROUTER`` 값에 따라 하나의 라우터만 판단한다.
* 장애 롤백: Laya 장애 시 해당 요청만 RuleRouter 로 대체한다.
* 확신이 없거나 모호하면 항상 System 2(``llm``)로 넘긴다.
"""

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from robo_claw_agent.agent_node.fast_router import (
    ROUTE_DIRECT_SKILL,
    ROUTE_LLM,
    ROUTE_SIMPLE_REPLY,
)
from robo_claw_agent.agent_node.system1_router import (
    CircuitBreaker,
    SelectedRouter,
    System1Config,
    System1Unavailable,
    SystemOneClient,
    SystemOneRouter,
    build_questions,
    build_router,
    interpret_response,
)


class _Skills:
    def list_skills(self, include_internal=True):
        return [
            {"name": "get_status", "description": "로봇 상태 조회", "risk_level": "read"},
            {"name": "identify_location", "description": "현재 위치 이름", "risk_level": "read"},
            {
                "name": "rag_search",
                "description": "지식 검색",
                "risk_level": "read",
                "input_schema": {"type": "object", "required": ["query"]},
            },
            {"name": "move_relative", "description": "상대 이동", "risk_level": "action"},
            {"name": "navigate_to", "description": "장소로 이동", "risk_level": "action"},
        ]


class _Memory:
    def get_all_objects(self):
        return [
            {"name": "주방", "metadata": {}},
            {"name": "키친", "metadata": {"alias_of": "주방"}},
            {"name": "거실", "metadata": {}},
        ]


class _Node:
    def __init__(self) -> None:
        self._skills = _Skills()
        self._memory = _Memory()
        self._logger = logging.getLogger("test_system1_router")

    def get_logger(self):
        return self._logger


class _FakeClient:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[tuple[dict, dict]] = []

    async def predict(self, state, questions):
        self.calls.append((state, questions))
        if self.error is not None:
            raise self.error
        return self.response


def _resp(intent, intent_conf=0.95, ambiguous=0.1, skill=None, skill_conf=0.95, place=None):
    answers = {
        "intent": {"choice": intent, "confidence": intent_conf},
        "ambiguous": {"noul": ambiguous, "confidence": 0.9},
    }
    if skill is not None:
        answers["skill"] = {"choice": skill, "confidence": skill_conf}
    if place is not None:
        answers["target_place"] = {"choice": place, "confidence": 0.95}
    return {"answers": answers}


def _laya(response=None, error=None, **cfg_kwargs):
    cfg = System1Config(router="laya", endpoint="http://laya.test", **cfg_kwargs)
    client = _FakeClient(response, error)
    return SystemOneRouter(cfg, client=client), client


def _decide(router, instruction):
    return asyncio.run(router.decide(_Node(), instruction))


class TestConfig:
    def test_defaults_keep_rule_router(self):
        cfg = System1Config.from_env({})
        assert cfg.router == "rule"
        assert cfg.shadow is False
        assert cfg.scope == "readonly"

    def test_reads_env(self):
        cfg = System1Config.from_env(
            {
                "SYSTEM1_ROUTER": "LAYA",
                "SYSTEM1_SHADOW": "true",
                "SYSTEM1_SCOPE": "navigation",
                "SYSTEM1_ENDPOINT": "http://laya:8000/",
                "SYSTEM1_TIMEOUT_MS": "500",
                "SYSTEM1_CONF_THRESHOLDS_JSON": '{"smalltalk": 0.7}',
                "SYSTEM1_SKILLS": "get_status, identify_location",
            }
        )
        assert cfg.router == "laya"
        assert cfg.shadow is True
        assert cfg.scope == "navigation"
        assert cfg.endpoint == "http://laya:8000"
        assert cfg.timeout_ms == 500.0
        assert cfg.thresholds["smalltalk"] == 0.7
        assert cfg.thresholds["single_skill"] == 0.85
        assert cfg.skills == ["get_status", "identify_location"]

    def test_invalid_values_fall_back_safely(self):
        cfg = System1Config.from_env(
            {
                "SYSTEM1_ROUTER": "gpt",
                "SYSTEM1_SCOPE": "all",
                "SYSTEM1_CONF_THRESHOLDS_JSON": "not-json",
            }
        )
        assert cfg.router == "rule"
        assert cfg.scope == "readonly"
        assert cfg.thresholds["smalltalk"] == 0.90


class TestBuildRouter:
    def test_rule_mode_uses_only_rule_router(self):
        router = build_router(System1Config.from_env({}), health_check=False)
        assert router.primary.name == "rule"
        assert router.fallback is None
        assert router.shadow is None

    def test_laya_without_endpoint_falls_back_to_rule(self):
        router = build_router(
            System1Config.from_env({"SYSTEM1_ROUTER": "laya"}), health_check=False
        )
        assert router.primary.name == "rule"

    def test_laya_mode_is_exclusive_with_rule_fallback(self):
        cfg = System1Config.from_env({"SYSTEM1_ROUTER": "laya", "SYSTEM1_ENDPOINT": "http://x"})
        router = build_router(cfg, health_check=False)
        assert isinstance(router.primary, SystemOneRouter)
        assert router.fallback is not None and router.fallback.name == "rule"
        assert router.shadow is None

    def test_rule_mode_with_shadow_runs_laya_in_shadow(self):
        cfg = System1Config.from_env({"SYSTEM1_SHADOW": "true", "SYSTEM1_ENDPOINT": "http://x"})
        router = build_router(cfg, health_check=False)
        assert router.primary.name == "rule"
        assert isinstance(router.shadow, SystemOneRouter)


class _RecordingLog:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def info(self, msg: str) -> None:
        self.infos.append(msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)


class TestRouterLogging:
    """ROS 노드에서는 모듈 로거 INFO 가 보이지 않으므로 노드 로거로 남겨야 한다."""

    def test_build_router_reports_config_to_node_logger(self):
        log = _RecordingLog()
        build_router(System1Config.from_env({}), health_check=False, log=log)
        assert log.infos == ["System1 router: router=rule shadow=False scope=readonly endpoint=-"]

    def test_misconfiguration_warns_on_node_logger(self):
        log = _RecordingLog()
        build_router(
            System1Config.from_env({"SYSTEM1_ROUTER": "laya"}), health_check=False, log=log
        )
        assert any("SYSTEM1_ENDPOINT is empty" in w for w in log.warnings)

    def test_health_check_failure_warns_on_node_logger(self):
        log = _RecordingLog()
        router, _ = _laya()
        router._client = SystemOneClient("http://127.0.0.1:9", timeout_ms=200)
        assert router.health_check(log) is False
        assert log.warnings and "health check failed" in log.warnings[0]


class TestQuestions:
    def test_readonly_scope_offers_only_no_arg_read_skills(self):
        router, client = _laya(_resp("question"))
        _decide(router, "지금 상태 알려줘")

        _, questions = client.calls[0]
        options = set(questions["skill"]["criteria"])
        assert options == {"get_status", "identify_location", "none"}
        assert "target_place" not in questions

    def test_navigation_scope_adds_navigate_and_places(self):
        router, client = _laya(_resp("question"), scope="navigation")
        _decide(router, "주방으로 가줘")

        _, questions = client.calls[0]
        assert "navigate_to" in questions["skill"]["criteria"]
        assert set(questions["target_place"]["criteria"]) == {"주방", "거실", "none"}

    def test_skill_allowlist(self):
        router, client = _laya(_resp("question"), skills=["get_status"])
        _decide(router, "상태")
        assert set(client.calls[0][1]["skill"]["criteria"]) == {"get_status", "none"}

    def test_build_questions_without_options(self):
        questions = build_questions({}, [])
        assert set(questions) == {"intent", "ambiguous"}


class TestInterpret:
    skills = {"get_status": "", "navigate_to": ""}

    def test_confident_readonly_skill_runs_directly(self):
        d = interpret_response(_resp("single_skill", skill="get_status"), self.skills, [], {})
        assert d.kind == ROUTE_DIRECT_SKILL
        assert d.skill == "get_status"
        assert d.params == {}
        assert d.source == "laya"

    def test_low_confidence_goes_to_llm(self):
        d = interpret_response(
            _resp("single_skill", intent_conf=0.6, skill="get_status"), self.skills, [], {}
        )
        assert d.kind == ROUTE_LLM
        assert d.hint["skill"] == "get_status"

    def test_ambiguous_goes_to_llm(self):
        d = interpret_response(
            _resp("single_skill", ambiguous=0.8, skill="get_status"), self.skills, [], {}
        )
        assert d.kind == ROUTE_LLM

    def test_unknown_skill_goes_to_llm(self):
        d = interpret_response(_resp("single_skill", skill="none"), self.skills, [], {})
        assert d.kind == ROUTE_LLM

    def test_smalltalk_gets_fixed_reply(self):
        d = interpret_response(_resp("smalltalk"), self.skills, [], {})
        assert d.kind == ROUTE_SIMPLE_REPLY

    def test_question_and_multi_step_go_to_llm(self):
        for intent in ("question", "multi_step"):
            assert interpret_response(_resp(intent), self.skills, [], {}).kind == ROUTE_LLM

    def test_navigate_requires_known_place(self):
        ok = interpret_response(
            _resp("single_skill", skill="navigate_to", place="주방"), self.skills, ["주방"], {}
        )
        assert ok.kind == ROUTE_DIRECT_SKILL
        assert ok.params == {"target_name": "주방"}

        missing = interpret_response(
            _resp("single_skill", skill="navigate_to", place="none"), self.skills, ["주방"], {}
        )
        assert missing.kind == ROUTE_LLM

    def test_thresholds_override(self):
        d = interpret_response(
            _resp("smalltalk", intent_conf=0.75), self.skills, [], {"smalltalk": 0.7}
        )
        assert d.kind == ROUTE_SIMPLE_REPLY


class TestSystemOneRouter:
    def test_compound_instruction_never_calls_model(self):
        router, client = _laya(_resp("single_skill", skill="get_status"))
        d = _decide(router, "회의실로 이동한 다음 컵을 집어줘")
        assert d.kind == ROUTE_LLM
        assert client.calls == []

    def test_relative_move_is_not_a_direct_skill_in_laya_mode(self):
        """배타 선택: Laya 모드에서는 규칙 지름길(move_relative)이 동작하지 않는다."""
        router, _ = _laya(_resp("single_skill", skill="none"))
        assert _decide(router, "앞으로 2미터 가줘").kind == ROUTE_LLM

    def test_failure_raises_and_opens_breaker(self):
        router, _ = _laya(error=System1Unavailable("timeout"))
        for _ in range(3):
            with pytest.raises(System1Unavailable):
                _decide(router, "상태 알려줘")
        assert router.breaker.is_open


class TestSelectedRouter:
    def test_laya_failure_falls_back_to_rule_per_request(self):
        laya, _ = _laya(error=System1Unavailable("timeout"))
        from robo_claw_agent.agent_node.fast_router import RuleRouter

        router = SelectedRouter(laya, fallback=RuleRouter())
        d = _decide(router, "앞으로 2미터 가줘")

        assert d.kind == ROUTE_DIRECT_SKILL
        assert d.skill == "move_relative"
        assert d.source == "rule"
        assert d.fallback is True

    def test_shadow_does_not_change_primary_decision(self, tmp_path):
        from robo_claw_agent.agent_node.fast_router import RuleRouter

        shadow, _ = _laya(_resp("single_skill", skill="get_status"))
        log = tmp_path / "shadow.jsonl"
        router = SelectedRouter(RuleRouter(), shadow=shadow, shadow_log=str(log))

        d = _decide(router, "앞으로 2미터 가줘")
        assert d.skill == "move_relative"
        assert d.source == "rule"

        for t in threading.enumerate():
            if t.name == "system1-shadow":
                t.join(timeout=5)
        entry = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert entry["primary"]["skill"] == "move_relative"
        assert entry["shadow"]["skill"] == "get_status"
        assert entry["agree"] is False


class TestCircuitBreaker:
    def test_opens_and_half_opens_after_cooldown(self):
        now = [0.0]
        br = CircuitBreaker(max_failures=2, cooldown_sec=10, clock=lambda: now[0])
        br.record_failure()
        assert br.allow()
        br.record_failure()
        assert not br.allow()
        now[0] = 11
        assert br.allow()  # half-open
        br.record_failure()
        assert not br.allow()
        now[0] = 22
        assert br.allow()
        br.record_success()
        assert not br.is_open


class TestHttpClient:
    def _serve(self, handler_cls):
        server = HTTPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server

    def test_posts_systemone_schema(self):
        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                received["path"] = self.path
                received["auth"] = self.headers.get("Authorization")
                length = int(self.headers["Content-Length"])
                received["body"] = json.loads(self.rfile.read(length))
                body = json.dumps(_resp("smalltalk")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = self._serve(Handler)
        try:
            client = SystemOneClient(
                f"http://127.0.0.1:{server.server_port}", timeout_ms=2000, api_key="k"
            )
            resp = client.predict_sync({"instruction": "안녕"}, build_questions({}, []))
        finally:
            server.shutdown()

        assert received["path"] == "/v1/systemone"
        assert received["auth"] == "Bearer k"
        assert received["body"]["state"] == {"instruction": "안녕"}
        assert resp["answers"]["intent"]["choice"] == "smalltalk"

    def test_http_error_raises_unavailable(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.send_response(500)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = self._serve(Handler)
        try:
            client = SystemOneClient(f"http://127.0.0.1:{server.server_port}", timeout_ms=2000)
            with pytest.raises(System1Unavailable):
                client.predict_sync({}, {})
        finally:
            server.shutdown()

    def test_connection_refused_raises_unavailable(self):
        client = SystemOneClient("http://127.0.0.1:9", timeout_ms=300)
        with pytest.raises(System1Unavailable):
            client.predict_sync({}, {})
