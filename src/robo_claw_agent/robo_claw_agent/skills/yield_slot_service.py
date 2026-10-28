"""자리 양보 협상 — 수신측 처리 (도메인 규칙을 노드 상태에 연결).

도메인 규칙(``domain.yield_slot``)은 순수 계산만 하고, 이 모듈이 실제 노드에서
필요한 정보(현재 위치, 실행 중 작업, 배터리, 비켜줄 후보지)를 모아 판정한다.
노드 의존 부분은 ``YieldServerContext`` 로 주입받아 단위 테스트가 가능하다.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from robo_claw_agent.domain.yield_slot import (
    ACCEPTED,
    BUSY,
    DENIED,
    Pose2D,
    YieldDecision,
    YieldReceiverState,
    YieldRequest,
    apply_execution_gate,
    evaluate_yield_request,
    select_yield_spot,
    validate_yield_request,
)

logger = logging.getLogger(__name__)

# 양보 이동에 허용하는 최대 시간(초). 서비스 응답 상한보다 짧아야 한다.
YIELD_MOVE_TIMEOUT_SEC = 25.0
# 조작 중인 로봇을 판정하는 리소스 이름. 이 중 하나라도 점유 중이면 조작 중이다.
MANIPULATION_RESOURCES = frozenset({"cloid_motion", "manipulation", "gripper"})
# 비켜줄 후보지 탐색 개수 상한 (지도 전역 스캔 비용 제한).
YIELD_CANDIDATE_LIMIT = 5
# 요청 목표에서 벗어나야 하는 최소 거리(m)와 후보지 개방도 하한(m).
YIELD_CLEAR_RADIUS_M = 1.5
YIELD_MIN_OPENNESS_M = 0.45
# 수신측 현재 위치에서 허용하는 최대 이동 거리(m).
YIELD_MAX_MOVE_DISTANCE_M = 4.0


@dataclass(frozen=True)
class YieldServerContext:
    """수신측 판정에 필요한 노드 상태 스냅샷 (지연 조회)."""

    agent_id: str
    pose_provider: Callable[[], Pose2D | None]
    busy_provider: Callable[[], bool]
    manipulation_provider: Callable[[], bool]
    battery_provider: Callable[[], float | None]
    candidates_provider: Callable[[], Iterable[Sequence[float]]]
    allow_yield: bool = True
    # 장소 이름을 **수신측 자기 맵 좌표**로 해석한다. 맵을 공유하지 않는 로봇
    # 사이의 협상은 이 해석기에 의존한다. 해석 실패는 None.
    place_resolver: Callable[[str], Pose2D | None] | None = None


def decide_yield(context: YieldServerContext, request: YieldRequest) -> YieldDecision:
    """요청을 판정한다. 이동은 수행하지 않는다.

    판정 순서: 파라미터 검증 → (장소면 자기 맵에서 좌표 해석) → 점유/거부 사유
    판정 → 실행 가능 게이트 → 비켜줄 자리 선정. 후보지 탐색은 실제로 수락 가능할
    때만 수행한다.

    장소 이름을 해석하지 못하면 추측하지 않고 ``unknown_place`` 로 거부한다.
    """
    if validate_yield_request(request):
        return YieldDecision(DENIED, "invalid_request")

    if not context.allow_yield:
        return YieldDecision(DENIED, "yield_disabled")

    if request.target is None:
        resolved = _resolve_place_target(context, request)
        if resolved is None:
            return YieldDecision(DENIED, "unknown_place")
        request = request.with_target(resolved)

    pose = context.pose_provider()
    state = YieldReceiverState(
        agent_id=context.agent_id,
        pose=pose,
        manipulation_active=context.manipulation_provider(),
        allow_yield=context.allow_yield,
        battery_percent=context.battery_provider(),
    )
    decision = evaluate_yield_request(request, state)
    if not decision.accepted:
        return decision

    decision = apply_execution_gate(decision, agent_busy=context.busy_provider())
    if not decision.accepted:
        return decision

    spot = select_yield_spot(
        context.candidates_provider(),
        receiver_pose=pose,
        requester_target=request.target,
        clear_radius_m=YIELD_CLEAR_RADIUS_M,
        min_openness_m=YIELD_MIN_OPENNESS_M,
        max_move_distance_m=YIELD_MAX_MOVE_DISTANCE_M,
    )
    if spot is None:
        return YieldDecision(
            DENIED, "no_safe_spot", distance_m=decision.distance_m, yield_spot=None
        )
    return YieldDecision(ACCEPTED, decision.reason, distance_m=decision.distance_m, yield_spot=spot)


def _resolve_place_target(context: YieldServerContext, request: YieldRequest) -> Pose2D | None:
    """요청의 장소 이름을 수신측 자기 맵 좌표로 해석한다."""
    place = request.place_name()
    if not place or context.place_resolver is None:
        return None
    try:
        resolved = context.place_resolver(place)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Yield] 장소 해석 실패(%s): %s", place, exc)
        return None
    if resolved is None or not resolved.is_finite():
        return None
    return resolved


def decision_message(decision: YieldDecision, *, requester_id: str, moved: bool) -> str:
    """서비스 응답·로그용 사람이 읽을 수 있는 요약."""
    if decision.decision == ACCEPTED:
        spot = decision.yield_spot
        where = f"({spot.x:.2f}, {spot.y:.2f})" if spot is not None else "알 수 없는 위치"
        if moved:
            return f"{requester_id}의 요청으로 {where}로 비켜섰습니다."
        return f"{requester_id}에게 {where}로 비켜주기로 했습니다."
    if decision.decision == BUSY:
        return (
            f"지금은 양보할 수 없습니다({decision.reason}). "
            f"{decision.retry_after_sec:.0f}초 후 다시 요청하세요."
        )
    if decision.reason == "not_blocking":
        return "요청한 지점을 점유하고 있지 않습니다."
    return f"양보를 거부했습니다({decision.reason})."


def fill_yield_response(response: Any, decision: YieldDecision, *, message: str) -> Any:
    """판정 결과를 YieldSlot.Response 로 옮긴다."""
    response.success = True
    response.decision = decision.decision
    response.reason = decision.reason
    response.distance_m = decision.distance_m if decision.distance_m is not None else -1.0
    response.retry_after_sec = float(decision.retry_after_sec)
    spot = decision.yield_spot
    response.has_yield_spot = spot is not None
    response.yield_spot_x = spot.x if spot is not None else 0.0
    response.yield_spot_y = spot.y if spot is not None else 0.0
    response.message = message
    return response


# --- 노드 상태 수집 (ROS 어댑터) ---------------------------------------------


def _current_map_pose(node: Any) -> Pose2D | None:
    """TF2 map→base_link 로 현재 위치를 조회한다. 실패하면 None."""
    tf_buffer = getattr(node, "_tf_buffer", None)
    if tf_buffer is None:
        return None
    map_frame = getattr(node, "_map_frame", "map")
    base_frame = getattr(node, "_robot_base_frame", "base_link")
    try:
        import rclpy.time

        from robo_claw_agent.geo_utils import yaw_from_quaternion

        tf = tf_buffer.lookup_transform(map_frame, base_frame, rclpy.time.Time())
        t = tf.transform.translation
        pose = Pose2D(t.x, t.y, yaw_from_quaternion(tf.transform.rotation))
        return pose if pose.is_finite() else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Yield] 현재 위치 조회 실패: %s", exc)
        return None


def _task_queue_idle(node: Any) -> bool:
    """태스크 큐가 비어 있는지. 큐가 없으면 실행 중 작업 유무로 대체 판정한다."""
    queue = getattr(node, "_task_queue", None)
    if queue is not None and hasattr(queue, "is_idle"):
        return bool(queue.is_idle())
    return not _active_executions(node)


def _background_loop_active() -> bool:
    """자율 행동/자율협동 백그라운드 루프가 돌고 있는지.

    이 루프들은 태스크 큐를 거치지 않고 직접 이동하므로, 큐가 비어 있어도
    양보 이동을 시작하면 base_control 이 겹친다.
    """
    try:
        from robo_claw_agent.skills.autonomous_skill.globals import is_autonomous_active

        if is_autonomous_active():
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from robo_claw_agent.skills.cooperation_skill.globals import is_cooperation_active

        if is_cooperation_active():
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _agent_busy(node: Any) -> bool:
    """지금 양보 이동을 시작할 수 없는 상태인지."""
    return (not _task_queue_idle(node)) or _background_loop_active()


def _active_executions(node: Any) -> list[dict[str, Any]]:
    skills = getattr(node, "_skills", None)
    if skills is None or not hasattr(skills, "active_executions"):
        return []
    try:
        return list(skills.active_executions())
    except Exception:  # noqa: BLE001
        return []


def _manipulation_active(node: Any) -> bool:
    """조작/파지 중인지. 점유 리소스 이름으로 판정한다."""
    skills = getattr(node, "_skills", None)
    for item in _active_executions(node):
        name = str(item.get("skill_name") or "")
        skill = None
        if skills is not None and hasattr(skills, "get_skill"):
            try:
                skill = skills.get_skill(name)
            except Exception:  # noqa: BLE001
                skill = None
        resources = set(getattr(skill, "exclusive_resources", ()) or ())
        if resources & MANIPULATION_RESOURCES:
            return True
    return False


def _battery_percent(node: Any) -> float | None:
    """배터리 잔량(%). 조회할 수 없으면 None(판정에 사용하지 않음)."""
    for attr in ("_battery_percent", "_battery_level"):
        value = getattr(node, attr, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value) if math.isfinite(float(value)) else None
    return None


def _yield_candidates(node: Any, pose: Pose2D | None) -> list[tuple[float, float, float]]:
    """비켜줄 후보지 — 전역 맵에서 도달 가능하고 개방된 지점을 뽑는다."""
    if pose is None:
        return []
    map_msg = None
    subscribe = getattr(node, "_wait_for_message", None)
    try:
        from nav_msgs.msg import OccupancyGrid  # type: ignore[import]

        if subscribe is not None:
            map_msg = subscribe(OccupancyGrid, "/map", timeout_sec=2.0, use_transient_local=True)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Yield] /map 조회 실패: %s", exc)
        map_msg = None
    if map_msg is None:
        return []
    try:
        from robo_claw_agent.skills.autonomous_skill.place_discovery import (
            find_reachable_places,
        )

        return find_reachable_places(
            map_msg,
            {"x": pose.x, "y": pose.y, "yaw": pose.yaw, "frame": "map"},
            min_openness_m=YIELD_MIN_OPENNESS_M,
            min_separation_m=1.0,
            max_places=YIELD_CANDIDATE_LIMIT,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Yield] 양보 후보지 계산 실패: %s", exc)
        return []


def resolve_place_in_local_map(node: Any, place: str) -> Pose2D | None:
    """장소 이름을 이 로봇의 시멘틱 맵 좌표로 해석한다.

    맵을 공유하지 않는 로봇끼리의 양보 협상은 이 해석 결과로만 성립한다.
    """
    memory = getattr(node, "_memory", None)
    if memory is None or not str(place or "").strip():
        return None
    try:
        from robo_claw_agent.skills.navigation_skill.core import _resolve_target_coordinates

        info = _resolve_target_coordinates(memory, str(place).strip())
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Yield] 장소 해석 실패(%s): %s", place, exc)
        return None
    if not isinstance(info, dict):
        return None
    position = info.get("position")
    if not isinstance(position, dict):
        return None
    try:
        pose = Pose2D(float(position["x"]), float(position["y"]), 0.0)
    except (KeyError, TypeError, ValueError):
        return None
    return pose if pose.is_finite() else None


def build_server_context(node: Any, *, agent_id: str, allow_yield: bool) -> YieldServerContext:
    """노드에서 판정용 컨텍스트를 만든다."""
    return YieldServerContext(
        agent_id=agent_id,
        pose_provider=lambda: _current_map_pose(node),
        busy_provider=lambda: _agent_busy(node),
        manipulation_provider=lambda: _manipulation_active(node),
        battery_provider=lambda: _battery_percent(node),
        candidates_provider=lambda: _yield_candidates(node, _current_map_pose(node)),
        allow_yield=allow_yield,
        place_resolver=lambda place: resolve_place_in_local_map(node, place),
    )


def execute_yield_move(
    node: Any, spot: Pose2D, *, timeout_sec: float = YIELD_MOVE_TIMEOUT_SEC
) -> bool:
    """선정된 자리로 실제 이동한다. 태스크 큐를 통해 실행해 다른 작업과 겹치지 않게 한다."""
    skills = getattr(node, "_skills", None)
    if skills is None:
        return False
    params = {"x": spot.x, "y": spot.y, "frame_id": "map"}

    submit = getattr(node, "_submit_task_to_queue", None)
    if submit is None:
        result = skills.execute("navigate_to", params, timeout_sec=timeout_sec)
        return bool(getattr(result, "success", False))

    done = threading.Event()
    outcome: dict[str, bool] = {}

    def _run() -> None:
        try:
            result = skills.execute("navigate_to", params, timeout_sec=timeout_sec)
            outcome["success"] = bool(getattr(result, "success", False))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Yield] 양보 이동 실패: %s", exc)
            outcome["success"] = False
        finally:
            done.set()

    task_id = submit(instruction="[yield] 자리 양보 이동", execute_fn=_run)
    if task_id is None:
        return False
    done.wait(timeout=timeout_sec + 10.0)
    return bool(outcome.get("success", False))
