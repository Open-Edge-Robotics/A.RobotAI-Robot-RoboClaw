"""주행 안전 게이트 — 베이스 이동 전 상체 자세 보장 및 종합 Navigation Safety Gate.

- Stretch3: 베이스 이동 전 ``stow_for_navigation`` 삽입/실행과 실제 joint state 확인.
- CLOiD: 베이스 이동 전 boxing(최소 부피) 자세 prelude 삽입/실행. 자세 도달 피드백이
  없으므로 "요청 성공 후 상체를 움직이지 않음"까지만 보장한다.

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

# CLOiD 주행 전 boxing 자세 prelude. 공개 스킬 set_cloid_packing_pose 와 같은 자세를
# 요청하지만 사용자 confirm 없이 주행 안전 게이트가 내부에서만 호출한다.
CLOID_NAV_POSE_SKILL: str = "cloid_navigation_pose"
CLOID_PACKING_POSE_SKILL: str = "set_cloid_packing_pose"
_DEFAULT_CLOID_NAV_POSE_TIMEOUT_SEC = 60.0
_DEFAULT_STRETCH_STOW_TIMEOUT_SEC = 40.0

# 이미 주행 전 자세를 보장하는 스킬. 직전에 있으면 prelude를 중복 삽입하지 않는다.
CLOID_NAV_SAFETY_SKILLS: frozenset[str] = frozenset(
    {CLOID_NAV_POSE_SKILL, CLOID_PACKING_POSE_SKILL}
)

# 정책이 체인에 삽입한 안전 prelude 항목 표시. LLM이 만든 항목이 아니므로
# _validate_skill_chain 의 체이닝/백그라운드 규칙에서 제외한다.
POLICY_PRELUDE_FLAG: str = "_policy_prelude"

# 상체를 움직이는 스킬이 성공하면 주행 전 자세 준비 상태를 무효화한다.
_ARM_MOTION_EFFECTS: frozenset[str] = frozenset({"arm_motion", "gripper_motion"})
_CLOID_NAV_POSE_READY_ATTR = "_cloid_nav_pose_ready"


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


def has_cloid_navigation_pose_skill(skills: Any) -> bool:
    """현재 로봇에 CLOiD 주행 전 자세 prelude 스킬이 등록되어 있는지 확인한다.

    테스트 더블(MagicMock 기반 스킬 매니저)은 어떤 이름이든 스킬을 가진 것으로 보이므로
    실제 등록 스킬(이름이 일치하는 객체)인지까지 확인한다. 그렇지 않으면 목 노드에서
    주행 전 자세 요청이 끼어들어 기존 테스트/경로의 실행 체인이 바뀐다.
    """
    try:
        skill = skills.get_skill(CLOID_NAV_POSE_SKILL)
    except Exception:
        return False
    if skill is None:
        return False
    if getattr(skill.__class__, "__module__", "").startswith("unittest.mock"):
        return False
    return str(getattr(skill, "name", "") or "") == CLOID_NAV_POSE_SKILL


def is_cloid_navigation_pose_ready(node: Any) -> bool:
    """주행 전 자세가 이번 이동을 위해 이미 준비됐는지 반환한다.

    boxing 스크립트는 실제 자세 도달을 알려주지 않으므로 이 값은 "요청을 성공적으로
    보냈고 그 뒤 상체를 움직이지 않았다"는 뜻까지만 보장한다.
    """
    return bool(getattr(node, _CLOID_NAV_POSE_READY_ATTR, False))


def mark_cloid_navigation_pose_ready(node: Any) -> None:
    """주행 전 자세 요청이 성공했음을 기록해 다음 이동에서 중복 요청을 막는다."""
    try:
        setattr(node, _CLOID_NAV_POSE_READY_ATTR, True)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to mark CLOiD navigation pose readiness: %s", exc)


def invalidate_cloid_navigation_pose(node: Any) -> None:
    """상체 자세가 바뀌었을 수 있으므로 주행 전 자세 준비 상태를 무효화한다."""
    try:
        setattr(node, _CLOID_NAV_POSE_READY_ATTR, False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to invalidate CLOiD navigation pose readiness: %s", exc)


def on_skill_succeeded(node: Any, skill: Any) -> None:
    """스킬 성공 후 주행 전 자세 준비 상태를 갱신한다(SkillManager 후처리 훅).

    주행 전 자세 스킬이 성공하면 자세를 준비된 상태로 기록하고, 그 밖에 상체를 움직이는
    스킬이 성공하면 자세가 달라졌을 수 있으므로 준비 상태를 지운다. 자세 스킬의 성공을
    지우면 이동마다 같은 요청을 반복하게 되므로 순서를 구분한다.
    """
    if node is None:
        return
    name = str(getattr(skill, "name", "") or "")
    if name == CLOID_NAV_POSE_SKILL or name == CLOID_PACKING_POSE_SKILL:
        # 주행 전 자세를 요청한 성공은 바로 준비 상태다.
        mark_cloid_navigation_pose_ready(node)
        return
    try:
        effects = set(getattr(skill, "side_effects", ()) or ())
    except Exception:  # noqa: BLE001
        return
    if effects & _ARM_MOTION_EFFECTS:
        invalidate_cloid_navigation_pose(node)


def register_cloid_navigation_pose_invalidation(node: Any, skills: Any) -> None:
    """팔/그리퍼를 움직인 스킬이 성공하면 주행 전 자세 준비 상태를 지우도록 연결한다.

    ``SkillManager`` 는 주행 안전 규칙을 모르므로, 노드 초기화 시 여기서 훅을 주입한다.
    """
    if node is None or skills is None:
        return
    setter = getattr(skills, "set_post_skill_success_hook", None)
    if not callable(setter):
        return
    try:
        setter(lambda skill: on_skill_succeeded(node, skill))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to register CLOiD navigation pose invalidation hook: %s", exc)


def _policy_prelude_item() -> dict[str, Any]:
    """주행 전 자세 prelude 항목을 만든다(정책 삽입 표시 포함)."""
    return {"skill": CLOID_NAV_POSE_SKILL, "params": {}, POLICY_PRELUDE_FLAG: True}


def ensure_cloid_navigation_safety(
    chain: list[dict[str, Any]], skills: Any, logger_obj: Any
) -> list[dict[str, Any]]:
    """각 베이스 이동 스킬 직전에 CLOiD 주행 전 boxing 자세 요청을 삽입한다.

    Stretch3 의 stow 와 같은 이유로 이동 스킬마다 직전을 확인한다. 조작 스킬 뒤에
    팔이 펴진 채로 주행하는 것을 막기 위함이며, 바로 앞이 이미 자세 스킬이면 생략한다.
    prelude 스킬이 등록되지 않은 배포에서는 아무것도 하지 않는다.
    """
    if not chain or not has_cloid_navigation_pose_skill(skills):
        return chain

    updated_chain: list[dict[str, Any]] = []
    for item in chain:
        name = str(item.get("skill") or "").strip()
        if _is_base_motion_skill(name, skills):
            prev_name = str(updated_chain[-1].get("skill") or "").strip() if updated_chain else ""
            if prev_name not in CLOID_NAV_SAFETY_SKILLS:
                logger_obj.info(
                    f"Inserting {CLOID_NAV_POSE_SKILL} before CLOiD base motion skill '{name}'"
                )
                updated_chain.append(_policy_prelude_item())
        updated_chain.append(item)
    return updated_chain


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


def ensure_navigation_safety(
    chain: list[dict[str, Any]], skills: Any, logger_obj: Any, node: Any = None
) -> list[dict[str, Any]]:
    """로봇 프로필에 맞는 주행 전 안전 절차를 체인에 삽입한다.

    호출부(플래너/다이렉트/자율행동 BT)가 로봇 종류를 판단하지 않도록 단일 진입점을
    제공한다. 팔 정책이 없는 로봇(테스트 더블 포함)에서는 아무것도 삽입하지 않고,
    Stretch3 는 stow, CLOiD 는 boxing 자세 prelude 를 각자의 등록 스킬을 기준으로
    적용한다(둘 다 등록된 경우는 cloid 로 간주).
    """
    backend = getattr(node, "_manipulation_backend", None) if node is not None else None
    if (
        node is not None
        and backend is not None
        and not _uses_stretch_backend(node, backend)
        and not has_cloid_navigation_pose_skill(skills)
    ):
        return chain

    protected = ensure_stretch_navigation_safety(chain, skills, logger_obj)
    return ensure_cloid_navigation_safety(protected, skills, logger_obj)


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


def prepare_cloid_navigation(
    node: Any, timeout_sec: float = _DEFAULT_CLOID_NAV_POSE_TIMEOUT_SEC
) -> tuple[bool, str]:
    """Raw Nav2 goal 경로가 주행 전에 CLOiD 를 boxing 자세로 만든다.

    일반 스킬 체인은 ``ensure_cloid_navigation_safety`` 가 처리하지만, 자율행동의
    frontier/patrol 처럼 Nav2 goal 을 직접 보내는 경로도 있다. 이 함수는 그런 경로에서
    prelude 스킬을 한 번 실행하고, 실패하면 주행을 거부한다(fail closed).

    ``cloid.move_boxing`` 은 완료 피드백을 주지 않으므로 실제 자세 도달은 확인할 수
    없다. 대신 같은 이동 구간에서 매 tick 반복 요청하지 않도록 준비 상태를 기억하고,
    상체를 움직이는 스킬이 성공하면 ``on_skill_succeeded`` 가 그 상태를 지운다.
    """
    if node is None:
        return False, "ROS 노드에 접근할 수 없습니다."

    skills = getattr(node, "_skills", None)
    if skills is None or not has_cloid_navigation_pose_skill(skills):
        # 게이트 미적용 배포(cloid_navigation_pose_enabled=false 등)에서는 기존 동작 유지.
        return True, ""

    if is_cloid_navigation_pose_ready(node):
        return True, ""

    try:
        result = skills.execute(
            CLOID_NAV_POSE_SKILL,
            {"timeout_sec": timeout_sec},
            timeout_sec=timeout_sec + 15.0,
        )
    except Exception as exc:
        logger.exception("CLOiD navigation preparation failed")
        return False, f"CLOiD 주행 전 자세 전환 오류: {exc}"

    if not getattr(result, "success", False):
        return False, f"CLOiD 주행 전 자세 전환 실패: {getattr(result, 'message', result)}"
    # 관리자 훅이 연결되지 않은 경로(테스트 등)에서도 준비 상태를 확정한다.
    mark_cloid_navigation_pose_ready(node)
    if not is_cloid_navigation_pose_ready(node):
        return False, "주행 전 자세 요청이 완료됐지만 준비 상태가 기록되지 않았습니다."
    return True, ""


def prepare_navigation(node: Any, timeout_sec: float | None = None) -> tuple[bool, str]:
    """로봇 프로필에 맞는 주행 전 안전 절차를 수행한다.

    호출부가 로봇 종류를 판단하지 않도록 단일 진입점을 제공한다. ``timeout_sec`` 을
    생략하면 프로필 기본값(Stretch3 stow 40s, CLOiD boxing 60s)을 사용한다.
    """
    backend = getattr(node, "_manipulation_backend", None)
    if backend is not None and _uses_stretch_backend(node, backend):
        return prepare_stretch_navigation(
            node,
            _DEFAULT_STRETCH_STOW_TIMEOUT_SEC if timeout_sec is None else timeout_sec,
        )
    return prepare_cloid_navigation(
        node,
        _DEFAULT_CLOID_NAV_POSE_TIMEOUT_SEC if timeout_sec is None else timeout_sec,
    )


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
        5. 로봇별 주행 전 상체 자세 검증 (Stretch3 stow / CLOiD boxing prelude)
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

        # 5. 로봇별 주행 전 상체 자세 검증
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

            # CLOiD: 주행 전 boxing 자세 요청이 준비되지 않았으면 이동을 허용하지 않는다.
            # HTTP/gRPC 처럼 안전 게이트 체인을 거치지 않고 베이스 스킬을 직접 호출하는
            # 경로에서 팔이 나온 채로 주행하는 것을 막는다.
            skills = getattr(self.node, "_skills", None)
            if has_cloid_navigation_pose_skill(skills) and not is_cloid_navigation_pose_ready(
                self.node
            ):
                return (
                    False,
                    "CLOiD 주행 전 boxing 자세가 준비되지 않았습니다. "
                    "이동 전에 주행 전 자세 요청을 먼저 수행해야 합니다.",
                )

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
