"""LLM 이 고른 계획을 지도 목표 계약에 맞춰 검증한다.

지도 목표 요청의 실제 이동은 하나뿐이다: 사용자가 지도에서 고른 좌표/프레임으로
가는 ``navigate_to``. 나머지 물리 동작(회전, 상대 이동, 웨이포인트, 조작 등)은
요청에 없으므로 거부한다. 읽기 전용 도구는 그대로 허용한다.

검증은 LLM 이 고른 **원본** 체인에 먼저 적용하고, 통과한 체인에만 기존
``ensure_navigation_safety`` 가 로봇별 주행 전 안전 prelude(boxing/stow)를
삽입한다. 삽입된 항목에는 엔진 표식(``POLICY_PRELUDE_FLAG``)을 붙여 엔진이 직접
실행하게 한다(Stretch3 ``stow_for_navigation`` 처럼 public 도구여도 마찬가지).
모델이 스스로 그 표식을 붙이거나 stow/자세 같은 준비 동작을 계획하면 거부한다.
"""

import logging
from typing import Any

from ...domain.map_goal_guard import is_physical_skill
from ..nav_safety import POLICY_PRELUDE_FLAG, ensure_navigation_safety
from .contract import (
    CONTEXT_TYPE,
    COORD_TOLERANCE_M,
    MapGoalError,
    canonical_coordinate,
)
from .scope import NAVIGATION_SKILL, MapGoalScope

logger = logging.getLogger(__name__)


def _skill_of(skills: Any, name: str) -> Any | None:
    try:
        return skills.get_skill(name)
    except Exception:  # noqa: BLE001
        return None


def _is_readonly(skill: Any) -> bool:
    return skill is not None and str(getattr(skill, "risk_level", "action")) == "read"


def _is_motion(skill: Any, name: str) -> bool:
    if skill is None:
        return is_physical_skill(name)
    return is_physical_skill(
        name,
        side_effects=getattr(skill, "side_effects", ()),
        exclusive_resources=getattr(skill, "exclusive_resources", ()),
    )


def _check_navigation_params(params: dict[str, Any], scope: MapGoalScope) -> str:
    """LLM 이 고른 navigate_to 파라미터가 선택 좌표/프레임과 같은지 검증한다."""
    context = scope.context
    raw_x, raw_y = params.get("x"), params.get("y")
    if raw_x is None or raw_y is None:
        return "지도 목표 이동에는 선택한 좌표(x, y)가 그대로 필요합니다."
    try:
        x = canonical_coordinate(raw_x)
        y = canonical_coordinate(raw_y)
    except MapGoalError as exc:
        return f"지도 목표 좌표가 유효하지 않습니다: {exc}"
    if abs(x - context.x) > COORD_TOLERANCE_M or abs(y - context.y) > COORD_TOLERANCE_M:
        return (
            "계획 좌표가 지도에서 선택한 좌표와 다릅니다. "
            f"선택 좌표: x={context.x:.6f}, y={context.y:.6f} (변경 금지)."
        )
    if str(params.get("target_name") or "").strip():
        return "지도 목표를 다른 장소명(target_name)으로 바꿀 수 없습니다."
    frame = str(params.get("frame_id") or "map").strip()
    if frame != context.frame_id:
        return f"계획 프레임이 선택한 프레임({context.frame_id})과 다릅니다: {frame}"
    if params.get("yaw") is not None:
        return "지도 목표에는 도착 yaw 를 지정할 수 없습니다."
    if params.get("align_heading_first") not in (None, False):
        return "지도 목표에는 사전 정렬 회전(align_heading_first)을 요청할 수 없습니다."
    return ""


def validate_map_goal_chain(
    chain: list[dict[str, Any]], scope: MapGoalScope, skills: Any
) -> tuple[list[dict[str, Any]], str]:
    """LLM 이 고른 원본 계획 체인을 검증한다. 실패 시 ``([], 사유)``.

    이 함수는 **모델이 만든 체인**만 받는다. 안전 prelude 표식은 엔진만 붙일 수
    있으므로, 모델 체인에 표식이 있으면 위조로 보고 거부한다.
    """
    if not chain:
        return [], "지도 목표 요청에는 검증된 좌표로 이동하는 계획이 필요합니다."

    physical_chain = any(
        not _is_readonly(_skill_of(skills, str(item.get("skill") or "").strip())) for item in chain
    )
    if scope.prep_hold() and physical_chain:
        # 준비 동작 결과가 확정되지 않았으면(타임아웃/모호한 실패) 추가 물리 동작을 금지한다.
        return [], (
            "주행 전 안전 준비 동작의 결과가 확정되지 않아 추가 이동/준비 동작을 계획할 수 없습니다."
        )
    if scope.dispatch_attempted() and physical_chain:
        # 주행을 이미 시도한 요청은 결과가 확정되기 전까지 추가 물리 동작을 계획할 수 없다.
        return [], (
            "이미 지도 목표 주행을 시도했습니다. 결과가 확정되지 않아 추가 이동/준비 동작을 "
            "계획할 수 없습니다."
        )

    kept: list[dict[str, Any]] = []
    navigation_item: dict[str, Any] | None = None
    for item in chain:
        name = str(item.get("skill") or "").strip()
        params = item.get("params") or {}
        if not name:
            return [], "스킬 이름이 비어 있는 계획은 실행할 수 없습니다."
        if not isinstance(params, dict):
            return [], f"{name}의 params 는 객체여야 합니다."
        if item.get(POLICY_PRELUDE_FLAG):
            # 엔진만 안전 prelude 를 삽입/표시할 수 있다(모델 표식은 위조).
            return [], (
                f"계획이 신뢰 표식을 위조했습니다: {name}. "
                "주행 안전 준비 동작은 엔진이 직접 삽입합니다."
            )
        skill = _skill_of(skills, name)
        if _is_readonly(skill):
            kept.append(item)
            continue
        if name == NAVIGATION_SKILL:
            if navigation_item is not None:
                return [], "지도 목표 이동은 요청당 한 번만 계획할 수 있습니다."
            error = _check_navigation_params(params, scope)
            if error:
                return [], error
            navigation_item = item
            kept.append(item)
            continue
        if _is_motion(skill, name):
            return [], f"지도 목표 요청에 없는 이동/회전 스킬입니다: {name}"
        if skill is None:
            return [], f"등록되지 않은 스킬입니다: {name}"
        return [], f"지도 목표 요청에는 이동 외 물리 동작을 계획할 수 없습니다: {name}"

    if navigation_item is None:
        return [], "지도 목표 요청에는 검증된 좌표로 이동하는 navigate_to 계획이 필요합니다."
    return kept, ""


def apply_map_goal_safety(
    chain: list[dict[str, Any]],
    scope: MapGoalScope,
    skills: Any,
    logger_obj: Any,
    node: Any = None,
) -> tuple[list[dict[str, Any]], str]:
    """검증된 체인에 로봇별 주행 전 안전 prelude 를 삽입하고 엔진 표식을 붙인다.

    순서는 기존 ``ensure_navigation_safety`` 규칙(Stretch stow → CLOiD boxing)을
    그대로 유지한다. 검증이 실패하면 어떤 스킬도 실행하지 않도록 빈 체인을 돌려준다.
    """
    validated, error = validate_map_goal_chain(chain, scope, skills)
    if error:
        logger.warning("Map goal plan rejected: %s (chain=%s)", error, chain)
        return [], error
    protected = ensure_navigation_safety(validated, skills, logger_obj, node)
    model_item_ids = {id(item) for item in validated}
    for item in protected:
        if id(item) in model_item_ids:
            # 모델이 고른 항목: 표식을 붙이지 않는다(다음 검증에서 위조로 보이지 않도록).
            continue
        # 엔진이 삽입한 안전 prelude: 엔진이 직접 실행하도록 trusted 표식을 붙인다.
        item[POLICY_PRELUDE_FLAG] = True
    return protected, ""


def map_goal_system_context(scope: MapGoalScope) -> str:
    """LLM 에 넣을 지도 목표 컨텍스트(타입이 고정된 데이터만).

    HTTP 호출자가 준 원문을 시스템 프롬프트로 그대로 넣지 않는다. 좌표/프레임은
    지시가 아니라 데이터이며, 스킬 이름을 강제하지 않는다.
    """
    context = scope.context
    return (
        f"[지도 목표 컨텍스트]\ntype={CONTEXT_TYPE}\n"
        f"robot_id={context.robot_id}\n"
        f"frame_id={context.frame_id}\n"
        f"x={context.x:.6f}\n"
        f"y={context.y:.6f}\n"
        f"geometry_id={context.geometry_id}\n"
        f"request_id={context.request_id}\n"
        "위 좌표와 프레임은 사용자가 지도에서 선택한 값입니다. 값을 바꾸거나 다른 "
        "장소명으로 대체하지 말고 그대로 사용하세요."
    )
