import json
import logging
import random
import time
import uuid
from typing import Any

import numpy as np

from robo_claw_agent.skill_manager import BaseSkill

from ..cleanup_policy import (
    _CLOID_INDICATOR_MOTIONS,
    _cleanup_target_categories,
    _cleanup_target_category,
    _cleanup_target_labels,
    _has_cleanup_indication,
    _safe_cleanup_disposal_targets,
    _safe_cleanup_storage_targets,
    _select_cleanup_demo_motion,
)
from ..cleanup_run_metrics import (
    peer_candidates,
    record_cleanup_delegate,
    record_cleanup_handoff,
)
from .core import BTNode, NodeStatus
from .globals import _AUTONOMOUS_ACTIVE, _SLEEP_WAKE, release_autonomous
from .helpers import (
    _discover_peer_agents,
    _find_connected_peer_with_capability,
    _has_cleanup_capability,
)

logger = logging.getLogger(__name__)


def _peer_candidate_snapshot(node: Any, selected: str = "") -> list[dict[str, Any]]:
    """설정에 등록된 동료 목록을 계측용 후보 스냅샷으로 만든다.

    ``_discover_peer_agents`` 는 환경변수/파라미터/ROS 그래프만 읽으므로 추가 RPC
    없이 호출할 수 있다. 실패하면 후보 목록 없이 결정만 기록한다.
    """
    try:
        return peer_candidates(_discover_peer_agents(node), selected)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[BT] Peer candidate snapshot failed: %s", exc)
        return []


def _record_delegate(
    *,
    blackboard: dict[str, Any],
    node: Any,
    skill_name: str,
    params: dict[str, Any],
    success: bool,
    message: str,
    duration_sec: float,
) -> None:
    """위임 요청 1건의 결과를 기록한다. 계측 실패는 실행 결과에 영향을 주지 않는다."""
    try:
        record_cleanup_delegate(
            run_id=str(blackboard.get("cleanup_run_id", "") or ""),
            source="tidy_home" if blackboard.get("cleanup_run_id") else "autonomous_patrol",
            agent_id=str(getattr(node, "_agent_id", "") or ""),
            place=str(blackboard.get("patrol_last_place", "") or ""),
            peer=str(params.get("peer_name") or params.get("agent_id") or ""),
            send_path=str(skill_name),
            success=bool(success),
            message=message,
            duration_ms=duration_sec * 1000.0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BT] Cleanup delegate record failed: %s", exc)


def _phase_ms(blackboard: dict[str, Any]) -> dict[str, float] | None:
    """호출한 정리 실행이 넘긴 구간 계측 dict(없으면 None)."""
    phase = blackboard.get("cleanup_phase_ms")
    return phase if isinstance(phase, dict) else None


class EmergencyLowBattery(BTNode):
    def __init__(self, skill: BaseSkill, check_only: bool = True) -> None:
        super().__init__("EmergencyLowBattery")
        self._skill = skill
        self._check_only = check_only
        self._warned = False

    def tick(self) -> NodeStatus:
        pct = self._blackboard.get("low_battery_pct", 0)
        if not self._check_only:
            msg = f"[자율 행동] 배터리 잔량이 {pct:.0f}%로 부족합니다. 자율 행동을 안전하게 종료합니다."
            logger.warning("[BT] %s", msg)
            self._skill.send_user_message(msg)
            release_autonomous()
            return NodeStatus.FAILURE

        if not self._warned:
            msg = (
                f"[자율 행동] 배터리 신호가 없거나 잔량이 {pct:.0f}%로 부족합니다. "
                "(체크 전용 모드로 자율 행동/협동을 계속 유지합니다)"
            )
            logger.warning("[BT] %s", msg)
            self._skill.send_user_message(msg)
            self._warned = True
        return NodeStatus.SUCCESS


class RunExploreOnce(BTNode):
    """프론티어 1개를 찾아 blocking으로 이동 (최대 60초)."""

    def __init__(self, skill: BaseSkill, exploration_radius: float = 3.0) -> None:
        super().__init__("RunExploreOnce")
        self._skill = skill
        self._radius = exploration_radius

    def tick(self) -> NodeStatus:
        from nav_msgs.msg import OccupancyGrid

        from robo_claw_agent.skills.explore_skill import (
            _EXPLORE_ACTIVE,
            _cluster_frontiers,
            _extract_frontiers,
            _filter_frontiers_by_radius,
            _get_robot_pose,
            _select_nearest_frontier,
        )
        from robo_claw_agent.skills.navigation_skill import (
            _send_navigation_goal,
            _wait_for_goal_result,
        )

        if _EXPLORE_ACTIVE.is_set():
            logger.debug("[BT] ExploreSkill already running — skipping RunExploreOnce")
            return NodeStatus.SUCCESS

        map_msg = self._skill.wait_for_message(
            OccupancyGrid, "/map", timeout_sec=5.0, use_transient_local=True
        )
        if not map_msg:
            return NodeStatus.FAILURE

        info = map_msg.info
        data = np.array(map_msg.data).reshape((info.height, info.width))

        pose = _get_robot_pose(self._skill)
        if not pose:
            return NodeStatus.FAILURE
        robot_x, robot_y = pose

        frontiers = _extract_frontiers(
            data, info.resolution, info.origin.position.x, info.origin.position.y
        )
        clusters = _cluster_frontiers(frontiers, cluster_dist=0.5)
        in_radius = _filter_frontiers_by_radius(clusters, robot_x, robot_y, self._radius)
        target = _select_nearest_frontier(in_radius, robot_x, robot_y)

        if not target:
            logger.info("[BT] No frontier to explore — treating exploration as complete")
            self._blackboard["has_map"] = True
            return NodeStatus.SUCCESS

        tx, ty = target
        logger.info("[BT] Short-term exploration target: (%.2f, %.2f)", tx, ty)
        accepted, _, goal_handle = _send_navigation_goal(self._skill.node, tx, ty, "map")
        if not accepted or goal_handle is None:
            return NodeStatus.FAILURE

        success, _ = _wait_for_goal_result(goal_handle, timeout_sec=60.0, label="RunExploreOnce")
        return NodeStatus.SUCCESS if success else NodeStatus.FAILURE


class LocalObservationNode(BTNode):
    """기억된 순찰 장소가 없을 때 제자리에서 4방향 관찰을 한 번 수행한다."""

    def __init__(self, skill: BaseSkill, scan_factory: Any = None) -> None:
        super().__init__("LocalObservationNode")
        self._skill = skill
        self._scan_factory = scan_factory

    def tick(self) -> NodeStatus:
        if self._blackboard.get("patrol_has_destination", False):
            self._blackboard["stationary_observation_only"] = False
            self._blackboard["stationary_scan_done"] = False
            return NodeStatus.SUCCESS

        self._blackboard["stationary_observation_only"] = True
        if self._blackboard.get("stationary_scan_done", False):
            return NodeStatus.SUCCESS

        self._blackboard["stationary_scan_done"] = True
        node = self._skill.node
        if node is None:
            return NodeStatus.SUCCESS

        self._skill.send_user_message("기억된 순찰 장소가 없어 제자리에서 주변을 관찰합니다.")
        try:
            from robo_claw_agent.agent_node.nav_safety import prepare_navigation

            ready, reason = prepare_navigation(node)
            if not ready:
                self._blackboard["stationary_scan_result"] = {
                    "success": False,
                    "message": reason,
                }
                logger.warning("[BT] Stationary scan blocked by navigation safety: %s", reason)
                return NodeStatus.SUCCESS

            from robo_claw_agent.skills.perception_skill.scan import ScanRoomSkill

            scanner = (
                self._scan_factory(_AUTONOMOUS_ACTIVE)
                if self._scan_factory is not None
                else ScanRoomSkill(cancel_event=_AUTONOMOUS_ACTIVE)
            )
            scanner.set_node(node)
            result = scanner.execute({"step_deg": 90.0, "settle_sec": 0.5})
            self._blackboard["stationary_scan_result"] = result
            if not result.get("success"):
                logger.warning(
                    "[BT] Stationary scan incomplete: %s", result.get("message", "unknown error")
                )
        except Exception as exc:
            self._blackboard["stationary_scan_result"] = {
                "success": False,
                "message": str(exc),
            }
            logger.exception("[BT] Stationary scan failed")
        return NodeStatus.SUCCESS


class ResolveCleanupHandoff(BTNode):
    """조작 불가능한 로봇이 발견한 정리 대상을 연결된 조작 가능 동료에게 한 번 위임한다."""

    def __init__(self, skill: BaseSkill) -> None:
        super().__init__("ResolveCleanupHandoff")
        self._skill = skill

    def _select_local_demo_motion(
        self,
        blackboard: dict[str, Any],
        skills: Any,
        allowed_demo_ids: list[int],
        place: str,
        target_description: str,
    ) -> bool:
        """시연 로봇이 로컬에서 바로 정리 시연 모션을 수행하도록 결정을 채운다."""
        # 시연 가능 개체: 동료를 부르지 않고 로컬에서 바로 정리 시연 모션 실행
        catalog_res = None
        try:
            catalog_res = skills.execute(
                "list_cloid_motions", {"timeout_sec": 3.0}, timeout_sec=5.0
            )
        except Exception:
            pass
        available_motions = []
        if (
            catalog_res
            and getattr(catalog_res, "success", False) is True
            and isinstance(getattr(catalog_res, "result_data", None), dict)
            and isinstance(catalog_res.result_data.get("motions"), list)
        ):
            available_motions = catalog_res.result_data["motions"]
        if not available_motions:
            available_motions = [
                {
                    "motion_id": mid,
                    "motion_name": _CLOID_INDICATOR_MOTIONS.get(mid, f"motion_{mid}"),
                }
                for mid in allowed_demo_ids
            ]

        selected_motion = _select_cleanup_demo_motion(
            available_motions,
            allowed_demo_ids,
            rng=random.Random(),
        )
        if selected_motion:
            blackboard["decided_skill"] = "execute_cloid_motion"
            blackboard["decided_params"] = {
                "motion_name": selected_motion["motion_name"],
                "motion_id": selected_motion["motion_id"],
                "confirm": True,
            }
            reason = (
                f"'{place or '현재 위치'}'에서 정리 대상 '{target_description}'을 확인하고 "
                f"정리 시연 모션 '{selected_motion['motion_name']}'을 수행합니다."
            )
            blackboard["decided_reason"] = reason
            self._skill.send_user_message(reason)
            return True
        return False

    def _build_handoff_instruction(
        self,
        *,
        handoff_kind: str,
        target_categories: set[str],
        target_description: str,
        location: str,
        scene: str,
        demo_target: str,
        place: str,
    ) -> str:
        """동료에게 전달할 정리 위임 지시문을 만든다."""
        if handoff_kind == "cleanup_demo":
            from ..cleanup_demo_protocol import encode_cleanup_demo_request

            instruction = encode_cleanup_demo_request(
                {
                    "request_id": str(uuid.uuid4()),
                    "target_location": place,
                    "target_object": demo_target,
                    "scene_summary": scene[:800] or "추가 장면 설명 없음",
                }
            )
        elif target_categories == {"waste"}:
            instruction = (
                f"정리 작업 요청: 명확한 폐기물 후보 '{target_description}'입니다. 위치: {location}. "
                f"관찰 내용: {scene[:800] or '추가 장면 설명 없음'}. 해당 위치에서 대상을 다시 확인하세요. "
                "명확한 폐기물로 확인되고 안전한 지정 폐기 장소가 알려져 있는 경우에만 한 건을 처리하세요. "
                "컵·병·가방·의류·개인 소지품·위험물·액체 유출 등은 임의로 이동하지 말고 사용자 확인을 요청하세요. "
                "다른 구역 순찰, 추가 대상 처리, 지속적인 자율협동은 시작하지 말고 결과를 반환하세요."
            )
        else:
            category = next(iter(target_categories), "생활용품")
            category_name = {
                "towel": "수건",
                "cushion": "쿠션/방석",
                "laundry": "빨래/세탁물",
            }.get(category, "생활용품")
            instruction = (
                f"정리 작업 요청: 관찰된 {category_name} 후보 '{target_description}'입니다. 위치: {location}. "
                f"관찰 내용: {scene[:800] or '추가 장면 설명 없음'}. 해당 위치에서 그 대상 하나만 다시 확인하세요. "
                f"{category_name}에 맞게 명시 승인된 비추정 보관 장소가 확인될 때만 처리하세요. "
                "폐기물로 취급하거나 쓰레기통에 넣지 말고, 목적지나 물체 위치를 추측하지 마세요. "
                "안전한 유형별 보관 장소가 없거나 대상 상태가 모호하면 이동하지 말고 사용자 확인을 요청하세요. "
                "다른 구역 순찰, 추가 대상 처리, 지속적인 자율협동은 시작하지 말고 결과를 반환하세요."
            )
        return instruction

    def tick(self) -> NodeStatus:
        blackboard = self._blackboard
        blackboard.pop("cleanup_handoff_prepared", None)
        blackboard.pop("cleanup_handoff_blocked", None)
        blackboard.pop("cleanup_handoff_target", None)
        blackboard.pop("cleanup_handoff_status", None)

        node = self._skill.node
        if node is None:
            return NodeStatus.SUCCESS
        # 실험 계측 — 이미 내려놓은 판단을 그대로 기록하기 위한 값만 준비한다.
        run_id = str(blackboard.get("cleanup_run_id", "") or "")
        metric_source = "tidy_home" if run_id else "autonomous_patrol"
        phase = _phase_ms(blackboard)

        def _find_peer(capability: str) -> dict[str, str] | None:
            """동료 탐색 소요 시간을 구간 계측에 누적하며 선택 결과를 돌려준다."""
            started = time.monotonic()
            try:
                return _find_connected_peer_with_capability(node, capability)
            finally:
                if phase is not None:
                    phase["discovery"] = phase.get("discovery", 0.0) + (time.monotonic() - started)

        def _record_handoff(*, place: str, target: str, **fields: Any) -> None:
            # 계측 실패는 위임 결정과 메시지 전송을 막지 않는다.
            try:
                record_cleanup_handoff(
                    run_id=run_id,
                    source=metric_source,
                    agent_id=str(getattr(node, "_agent_id", "") or ""),
                    place=place,
                    target=target,
                    discovery_ms=(float(phase.get("discovery", 0.0)) if phase is not None else 0.0),
                    **fields,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[BT] Cleanup handoff record failed: %s", exc)

        local_cleanup_capable = _has_cleanup_capability(node)
        missing_local_destination = False
        objects = blackboard.get("detected_objects", []) or []
        scene = str(blackboard.get("scene_analysis", "") or "")
        target_categories = set(blackboard.get("cleanup_target_categories", []) or ())
        if not target_categories:
            target_categories = _cleanup_target_categories(objects, scene)
        cleanup_targets = _cleanup_target_labels(objects, scene)
        if not _has_cleanup_indication(objects, scene):
            return NodeStatus.SUCCESS

        target_description = ", ".join(cleanup_targets) or "장면 분석에서 확인한 정리 후보"
        place = str(blackboard.get("patrol_last_place", "") or "").strip()

        # [자신이 가능하면 바로 모션을 수행]
        skills = getattr(node, "_skills", None)
        local_demo_capable = False
        allowed_demo_ids: list[int] = []
        if skills is not None and hasattr(skills, "has_skill"):
            has_demo_skill = skills.has_skill("cleanup_demo") or skills.has_skill(
                "cloid_cleanup_demo"
            )
            has_motion_skill = skills.has_skill("execute_cloid_motion") or skills.has_skill(
                "cloid_motion"
            )
            if has_demo_skill and has_motion_skill and hasattr(node, "get_parameter"):
                try:
                    raw_ids = node.get_parameter("cloid_cleanup_demo_motion_ids_json").value
                    parsed_ids = json.loads(str(raw_ids))
                    if isinstance(parsed_ids, list):
                        allowed_demo_ids = [
                            mid
                            for mid in parsed_ids
                            if isinstance(mid, int) and not isinstance(mid, bool) and mid > 0
                        ]
                except Exception:
                    allowed_demo_ids = []
            local_demo_capable = bool(allowed_demo_ids)

        if blackboard.get("cleanup_task_mode"):
            destinations = blackboard.get("cleanup_destinations")
            if not isinstance(destinations, list):
                memory = getattr(node, "_memory", None)
                destinations = _safe_cleanup_disposal_targets(
                    memory
                ) + _safe_cleanup_storage_targets(memory)
            destination_categories = {
                category
                for destination in destinations
                if isinstance(destination, dict)
                for category in destination.get("categories", [])
                if isinstance(category, str)
            }
            missing_local_destination = not target_categories or not target_categories.issubset(
                destination_categories
            )
            if local_cleanup_capable:
                local_cleanup_capable = not missing_local_destination

        if local_cleanup_capable:
            # 실제 조작 로봇: 로컬 집기/배치 계획으로 진행
            return NodeStatus.SUCCESS

        if local_demo_capable and self._select_local_demo_motion(
            blackboard, skills, allowed_demo_ids, place, target_description
        ):
            return NodeStatus.SUCCESS

        pose_text = ""
        try:
            pose = self._skill.get_map_pose()
            if pose:
                pose_text = f"지도 위치 x={float(pose['x']):.2f}, y={float(pose['y']):.2f}"
        except (AttributeError, KeyError, TypeError, ValueError):
            pass
        location_parts = [value for value in (place, pose_text) if value]
        location = "; ".join(location_parts) or "정확한 위치는 알 수 없음"

        # [불가능할 때 동료를 불러 시연/조작 요청]
        peer = _find_peer("manipulation")
        handoff_kind = "manipulation"
        all_candidate_labels = list(cleanup_targets)
        for st in _cleanup_target_labels([], scene):
            if st not in all_candidate_labels:
                all_candidate_labels.append(st)

        demo_candidates = [
            t
            for t in all_candidate_labels
            if _cleanup_target_category(t) in {"waste", "towel", "cushion"}
        ]
        waste_cands = [t for t in demo_candidates if _cleanup_target_category(t) == "waste"]
        towel_cands = [t for t in demo_candidates if _cleanup_target_category(t) == "towel"]
        if waste_cands:
            demo_target = waste_cands[0]
        elif towel_cands:
            demo_target = towel_cands[0]
        elif demo_candidates:
            demo_target = demo_candidates[0]
        else:
            demo_target = ""

        if not demo_target and (target_categories & {"waste", "towel", "cushion"}):
            category_names = {
                "waste": "쓰레기",
                "towel": "수건",
                "cushion": "쿠션",
            }
            valid_cats = sorted(target_categories & {"waste", "towel", "cushion"})
            first_cat = "waste" if "waste" in valid_cats else valid_cats[0]
            demo_target = category_names.get(first_cat, first_cat)

        if peer is None:
            if demo_target:
                peer = _find_peer("cleanup_demo")
                if peer is not None:
                    handoff_kind = "cleanup_demo"
                    if not place:
                        peer = None
                        blackboard["cleanup_handoff_status"] = (
                            "정리 시연 동료에게 보낼 장소 이름을 확인할 수 없습니다. "
                            "요청을 보내지 않았습니다."
                        )
            elif (target_categories & {"waste", "towel", "cushion"}) and not demo_target:
                blackboard["cleanup_handoff_status"] = (
                    "정리 시연 동료에게 보낼 장소 이름 또는 정리 후보를 확인할 수 없습니다. "
                    "요청을 보내지 않았습니다."
                )

        elif peer is not None and (len(target_categories) != 1 or len(cleanup_targets) != 1):
            peer = None
            blackboard["cleanup_handoff_status"] = (
                "동료에게 전달할 단일 대상과 항목 유형을 확인할 수 없어 요청하지 않았습니다."
            )
        if peer is None:
            local_reason = ""
            if missing_local_destination:
                local_reason = (
                    "대상 유형과 일치하는 승인된 비추정 3D 보관 장소가 없습니다. 보관 위치를 지정해 주세요. "
                    if target_categories - {"waste"}
                    else "승인된 비추정 3D 폐기 장소가 없습니다. 폐기 장소를 지정해 주세요. "
                )
            blackboard.setdefault(
                "cleanup_handoff_status",
                f"{local_reason}연결 상태와 작업 능력이 확인된 동료 로봇이 없습니다. "
                "정리를 완료했다고 말하지 말고 발견 내용만 보고하세요.",
            )
            blackboard["cleanup_handoff_blocked"] = True
            blackboard["decided_skill"] = "none"
            blackboard["decided_params"] = {}
            blackboard["decided_reason"] = blackboard["cleanup_handoff_status"]
            _record_handoff(
                place=place,
                target=demo_target or target_description,
                # 동료를 찾지 못해 위임 종류가 확정되지 않았다.
                handoff_kind="",
                blocked=True,
                reason=str(blackboard["cleanup_handoff_status"]),
                candidates=_peer_candidate_snapshot(node),
            )
            self._skill.send_user_message(
                f"정리 대상 '{target_description}'을 처리하지 않았습니다. "
                f"{blackboard['cleanup_handoff_status']}"
            )
            return NodeStatus.SUCCESS

        instruction = self._build_handoff_instruction(
            handoff_kind=handoff_kind,
            target_categories=target_categories,
            target_description=target_description,
            location=location,
            scene=scene,
            demo_target=demo_target,
            place=place,
        )
        skill_name = "delegate_task" if peer["transport"] == "ros2" else "call_peer_robot"
        params = (
            {
                "agent_id": peer["peer_name"],
                "instruction": instruction,
                "timeout_sec": 180.0,
            }
            if skill_name == "delegate_task"
            else {
                "peer_name": peer["peer_name"],
                "instruction": instruction,
                "wait_for_result": True,
                "timeout_sec": 180.0,
            }
        )
        skills = getattr(node, "_skills", None)
        if skills is None or not skills.has_skill(skill_name):
            blackboard["cleanup_handoff_status"] = (
                f"동료 {peer['peer_name']}는 연결되어 있지만 요청 경로 {skill_name}을 사용할 수 없습니다."
            )
            blackboard["cleanup_handoff_blocked"] = True
            blackboard["decided_skill"] = "none"
            blackboard["decided_params"] = {}
            blackboard["decided_reason"] = blackboard["cleanup_handoff_status"]
            _record_handoff(
                place=place,
                target=demo_target or target_description,
                handoff_kind=handoff_kind,
                peer=str(peer["peer_name"]),
                transport=str(peer["transport"]),
                send_path=skill_name,
                blocked=True,
                reason=str(blackboard["cleanup_handoff_status"]),
                candidates=_peer_candidate_snapshot(node, peer["peer_name"]),
            )
            self._skill.send_user_message(
                f"정리 대상 '{target_description}'을 발견했지만 동료에게 작업을 전달할 수 없어 처리하지 않았습니다."
            )
            return NodeStatus.SUCCESS

        blackboard["decided_skill"] = skill_name
        blackboard["decided_params"] = params
        blackboard["decided_reason"] = (
            f"{peer['peer_name']}에게 임시 정리 시연 모션을 요청"
            if handoff_kind == "cleanup_demo"
            else f"자신에게 매니퓰레이션 능력이 없어 연결된 조작 가능 동료 {peer['peer_name']}에게 위임"
        )
        blackboard["cleanup_handoff_prepared"] = True
        blackboard["cleanup_handoff_kind"] = handoff_kind
        blackboard["cleanup_handoff_target"] = (
            demo_target if handoff_kind == "cleanup_demo" else target_description
        )
        blackboard["cleanup_handoff_status"] = (
            f"{peer['peer_name']}에게 임시 정리 시연 모션을 요청합니다."
            if handoff_kind == "cleanup_demo"
            else f"{peer['peer_name']}에게 정리 작업을 요청합니다."
        )
        _record_handoff(
            place=place,
            target=str(blackboard["cleanup_handoff_target"]),
            handoff_kind=handoff_kind,
            peer=str(peer["peer_name"]),
            transport=str(peer["transport"]),
            send_path=skill_name,
            reason=str(blackboard["decided_reason"]),
            candidates=_peer_candidate_snapshot(node, peer["peer_name"]),
        )
        logger.info("[BT] Prepared cleanup handoff to %s via %s", peer["peer_name"], skill_name)
        return NodeStatus.SUCCESS


class ExecuteDecidedAction(BTNode):
    """task_queue에서 꺼내거나 단일 decided_skill을 실행한다."""

    def __init__(self, skill: BaseSkill) -> None:
        super().__init__("ExecuteDecidedAction")
        self._skill = skill

    def tick(self) -> NodeStatus:
        node = self._skill.node

        task_queue: list[dict[str, Any]] = self._blackboard.get("task_queue", [])
        if task_queue:
            step = task_queue.pop(0)
            self._blackboard["task_queue"] = task_queue
            skill_name = step.get("skill", "")
            params = step.get("params", {})
            reason = self._blackboard.get("decided_reason", "")
            logger.info(
                "[BT] Executing from queue: %s (%d steps remaining)", skill_name, len(task_queue)
            )
        else:
            skill_name = self._blackboard.get("decided_skill", "")
            params = self._blackboard.get("decided_params", {})
            reason = self._blackboard.get("decided_reason", "")

        if skill_name == "none":
            self._blackboard["last_action_result"] = {"noop": True}
            self._blackboard["last_action_success"] = True
            logger.info("[BT] No actionable response required: %s", reason)
            return NodeStatus.SUCCESS
        if not skill_name:
            return NodeStatus.FAILURE

        self._skill.send_user_message(
            f"[자율 행동] {skill_name} 실행 중... (판단: {reason})"
            if reason
            else f"[자율 행동] {skill_name} 실행 중..."
        )

        cleanup_handoff = bool(self._blackboard.get("cleanup_handoff_prepared"))
        success = False
        result = None
        try:
            from robo_claw_agent.agent_node.nav_safety import ensure_navigation_safety

            # 로봇 프로필별 주행 전 안전 절차(stow/boxing prelude)를 체인에 삽입한다.
            chain = ensure_navigation_safety(
                [{"skill": skill_name, "params": params}],
                node._skills,
                logger,
                node,
            )
            result = None
            skill_timeout_sec = 120.0
            if skill_name in {"delegate_task", "call_peer_robot"}:
                try:
                    requested_timeout = float(params.get("timeout_sec", 180.0))
                    if 10.0 <= requested_timeout <= 240.0:
                        skill_timeout_sec = requested_timeout
                except (TypeError, ValueError):
                    pass
            for step in chain:
                result = node._skills.execute(
                    step["skill"],
                    step.get("params", {}),
                    timeout_sec=skill_timeout_sec,
                )
                if not result.success:
                    break
            if result is None:
                raise RuntimeError("실행할 자율행동 스킬이 없습니다.")
            self._blackboard["last_action_result"] = getattr(result, "result_data", {})
            self._blackboard["last_action_success"] = getattr(result, "success", False)
            success = getattr(result, "success", False)
            # 실험 계측 — 위임 요청 1건의 소요 시간을 정리 실행 구간 합계에 누적한다.
            duration_sec = float(getattr(result, "duration_sec", 0.0) or 0.0)
            self._blackboard["last_action_duration_sec"] = duration_sec
            if cleanup_handoff:
                phase = _phase_ms(self._blackboard)
                if phase is not None:
                    # phase_ms 는 밀리초 단위이므로 초 단위 duration 을 환산해 더한다.
                    phase["delegate"] = phase.get("delegate", 0.0) + duration_sec * 1000.0
                _record_delegate(
                    blackboard=self._blackboard,
                    node=node,
                    skill_name=skill_name,
                    params=params,
                    success=success,
                    message=str(getattr(result, "message", "") or ""),
                    duration_sec=duration_sec,
                )
            if not success:
                self._blackboard["queue_execution_failed"] = True
            logger.info(
                "[BT] Execution complete: %s → %s",
                skill_name,
                "success" if success else "failure",
            )
        except Exception as exc:
            logger.error("[BT] Skill execution error (%s): %s", skill_name, exc)
            self._blackboard["queue_execution_failed"] = True

        if cleanup_handoff:
            peer_name = params.get("peer_name") or params.get("agent_id") or "동료 로봇"
            result_message = str(getattr(result, "message", "") or "")
            if success and self._blackboard.get("cleanup_handoff_kind") == "cleanup_demo":
                self._blackboard["cleanup_handoff_status"] = (
                    f"{peer_name}의 임시 정리 시연 모션 요청 응답을 받았습니다."
                )
                self._skill.send_user_message(
                    f"'{self._blackboard.get('cleanup_handoff_target', '')}' 정리 시연을 "
                    f"{peer_name}에게 요청했습니다. {result_message} 실제 물체 정리 완료는 확인되지 않았습니다."
                )
            elif success:
                self._blackboard["cleanup_handoff_status"] = (
                    f"{peer_name}의 정리 작업 완료를 확인했습니다."
                )
                self._skill.send_user_message(
                    f"정리 대상 '{self._blackboard.get('cleanup_handoff_target', '')}'을 "
                    f"{peer_name}에게 요청했고 완료 응답을 받았습니다. {result_message}".strip()
                )
            else:
                uncertain = any(
                    marker in result_message.lower()
                    for marker in ("timeout", "시간 초과", "응답 없음", "결과 확인")
                )
                status = (
                    "결과를 확인하지 못했습니다. 중복 실행을 피하기 위해 자동 재요청하지 않습니다."
                    if uncertain
                    else "요청이 실패하거나 거절되었습니다."
                )
                self._blackboard["cleanup_handoff_status"] = status
                self._skill.send_user_message(
                    f"{peer_name}에게 정리 요청을 보냈지만 {status} {result_message}".strip()
                )
            self._blackboard["cleanup_handoff_prepared"] = False

        task_history = self._blackboard.setdefault("task_history", [])
        task_history.append(
            {
                "cycle": self._blackboard.get("cycle_count", 0),
                "decided_skill": skill_name,
                "decided_params": params,
                "decided_reason": reason,
                "success": success,
            }
        )
        self._blackboard["task_history"] = task_history

        target_key = (
            params.get("target_name")
            or params.get("target_location")
            or f"{params.get('x')},{params.get('y')}"
        )
        repeat_key = f"{skill_name}:{target_key}"
        if success:
            self._blackboard["repeat_failure_key"] = None
            self._blackboard["repeat_failure_count"] = 0
        elif self._blackboard.get("repeat_failure_key") == repeat_key:
            self._blackboard["repeat_failure_count"] = (
                self._blackboard.get("repeat_failure_count", 0) + 1
            )
        else:
            self._blackboard["repeat_failure_key"] = repeat_key
            self._blackboard["repeat_failure_count"] = 1

        return NodeStatus.SUCCESS


class SleepBetweenCycles(BTNode):
    """사이클 간 대기. stop_autonomous 호출 시 즉시 깨어남."""

    def __init__(self, interval_sec: float = 3.0) -> None:
        super().__init__("SleepBetweenCycles")
        self._interval = interval_sec

    def tick(self) -> NodeStatus:
        _SLEEP_WAKE.clear()
        _SLEEP_WAKE.wait(timeout=self._interval)
        return NodeStatus.SUCCESS


class CheckGoalAchieved(BTNode):
    """task_queue가 비어 있으면 자율 행동 루프를 종료한다 (goal 모드 전용)."""

    def __init__(self, skill: BaseSkill) -> None:
        super().__init__("CheckGoalAchieved")
        self._skill = skill

    def tick(self) -> NodeStatus:
        if self._blackboard.get("queue_execution_failed"):
            logger.info("[BT] CheckGoalAchieved: plan execution had failure(s) — stopping loop")
            self._skill.send_user_message(
                "목표 계획 실행 중 실패가 발생하여 자율 행동을 종료합니다."
            )
            release_autonomous()
            return NodeStatus.FAILURE
        if self._blackboard.get("goal_achieved"):
            logger.info("[BT] CheckGoalAchieved: goal achieved — stopping autonomous loop")
            self._skill.send_user_message("목표를 달성했습니다. 자율 행동을 종료합니다.")
            release_autonomous()
            return NodeStatus.SUCCESS
        if not self._blackboard.get("task_queue"):
            logger.info("[BT] CheckGoalAchieved: task queue empty — plan completed")
            self._blackboard["goal_achieved"] = True
            self._skill.send_user_message("목표 계획을 모두 실행했습니다. 자율 행동을 종료합니다.")
            release_autonomous()
            return NodeStatus.SUCCESS
        return NodeStatus.SUCCESS
