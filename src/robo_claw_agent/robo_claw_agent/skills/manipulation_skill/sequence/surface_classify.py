"""물체가 바닥에 있는지, 높은 곳에 있는지 판단하는 스킬.

그리퍼 카메라(D405)는 손목 고정이라 ``wrist_extension`` 이 늘수록 대상이 화면
밖으로 밀리고 근접하면 YOLO 탐지가 실패한다. 파지 최종 단계를 근접 비주얼 서보
대신 "표면 유형별 고정 패턴"으로 대체할 때, 물체가 완전히 바닥인지 어딘가에
올라와 있는지의 이진 판단이 필요하다.

이 판단은 헤드 카메라(D435i)의 안정적인 base_link 3D 관측을 사용한다. 헤드는
마스트 상단에 있어 팔이 뻗어도 프레이밍이 무너지지 않고, 관측 지점이 물체에서
0.8~1.0m 로 유효 측정 범위 안이다. base_link 기준 z 좌표 하나만 보면 되므로
depth 정확도 요구사항이 낮고, 바닥(≈0)과 테이블/선반(0.3m+ 정도)을 확실히
분리할 수 있다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from robo_claw_agent.manipulation_runtime import (
    ManipulationConfigError,
    ManipulationRuntimeUnavailableError,
)
from robo_claw_agent.skill_manager import BaseSkill

from ..core import _get_runtime
from .head_assist import HeadAssist
from .head_vision import _observe_head_target
from .params import _bool_param, _float_param

logger = logging.getLogger(__name__)

# 바닥/높은 곳 판단 임계값(m). 이보다 낮으면 "floor", 이상이면 "elevated".
# 컵 중심이 바닥 위 약 5~15cm 에 위치하므로 0.15m 는 테이블(0.3m+)과 겹치지 않는
# 안전한 기본값이다.
_DEFAULT_FLOOR_Z_THRESHOLD_M = 0.15


class ClassifyObjectSurfaceSkill(BaseSkill):
    """헤드 카메라 3D 관측으로 물체가 바닥/높은 곳 중 어디에 있는지 판단."""

    name = "classify_object_surface"
    answer_mode = "informational"
    is_internal = True
    input_schema = {
        "type": "object",
        "properties": {
            "target_object": {"type": "string"},
            "camera": {"type": "string", "default": "base"},
        },
        "required": ["target_object"],
        "additionalProperties": False,
    }
    description = (
        "Stretch3에서 target_object가 완전히 바닥(floor)에 있는지 높은 곳(elevated)에 "
        "있는지 판단합니다. 헤드 카메라 RGB-D로 물체의 base_link 3D 좌표를 관측하고 "
        "z 값이 floor_z_threshold_m(기본 0.15) 미만이면 floor, 이상이면 elevated를 "
        "반환합니다. surface_hint(floor/elevated)를 주면 관측 없이 그 값을 우선 씁니다. "
        "물체의 3D 좌표(object_base_xyz)와 관측 결과도 함께 반환합니다."
    )

    def validate_params(self, params: dict[str, Any]) -> bool:
        return bool(str(params.get("target_object") or "").strip())

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        target_object = str(params.get("target_object") or "cup").strip()
        floor_z_threshold_m = _float_param(
            params, "floor_z_threshold_m", _DEFAULT_FLOOR_Z_THRESHOLD_M
        )
        surface_hint = str(params.get("surface_hint") or "").strip().lower()

        # 명시적 힌트가 있으면 관측 없이 바로 판단한다(호출부가 이미 알고 있는 경우).
        if surface_hint in {"floor", "elevated"}:
            return {
                "success": True,
                "message": f"표면 힌트({surface_hint})로 판단",
                "target_object": target_object,
                "surface": surface_hint,
                "surface_from": "hint",
                "floor_z_threshold_m": floor_z_threshold_m,
            }

        # 호출부가 이미 3D 좌표를 알고 있으면(object_base_xyz 파라미터) 재관측 없이
        # 그 z 값으로 바로 판단한다. 관측이 비싸고 근접에서는 그리퍼 카메라가
        # 불안정하므로, 가능하면 이미 얻은 좌표를 우선 사용하는 것이 낫다.
        raw = params.get("object_base_xyz")
        if isinstance(raw, Mapping):
            try:
                provided_xyz = (float(raw["x"]), float(raw["y"]), float(raw["z"]))
            except (KeyError, TypeError, ValueError):
                provided_xyz = None
            if provided_xyz is not None:
                z = provided_xyz[2]
                surface = "floor" if z < floor_z_threshold_m else "elevated"
                return {
                    "success": True,
                    "message": f"'{target_object}'는 {surface}(z={z:.3f}m, 제공 좌표)",
                    "target_object": target_object,
                    "surface": surface,
                    "surface_from": "object_base_xyz",
                    "z_m": round(z, 4),
                    "floor_z_threshold_m": floor_z_threshold_m,
                    "object_base_xyz": {
                        "x": round(provided_xyz[0], 4),
                        "y": round(provided_xyz[1], 4),
                        "z": round(provided_xyz[2], 4),
                    },
                }

        try:
            runtime = _get_runtime(self.node)
        except (ManipulationConfigError, ManipulationRuntimeUnavailableError) as exc:
            logger.warning("Surface classify failed: %s", exc)
            return {
                "success": False,
                "message": f"표면 판단 실패: {exc}",
                "target_object": target_object,
            }

        # 헤드 카메라로 물체 3D 좌표를 관측한다. ArUco 잔차 보정이 적용되도록
        # HeadAssist.observe()를 사용하고, 보조가 비활성이면 직접 관측으로 폴백한다.
        obj_xyz: tuple[float, float, float] | None = None
        observation: dict[str, Any] | None = None

        use_head_assist = _bool_param(params.get("use_head_assist"), True) and (
            runtime.config.backend == "stretch"
        )
        if use_head_assist:
            head_assist = HeadAssist(self, params, enabled=True)
            try:
                obj_xyz, observation = head_assist.observe()
            except Exception as exc:  # noqa: BLE001 - 보조 기능이므로 모든 예외 흡수
                logger.warning("Head assist observe raised, using direct observation: %s", exc)
                obj_xyz, observation = None, None

        if obj_xyz is None:
            if self.node is not None:
                try:
                    observation = _observe_head_target(self, params)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Head observation raised: %s", exc)
                    observation = None
            if observation is not None:
                raw = observation.get("object_base_xyz")
                if isinstance(raw, Mapping):
                    try:
                        obj_xyz = (float(raw["x"]), float(raw["y"]), float(raw["z"]))
                    except (KeyError, TypeError, ValueError):
                        obj_xyz = None

        if obj_xyz is None:
            return {
                "success": False,
                "message": f"헤드 카메라로 '{target_object}'의 3D 좌표를 얻지 못해 표면을 판단할 수 없습니다.",
                "target_object": target_object,
                "observation": observation,
            }

        z = obj_xyz[2]
        surface = "floor" if z < floor_z_threshold_m else "elevated"
        return {
            "success": True,
            "message": f"'{target_object}'는 {surface}(z={z:.3f}m)",
            "target_object": target_object,
            "surface": surface,
            "surface_from": "observation",
            "z_m": round(z, 4),
            "floor_z_threshold_m": floor_z_threshold_m,
            "object_base_xyz": {
                "x": round(obj_xyz[0], 4),
                "y": round(obj_xyz[1], 4),
                "z": round(obj_xyz[2], 4),
            },
            "observation": observation,
        }
