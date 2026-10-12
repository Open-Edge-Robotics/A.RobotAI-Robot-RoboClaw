"""One-room, one-target CLOi cleanup demonstration; never claims physical cleanup."""

from __future__ import annotations

import json
import logging
import random
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .autonomous_skill.globals import _AUTONOMOUS_ACTIVE, claim_autonomous, release_autonomous
from .cleanup_policy import (
    _has_ambiguous_cleanup_indication,
    _has_cleanup_indication,
    _normalize_name,
    _select_cleanup_demo_motion,
)

logger = logging.getLogger(__name__)


class CleanupDemoSkill(BaseSkill):
    """Navigate to a remembered room, re-observe one waste candidate, and request one motion."""

    name = "cleanup_demo"
    risk_level = "dangerous"
    is_internal = True
    side_effects = ("base_motion", "arm_motion", "gripper_motion")
    exclusive_resources = ("base_control", "cloid_motion")
    input_schema = {
        "type": "object",
        "properties": {
            "target_location": {"type": "string", "minLength": 1, "maxLength": 80},
            "target_object": {"type": "string", "minLength": 1, "maxLength": 120},
            "scene_summary": {"type": "string", "maxLength": 800},
            "request_id": {"type": "string", "maxLength": 80},
        },
        "required": ["request_id", "target_location", "target_object", "scene_summary"],
        "additionalProperties": False,
    }
    description = (
        "동료가 지정한 기억 장소로 이동해 폐기물 후보 하나를 다시 관찰하고, 설정된 검토 모션을 한 번 "
        "시연합니다. 물체를 집거나 배치하지 않으며 실제 정리 완료를 의미하지 않습니다. "
        "내부 버전 관리 요청에서만 사용합니다."
    )
    preconditions = (
        "이 기능이 활성화된 CLOi 개체에서만 등록됩니다.",
        "장소가 로컬 기억 좌표로 해석되고 요청된 대상이 현장에서 다시 확인되어야 합니다.",
        "운영자가 명시적으로 허용한 선행 동작 없는 모션만 실행합니다.",
    )
    postconditions = ("결과는 임시 모션 요청 상태이며 물체 정리나 모션 완료를 확인하지 않습니다.",)

    def __init__(self) -> None:
        super().__init__()
        self._previous_motion_id: int | None = None

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        location = str(params.get("target_location", "")).strip()
        target = str(params.get("target_object", "")).strip()
        scene_summary = str(params.get("scene_summary", "")).strip()
        request_id = str(params.get("request_id", "")).strip()
        if (
            not location
            or len(location) > 80
            or not target
            or len(target) > 120
            or len(scene_summary) > 800
            or not request_id
            or len(request_id) > 80
        ):
            return self.fail_result(
                "정리 시연 요청의 장소·대상·설명이 올바르지 않습니다.",
                failure_reason="invalid_request",
            )
        if (error := self.require_node()) is not None:
            return error
        if _has_ambiguous_cleanup_indication([target], ""):
            return self.fail_result(
                f"요청 대상 '{target}'은 모호하거나 위험할 수 있어 시연을 중단했습니다.",
                failure_reason="ambiguous_target",
            )
        allowed_ids = self._allowed_motion_ids()
        if not allowed_ids:
            return self.fail_result(
                "검토된 정리 시연 모션이 설정되지 않아 동작하지 않았습니다.",
                failure_reason="no_approved_cleanup_demo_motion",
            )
        skills = getattr(self.node, "_skills", None)
        if (
            skills is None
            or not skills.has_skill("list_cloid_motions")
            or not skills.has_skill("execute_cloid_motion")
        ):
            return self.fail_result(
                "CLOi 모션 조회·실행 스킬을 사용할 수 없습니다.",
                failure_reason="motion_skills_unavailable",
            )
        if not claim_autonomous(_AUTONOMOUS_ACTIVE):
            return self.fail_result(
                "다른 자율 행동이 실행 중이라 정리 시연을 시작하지 않았습니다.",
                failure_reason="autonomous_busy",
                recoverable=True,
            )

        try:
            from .tidy_home_skill import TidyHomeSkill

            room = TidyHomeSkill()
            room.set_node(self.node)
            places, error = room._collect_places("room", location)
            if error or len(places) != 1:
                return self.fail_result(
                    f"동료가 요청한 기억 장소 '{location}'를 한 곳으로 확인하지 못했습니다.",
                    failure_reason=error or "target_location_ambiguous",
                )
            place_name = str(places[0]["name"])
            self.send_user_message(
                f"동료의 정리 시연 요청에 따라 기억된 장소 '{place_name}'로 이동합니다."
            )
            arrived, nav_message = room._visit_place(place_name)
            if not arrived:
                return self.fail_result(
                    f"'{place_name}'에 도착하지 못해 정리 시연을 중단했습니다. {nav_message}",
                    failure_reason="location_unreachable",
                    recoverable=True,
                )

            observation = room._analyze_current_scene(place_name)
            if not observation.get("success"):
                return self.fail_result(
                    f"'{place_name}'에서 요청 대상을 다시 관찰하지 못했습니다. "
                    f"{observation.get('message', '')}".strip(),
                    failure_reason="observation_failed",
                    recoverable=True,
                )
            detected = observation.get("detected_objects", []) or []
            fresh_scene = str(observation.get("analysis", "") or "")
            if not self._target_reobserved(target, detected, fresh_scene):
                return self.fail_result(
                    f"'{place_name}'에서 요청 대상 '{target}'을 다시 확인하지 못해 시연하지 않았습니다.",
                    failure_reason="target_not_reobserved",
                )
            if _has_ambiguous_cleanup_indication(detected, fresh_scene):
                return self.fail_result(
                    f"'{place_name}'에 모호하거나 위험할 수 있는 물체가 보여 시연을 중단했습니다.",
                    failure_reason="ambiguous_scene",
                )
            if not _has_cleanup_indication(detected, fresh_scene):
                return self.fail_result(
                    f"'{place_name}'에서 명확한 폐기물 후보를 확인하지 못해 시연하지 않았습니다.",
                    failure_reason="cleanup_target_not_confirmed",
                )

            catalog = skills.execute("list_cloid_motions", {"timeout_sec": 3.0}, timeout_sec=5.0)
            if not catalog.success:
                return self.fail_result(
                    f"CLOi 모션 목록을 확인하지 못했습니다: {catalog.message}",
                    failure_reason="motion_catalog_unavailable",
                    recoverable=True,
                )
            motion = _select_cleanup_demo_motion(
                catalog.result_data.get("motions", []),
                allowed_ids,
                rng=random.Random(),
                previous_motion_id=self._previous_motion_id,
            )
            if motion is None:
                return self.fail_result(
                    "현재 모션 목록에 설정된 안전한 정리 시연 모션이 없습니다.",
                    failure_reason="no_approved_cleanup_demo_motion",
                    recoverable=True,
                )

            result = skills.execute(
                "execute_cloid_motion",
                {
                    "motion_name": motion["motion_name"],
                    "motion_id": motion["motion_id"],
                    "confirm": True,
                },
                timeout_sec=10.0,
            )
            if not result.success:
                return self.fail_result(
                    f"정리 시연 모션 실행이 거부되거나 실패했습니다: {result.message}",
                    failure_reason="motion_request_failed",
                    recoverable=True,
                )
            self._previous_motion_id = motion["motion_id"]
            return self.success_result(
                f"'{place_name}'에서 요청 대상 '{target}'을 다시 확인하고 임시 정리 시연 모션 "
                f"'{motion['motion_name']}' 실행을 요청했습니다. 실제 물체 정리와 모션 완료는 확인되지 않았습니다.",
                status="motion_requested",
                request_id=request_id,
                target_location=place_name,
                target_object=target,
                motion_id=motion["motion_id"],
                physical_cleanup_confirmed=False,
                motion_completion_confirmed=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[CleanupDemo] Demo task failed")
            return self.fail_result(
                f"정리 시연을 안전하게 마치지 못했습니다: {exc}",
                failure_reason="cleanup_demo_failed",
                recoverable=True,
            )
        finally:
            release_autonomous()

    def _allowed_motion_ids(self) -> list[int]:
        try:
            raw = self.node.get_parameter("cloid_cleanup_demo_motion_ids_json").value
            value = json.loads(str(raw))
        except Exception:
            return []
        if not isinstance(value, list):
            return []
        return [
            item
            for item in value
            if isinstance(item, int) and not isinstance(item, bool) and item > 0
        ]

    @staticmethod
    def _target_reobserved(target: str, detected: Any, scene: str) -> bool:
        expected = _normalize_name(target)
        if not expected:
            return False
        labels = []
        if isinstance(detected, (list, tuple)):
            labels.extend(
                item.get("name", "") if isinstance(item, dict) else str(item) for item in detected
            )
        observed = _normalize_name(" ".join([*(str(label) for label in labels), scene]))
        return expected in observed
