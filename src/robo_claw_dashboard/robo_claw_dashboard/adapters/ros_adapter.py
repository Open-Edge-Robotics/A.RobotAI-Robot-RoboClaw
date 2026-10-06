"""ROS2 어댑터 — DashboardBackend 를 실제 ROS2 클라이언트/구독으로 구현.

이 모듈은 rclpy 와 generated message 에 의존하므로 ROS2 환경이 필요하다.
순수 로직은 domain/application 계층에 두고, 여기서는 ROS 인터페이스만 담는다.
"""

import threading
from typing import Any

from rclpy.action.client import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node
from sensor_msgs.msg import BatteryState

from robo_claw_msgs.action import ExecuteTask
from robo_claw_msgs.msg import AgentStatus
from robo_claw_msgs.srv import ListPeers, QueryState

from ..domain.telemetry import build_battery_telemetry


def _wait_future(future: Any, timeout_sec: float) -> bool:
    """future 완료까지 동기 대기 후 완료 여부 반환(타임아웃 시 False)."""
    event = threading.Event()
    future.add_done_callback(lambda _: event.set())
    return event.wait(timeout=timeout_sec)


def _wait_service(client: Any, timeout_sec: float) -> bool:
    if hasattr(client, "service_is_ready"):
        return client.service_is_ready()
    return client.wait_for_service(timeout_sec=timeout_sec)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
    ) -> None:
        self._node = node
        self._timeout = request_timeout_sec
        self._cb = ReentrantCallbackGroup()
        ns = f"/{agent_namespace.strip('/')}"

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
