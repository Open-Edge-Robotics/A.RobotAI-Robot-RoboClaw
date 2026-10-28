"""Dashboard ROS2 노드 — 상태 취합·HTTP 서빙·에이전트 메시지 전송을 배선한다.

외부 웹 클라이언트는 이 노드의 HTTP 서버로 접속해 상태를 조회하고, /api/chat 으로
에이전트에 자연어 메시지를 전달할 수 있다.
"""

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

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

        #: 선언 시점 기본값. "작성하지 않은 값"을 화면에서 구분하는 데 쓴다.
        self._declared_defaults: dict = {}
        self._declare("dashboard_host", "127.0.0.1")
        self._declare("dashboard_port", 9090)
        self._declare("agent_name", "robo_claw_agent_node")
        self._declare("battery_topic", "/battery_state")
        # 동료 로봇 목록 서버는 채널(messenger) 노드다(에이전트 노드는 이 서비스를 제공하지 않음).
        self._declare("peers_service", "/robo_claw_messenger_client_node/list_peers")
        self._declare("status_topic", "")
        self._declare("request_timeout_sec", 30.0)
        # 로봇마다 지도 토픽이 다르다(former0047 `/map`, CLOiD `/lio/grid_map`).
        # 쉼표로 여러 개를 지정하면 데이터가 들어오는 토픽을 사용한다.
        self._declare("map_topic", "/map,/lio/grid_map")
        self._declare("map_frame", "map")
        self._declare("base_frame", "base_link")
        self._declare("initialpose_topic", "/initialpose")
        self._declare("config_query_timeout_sec", 2.0)
        # 로그 패널: /rosout 구독 버퍼(최근 N 줄)와 발행 토픽.
        self._declare("log_topic", "/rosout")
        self._declare("log_buffer_size", 500)
        self._declare("param_default_files", "agent.yaml")
        self._declare("contract_file", "")
        self._declare("static_root", "")
        self._declare("http_readonly_token", "")
        self._declare("http_control_token", "")
        self._declare("http_allowed_cidrs_json", DEFAULT_CIDRS_JSON)
        self._declare("http_rate_limit_per_minute", 60)
        self._declare("http_blocked_skills_json", json.dumps(DEFAULT_BLOCKED_SKILLS))

        host = str(self.get_parameter("dashboard_host").value)
        port = _as_int(self.get_parameter("dashboard_port").value, 9090)
        agent_name = str(self.get_parameter("agent_name").value)
        battery_topic = str(self.get_parameter("battery_topic").value)
        status_topic = str(self.get_parameter("status_topic").value)
        timeout = _as_float(self.get_parameter("request_timeout_sec").value, 30.0)
        map_topic = str(self.get_parameter("map_topic").value or "")
        map_frame = str(self.get_parameter("map_frame").value or "map")
        base_frame = str(self.get_parameter("base_frame").value or "base_link")
        initialpose_topic = str(self.get_parameter("initialpose_topic").value or "/initialpose")
        config_timeout = _as_float(self.get_parameter("config_query_timeout_sec").value, 2.0)
        log_topic = str(self.get_parameter("log_topic").value or "/rosout")
        log_buffer_size = _as_int(self.get_parameter("log_buffer_size").value, 500)
        peers_service = str(
            self.get_parameter("peers_service").value
            or "/robo_claw_messenger_client_node/list_peers"
        )
        param_default_files = str(self.get_parameter("param_default_files").value or "agent.yaml")
        param_config_dir = self._resolve_param_config_dir()
        contract_file = str(self.get_parameter("contract_file").value or "") or (
            self._resolve_contract_file()
        )
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
            peers_service=peers_service,
            config_query_timeout_sec=config_timeout,
            param_config_dir=param_config_dir,
            param_default_files=param_default_files,
            contract_file=contract_file,
            declared_defaults=self._declared_defaults,
            log_topic=log_topic,
            log_buffer_size=log_buffer_size,
        )
        self._service = DashboardService(backend)
        handler = make_handler(self._service, static_root, security)

        self._http_server = ThreadingHTTPServer((host, port), handler)
        self._http_thread = threading.Thread(target=self._http_server.serve_forever, daemon=True)
        self._http_thread.start()
        self.get_logger().info(f"[Dashboard] listening on http://{host}:{port} (web={static_root})")
        self.get_logger().info(
            f"[Dashboard] map topics={backend.map_topics} frames={map_frame}->{base_frame}"
        )
        self.get_logger().info(
            f"[Dashboard] log topic={log_topic or '(disabled)'} buffer={backend.log_capacity}"
        )
        if cidr_error:
            self.get_logger().error(f"[Dashboard] {cidr_error}")
        self._report_exposure(
            host=host, allowed_cidrs=allowed_cidrs, auth_enabled=security.auth_enabled
        )
        on_shutdown = getattr(self, "add_on_shutdown_callback", None)
        if on_shutdown is not None:
            on_shutdown(self._shutdown_http)

    def _declare(self, name: str, default):
        """파라미터를 선언하고 기본값을 기록한다(설정 패널의 기본값 표시용)."""
        self._declared_defaults[name] = default
        self.declare_parameter(name, default)

    def _resolve_contract_file(self) -> str:
        """계약 기본값 파일(runtime-contract.json) 경로를 찾는다(없으면 빈 문자열)."""
        candidates = []
        try:
            share = get_package_share_directory("robo_claw_dashboard")
            candidates.append(f"{share}/config/runtime-contract.json")
        except Exception:  # noqa: BLE001 - 설치본이 아닌 환경
            pass
        # 소스 트리에서 직접 실행하는 경우(개발/테스트)
        candidates.append(
            str(Path(__file__).resolve().parents[4] / "contracts" / "runtime-contract.json")
        )
        for candidate in candidates:
            if Path(candidate).is_file():
                return candidate
        return ""

    def _resolve_param_config_dir(self) -> str:
        """launch 가 쓰는 파라미터 파일 디렉터리(robo_claw_bringup/config)를 찾는다."""
        try:
            share = get_package_share_directory("robo_claw_bringup")
        except Exception:  # noqa: BLE001 - bringup 미설치 환경(단독 실행)에서는 기본값 없이 동작
            self.get_logger().debug("robo_claw_bringup share 디렉터리를 찾지 못했습니다.")
            return ""
        return f"{share}/config"

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
