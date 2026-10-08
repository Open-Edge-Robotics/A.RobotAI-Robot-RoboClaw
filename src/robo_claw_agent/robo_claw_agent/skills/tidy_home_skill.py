"""One-shot, location-aware home cleanup orchestration."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .autonomous_skill.globals import (
    _AUTONOMOUS_ACTIVE,
    claim_autonomous,
    release_autonomous,
)
from .cleanup_policy import (
    _cleanup_plan_has_complete_placement,
    _has_ambiguous_cleanup_indication,
    _has_cleanup_indication,
    _resolve_cleanup_places,
    _resolve_cleanup_plan_destinations,
    _safe_cleanup_disposal_targets,
    _select_cloid_indicator_motion,
)

logger = logging.getLogger(__name__)
_MAX_HOME_PLACES = 20
_MAX_CLEANUP_ACTIONS_PER_PLACE = 4


class TidyHomeSkill(BaseSkill):
    """Visit a requested remembered room or one pass through remembered home locations."""

    name = "tidy_home"
    terminal_behavior = "background"
    side_effects = ("base_motion", "arm_motion", "gripper_motion", "background_loop")
    exclusive_resources = ("base_control", "cloid_motion")
    preconditions = (
        "대상 장소가 기억된 좌표로 해석되어야 합니다.",
        "모호한 생활용품·위험물은 이동하지 않아야 합니다.",
        "로컬 배치에는 승인된 비추정 3D 폐기 pose가 필요합니다.",
        "조작은 등록된 정리 스킬 또는 확인된 동료에게만 요청해야 합니다.",
    )
    input_schema = {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["room", "home"],
                "default": "home",
                "description": "room은 target_location 한 곳, home은 기억된 장소를 한 차례씩 순회합니다.",
            },
            "target_location": {"type": "string", "maxLength": 80},
        },
        "additionalProperties": False,
    }
    description = (
        "한 번의 정리 작업을 수행하고 종료합니다. 지정 방은 기억된 시맨틱 맵/RAG 좌표로 먼저 이동합니다. "
        "집 전체는 기억된 장소만 한 차례씩 방문하며 탐험하지 않습니다. 명확한 폐기물만 후보로 삼고, 로컬 "
        "집기/배치는 승인된 비추정 3D 폐기 pose가 있을 때만 계획합니다. 모호한 항목은 이동하지 않으며, "
        "처리 능력이 부족하면 연결과 능력이 확인된 동료 한 대에게 단발 위임합니다. CLOiD는 VLA 구현 전까지 "
        "검토된 상태 표시 모션 하나를 임의 선택해 실행할 수 있으나, 이 임시 모션은 물체를 정리하지 않으며 "
        "완료로 보고하지 않습니다. 중단: stop_autonomous를 사용하세요."
    )

    def __init__(self) -> None:
        super().__init__()
        self._previous_indicator_motion_id: int | None = None
        self._places_truncated = False

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        scope = str(params.get("scope", "home")).strip().lower()
        target_location = str(params.get("target_location", "")).strip()
        if scope not in {"room", "home"}:
            return self.fail_result(
                "scope는 'room' 또는 'home'이어야 합니다.", failure_reason="invalid_scope"
            )
        if scope == "room" and not target_location:
            return self.fail_result(
                "방 범위 정리에는 target_location이 필요합니다.",
                failure_reason="target_location_required",
            )
        if scope == "home" and target_location:
            return self.fail_result(
                "집 전체 범위에서는 target_location을 지정하지 마세요.",
                failure_reason="unexpected_target_location",
            )
        if (error := self.require_node()) is not None:
            return error

        skills = getattr(self.node, "_skills", None)
        if skills is not None and skills.has_skill("run_butler_script"):
            if scope != "home":
                return self.fail_result(
                    "Butler 정리 스크립트에는 방 선택 입력이 없어 특정 방을 보장할 수 없습니다.",
                    failure_reason="room_scope_not_supported",
                )
            return self._start_butler_cleanup()

        self._places_truncated = False
        places, error = self._collect_places(scope, target_location)
        if error:
            message = {
                "no_remembered_places": "기억된 정리 장소가 없어 이동을 시작하지 않았습니다.",
                "requested_location_not_found": (
                    f"기억된 장소에서 '{target_location}'의 좌표를 찾지 못했습니다."
                ),
                "requested_location_ambiguous": (
                    f"'{target_location}'에 해당하는 기억 장소가 여러 곳이라 이동하지 않았습니다."
                ),
                "target_location_required": "방 범위 정리에는 target_location이 필요합니다.",
                "invalid_scope": "scope는 'room' 또는 'home'이어야 합니다.",
            }.get(error, "정리 장소를 확인할 수 없습니다.")
            return self.fail_result(message, failure_reason=error)

        if not claim_autonomous(_AUTONOMOUS_ACTIVE):
            return self.fail_result(
                "다른 자율 행동이 실행 중입니다. stop_autonomous로 중단한 뒤 다시 요청하세요.",
                failure_reason="autonomous_busy",
                recoverable=True,
            )

        worker = threading.Thread(
            target=self._run_cleanup,
            args=(scope, places),
            daemon=True,
            name="tidy-home-worker",
        )
        try:
            worker.start()
        except Exception as exc:
            release_autonomous()
            logger.exception("[TidyHome] Failed to start worker")
            return self.fail_result(
                f"정리 작업을 시작하지 못했습니다: {exc}", failure_reason="start_failed"
            )
        start_message = f"{len(places)}개 기억 장소를 대상으로 한 번의 정리 작업을 시작했습니다."
        if self._places_truncated:
            start_message += f" 안전상 최대 {_MAX_HOME_PLACES}곳만 이번 실행에서 확인합니다."
        return self.success_result(
            start_message,
            status="started",
            scope=scope,
            places=[place["name"] for place in places],
        )

    def _collect_places(
        self, scope: str, target_location: str
    ) -> tuple[list[dict[str, Any]], str | None]:
        memory = getattr(self.node, "_memory", None)
        if memory is None:
            return [], "no_memory"
        try:
            from .autonomous_skill.helpers import (
                _extract_rag_location_candidates,
                _split_semantic_places,
            )

            semantic, _map_generated = _split_semantic_places(memory.get_all_objects())
            rag = _extract_rag_location_candidates(memory, limit=100)
            candidates = semantic + rag
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TidyHome] Could not load remembered places: %s", exc)
            return [], "no_remembered_places"

        places, error = _resolve_cleanup_places(scope, target_location, candidates)
        if error and scope == "room" and target_location:
            try:
                from .navigation_skill.core import _resolve_target_coordinates

                resolved = _resolve_target_coordinates(memory, target_location)
                if resolved and isinstance(resolved.get("position"), dict):
                    pos = resolved["position"]
                    if "x" in pos and "y" in pos:
                        place_name = str(resolved.get("name") or target_location).strip()
                        return [
                            {
                                "name": place_name,
                                "position": {"x": float(pos["x"]), "y": float(pos["y"])},
                            }
                        ], None
            except Exception as exc:  # noqa: BLE001
                logger.debug("[TidyHome] Fallback to _resolve_target_coordinates failed: %s", exc)

        self._places_truncated = scope == "home" and len(places) > _MAX_HOME_PLACES
        if self._places_truncated:
            logger.warning(
                "[TidyHome] Capping home sweep from %d to %d remembered places",
                len(places),
                _MAX_HOME_PLACES,
            )
            places = places[:_MAX_HOME_PLACES]
        return places, error

    def _start_butler_cleanup(self) -> dict[str, Any]:
        if not claim_autonomous(_AUTONOMOUS_ACTIVE):
            return self.fail_result(
                "다른 자율 행동이 실행 중입니다. stop_autonomous로 중단한 뒤 다시 요청하세요.",
                failure_reason="autonomous_busy",
                recoverable=True,
            )
        worker = threading.Thread(
            target=self._run_butler_script,
            daemon=True,
            name="tidy-home-butler-worker",
        )
        try:
            worker.start()
        except Exception as exc:
            release_autonomous()
            return self.fail_result(
                f"Butler 정리 스크립트를 시작하지 못했습니다: {exc}",
                failure_reason="start_failed",
            )
        return self.success_result(
            "Butler의 등록된 정리 스크립트를 실행합니다.",
            status="started",
            scope="home",
        )

    def _run_butler_script(self) -> None:
        try:
            self.send_user_message("Butler 정리 스크립트를 실행합니다.")
            result = self.node._skills.execute(
                "run_butler_script", {"script_name": "56_organizer_req.sh"}, timeout_sec=120.0
            )
            success = bool(getattr(result, "success", False))
            message = str(getattr(result, "message", "") or "")
            if success:
                self.send_user_message(
                    f"Butler 정리 스크립트가 성공 응답을 반환했습니다. {message}".strip()
                )
            else:
                self.send_user_message(f"Butler 정리 스크립트가 실패했습니다. {message}".strip())
        except Exception as exc:  # noqa: BLE001
            logger.exception("[TidyHome] Butler organizer script failed")
            self.send_user_message(f"Butler 정리 스크립트 실행에 실패했습니다: {exc}")
        finally:
            release_autonomous()

    def _run_cleanup(self, scope: str, places: list[dict[str, Any]]) -> None:
        results: list[dict[str, str]] = []
        found_cleanup_target = False
        try:
            start_message = (
                f"정리 작업을 시작합니다. 기억된 장소 {len(places)}곳을 한 차례 확인합니다."
            )
            if self._places_truncated:
                start_message += f" 안전상 최대 {_MAX_HOME_PLACES}곳만 확인합니다."
            self.send_user_message(start_message)
            for index, place in enumerate(places, start=1):
                if not _AUTONOMOUS_ACTIVE.is_set():
                    results.append({"place": place["name"], "status": "cancelled"})
                    break

                place_name = str(place["name"])
                try:
                    arrived, nav_message = self._visit_place(place_name)
                except Exception as exc:  # noqa: BLE001
                    arrived, nav_message = False, str(exc)
                    logger.exception("[TidyHome] Navigation failed for %s", place_name)
                if not _AUTONOMOUS_ACTIVE.is_set():
                    results.append({"place": place_name, "status": "cancelled"})
                    break
                if not arrived:
                    results.append({"place": place_name, "status": "unreachable"})
                    self.send_user_message(
                        f"'{place_name}'에 도착하지 못해 해당 장소를 건너뜁니다. {nav_message}"
                    )
                    continue

                scene_result = self._analyze_current_scene(place_name)
                if not scene_result.get("success"):
                    results.append({"place": place_name, "status": "observation_failed"})
                    self.send_user_message(
                        f"'{place_name}'에서 장면을 분석하지 못해 정리 작업을 보류합니다. "
                        f"{scene_result.get('message', '')}"
                    )
                    continue

                scene = str(scene_result.get("analysis", "") or "")
                objects = scene_result.get("detected_objects", []) or []
                if not _has_cleanup_indication(objects, scene):
                    if _has_ambiguous_cleanup_indication(objects, scene):
                        results.append({"place": place_name, "status": "needs_clarification"})
                        self.send_user_message(
                            f"'{place_name}'에서 컵·병·개인 소지품 또는 위험 물품처럼 보이는 항목을 발견했지만, "
                            "폐기물인지 확실하지 않아 이동하거나 동료에게 위임하지 않았습니다. 어떤 항목을 정리할지 지정해 주세요."
                        )
                    else:
                        results.append({"place": place_name, "status": "no_target"})
                    continue

                found_cleanup_target = True
                status = self._handle_cleanup_target(
                    place_name=place_name,
                    scene=scene,
                    objects=objects,
                    cycle=index,
                )
                results.append({"place": place_name, "status": status})

            if found_cleanup_target and self._cloid_indicator_enabled():
                self._run_cloid_activity_indicator()
            self._report_summary(scope, results, found_cleanup_target)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[TidyHome] Cleanup run failed")
            self.send_user_message(f"정리 작업이 중단됐습니다. 완료 여부를 확인해 주세요: {exc}")
        finally:
            release_autonomous()

    def _visit_place(self, place_name: str) -> tuple[bool, str]:
        from robo_claw_agent.skills.navigation_skill.patrol import visit_place

        return visit_place(self.node, place_name)

    def _analyze_current_scene(self, place_name: str) -> dict[str, Any]:
        from robo_claw_agent.skills.vision_skill import AnalyzeSceneSkill

        scene_skill = AnalyzeSceneSkill()
        scene_skill.set_node(self.node)
        return scene_skill.execute(
            {
                "prompt": (
                    f"'{place_name}'에서 정리 대상으로 보이는 쓰레기, 흩어진 물건, "
                    "쏟아진 물질을 찾아주세요. 실제로 보이는 대상만 구체적으로 설명하고, "
                    "정리 대상이 없으면 없다고 명시하세요. 물체 위치를 추측하지 마세요."
                )
            }
        )

    def _handle_cleanup_target(
        self, *, place_name: str, scene: str, objects: list[Any], cycle: int
    ) -> str:
        from .autonomous_skill.actions import (
            ExecuteDecidedAction,
            LLMDecideAction,
            ResolveCleanupHandoff,
            ValidateDecidedAction,
        )
        from .autonomous_skill.bt_runner import wire_blackboard
        from .autonomous_skill.core import NodeStatus

        disposal_targets = _safe_cleanup_disposal_targets(getattr(self.node, "_memory", None))
        blackboard: dict[str, Any] = {
            "cycle_count": cycle,
            "task_queue": [],
            "task_history": [],
            "patrol_has_destination": True,
            "stationary_observation_only": False,
            "patrol_last_place": place_name,
            "scene_analysis": scene,
            "detected_objects": objects,
            "cleanup_task_mode": True,
            "cleanup_disposal_targets": disposal_targets,
        }
        handoff = ResolveCleanupHandoff(self)
        decide = LLMDecideAction(self)
        validate = ValidateDecidedAction(self)
        execute = ExecuteDecidedAction(self)
        wire_blackboard([handoff, decide, validate, execute], blackboard)

        if handoff.tick() != NodeStatus.SUCCESS:
            return "planning_failed"
        if blackboard.get("cleanup_handoff_blocked"):
            return "unhandled"
        if not blackboard.get("cleanup_handoff_prepared"):
            if decide.tick() != NodeStatus.SUCCESS:
                return "planning_failed"
            if blackboard.get("decided_skill") in {None, "", "none"} and not blackboard.get(
                "task_queue"
            ):
                return "no_action"

        planned_actions = blackboard.get("task_queue") or [
            {
                "skill": blackboard.get("decided_skill"),
                "params": blackboard.get("decided_params", {}),
            }
        ]
        if not _cleanup_plan_has_complete_placement(planned_actions):
            self.send_user_message(
                f"'{place_name}'의 정리 계획에 안전한 집기 후 배치 단계가 모두 포함되지 않아 물리 동작을 시작하지 않았습니다."
            )
            return "incomplete_placement_plan"
        if not _resolve_cleanup_plan_destinations(planned_actions, disposal_targets):
            self.send_user_message(
                f"'{place_name}'에 명시적으로 지정된 3D 폐기 장소가 없거나 계획과 일치하지 않아 물리 동작을 시작하지 않았습니다."
            )
            return "unsafe_placement_destination"

        step_count = 0
        while step_count < _MAX_CLEANUP_ACTIONS_PER_PLACE:
            if not _AUTONOMOUS_ACTIVE.is_set():
                return "cancelled"
            if not blackboard.get("task_queue") and not blackboard.get("decided_skill"):
                break
            if validate.tick() != NodeStatus.SUCCESS:
                return "action_rejected"
            was_handoff = bool(blackboard.get("cleanup_handoff_prepared"))
            if execute.tick() != NodeStatus.SUCCESS:
                return "execution_failed"
            if not blackboard.get("last_action_success", False):
                return "execution_failed"
            step_count += 1
            if was_handoff:
                # A handoff is one request; never plan another request for the same observation.
                return "delegated"
            if blackboard.get("task_queue"):
                blackboard["decided_skill"] = ""
                blackboard["decided_params"] = {}
                continue
            break
        if blackboard.get("task_queue"):
            return "partial_action_limit"
        return "handled" if step_count else "no_action"

    def _cloid_indicator_enabled(self) -> bool:
        try:
            if not self.node.has_parameter("cloid_cleanup_indicator_enabled"):
                return False
            return bool(
                self.node.get_parameter("cloid_cleanup_indicator_enabled")
                .get_parameter_value()
                .bool_value
            )
        except Exception:
            return False

    def _cloid_indicator_ids(self) -> list[int]:
        try:
            raw = self.node.get_parameter("cloid_cleanup_indicator_motion_ids_json").value
            values = json.loads(str(raw))
            if isinstance(values, list):
                return [
                    value
                    for value in values
                    if isinstance(value, int) and not isinstance(value, bool)
                ]
        except Exception:
            pass
        return []

    def _run_cloid_activity_indicator(self) -> None:
        skills = getattr(self.node, "_skills", None)
        if (
            skills is None
            or not skills.has_skill("list_cloid_motions")
            or not skills.has_skill("execute_cloid_motion")
        ):
            self.send_user_message(
                "CLOiD 정리 표시 모션을 실행할 수 없습니다. 정리 대상은 확인했지만 VLA 조작은 아직 구현되지 않았습니다."
            )
            return
        try:
            catalog = skills.execute("list_cloid_motions", {"timeout_sec": 3.0}, timeout_sec=5.0)
            if not catalog.success:
                raise RuntimeError(catalog.message)
            motion = _select_cloid_indicator_motion(
                catalog.result_data.get("motions", []),
                self._cloid_indicator_ids(),
                previous_motion_id=self._previous_indicator_motion_id,
            )
            if motion is None:
                self.send_user_message(
                    "현재 카탈로그에 실행 가능한 정리 표시 모션이 없습니다. 실제 정리 여부는 확인되지 않았습니다."
                )
                return
            self._previous_indicator_motion_id = motion["motion_id"]
            result = skills.execute(
                "execute_cloid_motion",
                {
                    "motion_name": motion["motion_name"],
                    "motion_id": motion["motion_id"],
                    "confirm": True,
                },
                timeout_sec=10.0,
            )
            if result.success:
                self.send_user_message(
                    f"CLOiD 임시 표시 모션 '{motion['motion_name']}'을 실행 요청했습니다. "
                    "이 고정 모션은 물체를 정리하지 않으며, 실제 정리 완료는 확인되지 않았습니다."
                )
            else:
                self.send_user_message(
                    f"CLOiD 표시 모션 실행이 거부되거나 실패했습니다: {result.message}. "
                    "실제 정리 완료는 확인되지 않았습니다."
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TidyHome] CLOiD status motion failed: %s", exc)
            self.send_user_message(
                "CLOiD 임시 정리 표시 모션을 실행하지 못했습니다. 실제 정리 완료는 확인되지 않았습니다."
            )

    def _report_summary(
        self, scope: str, results: list[dict[str, str]], found_cleanup_target: bool
    ) -> None:
        counts: dict[str, int] = {}
        for result in results:
            state = result["status"]
            counts[state] = counts.get(state, 0) + 1
        if not results:
            summary = "확인한 장소가 없습니다."
        else:
            summary = "장소별 결과: " + ", ".join(
                f"{result['place']}: {result['status']}" for result in results
            )
        if not found_cleanup_target:
            incomplete_states = {"unreachable", "observation_failed", "cancelled"}
            if any(result["status"] in incomplete_states for result in results):
                ending = "일부 장소를 확인하지 못해 정리 대상 유무와 작업 완료 여부를 확정할 수 없습니다."
            elif counts.get("needs_clarification"):
                ending = "모호하거나 위험할 수 있는 항목은 안전을 위해 이동하지 않았습니다. 사용자 확인이 필요합니다."
            else:
                ending = "확인한 장소에서는 정리 대상이 관찰되지 않았습니다."
        elif self._cloid_indicator_enabled():
            ending = "CLOiD 임시 표시 모션은 실제 물체 정리나 완료를 의미하지 않습니다."
        else:
            ending = "장소별 처리 결과를 확인해 주세요. 실패·위임·관찰 결과를 구분했습니다."
        if self._places_truncated:
            ending += f" 기억 장소가 {_MAX_HOME_PLACES}곳을 초과해 나머지는 이번 실행에서 확인하지 않았습니다."
        self.send_user_message(f"정리 작업이 종료됐습니다(scope={scope}). {summary}. {ending}")
