"""자율협동 스킬 — 동료 로봇과 지속적 자연어 대화로 도움 요청/지시/판단을 수행한다.

`autonomous_cooperate` 는 백그라운드 루프를 시작해:
  1. 수신 인박스(PeerMessageInbox)에서 동료가 보낸 메시지를 꺼내 LLM 으로 응답을 결정하고
     `call_peer_robot` 로 회신한다.
  2. 주기적으로 자신의 상태를 점검해 동료에게 도움 요청/지시를 능동적으로 보낼지 LLM 으로
     판단한다.
  3. 모든 대화를 PeerConversationManager 에 기록해 맥락을 유지한다.

관측은 협동 저널(``journal``)이 맡는다. 이 스킬은 세션 수명주기, 수신 상관 ID 전파,
그리고 저널 기록 시점만 결정하고, 전송(``peer_transport``)·원격 실행(``remote_task``)
은 전용 모듈에 위임한다.

ROS2 의존은 lazy import 로 감싸, rclpy 없이도 단위 테스트가 가능하다.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from robo_claw_agent import ros_future
from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills.yield_precheck import precheck_yield_for_move

from .conversation import get_peer_conversation
from .envelope import (
    KIND_REPLY,
    KIND_RESULT,
    OBSERVED_RESULT_STATUSES,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_DENIED,
    TASK_STATUS_FAILED,
    TASK_STATUS_PARTIAL,
    TASK_STATUS_RECEIVED,
    PeerEnvelope,
    envelope_from_transport,
    is_result_report,
    new_id,
    normalize_task_status,
)
from .globals import (
    _COOPERATE_WAKE,
    claim_cooperation,
    clear_cooperation_thread_if_current,
    get_cooperation_thread,
    is_cooperation_active,
    release_cooperation,
    set_cooperation_thread,
)
from .inbox import InboxMessage, PeerMessageInbox, get_peer_message_inbox
from .journal import (
    EVENT_DECISION,
    EVENT_MESSAGE_RECEIVED,
    EVENT_MESSAGE_REJECTED,
    CooperationJournal,
    get_cooperation_journal,
)
from .peer_transport import send_cooperation_message
from .remote_task import RESULT_EVENT_TYPES, execute_remote_task
from .sanitize import preview_text

logger = logging.getLogger(__name__)

#: 실행 결과 상태 → 회신 문구 접두.
_RESULT_LABELS = {
    TASK_STATUS_COMPLETED: "작업 완료",
    TASK_STATUS_FAILED: "작업 실패",
    TASK_STATUS_DENIED: "작업 거부",
    TASK_STATUS_PARTIAL: "부분 완료",
}


def _envelope_from_inbox_item(item: InboxMessage) -> PeerEnvelope:
    """인박스 항목의 상관 ID를 envelope 로 옮긴다."""
    return envelope_from_transport(
        content=item.message,
        message_id=item.message_id,
        task_id=item.task_id,
        session_id=item.session_id,
        reply_to=item.reply_to,
        status=item.status,
        result=item.result,
        kind=item.kind,
    )


_DEFAULT_REMOTE_ALLOWED_SKILLS = (
    "analyze_scene",
    "scan_room",
    "describe_surroundings",
    "detect_object",
    "navigate_to",
    "adaptive_pick_object",
    "grasp",
    "place",
    "open_gripper",
    "close_gripper",
)


def cooperation_start_params(message: str) -> dict[str, Any]:
    """협동 시작 메시지에서 자동 참여에 사용할 목표를 추출한다."""
    match = re.search(r"협동 목표는\s*['\"](?P<goal>[^'\"]+)['\"]", str(message or ""))
    return {"goal": match.group("goal").strip()} if match else {}


class AutonomousCooperateSkill(BaseSkill):
    """동료 로봇과 지속적으로 대화하며 협동하는 자율협동 스킬 (백그라운드)."""

    name = "autonomous_cooperate"
    terminal_behavior = "background"
    description = (
        "동료로 등록된 로봇들과 지속적으로 자연어 메시지를 주고받으며 협동합니다. "
        "goal을 지정하면 해당 목표를 달성하기 위해 작업을 나누고 진행 상황을 공유합니다. "
        "수신된 동료 메시지에 LLM 이 응답을 결정하고, 주기적으로 자신의 상태를 점검해 "
        "동료에게 도움을 요청하거나 지시를 보냅니다. "
        "파라미터: goal(string, 선택 — 협동 목표), "
        "cycle_interval_sec(float, 기본 5.0 — 사이클 간 대기), "
        "proactive_interval_sec(float, 기본 30.0 — 능동 협동 점검 주기), "
        "max_cycles(int, 기본 0=무제한), "
        "중단: stop_autonomous_cooperate 스킬을 사용하세요."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "default": ""},
            "cycle_interval_sec": {"type": "number", "default": 5.0},
            "proactive_interval_sec": {"type": "number", "default": 30.0},
            "max_cycles": {"type": "integer", "default": 0},
            "allow_remote_task_execution": {"type": "boolean", "default": False},
            "remote_allowed_skills": {
                "type": "array",
                "items": {"type": "string"},
                "default": [
                    "analyze_scene",
                    "scan_room",
                    "describe_surroundings",
                    "detect_object",
                    "navigate_to",
                    "adaptive_pick_object",
                    "grasp",
                    "place",
                    "open_gripper",
                    "close_gripper",
                ],
            },
            "peer_worker_count": {"type": "integer", "default": 2},
        },
        "additionalProperties": False,
    }
    side_effects = ("background_loop",)

    def __init__(self) -> None:
        super().__init__()
        self._conversation = get_peer_conversation()
        self._inbox: PeerMessageInbox | None = None
        self._thread: threading.Thread | None = None
        self._incoming_executor: ThreadPoolExecutor | None = None
        self._peer_worker_count = 2
        self._introduced_peers: set[str] = set()
        self._goal = ""
        self._place_sweep_started = False

    def _journal(self) -> CooperationJournal:
        """로컬 런 관측 저널(전역 공유)."""
        return get_cooperation_journal()

    # ── 스킬 진입점 ───────────────────────────────────────────────

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None:
            return {"success": False, "message": "ROS 노드에 접근할 수 없습니다."}

        goal = str(params.get("goal", "")).strip()
        cycle_interval = float(params.get("cycle_interval_sec", 5.0))
        proactive_interval = float(params.get("proactive_interval_sec", 30.0))
        max_cycles = int(params.get("max_cycles", 0))
        allow_remote_tasks = bool(params.get("allow_remote_task_execution", False))
        remote_allowed_skills = {
            str(name).strip()
            for name in params.get("remote_allowed_skills", _DEFAULT_REMOTE_ALLOWED_SKILLS)
            if str(name).strip()
        }
        peer_worker_count = int(params.get("peer_worker_count", 2))
        if (
            not 0.1 <= cycle_interval
            or not 1.0 <= proactive_interval
            or max_cycles < 0
            or not 1 <= peer_worker_count <= 8
            or len(goal) > 500
        ):
            return {
                "success": False,
                "message": (
                    "파라미터 범위가 잘못되었습니다. cycle_interval_sec>=0.1, "
                    "proactive_interval_sec>=1.0, max_cycles>=0, "
                    "peer_worker_count는 1~8이어야 하며 goal은 500자 이하여야 합니다."
                ),
            }

        self._inbox = get_peer_message_inbox()
        self._introduced_peers.clear()
        self._goal = goal
        self._place_sweep_started = False
        self._allow_remote_task_execution = allow_remote_tasks
        self._remote_allowed_skills = remote_allowed_skills
        self._peer_worker_count = peer_worker_count
        self._incoming_executor = ThreadPoolExecutor(
            max_workers=peer_worker_count,
            thread_name_prefix="cooperation-peer",
        )

        if not claim_cooperation():
            return {
                "success": False,
                "message": "이미 자율협동이 실행 중입니다. stop_autonomous_cooperate 로 먼저 중단하세요.",
            }
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(
                cycle_interval,
                proactive_interval,
                max_cycles,
            ),
            daemon=True,
            name="autonomous-cooperate",
        )
        set_cooperation_thread(self._thread)
        self._journal().start_session(goal=goal)
        self._thread.start()

        logger.info("[Coop] autonomous_cooperate started")
        goal_message = f" 목표: {goal}" if goal else ""
        self.send_user_message(
            f"자율협동 모드를 시작합니다.{goal_message} 동료 로봇과 지속적으로 대화하며 협동합니다."
        )
        return {
            "success": True,
            "message": (
                f"자율협동을 시작했습니다.{goal_message} 동료 로봇과 작업을 나누며 협동합니다."
            ),
        }

    # ── 백그라운드 루프 ───────────────────────────────────────────

    def _run_loop(
        self,
        cycle_interval: float,
        proactive_interval: float,
        max_cycles: int,
    ) -> None:
        cycle = 0
        consumer_id = f"autonomous_cooperate:{id(self)}"
        if self._inbox is not None and not self._inbox.claim_consumer(consumer_id):
            # 소비권을 못 잡으면 루프는 시작되지 않는다. 세션을 열린 채 두면 관측
            # API 가 active=true 로 거짓 보고하므로 여기서 명시적으로 닫는다.
            logger.warning("[Coop] Inbox is owned by another consumer; stopping loop")
            if self._incoming_executor is not None:
                self._incoming_executor.shutdown(wait=False, cancel_futures=True)
                self._incoming_executor = None
            release_cooperation(wake=False)
            clear_cooperation_thread_if_current()
            self._journal().stop_session(reason="inbox_consumer_conflict")
            return
        pending: set[Future[Any]] = set()
        last_proactive = 0.0
        try:
            while is_cooperation_active():
                cycle += 1
                if max_cycles > 0 and cycle > max_cycles:
                    logger.info("[Coop] Reached max cycles (%d). Stopping.", max_cycles)
                    self.send_user_message("설정된 최대 사이클에 도달해 자율협동을 종료합니다.")
                    break

                # 1) 수신 메시지 처리 — 상관 ID를 보존한 구조화 API로 꺼낸다.
                if self._inbox is not None:
                    available = max(0, self._peer_worker_count - len(pending))
                    for item in self._inbox.drain_messages(limit=available, consumer=consumer_id):
                        if self._incoming_executor is not None:
                            pending.add(
                                self._incoming_executor.submit(
                                    self._process_incoming,
                                    item.peer_name,
                                    item.message,
                                    _envelope_from_inbox_item(item),
                                )
                            )
                for future in list(pending):
                    if future.done():
                        pending.remove(future)
                        try:
                            future.result()
                        except Exception as exc:  # noqa: BLE001
                            logger.exception("[Coop] Incoming message handling error: %s", exc)

                # 2) 주기적 능동 협동 점검
                self._start_place_sweep_if_needed()
                now = time.monotonic()
                if now - last_proactive >= proactive_interval:
                    last_proactive = now
                    try:
                        self._proactive_check()
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("[Coop] Proactive check error: %s", exc)

                # 3) 사이클 간 대기 (stop 시 즉시 깨어남)
                _COOPERATE_WAKE.clear()
                _COOPERATE_WAKE.wait(timeout=cycle_interval)
        finally:
            if self._inbox is not None:
                self._inbox.release_consumer(consumer_id)
            if self._incoming_executor is not None:
                self._incoming_executor.shutdown(wait=False, cancel_futures=True)
                self._incoming_executor = None
            release_cooperation(wake=False)
            clear_cooperation_thread_if_current()
            # 세션 종료는 로컬 정지 표시다. 실행 중/알 수 없는 태스크 상태는 바꾸지 않는다.
            self._journal().stop_session(reason="cooperation_loop_ended")
            logger.info("[Coop] autonomous_cooperate loop ended")
            self.send_user_message("자율협동을 종료했습니다.")

    # ── 수신 메시지 처리 ──────────────────────────────────────────

    def _process_incoming(
        self, peer_name: str, message: str, envelope: PeerEnvelope | None = None
    ) -> dict[str, Any]:
        """동료가 보낸 메시지를 대화 이력에 기록하고 LLM 으로 응답을 결정해 회신한다.

        ``envelope`` 의 상관 ID(task/message/session)는 회신까지 그대로 이어져
        양쪽 로봇이 같은 작업을 같은 ID로 추적한다.

        결과/실행상태 보고(status 또는 kind=result)는 LLM 이나 스킬 실행 경로로
        보내지 않는다. 우리가 그 태스크로 보낸 message_id 로 회신된 경우에만 관측
        결과로 반영하고, 상관이 틀리면 ``message_rejected``(unconfirmed)로 끝낸다.
        """
        journal = self._journal()
        envelope = envelope or envelope_from_transport(content=message)
        task_id = envelope.task_id or new_id()
        message_id = envelope.message_id or new_id()
        result_report = is_result_report(kind=envelope.kind, status=envelope.status)
        # 이 메시지 이전부터 알던 태스크만 결과 상관 대상이다(수신이 태스크를 만들면
        # 자기 자신과 상관되는 일을 막는다).
        known_task = journal.task(task_id)
        if known_task is None:
            details: dict[str, Any] = {"result_report": result_report}
            if envelope.status:
                details["reported_status"] = envelope.status
            if envelope.kind:
                details["kind"] = envelope.kind
            journal.record(
                EVENT_MESSAGE_RECEIVED,
                peer_id=peer_name,
                task_id=task_id,
                message_id=message_id,
                reply_to=envelope.reply_to,
                session_id=envelope.session_id or None,
                status="" if result_report else TASK_STATUS_RECEIVED,
                content=message,
                details=details,
            )
            if not result_report:
                journal.upsert_task(
                    task_id,
                    peer_id=peer_name,
                    session_id=envelope.session_id,
                    description=message,
                    status=TASK_STATUS_RECEIVED,
                )

        if result_report:
            reason = (
                "unknown_task"
                if known_task is None
                else self._result_correlation_failure(peer_name, envelope, task_id, known_task)
            )
            if not reason:
                self._record_observed_result(
                    peer_name, envelope, task_id, message_id, known_task or {}
                )
                return {"action": "observed_result", "reply": "", "decision": {}}
            self._record_unconfirmed_result(peer_name, envelope, task_id, message_id, reason=reason)
            return {
                "action": "unconfirmed_result",
                "reply": "",
                "decision": {},
                "reason": reason,
            }

        if known_task is not None and self._is_outbound_correlation(task_id, envelope):
            # 상관된 단순 회신 = 상대가 받았다는 확인일 뿐, 완료 추론이 아니다.
            journal.mark_delivery_confirmed(
                task_id,
                peer_id=peer_name,
                message_id=message_id,
                reply_to=envelope.reply_to,
            )

        self._conversation.add_message(peer_name, "peer", message)
        logger.info(
            "[Coop] Incoming peer=%s task=%s msg=%s reply_to=%s body=%s",
            peer_name,
            task_id,
            message_id,
            envelope.reply_to or "-",
            preview_text(message),
        )

        decision = self._decide_incoming_response(peer_name, message)
        action = decision.get("action", "respond")
        response = decision.get("response", "")
        task_instruction = decision.get("task_instruction", "")
        skill_name = str(decision.get("skill_name", "")).strip()
        skill_params = decision.get("skill_params", {})
        journal.record(
            EVENT_DECISION,
            peer_id=peer_name,
            task_id=task_id,
            message_id=message_id,
            session_id=envelope.session_id or None,
            content=str(response),
            details={
                "action": action,
                "reason": decision.get("reason", ""),
                "skill_name": skill_name,
            },
        )

        if action == "execute_task" and task_instruction:
            # 동료가 위임한 작업을 실행하고 결과를 회신한다. 대화 태스크 저장소에도
            # 전송 계층과 같은 task_id 로 기록해 LLM 컨텍스트가 실제 작업과 맞게 한다.
            self._conversation.start_task(peer_name, task_instruction, task_id=task_id)
            result = self._execute_task(
                task_instruction,
                skill_name,
                skill_params,
                task_id=task_id,
                message_id=message_id,
                peer_id=peer_name,
                session_id=envelope.session_id or None,
            )
            status = self._result_status(result)
            self._record_conversation_status(task_id, status, str(result.get("message", "")))
            label = _RESULT_LABELS.get(status, "작업 실패")
            reply = f"{label}: {result['message']}"
            self._conversation.add_message(peer_name, "self", reply)
            send_result = self._send_to_peer(
                peer_name,
                reply,
                task_id=task_id,
                reply_to=message_id,
                session_id=envelope.session_id,
                status=status,
                result=str(result.get("message", "")),
                kind=KIND_RESULT,
            )
            if not send_result.get("success"):
                logger.warning(
                    "[Coop] Failed to send task result to %s: %s", peer_name, send_result
                )
            return {
                "action": action,
                "reply": reply,
                "decision": decision,
                "task_status": status,
            }

        if action == "request_help" and not response:
            response = "현재 해당 요청을 처리할 수 없습니다."

        if not response:
            response = "메시지를 받았습니다. 현재 협동 목표와 다음 작업을 계속 조율하겠습니다."

        if response:
            self._conversation.add_message(peer_name, "self", response)
            send_result = self._send_to_peer(
                peer_name,
                response,
                task_id=task_id,
                reply_to=message_id,
                session_id=envelope.session_id,
                kind=KIND_REPLY,
            )
            if not send_result.get("success"):
                logger.warning("[Coop] Failed to send reply to %s: %s", peer_name, send_result)
            return {"action": action, "reply": response, "decision": decision}

        return {"action": action, "reply": "", "decision": decision}

    def _is_outbound_correlation(self, task_id: str, envelope: PeerEnvelope) -> bool:
        """이 회신의 reply_to 가 우리가 그 태스크로 보낸 메시지인지."""
        if not envelope.reply_to:
            return False
        return self._journal().is_outbound_message(task_id, envelope.reply_to)

    def _result_correlation_failure(
        self,
        peer_name: str,
        envelope: PeerEnvelope,
        task_id: str,
        known_task: dict[str, Any],
    ) -> str:
        """결과 보고의 상관 실패 사유. 상관이 맞으면 빈 문자열."""
        if normalize_task_status(envelope.status, default="") not in OBSERVED_RESULT_STATUSES:
            return "status_missing"
        task_peer = str(known_task.get("peer_id") or "")
        if task_peer and task_peer != peer_name:
            return "peer_mismatch"
        if not envelope.reply_to:
            return "reply_to_missing"
        if not self._journal().is_outbound_message(task_id, envelope.reply_to):
            return "reply_to_mismatch"
        return ""

    def _record_unconfirmed_result(
        self,
        peer_name: str,
        envelope: PeerEnvelope,
        task_id: str,
        message_id: str,
        *,
        reason: str,
    ) -> None:
        """상관이 맞지 않는 결과 보고를 거부 관측으로만 남긴다(실행/LLM 금지)."""
        self._journal().record(
            EVENT_MESSAGE_REJECTED,
            peer_id=peer_name,
            task_id=task_id,
            message_id=message_id,
            reply_to=envelope.reply_to,
            session_id=envelope.session_id or None,
            content=envelope.result or envelope.content,
            details={
                "reason": reason,
                "unconfirmed": True,
                "reported_status": envelope.status,
                "kind": envelope.kind,
            },
        )
        logger.warning(
            "[Coop] Unconfirmed result peer=%s task=%s msg=%s reason=%s",
            peer_name,
            task_id or "-",
            message_id or "-",
            reason,
        )

    def _record_observed_result(
        self,
        peer_name: str,
        envelope: PeerEnvelope,
        task_id: str,
        message_id: str,
        known_task: dict[str, Any],
    ) -> bool:
        """상관이 확인된 실행 결과를 관측 결과로 기록한다(LLM 재호출 금지)."""
        status = normalize_task_status(envelope.status, default="")
        if status not in OBSERVED_RESULT_STATUSES:
            return False
        journal = self._journal()
        result_text = envelope.result or envelope.content
        # 상대가 보고한 결과다 — 로컬 실행 결과(observed=False)와 구분해 표시한다.
        journal.mark_observed_result(task_id, status=status, result=result_text)
        journal.record(
            RESULT_EVENT_TYPES[status],
            peer_id=peer_name,
            task_id=task_id,
            message_id=message_id,
            reply_to=envelope.reply_to,
            session_id=envelope.session_id or None,
            status=status,
            content=result_text,
            details={"observed": True},
        )
        # 상관된 결과를 받았다는 것은 전달도 확인된 것이다.
        journal.mark_delivery_confirmed(
            task_id,
            peer_id=peer_name,
            message_id=message_id,
            reply_to=envelope.reply_to,
        )
        self._conversation.add_message(peer_name, "peer", envelope.content)
        return True

    def _record_conversation_status(self, task_id: str, status: str, message: str) -> None:
        """대화 태스크 저장소(LLM 컨텍스트)의 상태를 실행 결과에 맞춘다."""
        if status == TASK_STATUS_COMPLETED:
            self._conversation.complete_task(task_id, message)
        elif status == TASK_STATUS_FAILED:
            self._conversation.fail_task(task_id, message)
        else:
            self._conversation.update_task(task_id, note=message, status=status)

    @staticmethod
    def _result_status(result: dict[str, Any]) -> str:
        """실행 결과 dict 를 태스크 상태로 매핑한다."""
        status = normalize_task_status(result.get("task_status"), default="")
        if status:
            return status
        return TASK_STATUS_COMPLETED if result.get("success") else TASK_STATUS_FAILED

    def _decide_incoming_response(self, peer_name: str, message: str) -> dict[str, Any]:
        """동료 메시지에 대한 응답을 LLM 으로 결정한다. (테스트 가능)"""
        node = self.node
        if not node or not getattr(node, "_llm", None):
            return {
                "action": "respond",
                "response": "응답을 결정할 수 없습니다.",
                "reason": "LLM 없음",
            }

        state = self._gather_state()
        context = self._conversation.get_context(peer_name, limit=10)
        tasks_ctx = self._conversation.get_tasks_context(peer_name)
        goal_context = self._goal or "지정된 목표 없음"
        allowed_skills = ", ".join(sorted(getattr(self, "_remote_allowed_skills", set()))) or "없음"

        system_prompt = f"""당신은 자율협동 중인 로봇 에이전트입니다.
동료 로봇이 보낸 메시지에 대해 어떻게 응답할지 결정하세요.

[현재 협동 목표]
{goal_context}

[동료와의 대화 이력]
{context}

[진행 중인 협업 태스크]
{tasks_ctx}

[원격 실행 허용 스킬]
{allowed_skills}

[자신의 현재 상태]
{state}

[응답 형식 — 반드시 JSON만 출력]
{{"action": "respond"|"execute_task"|"request_help", "response": "<동료에게 보낼 응답>", "skill_name": "<execute_task일 때 allowlist 스킬명>", "skill_params": {{<스킬 입력 파라미터>}}, "task_instruction": "<action=execute_task일 때 실행할 지시>", "reason": "<판단 근거>"}}

[규칙]
- 동료가 작업을 위임/지시한 경우 action=execute_task 로 두고 task_instruction 에 실행할 지시를 넣으세요.
        - execute_task는 skill_name에 허용된 단일 스킬명과 skill_params를 반드시 명시하세요.
        - 원격 실행 허용 스킬 목록에 없는 skill_name은 선택하지 마세요.
- 동료가 도움을 요청했고 자신이 처리할 수 있으면 action=respond 로 도움을 주는 응답을 하세요.
- 동료가 도움을 요청했지만 자신이 처리할 수 없으면 action=request_help 로 다른 동료에게 재요청하거나, 처리 불가를 알리는 응답을 하세요.
 - 현재 협동 목표가 있으면 질문을 반복하지 말고 목표 달성에 필요한 구체적인 작업을 우선 실행하세요.
 - 정리 목표에서는 먼저 analyze_scene으로 대상을 확인한 뒤, 가능한 경우 이동·집기·놓기 작업을 실행하세요.
- 단순 대화/질문에는 action=respond 로 자연스럽게 응답하세요.
- JSON 외의 텍스트를 출력하지 마세요."""

        messages = [
            {
                "role": "user",
                "content": f"동료 로봇 [{peer_name}] 메시지: {message}\n\n이 메시지에 어떻게 응답할지 결정해주세요.",
            }
        ]

        try:
            response = self._chat_with_cancellation(node, messages, system_prompt)
            if response is None:
                return {}
            decision = self._parse_decision(response)
            if not decision:
                return {
                    "action": "respond",
                    "response": "메시지를 이해하지 못했습니다. 현재 협동 목표와 맡을 작업을 다시 알려주세요.",
                    "reason": "LLM 응답 JSON 파싱 실패",
                }
            return decision
        except Exception as exc:  # noqa: BLE001
            logger.error("[Coop] LLM decision failed: %s", exc)
            return {
                "action": "respond",
                "response": "응답을 결정하지 못했습니다.",
                "reason": str(exc),
            }

    # ── 능동 협동 점검 ────────────────────────────────────────────

    def _start_place_sweep_if_needed(self) -> None:
        if self._place_sweep_started:
            return
        self._place_sweep_started = True
        threading.Thread(
            target=self._run_place_sweep,
            daemon=True,
            name="cooperation-place-sweep",
        ).start()

    def _is_cleanup_goal(self) -> bool:
        goal = self._goal.lower()
        return any(keyword in goal for keyword in ("정리", "청소", "clean", "tidy", "organize"))

    def _semantic_sweep_places(self, limit: int = 8) -> list[dict[str, Any]]:
        node = self.node
        memory = getattr(node, "_memory", None) if node else None
        if memory is None:
            return []

        places: list[dict[str, Any]] = []
        try:
            from robo_claw_agent.skills.autonomous_skill.helpers import (
                _remembered_place_candidates,
            )

            for item in _remembered_place_candidates(memory, include_map_generated=False):
                position = item.get("position", {}) if isinstance(item, dict) else {}
                try:
                    x = float(position["x"])
                    y = float(position["y"])
                except (KeyError, TypeError, ValueError):
                    continue
                name = str(item.get("name") or "").strip()
                places.append({"name": name or f"시멘틱장소{len(places) + 1}", "x": x, "y": y})
                if len(places) >= limit:
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Coop] Semantic place lookup failed: %s", exc)
        return places

    def _run_place_sweep(self) -> None:
        node = self.node
        skills = getattr(node, "_skills", None) if node else None
        if skills is None or not hasattr(skills, "execute"):
            logger.warning("[Coop] Cleanup sweep skipped: skill manager unavailable")
            return

        places = self._semantic_sweep_places()
        if not places:
            self._send_goal_update(
                "RAG 시멘틱 맵에 순회할 장소가 없어 장소 순회를 시작하지 못했습니다."
            )
            return

        activity = "정리 대상" if self._is_cleanup_goal() else "새로운 이벤트와 환경 변화"
        self._send_goal_update(
            f"RAG 시멘틱 맵의 {len(places)}개 장소를 순회하며 {activity}를 확인합니다."
        )
        for index, place in enumerate(places, start=1):
            if not is_cooperation_active():
                return
            target = {"x": place["x"], "y": place["y"], "frame_id": "map"}
            # 순회는 좌표로 이동하므로 navigate_to 가 자리 선확인을 건너뛴다. 이미 아는
            # 장소 이름으로 동료에게 먼저 묻는다(실패해도 이동은 계속한다).
            precheck_yield_for_move(node, "navigate_to", target, place=place["name"])
            nav_result = skills.execute("navigate_to", target, timeout_sec=180.0)
            if not getattr(nav_result, "success", False):
                self._send_goal_update(
                    f"장소 {index}/{len(places)} '{place['name']}' 이동 실패: "
                    f"{getattr(nav_result, 'message', '알 수 없는 오류')}"
                )
                continue

            prompt = (
                f"'{place['name']}' 주변의 정리 대상과 위치를 확인해줘."
                if self._is_cleanup_goal()
                else f"'{place['name']}' 주변의 새로운 이벤트, 환경 변화, 객체, 도움 요청 상황을 확인해줘."
            )
            inspect_result = skills.execute(
                "analyze_scene",
                {"prompt": prompt},
                timeout_sec=60.0,
            )
            summary = getattr(inspect_result, "message", "분석 결과 없음")
            self._send_goal_update(
                f"장소 {index}/{len(places)} '{place['name']}' 확인 완료: {summary}"
            )

    def _send_goal_update(self, message: str) -> None:
        logger.info("[Coop] goal_update body=%s", preview_text(message))
        for peer in self._discover_peers():
            result = self._send_to_peer(peer, message)
            if not result.get("success"):
                logger.warning("[Coop] Goal update to %s failed: %s", peer, result)

    def _proactive_check(self) -> list[dict[str, Any]]:
        """자신의 상태를 점검해 동료에게 도움 요청/지시를 보낼지 LLM 으로 판단한다."""
        node = self.node
        if not node:
            return []

        peers = self._discover_peers()
        if not peers:
            return []

        sent: list[dict[str, Any]] = []
        for peer in peers:
            if peer in self._introduced_peers:
                continue
            greeting = "자율협동을 시작했습니다. 현재 상태를 공유하고 서로 도움을 주고받겠습니다."
            if self._goal:
                greeting += (
                    f" 협동 목표는 '{self._goal}'입니다. 목표 달성을 위해 맡을 수 있는 작업과 "
                    "현재 상태를 알려주세요. 첫 단계로 analyze_scene을 실행해 정리 대상과 "
                    "위치를 공유하고, 질문만 반복하지 말고 가능한 작업을 바로 수행해주세요."
                )
            result = self._send_to_peer(peer, greeting)
            if result.get("success"):
                self._conversation.add_message(peer, "self", greeting)
                self._introduced_peers.add(peer)
            sent.append({"peer": peer, "content": greeting, "result": result})

        # LLM이 설정되지 않아도 시작 알림과 수신 메시지 처리는 동작해야 한다.
        if not getattr(node, "_llm", None):
            return sent

        state = self._gather_state()
        goal_context = self._goal or "지정된 목표 없음"
        context_lines = []
        for peer in peers:
            ctx = self._conversation.get_context(peer, limit=5)
            context_lines.append(f"[{peer}]\n{ctx}")
        context_str = "\n".join(context_lines) if context_lines else "  • 대화 이력 없음"

        system_prompt = f"""당신은 자율협동 중인 로봇 에이전트입니다.
현재 상황에서 동료 로봇에게 도움을 요청하거나 지시를 보낼지 결정하세요.

[현재 협동 목표]
{goal_context}

[자신의 현재 상태]
{state}

[동료 목록]
{", ".join(peers)}

[동료별 대화 이력]
{context_str}

[응답 형식 — 반드시 JSON만 출력]
{{"messages": [{{"peer": "<동료명>", "content": "<보낼 메시지>"}}], "reason": "<판단 근거>"}}

[규칙]
        - 협동 목표가 있으면 질문이나 구역 협의만 반복하지 말고 목표 달성에 필요한 다음 작업을 바로 실행하세요.
        - 정리 목표의 첫 단계는 analyze_scene으로 정리 대상과 위치를 확인하고 결과를 공유하는 것입니다.
        - 이후 가능한 경우 navigate_to -> adaptive_pick_object 또는 grasp -> place 순서로 작업하세요.
- 목표가 없을 때는 도움 요청/지시가 필요할 때만 messages 를 채우세요. 필요 없으면 빈 배열을 반환하세요.
- 예: 정리 목표의 구역을 나누거나, 처리할 수 없는 물체를 발견해 동료에게 위임할 때.
- 동료명은 반드시 목록에 있는 이름을 사용하세요.
- JSON 외의 텍스트를 출력하지 마세요."""

        messages = [
            {
                "role": "user",
                "content": "현재 상황에서 동료 로봇에게 보낼 메시지를 결정해주세요.",
            }
        ]

        try:
            response = self._chat_with_cancellation(node, messages, system_prompt)
            if response is None:
                return []
            decision = self._parse_decision(response)
            for item in decision.get("messages", []):
                peer = item.get("peer", "")
                content = item.get("content", "")
                if peer in peers and content:
                    self._conversation.add_message(peer, "self", content)
                    self._send_to_peer(peer, content)
                    sent.append({"peer": peer, "content": content})
            return sent
        except Exception as exc:  # noqa: BLE001
            logger.error("[Coop] Proactive check failed: %s", exc)
            return []

    # ── 헬퍼 ──────────────────────────────────────────────────────

    def _chat_with_cancellation(
        self, node: Any, messages: list[dict[str, str]], system_prompt: str
    ) -> str | None:
        """LLM 자체가 취소 API를 제공하지 않아 stop 시 결과를 폐기한다."""
        done = threading.Event()
        result: dict[str, Any] = {}

        def run() -> None:
            try:
                result["value"] = node._llm.chat(messages, system_prompt=system_prompt)
            except Exception as exc:  # noqa: BLE001
                result["error"] = exc
            finally:
                done.set()

        threading.Thread(target=run, daemon=True, name="cooperation-llm").start()
        cancellable = is_cooperation_active()
        while not done.wait(timeout=0.1):
            if cancellable and not is_cooperation_active():
                return None
        if "error" in result:
            raise result["error"]
        return str(result.get("value", ""))

    def _parse_decision(self, raw: str) -> dict[str, Any]:
        """LLM 응답에서 JSON 객체를 추출한다."""
        clean = re.sub(r"```(?:json)?\s*\n?", "", raw).strip()
        try:
            from robo_claw_agent.agent_node.utils import _extract_first_json_object

            json_str = _extract_first_json_object(clean)
            if "{" not in json_str:
                return {}
            decision = json.loads(json_str)
            if not isinstance(decision, dict):
                return {}
            messages = decision.get("messages")
            if messages is not None and not isinstance(messages, list):
                decision["messages"] = []
            return decision
        except Exception as exc:  # noqa: BLE001
            logger.error("[Coop] Failed to parse LLM decision: %s", exc)
            return {}

    def _gather_state(self) -> str:
        """자신의 현재 상태(배터리/위치/감지 객체/진행 태스크)를 문자열로 수집한다."""
        node = self.node
        parts = []
        try:
            pose = self.get_map_pose()
            if pose:
                parts.append(f"위치: x={pose['x']:.2f}, y={pose['y']:.2f}")
        except Exception:
            pass
        try:
            from sensor_msgs.msg import BatteryState  # type: ignore[import]

            msg = self.wait_for_message(BatteryState, "/battery_state", timeout_sec=1.0)
            if msg is not None:
                pct = msg.percentage * 100.0 if 0.0 <= msg.percentage <= 1.0 else msg.percentage
                parts.append(f"배터리: {pct:.0f}%")
        except Exception:
            pass
        if node is not None and hasattr(node, "_memory"):
            try:
                objs = node._memory.get_all_objects()
                if objs:
                    parts.append(f"알고 있는 장소/객체: {len(objs)}개")
            except Exception:
                pass
        return "; ".join(parts) if parts else "상태 정보 없음"

    def _discover_peers(self) -> list[str]:
        node = self.node
        if node is None:
            return []
        try:
            from robo_claw_agent.skills.autonomous_skill.helpers import _discover_peer_agents

            peers = list(_discover_peer_agents(node).keys())
            if peers:
                return peers
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Coop] Peer discovery failed (ignored): %s", exc)

        # 동일 ROS 그래프에서 에이전트 노드 이름이 같으면 그래프 탐색만으로
        # 피어를 구분할 수 없다. 연결된 gRPC 피어 목록을 보조 경로로 사용한다.
        client = getattr(node, "_list_peers_client", None)
        if client is None:
            return []
        try:
            from robo_claw_msgs.srv import ListPeers  # type: ignore[import]

            ok, response = ros_future.call_service(
                client,
                ListPeers.Request(),
                call_timeout_sec=10.0,
                not_found_message="ListPeers 서비스를 찾을 수 없습니다.",
                timeout_message="피어 목록 조회 시간 초과",
            )
            if not ok or not getattr(response, "success", False):
                return []
            return [peer.peer_name for peer in response.peers if peer.connected and peer.peer_name]
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Coop] Connected peer discovery failed (ignored): %s", exc)
            return []

    def _execute_task(
        self,
        instruction: str,
        skill_name: str = "",
        skill_params: dict[str, Any] | None = None,
        *,
        task_id: str = "",
        message_id: str = "",
        peer_id: str = "",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """동료가 위임한 작업을 실행하고 결과를 저널에 남긴다(``remote_task`` 위임)."""
        return execute_remote_task(
            node=self.node,
            instruction=instruction,
            skill_name=skill_name,
            skill_params=skill_params,
            allow_remote_execution=bool(getattr(self, "_allow_remote_task_execution", False)),
            allowed_skills=getattr(self, "_remote_allowed_skills", set()),
            journal=self._journal(),
            task_id=task_id,
            message_id=message_id,
            peer_id=peer_id,
            session_id=session_id,
        )

    def _send_to_peer(
        self,
        peer_name: str,
        message: str,
        *,
        task_id: str = "",
        reply_to: str = "",
        session_id: str = "",
        status: str = "",
        result: str = "",
        kind: str = "",
    ) -> dict[str, Any]:
        """동료 로봇에게 메시지를 전달한다(``peer_transport`` 위임)."""
        return send_cooperation_message(
            node=self.node,
            peer_name=peer_name,
            message=message,
            task_id=task_id,
            reply_to=reply_to,
            session_id=session_id,
            status=status,
            result=result,
            kind=kind,
            journal=self._journal(),
        )


class StopAutonomousCooperateSkill(BaseSkill):
    """자율협동 루프를 즉시 중단한다."""

    name = "stop_autonomous_cooperate"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    description = "현재 진행 중인 자율협동 루프를 즉시 중단합니다."

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if not is_cooperation_active():
            return {"success": False, "message": "현재 진행 중인 자율협동이 없습니다."}
        release_cooperation()
        thread = get_cooperation_thread()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.1)
        logger.info("[Coop] stop_autonomous_cooperate called")
        return {"success": True, "message": "자율협동을 중단했습니다."}
