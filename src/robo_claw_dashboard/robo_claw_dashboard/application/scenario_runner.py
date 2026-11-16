"""시나리오 테스트 실행기 — 실행 세션 상태·스레드·보고서 조합.

설정 검증(scenario_config), 원격 조회·캐시(scenario_remote), step 핸들러(scenario_steps)를
조합해 실행 상태를 관리한다. 기존 import 경로 호환을 위해 공개 이름을 재노출한다.

rclpy 및 ROS 메시지 직접 import 금지. backend 프로토콜 및 순수 Python 표준 라이브러리로만
구현되어, 테스트에서 가짜 백엔드로 격리 검증이 가능하다.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..domain.scenario_models import (
    Scenario,
    ScenarioTestCase,
    StepResult,
    parse_scenario_json,
)
from ..domain.scenario_report import (
    build_markdown_report,
    summarize_steps,
)
from .scenario_config import (
    USER_CONFIG_KEYS,
    ScenarioConfig,
    host_of,
    missing_config_warning,
    normalize_host_entry,
    parse_user_server_config,
    resolve_cli_config_defaults,
)
from .scenario_remote import default_http_fetcher, load_scenario
from .scenario_steps import ScenarioStepExecutor

logger = logging.getLogger(__name__)

__all__ = [
    "USER_CONFIG_KEYS",
    "ScenarioConfig",
    "ScenarioRunnerService",
    "normalize_host_entry",
    "parse_user_server_config",
    "resolve_cli_config_defaults",
]


class ScenarioRunnerService:
    """시나리오 테스트 관리 및 실행 서비스."""

    def __init__(
        self,
        backend: Any,
        config: ScenarioConfig | None = None,
        *,
        http_fetcher: Callable[[str, dict[str, str], float], tuple[int, bytes]] | None = None,
    ) -> None:
        self._backend = backend
        self._config = config or ScenarioConfig()
        self._http_fetcher = http_fetcher or default_http_fetcher
        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        self._state = "IDLE"  # "IDLE" | "RUNNING" | "COMPLETED" | "CANCELLED" | "FAILED"
        self._run_id: str = ""
        self._run_scenario_name: str = ""
        self._started_at: str = ""
        self._finished_at: str = ""
        self._current_step_id: str = ""
        self._step_results: list[StepResult] = []
        self._current_scenario: Scenario | None = None
        self._last_report_md: str = ""
        self._last_load_warning: str = ""
        #: 대시보드 UI 에서 입력한 AI Config 서버 설정 (없으면 startup 설정 사용)
        self._user_config: dict[str, str] | None = None
        #: step 타입별 검증은 scenario_steps 가 소유한다. 핸들러는 유효 설정을 따른다.
        self._steps = ScenarioStepExecutor(backend, self._effective_config)

    def _effective_config(self) -> ScenarioConfig:
        """startup 설정에 대시보드 입력값을 덮어쓴 유효 설정을 반환한다.

        URL/robot/environment 가 바뀌면 캐시 디렉터리도 그 조합으로 다시 계산해야
        하므로 ``cache_dir`` 은 비워 둔 뒤 dataclass 초기화에서 다시 채운다.
        """
        base = self._config
        user = self._user_config
        if not user:
            return base
        return ScenarioConfig(
            robot_name=user.get("robot_name", ""),
            environment=user.get("environment", ""),
            config_server_url=user.get("config_server_url", ""),
            device_token=user.get("device_token", ""),
            scenario_file=base.scenario_file,
            cache_dir="",
            robot_soul_file=base.robot_soul_file,
            robot_limits_file=base.robot_limits_file,
            agent_workspace_dir=base.agent_workspace_dir,
            llm_info=base.llm_info,
        )

    def _identity(self) -> tuple[str, str]:
        """보고서/결과 라벨에 쓰는 (robot_name, environment) 를 반환한다."""
        cfg = self._effective_config()
        return cfg.robot_name or "default", cfg.environment or "default"

    def config_summary(self) -> dict[str, Any]:
        """현재 유효한 AI Config 서버 설정을 토큰 없이 요약한다."""
        cfg = self._effective_config()
        return {
            "config_server_url": cfg.config_server_url,
            "robot_name": cfg.robot_name,
            "environment": cfg.environment,
            "device_token_set": bool(cfg.device_token),
            "cache_dir": cfg.cache_dir,
            "missing_warning": missing_config_warning(cfg),
        }

    def sync_scenario(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """대시보드에서 입력한 서버 설정으로 시나리오를 동기화한다.

        검증에 실패하면 400 과 함께 오류 메시지를 반환하고, 기존 설정은 유지한다.
        디바이스 토큰은 응답에 포함하지 않는다.

        이 메서드만 사용자 입력 호스트로 직접 HTTP 요청을 보내므로, 허용 목록과
        링크로컬·메타데이터 차단을 통과한 URL 만 수용한다.
        """
        values, error = parse_user_server_config(
            payload or {}, allowed_hosts=self.allowed_config_hosts()
        )
        if error:
            return {"error": error, "status_code": 400}
        with self._lock:
            self._user_config = values
        return self.get_scenario(refresh=True)

    def allowed_config_hosts(self) -> frozenset[str]:
        """사용자 입력 URL 에 허용되는 ``host:port`` 집합을 반환한다.

        설정된 허용 목록에 startup 설정의 AI Config 서버 호스트를 항상 더한다.
        startup 설정만으로 운영되던 기존 배포가 그대로 동작하게 하기 위해서다.
        """
        hosts = set(self._config.allowed_config_hosts)
        startup_host = host_of(self._config.config_server_url)
        if startup_host:
            hosts.add(startup_host)
        return frozenset(hosts)

    def _format_scenario_response(
        self,
        scenario: Scenario,
        warning: str = "",
        *,
        cfg: ScenarioConfig | None = None,
    ) -> dict[str, Any]:
        effective = cfg or self._effective_config()
        return {
            "scenario": scenario.to_dict(),
            "source": scenario.source,
            "robot_name": effective.robot_name or "default",
            "environment": effective.environment or "default",
            "config": {
                "config_server_url": effective.config_server_url,
                "robot_name": effective.robot_name,
                "environment": effective.environment,
                "device_token_set": bool(effective.device_token),
                "cache_dir": effective.cache_dir,
            },
            "warning": warning,
        }

    def get_scenario(self, *, refresh: bool = False) -> dict[str, Any]:
        """시나리오를 우선순위(파일 -> 원격 서버 -> 로컬 캐시 -> 기본)에 따라 조회한다."""
        with self._lock:
            cfg = self._effective_config()

            if not refresh and self._current_scenario is not None:
                return self._format_scenario_response(
                    self._current_scenario, warning=self._last_load_warning, cfg=cfg
                )

            loaded = load_scenario(
                cfg, fetcher=self._http_fetcher, previous_warning=self._last_load_warning
            )
            self._current_scenario = loaded.scenario
            self._last_load_warning = loaded.stored_warning
            return self._format_scenario_response(loaded.scenario, warning=loaded.warning, cfg=cfg)

    def start_run(
        self,
        payload: dict[str, Any] | None = None,
        *,
        async_mode: bool = True,
    ) -> dict[str, Any]:
        """테스트 시나리오 실행을 시작한다."""
        with self._lock:
            if self._state == "RUNNING":
                return {
                    "accepted": False,
                    "status_code": 409,
                    "error": "이미 실행 중인 테스트 시나리오가 있습니다.",
                }

            payload = payload or {}

            # 커스텀 시나리오 주입 확인
            if "scenario" in payload and payload["scenario"] is not None:
                sc, err = parse_scenario_json(payload["scenario"], source="custom")
                if sc is None:
                    return {"accepted": False, "status_code": 400, "error": err}
                scenario = sc
                self._current_scenario = sc
            else:
                if self._current_scenario is None:
                    self.get_scenario(refresh=False)
                assert self._current_scenario is not None
                scenario = Scenario(
                    name=self._current_scenario.name,
                    description=self._current_scenario.description,
                    test_cases=[
                        ScenarioTestCase.from_dict(tc.to_dict())
                        for tc in self._current_scenario.test_cases
                    ],
                    source=self._current_scenario.source,
                )

            # 실행 옵션 적용
            run_navigation = bool(payload.get("run_navigation", False))
            run_manipulation = bool(payload.get("run_manipulation", False))
            enabled_ids = payload.get("enabled_ids")

            if enabled_ids is not None:
                enabled_ids_set = set(enabled_ids) if isinstance(enabled_ids, list) else set()
                for tc in scenario.test_cases:
                    tc.enabled = tc.id in enabled_ids_set
                    if tc.type == "navigation" and not run_navigation:
                        tc.enabled = False
                    elif tc.type == "manipulation" and not run_manipulation:
                        tc.enabled = False
            else:
                for tc in scenario.test_cases:
                    if tc.type == "navigation" and not run_navigation:
                        tc.enabled = False
                    elif tc.type == "manipulation" and not run_manipulation:
                        tc.enabled = False

            now_utc = datetime.now(timezone.utc)
            run_id = f"run_{now_utc.strftime('%Y%m%d_%H%M%S')}"
            self._run_id = run_id
            self._run_scenario_name = scenario.name
            self._started_at = now_utc.isoformat()
            self._finished_at = ""
            self._current_step_id = ""
            self._state = "RUNNING"
            self._cancel_event.clear()
            self._last_report_md = ""
            self._step_results = [
                StepResult(
                    id=tc.id,
                    step=tc.step,
                    name=tc.name,
                    type=tc.type,
                    status="PENDING" if tc.enabled else "SKIP",
                    detail="" if tc.enabled else "비활성화 처리됨",
                )
                for tc in scenario.test_cases
            ]

        if async_mode:
            t = threading.Thread(target=self._execute_run, args=(scenario,), daemon=True)
            t.start()
            return {
                "accepted": True,
                "status_code": 202,
                "run_id": run_id,
                "status": "RUNNING",
                "state": "RUNNING",
            }

        self._execute_run(scenario)
        return {
            "accepted": True,
            "status_code": 200,
            "run_id": run_id,
            "status": self._state,
            "state": self._state,
        }

    def _persist_report_unlocked(self) -> None:
        """생성된 마크다운 보고서를 agent_workspace_dir/reports 에 최선의 노력으로 저장한다."""
        if not self._last_report_md or not self._config.agent_workspace_dir:
            return
        try:
            reports_dir = Path(self._config.agent_workspace_dir) / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            prefix = (
                f"test_report_{self._config.robot_name or 'robot'}_"
                f"{self._config.environment or 'env'}_{self._run_id}"
            )
            (reports_dir / f"{prefix}.md").write_text(self._last_report_md, encoding="utf-8")
        except Exception as exc:
            logger.debug("보고서 파일 디스크 저장 실패: %s", exc)

    def cancel_run(self) -> dict[str, Any]:
        """진행 중인 테스트 시나리오를 즉시 중단한다."""
        with self._lock:
            if self._state != "RUNNING":
                return {
                    "cancelled": False,
                    "status": self._state,
                    "state": self._state,
                    "error": "실행 중인 시나리오가 없습니다.",
                }
            self._cancel_event.set()
            self._state = "CANCELLED"
            for s in self._step_results:
                if s.status in ("PENDING", "RUNNING"):
                    s.status = "SKIP"
                    s.detail = "사용자 요청으로 중단됨"
            self._finished_at = datetime.now(timezone.utc).isoformat()
            self._current_step_id = ""
            robot_label, env_label = self._identity()
            self._last_report_md = build_markdown_report(
                scenario_name=self._run_scenario_name,
                robot_name=robot_label,
                environment=env_label,
                run_id=self._run_id,
                started_at=self._started_at,
                finished_at=self._finished_at,
                status=self._state,
                steps=self._step_results,
                llm_info=self._config.llm_info,
            )
            self._persist_report_unlocked()
            return {"cancelled": True, "status": "CANCELLED", "state": "CANCELLED"}

    def status(self) -> dict[str, Any]:
        """현재 실행 상태 및 스텝별 결과를 반환한다."""
        with self._lock:
            summary = summarize_steps(self._step_results)
            total_steps = summary["total"]
            finished = summary["pass"] + summary["fail"] + summary["warning"] + summary["skip"]
            progress_percent = round((finished / total_steps) * 100) if total_steps > 0 else 0
            return {
                "run_id": self._run_id,
                "scenario_name": self._run_scenario_name,
                "status": self._state,
                "state": self._state,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "current_step_id": self._current_step_id,
                "total_steps": total_steps,
                "progress_percent": progress_percent,
                "summary": summary,
                "steps": [s.to_dict() for s in self._step_results],
                "step_results": [s.to_dict() for s in self._step_results],
                "has_report": bool(self._last_report_md),
            }

    def get_report(self, fmt: str = "md") -> dict[str, Any]:
        """완료된 테스트 보고서를 지정된 형식(md 또는 json)으로 생성해 반환한다."""
        with self._lock:
            if not self._last_report_md:
                return {"found": False, "status_code": 404, "error": "생성된 보고서가 없습니다."}
            prefix = f"test_report_{self._identity()[0]}_{self._identity()[1]}_{self._run_id}"
            if fmt == "json":
                return {
                    "found": True,
                    "status_code": 200,
                    "content_type": "application/json; charset=utf-8",
                    "filename": f"{prefix}.json",
                    "body": json.dumps(self.status(), ensure_ascii=False, indent=2).encode("utf-8"),
                }
            return {
                "found": True,
                "status_code": 200,
                "content_type": "text/markdown; charset=utf-8",
                "filename": f"{prefix}.md",
                "body": self._last_report_md.encode("utf-8"),
            }

    def _execute_run(self, scenario: Scenario) -> None:
        """스레드 상에서 전체 시나리오를 단계별로 순차 실행한다."""
        for idx, tc in enumerate(scenario.test_cases):
            if self._cancel_event.is_set():
                break
            if not tc.enabled:
                continue

            with self._lock:
                self._current_step_id = tc.id
                self._step_results[idx].status = "RUNNING"

            res = self._execute_step(tc)

            with self._lock:
                self._step_results[idx].status = res.status
                self._step_results[idx].detail = res.detail
                self._step_results[idx].duration_ms = res.duration_ms
                self._step_results[idx].image_path = res.image_path
                if self._cancel_event.is_set():
                    break

        with self._lock:
            if self._cancel_event.is_set():
                self._state = "CANCELLED"
                for s in self._step_results:
                    if s.status in ("PENDING", "RUNNING"):
                        s.status = "SKIP"
                        s.detail = "사용자 요청으로 중단됨"
            else:
                summary = summarize_steps(self._step_results)
                if summary["fail"] > 0:
                    self._state = "FAILED"
                else:
                    self._state = "COMPLETED"

            self._finished_at = datetime.now(timezone.utc).isoformat()
            self._current_step_id = ""
            robot_label, env_label = self._identity()
            self._last_report_md = build_markdown_report(
                scenario_name=self._run_scenario_name,
                robot_name=robot_label,
                environment=env_label,
                run_id=self._run_id,
                started_at=self._started_at,
                finished_at=self._finished_at,
                status=self._state,
                steps=self._step_results,
                llm_info=self._config.llm_info,
            )
            self._persist_report_unlocked()

    def _execute_step(self, tc: ScenarioTestCase) -> StepResult:
        """개별 테스트 케이스 검증을 step 실행기에 위임하고 소요시간을 기록한다."""
        return self._steps.execute(tc)
