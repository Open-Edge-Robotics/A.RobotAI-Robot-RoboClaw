"""LLMPlanner — LLM 기반 계획 수립, 컨텍스트 수집, 스킬 체인 실행 조율."""

import json
import threading
from typing import Any

from ...answer import looks_like_json
from ...tracing import traceable
from ...types import AgentState
from ..nav_safety import ensure_navigation_safety
from ..utils import _omit_base64_data
from .answers import _MAX_ANSWER_ROUNDS, _compose_result_answer
from .classify import (
    _ACTION_TERMINAL_SKILLS,
    _RAG_EXCLUDED_TYPES,
    _READONLY_TERMINAL_SKILLS,
    _TERMINAL_SKILLS,
)
from .routing import (
    _force_navigation_if_misrouted,
    _is_likely_conversational,
    _location_chain_for,
    _preserve_user_navigation_target,
    _route_location_query,
)


class LLMPlanner:
    """LLM을 통한 계획 수립, 컨텍스트 수집 및 스킬 체인 실행 조율 클래스"""

    def __init__(self, node: Any) -> None:
        self.node = node

    def _skill_metadata(self, skill_name: str) -> Any | None:
        """등록된 스킬의 실행 메타데이터를 조회한다.

        과거에는 planner가 별도의 고정 집합을 유지해 새 스킬을 추가할 때
        terminal/action 분류를 놓칠 수 있었다. 고정 집합은 하위 호환용
        fallback으로 남기되, 등록된 스킬의 메타데이터를 우선 사용한다.
        """
        try:
            return self.node._skills.get_skill(skill_name)
        except Exception:
            return None

    def _is_terminal_skill(self, skill_name: str) -> bool:
        skill = self._skill_metadata(skill_name)
        if skill is not None:
            return getattr(skill, "terminal_behavior", "terminal") in {
                "terminal",
                "background",
                "loop",
            }
        return skill_name in _TERMINAL_SKILLS

    def _is_readonly_skill(self, skill_name: str) -> bool:
        skill = self._skill_metadata(skill_name)
        if skill is not None:
            return getattr(skill, "risk_level", "action") == "read"
        return skill_name in _READONLY_TERMINAL_SKILLS

    def _is_action_skill(self, skill_name: str) -> bool:
        skill = self._skill_metadata(skill_name)
        if skill is not None:
            return getattr(skill, "risk_level", "action") != "read"
        return skill_name in _ACTION_TERMINAL_SKILLS

    def _is_informational_skill(self, skill_name: str) -> bool:
        """결과 데이터를 사용자에게 정리해 답해야 하는 정보성 스킬인지 판정한다.

        안전 기본값은 ``"action"`` 이다. 등록되지 않은 스킬(MCP 등)은 물리적
        부작용을 알 수 없으므로 기존 동작(스킬 message 로 즉시 종료)을 유지한다.
        """
        skill = self._skill_metadata(skill_name)
        if skill is not None:
            return getattr(skill, "answer_mode", "action") == "informational"
        return False

    def _chain_needs_answer(self, chain: list[dict[str, Any]]) -> bool:
        """체인 전체가 정보성 스킬일 때만 결과 요약 라운드를 요청한다.

        물리적 부작용이 있는 스킬이 하나라도 섞이면 추가 LLM 라운드가 두 번째
        물리 동작을 계획할 위험이 있으므로 요약하지 않고 즉시 종료한다.
        """
        if not chain:
            return False
        return all(self._is_informational_skill(str(item.get("skill") or "")) for item in chain)

    @traceable(name="collect_task_context")
    async def collect_task_context(
        self, instruction: str, goal_handle: Any
    ) -> tuple[list[dict[str, str]], dict]:
        """RAG 검색, 로봇 상태 수집, 스킬 교훈 인출을 수행해 LLM messages를 구성한다."""
        knowledge_ctx = ""
        if self.node._enable_rag:
            search_query = instruction
            if self.node._llm:
                try:
                    reformulate_prompt = (
                        "다음 사용자 명령에서 RAG 지식 검색을 위한 핵심 키워드 및 환경 요소를 추출하여 검색 쿼리 하나만 반환하세요.\n"
                        "불필요한 지시어나 서술어는 제거하고 핵심 명사나 명사구 위주로 작성하세요. 다른 텍스트는 절대 출력하지 마세요.\n\n"
                        f"명령: {instruction}\n"
                        "쿼리:"
                    )
                    reformulated = await self.node._run_blocking(
                        "query_reformulate",
                        self.node._llm.chat,
                        [{"role": "user", "content": reformulate_prompt}],
                        timeout_sec=getattr(self.node, "_llm_timeout_sec", 60.0),
                    )
                    reformulated = reformulated.strip().strip('"').strip("'")
                    if reformulated:
                        self.node.get_logger().info(
                            f"RAG query refined: '{instruction}' -> '{reformulated}'"
                        )
                        search_query = reformulated
                except Exception as e:
                    self.node.get_logger().warning(
                        f"RAG query refinement failed (using original instruction): {e}"
                    )

            try:
                related_knowledge = await self.node._run_blocking(
                    "rag_search",
                    self.node._memory.search_knowledge,
                    search_query,
                    self.node._rag_top_k,
                    None,
                    {"type_exclude": _RAG_EXCLUDED_TYPES},
                )
                if related_knowledge:
                    # 위치 지식은 metadata의 좌표를 함께 실어 LLM이 정확한 좌표를
                    # 참조할 수 있게 한다. (text만 전달하면 좌표가 누락됨)
                    lines = []
                    for k in related_knowledge:
                        meta = k.get("metadata") or {}
                        text = str(k.get("text", "")).strip()
                        if (
                            isinstance(meta, dict)
                            and meta.get("type") == "location"
                            and "x" in meta
                            and "y" in meta
                        ):
                            name = meta.get("location_name") or meta.get("name") or ""
                            try:
                                lines.append(
                                    f"- '{name}'의 위치는 x: {float(meta['x']):.2f}, "
                                    f"y: {float(meta['y']):.2f} 입니다."
                                )
                                continue
                            except (TypeError, ValueError):
                                pass
                        lines.append(f"- {text}")
                    knowledge_ctx = "[참고 지식 (과거 경험)]\n" + "\n".join(lines)
            except Exception as e:
                self.node.get_logger().warning(
                    f"RAG knowledge search failed (RAG backend/Qdrant issue): {e}"
                )

        # 로봇 상태 수집
        robot_summary: dict = {}
        try:
            status_skill = self.node._skills._skills.get("get_status")
            if getattr(self.node._skills, "mock_robot", False) is True:
                robot_summary = {"mocked": True, "hardware_connected": False}
            elif hasattr(status_skill, "get_robot_summary"):
                robot_summary = await self.node._run_blocking(
                    "get_robot_summary", status_skill.get_robot_summary
                )
        except Exception as e:
            self.node.get_logger().warning(
                f"Failed to retrieve robot summary from get_status skill: {e}"
            )

        # LLM messages 구성
        messages: list[dict[str, str]] = []
        if self.node._llm:
            history = self.node._memory.get_conversation_history(self.node._multiturn_n)
            messages = history + [{"role": "user", "content": instruction}]

            health_state = self.node._get_robot_health_state(robot_summary)
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": f"[로봇 현재 상태]\n{json.dumps(robot_summary, ensure_ascii=False)}\n\n[로봇 자가진단 상태 (Health State)]\n{json.dumps(health_state, ensure_ascii=False)}",
                },
            )
            if knowledge_ctx:
                messages.insert(0, {"role": "system", "content": knowledge_ctx})

            # 상황 인지형 스킬 교훈 인출
            if getattr(self.node, "_enable_skill_learning", False) and getattr(
                self.node, "_enable_rag", False
            ):
                try:
                    current_pose = None
                    pose = robot_summary.get("pose") if isinstance(robot_summary, dict) else None
                    if isinstance(pose, dict) and "x" in pose and "y" in pose:
                        current_pose = (float(pose["x"]), float(pose["y"]))
                    lessons_ctx = await self.node._run_blocking(
                        "render_relevant_skill_lessons",
                        self.node._memory.render_relevant_skill_lessons,
                        instruction,
                        current_pose=current_pose,
                    )
                    if lessons_ctx:
                        messages.insert(0, {"role": "system", "content": lessons_ctx})
                except Exception as e:
                    self.node.get_logger().warning(f"Failed to retrieve relevant lessons: {e}")

            if goal_handle.request.context_json:
                messages.insert(
                    0,
                    {
                        "role": "system",
                        "content": f"추가 컨텍스트: {goal_handle.request.context_json}",
                    },
                )

        return messages, robot_summary

    @traceable(name="run_llm_planning_loop")
    async def run_llm_planning_loop(
        self,
        goal_handle: Any,
        instruction: str,
        messages: list[dict[str, str]],
        robot_summary: dict,
        timeout: float,
        send_fb: Any,
    ) -> tuple[str, list[Any], bool, list[threading.Event]]:
        """LLM 계획 수립 → 스킬 체인 실행을 최대 max_rounds회 반복한다."""
        max_rounds = 5
        current_round = 0
        # 응답 형식 교정 재시도는 계획 라운드와 별도 예산으로 관리한다.
        # (형식 오류가 반복돼도 실제 작업 수행 라운드가 소진되지 않도록)
        format_retries = 0
        max_format_retries = 2
        # 대화형 오분류 교정도 1회로 제한한다. 무제한 재요구는 판정이 틀렸을 때
        # 계획 라운드를 전부 태우고 "재플래닝 횟수 초과"로 태스크를 실패시킨다.
        conversational_retries = 0
        max_conversational_retries = 1
        # 정보성 스킬 결과를 사용자 답변으로 합성하는 라운드 상태.
        # ``answering`` 이 True인 동안은 스킬을 새로 실행하지 않는다(중복 실행 방지).
        answer_rounds = 0
        answering = False
        recovered_pending = False
        skill_results: list[Any] = []
        pending_send_events: list[threading.Event] = []
        result_msg = ""
        task_success = True

        while current_round < max_rounds:
            current_round += 1
            send_fb(f"계획 수립 중 (라운드 {current_round})", 10 + current_round * 5)
            self.node._set_state(AgentState.THINKING, "", "계획 분석 중")

            try:
                self.node.get_logger().info(f"Starting LLM planning (round {current_round})")
                llm_resp = await self.node._run_blocking(
                    "llm_chat",
                    self.node._llm.chat,
                    messages,
                    system_prompt=self.node._build_system_prompt(),
                    response_format={"type": "json_object"},
                    timeout_sec=getattr(self.node, "_llm_timeout_sec", timeout),
                )
                from ..utils import parse_llm_plan

                plan = parse_llm_plan(llm_resp)
                self.node.get_logger().info(
                    f"LLM planning complete: {len(plan.skills)} skills planned, final={plan.final}"
                )
            except Exception as e:
                self.node.get_logger().error(f"Failed to parse LLM response: {e}")
                result_msg = f"해석 오류: {e}"
                task_success = False
                self.node._set_state(AgentState.ERROR, "", f"LLM 에러: {e}")
                break

            if answering and plan.skills:
                # 요약 라운드에서 스킬 재호출은 받지 않는다. 정보성 스킬만 요약
                # 라운드에 들어오므로 물리적 부작용은 없지만, 같은 조회 스킬을
                # 반복 실행하면 지연과 토큰만 늘고 답변은 나오지 않는다.
                self.node.get_logger().warning(
                    "Summary round returned a skill plan instead of a final answer — "
                    "using deterministic fallback from executed results"
                )
                result_msg = _compose_result_answer(skill_results) or "완료"
                break

            if not plan.skills:
                # 위치 질의인데 스킬을 못 골랐으면 결정론적으로 실행한다.
                #
                # 약한 LLM(gemma4:e4b)은 툴 호출 대신 툴 호출을 **서술**하는 산문을 낸다
                # (실측: "…[identify_location] 스킬을 사용하겠습니다 …"). 이때 plan.skills 가
                # 비어 final=True 가 되므로, 아래 `_route_location_query` 교정은 호출될 기회조차
                # 없이 산문이 그대로 최종 답변으로 나갔다. 형식 재시도로 왕복을 늘리기보다
                # 위치 질의는 여기서 바로 확정 처리한다(모델 성능과 무관하게 동작).
                # 이동 명령도 같은 처리를 받는다("키친으로 이동해" 에 산문만 답하는 경우).
                forced_chain = None
                if not answering:
                    # 요약 라운드에서는 결정론적 강제 실행을 하지 않는다. 이 라운드의
                    # 목적은 이미 실행된 결과를 답변으로 만드는 것이므로, 스킬을 새로
                    # 강제하면 답변 없이 스킬만 다시 실행된다.
                    forced_chain = _force_navigation_if_misrouted(
                        [], instruction, self.node.get_logger()
                    ) or _location_chain_for(instruction, self.node, self.node.get_logger())
                if forced_chain:
                    self.node.get_logger().warning(
                        "LLM returned no skill for a location/navigation command "
                        f"(final={plan.final}, parse_failed={plan.parse_failed}) — "
                        f"forcing '{forced_chain[0].get('skill')}' deterministically. "
                        f"Raw response (first 200 chars): {llm_resp[:200]}"
                    )
                    chain_success, recovered_pending = await self.execute_skill_chain(
                        forced_chain,
                        messages,
                        llm_resp,
                        instruction,
                        timeout,
                        skill_results,
                        pending_send_events,
                        recovered_pending,
                        send_fb,
                    )
                    if chain_success and skill_results and skill_results[-1].success:
                        result_msg = skill_results[-1].message or "완료"
                        break
                    # 강제 실행이 실패하면 기존 형식 교정 흐름으로 계속 진행한다.

                # 형식 교정 재시도 대상 판정. 두 가지 경우 모두 포함한다:
                # (1) final=False — LLM이 도구를 호출하려 했지만 형식이 맞지 않아
                #     파싱에 실패한 경우(소형 LLM이 tool_calls로 회귀 등).
                # (2) parse_failed=True — JSON 파싱 자체가 실패해 원문 전체가
                #     response로 강등되어 final=True가 된 경우. 이 경우 겉보기엔
                #     "의도된 최종 답변"처럼 보이지만 실제로는 형식 오류이므로,
                #     사용자에게 산문(혹은  thinking 잔해)을 그대로 넘기기 전에
                #     최소 1회는 올바른 형식으로 재시도할 가치가 있다.
                needs_format_retry = not plan.final or plan.parse_failed
                if needs_format_retry and format_retries < max_format_retries:
                    format_retries += 1
                    self.node.get_logger().warning(
                        f"LLM returned 0 skills (round {current_round}, "
                        f"final={plan.final}, parse_failed={plan.parse_failed}) — "
                        f"requesting format correction and retrying "
                        f"({format_retries}/{max_format_retries})"
                    )
                    # raw 응답의 앞부분을 로깅하여 디버깅 지원
                    self.node.get_logger().info(
                        f"Raw LLM response (first 300 chars): {llm_resp[:300]}"
                    )
                    messages.append({"role": "assistant", "content": llm_resp})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "응답이 올바른 JSON 형식이 아닙니다. "
                                "반드시 다음 형식 중 하나로만 다시 응답해 주세요.\n"
                                '{"skill": "<스킬명>", "params": {...}, "reason": "..."}\n'
                                '또는 {"skills": [{"skill": "<스킬명>", "params": {...}}], "reason": "..."}\n'
                                '또는 {"skill": null, "response": "<답변>"}\n'
                                "tool_calls 형식은 사용하지 마세요. "
                                "[사용 가능한 스킬]에 있는 스킬명만 사용하세요. "
                                "장소/좌표 지식 조회는 get_location, 현재 로봇 좌표 조회는 get_status를 사용하세요."
                            ),
                        }
                    )
                    # 형식 교정 재시도는 계획 라운드 예산을 소모하지 않는다.
                    current_round -= 1
                    continue

                if not plan.final:
                    # 형식 교정 예산을 모두 쓰고도 스킬도 응답도 얻지 못한 상태.
                    # 성공으로 위장해 "완료"를 돌려주면 실패가 은폐되므로 실패 처리한다.
                    self.node.get_logger().error(
                        "LLM failed to produce a valid plan after "
                        f"{max_format_retries} format corrections"
                    )
                    result_msg = plan.response or "LLM 응답 형식 오류로 계획을 수립하지 못했습니다."
                    task_success = False
                    self.node._set_state(AgentState.ERROR, "", "LLM 응답 형식 오류")
                    break

                if plan.parse_failed:
                    # 형식 교정 재시도를 다 썼는데도 JSON을 못 냈다 — 사용자 확정 정책대로
                    # 산문을 답변으로 그대로 사용하되, 재시도가 소진됐다는 사실을 로그로
                    # 남겨 "정상적인 최종 답변"과 구분되도록 한다.
                    self.node.get_logger().warning(
                        f"Format correction retries ({max_format_retries}) exhausted with "
                        "still-unparseable output — using raw text as final answer "
                        f"(first 300 chars): {llm_resp[:300]}"
                    )
                result_msg = plan.response or "완료"
                if answering and (not plan.response or looks_like_json(plan.response)):
                    # 요약 라운드가 사람이 읽을 답변을 내지 못한 경우(형식 재시도 소진으로
                    # 강등된 JSON 원문 등)에는 실행 결과 데이터로 직접 답한다.
                    result_msg = _compose_result_answer(skill_results) or result_msg
                self.node.get_logger().info(f"Final answer determined: {result_msg}")
                break

            # 대화형 쿼리에 LLM이 실수로 스킬을 선택한 경우 1회만 재요구한다.
            # 예산을 다 쓰고도 LLM이 스킬을 고집하면 체인 실행으로 넘어가고,
            # 물리 동작 스킬은 execute_skill_chain 에서 개별 차단한다.
            if _is_likely_conversational(instruction) and (
                conversational_retries < max_conversational_retries
            ):
                conversational_retries += 1
                self.node.get_logger().info(
                    f"Conversational query '{instruction}' got skill plan — requesting re-evaluation"
                )
                messages.append({"role": "assistant", "content": llm_resp})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "사용자의 요청은 스킬이 필요하지 않은 대화형 질문입니다. "
                            '반드시 {"skill": null, "response": "<답변>"} 형식으로만 응답하세요.'
                        ),
                    }
                )
                continue

            raw_chain = [{"skill": item.skill, "params": item.params} for item in plan.skills]
            raw_chain = _preserve_user_navigation_target(
                raw_chain, instruction, self.node.get_logger()
            )
            # 이동 교정을 위치질의 교정보다 **먼저** 한다. "거실 위치로 이동해" 처럼 두 조건이
            # 모두 걸리는 문장에서, 사용자가 원한 이동을 조회로 바꿔치기하면 안 된다.
            nav_corrected = _force_navigation_if_misrouted(
                raw_chain, instruction, self.node.get_logger()
            )
            if nav_corrected is not raw_chain:
                raw_chain = nav_corrected
            else:
                raw_chain = _route_location_query(
                    raw_chain, instruction, self.node, self.node.get_logger()
                )
            raw_chain = ensure_navigation_safety(
                raw_chain, self.node._skills, self.node.get_logger(), self.node
            )
            policy_error = self.node._validate_skill_chain(raw_chain)
            if policy_error:
                self.node.get_logger().warning(f"Plan policy validation failed: {policy_error}")
                messages.append({"role": "assistant", "content": llm_resp})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"계획 검증 실패: {policy_error}. "
                            "복합 조작 스킬(adaptive_pick_object, grasp, place, vla_pick_*, "
                            "pick_*, prepare_right_side_pick, servo_gripper_to_object)은 "
                            "자체완결형이라 다른 스킬과 체이닝할 수 없습니다. 단일 스킬로만 "
                            "계획하거나, 여러 동작이 필요하면 각 동작을 별도 라운드로 순차 "
                            "처리하세요. 장소/좌표 지식 조회는 get_location, 현재 로봇 좌표 "
                            "조회는 get_status를 사용하세요."
                        ),
                    }
                )
                continue

            # 스킬 체인 실행
            chain_success, recovered_pending = await self.execute_skill_chain(
                raw_chain,
                messages,
                llm_resp,
                instruction,
                timeout,
                skill_results,
                pending_send_events,
                recovered_pending,
                send_fb,
            )

            if chain_success:
                # 정보성 스킬만으로 이루어진 체인은 결과 데이터를 사용자에게 정리해
                # 답해야 한다. 예전에는 마지막 스킬의 message(ros_command 는
                # "실행 완료")를 그대로 답변으로 써서 실제 조회 결과가 사라졌다.
                if (
                    not plan.final
                    and not answering
                    and answer_rounds < _MAX_ANSWER_ROUNDS
                    and self._chain_needs_answer(raw_chain)
                ):
                    answer_rounds += 1
                    answering = True
                    self.node.get_logger().info(
                        "Informational skill chain succeeded — requesting a synthesized "
                        f"final answer from executed results "
                        f"({answer_rounds}/{_MAX_ANSWER_ROUNDS})"
                    )
                    # execute_skill_chain 이 이미 messages 에 실행 결과 요약과 최종
                    # 답변 요청을 넣었다. 다음 라운드가 그것을 소비한다.
                    continue

                # 물리 동작/백그라운드 스킬이거나 요약 라운드 예산을 다 쓴 경우:
                # 마지막 스킬의 결과 메시지로 종료한다. reason 은 내부 전략
                # 필드이므로 사용자 답변으로 쓰지 않는다.
                last_result = skill_results[-1] if skill_results else None
                result_msg = (
                    (last_result.message if last_result else "")
                    or _compose_result_answer(skill_results)
                    or "실행 완료"
                )
                break
            continue
        else:
            self.node.get_logger().error(
                f"Exceeded max replanning rounds. Result so far: {result_msg}"
            )
            result_msg = "재플래닝 횟수 초과 (결과 분석 실패)"
            task_success = False

        return result_msg, skill_results, task_success, pending_send_events

    async def execute_skill_chain(
        self,
        chain: list[dict[str, Any]],
        messages: list[dict[str, str]],
        llm_resp: str,
        instruction: str,
        timeout: float,
        skill_results: list[Any],
        pending_send_events: list[threading.Event],
        recovered_pending: bool,
        send_fb: Any,
    ) -> tuple[bool, bool]:
        """스킬 체인을 순차 실행하고 결과를 messages에 반영한다."""
        chain_success = True
        executed_reports = []

        # assistant 메시지는 체인 실행 전 1회만 추가하여 중복 누적 방지
        messages.append({"role": "assistant", "content": llm_resp})

        for item in chain:
            name, params = item.get("skill"), item.get("params", {})
            if not name:
                continue

            # 대화형 쿼리에서 action 스킬이 선택된 경우 안전하게 건너뛴다.
            # (재요구 예산을 소진하고도 LLM이 물리 동작을 고집한 경우의 최종 방어선)
            if self._is_action_skill(name) and _is_likely_conversational(instruction):
                self.node.get_logger().warning(
                    f"Action skill '{name}' blocked for conversational query: '{instruction}' — skipping"
                )
                executed_reports.append(
                    f"- '{name}': 실행하지 않음. 대화형 요청이라 물리 동작 스킬은 차단됩니다."
                )
                continue

            self.node.get_logger().info(f"Attempting skill execution: {name} (params={params})")
            send_fb(f"실행: {name}", 40)
            self.node._set_state(AgentState.EXECUTING, name, f"{name} 실행 중")
            res = await self.node._run_blocking(
                f"skill:{name}",
                self.node._skills.execute,
                name,
                params,
                timeout,
            )

            clean_result_data = _omit_base64_data(res.result_data)

            if res.success and "file_path" in res.result_data:
                file_path = res.result_data["file_path"]
                msg_text = res.result_data.get("message", f"'{name}' 실행 결과 이미지입니다.")
                self.node.get_logger().info(f"Starting image transfer: {file_path}")
                event = self.node._start_channel_send(msg_text, file_path)
                pending_send_events.append(event)

            skill_results.append(res)

            if getattr(self.node, "_enable_skill_learning", False) and not res.result_data.get(
                "mocked"
            ):
                try:
                    await self.node._run_blocking(
                        "record_skill_episode",
                        self.node._memory.record_skill_episode,
                        name,
                        res.success,
                        duration_sec=res.duration_sec,
                        error="" if res.success else res.message,
                        params=params,
                        instruction=instruction,
                        replanned=not res.success,
                        recovered=(res.success and recovered_pending),
                    )
                except Exception as e:
                    self.node.get_logger().warning(f"Failed to record skill experience: {e}")

            if not res.success:
                self.node.get_logger().error(
                    f"Skill '{name}' failed: {res.message}. Collecting telemetry and attempting replanning..."
                )
                # 실패 직후 최신 로봇 상태 수집 (텔레메트리 Context 주입)
                latest_summary: dict = {}
                try:
                    status_skill = self.node._skills._skills.get("get_status")
                    if hasattr(status_skill, "get_robot_summary"):
                        latest_summary = await self.node._run_blocking(
                            "get_robot_summary_on_failure", status_skill.get_robot_summary
                        )
                except Exception as e:
                    self.node.get_logger().warning(
                        f"Failed to retrieve robot summary on skill failure: {e}"
                    )

                result_details = res.result_data if isinstance(res.result_data, dict) else {}
                error_type = str(result_details.get("error_type") or "SkillExecutionFailed")
                fail_context = {
                    "error_type": error_type,
                    "failed_skill": name,
                    "params": params,
                    "error_message": res.message,
                    "robot_state_at_failure": _omit_base64_data(latest_summary),
                }
                if error_type == "ParameterSchemaError":
                    fail_context["schema_error"] = result_details.get("schema_error", "")
                elif error_type == "SkillPreconditionError":
                    fail_context["precondition_error"] = result_details.get(
                        "precondition_error", ""
                    )
                    fail_context["preconditions"] = result_details.get("preconditions", [])

                recovery_instruction = (
                    "이는 실행 전 파라미터 형식 검증 오류입니다. 로봇은 움직이지 않았습니다. "
                    "input_schema에 맞게 필수 파라미터/타입/enum을 수정한 단일 스킬 계획을 다시 작성하세요. "
                    "같은 잘못된 파라미터를 반복하지 마세요."
                    if error_type == "ParameterSchemaError"
                    else "이는 실행 전 환경 조건 검증 오류입니다. 물리 동작은 시작되지 않았습니다. "
                    "preconditions와 최신 로봇 상태를 확인하고, 조건을 만족하도록 복구 스킬을 먼저 실행하거나 "
                    "안전한 대체 계획을 작성하세요."
                    if error_type == "SkillPreconditionError"
                    else "위 로봇 상태와 실패 사유를 고려하여 최적의 대처 및 다음 계획을 수립해 주세요."
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"오류: 스킬 '{name}' 실행 실패.\n"
                            f"상황 분석 데이터:\n{json.dumps(fail_context, ensure_ascii=False, indent=2)}\n"
                            f"{recovery_instruction} "
                            "장소명 좌표가 필요하면 get_location을 사용하고, analyze_error 같은 등록되지 않은 함수는 사용하지 마세요."
                        ),
                    }
                )
                chain_success = False
                recovered_pending = True
                break
            else:
                recovered_pending = False
                self.node.get_logger().info(f"Skill '{name}' executed successfully")
                executed_reports.append(
                    f"- '{name}': 성공. 결과: {json.dumps(clean_result_data, ensure_ascii=False)}"
                )

        if chain_success:
            results_summary = "\n".join(executed_reports)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"스킬 체인 실행 결과:\n{results_summary}\n\n"
                        "요청된 스킬들이 성공적으로 수행되었습니다. "
                        "다음 행동이 필요하면 스킬을 실행하고, 완료됐으면 반드시 JSON 형식으로만 최종 답변하세요: "
                        '{"skill": null, "response": "<최종 답변>"}'
                    ),
                }
            )

        return chain_success, recovered_pending
