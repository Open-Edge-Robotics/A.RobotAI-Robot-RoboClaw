"""헤드 pan/tilt 스윕과 몸통 회전을 결합한 능동 물체 탐색 스킬.

Stretch3는 팔이 몸통 오른쪽에 고정되어 있어, 물체를 집으려면 먼저 어느 방향에
있는지 알아야 몸통을 그 쪽으로 돌릴 수 있다. 기존에는 현재 헤딩에서 베이스
카메라로 '한 번만' 확인하고 실패하면 바로 포기했기 때문에, 카메라 시야 밖에
있는 물체는 사실상 찾을 수 없었다. 이 스킬은 Hello Robot 공식 stretch_funmap
패키지(HeadScan.execute_front, manipulation_planning.move_head)에서 검증된 값을
참고해 (1) 헤드를 내려다보게 tilt한 뒤 pan을 좌우로 스윕하고, (2) 그래도 못
찾으면 몸통을 일정 각도씩 회전하며 pan 스윕을 반복하는 능동 탐색을 수행한다.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from robo_claw_agent.manipulation_runtime import (
    ManipulationConfigError,
    ManipulationRuntimeUnavailableError,
)
from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills.navigation_skill.move_relative import MoveRelativeSkill

from ..core import _get_runtime
from ..head import HeadPanTiltSkill
from .align_skill import AlignRightArmToFrontSkill
from .params import _angle_sequence_deg, _bool_param, _float_param
from .rotation import _localize_rotation_with_base_camera

logger = logging.getLogger(__name__)

# Hello Robot stretch_funmap의 HeadScan.execute_front 기본값(rad)을 deg로 환산.
_DEFAULT_SEARCH_TILT_DEG = -45.8  # ≈ -0.8 rad
_DEFAULT_PAN_SWEEP_DEG = [-68.8, 0.0, 68.8]  # ≈ [-1.2, 0.0, 1.2] rad
_DEFAULT_BODY_ROTATION_STEP_DEG = 90.0
_DEFAULT_MAX_BODY_ROTATIONS = 3
_DEFAULT_HEAD_SETTLE_SEC = 0.5
# Tier 4: pan 뷰당 관측 재시도 — 헤드 스윕 중 모션 블러/temporal 필터 때문에 한
# 번에 놓치는 것을 감안해 같은 뷰에서 여러 번 재관측한다.
_DEFAULT_VIEW_RETRIES = 2
# Tier 4: 전체 스윕 실패 후 물체 쪽으로 접근해 한 번 더 스윕(원거리 소형 물체 대응).
_DEFAULT_APPROACH_SEARCH_DISTANCE_M = 0.5
_DEFAULT_MAX_APPROACH_SCANS = 1


class SearchObjectSkill(BaseSkill):
    """헤드 pan/tilt 스윕 + 몸통 회전 스윕으로 target_object의 방위를 능동 탐색."""

    name = "search_object"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "target_object": {"type": "string"},
            "search_angles_deg": {"type": "array", "items": {"type": "number"}},
            "max_body_rotations": {"type": "integer"},
            "body_rotation_step_deg": {"type": "number"},
        },
        "required": ["target_object"],
        "additionalProperties": True,
    }
    description = (
        "Stretch3에서 target_object가 어느 방향에 있는지 모를 때 사용하는 능동 탐색 "
        "스킬입니다. 헤드 카메라를 아래로 tilt한 뒤 pan을 좌우로 스윕하며 "
        "find_object로 탐지를 시도하고, 그래도 못 찾으면 몸통을 일정 각도씩 회전하며 "
        "pan 스윕을 반복합니다. 성공하면 몸통을 물체 쪽으로 정렬하는 데 필요한 "
        "align_angle_deg(오른쪽 팔 작업축 기준)를 함께 반환합니다. "
        "view_retries(기본 2)는 pan 뷰당 재관측 횟수로 모션 블러/임시 필터에 강인해지고, "
        "approach_search_distance_m(기본 0.5)은 전체 스윕 실패 후 물체 쪽으로 접근해 "
        "재스윕하는 거리(m)입니다."
    )

    def validate_params(self, params: dict[str, Any]) -> bool:
        return bool(str(params.get("target_object") or "").strip())

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        target_object = str(params.get("target_object") or "").strip()
        if not target_object:
            return {"success": False, "message": "target_object 파라미터가 필요합니다."}

        try:
            runtime = _get_runtime(self.node)
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
        ) as exc:
            logger.warning("Active search failed: %s", exc)
            return {"success": False, "message": f"능동 탐색 실패: {exc}"}

        use_head = _bool_param(params.get("use_head_camera"), True) and (
            runtime.config.backend == "stretch"
        )
        search_tilt_deg = _float_param(params, "search_tilt_deg", _DEFAULT_SEARCH_TILT_DEG)
        pan_sweep_deg = _angle_sequence_deg(
            params.get("pan_sweep_deg"), list(_DEFAULT_PAN_SWEEP_DEG)
        )
        body_rotation_step_deg = _float_param(
            params, "body_rotation_step_deg", _DEFAULT_BODY_ROTATION_STEP_DEG
        )
        max_body_rotations = int(params.get("max_body_rotations", _DEFAULT_MAX_BODY_ROTATIONS))
        head_settle_sec = _float_param(params, "head_settle_sec", _DEFAULT_HEAD_SETTLE_SEC)
        # Tier 4: pan 뷰당 재관측 횟수와 전체 실패 후 접근 재스윕 설정.
        view_retries = max(1, int(params.get("view_retries", _DEFAULT_VIEW_RETRIES)))
        approach_distance_m = _float_param(
            params, "approach_search_distance_m", _DEFAULT_APPROACH_SEARCH_DISTANCE_M
        )
        max_approach_scans = max(
            0, int(params.get("max_approach_scans", _DEFAULT_MAX_APPROACH_SCANS))
        )

        pan_candidates = pan_sweep_deg if use_head else [None]
        steps: list[dict[str, Any]] = []
        view_index = 0

        def _sweep() -> tuple[bool, dict[str, Any]]:
            """회전+pan 스윕 1회(뷰당 view_retries회 재관측). (성공여부, 결과)."""
            nonlocal view_index
            cumulative_body_rotation_deg = 0.0

            for rotation_round in range(max(0, max_body_rotations) + 1):
                for pan_deg in pan_candidates:
                    if use_head and pan_deg is not None:
                        head = HeadPanTiltSkill()
                        head.set_node(self.node)
                        head_result = head.execute(
                            {"pan_deg": pan_deg, "tilt_deg": search_tilt_deg}
                        )
                        steps.append(
                            {
                                "skill": "head_pan_tilt",
                                "pan_deg": pan_deg,
                                "tilt_deg": search_tilt_deg,
                                "result": head_result,
                            }
                        )
                        if head_result.get("success", False):
                            time.sleep(max(0.0, head_settle_sec))
                        else:
                            # 헤드 제어가 실패해도(구성/백엔드 문제 등) 탐색 자체는
                            # 베이스 카메라 단독으로 계속 진행한다(best-effort).
                            logger.warning(
                                "Head pan/tilt move failed, continuing search without head assist: %s",
                                head_result.get("message", ""),
                            )

                    for retry in range(view_retries):
                        theta_rad, find_result = _localize_rotation_with_base_camera(
                            self.node, params, target_object
                        )
                        steps.append(
                            {
                                "skill": "find_object",
                                "view_index": view_index,
                                "retry": retry + 1,
                                "pan_deg": pan_deg,
                                "body_rotation_deg": cumulative_body_rotation_deg,
                                "result": find_result,
                            }
                        )
                        view_index += 1
                        if find_result.get("success", False):
                            return True, {
                                "success": True,
                                "message": (
                                    f"'{target_object}' 탐지 성공 "
                                    f"(몸통 누적회전={cumulative_body_rotation_deg:.0f}°, "
                                    f"헤드 pan={pan_deg if pan_deg is not None else 'N/A'})"
                                ),
                                "target_object": target_object,
                                "find_result": find_result,
                                "align_angle_deg": (
                                    math.degrees(theta_rad) if theta_rad is not None else None
                                ),
                                "body_rotation_applied_deg": cumulative_body_rotation_deg,
                                "head_pan_deg": pan_deg,
                                "view_retries": view_retries,
                                "steps": list(steps),
                            }
                        if retry < view_retries - 1:
                            # temporal 필터/temporal 확인이 채워지고 잔상이 가라앉도록
                            # 짧은 settling 후 재관측한다.
                            time.sleep(max(0.0, head_settle_sec))

                if rotation_round >= max_body_rotations:
                    break

                align = AlignRightArmToFrontSkill()
                align.set_node(self.node)
                align_result = align.execute({"angle_deg": body_rotation_step_deg})
                steps.append(
                    {
                        "skill": "align_right_arm_to_front",
                        "angle_deg": body_rotation_step_deg,
                        "purpose": "search_rotate",
                        "result": align_result,
                    }
                )
                if not align_result.get("success", False):
                    # 회전 한계 등으로 더 이상 회전 탐색을 진행할 수 없다. 지금까지의
                    # 결과로 탐색 실패를 반환한다(예외를 던지지 않음).
                    logger.warning(
                        "Aborting search due to search rotation failure: %s",
                        align_result.get("message", ""),
                    )
                    break
                cumulative_body_rotation_deg += body_rotation_step_deg

            return False, {
                "success": False,
                "message": f"탐색 범위 내에서 '{target_object}'를 찾지 못했습니다.",
                "target_object": target_object,
                "body_rotation_applied_deg": cumulative_body_rotation_deg,
                "steps": list(steps),
            }

        ok, result = _sweep()
        if ok:
            return result

        # Tier 4: 원거리/소형 물체가 놓칠 수 있으므로, 물체 쪽으로 접근해 스윕을
        # 한 번 더 반복한다(best-effort — 이동 실패 시 기존 실패 결과로 마무리).
        approach_scans = 0
        while approach_scans < max_approach_scans and approach_distance_m > 0:
            move = MoveRelativeSkill()
            move.set_node(self.node)
            move_result = move.execute(
                {"forward": approach_distance_m, "timeout_sec": head_settle_sec + 5.0}
            )
            steps.append(
                {
                    "skill": "move_relative",
                    "purpose": "search_approach",
                    "forward_m": approach_distance_m,
                    "result": move_result,
                }
            )
            if not move_result.get("success", False):
                logger.warning(
                    "Search approach move failed, giving up: %s",
                    move_result.get("message", ""),
                )
                break
            approach_scans += 1
            ok, result = _sweep()
            if ok:
                result["approach_scans"] = approach_scans
                return result

        result["approach_scans"] = approach_scans
        result["steps"] = steps
        return result
