"""자리 양보 협상 도메인 규칙 (ROS 의존 없음).

두 로봇이 같은 지점을 두고 경쟁할 때 "누가 비켜줄지"를 결정적으로 판정한다.
네트워크·LLM 판단 없이 순수 계산만 수행하므로 단독 테스트가 가능하다.

설계 원칙:

- **fail-closed**: 위치를 모르거나, 비켜줄 안전한 자리가 없으면 양보하지 않고
  거부를 반환한다. 추측으로 이동하지 않는다.
- **반대칭 중재**: 두 로봇이 동시에 요청해도 정확히 한 대만 양보한다. 로봇 id
  사전순 비교를 쓰므로 양쪽에서 본 판정이 서로 반대가 되어 교착·진동이 없다.
- **조작 우선**: 파지/조작 중인 로봇은 절대 양보하지 않는다(물체 낙하 위험).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

# 판정 결과 코드 — 수신측 응답과 로그에 그대로 노출된다.
NOT_NEEDED = "not_needed"
ACCEPTED = "accepted"
BUSY = "busy"
DENIED = "denied"

# 중재 결과 코드
REQUESTER_WINS = "requester_wins"
RECEIVER_WINS = "receiver_wins"

DEFAULT_RADIUS_M = 0.8
MAX_RADIUS_M = 2.0
DEFAULT_TIMEOUT_SEC = 5.0
MAX_TIMEOUT_SEC = 10.0

# 양보 목적지 선정 기준
DEFAULT_CLEAR_RADIUS_M = 1.5
DEFAULT_MIN_OPENNESS_M = 0.45
DEFAULT_MAX_MOVE_DISTANCE_M = 4.0

# 배터리가 이 값 미만이면 충전 복귀 중으로 보고 양보를 거부한다.
MIN_BATTERY_PERCENT_FOR_YIELD = 10.0

# 조작이 끝날 때까지의 재시도 안내 간격(초)
_MANIPULATION_RETRY_SEC = 5.0
# 상대가 우선순위를 가질 때의 재시도 안내 간격(초)
_PRIORITY_RETRY_SEC = 3.0


@dataclass(frozen=True)
class Pose2D:
    """map frame 기준 2D 자세."""

    x: float
    y: float
    yaw: float = 0.0

    def is_finite(self) -> bool:
        return all(math.isfinite(v) for v in (self.x, self.y, self.yaw))

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "yaw": self.yaw}


@dataclass(frozen=True)
class YieldRequest:
    """자리를 요청하는 로봇이 보낸 협상 요청.

    목표는 **장소 이름**으로만 지정한다. 로봇마다 같은 맵을 쓰더라도 좌표가
    다를 수 있으므로 좌표를 로봇 사이에 넘기지 않는다. 수신자가 자기 시멘틱
    맵에서 자기 좌표로 해석한다(``with_target``).

    ``target`` 은 그 해석 결과를 담는 **내부 값**이다. 외부 요청에는 없다.

    ``requester_at_target`` 은 요청자가 **자기 맵 기준으로** 그 장소 반경 안에
    있는지 스스로 판정한 결과다. 이것도 좌표를 넘기지 않고 동시 점유를
    판정하기 위한 값이다. ``None`` 이면 판정하지 않은 것으로 본다.
    """

    requester_id: str
    target_place: str = ""
    radius_m: float = DEFAULT_RADIUS_M
    timeout_sec: float = DEFAULT_TIMEOUT_SEC
    requester_at_target: bool | None = None
    # 장소 이름을 수신측 맵에서 해석한 결과(내부용).
    target: Pose2D | None = None

    def place_name(self) -> str:
        return str(self.target_place or "").strip()

    def has_target(self) -> bool:
        return self.target is not None or bool(self.place_name())

    def with_target(self, target: Pose2D) -> YieldRequest:
        """장소 이름을 해석한 좌표로 채운 사본을 만든다."""
        return replace(self, target=target)


@dataclass(frozen=True)
class YieldReceiverState:
    """요청을 받은 로봇의 현재 상태 스냅샷."""

    agent_id: str
    pose: Pose2D | None = None
    manipulation_active: bool = False
    allow_yield: bool = True
    battery_percent: float | None = None


@dataclass(frozen=True)
class YieldDecision:
    """협상 판정 결과."""

    decision: str
    reason: str
    distance_m: float | None = None
    retry_after_sec: float = 0.0
    yield_spot: Pose2D | None = None

    @property
    def accepted(self) -> bool:
        return self.decision == ACCEPTED

    def as_dict(self) -> dict[str, Any]:
        """로그·서비스 응답 직렬화용. 0/None 값은 생략한다."""
        payload: dict[str, Any] = {"decision": self.decision, "reason": self.reason}
        if self.distance_m is not None:
            payload["distance_m"] = round(self.distance_m, 3)
        if self.retry_after_sec > 0.0:
            payload["retry_after_sec"] = self.retry_after_sec
        if self.yield_spot is not None:
            payload["yield_spot"] = self.yield_spot.as_dict()
        return payload


def apply_execution_gate(
    decision: YieldDecision, *, agent_busy: bool, retry_after_sec: float = _PRIORITY_RETRY_SEC
) -> YieldDecision:
    """수락했더라도 지금 양보 이동을 시작할 수 없으면 ``busy`` 로 낙춱인다.

    양보 이동은 ``base_control`` 을 잡는다. 다른 작업이 실행 중이거나 대기 중이면
    태스크 큐가 실행 중 태스크를 선점할 수 없으므로 이동을 시작할 수 없다.
    그런 경우 수락을 보내면 요청자는 곧 이동할 것이라고 믿고 재시도하다 실패한다.
    실행할 수 없으면 목적지를 약속하지 않고 재시도만 안내한다.
    """
    if not decision.accepted or not agent_busy:
        return decision
    return YieldDecision(
        BUSY,
        "agent_busy",
        distance_m=decision.distance_m,
        retry_after_sec=retry_after_sec,
        yield_spot=None,
    )


def _finite_float(value: Any) -> float | None:
    """숫자로 해석 가능하고 유한한 값만 float 로 반환한다."""
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def validate_yield_request(
    request: YieldRequest, *, max_timeout_sec: float = MAX_TIMEOUT_SEC
) -> str:
    """요청 파라미터를 검증한다. 통과하면 빈 문자열, 아니면 사유 메시지."""
    if not str(request.requester_id or "").strip():
        return "requester_id가 필요합니다."
    if not request.place_name():
        return "장소 이름(target_place)이 필요합니다."
    if request.target is not None and not request.target.is_finite():
        return "해석된 장소 좌표가 유효하지 않습니다."
    radius = _finite_float(request.radius_m)
    if radius is None or radius <= 0.0 or radius > MAX_RADIUS_M:
        return f"radius_m은 0보다 크고 {MAX_RADIUS_M} 이하여야 합니다."
    timeout = _finite_float(request.timeout_sec)
    if timeout is None or timeout <= 0.0 or timeout > max_timeout_sec:
        return f"timeout_sec은 0보다 크고 {max_timeout_sec} 이하여야 합니다."
    return ""


def arbitration_outcome(requester_id: str, receiver_id: str) -> str:
    """동시 요청 시 누가 우선인지 판정한다.

    로봇 id 사전순 비교는 반대칭이므로, 두 로봇이 각자 상대에게 판정해도
    항상 한쪽만 '요청자 우선'이 된다. 같은 id(자기 요청)는 수신측 우선이다.
    """
    requester = str(requester_id or "").strip()
    receiver = str(receiver_id or "").strip()
    if not requester or not receiver or requester == receiver:
        return RECEIVER_WINS
    return REQUESTER_WINS if requester < receiver else RECEIVER_WINS


def evaluate_yield_request(request: YieldRequest, state: YieldReceiverState) -> YieldDecision:
    """내가 요청자의 목표 지점을 막고 있는지, 그리고 양보 가능한지 판정한다.

    ``plan_yield`` 와 달리 목적지 후보를 보지 않는다. 판정 순서는
    "요청이 무의미한가" → "거부 사유가 있는가" → "지금은 불가한가" → 수락.

    핵심 규칙: **그 자리를 점유한 쪽이 비켜준다.** 요청자도 같은 반경 안에
    있는 동시 점유 상황에서만 id 기반 중재를 쓴다(반대칭이므로 한 대만 양보).
    """
    if not state.allow_yield:
        return YieldDecision(DENIED, "yield_disabled")
    if state.pose is None or not state.pose.is_finite():
        return YieldDecision(DENIED, "pose_unknown")
    if request.target is None or not request.target.is_finite():
        # 장소 이름은 호출자(어댑터)가 수신측 맵에서 해석한 뒤 넘긴다.
        return YieldDecision(DENIED, "target_unknown")

    radius = _finite_float(request.radius_m) or DEFAULT_RADIUS_M
    target_x, target_y = request.target.x, request.target.y
    distance = math.dist((state.pose.x, state.pose.y), (target_x, target_y))
    if distance > radius:
        return YieldDecision(NOT_NEEDED, "not_blocking", distance_m=distance)

    if state.manipulation_active:
        return YieldDecision(
            BUSY,
            "manipulation_in_progress",
            distance_m=distance,
            retry_after_sec=_MANIPULATION_RETRY_SEC,
        )

    battery = _finite_float(state.battery_percent)
    if battery is not None and battery < MIN_BATTERY_PERCENT_FOR_YIELD:
        return YieldDecision(DENIED, "battery_critical", distance_m=distance)

    # 동시 점유 판정은 요청자가 자기 맵에서 판정한 불리언만 쓴다. 좌표를 로봇
    # 사이에 넘기지 않으므로 맵 공유가 필요 없고, 판정하지 않았으면 보수적으로
    # 동시 점유로 본다.
    contested = (
        bool(request.requester_at_target)
        if request.requester_at_target is not None
        else True
    )

    if contested:
        if arbitration_outcome(request.requester_id, state.agent_id) == RECEIVER_WINS:
            return YieldDecision(
                BUSY,
                "requester_lower_priority",
                distance_m=distance,
                retry_after_sec=_PRIORITY_RETRY_SEC,
            )

    return YieldDecision(ACCEPTED, "yield_prepared", distance_m=distance)


def select_yield_spot(
    candidates: Iterable[Sequence[float]],
    *,
    receiver_pose: Pose2D,
    requester_target: Pose2D,
    clear_radius_m: float = DEFAULT_CLEAR_RADIUS_M,
    min_openness_m: float = DEFAULT_MIN_OPENNESS_M,
    max_move_distance_m: float = DEFAULT_MAX_MOVE_DISTANCE_M,
) -> Pose2D | None:
    """비켜줄 자리를 고른다.

    ``candidates`` 는 ``(x, y, openness_m)`` 튜플 목록이다
    (``place_discovery.find_reachable_places`` 반환 형식과 동일).

    조건: 요청자 목표에서 ``clear_radius_m`` 이상, 개방도 ``min_openness_m`` 이상,
    수신측 현재 위치에서 ``max_move_distance_m`` 이내. 유효 후보 중 **가장 가까운**
    곳을 고른다 — 최소 이동이 가장 안전하다. 유효 후보가 없으면 ``None``.
    """
    best: tuple[float, Pose2D] | None = None
    for candidate in candidates:
        if len(candidate) < 3:
            continue
        x = _finite_float(candidate[0])
        y = _finite_float(candidate[1])
        openness = _finite_float(candidate[2])
        if x is None or y is None or openness is None:
            continue
        if openness < min_openness_m:
            continue
        if math.dist((x, y), (requester_target.x, requester_target.y)) < clear_radius_m:
            continue
        move_distance = math.dist((x, y), (receiver_pose.x, receiver_pose.y))
        if move_distance > max_move_distance_m:
            continue
        if best is None or move_distance < best[0]:
            best = (move_distance, Pose2D(x, y, 0.0))
    return None if best is None else best[1]


def plan_yield(
    request: YieldRequest,
    state: YieldReceiverState,
    candidates: Iterable[Sequence[float]] = (),
    *,
    clear_radius_m: float = DEFAULT_CLEAR_RADIUS_M,
    min_openness_m: float = DEFAULT_MIN_OPENNESS_M,
    max_move_distance_m: float = DEFAULT_MAX_MOVE_DISTANCE_M,
) -> YieldDecision:
    """요청 검증 → 수락 판정 → 양보 목적지 선정을 한 번에 수행한다."""
    if validate_yield_request(request):
        return YieldDecision(DENIED, "invalid_request", yield_spot=None)

    decision = evaluate_yield_request(request, state)
    if not decision.accepted:
        return decision

    spot = select_yield_spot(
        candidates,
        receiver_pose=state.pose,
        requester_target=request.target,
        clear_radius_m=clear_radius_m,
        min_openness_m=min_openness_m,
        max_move_distance_m=max_move_distance_m,
    )
    if spot is None:
        return YieldDecision(
            DENIED, "no_safe_spot", distance_m=decision.distance_m, yield_spot=None
        )
    return YieldDecision(
        ACCEPTED,
        decision.reason,
        distance_m=decision.distance_m,
        yield_spot=spot,
    )
