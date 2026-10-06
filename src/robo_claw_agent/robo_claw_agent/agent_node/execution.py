import asyncio
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from robo_claw_msgs.action import ExecuteTask
from robo_claw_msgs.srv import SendMessage

from ..answer import sanitize_final_answer
from ..tracing import (
    current_trace_id,
    set_current_trace_metadata,
    trace_process_inputs,
    trace_process_outputs,
    traceable,
)
from ..types import AgentState
from .direct_skills import direct_cup_pick_skill, start_background_direct_skill
from .fast_router import (
    DEFAULT_RULE_ROUTER,
    ROUTE_DIRECT_SKILL,
    ROUTE_SIMPLE_REPLY,
)
from .nav_safety import ensure_stretch_navigation_safety
from .skill_result_msg import build_skill_result_msg
from .task_queue import QueuedTask, TaskQueue

if TYPE_CHECKING:
    pass


@traceable(
    run_type="chain",
    name="runtime_operation",
    process_inputs=trace_process_inputs,
    process_outputs=trace_process_outputs,
)
def _trace_runtime_operation(
    op_name: str,
    func: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    del op_name
    return func(*args, **kwargs)


class ExecutionMixin:
    """AgentNode의 비동기 실행 및 LLM 태스크 제어를 담당하는 Mixin"""

    if TYPE_CHECKING:
        _state: AgentState
        _agent_id: str
        _llm: Any
        _memory: Any
        _skills: Any
        _enable_rag: bool
        _rag_top_k: int
        _multiturn_n: int
        _enable_skill_learning: bool
        _runtime_metrics: Any
        _send_msg_client: Any
        _robot_limits_dict: Any
        _skill_lessons_dirty: bool
        _skill_lessons_ctx: str

        def _set_state(self, state: AgentState, skill: str = "", msg: str = "") -> None: ...
        def _build_system_prompt(self) -> str: ...
        def _get_robot_health_state(self, robot_summary: dict = None) -> dict: ...
        def get_logger(self) -> Any: ...
        def get_clock(self) -> Any: ...
        def mark_skill_lessons_dirty(self) -> None: ...

    def _direct_cup_pick_skill(self, instruction: str) -> dict[str, Any] | None:
        """명확한 컵 집기 요청을 결정론적으로 라우팅한다.

        하위 구현은 direct_skills에 두고, 이 wrapper는 기존 ExecutionMixin API와
        테스트/호출부의 결합을 유지한다.
        """
        return direct_cup_pick_skill(self, instruction)

    def _start_background_direct_skill(
        self, instruction: str, skill_name: str, params: dict[str, Any]
    ) -> Any:
        """직접 라우팅된 장시간 skill을 백그라운드에서 시작한다."""
        return start_background_direct_skill(self, instruction, skill_name, params)

    # 태스크 큐 — 순차 실행 보장
    _task_queue: TaskQueue | None = None

    def _init_task_queue(self, max_size: int = 8) -> None:
        """태스크 큐를 초기화하고 워커를 시작한다. node.__init__ 에서 호출."""
        self._task_queue = TaskQueue(max_size=max_size)
        self._task_queue.start()

    def _submit_task_to_queue(
        self,
        instruction: str,
        execute_fn: Callable[[], tuple[str, list[Any], bool]],
        on_queued: Callable[[], None] | None = None,
        on_start: Callable[[], None] | None = None,
    ) -> str | None:
        """태스크를 큐에 적재한다. 큐가 가득 차면 None 반환."""
        if self._task_queue is None:
            return None
        task = QueuedTask(
            task_id="",
            instruction=instruction,
            execute_fn=execute_fn,
            on_queued=on_queued,
            on_start=on_start,
        )
        if not self._task_queue.submit(task):
            return None
        return task.task_id

    async def _run_blocking(
        self,
        op_name: str,
        func: Any,
        *args: Any,
        timeout_sec: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """블로킹 함수를 스레드에서 실행하고, 선택적으로 타임아웃을 적용한다.

        ``timeout_sec`` 이 주어지면 ``asyncio.wait_for`` 로 상한을 둔다. 초과 시
        ``asyncio.TimeoutError`` 를 발생시켜 호출부가 재시도/오류 처리할 수 있게 한다.
        (스레드 자체는 종료되지 않지만, 코루틴은 더 이상 대기하지 않아 에이전트가
        무한 블로킹으로 멈추는 것을 방지한다.)
        """
        start = time.monotonic()
        try:
            try:
                asyncio.get_running_loop()
                coro = asyncio.to_thread(
                    _trace_runtime_operation,
                    op_name,
                    func,
                    args,
                    kwargs,
                )
                if timeout_sec is not None:
                    coro = asyncio.wait_for(coro, timeout=timeout_sec)
                result = await coro
            except RuntimeError:
                result = _trace_runtime_operation(op_name, func, args, kwargs)
        except asyncio.TimeoutError:
            self._runtime_metrics.record(
                op_name,
                success=False,
                duration_sec=time.monotonic() - start,
                error=f"timeout after {timeout_sec}s",
            )
            self.get_logger().error(f"Async operation timed out ({op_name}) after {timeout_sec}s")
            raise
        except Exception as exc:
            import traceback

            error_detail = traceback.format_exc()
            self._runtime_metrics.record(
                op_name,
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(exc),
            )
            self.get_logger().error(f"Async operation failed ({op_name}): {exc}\n{error_detail}")
            raise

        self._runtime_metrics.record(op_name, success=True, duration_sec=time.monotonic() - start)
        return result

    def _validate_skill_chain(self, chain: list[dict[str, Any]]) -> str | None:
        if not chain:
            return "실행할 스킬이 비어 있습니다."

        disallow_chain = []
        background_skills: list[str] = []
        for item in chain:
            name = str(item.get("skill") or "").strip()
            params = item.get("params", {})
            if not name:
                return "스킬 이름이 비어 있습니다."
            if not isinstance(params, dict):
                return f"{name}의 params는 객체(JSON dict)여야 합니다."
            skill = self._skills.get_skill(name)
            if skill is None:
                return f"등록되지 않은 스킬입니다: {name}"
            preconditions_valid, precondition_error = skill.check_preconditions(params)
            if not preconditions_valid:
                return f"{name}의 실행 전 조건이 충족되지 않았습니다: {precondition_error}"
            schema_valid, schema_error = skill.validate_input_schema(params)
            if not schema_valid:
                return f"{name}의 파라미터 형식이 유효하지 않습니다: {schema_error}"
            if not skill.validate_params(params):
                return f"{name}의 파라미터가 유효하지 않습니다."
            if not getattr(skill, "allow_with_others", True):
                disallow_chain.append(name)
            behavior = getattr(skill, "terminal_behavior", "terminal")
            if behavior == "background":
                background_skills.append(name)
            # side_effects/exclusive_resources는 계획 설명과 관찰성에 사용한다.
            # 체인은 순차 실행되므로 같은 자원을 두 번 사용하는 것 자체는
            # 합법적일 수 있다(예: 이동 후 회전). 동시 실행 충돌로 오판해
            # 정상적인 사용자 명령을 차단하지 않는다.

            if name == "delegate_task":
                target = str(params.get("agent_id", "")).strip().lstrip("/")
                agent_id = getattr(self, "_agent_id", "")
                if target and agent_id and target == agent_id.lstrip("/"):
                    return "같은 에이전트에게 자기 자신을 위임할 수 없습니다."
            if name in ("call_peer_robot", "coordinate_peer_task"):
                target = str(params.get("peer_name", "")).strip()
                my_ns = ""
                try:
                    my_ns = (self.get_namespace() or "").strip("/")
                except Exception:
                    pass
                if target and target == my_ns:
                    return "같은 로봇에게 자기 자신을 위임할 수 없습니다."

        if background_skills and len(chain) > 1:
            allowed_pre_background = {"send_message", "say", "log_observation"}
            last_skill_name = str(chain[-1].get("skill") or "").strip()
            prior_skills = [str(item.get("skill") or "").strip() for item in chain[:-1]]
            last_skill_obj = self._skills.get_skill(last_skill_name)
            is_last_background = (
                getattr(last_skill_obj, "terminal_behavior", "terminal") == "background"
            )

            if len(background_skills) > 1 or not is_last_background:
                return (
                    f"백그라운드 스킬은 다른 스킬과 체이닝할 수 없습니다: "
                    f"{', '.join(background_skills)}"
                )

            disallowed_prior = [s for s in prior_skills if s not in allowed_pre_background]
            if disallowed_prior:
                return (
                    f"백그라운드 스킬은 다른 스킬과 체이닝할 수 없습니다: "
                    f"{', '.join(background_skills)}"
                )

        if disallow_chain and len(chain) > 1:
            joined = ", ".join(disallow_chain)
            return f"다음 스킬은 다른 스킬과 체이닝할 수 없습니다: {joined}"
        return None

    async def _handle_execute_task(self, goal_handle: Any) -> ExecuteTask.Result:
        instruction = goal_handle.request.instruction
        timeout = goal_handle.request.timeout_sec or 600.0
        self._memory.add_event("task_received", {"instruction": instruction})

        # 큐가 초기화되지 않았으면 즉시 실행(기존 동작 유지)
        if self._task_queue is None:
            return await self._execute_task_direct(goal_handle, instruction, timeout)

        return await self._execute_task_via_queue(goal_handle, instruction, timeout)

    async def _execute_task_direct(
        self, goal_handle: Any, instruction: str, timeout: float
    ) -> ExecuteTask.Result:
        """큐 없이 즉시 실행 (폴백용)."""
        task_success = True
        result_msg = ""
        skill_results: list[Any] = []

        try:
            result_msg, skill_results, task_success = await self._execute_task_inner(
                goal_handle, instruction, timeout
            )
        except Exception as exc:  # noqa: BLE001
            import traceback

            error_detail = traceback.format_exc()
            self.get_logger().error(
                f"Unhandled exception during task execution: {exc}\n{error_detail}"
            )
            result_msg = f"내부 오류: {exc}"
            task_success = False
        finally:
            self._set_state(AgentState.IDLE)

        self._memory.add_event("task_completed", {"result": result_msg})

        if task_success:
            goal_handle.succeed()
        else:
            goal_handle.abort()

        action_results_ros = [build_skill_result_msg(self.get_clock(), r) for r in skill_results]

        action_result = ExecuteTask.Result()
        action_result.success = task_success
        # 최종 답변은 모든 채널(HTTP/gRPC/ROS action/메신저)로 나가는 단일 경계다.
        # LLM 이 JSON envelope 를 벗어나거나 형식 교정 재시도를 소진한 경우의 원문이
        # 그대로 사용자에게 노출되지 않도록 여기서 정리한다.
        action_result.result_message = sanitize_final_answer(str(result_msg)) or "완료"
        action_result.skill_results = action_results_ros
        return action_result

    async def _execute_task_via_queue(
        self, goal_handle: Any, instruction: str, timeout: float
    ) -> ExecuteTask.Result:
        """태스크 큐를 통해 순차 실행한다."""
        assert self._task_queue is not None

        done_event = threading.Event()
        result_box: dict[str, Any] = {}

        def _on_queued() -> None:
            """큐에 적재되었을 때 feedback 전송."""
            try:
                depth = self._task_queue.metrics.current_queue_depth if self._task_queue else 0
                fb = ExecuteTask.Feedback()
                fb.current_step = f"순차 대기 중 (대기열: {depth})"
                fb.progress = 2
                fb.agent_status.state = int(self._state)
                fb.agent_status.agent_id = self._agent_id
                goal_handle.publish_feedback(fb)
            except Exception:
                pass

        def _on_start() -> None:
            """실행 시작 시 feedback 전송."""
            try:
                fb = ExecuteTask.Feedback()
                fb.current_step = "실행 시작"
                fb.progress = 5
                fb.agent_status.state = int(self._state)
                fb.agent_status.agent_id = self._agent_id
                goal_handle.publish_feedback(fb)
            except Exception:
                pass

        def _execute() -> tuple[str, list[Any], bool]:
            """실제 태스크 실행 (워커 스레드에서 호출됨)."""
            try:
                # asyncio 이벤트 루프가 없는 스레드에서 실행하므로
                # 동기적으로 실행한다.
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(
                        self._execute_task_inner(goal_handle, instruction, timeout)
                    )
                    return result
                finally:
                    loop.close()
            except Exception as exc:  # noqa: BLE001
                import traceback

                error_detail = traceback.format_exc()
                self.get_logger().error(f"Task execution error: {exc}\n{error_detail}")
                return f"내부 오류: {exc}", [], False

        def _run_task() -> None:
            """워커 스레드에서 호출되는 래퍼."""
            try:
                result = _execute()
                result_box["result"] = result
            except Exception as exc:
                result_box["result"] = (f"내부 오류: {exc}", [], False)
                result_box["error"] = exc
            finally:
                self._set_state(AgentState.IDLE)
                done_event.set()

        # 동일 명령이 이미 실행 중이거나 대기 큐에 있으면 중복 실행 방지
        if self._task_queue.is_instruction_active(instruction):
            self.get_logger().warning(f"TaskQueue: Duplicate instruction rejected: '{instruction}'")
            self._memory.add_event("task_rejected_duplicate", {"instruction": instruction})
            goal_handle.abort()
            action_result = ExecuteTask.Result()
            action_result.success = False
            action_result.result_message = "동일한 명령이 이미 실행 중이거나 대기 열에 있습니다."
            return action_result

        task_id = self._submit_task_to_queue(
            instruction=instruction,
            execute_fn=_run_task,
            on_queued=_on_queued,
            on_start=_on_start,
        )

        if task_id is None:
            self._memory.add_event("task_rejected", {"instruction": instruction})
            goal_handle.abort()
            action_result = ExecuteTask.Result()
            action_result.success = False
            action_result.result_message = "처리 대기열이 가득 찼습니다. 잠시 후 다시 시도해주세요."
            return action_result

        # 완료 대기 — asyncio 이벤트 루프 유무에 맞춰 비동기/동기 대기.
        # 복합 명령은 여러 단계 × 재시도를 거치며 단일 명령보다 오래 걸릴 수 있으므로
        # 여유 시간을 분해 설정에 맞춰 늘린다(단순 명령은 기존 마진 그대로 유지).
        wait_margin = self._task_decomposer.estimate_wait_margin_sec(instruction)
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, done_event.wait, timeout + wait_margin)
        except RuntimeError:
            done_event.wait(timeout + wait_margin)

        result = result_box.get("result", ("시간 초과", [], False))
        result_msg, skill_results, task_success = result

        self._memory.add_event("task_completed", {"result": result_msg})

        if task_success:
            goal_handle.succeed()
        else:
            goal_handle.abort()

        action_results_ros = [build_skill_result_msg(self.get_clock(), r) for r in skill_results]

        action_result = ExecuteTask.Result()
        action_result.success = task_success
        action_result.result_message = sanitize_final_answer(str(result_msg)) or "완료"
        action_result.skill_results = action_results_ros
        return action_result

    @traceable(
        run_type="chain",
        name="execute_task",
        tags=["robot-task"],
        process_inputs=trace_process_inputs,
        process_outputs=trace_process_outputs,
    )
    async def _execute_task_inner(
        self, goal_handle: Any, instruction: str, timeout: float
    ) -> tuple[str, list[Any], bool]:
        set_current_trace_metadata(
            {"robot_id": self._agent_id, "instruction_length": len(instruction)},
            tags=[f"robot:{self._agent_id}"],
        )
        trace_id = current_trace_id()
        if trace_id:
            self.get_logger().info(
                f"Task trace started: trace_id={trace_id} agent_id={self._agent_id}"
            )

        def send_fb(step: str, progress: int) -> None:
            fb = ExecuteTask.Feedback()
            fb.current_step, fb.progress = step, progress
            fb.agent_status.state, fb.agent_status.agent_id = (
                int(self._state),
                self._agent_id,
            )
            goal_handle.publish_feedback(fb)

        send_fb("상태 및 과거 지식 파악 중", 5)
        messages, robot_summary = await self._planner.collect_task_context(instruction, goal_handle)

        skill_results: list[Any] = []
        pending_send_events: list[threading.Event] = []
        result_msg = ""
        task_success = True

        from .direct_skills import start_background_direct_skill

        # 사전 라우팅(그룹 A). SYSTEM1_ROUTER 설정에 따라 RuleRouter 또는 Laya 중
        # 하나만 판단한다. 복합 명령 선점 방지 등 기존 규칙은 RuleRouter 가 담당한다.
        router = getattr(self, "_router", None) or DEFAULT_RULE_ROUTER
        route = await router.decide(self, instruction)
        set_current_trace_metadata(route.trace_metadata())
        direct_skill = route.as_direct_skill() if route.kind == ROUTE_DIRECT_SKILL else None
        if direct_skill is not None:
            name = direct_skill["skill"]
            params = direct_skill["params"]
            # 결과 메시지에 쓸 실제 실행 스킬명. stow 등 선행 스킬이 실패하면
            # 원래 요청 스킬명이 아니라 실패한 스킬명을 보고해야 한다.
            step_name = name
            self.get_logger().info(f"Direct skill routing: {name} (params={params})")
            if direct_skill.get("background"):
                send_fb(f"실행: {name}", 40)
                res = start_background_direct_skill(self, instruction, name, params)
                skill_results.append(res)
            else:
                # LLM 계획 경로와 동일한 주행 안전 규칙을 적용한다.
                chain = ensure_stretch_navigation_safety(
                    [{"skill": name, "params": params}], self._skills, self.get_logger()
                )

                res = None
                for direct_item in chain:
                    step_name = direct_item["skill"]
                    step_params = direct_item["params"]
                    send_fb(f"실행: {step_name}", 40)
                    self._set_state(AgentState.EXECUTING, step_name, f"{step_name} 실행 중")
                    res = await self._run_blocking(
                        f"skill:{step_name}",
                        self._skills.execute,
                        step_name,
                        step_params,
                        timeout,
                    )
                    skill_results.append(res)
                    if (
                        res.success
                        and isinstance(res.result_data, dict)
                        and "file_path" in res.result_data
                    ):
                        file_path = res.result_data["file_path"]
                        msg_text = res.result_data.get(
                            "message", f"'{step_name}' 실행 결과 이미지입니다."
                        )
                        self.get_logger().info(f"Starting image transfer: {file_path}")
                        event = self._start_channel_send(msg_text, file_path)
                        pending_send_events.append(event)
                    if not res.success:
                        break

            if res is None:
                result_msg = f"{name} 실행 실패"
                task_success = False
                return result_msg, skill_results, task_success
            task_success = bool(res.success)
            result_msg = res.message or (
                f"{step_name} 실행 완료" if res.success else f"{step_name} 실행 실패"
            )

            if pending_send_events:
                try:
                    asyncio.get_running_loop()
                    await asyncio.gather(
                        *(asyncio.to_thread(event.wait, 60.0) for event in pending_send_events)
                    )
                except RuntimeError:
                    for event in pending_send_events:
                        event.wait(timeout=60.0)

            return result_msg, skill_results, task_success

        # 스킬이 필요없는 단순 대화형 쿼리는 LLM 호출 없이 즉시 응답
        if route.kind == ROUTE_SIMPLE_REPLY:
            result_msg = route.reply
            task_success = True
            return result_msg, skill_results, task_success

        if (
            self._llm
            and getattr(self, "_enable_task_decomposition", True)
            and (self._task_decomposer.looks_compound(instruction))
        ):
            (
                result_msg,
                skill_results,
                task_success,
                pending_send_events,
            ) = await self._task_decomposer.run(
                goal_handle,
                instruction,
                messages,
                robot_summary,
                timeout,
                send_fb,
            )
        elif self._llm:
            (
                result_msg,
                skill_results,
                task_success,
                pending_send_events,
            ) = await self._planner.run_llm_planning_loop(
                goal_handle,
                instruction,
                messages,
                robot_summary,
                timeout,
                send_fb,
            )
        else:
            result_msg = "LLM 미설정"
            task_success = False

        if pending_send_events:
            try:
                asyncio.get_running_loop()
                await asyncio.gather(
                    *(asyncio.to_thread(event.wait, 60.0) for event in pending_send_events)
                )
            except RuntimeError:
                for event in pending_send_events:
                    event.wait(timeout=60.0)

        # 중간에 실패한 스킬이 있어도 재계획으로 복구했다면 실패로 뒤집지 않는다.
        # 성공 판정은 이미 planner/decomposer 가 내렸고, 여기서는 "마지막 시도가
        # 실패로 끝났는데 성공으로 보고되는" 경우만 방어한다. 과거처럼 all() 로
        # 보면 복구된 실패까지 전부 실패가 되고, 단계가 많은 분해 경로에서는
        # 그 확률이 단계 수만큼 곱해져 정상 완료도 실패로 보고됐다.
        if skill_results and not skill_results[-1].success:
            task_success = False

        return result_msg, skill_results, task_success

    def _start_channel_send(self, message: str, file_path: str = "") -> threading.Event:
        """이미지/메시지 전송을 비동기로 시작하고 완료 이벤트를 반환한다."""
        done = threading.Event()

        if not self._send_msg_client.service_is_ready():
            self.get_logger().warning("Message send service is not ready.")
            done.set()
            return done

        req = SendMessage.Request()
        req.message = message
        req.file_path = file_path

        def _on_done(f: Any) -> None:
            try:
                result = f.result()
                self.get_logger().info(
                    f"Message send {'succeeded' if result and result.success else 'failed'}"
                )
            except Exception as e:
                self.get_logger().error(f"Error during message send: {e}")
            finally:
                done.set()

        try:
            future = self._send_msg_client.call_async(req)
            future.add_done_callback(_on_done)
        except Exception as e:
            self.get_logger().error(f"Error during message send request: {e}")
            done.set()

        return done

    async def _send_to_channel(self, message: str, file_path: str = "") -> None:
        """메신저 채널로 메시지나 파일을 전송하고 완료까지 대기합니다."""
        event = self._start_channel_send(message, file_path)
        await self._run_blocking("channel_send_wait", event.wait, 60.0)
