"""SkillManager — 스킬 플러그인 동적 로드/실행 관리자.

``BaseSkill``/``SkillResult`` 등 실행 계약 타입은 ``skills/base.py`` 에 있다.
"""

import importlib
import inspect
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import TYPE_CHECKING, Any, Optional

from ..observability import OperationTracker
from ..tracing import trace_process_inputs, trace_process_outputs, traceable
from .base import (
    BaseSkill,
    SkillResult,
    _standard_failure_data,
    _TimeoutSentinel,
    _trace_skill_preconditions,
    _trace_skill_schema,
)

if TYPE_CHECKING:
    from robo_claw_agent.agent_node import AgentNode

logger = logging.getLogger(__name__)


class SkillManager:
    """
    스킬 플러그인 동적 로드 및 실행 관리자.
    - 모듈 경로 기반으로 스킬 로드
    - 이름 기반 스킬 실행
    - 타임아웃 처리
    """

    def __init__(self, node: Optional["AgentNode"] = None) -> None:
        self._skills: dict[str, BaseSkill] = {}
        self._node = node
        self.mock_robot = os.environ.get("RC_MOCK_ROBOT", "").strip().lower() == "true"
        self._metrics = OperationTracker()
        self._allowed_skills: set[str] = set()
        self._blocked_skills: set[str] = set()
        self._active_executions: dict[str, dict[str, Any]] = {}
        self._active_lock = threading.Lock()
        # 스킬 성공 후처리 훅. 스킬 계층이 주행 안전 규칙을 알지 않도록 주입식으로 둔다.
        # 예: 팔을 움직인 스킬이 성공하면 nav_safety 의 주행 전 자세 준비 상태를 지운다.
        self._post_skill_success_hook: Callable[[BaseSkill], None] | None = None
        # 재사용 가능한 작업 스레드 풀 — 매 호출마다 스레드 생성/소멸 오버헤드를 줄이고 중첩 스킬 호출 데드락 방지
        self._executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="skill-exec")

    def set_post_skill_success_hook(self, hook: Callable[["BaseSkill"], None] | None) -> None:
        """스킬이 성공한 뒤 호출할 후처리 훅을 등록한다(주행 안전 상태 무효화 등).

        훅은 스킬 실행/정리 흐름을 방해해선 안 되므로 예외를 모두 삼킨다.
        """
        self._post_skill_success_hook = hook

    def set_skill_policy(
        self, allowed: list[str] | None = None, blocked: list[str] | None = None
    ) -> None:
        """스킬 허용/차단 정책을 설정한다.

        모든 실행 경로(HTTP /task, /skill, gRPC, 메신저, ROS service)에서
        공통으로 적용된다. allowed가 비어있으면 모든 스킬 허용.
        """
        self._allowed_skills = {s.strip() for s in (allowed or []) if s.strip()}
        self._blocked_skills = {s.strip() for s in (blocked or []) if s.strip()}
        logger.info(
            "Skill policy configured: allowed=%s, blocked=%s",
            sorted(self._allowed_skills) or "(all allowed)",
            sorted(self._blocked_skills) or "(none blocked)",
        )

    @traceable(
        run_type="chain",
        name="skill_policy_check",
        process_inputs=trace_process_inputs,
        process_outputs=trace_process_outputs,
    )
    def _check_skill_policy(self, skill_name: str) -> str | None:
        """스킬 정책 위반 여부를 검사한다.

        Returns:
            None if OK, 오류 메시지 if 위반.
        """
        normalized = skill_name.strip()
        if normalized in self._blocked_skills:
            return f"정책에 의해 차단된 스킬입니다: {normalized}"
        if self._allowed_skills and normalized not in self._allowed_skills:
            return f"스킬 허용 목록에 없는 스킬입니다: {normalized}"
        return None

    def _node_manipulation_backend(self) -> str:
        """노드의 manipulation_backend 파라미터를 안전하게 읽는다.

        requires_manipulation_backend 게이팅의 기준값. 노드가 없거나 파라미터 읽기에
        실패하면 기본값 ``"moveit"`` 을 반환한다.
        """
        node = self._node
        if node is None:
            return "moveit"
        try:
            param = node.get_parameter("manipulation_backend")
            value = getattr(param, "value", None)
        except Exception:
            return "moveit"
        if value is None:
            return "moveit"
        text = str(value).strip().lower()
        return text or "moveit"

    def register(self, skill: BaseSkill) -> None:
        """스킬 인스턴스를 직접 등록"""
        if self._node:
            skill.set_node(self._node)
        self._skills[skill.name] = skill
        logger.info("Skill registered: %s", skill.name)

    def load_from_module(self, module_path: str) -> None:
        """
        모듈 경로에서 BaseSkill 서브클래스를 모두 로드.

        Args:
            module_path: 예) "robo_claw_agent.skills.navigation_skill"
        """
        try:
            module = importlib.import_module(module_path)
            logger.info("Module loaded successfully: %s", module_path)
        except Exception as e:
            logger.error("!!! Fatal error loading skill module [%s]: %s !!!", module_path, e)
            return

        found_count = 0
        node_backend = self._node_manipulation_backend()
        for _name, cls in inspect.getmembers(module, inspect.isclass):
            if (
                inspect.isclass(cls)
                and issubclass(cls, BaseSkill)
                and cls is not BaseSkill
                and hasattr(cls, "name")
                and cls.name
                and getattr(cls, "enabled", True)
            ):
                required_backend = getattr(cls, "requires_manipulation_backend", None)
                if required_backend and required_backend != node_backend:
                    logger.info(
                        "  -> Skipping skill registration: %s (requires backend '%s', current '%s')",
                        cls.name,
                        required_backend,
                        node_backend,
                    )
                    continue
                logger.info("  -> Registering skill: %s", cls.name)
                self.register(cls())
                found_count += 1

        if found_count == 0:
            logger.warning("No registrable skill classes found in module [%s].", module_path)

    @traceable(
        run_type="tool",
        name="SkillManager.execute",
        tags=["robot-skill"],
        process_inputs=trace_process_inputs,
        process_outputs=trace_process_outputs,
    )
    def execute(
        self,
        skill_name: str,
        params: dict[str, Any] | None = None,
        timeout_sec: float = 30.0,
    ) -> SkillResult:
        """
        스킬 이름으로 실행.

        Args:
            skill_name: 등록된 스킬 이름
            params: 실행 파라미터
            timeout_sec: 타임아웃 (초). 0 이하면 무제한.

        Returns:
            SkillResult

        Notes:
            스레드 기반 timeout은 응답을 즉시 반환하지만, 스킬 내부 작업이
            강제 중단되지는 않는다. ROS action 기반 skill(navigation 등)은
            별도 cancel 로직을 통해 실제 동작을 멈춰야 한다.
        """
        params = params or {}

        skill = self._skills.get(skill_name)
        if skill is None:
            logger.error("Skill does not exist: %s", skill_name)
            return SkillResult(
                skill_name,
                False,
                f"스킬 없음: {skill_name}",
                0.0,
                _standard_failure_data("skill_not_found"),
            )

        mock_execution = self.mock_robot and self._requires_robot(skill)
        early_failure = self._validate_execution(
            skill_name, skill, params, mock_execution=mock_execution
        )
        if early_failure is not None:
            return early_failure

        if mock_execution:
            message = f"[MOCK] {skill_name} 가짜 성공입니다. 실제 로봇 동작은 실행하지 않았습니다."
            logger.warning("%s params=%s", message, params)
            return SkillResult(
                skill_name,
                True,
                message,
                0.0,
                {
                    "success": True,
                    "message": message,
                    "status": "completed",
                    "mocked": True,
                    "executed": False,
                    "params": params,
                },
            )

        skill.set_timeout_hint(timeout_sec if timeout_sec and timeout_sec > 0 else 0.0)

        logger.info("Starting skill execution: %s (params=%s)", skill_name, params)
        start = time.monotonic()
        with self._active_lock:
            self._active_executions[skill_name] = {
                "skill_name": skill_name,
                "status": "running",
                "started_at_monotonic": start,
            }

        result_data = self._run_with_retries(skill, skill_name, params, timeout_sec)
        return self._finalize_execution(skill_name, skill, params, result_data, start, timeout_sec)

    @staticmethod
    def _requires_robot(skill: "BaseSkill") -> bool:
        # 로컬 파일/시간/RAG 이외의 스킬과 외부 플러그인은 보수적으로 대체합니다.
        # 현재 위치 조회 및 임의 스크립트는 로봇에 접근할 수 있으므로 예외입니다.
        local_modules = (
            "robo_claw_agent.skills.system_skill.datetime",
            "robo_claw_agent.skills.system_skill.rag",
            "robo_claw_agent.skills.system_skill.learning",
            "robo_claw_agent.skills.file_skill",
        )
        module = type(skill).__module__
        return skill.name in {"get_location", "identify_location", "run_script"} or not any(
            module == prefix or module.startswith(prefix + ".") for prefix in local_modules
        )

    def _validate_execution(
        self,
        skill_name: str,
        skill: "BaseSkill",
        params: dict[str, Any],
        *,
        mock_execution: bool = False,
    ) -> SkillResult | None:
        """정책·사전조건·스키마·파라미터 검증. 실패 시 SkillResult, 통과 시 None."""
        # 공통 스킬 정책 검사 (HTTP, gRPC, 메신저, ROS service 공통)
        policy_error = self._check_skill_policy(skill_name)
        if policy_error:
            logger.warning("Skill policy violation: %s -> %s", skill_name, policy_error)
            return SkillResult(
                skill_name,
                False,
                policy_error,
                0.0,
                _standard_failure_data("safety_rejected"),
            )

        preconditions_valid, precondition_error = (
            (True, "") if mock_execution else _trace_skill_preconditions(skill_name, skill, params)
        )
        if not preconditions_valid:
            logger.warning("Skill precondition failed: %s: %s", skill_name, precondition_error)
            return SkillResult(
                skill_name,
                False,
                f"실행 전 조건 미충족: {precondition_error}",
                0.0,
                {
                    **_standard_failure_data("precondition_failed"),
                    "error_type": "SkillPreconditionError",
                    "precondition_error": precondition_error,
                    "preconditions": list(getattr(skill, "preconditions", ())),
                    "params": params,
                },
            )

        schema_valid, schema_error = _trace_skill_schema(skill_name, skill, params)
        if not schema_valid:
            logger.error("Input schema validation failed: %s: %s", skill_name, schema_error)
            return SkillResult(
                skill_name,
                False,
                f"파라미터 형식 오류: {schema_error}",
                0.0,
                {
                    **_standard_failure_data("invalid_input"),
                    "error_type": "ParameterSchemaError",
                    "schema_error": schema_error,
                    "params": params,
                },
            )

        if not skill.validate_params(params):
            logger.error("Parameter validation failed: %s", skill_name)
            return SkillResult(
                skill_name,
                False,
                "파라미터 유효성 검사 실패",
                0.0,
                _standard_failure_data("invalid_input"),
            )
        return None

    def _run_with_retries(
        self,
        skill: "BaseSkill",
        skill_name: str,
        params: dict[str, Any],
        timeout_sec: float,
    ) -> dict[str, Any] | _TimeoutSentinel:
        """로컬 재시도 예산(max_retries) 안에서 스킬을 실행한다."""
        max_retries = max(0, getattr(skill, "max_retries", 0))
        max_attempts = max_retries + 1
        retry_delay = max(0.0, getattr(skill, "retry_delay_sec", 0.5))

        result_data: dict[str, Any] | _TimeoutSentinel = {}
        for attempt in range(1, max_attempts + 1):
            if timeout_sec and timeout_sec > 0:
                result_data = self._execute_with_timeout(
                    skill, params, timeout_sec, attempt=attempt
                )
            else:
                result_data = self._execute_direct(skill, params, attempt=attempt)

            if isinstance(result_data, _TimeoutSentinel):
                if attempt < max_attempts:
                    logger.warning(
                        "Skill %s timed out (attempt %d/%d). Retrying in %.1fs...",
                        skill_name,
                        attempt,
                        max_attempts,
                        retry_delay,
                    )
                    time.sleep(retry_delay)
                    continue
                break

            success = result_data.get("success", True)
            if success or attempt == max_attempts:
                break

            logger.warning(
                "Skill %s failed (attempt %d/%d): %s. Retrying in %.1fs...",
                skill_name,
                attempt,
                max_attempts,
                result_data.get("message"),
                retry_delay,
            )
            time.sleep(retry_delay)
        return result_data

    def _finalize_execution(
        self,
        skill_name: str,
        skill: "BaseSkill",
        params: dict[str, Any],
        result_data: dict[str, Any] | _TimeoutSentinel,
        start: float,
        timeout_sec: float,
    ) -> SkillResult:
        """타임아웃/성공/실패 결과를 정규화하고 메트릭·recover·active 상태를 정리한다."""
        duration = time.monotonic() - start

        if isinstance(result_data, _TimeoutSentinel):
            self._metrics.record(
                skill_name,
                success=False,
                duration_sec=duration,
                error=f"timeout ({timeout_sec}s)",
            )
            logger.error("Skill timed out: %s (%.1fs)", skill_name, timeout_sec)
            try:
                skill.recover(params, f"스킬 실행 타임아웃 ({timeout_sec}s)")
            except Exception as e:  # noqa: BLE001
                logger.warning("Error during skill recover: %s", e)
            with self._active_lock:
                if skill_name in self._active_executions:
                    self._active_executions[skill_name]["status"] = "unknown_after_timeout"
            return SkillResult(
                skill_name,
                False,
                f"스킬 실행 타임아웃 ({timeout_sec}s)",
                duration,
                {
                    "success": False,
                    "message": f"스킬 실행 타임아웃 ({timeout_sec}s)",
                    "status": "timeout",
                    "failure_reason": "timeout",
                    "recoverable": False,
                    "cancel_requested": True,
                    "cancel_confirmed": result_data.cancel_confirmed,
                    "error_type": "TimeoutError",
                },
            )

        success = result_data.get("success", True)
        message = result_data.get("message", "완료")
        result_data.setdefault("failure_reason", "" if success else "execution_failed")
        result_data.setdefault("recoverable", False)
        # 백그라운드/장기 실행 스킬은 호출 수락과 작업 완료를 구분한다.
        # 기존 스킬 구현을 모두 수정하지 않고 공통 결과 계층에서 상태를 보완한다.
        if getattr(skill, "terminal_behavior", "terminal") == "background":
            # background 스킬의 execute() 반환은 "호출 수락" 결과다. 스킬이 status 를
            # 명시하지 않아도(또는 success_result() 기본값 status="completed" 를 써도)
            # 호출 수락/시작 실패를 구분하도록 공통 계층에서 확정한다.
            result_data["status"] = "started" if success else "failed_to_start"
        else:
            result_data.setdefault("status", "completed" if success else "failed")
        declared_postconditions: tuple[str, ...] = tuple(getattr(skill, "postconditions", ()))
        if declared_postconditions:
            # 선언적 postcondition의 실제 판정은 스킬이 결과에 덮어쓸 수 있다.
            # 기본값은 성공한 호출이 조건을 보고했다는 뜻이 아니라, 검증 대기 상태다.
            result_data.setdefault(
                "postconditions",
                {
                    condition: (
                        "pending"
                        if result_data.get("status") == "started"
                        else ("satisfied" if success else "not_satisfied")
                    )
                    for condition in declared_postconditions
                },
            )
        self._metrics.record(skill_name, success=success, duration_sec=duration)
        logger.info("Skill completed: %s (%.2fs)", skill_name, duration)

        if success and self._post_skill_success_hook is not None:
            try:
                self._post_skill_success_hook(skill)
            except Exception as e:  # noqa: BLE001
                logger.warning("Post-skill success hook failed for %s: %s", skill_name, e)

        if not success:
            try:
                skill.recover(params, message)
            except Exception as e:  # noqa: BLE001
                logger.warning("Error during skill recover: %s", e)

        with self._active_lock:
            self._active_executions.pop(skill_name, None)
        return SkillResult(skill_name, success, message, duration, result_data)

    def active_executions(self) -> list[dict[str, Any]]:
        """현재 SkillManager가 실행 중인 스킬의 안전한 스냅샷을 반환한다."""
        now = time.monotonic()
        with self._active_lock:
            return [
                {
                    **item,
                    "elapsed_sec": round(max(0.0, now - item["started_at_monotonic"]), 3),
                }
                for item in self._active_executions.values()
            ]

    @traceable(
        run_type="tool",
        name="skill_attempt_direct",
        process_inputs=trace_process_inputs,
        process_outputs=trace_process_outputs,
    )
    def _execute_direct(
        self,
        skill: "BaseSkill",
        params: dict[str, Any],
        *,
        attempt: int = 1,
    ) -> dict[str, Any]:
        """타임아웃 없이 스킬을 직접 실행한다. 예외는 실패 결과 dict로 변환한다."""
        del attempt
        try:
            return skill.execute(params)
        except Exception as e:  # noqa: BLE001
            logger.exception("Error executing skill: %s", skill.name)
            return {"success": False, "message": f"오류: {e}"}

    @traceable(
        run_type="tool",
        name="skill_attempt_with_timeout",
        process_inputs=trace_process_inputs,
        process_outputs=trace_process_outputs,
    )
    def _execute_with_timeout(
        self,
        skill: "BaseSkill",
        params: dict[str, Any],
        timeout_sec: float,
        *,
        attempt: int = 1,
    ) -> dict[str, Any] | _TimeoutSentinel:
        """재사용 스레드 풀에서 스킬을 실행하고 timeout_sec 내에 완료되지 않으면
        타임아웃 결과를 반환한다.

        타임아웃 시 skill.cancel()을 호출해 ROS action 기반 스킬이 자체적으로
        goal cancel을 수행할 기회를 제공한다. Python 스레드는 강제 종료가
        불가능하므로, cancel()은 협조적 중단 요청이다.
        """
        del attempt
        future = self._executor.submit(skill.execute, params)
        try:
            return future.result(timeout=timeout_sec)
        except FuturesTimeoutError:
            # 큐에서 대기 중인 미시작 작업 취소 (타임아웃 이후 지연 실행 방지)
            future.cancel()
            # 스킬에 협조적 취소 기회 제공 (ROS action goal cancel 등)
            cancel_confirmed = False
            try:
                cancel_result = skill.cancel()
                if isinstance(cancel_result, bool):
                    cancel_confirmed = cancel_result
            except Exception as e:  # noqa: BLE001
                logger.warning("Error during skill cancel: %s", e)
            return _TimeoutSentinel(cancel_confirmed=cancel_confirmed)
        except Exception as e:  # noqa: BLE001
            logger.exception("Error executing skill: %s", skill.name)
            return {"success": False, "message": f"오류: {e}"}

    def shutdown(self, *, wait: bool = False, cancel_futures: bool = True) -> None:
        """스킬 실행 스레드 풀을 종료한다."""
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)

    def list_skills(self, *, include_internal: bool = True) -> list[dict[str, Any]]:
        """등록된 스킬 목록 반환.

        MCP 스킬은 LLM이 인자를 구성할 수 있도록 input_schema도 노출한다.
        ``include_internal=False``이면 LLM에 노출할 Public 스킬만 반환한다.
        """
        skills: list[dict[str, Any]] = []
        for skill in self._skills.values():
            is_internal = bool(getattr(skill, "is_internal", False))
            if is_internal and not include_internal:
                continue
            item: dict[str, Any] = {
                "name": skill.name,
                "description": skill.description,
                # 설명만으로 위험도/체이닝 가능 여부를 추론하지 않도록 메타데이터를
                # 함께 공개한다. 이는 일반 스킬과 MCP 스킬의 인터페이스를 통일한다.
                "risk_level": getattr(skill, "risk_level", "action"),
                "allow_with_others": bool(getattr(skill, "allow_with_others", True)),
                "terminal_behavior": getattr(skill, "terminal_behavior", "terminal"),
                "is_internal": is_internal,
            }
            side_effects = getattr(skill, "side_effects", ())
            if side_effects:
                item["side_effects"] = list(side_effects)
            exclusive_resources = getattr(skill, "exclusive_resources", ())
            if exclusive_resources:
                item["exclusive_resources"] = list(exclusive_resources)
            for field_name in ("preconditions", "postconditions"):
                values = getattr(skill, field_name, ())
                if values:
                    item[field_name] = list(values)
            required_backend = getattr(skill, "requires_manipulation_backend", None)
            if required_backend:
                item["requires_manipulation_backend"] = required_backend
            input_schema = getattr(skill, "input_schema", None)
            if isinstance(input_schema, dict):
                item["input_schema"] = input_schema
            skills.append(item)
        return skills

    def has_skill(self, name: str) -> bool:
        return name in self._skills

    def get_skill(self, name: str) -> BaseSkill | None:
        return self._skills.get(name)

    def runtime_summary(self) -> dict[str, Any]:
        return {
            "registered_count": len(self._skills),
            "active_executions": self.active_executions(),
            "metrics": self._metrics.snapshot(),
        }
