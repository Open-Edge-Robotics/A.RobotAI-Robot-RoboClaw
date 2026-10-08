import json
import logging
import re
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .core import BTNode, NodeStatus
from .helpers import (
    _discover_peer_agents,
    _extract_rag_location_candidates,
    _find_connected_peer_with_capability,
    _format_place,
    _has_cleanup_capability,
    _self_capabilities,
    _split_semantic_places,
)

logger = logging.getLogger(__name__)

_AUTONOMOUS_EXCLUDED_SKILLS = frozenset(
    {
        "autonomous_act",
        "stop_autonomous",
        "autonomous_cooperate",
        "stop_autonomous_cooperate",
        "reactive_navigate",
        "condition_reactive",
        "explore",
        "patrol",
        "stop_patrol",
    }
)
_CLEANUP_ACTION_SKILLS = frozenset(
    {
        "adaptive_pick_object",
        "vla_pick_gripper_object",
        "vla_pick_front_object",
        "pick_front_object",
        "pick_from_right_side_zone",
        "grasp",
        "place",
    }
)
_LOCAL_OBSERVATION_SKILLS = frozenset(
    {
        "analyze_scene",
        "capture_camera_image",
        "describe_surroundings",
        "get_distance",
        "get_status",
        "identify_location",
        "list_topics",
        "log_observation",
        "say",
        "send_message",
    }
)


def _available_autonomous_skills(node: Any, *, local_only: bool = False) -> list[dict[str, Any]]:
    skills = getattr(node, "_skills", None)
    if skills is None or not hasattr(skills, "list_skills"):
        return []

    available = []
    for skill_info in skills.list_skills():
        name = str(skill_info.get("name", ""))
        if not name or name in _AUTONOMOUS_EXCLUDED_SKILLS:
            continue
        if local_only and name not in _LOCAL_OBSERVATION_SKILLS:
            continue
        get_skill = getattr(skills, "get_skill", None)
        skill_obj = get_skill(name) if callable(get_skill) else None
        if getattr(skill_obj, "terminal_behavior", None) == "background":
            continue
        available.append(skill_info)
    return available


def _matches_remembered_coordinate(memory: Any, x: Any, y: Any, tolerance: float = 0.15) -> bool:
    try:
        target_x, target_y = float(x), float(y)
        objects = memory.get_all_objects()
        semantic_places, map_generated_places = _split_semantic_places(objects)
        remembered = semantic_places + map_generated_places
        remembered += _extract_rag_location_candidates(memory, limit=20)
        return any(
            abs(float(place["position"]["x"]) - target_x) <= tolerance
            and abs(float(place["position"]["y"]) - target_y) <= tolerance
            for place in remembered
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


_LOCATION_PARAM_KEYS = ("target_name", "target_location")


def _peek_next_action(blackboard: dict[str, Any]) -> tuple[str, dict[str, Any], bool]:
    task_queue = blackboard.get("task_queue", [])
    if task_queue:
        step = task_queue[0]
        return step.get("skill", ""), step.get("params", {}) or {}, True
    return blackboard.get("decided_skill", ""), blackboard.get("decided_params", {}) or {}, False


class LLMDecideAction(BTNode):
    """LLM에게 현재 상황을 주고 다음 행동(단일 또는 다단계 계획)을 결정받는다."""

    def __init__(self, skill: BaseSkill) -> None:
        super().__init__("LLMDecideAction")
        self._skill = skill

    def tick(self) -> NodeStatus:
        if self._blackboard.get("cleanup_handoff_prepared") or self._blackboard.get(
            "cleanup_handoff_blocked"
        ):
            # 정리 위임/불가 판정은 코드에서 확정하며 LLM 재계획으로 덮어쓰지 않는다.
            return NodeStatus.SUCCESS

        node = self._skill.node
        if not node or not getattr(node, "_llm", None):
            return NodeStatus.FAILURE

        if self._blackboard.get("task_queue"):
            return NodeStatus.SUCCESS

        scene = self._blackboard.get("scene_analysis", "장면 분석 정보 없음")
        objects = self._blackboard.get("detected_objects", [])
        coverage = self._blackboard.get("current_map_coverage", "알 수 없음")
        cycle = self._blackboard.get("cycle_count", 0)

        history_str = ""
        task_history = self._blackboard.get("task_history", [])
        if task_history:
            history_str = "\n".join(
                f"  - [사이클 {h['cycle']}] {h['decided_skill']}({h['decided_params']}) -> {'성공' if h['success'] else '실패'} (이유: {h['decided_reason']})"
                for h in task_history[-5:]
            )
        else:
            history_str = "  - 없음 (자율 행동 시작 단계)"

        invalid_reason = self._blackboard.pop("decision_invalid_reason", "")
        if invalid_reason:
            history_str += (
                f"\n  - [경고] 직전 결정이 무효하여 실행하지 않고 폐기됨: {invalid_reason}"
            )

        if self._blackboard.get("repeat_failure_count", 0) >= 2:
            history_str += (
                f"\n  - [경고] 동일한 (스킬, 목적지) 조합이 "
                f"{self._blackboard['repeat_failure_count']}회 연속 실패했습니다. "
                "반드시 다른 장소나 다른 행동을 선택하세요."
            )

        semantic_places_str = "  • 없음"
        rag_places_str = "  • 없음"
        map_generated_places_str = "  • 없음"
        if hasattr(node, "_memory"):
            try:
                all_objs = node._memory.get_all_objects()
                semantic_places, map_generated_places = _split_semantic_places(all_objs)
                if semantic_places:
                    semantic_places_str = "\n".join(_format_place(obj) for obj in semantic_places)
                rag_candidates = _extract_rag_location_candidates(node._memory, limit=5)
                if rag_candidates:
                    rag_places_str = "\n".join(_format_place(obj) for obj in rag_candidates)
                if map_generated_places:
                    map_generated_places_str = "\n".join(
                        _format_place(obj) for obj in map_generated_places
                    )
            except Exception as e:
                logger.warning("[BT] Failed to query location: %s", e)
                semantic_places_str = "  • 조회 실패"
        else:
            semantic_places_str = "  • 미지원"

        robot_pose_str = "알 수 없음"
        try:
            pose = self._skill.get_map_pose()
            if pose:
                robot_pose_str = f"x={pose['x']:.2f}, y={pose['y']:.2f}"
        except Exception:
            pass

        other_agents = _discover_peer_agents(node)
        peer_status_cache = self._blackboard.get("peer_status_cache", {})

        agent_lines = []
        for name, info in other_agents.items():
            role = info.get("role") or "역할 미지정"
            desc = info.get("description") or "설명 없음"
            caps = info.get("capabilities") or []
            caps_str = f", 능력: {', '.join(caps)}" if caps else ""
            status_entry = peer_status_cache.get(name)
            status_str = f", 상태: {status_entry['summary']}" if status_entry else ""
            agent_lines.append(f"  - {name} (역할: {role}{caps_str}): {desc}{status_str}")
        agents_list_str = "\n".join(agent_lines) if agent_lines else "  - 없음"

        # 자율협동 — 동료와의 최근 대화 컨텍스트 주입
        peer_conversation_ctx = ""
        try:
            from robo_claw_agent.skills.cooperation_skill.bt_nodes import (
                build_peer_conversation_context,
            )

            peer_conversation_ctx = build_peer_conversation_context(self._blackboard)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[BT] Peer conversation context unavailable (ignored): %s", exc)

        local_only = bool(self._blackboard.get("stationary_observation_only")) or (
            self._blackboard.get("patrol_has_destination") is False
        )
        available_skills = _available_autonomous_skills(node, local_only=local_only)
        cleanup_task_mode = bool(self._blackboard.get("cleanup_task_mode"))
        if cleanup_task_mode:
            available_skills = [
                skill for skill in available_skills if skill.get("name") in _CLEANUP_ACTION_SKILLS
            ]
        skill_list = "\n".join(
            f"  - {s['name']}: {s.get('description', '')}" for s in available_skills
        )

        rag_context = ""
        if (
            hasattr(node, "_memory")
            and node._memory.rag_status().get("enabled")
            and scene != "장면 분석 정보 없음"
        ):
            try:
                query_parts = []
                if objects:
                    query_parts.append(f"감지 객체: {', '.join(objects)}")
                if scene:
                    query_parts.append(f"장면: {scene[:200]}")
                query_str = ". ".join(query_parts) if query_parts else "상황 묘사 없음"

                hits = node._memory.search_knowledge(query_str, top_k=3)
                if hits:
                    entries = "\n".join(f"  • {h['text']}" for h in hits)
                    rag_context = f"\n[과거 관련 관찰]\n{entries}\n"
                    logger.debug("[BT] Injected %d RAG search results", len(hits))
            except Exception as exc:
                logger.debug("[BT] RAG search failed (ignored): %s", exc)

        robot_soul = getattr(node, "_robot_soul", "")
        soul_prefix = f"{robot_soul}\n\n---\n\n" if robot_soul else ""

        self_caps = _self_capabilities(node)
        self_caps_str = ", ".join(self_caps) if self_caps else "없음"
        delegate_force_hint = ""
        handoff_status = str(self._blackboard.get("cleanup_handoff_status", "") or "")
        cleanup_capable = _has_cleanup_capability(node)
        if not cleanup_capable:
            manipulation_peer = (
                None
                if handoff_status
                else _find_connected_peer_with_capability(node, "manipulation")
            )
            if manipulation_peer:
                delegate_force_hint = (
                    f"\n- [위임 필수] 당신에게 매니퓰레이션(파지/배치) 능력이 없습니다. "
                    f"정리 대상은 연결된 조작 가능 동료 '{manipulation_peer['peer_name']}'에게만 "
                    "단발 작업으로 위임하세요. 직접 grasp/place를 시도하거나 자율협동을 시작하지 마세요."
                )
            else:
                delegate_force_hint = (
                    "\n- [위임 제한] 자신에게 매니퓰레이션 능력이 없습니다. 연결 여부와 조작 능력이 "
                    "확인되지 않은 동료에게 요청하지 말고, 정리를 완료했다고 주장하지 마세요. "
                    "대상을 관찰·기록하고 처리할 수 없음을 사용자에게 보고하세요."
                )

        cleanup_disposal_targets = self._blackboard.get("cleanup_disposal_targets", [])
        cleanup_disposal_targets_str = (
            ", ".join(str(target.get("name", "")) for target in cleanup_disposal_targets)
            if isinstance(cleanup_disposal_targets, list) and cleanup_disposal_targets
            else "없음 — 이 경우 집기/배치를 계획하지 마세요"
        )
        cleanup_disposal_targets_section = (
            f"\n[승인된 안전 폐기 장소]\n{cleanup_disposal_targets_str}\n"
            if cleanup_task_mode
            else ""
        )
        cleanup_mode_rule = (
            "\n[일회성 정리 작업]\n"
            "현재 장소에서 명확히 폐기물로 확인된 대상만 처리하세요. 컵·병·옷·개인 소지품·위험물·액체는 "
            "임의로 옮기거나 동료에게 위임하지 마세요. 목록에 있는 집기·배치 스킬만 사용하고 다른 장소로 "
            "내비게이션하거나 탐험하지 마세요. 집기 계획에는 유효한 안전 배치 장소로의 place 단계를 반드시 포함하세요. "
            "대상이 모호하거나 안전한 지정 배치 장소가 없으면 물리 동작을 계획하지 말고 사용자 확인을 요청하세요.\n"
            if cleanup_task_mode
            else ""
        )
        system_prompt = f"""{soul_prefix}당신은 자율 행동 중인 로봇 에이전트입니다.
현재까지 수집한 정보를 바탕으로 다음에 실행할 행동을 결정하세요.
{cleanup_mode_rule}

[사용 가능한 스킬]
{skill_list}
{rag_context}

[이동 후보 우선순위]
1. 시맨틱 맵 등록 장소: 직접 등록되었거나 관찰로 의미가 붙은 장소입니다. 이동할 때 최우선으로 사용하세요.
{semantic_places_str}

2. RAG 위치 후보: 시맨틱 맵에 적절한 장소가 없을 때만 사용하세요.
{rag_places_str}

3. 맵 자동 발굴 지점: 의미 정보가 없는 단순 이동 가능 좌표입니다. 시맨틱 맵과 RAG 후보가 모두 없을 때만 최후순위로 사용하세요.
{map_generated_places_str}

[과거 행동 실행 이력 (최근 5사이클)]
{history_str}

[주변의 다른 로봇 에이전트 목록]
{agents_list_str}

[동료와의 최근 대화]
{peer_conversation_ctx}

[정리 위임 상태]
{handoff_status or "자동 위임 판단 대상 아님"}

[자신의 능력]
{self_caps_str}
[정리 작업 능력]
{"정리 대상을 집어 옮길 등록 스킬이 있습니다." if cleanup_capable else "정리 대상을 집어 옮길 등록 스킬이 없습니다."}
{cleanup_disposal_targets_section}{delegate_force_hint}

[응답 형식 — 반드시 JSON만 출력]

단일 행동:
{{"skill": "<스킬명>", "params": {{}}, "reason": "<판단 근거>"}}

다단계 계획 (쓰레기 줍기, 정리 등 여러 단계가 필요한 경우):
{{"plan": [
  {{"skill": "<스킬명>", "params": {{}}}},
  {{"skill": "<스킬명>", "params": {{}}}}
], "reason": "<판단 근거>"}}

[규칙]
- 이 판단은 순찰 중 발견한 새로운 특이사항에 대한 대응입니다. 정상적인 순찰은 자동으로 처리됩니다.
  별도의 대응이 필요하지 않으면 스킬을 실행하지 않도록
  {{"skill": "none", "params": {{}}, "reason": "추가 행동 없음"}}을 반환하세요.
- 기억된 순찰 장소가 없어 제자리 관찰 중이면 [사용 가능한 스킬]에 표시된 관찰·보고 스킬만 선택하세요. 이동, 탐험, 팔·그리퍼 동작을 계획하지 마세요.
- 등록된 스킬만 사용하세요.
- 이동이 필요하면 시맨틱 맵/RAG 및 과거에 저장된 맵 장소만 사용하세요. 새 좌표나 탐험 지점을 만들지 말고, 각 장소 옆의 "[N분/시간/일 전 방문]" 표기를 참고해 최근에 방문한 곳은 피하세요.
- 과거 이력을 적극 활용하세요. 특히 [경고]로 표시된 반복 실패나 무효 결정이 있다면 동일한 장소/행동을 다시 선택하지 마세요.
- 다음 경우 [주변의 다른 로봇 에이전트 목록]을 참고해 `delegate_task`(같은 ROS 네트워크) 또는 `call_peer_robot`(gRPC 원격)로 위임을 우선 고려하세요: (1) 자신의 역할/스킬 목록에 없는 작업(예: 자신에게 매니퓰레이션 스킬이 없는데 물건을 집어야 하는 경우)을 동료의 역할(role)이나 능력(capabilities)이 커버하는 경우, (2) 동료의 상태(캐시된 배터리/위치)가 확인되고 자신보다 목적지에 훨씬 가깝거나 배터리가 더 충분한 경우. 상태가 "상태 불명"인 동료에게는 위임 전에 `query_peer_status`로 먼저 확인하세요.
- [동료와의 최근 대화]에 동료가 보낸 메시지가 있다면, 그 요청/지시/도움 요청에 대응하는 행동을 우선 고려하세요. 동료가 도움을 요청했고 자신이 처리할 수 있으면 해당 행동을, 처리할 수 없으면 `call_peer_robot`으로 다른 동료에게 재요청하거나 처리 불가를 알리세요.
- 정리 대상이 확인되고 [정리 작업 능력]이 있다고 표시되면 자신의 등록된 조작 스킬로 직접 처리하세요.
- [정리 작업 능력]이 없으면 연결 및 조작 능력이 확인된 동료 한 대에게 `delegate_task`(같은 ROS 네트워크) 또는 `call_peer_robot`(gRPC)으로 해당 대상만 단발 요청하세요. `autonomous_cooperate`를 시작하지 마세요.
- [정리 위임 상태]에 연결된 조작 가능 동료가 없다고 표시되면 다른 피어를 추측하거나 직접 조작하지 말고, 관찰 결과와 미처리 사유를 보고하세요.
- 쓰레기 줍기나 정리처럼 여러 단계가 필요하면 plan 형식을 사용하세요.
- JSON 외의 텍스트를 출력하지 마세요.
"""
        messages = [
            {
                "role": "user",
                "content": (
                    f"[사이클 {cycle}] 현재 상황:\n"
                    f"- 현재 로봇 위치: {robot_pose_str}\n"
                    f"- 지도 탐사율: {coverage}%\n"
                    f"- 감지된 객체: {', '.join(objects) if objects else '없음'}\n"
                    f"- 장면 분석: {scene[:500]}\n\n"
                    "과거 실행 이력, 장소 목록 및 협업 가능한 다른 에이전트들을 고려하여, 다음에 할 행동을 판단해서 결정해주세요."
                ),
            }
        ]

        try:
            response = node._llm.chat(messages, system_prompt=system_prompt)
            clean = re.sub(r"```(?:json)?\s*\n?", "", response).strip()
            from robo_claw_agent.agent_node.utils import _extract_first_json_object

            json_str = _extract_first_json_object(clean)
            if "{" not in json_str:
                raise ValueError("JSON 객체를 찾을 수 없음")
            decision = json.loads(json_str)

            if "plan" in decision:
                plan: list[dict[str, Any]] = decision["plan"]
                self._blackboard["task_queue"] = plan
                self._blackboard["decided_skill"] = ""
                self._blackboard["decided_params"] = {}
                self._blackboard["decided_reason"] = decision.get("reason", "")
                logger.info(
                    "[BT] LLM multi-step plan: %d steps (%s)",
                    len(plan),
                    decision.get("reason", ""),
                )
            else:
                self._blackboard["decided_skill"] = decision.get("skill", "")
                self._blackboard["decided_params"] = decision.get("params", {})
                self._blackboard["decided_reason"] = decision.get("reason", "")
                logger.info(
                    "[BT] LLM single decision: skill=%s, reason=%s",
                    decision.get("skill"),
                    decision.get("reason"),
                )
            return NodeStatus.SUCCESS
        except Exception as exc:
            logger.error("[BT] LLM decision failed: %s", exc)
            return NodeStatus.FAILURE


class ValidateDecidedAction(BTNode):
    """LLM이 결정한 다음 행동(스킬/목적지)이 실행 가능한지 실행 전에 검증한다."""

    def __init__(self, skill: BaseSkill) -> None:
        super().__init__("ValidateDecidedAction")
        self._skill = skill

    def tick(self) -> NodeStatus:
        node = self._skill.node
        skill_name, params, from_queue = _peek_next_action(self._blackboard)

        if not skill_name or skill_name == "none":
            return NodeStatus.SUCCESS

        reason = self._validate(node, skill_name, params)
        if reason is None:
            return NodeStatus.SUCCESS

        logger.warning("[BT] Discarding invalid decision: %s(%s) — %s", skill_name, params, reason)
        self._blackboard["decision_invalid_reason"] = f"{skill_name}({params}): {reason}"
        if from_queue:
            task_queue: list[dict[str, Any]] = self._blackboard.get("task_queue", [])
            if task_queue:
                task_queue.pop(0)
            self._blackboard["task_queue"] = task_queue
        else:
            self._blackboard["decided_skill"] = ""
            self._blackboard["decided_params"] = {}
        return NodeStatus.FAILURE

    _DISALLOWED_AUTONOMOUS_SKILLS = frozenset(
        set(_AUTONOMOUS_EXCLUDED_SKILLS) | {"emergency_stop", "ros_command", "run_script"}
    )

    def _validate(self, node: Any, skill_name: str, params: dict[str, Any]) -> str | None:
        if skill_name in self._DISALLOWED_AUTONOMOUS_SKILLS:
            return f"자율 행동 내 재귀·탐험·백그라운드 스킬 차단 ({skill_name})"

        local_only = bool(self._blackboard.get("stationary_observation_only")) or (
            self._blackboard.get("patrol_has_destination") is False
        )
        authorized_cleanup_handoff = (
            bool(self._blackboard.get("cleanup_handoff_prepared"))
            and skill_name in {"delegate_task", "call_peer_robot"}
            and skill_name == self._blackboard.get("decided_skill")
            and params == self._blackboard.get("decided_params")
        )
        if (
            self._blackboard.get("cleanup_task_mode")
            and skill_name not in _CLEANUP_ACTION_SKILLS
            and not authorized_cleanup_handoff
        ):
            return f"정리 작업에 허용되지 않은 스킬입니다 ({skill_name})"
        if (
            local_only
            and skill_name not in _LOCAL_OBSERVATION_SKILLS
            and not authorized_cleanup_handoff
        ):
            return f"기억된 이동 목적지가 없어 제자리 관찰 스킬만 허용됩니다 ({skill_name})"
        if (
            local_only
            and skill_name == "describe_surroundings"
            and bool(params.get("capture_4way", False))
        ):
            return "제자리 관찰 중에는 베이스 회전을 수반하는 capture_4way를 사용할 수 없습니다."

        skills = getattr(node, "_skills", None) if node else None
        if skills is None or not skills.has_skill(skill_name):
            return "등록되지 않은 스킬"

        skill_obj = getattr(skills, "get_skill", lambda name: None)(skill_name)
        if skill_obj is not None:
            if getattr(skill_obj, "terminal_behavior", None) == "background":
                return f"백그라운드 스킬은 자율 계획 내 직접 실행 금지 ({skill_name})"
            schema_fn = getattr(skill_obj, "validate_input_schema", None)
            if callable(schema_fn):
                schema_result = schema_fn(params)
                if isinstance(schema_result, tuple) and len(schema_result) == 2:
                    schema_valid, schema_error = schema_result
                    if not schema_valid:
                        return f"파라미터 스키마 검증 실패 ({skill_name}: {schema_error})"
            validate_fn = getattr(skill_obj, "validate_params", None)
            if callable(validate_fn) and not validate_fn(params):
                return f"파라미터 유효성 검증 실패 ({skill_name}: {params})"

        side_effects = set(getattr(skill_obj, "side_effects", ()) or ())
        if "base_motion" in side_effects:
            cleanup_composite_pick = (
                self._blackboard.get("cleanup_task_mode") and skill_name == "adaptive_pick_object"
            )
            if skill_name == "navigate_to":
                memory = getattr(node, "_memory", None)
                if memory is None:
                    return "기억된 목적지를 확인할 메모리가 없습니다."
                has_location_name = any(params.get(key) for key in _LOCATION_PARAM_KEYS)
                has_x, has_y = params.get("x") is not None, params.get("y") is not None
                if not has_location_name:
                    if not (has_x and has_y):
                        return "navigate_to 목적지는 기억된 장소 이름 또는 기억 좌표여야 합니다."
                    if not _matches_remembered_coordinate(memory, params["x"], params["y"]):
                        return "요청한 좌표가 기억된 순찰 장소와 일치하지 않습니다."
                    return None
                if has_x != has_y or (
                    has_x and not _matches_remembered_coordinate(memory, params["x"], params["y"])
                ):
                    return "navigate_to 좌표가 기억된 장소 이름과 일치하지 않습니다."
            elif not cleanup_composite_pick:
                return "자율 행동의 베이스 이동은 기억된 장소 이동 또는 정리용 복합 파지 스킬만 허용됩니다."

        location_value = None
        for key in _LOCATION_PARAM_KEYS:
            value = params.get(key)
            if value:
                location_value = str(value)
                break
        if location_value is None:
            return None
        if params.get("x") is not None and params.get("y") is not None:
            return None

        memory = getattr(node, "_memory", None)
        if memory is None:
            return None
        from robo_claw_agent.skills.navigation_skill.core import (
            _resolve_target_coordinates,
        )

        resolved = _resolve_target_coordinates(memory, location_value)
        if resolved is None:
            return f"목적지 '{location_value}'를 후보 목록에서 찾을 수 없음"
        if "base_motion" in side_effects:
            position = resolved.get("position", {}) if isinstance(resolved, dict) else {}
            if not _matches_remembered_coordinate(memory, position.get("x"), position.get("y")):
                return f"목적지 '{location_value}'가 기억된 순찰 장소가 아닙니다."
        return None


class GoalPlannerNode(BTNode):
    """목표 지시형 자율행동 모드에서 LLM이 주어진 goal을 다단계 plan으로 분해한다."""

    def __init__(self, skill: BaseSkill, goal: str) -> None:
        super().__init__("GoalPlannerNode")
        self._skill = skill
        self._goal = goal

    def tick(self) -> NodeStatus:
        node = self._skill.node
        if not node or not getattr(node, "_llm", None):
            logger.error("[BT] GoalPlannerNode: no LLM available")
            return NodeStatus.FAILURE

        if self._blackboard.get("task_queue"):
            return NodeStatus.SUCCESS

        goal = self._goal or str(self._blackboard.get("goal", ""))
        if not goal:
            logger.warning("[BT] GoalPlannerNode: no goal specified")
            return NodeStatus.FAILURE

        semantic_places_str = "  • 없음"
        rag_places_str = "  • 없음"
        if hasattr(node, "_memory"):
            try:
                all_objs = node._memory.get_all_objects()
                semantic_places, _map_generated = _split_semantic_places(all_objs)
                if semantic_places:
                    semantic_places_str = "\n".join(_format_place(obj) for obj in semantic_places)
                rag_candidates = _extract_rag_location_candidates(node._memory, limit=5)
                if rag_candidates:
                    rag_places_str = "\n".join(_format_place(obj) for obj in rag_candidates)
            except Exception as e:
                logger.warning("[BT] GoalPlannerNode: failed to query locations: %s", e)

        self_caps = _self_capabilities(node)
        self_caps_str = ", ".join(self_caps) if self_caps else "없음"
        other_agents = _discover_peer_agents(node)
        agent_lines = []
        for name, info in other_agents.items():
            role = info.get("role") or "역할 미지정"
            caps = info.get("capabilities") or []
            caps_str = f", 능력: {', '.join(caps)}" if caps else ""
            agent_lines.append(f"  - {name} (역할: {role}{caps_str})")
        agents_list_str = "\n".join(agent_lines) if agent_lines else "  - 없음"

        available_skills = _available_autonomous_skills(node)
        skill_list = "\n".join(
            f"  - {s['name']}: {s.get('description', '')}" for s in available_skills
        )

        robot_soul = getattr(node, "_robot_soul", "")
        soul_prefix = f"{robot_soul}\n\n---\n\n" if robot_soul else ""

        system_prompt = f"""{soul_prefix}당신은 목표 지시형 자율 행동 모드의 로봇 에이전트입니다.
주어진 목표를 달성하기 위해 실행 가능한 다단계 계획을 수립하세요.

[사용 가능한 스킬]
{skill_list}

[이동 후보]
1. 시맨틱 맵 등록 장소:
{semantic_places_str}
2. RAG 위치 후보:
{rag_places_str}

[주변의 다른 로봇 에이전트 목록]
{agents_list_str}

[자신의 능력]
{self_caps_str}

[응답 형식 — 반드시 JSON만 출력]
{{"plan": [
  {{"skill": "<스킬명>", "params": {{}}, "reason": "<이 단계의 목적>"}},
  ...
], "goal_achieved": <true|false>}}

[규칙]
- 목표를 달성하기 위해 필요한 스킬을 순서대로 배치하세요.
- autonomous_act는 탐험하지 않습니다. 미기억 목적지를 새로 탐색하거나 `explore`를 선택하지 마세요.
- 이동이 필요하면 시맨틱 맵/RAG 및 과거에 저장된 장소 좌표만 사용하세요. 새 좌표를 만들거나 탐험하지 마세요.
- 자신에게 없는 능력(예: 매니퓰레이션)이 필요한 단계는 동료에게 위임하세요.
  같은 ROS 네트워크면 `delegate_task`(agent_id, instruction), gRPC 원격이면
  `call_peer_robot`(peer_name, instruction)을 plan 단계로 배치하세요.
- 각 단계는 이전 단계의 결과를 전제로 작성하세요.
- 목표가 이미 달성된 상태라면 빈 plan과 goal_achieved=true를 반환하세요.
- JSON 외의 텍스트를 출력하지 마세요.
"""
        messages = [
            {
                "role": "user",
                "content": f"목표: {goal}\n\n이 목표를 달성하기 위한 실행 계획을 수립해주세요.",
            }
        ]

        try:
            response = node._llm.chat(messages, system_prompt=system_prompt)
            clean = re.sub(r"```(?:json)?\s*\n?", "", response).strip()
            from robo_claw_agent.agent_node.utils import _extract_first_json_object

            json_str = _extract_first_json_object(clean)
            if "{" not in json_str:
                raise ValueError("JSON 객체를 찾을 수 없음")
            decision = json.loads(json_str)

            plan: list[dict[str, Any]] = decision.get("plan", [])
            goal_achieved = decision.get("goal_achieved", False)

            if goal_achieved:
                logger.info("[BT] GoalPlannerNode: goal already achieved or empty plan")
                self._blackboard["goal_achieved"] = True
                self._blackboard["task_queue"] = []
                return NodeStatus.SUCCESS
            if not plan:
                self._blackboard["goal_achieved"] = False
                planning_failures = self._blackboard.get("planning_failure_count", 0) + 1
                self._blackboard["planning_failure_count"] = planning_failures
                self._blackboard["planning_failure_reason"] = (
                    "planner returned an empty plan without explicit goal_achieved"
                )
                logger.warning(
                    "[BT] GoalPlannerNode: empty plan did not prove goal completion (attempt %d)",
                    planning_failures,
                )
                return NodeStatus.FAILURE

            self._blackboard["task_queue"] = plan
            self._blackboard["decided_reason"] = f"목표: {goal}"
            self._blackboard["goal_achieved"] = False
            self._blackboard["planning_failure_count"] = 0
            self._blackboard.pop("planning_failure_reason", None)
            logger.info(
                "[BT] GoalPlannerNode: decomposed goal '%s' into %d steps",
                goal,
                len(plan),
            )
            return NodeStatus.SUCCESS
        except Exception as exc:
            logger.error("[BT] GoalPlannerNode: LLM planning failed: %s", exc)
            self._blackboard["planning_failure_count"] = (
                self._blackboard.get("planning_failure_count", 0) + 1
            )
            self._blackboard["planning_failure_reason"] = str(exc)
            return NodeStatus.FAILURE
