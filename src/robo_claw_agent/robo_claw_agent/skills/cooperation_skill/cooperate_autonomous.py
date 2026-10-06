"""자율협동 스킬 — 동료 로봇과 지속적 자연어 대화로 도움 요청/지시/판단을 수행한다.

`autonomous_cooperate` 는 백그라운드 루프를 시작해:
  1. 수신 인박스(PeerMessageInbox)에서 동료가 보낸 메시지를 꺼내 LLM 으로 응답을 결정하고
     `call_peer_robot` 로 회신한다.
  2. 주기적으로 자신의 상태를 점검해 동료에게 도움 요청/지시를 능동적으로 보낼지 LLM 으로
     판단한다.
  3. 모든 대화를 PeerConversationManager 에 기록해 맥락을 유지한다.

ROS2 의존은 lazy import 로 감싸, rclpy 없이도 단위 테스트가 가능하다.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from . import globals as _coop_globals
from .conversation import get_peer_conversation
from .globals import _COOPERATE_ACTIVE, _COOPERATE_LOCK, _COOPERATE_WAKE
from .inbox import PeerMessageInbox, get_peer_message_inbox
from .policy import evaluate_remote_skill

logger = logging.getLogger(__name__)

# 동료 호출 로컬 대기 상한 (cooperate_skill 의 _DEFAULT_PEER_CALL_LOCAL_WAIT_SEC 와 동일 정신)
_DEFAULT_PEER_CALL_LOCAL_WAIT_SEC = 300.0
_SERVICE_CALL_MARGIN_SEC = 5.0
_COOPERATION_MESSAGE_TYPE = "peer_cooperation"
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


def _local_wait_sec(timeout_sec: float) -> float:
    return timeout_sec + _SERVICE_CALL_MARGIN_SEC if timeout_sec else _DEFAULT_PEER_CALL_LOCAL_WAIT_SEC


def _wait_for_future(future: Any, timeout_sec: float, message: str) -> tuple[bool, Any]:
    done = threading.Event()
    future.add_done_callback(lambda _: done.set())
    if not done.wait(timeout=timeout_sec):
        return False, message
    return True, future.result()


def _call_service(
    client: Any,
    request: Any,
    call_timeout_sec: float,
    not_found_message: str,
    timeout_message: str,
) -> tuple[bool, Any]:
    if not client.wait_for_service(timeout_sec=5.0):
        return False, not_found_message
    future = client.call_async(request)
    return _wait_for_future(future, call_timeout_sec, timeout_message)


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

        with _COOPERATE_LOCK:
            if _COOPERATE_ACTIVE.is_set() or (
                _coop_globals._COOPERATE_THREAD is not None
                and _coop_globals._COOPERATE_THREAD.is_alive()
            ):
                return {
                    "success": False,
                    "message": "이미 자율협동이 실행 중입니다. stop_autonomous_cooperate 로 먼저 중단하세요.",
                }
            _COOPERATE_ACTIVE.set()
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
            _coop_globals._COOPERATE_THREAD = self._thread
            self._thread.start()

        logger.info("[Coop] autonomous_cooperate started")
        goal_message = f" 목표: {goal}" if goal else ""
        self.send_user_message(
            f"자율협동 모드를 시작합니다.{goal_message} 동료 로봇과 지속적으로 대화하며 협동합니다."
        )
        return {
            "success": True,
            "message": (
                f"자율협동을 시작했습니다.{goal_message} "
                "동료 로봇과 작업을 나누며 협동합니다."
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
            logger.warning("[Coop] Inbox is owned by another consumer; stopping loop")
            _COOPERATE_ACTIVE.clear()
            return
        pending: set[Future[Any]] = set()
        last_proactive = 0.0
        try:
            while _COOPERATE_ACTIVE.is_set():
                cycle += 1
                if max_cycles > 0 and cycle > max_cycles:
                    logger.info("[Coop] Reached max cycles (%d). Stopping.", max_cycles)
                    self.send_user_message("설정된 최대 사이클에 도달해 자율협동을 종료합니다.")
                    break

                # 1) 수신 메시지 처리
                if self._inbox is not None:
                    available = max(0, self._peer_worker_count - len(pending))
                    for peer_name, message in self._inbox.drain(
                        limit=available, consumer=consumer_id
                    ):
                        if self._incoming_executor is not None:
                            pending.add(
                                self._incoming_executor.submit(
                                    self._process_incoming, peer_name, message
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
            _COOPERATE_ACTIVE.clear()
            with _COOPERATE_LOCK:
                if _coop_globals._COOPERATE_THREAD is threading.current_thread():
                    _coop_globals._COOPERATE_THREAD = None
            logger.info("[Coop] autonomous_cooperate loop ended")
            self.send_user_message("자율협동을 종료했습니다.")

    # ── 수신 메시지 처리 ──────────────────────────────────────────

    def _process_incoming(self, peer_name: str, message: str) -> dict[str, Any]:
        """동료가 보낸 메시지를 대화 이력에 기록하고 LLM 으로 응답을 결정해 회신한다."""
        self._conversation.add_message(peer_name, "peer", message)
        logger.info("[Coop] Incoming from %s: %s", peer_name, message)

        decision = self._decide_incoming_response(peer_name, message)
        action = decision.get("action", "respond")
        response = decision.get("response", "")
        task_instruction = decision.get("task_instruction", "")
        skill_name = str(decision.get("skill_name", "")).strip()
        skill_params = decision.get("skill_params", {})

        if action == "execute_task" and task_instruction:
            # 동료가 위임한 작업을 실행하고 결과를 회신
            result = self._execute_task(task_instruction, skill_name, skill_params)
            status = "작업 완료" if result["success"] else "작업 실패"
            reply = f"{status}: {result['message']}"
            self._conversation.add_message(peer_name, "self", reply)
            send_result = self._send_to_peer(peer_name, reply)
            if not send_result.get("success"):
                logger.warning("[Coop] Failed to send task result to %s: %s", peer_name, send_result)
            return {"action": action, "reply": reply, "decision": decision}

        if action == "request_help" and not response:
            response = "현재 해당 요청을 처리할 수 없습니다."

        if not response:
            response = "메시지를 받았습니다. 현재 협동 목표와 다음 작업을 계속 조율하겠습니다."

        if response:
            self._conversation.add_message(peer_name, "self", response)
            send_result = self._send_to_peer(peer_name, response)
            if not send_result.get("success"):
                logger.warning("[Coop] Failed to send reply to %s: %s", peer_name, send_result)
            return {"action": action, "reply": response, "decision": decision}

        return {"action": action, "reply": "", "decision": decision}

    def _decide_incoming_response(self, peer_name: str, message: str) -> dict[str, Any]:
        """동료 메시지에 대한 응답을 LLM 으로 결정한다. (테스트 가능)"""
        node = self.node
        if not node or not getattr(node, "_llm", None):
            return {"action": "respond", "response": "응답을 결정할 수 없습니다.", "reason": "LLM 없음"}

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
            return {"action": "respond", "response": "응답을 결정하지 못했습니다.", "reason": str(exc)}

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
        seen: set[tuple[str, float, float]] = set()
        try:
            from robo_claw_agent.skills.autonomous_skill.helpers import (
                _extract_rag_location_candidates,
                _split_semantic_places,
            )

            all_objects = list(memory.get_all_objects()) if hasattr(memory, "get_all_objects") else []
            semantic_places, _ = _split_semantic_places(all_objects)
            candidates = semantic_places + _extract_rag_location_candidates(memory, limit=limit * 2)
            for item in candidates:
                position = item.get("position", {}) if isinstance(item, dict) else {}
                try:
                    x = float(position["x"])
                    y = float(position["y"])
                except (KeyError, TypeError, ValueError):
                    continue
                name = str(item.get("name") or "").strip()
                key = (name, round(x, 3), round(y, 3))
                if key in seen:
                    continue
                seen.add(key)
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
            self._send_goal_update("RAG 시멘틱 맵에 순회할 장소가 없어 장소 순회를 시작하지 못했습니다.")
            return

        activity = "정리 대상" if self._is_cleanup_goal() else "새로운 이벤트와 환경 변화"
        self._send_goal_update(
            f"RAG 시멘틱 맵의 {len(places)}개 장소를 순회하며 {activity}를 확인합니다."
        )
        for index, place in enumerate(places, start=1):
            if not _COOPERATE_ACTIVE.is_set():
                return
            target = {"x": place["x"], "y": place["y"], "frame_id": "map"}
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
        logger.info("[Coop] %s", message)
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
{', '.join(peers)}

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
        cancellable = _COOPERATE_ACTIVE.is_set()
        while not done.wait(timeout=0.1):
            if cancellable and not _COOPERATE_ACTIVE.is_set():
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

            ok, response = _call_service(
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
    ) -> dict[str, Any]:
        """동료가 위임한 작업을 스킬 매니저로 실행하고 결과 메시지를 반환한다."""
        if not getattr(self, "_allow_remote_task_execution", False):
            return {"success": False, "message": "원격 작업 실행이 안전 정책에 의해 차단되었습니다."}
        allowed = getattr(self, "_remote_allowed_skills", set())
        if not allowed or not skill_name or skill_name not in allowed:
            return {"success": False, "message": "원격 작업 스킬이 allowlist에 없습니다."}
        if not isinstance(skill_params, dict):
            return {"success": False, "message": "원격 작업 스킬 파라미터 형식이 잘못되었습니다."}
        node = self.node
        if node is None or not hasattr(node, "_skills"):
            return {"success": False, "message": "스킬 매니저에 접근할 수 없습니다."}
        get_skill = getattr(node._skills, "get_skill", None)
        if callable(get_skill):
            skill = get_skill(skill_name)
            if skill is None:
                return {
                    "success": False,
                    "message": f"원격 작업 스킬이 현재 로봇에 등록되어 있지 않습니다: {skill_name}",
                    "skill_name": skill_name,
                }
            policy = evaluate_remote_skill(
                skill,
                enabled=True,
                allowlisted=True,
            )
            if not policy.allowed:
                message = (
                    f"위험도가 높은 스킬은 원격 실행할 수 없습니다: {skill_name}"
                    if policy.reason.startswith("remote_risk_level_")
                    else f"원격 작업 정책으로 실행하지 않았습니다: {policy.reason}"
                )
                return {
                    "success": False,
                    "message": message,
                    "skill_name": skill_name,
                    **policy.as_dict(),
                }
        else:
            return {
                "success": False,
                "message": f"원격 작업 스킬을 확인할 수 없습니다: {skill_name}",
                "skill_name": skill_name,
                "policy_decision": "deny",
                "policy_reason": "skill_lookup_unavailable",
                "fallback_action": "query_peer_capabilities",
            }
        try:
            timeout_sec = float(policy.limits.get("max_duration_sec", 120.0))
            result = node._skills.execute(skill_name, skill_params, timeout_sec=timeout_sec)
            response = {
                "success": bool(getattr(result, "success", False)),
                "message": str(getattr(result, "message", "완료"))
                + (f" (지시: {instruction})" if instruction else ""),
                "skill_name": skill_name,
            }
            response.update(policy.as_dict())
            if not response["success"]:
                response["fallback_action"] = "report_partial_result"
            return response
        except Exception as exc:  # noqa: BLE001
            logger.error("[Coop] Task execution error: %s", exc)
            return {"success": False, "message": f"작업 실행 실패: {exc}", "skill_name": skill_name}

    def _send_to_peer(self, peer_name: str, message: str) -> dict[str, Any]:
        """동료 로봇에게 메시지를 전달한다 (CallPeerRobot 서비스 경유)."""
        if not _COOPERATE_ACTIVE.is_set():
            return {"success": False, "message": "자율협동이 중단되었습니다."}
        node = self.node
        if node is None or not hasattr(node, "_call_peer_client"):
            return {"success": False, "message": "피어 호출 클라이언트가 설정되지 않았습니다."}

        from robo_claw_msgs.srv import CallPeerRobot  # type: ignore[import]

        req = CallPeerRobot.Request()
        req.peer_name = peer_name
        req.instruction = json.dumps(
            {
                "_robo_claw_message_type": _COOPERATION_MESSAGE_TYPE,
                "content": message,
                "task_id": uuid.uuid4().hex,
                "created_at": time.time(),
                "expires_at": time.time() + 300.0,
            },
            ensure_ascii=False,
        )
        req.file_path = ""
        req.wait_for_result = False  # 대화는 비동기로 전달
        req.timeout_sec = 0.0

        ok, resp = _call_service(
            node._call_peer_client,
            req,
            call_timeout_sec=_local_wait_sec(0.0),
            not_found_message="CallPeerRobot 서비스를 찾을 수 없습니다.",
            timeout_message="동료 로봇 호출 시간 초과",
        )
        if not ok:
            logger.warning("[Coop] Failed to send to %s: %s", peer_name, resp)
            return {"success": False, "message": str(resp)}
        return {
            "success": bool(resp.success),
            "message": resp.result_message or resp.error_message or "응답 없음",
        }


class StopAutonomousCooperateSkill(BaseSkill):
    """자율협동 루프를 즉시 중단한다."""

    name = "stop_autonomous_cooperate"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    description = "현재 진행 중인 자율협동 루프를 즉시 중단합니다."

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if not _COOPERATE_ACTIVE.is_set():
            return {"success": False, "message": "현재 진행 중인 자율협동이 없습니다."}
        _COOPERATE_ACTIVE.clear()
        _COOPERATE_WAKE.set()
        thread = _coop_globals._COOPERATE_THREAD
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.1)
        logger.info("[Coop] stop_autonomous_cooperate called")
        return {"success": True, "message": "자율협동을 중단했습니다."}
