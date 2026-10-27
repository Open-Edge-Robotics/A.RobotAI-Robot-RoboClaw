"""ROS2 어댑터 — DashboardBackend 를 실제 ROS2 클라이언트/구독으로 구현.

이 모듈은 rclpy 와 generated message 에 의존하므로 ROS2 환경이 필요하다.
순수 로직은 domain/application 계층에 두고, 여기서는 ROS 인터페이스만 담는다.
"""

import math
import threading
import time
from typing import Any

from geometry_msgs.msg import PoseWithCovarianceStamped
from nav_msgs.msg import OccupancyGrid
from rcl_interfaces.msg import ParameterType
from rcl_interfaces.srv import GetParameters, ListParameters
from rclpy.action.client import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from sensor_msgs.msg import BatteryState
from tf2_ros import Buffer, TransformListener

from robo_claw_msgs.action import ExecuteTask
from robo_claw_msgs.msg import AgentStatus
from robo_claw_msgs.srv import ListPeers, QueryState

from ..domain.localization import (
    build_map_snapshot,
    build_pose_snapshot,
    unavailable_map,
    unavailable_pose,
)
from ..domain.telemetry import build_battery_telemetry


def _wait_future(future: Any, timeout_sec: float) -> bool:
    """future 완료까지 동기 대기 후 완료 여부 반환(타임아웃 시 False)."""
    event = threading.Event()
    future.add_done_callback(lambda _: event.set())
    return event.wait(timeout=timeout_sec)


def _wait_service(client: Any, timeout_sec: float) -> bool:
    """서비스 준비 여부를 즉시 판정한다(상태 폴링이 블로킹되지 않도록 기다리지 않는다)."""
    return client.service_is_ready()


from ..coercion import coerce_float as _coerce_float
from ..coercion import coerce_int as _coerce_int


class RosBackend:
    """DashboardBackend(application.service) 의 ROS2 구현체.

    에이전트 QueryState/ExecuteTask/ListPeers 클라이언트와 AgentStatus/BatteryState
    구독을 보유하고, 도메인/서비스 계층이 필요한 데이터를 안전한 기본값과 함께 제공한다.
    """

    def __init__(
        self,
        node: Node,
        *,
        agent_namespace: str = "robo_claw_agent_node",
        battery_topic: str = "/battery_state",
        status_topic: str | None = None,
        request_timeout_sec: float = 30.0,
        map_topic: str = "/map",
        map_frame: str = "map",
        base_frame: str = "base_link",
        initialpose_topic: str = "/initialpose",
        initialpose_subscriber_wait_sec: float = 1.0,
        config_query_timeout_sec: float = 2.0,
        config_cache_ttl_sec: float = 5.0,
    ) -> None:
        self._node = node
        self._timeout = request_timeout_sec
        self._cb = ReentrantCallbackGroup()
        self._map_frame = map_frame
        self._base_frame = base_frame
        self._initialpose_wait = initialpose_subscriber_wait_sec
        self._config_timeout = config_query_timeout_sec
        self._config_cache: tuple[float, dict[str, Any]] | None = None
        self._config_cache_ttl = config_cache_ttl_sec
        self._agent_node_name = agent_namespace
        ns = f"/{agent_namespace.strip('/')}"

        self._latest_map_snapshot: dict[str, Any] = unavailable_map(
            "지도를 아직 수신하지 못했습니다"
        )
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, node, spin_thread=False)
        # 원격 노드 파라미터 조회는 파라미터 서비스를 직접 호출한다. rclpy 의
        # AsyncParameterClient 는 list/get 응답 형태가 버전마다 달라 직접 호출이 안전하다.
        self._list_parameters_client = node.create_client(
            ListParameters, f"{ns}/list_parameters", callback_group=self._cb
        )
        self._get_parameters_client = node.create_client(
            GetParameters, f"{ns}/get_parameters", callback_group=self._cb
        )
        self._initialpose_pub = node.create_publisher(
            PoseWithCovarianceStamped,
            initialpose_topic,
            QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE),
        )
        # map_server 는 지도를 transient local 로 latch 해서 보낸다. 같은 QoS 로 구독하지
        # 않으면 대시보드가 늦게 떠서 지도를 영영 받지 못한다.
        map_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        node.create_subscription(
            OccupancyGrid,
            map_topic,
            self._on_map,
            map_qos,
            callback_group=self._cb,
        )

        self._query_client = node.create_client(
            QueryState, f"{ns}/query_state", callback_group=self._cb
        )
        self._peers_client = node.create_client(
            ListPeers, f"{ns}/list_peers", callback_group=self._cb
        )
        self._task_client = ActionClient(
            node, ExecuteTask, f"{ns}/execute_task", callback_group=self._cb
        )

        self._latest_agent_status: dict[str, Any] = {}
        self._latest_battery: dict[str, Any] = {}
        self._status_topic = status_topic or f"{ns}/status"
        node.create_subscription(
            AgentStatus,
            self._status_topic,
            self._on_agent_status,
            10,
            callback_group=self._cb,
        )
        node.create_subscription(
            BatteryState,
            battery_topic,
            self._on_battery,
            10,
            callback_group=self._cb,
        )

    # ---- subscription 콜백 -------------------------------------------------
    def _on_agent_status(self, msg: AgentStatus) -> None:
        self._latest_agent_status = {
            "state": _coerce_int(msg.state),
            "current_skill": str(msg.current_skill),
            "message": str(msg.message),
        }

    def _on_battery(self, msg: BatteryState) -> None:
        charging = msg.power_supply_status == BatteryState.POWER_SUPPLY_STATUS_CHARGING
        self._latest_battery = build_battery_telemetry(
            percentage=_coerce_float(msg.percentage, default=-1.0) or None,
            voltage=_coerce_float(msg.voltage),
            is_charging=charging,
        )

    def _on_map(self, msg: OccupancyGrid) -> None:
        try:
            self._latest_map_snapshot = build_map_snapshot(
                width=int(msg.info.width),
                height=int(msg.info.height),
                resolution=float(msg.info.resolution),
                origin={
                    "x": float(msg.info.origin.position.x),
                    "y": float(msg.info.origin.position.y),
                    "yaw": _yaw_from_quaternion(
                        msg.info.origin.orientation.x,
                        msg.info.origin.orientation.y,
                        msg.info.origin.orientation.z,
                        msg.info.origin.orientation.w,
                    ),
                },
                frame_id=str(msg.header.frame_id or self._map_frame),
                cells=list(msg.data),
                stamp_ns=_stamp_to_ns(msg.header.stamp),
            )
        except (TypeError, ValueError) as exc:  # 잘못된 지도 메시지는 무시하고 이전 값을 유지
            self._node.get_logger().warning(f"[Dashboard] 지도 메시지 무시: {exc}")

    # ---- DashboardBackend 구현 --------------------------------------------
    def query_state(self) -> str | None:
        if not _wait_service(self._query_client, self._timeout):
            return None
        request = QueryState.Request()
        request.query = ""
        future = self._query_client.call_async(request)
        if not _wait_future(future, self._timeout):
            return None
        result = future.result()
        if result is None:
            return None
        if not getattr(result, "success", False):
            return None
        return str(result.state_json)

    def latest_agent_status(self) -> dict[str, Any]:
        return dict(self._latest_agent_status)

    def latest_telemetry(self) -> dict[str, Any]:
        return {"battery": dict(self._latest_battery)}

    def latest_sensor_health(self) -> dict[str, Any]:
        # 센서 헬스는 이후 단계에서 에이전트 state_json 또는 별도 토픽으로 취합한다.
        return {}

    def list_peers(self) -> list[dict[str, Any]]:
        peers: list[dict[str, Any]] = []
        if not self._peers_client.service_is_ready():
            return peers
        future = self._peers_client.call_async(ListPeers.Request())
        if not _wait_future(future, self._timeout) or future.result() is None:
            return peers
        result = future.result()
        for item in getattr(result, "peers", None) or []:
            peers.append(
                {
                    "name": str(item.name),
                    "connected": bool(getattr(item, "connected", False)),
                }
            )
        return peers

    def describe_config(self) -> dict[str, Any] | None:
        """대시보드/에이전트 노드의 실행 파라미터를 수집한다(마스킹은 도메인 계층)."""
        cached = self._config_cache
        if cached is not None and time.monotonic() - cached[0] < self._config_cache_ttl:
            return dict(cached[1])
        snapshot = {
            "agent_node": self._agent_node_name,
            "agent_parameters": self._fetch_parameters(self._agent_node_name),
            "dashboard_node": self._node.get_name(),
            "dashboard_parameters": self._local_parameters(),
        }
        self._config_cache = (time.monotonic(), snapshot)
        return snapshot

    def _local_parameters(self) -> dict[str, Any] | None:
        """대시보드 노드 자신의 파라미터를 Python 값으로 모은다."""
        try:
            parameters = self._node.get_parameters_by_prefix("")
            if not parameters:
                return None
            return {
                name: _parameter_to_python(parameter.get_parameter_value())
                for name, parameter in parameters.items()
            }
        except Exception:  # noqa: BLE001 - 파라미터 조회 실패는 정상 상태로 처리
            return None

    def _fetch_parameters(self, node_name: str) -> dict[str, Any] | None:
        """원격 노드의 파라미터를 ROS2 파라미터 서비스로 조회한다."""
        try:
            if not self._list_parameters_client.service_is_ready() and not (
                self._list_parameters_client.wait_for_service(
                    timeout_sec=min(1.0, self._config_timeout)
                )
            ):
                return None
            if not self._get_parameters_client.service_is_ready() and not (
                self._get_parameters_client.wait_for_service(
                    timeout_sec=min(1.0, self._config_timeout)
                )
            ):
                return None

            list_future = self._list_parameters_client.call_async(ListParameters.Request())
            if not _wait_future(list_future, self._config_timeout):
                return None
            list_result = list_future.result()
            if list_result is None:
                return None
            names = list(getattr(list_result.result, "names", []) or [])
            if not names:
                # 이름이 하나도 없으면 노드가 없거나 파라미터 서비스가 꺼진 상태다.
                return None

            request = GetParameters.Request()
            request.names = names
            get_future = self._get_parameters_client.call_async(request)
            if not _wait_future(get_future, self._config_timeout):
                return None
            get_result = get_future.result()
            if get_result is None:
                return None
            collected: dict[str, Any] = {}
            for name, value in zip(names, get_result.values, strict=False):
                collected[str(name)] = _parameter_to_python(value)
            return collected or None
        except Exception:  # noqa: BLE001 - 노드 미기동/타임아웃은 정상 상태
            self._node.get_logger().debug(
                f"[Dashboard] {node_name} 파라미터 조회 실패", exc_info=True
            )
            return None

    def latest_map(self) -> dict[str, Any]:
        return dict(self._latest_map_snapshot)

    def latest_pose(self) -> dict[str, Any]:
        """map→base_link TF 를 조회해 로봇 위치를 반환한다(미수신 시 available=false)."""
        try:
            transform = self._tf_buffer.lookup_transform(self._map_frame, self._base_frame, Time())
        except Exception:  # noqa: BLE001 - TF 미수신은 정상 상태(초기 부팅/AMCL 미기동)
            return unavailable_pose()
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        pose = build_pose_snapshot(
            x=float(translation.x),
            y=float(translation.y),
            yaw=_yaw_from_quaternion(rotation.x, rotation.y, rotation.z, rotation.w),
            frame_id=self._map_frame,
            base_frame=self._base_frame,
            stamp_ns=_stamp_to_ns(transform.header.stamp),
        )
        return pose if pose is not None else unavailable_pose()

    def set_initial_pose(self, pose: dict[str, Any]) -> dict[str, Any]:
        """PoseWithCovarianceStamped 를 /initialpose 로 발행한다."""
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = str(pose.get("frame_id") or self._map_frame)
        msg.header.stamp = self._node.get_clock().now().to_msg()
        msg.pose.pose.position.x = float(pose["x"])
        msg.pose.pose.position.y = float(pose["y"])
        msg.pose.pose.position.z = 0.0
        msg.pose.pose.orientation.z = float(pose["orientation_z"])
        msg.pose.pose.orientation.w = float(pose["orientation_w"])
        covariance = list(pose.get("covariance") or [])
        for index, value in enumerate(covariance[:36]):
            msg.pose.covariance[index] = float(value)

        deadline = time.monotonic() + max(0.0, self._initialpose_wait)
        while time.monotonic() < deadline:
            if self._initialpose_pub.get_subscription_count() > 0:
                break
            time.sleep(0.05)
        self._initialpose_pub.publish(msg)
        return {"success": True, "message": "초기 위치를 발행했습니다"}

    def send_chat(self, message: str) -> dict[str, Any]:
        """ExecuteTask 액션으로 자연어 메시지를 에이전트에 전달한다."""
        if not self._task_client.wait_for_server(timeout_sec=self._timeout):
            return {"success": False, "error": "에이전트 액션 서버 연결 실패"}

        goal = ExecuteTask.Goal()
        goal.instruction = message
        goal.context_json = ""
        goal.timeout_sec = self._timeout

        future = self._task_client.send_goal_async(goal)
        if not _wait_future(future, self._timeout):
            return {"success": False, "error": "태스크 goal 응답 타임아웃"}
        goal_handle = future.result()
        if goal_handle is None or not goal_handle.accepted:
            return {"success": False, "error": "태스크 거부됨"}

        result_future = goal_handle.get_result_async()
        if not _wait_future(result_future, self._timeout):
            return {"success": False, "error": "태스크 결과 대기 타임아웃"}
        result = result_future.result()
        if result is None:
            return {"success": False, "error": "태스크 결과 없음"}

        skill_results: list[dict[str, Any]] = []
        for item in getattr(result.result, "skill_results", None) or []:
            skill_results.append(
                {
                    "skill_name": str(getattr(item, "skill_name", "") or ""),
                    "code": _coerce_int(getattr(item, "code", 0) or 0),
                    "message": str(getattr(item, "message", "") or ""),
                    "duration_sec": _coerce_float(getattr(item, "duration_sec", 0.0) or 0.0),
                    "result_json": str(getattr(item, "result_json", "") or ""),
                }
            )
        return {
            "success": bool(result.result.success),
            "message": str(result.result.result_message),
            "skill_results": skill_results,
        }


def _yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    """쿼터니언에서 yaw(rad) 를 계산한다."""
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def _stamp_to_ns(stamp: Any) -> int:
    try:
        return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)
    except (AttributeError, TypeError, ValueError):
        return 0


#: ParameterValue.type → 값 필드 이름.
_PARAMETER_VALUE_FIELDS = {
    ParameterType.PARAMETER_BOOL: "bool_value",
    ParameterType.PARAMETER_INTEGER: "integer_value",
    ParameterType.PARAMETER_DOUBLE: "double_value",
    ParameterType.PARAMETER_STRING: "string_value",
    ParameterType.PARAMETER_BYTE_ARRAY: "byte_array_value",
    ParameterType.PARAMETER_BOOL_ARRAY: "bool_array_value",
    ParameterType.PARAMETER_INTEGER_ARRAY: "integer_array_value",
    ParameterType.PARAMETER_DOUBLE_ARRAY: "double_array_value",
    ParameterType.PARAMETER_STRING_ARRAY: "string_array_value",
}


def _parameter_to_python(value: Any) -> Any:
    """rcl_interfaces ParameterValue 를 JSON 직렬화 가능한 값으로 변환한다."""
    field = _PARAMETER_VALUE_FIELDS.get(getattr(value, "type", None))
    if field is None:
        return None
    raw = getattr(value, field, None)
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return raw
