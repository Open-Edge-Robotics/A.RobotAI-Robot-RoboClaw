import logging
import math
import re
import threading
from collections.abc import Callable
from typing import Any

from action_msgs.msg import GoalStatus
from builtin_interfaces.msg import Duration
from geometry_msgs.msg import PoseStamped
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup

from . import globals

logger = logging.getLogger(__name__)

_GOAL_STATUS_LABELS = {
    GoalStatus.STATUS_UNKNOWN: "UNKNOWN",
    GoalStatus.STATUS_ACCEPTED: "ACCEPTED",
    GoalStatus.STATUS_EXECUTING: "EXECUTING",
    GoalStatus.STATUS_CANCELING: "CANCELING",
    GoalStatus.STATUS_SUCCEEDED: "SUCCEEDED",
    GoalStatus.STATUS_CANCELED: "CANCELED",
    GoalStatus.STATUS_ABORTED: "ABORTED",
}


def _get_nav2_dependency_error() -> str | None:
    """Nav2 액션 의존성 누락 여부를 사전에 확인한다."""
    try:
        from nav2_msgs.action import NavigateToPose as _NavigateToPose  # noqa: F401

        return None
    except ModuleNotFoundError as e:
        if e.name == "nav2_msgs":
            return (
                "Nav2 이동 기능을 사용할 수 없습니다. "
                "ROS 패키지 'nav2_msgs'가 현재 런타임에 설치되어 있지 않습니다."
            )
        raise


def _ensure_action_clients(
    node: Any,
) -> tuple[ActionClient, ActionClient, ActionClient]:
    """현재 agent node에 Nav2 액션 클라이언트를 지연 초기화한다."""
    if (
        globals.NAV_CLIENT is not None
        and globals.SPIN_CLIENT is not None
        and globals.WP_CLIENT is not None
        and globals.CLIENT_NODE is node
    ):
        return globals.NAV_CLIENT, globals.SPIN_CLIENT, globals.WP_CLIENT

    from nav2_msgs.action import FollowWaypoints, NavigateToPose, Spin

    globals.CLIENT_GROUP = ReentrantCallbackGroup()
    globals.NAV_CLIENT = ActionClient(
        node,
        NavigateToPose,
        "/navigate_to_pose",
        callback_group=globals.CLIENT_GROUP,
    )
    globals.SPIN_CLIENT = ActionClient(
        node,
        Spin,
        "/spin",
        callback_group=globals.CLIENT_GROUP,
    )
    globals.WP_CLIENT = ActionClient(
        node,
        FollowWaypoints,
        "/follow_waypoints",
        callback_group=globals.CLIENT_GROUP,
    )
    globals.CLIENT_NODE = node
    return globals.NAV_CLIENT, globals.SPIN_CLIENT, globals.WP_CLIENT


def _wait_for_future(future: Any, timeout_sec: float, label: str) -> tuple[bool, Any]:
    """executor가 이미 spin 중이므로 future 완료만 이벤트로 대기한다."""
    done = threading.Event()
    future.add_done_callback(lambda _: done.set())
    if not done.wait(timeout=timeout_sec):
        logger.error("%s timed out (timeout=%.1fs)", label, timeout_sec)
        return False, None

    try:
        return True, future.result()
    except Exception as e:
        logger.error("Error obtaining %s result: %s", label, e)
        return False, None


class GoalRegistry:
    """Thread-safe registry for active navigation goals with generation tokens."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token_counter = 0
        self._active_token: int | None = None
        self._active_handle: Any | None = None
        self._active_kind: str = ""
        self._active_label: str = ""

    def register_goal(self, handle: Any, kind: str, label: str = "") -> int:
        with self._lock:
            self._token_counter += 1
            token = self._token_counter
            self._active_token = token
            self._active_handle = handle
            self._active_kind = kind
            self._active_label = label
            # Keep legacy globals updated for backward compatibility
            globals.ACTIVE_GOAL_HANDLE = handle
            globals.ACTIVE_GOAL_KIND = kind
            try:
                handle._goal_token = token
            except Exception:
                pass
            return token

    def get_active_token(self) -> int | None:
        with self._lock:
            return self._active_token

    def is_active(self, token: int) -> bool:
        with self._lock:
            return self._active_token == token

    def get_active_handle(self) -> Any | None:
        with self._lock:
            return self._active_handle

    def clear_goal(self, token: int | None = None, handle: Any | None = None) -> None:
        with self._lock:
            if token is not None and self._active_token != token:
                return
            if handle is not None and self._active_handle is not handle:
                return
            self._active_token = None
            self._active_handle = None
            self._active_kind = ""
            self._active_label = ""
            globals.ACTIVE_GOAL_HANDLE = None
            globals.ACTIVE_GOAL_KIND = ""

    def cancel_active_goal(self, token: int | None = None) -> tuple[bool, str]:
        with self._lock:
            if token is not None and self._active_token != token:
                return False, "지정된 목표 토큰이 이미 만료되었거나 활성 상태가 아닙니다."
            handle = self._active_handle
            kind = self._active_kind or "내비게이션"
            current_token = self._active_token

        if handle is None:
            return False, "취소할 활성 내비게이션 작업이 없습니다."

        try:
            cancel_future = handle.cancel_goal_async()
            ok, cancel_response = _wait_for_future(cancel_future, 5.0, f"{kind} 취소")
            if not ok or cancel_response is None:
                return False, f"{kind} 취소 응답을 받지 못했습니다."

            if getattr(cancel_response, "goals_canceling", None):
                self.clear_goal(token=current_token)
                return True, f"활성 {kind} 작업을 취소했습니다."
            return False, f"활성 {kind} 작업 취소가 거부되었습니다."
        except Exception as exc:
            return False, f"{kind} 취소 중 오류 발생: {exc}"


GOAL_REGISTRY = GoalRegistry()


def _set_active_goal(goal_handle: Any, kind: str, label: str = "") -> int:
    return GOAL_REGISTRY.register_goal(goal_handle, kind, label)


def _clear_active_goal(goal_handle: Any = None, token: int | None = None) -> None:
    GOAL_REGISTRY.clear_goal(token=token, handle=goal_handle)


def _cancel_active_goal(token: int | None = None) -> tuple[bool, str]:
    return GOAL_REGISTRY.cancel_active_goal(token=token)


# Nav2 기본 xy_goal_tolerance(0.25m)와 동일한 도착 판정 오차.
# 목표가 오차 안인데도 Nav2가 yaw 정렬/진행 검사 실패로 ABORTED를 반환하는
# 오탐(실측: 2026-09-11, 로봇이 0.19m까지 접근 후 status=6)을 막기 위해 사용한다.
DEFAULT_ARRIVAL_TOLERANCE_M = 0.25


def _get_arrival_tolerance_m(node: Any) -> float:
    """ROBOT_LIMITS의 navigation.arrival_tolerance_m을 읽고, 없으면 기본값을 반환한다."""
    limits = getattr(node, "_robot_limits_dict", None) if node is not None else None
    if isinstance(limits, dict):
        navigation = limits.get("navigation")
        if isinstance(navigation, dict):
            raw = navigation.get("arrival_tolerance_m")
            if raw is not None:
                try:
                    value = float(raw)
                    if value >= 0.0:
                        return value
                except (TypeError, ValueError):
                    logger.warning("Invalid arrival_tolerance_m value in ROBOT_LIMITS: %r", raw)
    return DEFAULT_ARRIVAL_TOLERANCE_M


def _pose_matches_frame(pose: Any, frame: str) -> bool:
    """pose의 좌표 frame이 목표 frame과 같은지 확인한다.

    서로 다른 frame(map vs odom)의 좌표를 거리 비교하면 잘못된 도착 판정을
    하므로, frame이 다르면 허용 오차 판정을 적용하지 않는다.
    """
    if not isinstance(pose, dict):
        return False
    return str(pose.get("frame") or "") == str(frame or "")


def _distance_to_target(pose: Any, x: float, y: float) -> float | None:
    """pose와 목표 좌표 사이의 평면 거리(m)를 계산한다(실패 시 None)."""
    if not isinstance(pose, dict):
        return None
    try:
        return math.hypot(float(pose["x"]) - float(x), float(pose["y"]) - float(y))
    except (KeyError, TypeError, ValueError):
        return None


def _send_navigation_goal(
    node: Any, x: float, y: float, frame_id: str, yaw: float = 0.0
) -> tuple[bool, str, Any]:
    try:
        x_val = float(x)
        y_val = float(y)
        yaw_val = float(yaw)
    except (TypeError, ValueError):
        return False, "내비게이션 목표 좌표/각도가 숫자가 아닙니다.", None

    nav_client, _, __ = _ensure_action_clients(node)

    if not nav_client.wait_for_server(timeout_sec=3.0):
        return False, "Nav2의 '/navigate_to_pose' 액션 서버를 찾을 수 없습니다.", None

    import math

    from nav2_msgs.action import NavigateToPose

    goal = NavigateToPose.Goal()
    goal.pose = PoseStamped()
    goal.pose.header.frame_id = frame_id
    # 타임스탬프 0은 Nav2 내부 TF 변환 시 최신(latest) 변환값을 사용하도록 유도함
    goal.pose.header.stamp.sec = 0
    goal.pose.header.stamp.nanosec = 0
    goal.pose.pose.position.x = x_val
    goal.pose.pose.position.y = y_val
    goal.pose.pose.orientation.z = math.sin(yaw_val / 2.0)
    goal.pose.pose.orientation.w = math.cos(yaw_val / 2.0)

    send_goal_future = nav_client.send_goal_async(goal)
    ok, goal_handle = _wait_for_future(send_goal_future, 5.0, "내비게이션 목표 전송")
    if not ok or goal_handle is None:
        return False, "Nav2 목표 전송 응답을 받지 못했습니다.", None

    if not goal_handle.accepted:
        return False, "Nav2가 이동 목표를 거절했습니다.", None

    _set_active_goal(goal_handle, "navigate_to_pose")
    return True, "accepted", goal_handle


def _spin_time_allowance_sec(angle_rad: float) -> float:
    """회전 각도에 비례한 Nav2 Spin 시간 한도를 계산한다.

    Nav2 Spin은 소각도 회전에서도 가속/감속과 yaw 허용오차 진입 대기 시간이 필요하다.
    실제 로봇에서 0.28rad 회전이 10초 제한으로 abort된 사례가 있어 최소 20초를 보장한다.
    """
    return max(20.0, abs(angle_rad) / 0.15 + 10.0)


def _send_spin_goal(
    node: Any, angle_rad: float, time_allowance_sec: float | None = None
) -> tuple[bool, str, Any]:
    _, spin_client, _wp2 = _ensure_action_clients(node)
    del _wp2

    if not spin_client.wait_for_server(timeout_sec=3.0):
        return False, "Nav2의 '/spin' 액션 서버를 찾을 수 없습니다.", None

    from nav2_msgs.action import Spin

    try:
        angle_val = float(angle_rad)
        if time_allowance_sec is None:
            time_allowance_sec = _spin_time_allowance_sec(angle_val)
        allowance_sec = int(math.ceil(float(time_allowance_sec)))
    except (TypeError, ValueError):
        return False, "회전 각도/시간 한도가 숫자가 아닙니다.", None

    goal = Spin.Goal()
    goal.target_yaw = angle_val
    goal.time_allowance = Duration(sec=allowance_sec)

    send_goal_future = spin_client.send_goal_async(goal)
    ok, goal_handle = _wait_for_future(send_goal_future, 5.0, "회전 목표 전송")
    if not ok or goal_handle is None:
        return False, "회전 목표 전송 응답을 받지 못했습니다.", None

    if not goal_handle.accepted:
        return False, "Nav2가 회전 목표를 거절했습니다.", None

    _set_active_goal(goal_handle, "spin")
    return True, "accepted", goal_handle


def _send_follow_waypoints_goal(node: Any, poses: list[PoseStamped]) -> tuple[bool, str, Any]:
    _nav2, _spin2, wp_client = _ensure_action_clients(node)
    del _nav2, _spin2

    if not wp_client.wait_for_server(timeout_sec=3.0):
        return False, "Nav2의 '/follow_waypoints' 액션 서버를 찾을 수 없습니다.", None

    from nav2_msgs.action import FollowWaypoints

    goal = FollowWaypoints.Goal()
    goal.poses = poses

    send_goal_future = wp_client.send_goal_async(goal)
    ok, goal_handle = _wait_for_future(send_goal_future, 5.0, "웨이포인트 목표 전송")
    if not ok or goal_handle is None:
        return False, "Nav2 웨이포인트 목표 전송 응답을 받지 못했습니다.", None

    if not goal_handle.accepted:
        return False, "Nav2가 웨이포인트 목표를 거절했습니다.", None

    _set_active_goal(goal_handle, "follow_waypoints")
    return True, "accepted", goal_handle


def _wait_for_goal_result(
    goal_handle: Any,
    timeout_sec: float,
    label: str,
    token: int | None = None,
) -> tuple[bool, str]:
    resolved_token = token if token is not None else getattr(goal_handle, "_goal_token", None)
    result_future = goal_handle.get_result_async()
    ok, result = _wait_for_future(result_future, timeout_sec, label)

    if not ok or result is None:
        try:
            status_code = goal_handle.status
            status_label = _GOAL_STATUS_LABELS.get(status_code, "UNKNOWN")
            logger.error(
                "%s timed out — goal status=%s(%s). Canceling goal...",
                label,
                status_code,
                status_label,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s timed out — failed to query goal status: %s", label, exc)
        _cancel_active_goal(token=resolved_token)
        return False, f"{label} 결과를 받지 못했습니다 (타임아웃)."

    _clear_active_goal(goal_handle=goal_handle, token=resolved_token)

    if result.status == GoalStatus.STATUS_SUCCEEDED:
        return True, "success"

    if result.status == GoalStatus.STATUS_CANCELED:
        return False, "취소되었습니다."

    _cancel_active_goal(token=resolved_token)
    status_label = _GOAL_STATUS_LABELS.get(result.status, "UNKNOWN_STATUS")
    return False, f"{label} 실패(status={result.status}/{status_label})"


def notify_goal_outcome(
    skill: Any,
    success: bool,
    result_message: str,
    *,
    label: str,
    success_message: str,
    cancel_message: str,
    fail_message: str | Callable[[str], str],
) -> None:
    """_wait_for_goal_result의 결과(success, result_message)를 받아 사용자 알림만 전송한다.

    성공/취소("취소되었습니다." 매직 스트링)/실패 3분기를 통일된 방식으로 처리한다.
    fail_message가 str이면 뒤에 result_message를 붙이고, callable이면
    fail_message(result_message)로 커스텀 포맷을 허용한다(예: face_direction의
    폴백 재시도 문구).
    """
    if success:
        skill.send_user_message(success_message)
        return
    if result_message == "취소되었습니다.":
        skill.send_user_message(cancel_message)
        return
    logger.error("%s failed: %s", label, result_message)
    if callable(fail_message):
        skill.send_user_message(fail_message(result_message))
    else:
        skill.send_user_message(f"{fail_message} {result_message}")


def _extract_coords_from_entry(text: str, meta: Any, normalized: str) -> dict[str, Any] | None:
    """RAG entry에서 좌표를 추출한다.

    메타데이터 좌표 > 순서쌍 파싱 > x/y 형식 파싱 순서로 시도한다.
    단어 매칭이 안 되면 None을 반환한다.
    """
    text_match = normalized.lower() in text.lower()
    meta_match = False
    if isinstance(meta, dict):
        for key in ("name", "target_name", "object_name", "place_name", "location_name"):
            if normalized.lower() in str(meta.get(key, "")).lower():
                meta_match = True
                break
    if not (text_match or meta_match):
        return None

    # 메타데이터에 좌표가 있는 경우 우선
    if isinstance(meta, dict) and "x" in meta and "y" in meta:
        try:
            return {
                "name": normalized,
                "position": {"x": float(meta["x"]), "y": float(meta["y"])},
            }
        except (ValueError, TypeError):
            pass

    # 순서쌍 좌표 형식 (0.0, -3.0)
    pair_match = re.search(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)", text)
    if pair_match:
        try:
            return {
                "name": normalized,
                "position": {
                    "x": float(pair_match.group(1)),
                    "y": float(pair_match.group(2)),
                },
            }
        except (ValueError, TypeError):
            pass

    # x/y 형식 (x: -1.5, y: -4.0)
    x_match = re.search(r"x\s*[:=]\s*(-?\d+(?:\.\d+)?)", text, re.IGNORECASE)
    y_match = re.search(r"y\s*[:=]\s*(-?\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if x_match and y_match:
        try:
            return {
                "name": normalized,
                "position": {
                    "x": float(x_match.group(1)),
                    "y": float(y_match.group(1)),
                },
            }
        except (ValueError, TypeError):
            pass

    return None


def _resolve_target_coordinates(memory: Any, target_name: str) -> dict[str, Any] | None:
    """번호 목적지 별칭을 포함해 저장된 장소 좌표를 조회한다.

    우선순위는 시맨틱 맵 → RAG 검색 → RAG 풀스캔이다. 자율 행동 중 의미가 붙은
    장소를 단순 좌표 지식보다 우선 사용하기 위해 시맨틱 맵을 먼저 본다.
    """
    normalized = str(target_name or "").strip()
    if not normalized:
        return None

    candidates = [normalized]
    compact = re.sub(r"\s+", "", normalized)
    if compact not in candidates:
        candidates.append(compact)

    match = re.fullmatch(r"(\d+)번?", compact)
    if match:
        index = match.group(1)
        for candidate in [
            index,
            f"{index}번",
            f"{index} 번",
            f"{index}번 위치",
            f"{index}번 장소",
        ]:
            if candidate not in candidates:
                candidates.append(candidate)

    # 1. 시맨틱 맵 조회 (O(1))
    for candidate in candidates:
        obj_info = memory.get_object_location(candidate)
        if obj_info:
            return obj_info

    # 2. RAG 지식 베이스 검색 시도
    if hasattr(memory, "search_knowledge"):
        try:
            results = memory.search_knowledge(normalized, top_k=5, score_threshold=0.35)
            for candidate in candidates:
                for res in results:
                    coords = _extract_coords_from_entry(
                        res.get("text", ""), res.get("metadata", {}), candidate
                    )
                    if coords:
                        return coords
        except Exception as e:
            logger.warning("Error occurred while looking up target coordinates via RAG: %s", e)

    # 3. RAG 전체 리스트 풀스캔 백업 (키워드 100% 매칭 보장)
    if hasattr(memory, "_vector_store") and hasattr(memory._vector_store, "list_entries"):
        try:
            all_entries = memory._vector_store.list_entries()
            for candidate in candidates:
                for entry in reversed(all_entries):
                    coords = _extract_coords_from_entry(
                        entry.get("text", ""), entry.get("metadata", {}), candidate
                    )
                    if coords:
                        return coords
        except Exception as e:
            logger.warning(
                "Error occurred while looking up target coordinates via RAG full scan: %s", e
            )

    return None


def _get_cmd_vel_topic(node: Any) -> str:
    """로봇 설정(2번 방식)을 최우선으로 사용하고, 미설정 시 ROS2 graph active topics에서 자동 감지(3번 보험 방식)한다."""
    if node is None:
        return "/cmd_vel"

    # 1. [2번 방식 - 최우선] 노드 파라미터, 로봇 프로필(_robot_limits_dict), 속성에 명시된 cmd_vel_topic
    topic = getattr(node, "_cmd_vel_topic", None)
    if topic:
        return str(topic)

    limits = getattr(node, "_robot_limits_dict", {}) or {}
    if isinstance(limits, dict) and "cmd_vel_topic" in limits and limits["cmd_vel_topic"]:
        return str(limits["cmd_vel_topic"])

    if hasattr(node, "has_parameter") and hasattr(node, "get_parameter"):
        try:
            if node.has_parameter("cmd_vel_topic"):
                val = node.get_parameter("cmd_vel_topic").value
                if val:
                    return str(val)
            if node.has_parameter("manipulation_cmd_vel_topic"):
                val = node.get_parameter("manipulation_cmd_vel_topic").value
                if val:
                    return str(val)
        except Exception:  # noqa: BLE001
            pass

    # 2. [3번 방식 - 보험] active ROS topics 및 message type 정보 기반 동적 탐지
    if hasattr(node, "get_topic_names_and_types"):
        try:
            topic_types_map = dict(node.get_topic_names_and_types())

            # 널리 쓰이는 표준 cmd_vel 토픽 후보
            preferred_topics = [
                "/stretch/cmd_vel",
                "/base_controller/cmd_vel_unstamped",
                "/cmd_vel",
                "/cmd_vel_nav",
                "/nav_vel",
            ]
            for pref in preferred_topics:
                if pref in topic_types_map:
                    return pref

            # 그 외 이름에 'cmd_vel'이 포함되고 Twist 계열 타입인 active topic 동적 감지
            for topic_name, types in topic_types_map.items():
                if "cmd_vel" in topic_name.lower():
                    for t in types:
                        if "Twist" in t:
                            return topic_name
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dynamic cmd_vel topic discovery failed: %s", exc)

    # 3. 기본 폴백
    return "/stretch/cmd_vel" if "stretch" in str(limits).lower() else "/cmd_vel"


def _cancel_cmd_vel_rotation() -> tuple[bool, str]:
    """진행 중인 직접 cmd_vel 회전을 협조적으로 중단하고 zero command를 발행한다."""
    globals.CMD_VEL_ROTATION_CANCEL.set()
    pub = globals.CMD_VEL_ROTATION_PUBLISHER
    if pub is None:
        return False, "취소할 직접 cmd_vel 회전이 없습니다."
    try:
        from geometry_msgs.msg import Twist

        pub.publish(Twist())
        return True, "직접 cmd_vel 회전 취소를 요청했습니다."
    except Exception as exc:  # noqa: BLE001
        return False, f"직접 cmd_vel 정지 명령 발행 실패: {exc}"


def _rotate_via_navigation(
    skill_or_node: Any,
    target_yaw: float,
    timeout_sec: float = 20.0,
    label: str = "네비게이션 회전",
) -> tuple[bool, str]:
    """NavigateToPose를 사용하여 현재 위치에서 목표 target_yaw로 제자리 회전한다.

    로봇 베이스 컨트롤러(DWB/Nav2)가 올바른 cmd_vel 토픽으로 안전하게 제자리 회전을
    수행하므로 가장 우수한 회전 성능을 보인다 (1순위 회전 방식).
    """
    if hasattr(skill_or_node, "create_publisher"):
        node = skill_or_node
    else:
        node = getattr(skill_or_node, "node", None)

    if hasattr(skill_or_node, "get_map_pose"):
        get_pose_fn = skill_or_node.get_map_pose
    elif node and hasattr(node, "get_map_pose"):
        get_pose_fn = node.get_map_pose
    else:
        get_pose_fn = None

    if not get_pose_fn or node is None:
        return False, "현재 로봇 위치를 가져올 수 없습니다."

    pose = get_pose_fn()
    if not pose:
        return False, "현재 로봇 위치/방향 정보를 읽지 못했습니다."

    try:
        x = float(pose["x"])
        y = float(pose["y"])
        yaw = float(target_yaw)
    except (KeyError, TypeError, ValueError):
        return False, "현재 로봇 위치/방향 정보를 읽지 못했습니다."
    frame_id = str(pose.get("frame") or "map")

    nav_accepted, nav_message, nav_goal_handle = _send_navigation_goal(
        node, x, y, frame_id, yaw=yaw
    )
    if not nav_accepted:
        return False, f"내비게이션 회전 목표 전송 실패: {nav_message}"

    success, result_message = _wait_for_goal_result(
        nav_goal_handle,
        timeout_sec=timeout_sec,
        label=label,
    )
    return success, result_message


def _rotate_via_cmd_vel(
    skill_or_node: Any,
    target_yaw: float,
    timeout_sec: float = 8.0,
    tolerance_rad: float = 0.05,
    kp: float = 1.2,
    max_w: float = 0.8,
    min_w: float = 0.25,
) -> tuple[bool, str]:
    """cmd_vel direct P-control 제자리 회전 폴백.

    Nav2 /spin 이 costmap obstacle/behavior 요인으로 ABORT 되거나
    NavigateToPose 폴백이 지연될 때 안전하고 신속하게 목표 yaw로 제자리 회전한다.
    """
    import time

    from geometry_msgs.msg import Twist

    from .orientation import shortest_angular_delta_rad

    if hasattr(skill_or_node, "create_publisher"):
        node = skill_or_node
    else:
        node = getattr(skill_or_node, "node", None)

    if hasattr(skill_or_node, "get_map_pose"):
        get_pose_fn = skill_or_node.get_map_pose
    elif node and hasattr(node, "get_map_pose"):
        get_pose_fn = node.get_map_pose
    else:
        get_pose_fn = None

    if not get_pose_fn or node is None:
        return False, "로봇 위치 정보를 얻을 수 없습니다."

    if not globals.CMD_VEL_ROTATION_LOCK.acquire(blocking=False):
        return False, "다른 직접 cmd_vel 회전이 이미 실행 중입니다."

    globals.CMD_VEL_ROTATION_CANCEL.clear()
    topic = _get_cmd_vel_topic(node)
    try:
        pub = node.create_publisher(Twist, topic, 10)
    except Exception as exc:
        globals.CMD_VEL_ROTATION_LOCK.release()
        return False, f"cmd_vel 퍼블리셔 생성 실패 ({topic}): {exc}"

    globals.CMD_VEL_ROTATION_PUBLISHER = pub

    t0 = time.monotonic()
    success = False
    msg_out = "cmd_vel 회전 완료"

    try:
        while time.monotonic() - t0 < timeout_sec and not globals.CMD_VEL_ROTATION_CANCEL.is_set():
            pose = get_pose_fn()
            if not pose:
                time.sleep(0.05)
                continue
            curr_yaw = float(pose["yaw"])
            err_rad = shortest_angular_delta_rad(curr_yaw, target_yaw)

            if abs(err_rad) <= tolerance_rad:
                success = True
                msg_out = f"목표 방향 정렬 성공 (오차: {math.degrees(err_rad):.1f}°)"
                break

            w = kp * err_rad
            if w > 0:
                w = max(min_w, min(max_w, w))
            else:
                w = min(-min_w, max(-max_w, w))

            cmd = Twist()
            cmd.angular.z = float(w)
            pub.publish(cmd)
            time.sleep(0.05)
    except Exception as exc:
        msg_out = f"cmd_vel 회전 중 예외 발생: {exc}"
    finally:
        was_cancelled = globals.CMD_VEL_ROTATION_CANCEL.is_set()
        stop_cmd = Twist()
        try:
            pub.publish(stop_cmd)
            node.destroy_publisher(pub)
        except Exception:  # noqa: BLE001
            pass
        globals.CMD_VEL_ROTATION_PUBLISHER = None
        globals.CMD_VEL_ROTATION_CANCEL.clear()
        globals.CMD_VEL_ROTATION_LOCK.release()

    if not success and was_cancelled:
        msg_out = "cmd_vel 회전 취소됨"
    elif not success and time.monotonic() - t0 >= timeout_sec:
        msg_out = f"cmd_vel 회전 타임아웃 ({timeout_sec}s)"

    return success, msg_out
