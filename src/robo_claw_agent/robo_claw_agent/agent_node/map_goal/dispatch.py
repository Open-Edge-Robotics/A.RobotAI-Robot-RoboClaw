"""지도 목표 주행 직전 검증과 결과 증거.

``navigate_to`` 는 지도 목표 스코프가 활성일 때 이 가드를 통과해야만 실제 Nav2
goal 을 보낸다:

1. 계획 좌표/프레임이 선택값과 같은지 재확인(방어적).
2. 선택 유효 시간(60초) 경과 여부 확인(첫 실제 주행에서만).
3. 같은 지도 토픽에서 현재 OccupancyGrid 를 다시 읽어 지오메트리 ID 와
   확인된 자유 셀(값 0)을 재검증.
4. 요청당 이동 1회 예약(두 번째 시도는 거부).

결과는 ``map_goal_request`` 증거로 결과 dict 에 붙는다. 대시보드는 이 증거(또는
같은 요청에 대한 에이전트 기록)가 있을 때만 도착으로 표시할 수 있다.
"""

import logging
from typing import Any

from .contract import (
    COORD_TOLERANCE_M,
    SELECTION_WINDOW_SEC,
    MapGoalContext,
    now_epoch,
)
from .scope import MapGoalScope, current_map_goal_scope
from .tracker import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_REACHED,
    STATUS_RUNNING,
    STATUS_UNKNOWN,
    TERMINAL_STATUSES,
    get_map_goal_tracker,
)

logger = logging.getLogger(__name__)

# 직전 지도 재확인: 대기 2초, 동적 지도는 10초 이내 신선도 요구.
MAP_READ_TIMEOUT_SEC = 2.0
MAP_MAX_AGE_SEC = 10.0

_TIMEOUT_MARKERS = ("타임아웃", "받지 못했습니다")
_CANCEL_MARKER = "취소되었습니다."

REJECT_SELECTION_EXPIRED = "map_goal_selection_expired"
REJECT_MAP_UNAVAILABLE = "map_goal_map_unavailable"
REJECT_MAP_FRAME_MISMATCH = "map_goal_map_frame_mismatch"
REJECT_GEOMETRY_MISMATCH = "map_goal_geometry_mismatch"
REJECT_CELL_UNCONFIRMED = "map_goal_cell_unconfirmed"
REJECT_DUPLICATE_DISPATCH = "map_goal_duplicate_dispatch"
REJECT_BUDGET_EXHAUSTED = "map_goal_budget_exhausted"
REJECT_COORDINATES_MISMATCH = "map_goal_coordinates_mismatch"
REJECT_PREP_UNCONFIRMED = "map_goal_prep_unconfirmed"


class MapGoalNavigationGuard:
    """지도 목표 주행 1회를 감싸는 가드."""

    def __init__(self, skill: Any, scope: MapGoalScope) -> None:
        self.skill = skill
        self.scope = scope
        self.node = getattr(skill, "node", None)
        self._tracker = get_map_goal_tracker(self.node) if self.node is not None else None
        self._goal_token: int | None = None

    # ── 진입 ────────────────────────────────────────────────────────────────
    @classmethod
    def begin(
        cls, skill: Any, *, x: Any, y: Any, frame: str
    ) -> tuple["MapGoalNavigationGuard | None", str]:
        """스코프가 없으면 ``(None, "")``. 요청이 있으면 검증 후 가드를 돌려준다."""
        scope = current_map_goal_scope()
        if scope is None:
            return None, ""
        context = scope.context

        try:
            matches = (
                abs(float(x) - context.x) <= COORD_TOLERANCE_M
                and abs(float(y) - context.y) <= COORD_TOLERANCE_M
            )
        except (TypeError, ValueError):
            matches = False
        if not matches:
            return None, "계획 좌표가 지도에서 선택한 좌표와 다릅니다."
        if str(frame or "") != context.frame_id:
            return None, f"계획 프레임이 선택한 프레임({context.frame_id})과 다릅니다."

        guard = cls(skill, scope)
        if not scope.execution_started and now_epoch() > context.expires_at:
            guard._fail(
                REJECT_SELECTION_EXPIRED,
                f"지도에서 선택한 목표의 유효 시간({SELECTION_WINDOW_SEC:.0f}초)이 지났습니다. "
                "지도에서 다시 선택해 주세요.",
            )
            return (
                None,
                f"지도에서 선택한 목표의 유효 시간({SELECTION_WINDOW_SEC:.0f}초)이 지났습니다.",
            )

        if scope.prep_hold():
            message = "주행 전 안전 준비 동작의 결과가 확정되지 않아 주행을 시작하지 않습니다."
            guard._fail(REJECT_PREP_UNCONFIRMED, message)
            return None, message

        if scope.remaining_budget_sec() <= 0.0:
            guard._fail(
                REJECT_BUDGET_EXHAUSTED,
                "지도 목표 요청의 주행 시간 예산이 모두 지났습니다.",
            )
            return None, "지도 목표 요청의 주행 시간 예산이 모두 지났습니다."

        ok, error = guard._verify_current_map(context)
        if not ok:
            return None, error

        claimed, claim_error = scope.claim_dispatch()
        if not claimed:
            guard._fail(REJECT_DUPLICATE_DISPATCH, claim_error)
            return None, claim_error
        return guard, ""

    def _fail(self, reason: str, message: str) -> None:
        """가드 진입 실패를 기록한다.

        권위 있는 실제 주행 결과가 아니므로, 결과 미확정(``unknown``)/취소 진행
        (``cancel_requested``) 기록은 덮지 않는다(tracker 가 보호한다).
        """
        if self._tracker is not None:
            self._tracker.transition(
                self.scope.request_id, STATUS_FAILED, message=message, reason=reason
            )

    def _verify_current_map(self, context: MapGoalContext) -> tuple[bool, str]:
        """주행 직전 지도를 다시 읽어 지오메트리/자유 셀을 재검증한다."""
        from .contract import geometry_from_map_message

        waiter = getattr(self.skill, "wait_for_map_message", None)
        if not callable(waiter):
            return False, self._reject(
                REJECT_MAP_UNAVAILABLE, "지도 확인 기능을 사용할 수 없습니다."
            )
        try:
            message = waiter(
                context.map_topic,
                timeout_sec=MAP_READ_TIMEOUT_SEC,
                max_age_sec=MAP_MAX_AGE_SEC,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Map goal map read failed: %s", exc)
            return False, self._reject(
                REJECT_MAP_UNAVAILABLE, f"현재 지도({context.map_topic})를 읽지 못했습니다."
            )
        if message is None:
            return False, self._reject(
                REJECT_MAP_UNAVAILABLE, f"현재 지도({context.map_topic})를 확인할 수 없습니다."
            )

        validator = getattr(self.skill, "validate_message_frame", None)
        if callable(validator):
            valid, reason = validator(message, context.frame_id, "OccupancyGrid")
            if not valid:
                return False, self._reject(REJECT_MAP_FRAME_MISMATCH, reason)

        geometry = geometry_from_map_message(message, context.map_topic, context.frame_id)
        if geometry is None:
            return False, self._reject(
                REJECT_MAP_UNAVAILABLE, "현재 지도의 지오메트리를 해석하지 못했습니다."
            )
        if geometry.geometry_id() != context.geometry_id:
            return False, self._reject(
                REJECT_GEOMETRY_MISMATCH,
                "현재 지도가 목표를 선택할 때의 지도와 다릅니다. 지도에서 다시 선택해 주세요.",
            )
        if not geometry.is_confirmed_free(context.x, context.y):
            return False, self._reject(
                REJECT_CELL_UNCONFIRMED,
                f"선택한 지점(x={context.x:.6f}, y={context.y:.6f})이 지도에서 확인된 "
                "자유 셀이 아닙니다. 지도에서 다시 선택해 주세요.",
            )
        return True, ""

    def _reject(self, reason: str, message: str) -> str:
        """검증 실패를 기록하고 사용자 메시지를 돌려준다(주행은 시작하지 않는다)."""
        logger.warning("Map goal dispatch rejected (%s): %s", reason, message)
        self._fail(reason, message)
        return message

    # ── 주행 예산/실행 표시 ─────────────────────────────────────────────────
    def timeout_sec(self, default: float) -> float:
        """요청 예산 안으로 제한한 Nav2 결과 대기 시간."""
        remaining = self.scope.remaining_budget_sec()
        if remaining <= 0.0:
            return 1.0
        return min(float(default), remaining)

    def mark_execution_started(self, goal_handle: Any = None) -> None:
        """실제 Nav2 goal 을 보낸 뒤 호출한다(도착 판정과 무관)."""
        self.scope.mark_execution_started()
        token = getattr(goal_handle, "_goal_token", None)
        if isinstance(token, int):
            self._goal_token = token
        if self._tracker is not None:
            self._tracker.transition(
                self.scope.request_id,
                STATUS_RUNNING,
                message="주행을 시작했습니다.",
                reason="",
                execution_started=True,
            )

    # ── 결과 확정 ───────────────────────────────────────────────────────────
    def finalize(self, result: dict[str, Any], *, success: bool, result_message: str) -> dict:
        """주행 결과를 요청 기록에 반영하고 ``map_goal_request`` 증거를 붙인다."""
        status, reason, cancel_confirmed = classify_navigation_outcome(
            success=success, result_message=result_message
        )
        message = str(result.get("message") or result_message or "")
        if self._tracker is not None:
            self._tracker.transition(
                self.scope.request_id,
                status,
                message=message,
                reason=reason,
                execution_started=self.scope.execution_started,
                cancel_confirmed=cancel_confirmed,
                # 실제 주행 결과(성공/취소/미확인)만 권위 있는 전이로 인정한다.
                authoritative=True,
            )
        result["map_goal_request"] = self.evidence(
            status=status,
            message=message,
            reason=reason,
            cancel_confirmed=cancel_confirmed,
        )
        return result

    def evidence(
        self,
        *,
        status: str,
        message: str,
        reason: str,
        cancel_confirmed: bool,
    ) -> dict[str, Any]:
        context = self.scope.context
        return {
            "request_id": context.request_id,
            "robot_id": context.robot_id,
            "status": status,
            "message": message,
            "reason": reason,
            "frame_id": context.frame_id,
            "x": context.x,
            "y": context.y,
            "geometry_id": context.geometry_id,
            # 실제 실행 효과(goal 전송 여부)를 그대로 보고한다. 이미 오차 안이라
            # goal 을 보내지 않은 경로는 False 로 남는다.
            "execution_started": self.scope.execution_started,
            "goal_sent": self.scope.execution_started,
            "cancel_confirmed": cancel_confirmed,
            "goal_token": self._goal_token,
            "recorded_at": now_epoch(),
        }


def classify_navigation_outcome(*, success: bool, result_message: str) -> tuple[str, str, bool]:
    """Nav2 결과 문자열을 요청 상태/사유/취소 확인 여부로 분류한다.

    결과를 확인하지 못한 경우는 ``unknown`` 이며 취소 확인은 False 다. 로봇이
    멈췄다고 단정하지 않기 위해서다.
    """
    text = str(result_message or "")
    if success:
        return STATUS_REACHED, "", False
    if text == _CANCEL_MARKER:
        return STATUS_CANCELLED, "navigation_cancelled", True
    if any(marker in text for marker in _TIMEOUT_MARKERS):
        return STATUS_UNKNOWN, "navigation_result_unknown", False
    return STATUS_FAILED, "navigation_failed", False


def finish_navigation_dispatch(
    result: dict[str, Any],
    guard: MapGoalNavigationGuard | None,
    *,
    success: bool,
    result_message: str,
) -> dict[str, Any]:
    """가드가 있으면 요청 기록/증거를 붙인 결과를, 없으면 결과를 그대로 돌려준다."""
    if guard is None:
        return result
    return guard.finalize(result, success=success, result_message=result_message)


def navigation_timeout_sec(guard: MapGoalNavigationGuard | None, default: float) -> float:
    """지도 목표 요청이면 남은 요청 예산 안으로 제한한 대기 시간(초)."""
    return float(default) if guard is None else guard.timeout_sec(default)


def apply_task_outcome(
    node: Any, scope: MapGoalScope, skill_results: Any, result_msg: str
) -> dict[str, Any] | None:
    """planner 실행이 끝난 뒤 지도 목표 기록을 확정한다.

    실제 ``navigate_to`` 결과에 붙은 ``map_goal_request`` 증거만 도착 근거로
    인정한다. 증거가 없으면(산문 응답, 빈 계획, 계획 거부) 도착으로 확정하지
    않고 실패로 남긴다. 단, 안전 prelude 결과가 미확정이면(실제 팔/베이스가
    움직였을 수 있음) 실패로 닫지 않고 ``unknown`` 으로 lease 를 유지한다.
    """
    tracker = get_map_goal_tracker(node)
    existing = tracker.get(scope.request_id)
    if existing is not None and existing.get("status") in TERMINAL_STATUSES:
        return existing

    evidence: dict[str, Any] | None = None
    for result in skill_results or []:
        if str(getattr(result, "skill_name", "") or "") != "navigate_to":
            continue
        data = getattr(result, "result_data", None)
        if not isinstance(data, dict):
            continue
        proof = data.get("map_goal_request")
        if isinstance(proof, dict) and str(proof.get("request_id") or "") == scope.request_id:
            evidence = proof
    if evidence is not None:
        applied = tracker.apply_evidence(evidence)
        if applied is not None:
            return applied
    if scope.prep_hold():
        # 준비 동작이 실제로 실행됐을 수 있고 정지/no-effect 가 확인되지 않았다.
        # 도착 증거가 없더라도 failed 로 닫지 않고 결과 미확정으로 lease 를 유지한다.
        return tracker.transition(
            scope.request_id,
            STATUS_UNKNOWN,
            message="주행 전 안전 준비 동작의 결과를 확인하지 못했습니다.",
            reason="prep_outcome_unknown",
        )
    if scope.dispatch_attempted():
        return tracker.transition(
            scope.request_id,
            STATUS_UNKNOWN,
            message=str(result_msg or "") or "주행 요청 후 결과를 확인하지 못했습니다.",
            reason="navigation_result_unknown",
            cancel_confirmed=False,
        )
    return tracker.transition(
        scope.request_id,
        STATUS_FAILED,
        message=str(result_msg or "") or "지도 목표 이동 결과를 확인하지 못했습니다.",
        reason="no_navigation_evidence",
    )
