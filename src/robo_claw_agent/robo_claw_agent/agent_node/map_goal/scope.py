"""지도 목표 요청 스코프(ContextVar)와 요청 단위 상태.

지도 목표 제약을 노드 전역에 두면 동시에 처리되는 일반 요청까지 영향을 받는다.
그래서 요청 하나를 나타내는 불변 컨텍스트 + 요청 단위 가변 상태를 ContextVar 에
올리고, 실행 경로(planner/skill)가 그 스코프를 읽는다. ``_run_blocking`` 처럼
스레드로 넘어가는 경계는 ``run_in_context`` 로 컨텍스트를 명시적으로 복사한다.
"""

import contextvars
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from ...domain.map_goal_guard import (
    current_trusted_prelude,
    is_physical_skill,
    reset_map_goal_guard,
    set_map_goal_guard,
)
from ..nav_safety import CLOID_NAV_SAFETY_SKILLS, STRETCH_NAV_SAFETY_SKILLS
from .contract import (
    NAV_BUDGET_SEC,
    MapGoalContext,
    MapGoalError,
    canonical_message_matches,
    is_expired,
    parse_context_json,
)
from .tracker import get_map_goal_tracker

_SCOPE: contextvars.ContextVar["MapGoalScope | None"] = contextvars.ContextVar(
    "robo_claw_map_goal_scope", default=None
)

# 안전 prelude 실패 결과가 "아무 효과도 없었다"고 명시한 상태값.
# 이 값이 없으면(타임아웃/예외/모호한 실패) 실제 팔/베이스가 움직였을 수 있으므로
# 요청을 결과 미확정(unknown)으로 보수적으로 유지한다.
_NO_EFFECT_STATUSES: frozenset[str] = frozenset(
    {"skipped", "not_executed", "no_effect", "failed_to_start"}
)


def _confirms_no_effect(result_data: Any) -> bool:
    """prelude 실패 결과가 실제 효과 없음(no-op)을 명시하는지 판정한다."""
    if not isinstance(result_data, dict):
        return False
    if result_data.get("executed") is False:
        return True
    if result_data.get("mocked") is True:
        return True
    return str(result_data.get("status") or "") in _NO_EFFECT_STATUSES


# 엔진이 삽입할 수 있는 주행 전 안전 prelude(설정된 로봇 프로필의 것만 신뢰).
CONFIGURED_PRELUDE_SKILLS: frozenset[str] = CLOID_NAV_SAFETY_SKILLS | STRETCH_NAV_SAFETY_SKILLS

# 지도 목표 요청에서 허용되는 이동 스킬(좌표/프레임/지도 검증은 주행 가드 담당).
NAVIGATION_SKILL = "navigate_to"

# 요청 수락/거부 사유(영문 코드). 로그와 테스트에서 그대로 쓴다.
REJECT_MALFORMED = "map_goal_context_invalid"
REJECT_ROBOT_MISMATCH = "map_goal_robot_mismatch"
REJECT_FRAME_MISMATCH = "map_goal_frame_mismatch"
REJECT_MESSAGE_MISMATCH = "map_goal_message_mismatch"
REJECT_EXPIRED = "map_goal_selection_expired"
REJECT_SAME_ID_DIFFERENT_PAYLOAD = "map_goal_request_conflict"
REJECT_ACTIVE_REQUEST = "map_goal_request_active"
REJECT_PREVIOUS_UNCONFIRMED = "map_goal_previous_unconfirmed"
REJECT_DUPLICATE_REQUEST = "map_goal_duplicate_request"

_REJECT_MESSAGES = {
    REJECT_ROBOT_MISMATCH: "지도 목표의 로봇 ID가 이 에이전트와 일치하지 않습니다.",
    REJECT_FRAME_MISMATCH: "지도 목표의 기준 프레임이 이 로봇의 지도 프레임과 다릅니다.",
    REJECT_MESSAGE_MISMATCH: (
        "지도 목표 문장이 컨텍스트의 좌표/프레임과 일치하지 않습니다. 지도에서 다시 선택해 주세요."
    ),
    REJECT_EXPIRED: "지도에서 선택한 목표의 유효 시간이 지났습니다. 지도에서 다시 선택해 주세요.",
    REJECT_SAME_ID_DIFFERENT_PAYLOAD: "같은 요청 ID로 다른 목표가 이미 접수되었습니다.",
    REJECT_ACTIVE_REQUEST: "다른 지도 목표 요청이 아직 진행 중입니다.",
    REJECT_PREVIOUS_UNCONFIRMED: (
        "이전 지도 목표 이동의 결과가 확정되지 않았습니다. 실제 도착/종료를 확인한 뒤 다시 시도해 주세요."
    ),
    REJECT_DUPLICATE_REQUEST: (
        "이미 접수된 지도 목표 요청입니다. 같은 요청은 다시 실행하지 않습니다."
    ),
}


@dataclass
class MapGoalScope:
    """한 지도 목표 요청의 실행 상태(요청 단위, 스레드 공유)."""

    context: MapGoalContext
    navigation_frame: str
    deadline_monotonic: float = 0.0
    dispatch_count: int = 0
    execution_started: bool = False
    # 엔진이 삽입한 안전 prelude 실행 추적(준비 동작도 요청의 실제 효과다).
    prep_attempts: int = 0
    prep_started: bool = False
    prep_succeeded: bool = False
    prep_uncertain: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if self.deadline_monotonic <= 0.0:
            self.deadline_monotonic = time.monotonic() + NAV_BUDGET_SEC

    @property
    def request_id(self) -> str:
        return self.context.request_id

    def remaining_budget_sec(self) -> float:
        """요청 전체 예산(180초) 중 남은 시간."""
        return max(0.0, self.deadline_monotonic - time.monotonic())

    def mark_prep_started(self) -> None:
        """엔진이 삽입한 안전 준비 동작 실행을 시작했음을 기록한다."""
        with self._lock:
            self.prep_attempts += 1
            self.prep_started = True

    def mark_prelude_result(self, *, success: bool, result_data: Any = None) -> bool:
        """trusted prelude 결과를 기록하고 '결과 미확정 hold' 여부를 돌려준다.

        성공하면 준비 완료다. 실패는 실제 효과 없음이 명시된 경우에만 확정 실패로
        보고, 그 밖(타임아웃/예외/모호한 실패)은 결과 미확정으로 유지한다. 한 번
        미확정이 되면 이후 성공으로도 풀리지 않는다(보수적).
        """
        with self._lock:
            if success:
                self.prep_succeeded = True
                return self.prep_uncertain
            if not _confirms_no_effect(result_data):
                self.prep_uncertain = True
            return self.prep_uncertain

    def prep_hold(self) -> bool:
        """준비 동작 결과가 확정되지 않아 추가 물리 실행을 막아야 하는지."""
        with self._lock:
            return self.prep_uncertain

    def dispatch_attempted(self) -> bool:
        """실제 주행을 한 번이라도 시도했는지."""
        with self._lock:
            return self.dispatch_count > 0

    def denied_reason(
        self,
        skill_name: Any,
        *,
        risk_level: Any = "action",
        side_effects: Any = (),
        exclusive_resources: Any = (),
    ) -> str:
        """저수준(SkillManager) 실행 경계에서 이 스킬을 허용할지 판정한다.

        - 준비 동작 결과가 미확정이면 이동/prelude 를 포함한 물리 실행을 모두 거부한다.
        - 검증된 이동(``navigate_to``)은 주행 가드가 좌표/프레임/지도를 재검증한다.
        - 엔진이 삽입한 설정된 안전 prelude 만 trusted 컨텍스트에서 허용한다.
        - 등록된 읽기 전용(``risk_level="read"``)이면서 비물리인 도구만 허용한다.
          비물리라도 쓰기/action 도구(원격 위임 포함)는 거부한다.
        """
        name = str(skill_name or "")
        physical = is_physical_skill(
            name, side_effects=side_effects, exclusive_resources=exclusive_resources
        )
        if self.prep_hold() and (
            physical or name == NAVIGATION_SKILL or name in CONFIGURED_PRELUDE_SKILLS
        ):
            return (
                "주행 전 안전 준비 동작의 결과가 확정되지 않아 추가 물리 동작을 실행하지 않습니다. "
                "실제 정지/효과 여부를 확인해야 합니다."
            )
        if name == NAVIGATION_SKILL:
            return ""
        if name in CONFIGURED_PRELUDE_SKILLS and current_trusted_prelude() == name:
            return ""
        if physical:
            if self.dispatch_attempted():
                return (
                    "이미 지도 목표 주행을 시도했습니다. 결과가 확정되지 않아 추가 물리 동작을 "
                    "실행하지 않습니다."
                )
            return f"지도 목표 요청에서는 요청하지 않은 물리 동작을 실행할 수 없습니다: {name}"
        if str(risk_level) == "read":
            return ""
        return (
            "지도 목표 요청에서는 읽기 전용 도구 외의 동작을 실행할 수 없습니다: "
            f"{name} (다른 로봇 이동/위임 포함)"
        )

    def claim_dispatch(self) -> tuple[bool, str]:
        """실제 이동 1회를 예약한다. 두 번째 호출은 거부한다."""
        with self._lock:
            if self.dispatch_count > 0:
                return False, "지도 목표 이동은 요청당 한 번만 실행할 수 있습니다."
            self.dispatch_count += 1
            return True, ""

    def mark_execution_started(self) -> None:
        with self._lock:
            self.execution_started = True


def current_map_goal_scope() -> MapGoalScope | None:
    """현재 실행 스코프의 지도 목표 요청(없으면 None)."""
    return _SCOPE.get()


@contextmanager
def map_goal_scope(scope: MapGoalScope):
    """요청 스코프를 설정하고, 예외가 나도 반드시 원래 컨텍스트로 되돌린다.

    저수준 실행 경계(SkillManager)가 같은 요청을 볼 수 있도록 ROS 비의존
    ContextVar 에도 같은 스코프를 등록한다.
    """
    token = _SCOPE.set(scope)
    guard_token = set_map_goal_guard(scope)
    try:
        yield scope
    finally:
        reset_map_goal_guard(guard_token)
        _SCOPE.reset(token)


def run_in_context(func: Any, *args: Any, **kwargs: Any) -> Any:
    """현재 컨텍스트를 복사해 다른 스레드/실행 경계에서 함수를 실행한다."""
    return contextvars.copy_context().run(func, *args, **kwargs)


def _normalize_identity(value: Any) -> str:
    return str(value or "").strip().strip("/")


def reject_reason_message(reason: str) -> str:
    """거부 사유 코드를 사용자에게 보여줄 문장으로 바꾼다."""
    return _REJECT_MESSAGES.get(reason, f"지도 목표 요청을 실행할 수 없습니다: {reason}")


def activate_map_goal_scope(
    node: Any, goal_handle: Any, instruction: str
) -> tuple[MapGoalScope | None, str]:
    """ExecuteTask 요청의 context_json 을 지도 목표 스코프로 승격한다.

    반환값: ``(scope, error)``. 지도 목표가 아닌 일반 요청이면 ``(None, "")``.
    지도 목표로 보이는데 검증에 실패하면 ``(None, error)`` 이며, 호출부는 어떤
    스킬도 실행하지 않고 요청을 거부해야 한다(자유 텍스트 폴백 금지).
    """
    request = getattr(goal_handle, "request", None)
    raw = getattr(request, "context_json", "") or ""
    try:
        context = parse_context_json(raw)
    except MapGoalError as exc:
        return None, f"{reject_reason_message(REJECT_MALFORMED)} ({exc})"
    if context is None:
        return None, ""

    agent_id = _normalize_identity(getattr(node, "_agent_id", ""))
    if not agent_id or _normalize_identity(context.robot_id) != agent_id:
        return None, reject_reason_message(REJECT_ROBOT_MISMATCH)

    navigation_frame = str(getattr(node, "_map_frame", "map") or "map").strip()
    if not navigation_frame or context.frame_id != navigation_frame:
        return None, reject_reason_message(REJECT_FRAME_MISMATCH)

    if not canonical_message_matches(context, instruction):
        return None, reject_reason_message(REJECT_MESSAGE_MISMATCH)

    if is_expired(context):
        return None, reject_reason_message(REJECT_EXPIRED)

    tracker = get_map_goal_tracker(node)
    if tracker.get(context.request_id) is not None:
        # 같은 요청 ID 의 재전송은 실행하지 않는다(기존 기록이 권위 있는 결과다).
        reason = (
            REJECT_DUPLICATE_REQUEST
            if tracker.matches_payload(context)
            else REJECT_SAME_ID_DIFFERENT_PAYLOAD
        )
        return None, reject_reason_message(reason)
    _, admit_error = tracker.admit(context)
    if admit_error:
        reason = {
            "same_request_id_different_payload": REJECT_SAME_ID_DIFFERENT_PAYLOAD,
            "active_request_in_progress": REJECT_ACTIVE_REQUEST,
            "previous_request_unconfirmed": REJECT_PREVIOUS_UNCONFIRMED,
        }.get(admit_error, admit_error)
        return None, reject_reason_message(reason)

    scope = MapGoalScope(
        context=context,
        navigation_frame=navigation_frame,
        deadline_monotonic=time.monotonic() + NAV_BUDGET_SEC,
    )
    return scope, ""
