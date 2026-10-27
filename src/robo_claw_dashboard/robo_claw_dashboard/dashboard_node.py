"""Dashboard ROS2 노드 — 상태 취합·HTTP 서빙·에이전트 메시지 전송을 배선한다.

외부 웹 클라이언트는 이 노드의 HTTP 서버로 접속해 상태를 조회하고, /api/chat 으로
에이전트에 자연어 메시지를 전달할 수 있다.
"""

import json
import threading
from http.server import ThreadingHTTPServer

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from .adapters.http_handlers import make_handler
from .adapters.ros_adapter import RosBackend
from .adapters.security import DashboardHttpSecurityPolicy
from .application.service import DashboardService
from .domain.exposure import ERROR, exposure_findings, is_loopback_host, parse_allowed_cidrs

DEFAULT_BLOCKED_SKILLS = [
    "delete_file",
    "emergency_stop",
    "rag_delete",
    "rag_reindex",
]

DEFAULT_CIDRS_JSON = '["127.0.0.1/32", "::1/128"]'


def _parse_list(raw: str, default: list[str] | None = None) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return list(default) if default else []
    return (
        [str(x) for x in parsed] if isinstance(parsed, list) else (list(default) if default else [])
    )


from .coercion import coerce_float as _as_float
from .coercion import coerce_int as _as_int


def _resolve_static_root(static_root: str, node: Node) -> str:
    if static_root:
        return static_root
    try:
        share = get_package_share_directory("robo_claw_dashboard")
        return f"{share}/web"
    except Exception:  # noqa: BLE001 - 웹 리소스 누락 시 기본값으로 진행
        node.get_logger().warn(
            "robo_claw_dashboard 웹 리소스 디렉터리를 찾지 못해 기본 web/ 을 사용합니다."
        )
        return "web"


class DashboardNode(Node):
    """상태 취합 + HTTP 대시보드 + 채팅 전송을 제공하는 ROS2 라이프사이클 노드."""

    def __init__(self) -> None:
        super().__init__("robo_claw_dashboard_node")

        self.declare_parameter("dashboard_host", "127.0.0.1")
        self.declare_parameter("dashboard_port", 9090)
        self.declare_parameter("agent_name", "robo_claw_agent_node")
        self.declare_parameter("battery_topic", "/battery_state")
        self.declare_parameter("status_topic", "")
        self.declare_parameter("request_timeout_sec", 30.0)
        self.declare_parameter("map_topic", "/map")
        self.declare_parameter("map_frame", "map")
        self.declare_parameter("base_frame", "base_link")
        self.declare_parameter("initialpose_topic", "/initialpose")
        self.declare_parameter("config_query_timeout_sec", 2.0)
        self.declare_parameter("static_root", "")
        self.declare_parameter("http_readonly_token", "")
        self.declare_parameter("http_control_token", "")
        self.declare_parameter("http_allowed_cidrs_json", DEFAULT_CIDRS_JSON)
        self.declare_parameter("http_rate_limit_per_minute", 60)
        self.declare_parameter("http_blocked_skills_json", json.dumps(DEFAULT_BLOCKED_SKILLS))

        host = str(self.get_parameter("dashboard_host").value)
        port = _as_int(self.get_parameter("dashboard_port").value, 9090)
        agent_name = str(self.get_parameter("agent_name").value)
        battery_topic = str(self.get_parameter("battery_topic").value)
        status_topic = str(self.get_parameter("status_topic").value)
        timeout = _as_float(self.get_parameter("request_timeout_sec").value, 30.0)
        map_topic = str(self.get_parameter("map_topic").value or "/map")
        map_frame = str(self.get_parameter("map_frame").value or "map")
        base_frame = str(self.get_parameter("base_frame").value or "base_link")
        initialpose_topic = str(self.get_parameter("initialpose_topic").value or "/initialpose")
        config_timeout = _as_float(self.get_parameter("config_query_timeout_sec").value, 2.0)
        static_root = _resolve_static_root(str(self.get_parameter("static_root").value), self)

        allowed_cidrs, cidr_error = parse_allowed_cidrs(
            str(self.get_parameter("http_allowed_cidrs_json").value),
            fallback=_parse_list(DEFAULT_CIDRS_JSON),
        )
        security = DashboardHttpSecurityPolicy(
            readonly_token=str(self.get_parameter("http_readonly_token").value),
            control_token=str(self.get_parameter("http_control_token").value),
            allowed_cidrs=allowed_cidrs,
            rate_limit_per_minute=_as_int(
                self.get_parameter("http_rate_limit_per_minute").value, 60
            ),
            allowed_skills=[],
            blocked_skills=_parse_list(str(self.get_parameter("http_blocked_skills_json").value)),
        )

        backend = RosBackend(
            self,
            agent_namespace=agent_name,
            battery_topic=battery_topic,
            status_topic=status_topic or None,
            request_timeout_sec=timeout,
            map_topic=map_topic,
            map_frame=map_frame,
            base_frame=base_frame,
            initialpose_topic=initialpose_topic,
            config_query_timeout_sec=config_timeout,
        )
        self._service = DashboardService(backend)
        handler = make_handler(self._service, static_root, security)

        self._http_server = ThreadingHTTPServer((host, port), handler)
        self._http_thread = threading.Thread(target=self._http_server.serve_forever, daemon=True)
        self._http_thread.start()
        self.get_logger().info(f"[Dashboard] listening on http://{host}:{port} (web={static_root})")
        if cidr_error:
            self.get_logger().error(f"[Dashboard] {cidr_error}")
        self._report_exposure(
            host=host, allowed_cidrs=allowed_cidrs, auth_enabled=security.auth_enabled
        )
        on_shutdown = getattr(self, "add_on_shutdown_callback", None)
        if on_shutdown is not None:
            on_shutdown(self._shutdown_http)

    def _report_exposure(self, *, host: str, allowed_cidrs: list[str], auth_enabled: bool) -> None:
        """로컬/원격 접속 가능 여부와 위험한 노출 설정을 기동 시점에 남긴다."""
        findings = exposure_findings(
            host=host, allowed_cidrs=allowed_cidrs, auth_enabled=auth_enabled
        )
        for finding in findings:
            message = f"[Dashboard] {finding.message}"
            if finding.severity == ERROR:
                self.get_logger().error(message)
            else:
                self.get_logger().warning(message)
        if findings:
            return
        scope = "local only" if is_loopback_host(host) else "remote"
        self.get_logger().info(
            f"[Dashboard] access scope={scope} allowed_cidrs={allowed_cidrs or ['*']} "
            f"auth={'on' if auth_enabled else 'off'}"
        )

    def _shutdown_http(self) -> None:
        try:
            self._http_server.shutdown()
            self._http_server.server_close()
        except Exception:  # noqa: BLE001 - 종료 중 최선의 노력
            self.get_logger().debug("dashboard HTTP server shutdown 오류: %s", exc_info=True)


def main(args: list[str] | None = None) -> int:
    rclpy.init(args=args)
    node = DashboardNode()
    executor = MultiThreadedExecutor()
    try:
        rclpy.spin(node, executor=executor)
    except KeyboardInterrupt:  # noqa: PERF203
        node.get_logger().info("Dashboard node interrupted — shutting down.")
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
