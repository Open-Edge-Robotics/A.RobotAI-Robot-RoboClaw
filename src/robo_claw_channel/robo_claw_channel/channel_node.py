"""
채널 노드 — HTTP REST API로 외부 명령을 수신해 ROS2 액션/서비스로 중계

엔드포인트:
  POST /task   → ExecuteTask 액션 전송
  POST /skill  → ExecuteSkill 서비스 호출
  GET  /status → QueryState 서비스 호출 후 JSON 반환
"""

import json
import logging
import threading
import time
from http.server import ThreadingHTTPServer
from typing import Any

import rclpy
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from robo_claw_msgs.action import ExecuteTask
from robo_claw_msgs.srv import ExecuteSkill, PeerMessage, QueryState, SendMessage

from .channels import (
    BaseMessengerChannel,
    DiscordChannel,
    GrpcChannel,
    SlackChannel,
    TelegramChannel,
)
from .grpc_server import DEFAULT_DEDUP_PATH, DEFAULT_MAX_FILE_BYTES, GrpcServer
from .http_handlers import make_http_handler
from .param_helpers import param_bool, param_float, param_int, param_string
from .ros_future_utils import (
    await_future_result,
    send_execute_task,
    wait_for_future,
    wait_for_ready,
)
from .security import (  # noqa: F401  (HttpSecurityPolicy 등은 외부 호환을 위해 재노출)
    HttpAccessResult,
    HttpSecurityPolicy,
    SlidingWindowRateLimiter,
)
from .security import (
    parse_string_list as _parse_string_list,
)
from .task_result import build_task_result_json

logger = logging.getLogger(__name__)


class ChannelNode(Node):
    """
    멀티 채널 노드 — HTTP, Telegram, Slack, Discord 및 gRPC 통합 인터페이스
    """

    def __init__(self) -> None:
        super().__init__("robo_claw_channel_node")

        self.declare_parameter("http_host", "127.0.0.1")
        self.declare_parameter("http_port", 8080)
        self.declare_parameter("grpc_port", 50052)
        self.declare_parameter("grpc_peer_token", "")
        self.declare_parameter("grpc_peer_tokens_json", "{}")
        self.declare_parameter("grpc_max_file_bytes", DEFAULT_MAX_FILE_BYTES)
        self.declare_parameter("grpc_dedup_path", DEFAULT_DEDUP_PATH)
        self.declare_parameter("agent_name", "robo_claw_agent_node")
        self.declare_parameter("request_timeout_sec", 600.0)
        self.declare_parameter("http_readonly_token", "")
        self.declare_parameter("http_control_token", "")
        self.declare_parameter(
            "http_allowed_cidrs_json", '["127.0.0.1/32", "::1/128"]'
        )
        self.declare_parameter("http_rate_limit_per_minute", 60)
        self.declare_parameter("http_allowed_skills_json", "[]")
        self.declare_parameter(
            "http_blocked_skills_json",
            '["delete_file", "emergency_stop", "rag_delete", "rag_reindex"]',
        )

        self.declare_parameter("telegram_token", "")
        self.declare_parameter("slack_app_token", "")
        self.declare_parameter("slack_bot_token", "")
        self.declare_parameter("discord_token", "")

        self.declare_parameter("enable_discord", True)
        self.declare_parameter("enable_telegram", False)
        self.declare_parameter("enable_slack", False)
        self.declare_parameter("enable_grpc", True)

        host = param_string(self, "http_host")
        port = param_int(self, "http_port")
        grpc_port = param_int(self, "grpc_port")
        grpc_peer_token = param_string(self, "grpc_peer_token")
        grpc_peer_tokens_json = param_string(self, "grpc_peer_tokens_json")
        grpc_max_file_bytes = int(self.get_parameter("grpc_max_file_bytes").value)
        grpc_dedup_path = param_string(self, "grpc_dedup_path")
        try:
            grpc_peer_tokens = json.loads(grpc_peer_tokens_json or "{}")
            if not isinstance(grpc_peer_tokens, dict):
                raise ValueError("grpc_peer_tokens_json은 JSON object여야 합니다.")
            grpc_peer_tokens = {str(k): str(v) for k, v in grpc_peer_tokens.items()}
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self.get_logger().error(f"gRPC peer token 설정 파싱 실패: {exc}")
            grpc_peer_tokens = {}
        agent_name = param_string(self, "agent_name")
        self._timeout = param_float(self, "request_timeout_sec")
        self._http_security = HttpSecurityPolicy(
            readonly_token=param_string(self, "http_readonly_token"),
            control_token=param_string(self, "http_control_token"),
            allowed_cidrs=_parse_string_list(
                param_string(self, "http_allowed_cidrs_json"),
                parameter_name="http_allowed_cidrs_json",
            ),
            rate_limit_per_minute=param_int(self, "http_rate_limit_per_minute"),
            allowed_skills=_parse_string_list(
                param_string(self, "http_allowed_skills_json"),
                parameter_name="http_allowed_skills_json",
            ),
            blocked_skills=_parse_string_list(
                param_string(self, "http_blocked_skills_json"),
                parameter_name="http_blocked_skills_json",
            ),
        )

        self._agent_ns = f"/{agent_name}"
        self._messenger_channels: list[BaseMessengerChannel] = []
        self._rpc_metrics: dict[str, dict[str, Any]] = {}

        self._cb_group = ReentrantCallbackGroup()
        self._task_client = ActionClient(
            self,
            ExecuteTask,
            f"{self._agent_ns}/execute_task",
            callback_group=self._cb_group,
        )
        self._skill_client = self.create_client(
            ExecuteSkill,
            f"{self._agent_ns}/execute_skill",
            callback_group=self._cb_group,
        )
        self._query_client = self.create_client(
            QueryState, f"{self._agent_ns}/query_state", callback_group=self._cb_group
        )
        self._peer_message_client = self.create_client(
            PeerMessage,
            f"{self._agent_ns}/peer_message",
            callback_group=self._cb_group,
        )

        self._send_msg_srv = self.create_service(
            SendMessage,
            "~/send_message",
            self._handle_send_message,
            callback_group=self._cb_group,
        )

        self._http_server = ThreadingHTTPServer((host, port), self._make_handler())
        self._http_thread = threading.Thread(
            target=self._http_server.serve_forever, daemon=True
        )
        self._http_thread.start()

        enable_grpc = param_bool(self, "enable_grpc")
        if enable_grpc:

            def on_msg(text: str) -> tuple[bool, str, str]:
                self.get_logger().info(f"[gRPC call] {text}")
                result = self.send_task(text)
                success = bool(result.get("success"))
                # 스킬별 result_data 를 result_json 으로 함께 반환한다(진단용).
                # 예: rag_search 의 referenced → 클라이언트 리포트의 "참조 Qdrant 항목".
                result_json = build_task_result_json(result)
                if success:
                    msg = str(result.get("message") or "완료")
                    return True, msg, result_json
                else:
                    err = str(result.get("error") or result.get("message") or "명령 처리 실패")
                    return False, f"실패: {err}", result_json

            self._grpc_server = GrpcServer(
                grpc_port,
                on_msg,
                on_peer_message_cb=self.on_peer_message,
                peer_auth_token=grpc_peer_token,
                peer_auth_tokens=grpc_peer_tokens,
                max_file_bytes=grpc_max_file_bytes,
                dedup_path=grpc_dedup_path or DEFAULT_DEDUP_PATH,
                # 원격 peer 통신은 token이 설정된 경우에만 외부 인터페이스를 연다.
                bind_host="0.0.0.0" if (grpc_peer_token or grpc_peer_tokens) else "127.0.0.1",
            )
            self._grpc_server.start()

            grpc_ch = GrpcChannel(self._grpc_server)
            grpc_ch.start()
            self._messenger_channels.append(grpc_ch)
        else:
            self._grpc_server = None
            self.get_logger().info("gRPC server disabled")

        self._init_messenger_channels()

        self.get_logger().info(
            f"Channel node ready [HTTP:{port}, gRPC:{grpc_port if enable_grpc else 'OFF'}]"
        )
        self.get_logger().info(
            "HTTP security settings "
            f"[host={host}, "
            f"auth={'enabled' if self._http_security.auth_enabled else 'disabled'}, "
            f"cidrs={','.join(self._http_security.allowed_cidrs) or 'ALL'}, "
            f"rate_limit={self._http_security.rate_limit_per_minute}/min]"
        )

    def _init_messenger_channels(self):
        """설정된 토큰 및 활성화 상태 확인 후 메신저 채널 시작"""
        tg_token = param_string(self, "telegram_token")
        slack_app = param_string(self, "slack_app_token")
        slack_bot = param_string(self, "slack_bot_token")
        dc_token = param_string(self, "discord_token")

        en_tg = param_bool(self, "enable_telegram")
        en_sl = param_bool(self, "enable_slack")
        en_dc = param_bool(self, "enable_discord")

        # 메시지 수신 시 에이전트 태스크로 연결하는 콜백
        def on_message(text: str) -> str:
            self.get_logger().info(f"Messenger command received: {text}")
            result = self.send_task(text)
            return result.get("message", "응답 없음")

        if en_tg and tg_token:
            ch = TelegramChannel(tg_token, on_message)
            ch.start()
            self._messenger_channels.append(ch)

        if en_sl and slack_app and slack_bot:
            ch = SlackChannel(slack_app, slack_bot, on_message)
            ch.start()
            self._messenger_channels.append(ch)

        if en_dc and dc_token:
            ch = DiscordChannel(dc_token, on_message)
            ch.start()
            self._messenger_channels.append(ch)

    def destroy_node(self) -> None:
        if self._grpc_server:
            self._grpc_server.stop()
        for ch in self._messenger_channels:
            ch.stop()
        self._http_server.shutdown()
        super().destroy_node()

    def on_peer_message(self, sender_id: str, message: str) -> tuple[bool, str, str]:
        """인증된 gRPC 협동 메시지를 로컬 에이전트 인박스로 전달한다."""
        sender_id = str(sender_id or "").strip()
        message = str(message or "").strip()
        if not sender_id or not message or sender_id == "client_node":
            return False, "협동 메시지의 sender_id와 내용이 필요합니다.", ""

        ready_error = wait_for_ready(
            self._peer_message_client.wait_for_service,
            5.0,
            "PeerMessage 서비스 연결 실패",
        )
        if ready_error:
            return False, ready_error, ""

        request = PeerMessage.Request()
        request.peer_name = sender_id
        request.message = message
        ok, response = await_future_result(
            self._peer_message_client.call_async(request),
            timeout_sec=10.0,
            timeout_error="협동 메시지 전달 타임아웃",
            none_error="협동 메시지 응답 없음",
        )
        if not ok:
            return False, str(response), ""
        if not response.success:
            return False, response.error_message or "협동 메시지 수신 실패", ""
        return True, response.result_message or "협동 메시지 수신", ""

    def _wait_for_future(self, future: Any, timeout_sec: float) -> bool:
        return wait_for_future(future, timeout_sec)

    def _record_rpc(
        self, name: str, *, success: bool, duration_sec: float, error: str = ""
    ) -> None:
        if not hasattr(self, "_rpc_metrics"):
            self._rpc_metrics = {}
        stat = self._rpc_metrics.setdefault(
            name,
            {
                "count": 0,
                "success": 0,
                "failure": 0,
                "total_duration_sec": 0.0,
                "last_duration_sec": 0.0,
                "last_error": "",
            },
        )
        stat["count"] += 1
        stat["success"] += 1 if success else 0
        stat["failure"] += 0 if success else 1
        stat["total_duration_sec"] += max(0.0, duration_sec)
        stat["last_duration_sec"] = max(0.0, duration_sec)
        if error:
            stat["last_error"] = error
        elif success:
            stat["last_error"] = ""

    def _rpc_snapshot(self) -> dict[str, dict[str, Any]]:
        if not hasattr(self, "_rpc_metrics"):
            return {}
        return {
            name: {
                **stat,
                "avg_duration_sec": (
                    stat["total_duration_sec"] / stat["count"]
                    if stat["count"]
                    else 0.0
                ),
            }
            for name, stat in self._rpc_metrics.items()
        }

    def _wait_for_ready(self, wait_fn: Any, timeout_sec: float, error: str) -> str | None:
        return wait_for_ready(wait_fn, timeout_sec, error)

    def _await_future_result(
        self, future: Any, timeout_sec: float, timeout_error: str, none_error: str
    ) -> tuple[bool, Any]:
        return await_future_result(future, timeout_sec, timeout_error, none_error)

    def send_task(self, instruction: str, context_json: str = "") -> dict[str, Any]:
        """ExecuteTask 액션 전송 후 결과 반환 (공통 send_execute_task + RPC 메트릭 기록)"""
        start = time.monotonic()
        response = send_execute_task(
            self._task_client, instruction, context_json, self._timeout
        )
        success = bool(response.get("success"))
        error = "" if success else str(
            response.get("error") or response.get("message") or ""
        )
        if not success:
            logger.error(f"send_task failed: {error}")
        self._record_rpc(
            "send_task",
            success=success,
            duration_sec=time.monotonic() - start,
            error=error,
        )
        return response

    def call_skill(self, skill_name: str, params_json: str = "{}") -> dict[str, Any]:
        """ExecuteSkill 서비스 호출"""
        start = time.monotonic()
        ready_error = self._wait_for_ready(
            self._skill_client.wait_for_service,
            self._timeout,
            "ExecuteSkill 서비스 연결 실패",
        )
        if ready_error:
            logger.error(f"call_skill failed: {ready_error}")
            self._record_rpc(
                "call_skill",
                success=False,
                duration_sec=time.monotonic() - start,
                error=ready_error,
            )
            return {"success": False, "error": ready_error}

        req = ExecuteSkill.Request()
        req.skill_name = skill_name
        req.params_json = params_json
        req.timeout_sec = self._timeout

        future = self._skill_client.call_async(req)
        ok, resp = self._await_future_result(
            future,
            timeout_sec=self._timeout,
            timeout_error="서비스 호출 타임아웃",
            none_error="서비스 응답 없음",
        )
        if not ok:
            self._record_rpc(
                "call_skill",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(resp),
            )
            return {"success": False, "error": resp}

        # result_json 을 함께 돌려준다. 스킬의 구조화 결과(get_status 의 pose/battery,
        # identify_location 의 location_name 등)가 여기 담기는데, 이전에는 버려져서
        # REST 호출자가 message 문자열을 파싱해야 했다(프로그램 제어에 부적합).
        response = {
            "success": resp.result.code == 0,
            "message": resp.result.message,
            "skill_name": resp.result.skill_name,
            "result_json": getattr(resp.result, "result_json", "") or "",
        }
        self._record_rpc(
            "call_skill",
            success=bool(response["success"]),
            duration_sec=time.monotonic() - start,
            error="" if response["success"] else str(response["message"]),
        )
        return response

    def get_status(self) -> dict[str, Any]:
        """QueryState 서비스로 에이전트 상태 조회"""
        start = time.monotonic()
        ready_error = self._wait_for_ready(
            self._query_client.wait_for_service,
            self._timeout,
            "QueryState 서비스 연결 실패",
        )
        if ready_error:
            logger.error(f"get_status failed: {ready_error}")
            self._record_rpc(
                "get_status",
                success=False,
                duration_sec=time.monotonic() - start,
                error=ready_error,
            )
            return {"success": False, "error": ready_error}

        req = QueryState.Request()
        future = self._query_client.call_async(req)
        ok, resp = self._await_future_result(
            future,
            timeout_sec=self._timeout,
            timeout_error="상태 조회 타임아웃",
            none_error="서비스 응답 없음",
        )
        if not ok:
            self._record_rpc(
                "get_status",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(resp),
            )
            return {"success": False, "error": resp}

        try:
            state_data = json.loads(resp.state_json)
        except json.JSONDecodeError:
            state_data = {"raw": resp.state_json}

        response = {"success": resp.success, "state": state_data}
        self._record_rpc(
            "get_status",
            success=bool(resp.success),
            duration_sec=time.monotonic() - start,
            error="" if resp.success else "QueryState 응답 실패",
        )
        return response

    def _handle_send_message(self, request, response):
        msg = request.message
        file_path = request.file_path

        if not self._messenger_channels:
            response.success = True
            return response

        from concurrent.futures import ThreadPoolExecutor

        def _send_task(ch):
            try:
                if file_path:
                    return ch.send_file(file_path, caption=msg)
                else:
                    return ch.send_text(msg)
            except Exception as e:
                self.get_logger().error(f"Channel {ch.name} send error: {e}")
                return False

        with ThreadPoolExecutor(max_workers=len(self._messenger_channels)) as executor:
            results = list(executor.map(_send_task, self._messenger_channels))

        success_count = sum(1 for res in results if res)
        response.success = success_count > 0
        if not response.success:
            response.error_message = "모든 채널로의 전송에 실패했습니다."
        self._record_rpc(
            "send_message",
            success=bool(response.success),
            duration_sec=0.0,
            error=response.error_message if not response.success else "",
        )

        return response

    # ── HTTP 핸들러 팩토리 ────────────────────────────────────

    def _make_handler(self) -> type:
        """ChannelNode 참조를 캡처한 HTTP 핸들러 클래스 생성"""
        return make_http_handler(self)


def main() -> None:
    rclpy.init()
    node = ChannelNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass
