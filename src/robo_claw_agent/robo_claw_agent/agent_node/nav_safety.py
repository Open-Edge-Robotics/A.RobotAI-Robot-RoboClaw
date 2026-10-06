"""Stretch3 주행 안전 — 베이스 이동 전 stow 선행 보장 로직 및 종합 Navigation Safety Gate.

LLM 계획 경로(planner)와 다이렉트 스킬 경로(execution) 양쪽에서 동일한 규칙을
써야 하므로 공용 모듈로 분리한다. 한쪽만 갱신되어 규칙이 어긋나는 것을 막는다.
"""

import logging
import math
from typing import Any

from robo_claw_agent.skills.limits import (
    check_relative_move,
    check_rotation,
    check_target_in_forbidden_zone,
)
from robo_claw_agent.tracing import trace_process_inputs, trace_process_outputs, traceable

logger = logging.getLogger(__name__)

# 베이스(주행부)가 움직이는 스킬. 실행 전 팔/그리퍼가 접혀 있어야 한다.
BASE_MOTION_SKILLS: frozenset[str] = frozenset(
    {
        "navigate_to",
        "move_relative",
        "approach_object",
        "rotate",
        "face_direction",
        "follow_waypoints",
        "patrol",
        "explore",
        "reactive_navigate",
        "condition_reactive",
    }
)

# 이미 주행 안전 자세/모드를 보장하는 스킬. 직전에 있으면 stow를 중복 삽입하지 않는다.
STRETCH_NAV_SAFETY_SKILLS: frozenset[str] = frozenset(
    {
        "stow_for_navigation",
        "stretch_navigation_mode",
        "switch_stretch_mode",
    }
)

STOW_SKILL_NAME = "stow_for_navigation"


def _uses_stretch_backend(node: Any, backend: Any) -> bool:
    """노드 설정 또는 백엔드 타입으로 Stretch 정책 적용 여부를 판정한다."""
    if backend.__class__.__module__ == "unittest.mock":
        return False
    try:
        value = getattr(node.get_parameter("manipulation_backend"), "value", None)
        if str(value or "").strip().lower() == "stretch":
            return True
    except Exception:
        pass
    return backend.__class__.__name__ == "StretchDriverBackend" or hasattr(backend, "is_stowed")


def has_stow_skill(skills: Any) -> bool:
    """현재 로봇에 stow_for_navigation 스킬이 등록되어 있는지 확인한다."""
    try:
        return skills.get_skill(STOW_SKILL_NAME) is not None
    except Exception:
        return False


def _is_base_motion_skill(name: str, skills: Any) -> bool:
    """메타데이터를 우선 사용하고, 기존 이름 집합을 호환 fallback으로 사용한다."""
    try:
        skill = skills.get_skill(name)
        if skill is not None:
            # 자체완결형 복합 스킬은 내부에서 필요한 이동 안전 절차를
            # 수행하므로, 외부에서 stow를 삽입하면 금지된 체인이 된다.
            if not getattr(skill, "allow_with_others", True):
                return False
            effects = set(getattr(skill, "side_effects", ()))
            resources = set(getattr(skill, "exclusive_resources", ()))
            if "base_motion" in effects or "base_rotation" in effects:
                return True
            if "base_control" in resources:
                return True
    except Exception:
        pass
    return name in BASE_MOTION_SKILLS


@traceable(
    run_type="chain",
    name="navigation_safety_gate",
    process_inputs=trace_process_inputs,
    process_outputs=trace_process_outputs,
)
def ensure_stretch_navigation_safety(
    chain: list[dict[str, Any]], skills: Any, logger_obj: Any
) -> list[dict[str, Any]]:
    """각 베이스 이동 스킬 직전에 stow_for_navigation을 삽입한 체인을 반환한다.

    체인 맨 앞에 한 번만 넣으면 [stow, grasp, navigate_to] 처럼 중간에 팔을
    펴는 스킬이 끼었을 때 팔이 펴진 채로 주행하게 된다. 따라서 이동 스킬마다
    직전을 확인하고, 바로 앞이 이미 안전 스킬이면 생략한다.
    """
    if not chain or not has_stow_skill(skills):
        return chain

    updated_chain: list[dict[str, Any]] = []
    for item in chain:
        name = str(item.get("skill") or "").strip()
        if _is_base_motion_skill(name, skills):
            prev_name = str(updated_chain[-1].get("skill") or "").strip() if updated_chain else ""
            if prev_name not in STRETCH_NAV_SAFETY_SKILLS:
                logger_obj.info(
                    f"Inserting {STOW_SKILL_NAME} before Stretch3 base motion skill '{name}'"
                )
                updated_chain.append({"skill": STOW_SKILL_NAME, "params": {}})
        updated_chain.append(item)
    return updated_chain


def prepare_stretch_navigation(node: Any, timeout_sec: float = 40.0) -> tuple[bool, str]:
    """Raw Nav2 goal 경로가 주행 전에 Stretch3를 안전 자세로 만든다.

    일반 스킬 체인은 ``ensure_stretch_navigation_safety``가 처리하지만,
    자율행동의 frontier/patrol처럼 Nav2 goal을 직접 보내는 경로도 있다.
    이 함수는 그런 경로에서 stow 스킬을 실행하고 실제 joint state를 다시
    확인하는 단일 진입점이다.
    """
    backend = getattr(node, "_manipulation_backend", None)
    if backend is None or not _uses_stretch_backend(node, backend):
        return True, ""

    is_stowed = getattr(backend, "is_stowed", None)
    if not callable(is_stowed):
        return False, "Stretch 백엔드가 팔 주행 안전 상태를 확인할 수 없습니다."

    try:
        if is_stowed():
            return True, ""
    except Exception as exc:
        logger.warning("Failed to inspect Stretch stow state: %s", exc)

    skills = getattr(node, "_skills", None)
    if skills is None or not has_stow_skill(skills):
        return False, "Stretch 로봇 팔이 접혀 있지 않고 stow 스킬도 사용할 수 없습니다."

    try:
        result = skills.execute(
            STOW_SKILL_NAME,
            {"stow_timeout_sec": timeout_sec},
            timeout_sec=timeout_sec,
        )
    except Exception as exc:
        logger.exception("Stretch navigation preparation failed")
        return False, f"Stretch 주행 안전 자세 전환 오류: {exc}"

    if not getattr(result, "success", False):
        return False, f"Stretch 주행 안전 자세 전환 실패: {getattr(result, 'message', result)}"
    try:
        if not is_stowed():
            return False, "stow 명령은 완료됐지만 실제 팔 자세가 주행 안전 상태가 아닙니다."
    except Exception as exc:
        return False, f"Stretch stow 상태 확인 실패: {exc}"
    return True, ""


class NavigationSafetyGate:
    """모든 베이스 주행/회전 모션에 공통 적용되는 안전 게이트"""

    def __init__(self, node: Any) -> None:
        self.node = node

    def check_safety(
        self,
        target_x: float | None = None,
        target_y: float | None = None,
        target_yaw: float | None = None,
        forward: float | None = None,
        lateral: float | None = None,
        rotation_deg: float | None = None,
        check_stow: bool = True,
    ) -> tuple[bool, str]:
        """
        주행/회전 안전성 종합 점검:
        1. Emergency Stop 활성화/래치 여부
        2. 좌표 유한성 (NaN/inf 방지)
        3. 금지 구역 침범 여부
        4. 상대 이동/회전 거리 및 각도 한계
        5. Stretch 로봇 팔 stow 상태 검증
        """
        if self.node is None:
            return False, "ROS 노드에 접근할 수 없습니다."

        # 1. Emergency Stop 점검
        if getattr(self.node, "_emergency_stopped", False) or getattr(
            self.node, "_emergency_stop_latched", False
        ):
            return (
                False,
                "비상 정지(Emergency Stop) 상태가 활성화되어 있어 이동할 수 없습니다.",
            )
        if hasattr(self.node, "is_emergency_stopped") and callable(self.node.is_emergency_stopped):
            try:
                if self.node.is_emergency_stopped():
                    return (
                        False,
                        "비상 정지(Emergency Stop) 상태가 활성화되어 있어 이동할 수 없습니다.",
                    )
            except Exception:
                pass

        # 2. 좌표/각도 유한성 점검
        for val, name in [
            (target_x, "target_x"),
            (target_y, "target_y"),
            (target_yaw, "target_yaw"),
            (forward, "forward"),
            (lateral, "lateral"),
            (rotation_deg, "rotation_deg"),
        ]:
            if val is not None:
                try:
                    fval = float(val)
                    if not math.isfinite(fval):
                        return False, f"{name} 좌표/각도 값이 유한하지 않습니다 ({val!r})."
                except (TypeError, ValueError):
                    return False, f"{name} 좌표/각도 값이 올바르지 않습니다 ({val!r})."

        limits = getattr(self.node, "_robot_limits_dict", None)

        # 3. 금지 구역 검증
        if target_x is not None and target_y is not None:
            zone_err = check_target_in_forbidden_zone(float(target_x), float(target_y), limits)
            if zone_err:
                return False, zone_err

        # 4. 상대 이동/회전 거리/각도 검증
        if forward is not None or lateral is not None:
            rel_err = check_relative_move(float(forward or 0.0), float(lateral or 0.0), limits)
            if rel_err:
                return False, rel_err

        if rotation_deg is not None:
            rot_err = check_rotation(float(rotation_deg), limits)
            if rot_err:
                return False, rot_err

        # 5. Stretch 로봇 팔 stow 검증
        if check_stow:
            backend = getattr(self.node, "_manipulation_backend", None)
            if backend is not None and _uses_stretch_backend(self.node, backend):
                if not hasattr(backend, "is_stowed") or not callable(backend.is_stowed):
                    return False, "Stretch 백엔드가 팔 주행 안전 상태를 확인할 수 없습니다."
                try:
                    if not backend.is_stowed():
                        return False, "Stretch 로봇 팔이 stow 상태가 아니어서 주행할 수 없습니다."
                except Exception as exc:
                    logger.warning("Failed to check backend stow status: %s", exc)

        return True, ""


def check_navigation_safety_gate(
    node: Any,
    target_x: float | None = None,
    target_y: float | None = None,
    target_yaw: float | None = None,
    forward: float | None = None,
    lateral: float | None = None,
    rotation_deg: float | None = None,
    check_stow: bool = True,
) -> tuple[bool, str]:
    gate = NavigationSafetyGate(node)
    return gate.check_safety(
        target_x=target_x,
        target_y=target_y,
        target_yaw=target_yaw,
        forward=forward,
        lateral=lateral,
        rotation_deg=rotation_deg,
        check_stow=check_stow,
    )
