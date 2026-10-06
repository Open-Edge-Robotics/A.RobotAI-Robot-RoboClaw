"""System 1 모델(Laya / Jev) 기반 사전 라우터와 라우터 선택 로직.

설계: ``docs/SYSTEM1_FAST_ROUTER_DESIGN.md``

* ``SYSTEM1_ROUTER=rule`` (기본): 기존 RuleRouter 만 판단한다.
* ``SYSTEM1_ROUTER=laya``: SystemOneRouter 만 판단한다(**배타**). Laya 장애(timeout /
  HTTP 오류 / circuit open) 시에만 해당 요청을 RuleRouter 로 대체한다.
* ``SYSTEM1_SHADOW=true``: 선택되지 않은 라우터도 백그라운드로 실행해 판단만 기록한다.

Laya(``laya-serve``)와 Jev 는 같은 ``POST /v1/systemone`` 스키마를 쓰므로
``SYSTEM1_ENDPOINT`` 만 바꾸면 provider 를 교체할 수 있다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from ..system1_local_server import local_server_enabled, local_server_endpoint
from .fast_router import (
    ROUTE_DIRECT_SKILL,
    ROUTE_SIMPLE_REPLY,
    SIMPLE_REPLY_MESSAGE,
    FastRouter,
    RouteDecision,
    RuleRouter,
)

logger = logging.getLogger(__name__)

ROUTER_RULE = "rule"
ROUTER_LAYA = "laya"
_VALID_ROUTERS = (ROUTER_RULE, ROUTER_LAYA)

SCOPE_READONLY = "readonly"
SCOPE_NAVIGATION = "navigation"
_VALID_SCOPES = (SCOPE_READONLY, SCOPE_NAVIGATION)

NAVIGATE_SKILL = "navigate_to"
NONE_OPTION = "none"

# intent 선택지. 문구는 Laya 의 option-marker scorer 가 읽으므로 짧고 중립적으로 쓴다.
INTENT_CRITERIA: dict[str, str] = {
    "smalltalk": "인사, 감사, 잡담처럼 로봇 동작이나 정보 조회가 필요 없는 말",
    "question": "로봇이 설명이나 판단을 담아 말로 답해야 하는 질문",
    "single_skill": "로봇 기능 하나만 실행하면 처리되는 단일 명령이나 조회",
    "multi_step": "여러 단계나 여러 기능이 필요한 명령",
}

DEFAULT_THRESHOLDS: dict[str, float] = {
    "smalltalk": 0.90,
    "single_skill": 0.85,
    "skill": 0.80,
    "target_place": 0.80,
    # ambiguous(noul) 확률이 이 값 이상이면 System 2 로 넘긴다.
    "ambiguous": 0.50,
}


class System1Unavailable(RuntimeError):
    """System 1 서버를 사용할 수 없음(timeout / HTTP 오류 / 응답 형식 오류 / circuit open)."""


def _env_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_float(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


@dataclass
class System1Config:
    router: str = ROUTER_RULE
    shadow: bool = False
    scope: str = SCOPE_READONLY
    endpoint: str = ""
    provider: str = "laya"
    api_key: str = ""
    timeout_ms: float = 300.0
    thresholds: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    # 비어 있으면 risk_level=read 이고 필수 인자가 없는 공개 스킬 전체가 후보다.
    skills: list[str] = field(default_factory=list)
    max_options: int = 12
    breaker_failures: int = 3
    breaker_cooldown_sec: float = 30.0
    shadow_log: str = ""
    # 같은 컨테이너에서 launch 가 laya-serve 를 함께 띄우는지(SYSTEM1_LOCAL_SERVER)
    local_server: bool = False
    # 기동 시 health check 가 서버 준비를 기다리는 최대 시간(초). 모델 로딩 시간을 흡수한다.
    health_wait_sec: float = 0.0

    @classmethod
    def from_env(cls, env: Any = None) -> System1Config:
        env = os.environ if env is None else env
        cfg = cls()
        router = (env.get("SYSTEM1_ROUTER") or ROUTER_RULE).strip().lower()
        if router not in _VALID_ROUTERS:
            logger.warning("Unknown SYSTEM1_ROUTER=%r — falling back to 'rule'", router)
            router = ROUTER_RULE
        cfg.router = router
        cfg.shadow = _env_bool(env.get("SYSTEM1_SHADOW"))
        scope = (env.get("SYSTEM1_SCOPE") or SCOPE_READONLY).strip().lower()
        if scope not in _VALID_SCOPES:
            logger.warning("Unknown SYSTEM1_SCOPE=%r — falling back to 'readonly'", scope)
            scope = SCOPE_READONLY
        cfg.scope = scope
        cfg.endpoint = (env.get("SYSTEM1_ENDPOINT") or "").strip().rstrip("/")
        cfg.provider = (env.get("SYSTEM1_PROVIDER") or "laya").strip().lower()
        cfg.api_key = (env.get("SYSTEM1_API_KEY") or "").strip()
        cfg.timeout_ms = max(1.0, _env_float(env.get("SYSTEM1_TIMEOUT_MS"), cfg.timeout_ms))
        raw_thresholds = env.get("SYSTEM1_CONF_THRESHOLDS_JSON") or ""
        if raw_thresholds.strip():
            try:
                parsed = json.loads(raw_thresholds)
                if not isinstance(parsed, dict):
                    raise ValueError("not an object")
                cfg.thresholds.update({str(k): float(v) for k, v in parsed.items()})
            except (ValueError, TypeError) as exc:
                logger.warning("Invalid SYSTEM1_CONF_THRESHOLDS_JSON ignored: %s", exc)
        cfg.skills = [s.strip() for s in (env.get("SYSTEM1_SKILLS") or "").split(",") if s.strip()]
        cfg.max_options = max(2, int(_env_float(env.get("SYSTEM1_MAX_OPTIONS"), cfg.max_options)))
        cfg.shadow_log = (env.get("SYSTEM1_SHADOW_LOG") or "").strip()
        cfg.local_server = local_server_enabled(env)
        if cfg.local_server and not cfg.endpoint:
            # 내장 서버를 쓰면 endpoint 를 비워 둬도 loopback 주소로 접속한다.
            cfg.endpoint = local_server_endpoint(env)
        cfg.health_wait_sec = max(
            0.0,
            _env_float(env.get("SYSTEM1_HEALTH_WAIT_SEC"), 120.0 if cfg.local_server else 0.0),
        )
        return cfg


class CircuitBreaker:
    """연속 실패가 ``max_failures`` 에 도달하면 ``cooldown_sec`` 동안 호출을 막는다."""

    def __init__(self, max_failures: int = 3, cooldown_sec: float = 30.0, clock=time.monotonic):
        self._max_failures = max(1, max_failures)
        self._cooldown_sec = cooldown_sec
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if self._clock() - self._opened_at >= self._cooldown_sec:
                # half-open: 한 번 시도해 보고 결과로 닫거나 다시 연다.
                self._opened_at = None
                self._failures = self._max_failures - 1
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._max_failures:
                self._opened_at = self._clock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._opened_at is not None


class SystemOneClient:
    """``POST {endpoint}/v1/systemone`` HTTP 클라이언트 (표준 라이브러리만 사용)."""

    def __init__(self, endpoint: str, timeout_ms: float, api_key: str = ""):
        self._url = f"{endpoint.rstrip('/')}/v1/systemone"
        self._timeout = timeout_ms / 1000.0
        self._api_key = api_key

    def predict_sync(self, state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({"state": state, "questions": questions}, ensure_ascii=False).encode()
        req = urllib.request.Request(self._url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if self._api_key:
            req.add_header("Authorization", f"Bearer {self._api_key}")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise System1Unavailable(f"{type(exc).__name__}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
            raise System1Unavailable("invalid response: missing 'answers'")
        return data

    async def predict(self, state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.predict_sync, state, questions)


def _skill_candidates(node: Any, cfg: System1Config) -> dict[str, str]:
    """Laya 가 단독으로 실행해도 되는 스킬 후보 {name: description}."""
    skills = getattr(node, "_skills", None)
    if skills is None or not hasattr(skills, "list_skills"):
        return {}
    try:
        listed = skills.list_skills(include_internal=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("System1: list_skills failed: %s", exc)
        return {}
    allow = set(cfg.skills)
    out: dict[str, str] = {}
    for item in listed:
        name = str(item.get("name") or "")
        if not name or (allow and name not in allow):
            continue
        if name == NAVIGATE_SKILL:
            if cfg.scope != SCOPE_NAVIGATION:
                continue
        else:
            if item.get("risk_level") != "read":
                continue
            schema = item.get("input_schema") or {}
            if isinstance(schema, dict) and schema.get("required"):
                continue
        out[name] = str(item.get("description") or name)[:160]
    if len(out) > cfg.max_options:
        logger.warning(
            "System1: %d skill options exceed SYSTEM1_MAX_OPTIONS=%d — truncating. "
            "Set SYSTEM1_SKILLS to choose the candidates explicitly.",
            len(out),
            cfg.max_options,
        )
        out = dict(list(out.items())[: cfg.max_options])
    return out


def _place_candidates(node: Any, instruction: str, cfg: System1Config) -> list[str]:
    """navigate_to 목적지 후보(시맨틱 맵의 기억된 장소)."""
    memory = getattr(node, "_memory", None)
    names: list[str] = []
    if memory is not None and hasattr(memory, "get_all_objects"):
        try:
            for obj in memory.get_all_objects():
                meta = obj.get("metadata", {})
                if isinstance(meta, dict) and meta.get("alias_of"):
                    continue
                name = str(obj.get("name") or "").strip()
                if name and name not in names:
                    names.append(name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("System1: place lookup failed: %s", exc)
    if len(names) > cfg.max_options:
        # 후보가 많으면 선택지 정확도가 떨어진다(모델 카드: 고카디널리티 약점).
        # 지시문에 이름이 들어있는 장소로 좁힌다.
        try:
            from robo_claw_agent.memory_manager.semantic import known_place_in_text

            hit = known_place_in_text(instruction, memory)
        except Exception:  # noqa: BLE001
            hit = ""
        names = [hit] if hit else []
    return names


def build_questions(skills: dict[str, str], places: list[str]) -> dict[str, Any]:
    questions: dict[str, Any] = {
        "intent": {
            "type": "choice",
            "instructions": "사용자가 로봇에게 한 말의 종류는?",
            "criteria": dict(INTENT_CRITERIA),
        },
        "ambiguous": {
            "type": "noul",
            "instructions": "무엇을 어디에 해야 하는지 대상이나 장소가 불분명한가?",
        },
    }
    if skills:
        questions["skill"] = {
            "type": "choice",
            "instructions": "이 말을 처리할 로봇 기능은?",
            "criteria": {**skills, NONE_OPTION: "위 기능 중 해당 없음"},
        }
    if places:
        questions["target_place"] = {
            "type": "choice",
            "instructions": "로봇이 이동해야 하는 목적지는?",
            "criteria": {**{p: f"기억된 장소 '{p}'" for p in places}, NONE_OPTION: "목적지 없음"},
        }
    return questions


def _answer(resp: dict[str, Any], key: str) -> dict[str, Any]:
    value = resp.get("answers", {}).get(key)
    return value if isinstance(value, dict) else {}


def _conf(ans: dict[str, Any]) -> float:
    try:
        return float(ans.get("confidence", 0.0))
    except (TypeError, ValueError):
        return 0.0


def interpret_response(
    resp: dict[str, Any],
    skills: dict[str, str],
    places: list[str],
    thresholds: dict[str, float],
    source: str = ROUTER_LAYA,
) -> RouteDecision:
    """System 1 응답을 라우팅 결정으로 변환한다. 확신이 없으면 항상 ``llm``."""
    th = {**DEFAULT_THRESHOLDS, **thresholds}
    intent_ans = _answer(resp, "intent")
    intent = str(intent_ans.get("choice") or "")
    intent_conf = _conf(intent_ans)
    hint: dict[str, Any] = {"intent": intent, "intent_confidence": intent_conf}

    try:
        ambiguous = float(_answer(resp, "ambiguous").get("noul", 0.0))
    except (TypeError, ValueError):
        ambiguous = 0.0
    hint["ambiguous"] = ambiguous

    skill_ans = _answer(resp, "skill")
    skill = str(skill_ans.get("choice") or "")
    skill_conf = _conf(skill_ans)
    if skill:
        hint.update(skill=skill, skill_confidence=skill_conf)
    place_ans = _answer(resp, "target_place")
    place = str(place_ans.get("choice") or "")
    place_conf = _conf(place_ans)
    if place:
        hint.update(target_place=place, target_place_confidence=place_conf)

    def _llm() -> RouteDecision:
        return RouteDecision.to_llm(source=source, intent=intent, confidence=intent_conf, hint=hint)

    if ambiguous >= th["ambiguous"]:
        return _llm()

    if intent == "smalltalk" and intent_conf >= th["smalltalk"]:
        return RouteDecision(
            kind=ROUTE_SIMPLE_REPLY,
            reply=SIMPLE_REPLY_MESSAGE,
            intent=intent,
            confidence=intent_conf,
            source=source,
            hint=hint,
        )

    if (
        intent == "single_skill"
        and intent_conf >= th["single_skill"]
        and skill in skills
        and skill_conf >= th["skill"]
    ):
        params: dict[str, Any] = {}
        if skill == NAVIGATE_SKILL:
            if place not in places or place_conf < th["target_place"]:
                return _llm()
            params = {"target_name": place}
        return RouteDecision(
            kind=ROUTE_DIRECT_SKILL,
            skill=skill,
            params=params,
            intent=intent,
            confidence=min(intent_conf, skill_conf),
            source=source,
            hint=hint,
        )

    return _llm()


class SystemOneRouter:
    """Laya/Jev ``/v1/systemone`` 기반 사전 라우터."""

    def __init__(self, cfg: System1Config, client: SystemOneClient | None = None):
        self.cfg = cfg
        self.name = cfg.provider or ROUTER_LAYA
        self._client = client or SystemOneClient(cfg.endpoint, cfg.timeout_ms, cfg.api_key)
        self.breaker = CircuitBreaker(cfg.breaker_failures, cfg.breaker_cooldown_sec)

    async def decide(self, node: Any, instruction: str) -> RouteDecision:
        # 가드(그룹 B'): 복합 명령을 단일 스킬로 축약하면 나머지 절이 사라진다.
        from .task_planner import looks_compound

        if looks_compound(instruction):
            return RouteDecision.to_llm(source=self.name, intent="multi_step")

        if not self.breaker.allow():
            raise System1Unavailable("circuit open")

        skills = _skill_candidates(node, self.cfg)
        places = _place_candidates(node, instruction, self.cfg) if NAVIGATE_SKILL in skills else []
        if NAVIGATE_SKILL in skills and not places:
            skills = {k: v for k, v in skills.items() if k != NAVIGATE_SKILL}
        questions = build_questions(skills, places)
        state = {"instruction": instruction}

        start = time.perf_counter()
        try:
            resp = await self._client.predict(state, questions)
        except System1Unavailable:
            self.breaker.record_failure()
            raise
        self.breaker.record_success()
        decision = interpret_response(resp, skills, places, self.cfg.thresholds, self.name)
        decision.latency_ms = (time.perf_counter() - start) * 1000.0
        return decision

    def health_check(
        self, log: Any = None, *, sleep=time.sleep, clock=time.monotonic, interval_sec: float = 3.0
    ) -> bool:
        """서버 응답을 확인한다. ``health_wait_sec`` 동안은 준비될 때까지 재시도한다.

        기다리는 동안의 실패는 circuit breaker 에 반영하지 않는다(모델 로딩 중은 장애가 아님).
        그동안 들어온 요청은 평소처럼 실패 시 RuleRouter 로 대체된다.
        """
        log = log or logger
        deadline = clock() + self.cfg.health_wait_sec
        start = clock()
        while True:
            try:
                self._client.predict_sync(
                    {"instruction": "안녕"},
                    {"intent": build_questions({}, [])["intent"]},
                )
            except System1Unavailable as exc:
                if clock() + interval_sec <= deadline:
                    sleep(interval_sec)
                    continue
                log.warning(f"System1 health check failed ({self.cfg.endpoint}): {exc}")
                self.breaker.record_failure()
                return False
            if self.cfg.health_wait_sec > 0:
                log.info(f"System1 server ready ({self.cfg.endpoint}) after {clock() - start:.0f}s")
            return True


class SelectedRouter:
    """배타 선택 라우터. primary 하나만 판단하고 fallback 은 장애 시에만 쓴다."""

    def __init__(
        self,
        primary: FastRouter,
        fallback: RuleRouter | None = None,
        shadow: FastRouter | None = None,
        shadow_log: str = "",
    ):
        self.primary = primary
        self.fallback = fallback
        self.shadow = shadow
        self.shadow_log = shadow_log
        self.name = primary.name
        self._shadow_lock = threading.Lock()

    async def decide(self, node: Any, instruction: str) -> RouteDecision:
        try:
            decision = await self.primary.decide(node, instruction)
        except System1Unavailable as exc:
            if self.fallback is None:
                raise
            node.get_logger().warning(
                f"System1 router '{self.primary.name}' unavailable ({exc}) — using rule router"
            )
            start = time.perf_counter()
            decision = self.fallback.decide_sync(node, instruction)
            decision.fallback = True
            decision.latency_ms = (time.perf_counter() - start) * 1000.0
        if self.shadow is not None:
            self._start_shadow(node, instruction, decision)
        return decision

    def _start_shadow(self, node: Any, instruction: str, primary: RouteDecision) -> None:
        def _work() -> None:
            try:
                shadow = asyncio.run(self.shadow.decide(node, instruction))
                record = {"error": None, **_decision_record(shadow)}
            except Exception as exc:  # noqa: BLE001
                record = {"error": f"{type(exc).__name__}: {exc}"}
            entry = {
                "ts": time.time(),
                "instruction": instruction,
                "primary": _decision_record(primary),
                "shadow": record,
                "agree": record.get("kind") == primary.kind
                and record.get("skill", "") == primary.skill,
            }
            node.get_logger().info(f"System1 shadow: {json.dumps(entry, ensure_ascii=False)}")
            if self.shadow_log:
                try:
                    with self._shadow_lock, open(self.shadow_log, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except OSError as exc:
                    node.get_logger().warning(f"System1 shadow log write failed: {exc}")

        threading.Thread(target=_work, name="system1-shadow", daemon=True).start()


def _decision_record(d: RouteDecision) -> dict[str, Any]:
    return {
        "kind": d.kind,
        "skill": d.skill,
        "params": d.params,
        "intent": d.intent,
        "confidence": d.confidence,
        "source": d.source,
        "fallback": d.fallback,
        "latency_ms": round(d.latency_ms, 1),
    }


def build_router(
    cfg: System1Config, *, health_check: bool = True, log: Any = None
) -> SelectedRouter:
    """설정에 따라 배타 선택 라우터를 만든다.

    ``log`` 에 ROS 노드 로거를 넘기면 구성 결과와 경고를 노드 로그로 남긴다(ROS 노드에서는
    모듈 로거의 INFO 가 출력되지 않는다).
    """
    log = log or logger
    rule = RuleRouter()
    if cfg.router == ROUTER_LAYA and not cfg.endpoint:
        log.warning("SYSTEM1_ROUTER=laya but SYSTEM1_ENDPOINT is empty — using rule router")
        cfg.router = ROUTER_RULE

    if cfg.router == ROUTER_LAYA:
        system1 = SystemOneRouter(cfg)
        if health_check:
            threading.Thread(
                target=system1.health_check, args=(log,), name="system1-health", daemon=True
            ).start()
        router = SelectedRouter(
            system1, fallback=rule, shadow=rule if cfg.shadow else None, shadow_log=cfg.shadow_log
        )
    else:
        shadow: FastRouter | None = None
        if cfg.shadow and cfg.endpoint:
            shadow = SystemOneRouter(cfg)
        elif cfg.shadow:
            log.warning("SYSTEM1_SHADOW=true but SYSTEM1_ENDPOINT is empty — shadow disabled")
        router = SelectedRouter(rule, shadow=shadow, shadow_log=cfg.shadow_log)

    log.info(
        f"System1 router: router={cfg.router} shadow={cfg.shadow} "
        f"scope={cfg.scope} endpoint={cfg.endpoint or '-'} local_server={cfg.local_server}"
    )
    return router
