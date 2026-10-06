"""
gRPC 메신저 클라이언트 노드 — 외부 로봇의 gRPC 메신저 서버에 연결하여 실시간 메시지 송수신 및 에이전트 명령 중계
"""

import ast
import json
import logging
import os
import queue
import threading
import time
import uuid
from collections.abc import Generator
from typing import Any

import grpc
import rclpy
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from robo_claw_msgs.action import ExecuteTask
from robo_claw_msgs.msg import PeerInfo
from robo_claw_msgs.srv import CallPeerRobot, ListPeers, PeerMessage, SendMessage

# pb2 및 grpc stub 임포트
from . import messenger_pb2 as pb2
from . import messenger_pb2_grpc as pb2_grpc
from .grpc_server import HANDSHAKE_CONTENT, HANDSHAKE_SENDER_ID
from .param_helpers import param_float, param_int, param_string
from .ros_future_utils import (
    await_future_result,
    send_execute_task,
    wait_for_future,
    wait_for_ready,
)

logger = logging.getLogger(__name__)
_MAX_PEER_QUEUE_SIZE = 128


class MessengerClientNode(Node):
    """
    gRPC 메신저 클라이언트 노드.
    상대 동료 로봇들의 gRPC 서버에 접속하여 ChatStream을 유지하고,
    수신된 상대 메시지를 로컬 에이전트의 ExecuteTask 액션으로 보내 처리한 뒤 응답을 돌려줍니다.
    또한 로컬의 SendMessage 서비스 요청을 gRPC를 통해 모든 상대방(들)에게 브로드캐스트합니다.
    """

    @staticmethod
    def _peer_display_name(peer: dict[str, Any]) -> str:
        """피어 설정 dict로부터 표시용 peer_name을 계산 (agent_name 우선, 없으면 host:port)"""
        return peer.get("agent_name") or f"{peer.get('host')}:{peer.get('port')}"

    def _peer_auth_token(self, peer_name: str) -> str:
        # 1. peer dict 자체의 auth_token 우선
        for peer in self._peers:
            if self._peer_display_name(peer) == peer_name:
                tok = str(peer.get("auth_token") or "").strip()
                if tok:
                    return tok
        # 2. _peer_tokens_map에서 peer_name 매칭 (대소문자 무시 지원)
        peer_tokens = getattr(self, "_peer_tokens_map", {})
        norm_name = str(peer_name or "").strip().lower()
        for k, v in peer_tokens.items():
            if k.strip().lower() == norm_name and v:
                return v
        # 3. 공통 peer token fallback
        common = getattr(self, "_common_peer_token", "")
        if common:
            return common
        return ""

    def _resolve_peer_name(self, peer_name: str) -> str:
        """sender_id와 설정된 표시 이름이 다를 때 호출 대상 이름을 보정한다."""
        requested = str(peer_name or "").strip()
        if requested in self._peer_stubs:
            return requested
        for peer in self._peers:
            aliases = {
                str(peer.get(key) or "").strip()
                for key in ("agent_name", "agent_id", "peer_name", "name", "id")
            }
            if requested in aliases:
                return self._peer_display_name(peer)
        # 1:1 설정에서는 원격 sender_id가 기본 agent_id와 달라도
        # 현재 연결된 유일한 피어로 안전하게 보정할 수 있다.
        if len(self._peer_stubs) == 1:
            return next(iter(self._peer_stubs))
        return requested

    def _peer_rpc_metadata(self, peer_name: str) -> tuple[tuple[str, str], ...]:
        token = self._peer_auth_token(peer_name)
        if not token:
            return ()
        return (
            ("x-robo-claw-peer-token", token),
            ("x-robo-claw-peer-id", self._sender_id),
        )

    def _authorized_peer_message(self, peer_name: str, message: pb2.ChatMessage) -> pb2.ChatMessage:
        """피어 토큰이 설정된 로봇에는 모든 송신 메시지에 토큰을 실어 보낸다.

        서버는 토큰이 설정되면 협동 메시지뿐 아니라 회신(sender_id="robot")과
        일반 지시도 검증하므로, 토큰이 빠지면 '협동 메시지 인증 실패'로 버려진다.
        """
        token = self._peer_auth_token(peer_name)
        if token:
            message.metadata["auth_token"] = token
        return message

    def _warn_duplicate_peer_names(self, peers: list) -> None:
        """target_peers_json 설정에 중복된 peer_name이 있으면 경고 로그를 남긴다"""
        seen: dict[str, dict[str, Any]] = {}
        for peer in peers:
            name = self._peer_display_name(peer)
            if name in seen:
                self.get_logger().warning(
                    f"Duplicate peer_name '{name}' found — check target_peers_json configuration: "
                    f"{seen[name]} vs {peer}"
                )
            else:
                seen[name] = peer

    def __init__(self) -> None:
        super().__init__("robo_claw_messenger_client_node")

        # 1:1 하위 호환성용 파라미터
        self.declare_parameter("target_host", "127.0.0.1")
        self.declare_parameter("target_port", 50052)
        # 1:N 연결용 JSON 파라미터
        # 예: '[{"host": "192.168.1.10", "port": 50051, "agent_name": "butler"}, {"host": "192.168.1.11", "port": 50051, "agent_name": "former"}]'
        # 기본값을 빈 문자열("")로 두어 하위 호환성 1:1 폴백과 명시적 0대 피어 설정("[]")을 구분합니다.
        self.declare_parameter("target_peers_json", "")

        self.declare_parameter("grpc_peer_token", "")
        self.declare_parameter("grpc_peer_tokens_json", "{}")

        self.declare_parameter("agent_name", "robo_claw_agent_node")
        self.declare_parameter("agent_id", "")
        self.declare_parameter("request_timeout_sec", 600.0)

        self._target_host = param_string(self, "target_host")
        self._target_port = param_int(self, "target_port")
        self._peers_json = param_string(self, "target_peers_json")
        self._common_peer_token = param_string(self, "grpc_peer_token")
        grpc_peer_tokens_json = param_string(self, "grpc_peer_tokens_json")
        try:
            raw_tokens = json.loads(grpc_peer_tokens_json or "{}")
            if not isinstance(raw_tokens, dict):
                raw_tokens = {}
            self._peer_tokens_map = {
                str(k).strip(): str(v).strip() for k, v in raw_tokens.items()
            }
        except Exception:
            self._peer_tokens_map = {}

        self._agent_name = param_string(self, "agent_name")
        configured_agent_id = param_string(self, "agent_id")
        self._sender_id = configured_agent_id or self._agent_name
        self._timeout = param_float(self, "request_timeout_sec")

        self._agent_ns = f"/{self._agent_name}"
        self._cb_group = ReentrantCallbackGroup()

        # 로컬 에이전트 액션 클라이언트 초기화
        self._task_client = ActionClient(
            self,
            ExecuteTask,
            f"{self._agent_ns}/execute_task",
            callback_group=self._cb_group,
        )

        # 자율협동 — 로컬 에이전트의 협동 인박스로 메시지를 전달하기 위한 클라이언트
        self._peer_message_client = self.create_client(
            PeerMessage,
            f"{self._agent_ns}/peer_message",
            callback_group=self._cb_group,
        )

        # 로컬 메시지 송신 서비스 서버 생성
        self._send_msg_srv = self.create_service(
            SendMessage,
            "~/send_message",
            self._handle_send_message,
            callback_group=self._cb_group,
        )

        # 이름으로 지정한 특정 동료 로봇을 호출(응답 대기 옵션)하기 위한 서비스 서버 생성
        self._call_peer_srv = self.create_service(
            CallPeerRobot,
            "~/call_peer",
            self._handle_call_peer,
            callback_group=self._cb_group,
        )

        # 설정된 동료 로봇 목록과 연결 상태를 조회하기 위한 서비스 서버 생성
        self._list_peers_srv = self.create_service(
            ListPeers,
            "~/list_peers",
            self._handle_list_peers,
            callback_group=self._cb_group,
        )

        self._running = True
        self._peer_threads = []
        self._peer_queues: dict[str, queue.Queue] = {}

        # 실시간 송신 및 연결 유지를 위해 스레드 세이프 딕셔너리로 gRPC 리소스 관리
        self._peer_stubs_lock = threading.Lock()
        self._peer_stubs: dict[str, pb2_grpc.RoboMessengerStub] = {}
        self._peer_channels: dict[str, grpc.Channel] = {}

        # 피어 목록 구성
        self._peers = []
        peers_json = self._peers_json.strip() if self._peers_json else ""
        if peers_json in ('""', "''"):
            peers_json = ""
        # ros2 launch 의 := value 처리가 distro 마다 달라 yamlStringArg 가 감싼
        # 작은따옴표가 리터럴로 남는 경우(humble Docker)가 있다. 한 겹의 짝맞는
        # 인용부호를 벗겨 깨끗한 JSON/Python repr 로 정규화한다.
        if len(peers_json) >= 2 and peers_json[0] == peers_json[-1] and peers_json[0] in ('"', "'"):
            inner = peers_json[1:-1].strip()
            if inner and inner[0] in "[{":
                peers_json = inner
        is_explicit_empty = peers_json == "[]"
        try:
            if peers_json and not is_explicit_empty:
                try:
                    parsed_peers = json.loads(peers_json)
                except json.JSONDecodeError:
                    parsed_peers = ast.literal_eval(peers_json)
                valid_peers = []
                for peer in parsed_peers:
                    if isinstance(peer, dict) and peer.get("host") and peer.get("port"):
                        valid_peers.append(peer)
                    else:
                        self.get_logger().error(
                            f"target_peers_json entry missing host/port, skipping: {peer}"
                        )
                self._peers = valid_peers
                self.get_logger().info(
                    f"target_peers_json parsed successfully: {len(self._peers)} peer(s) configured"
                )
        except Exception as e:
            self.get_logger().error(f"Failed to parse target_peers_json: {e}")

        # target_peers_json 파라미터가 아예 제공되지 않았거나 빈 문자열("")인 경우에만
        # 하위 호환성을 위해 기존 target_host/port 기반 1:1 피어를 자동으로 구성합니다.
        # 명시적으로 빈 리스트("[]")가 제공된 경우(0대 설정)에는 폴백 피어를 생성하지 않습니다.
        if not self._peers and not is_explicit_empty and not self._peers_json.strip():
            self._peers = [
                {
                    "host": self._target_host,
                    "port": self._target_port,
                    "agent_name": f"{self._target_host}_{self._target_port}",
                    "auth_token": self._common_peer_token,
                }
            ]
            self.get_logger().info(
                f"No peers specified and parameter missing — using default target peer: {self._target_host}:{self._target_port}"
            )

        self._warn_duplicate_peer_names(self._peers)
        if len(self._peers) > 1 and self._sender_id == "robo_claw_agent":
            self.get_logger().warning(
                "Multiple peer connections are configured but agent_id is the default "
                "'robo_claw_agent'. Set a unique agent_id per robot so cooperation replies "
                "can be routed to the correct peer."
            )

        # 백그라운드 gRPC 연결 및 수신 스레드 시작
        for peer in self._peers:
            peer_name = self._peer_display_name(peer)
            self._peer_queues[peer_name] = queue.Queue(maxsize=_MAX_PEER_QUEUE_SIZE)

            thread = threading.Thread(
                target=self._grpc_peer_connection_loop,
                args=(peer,),
                daemon=True,
                name=f"grpc-peer-{peer_name}",
            )
            thread.start()
            self._peer_threads.append(thread)

        self.get_logger().info(
            f"gRPC messenger client node started [managing {len(self._peers)} peer connection(s)]"
        )

    def _grpc_peer_connection_loop(self, peer: dict[str, Any]) -> None:
        """각 상대 동료 로봇 피어별 연결 및 ChatStream 스트리밍 수신 백그라운드 루프 (자동 재연결 지원)"""
        host = peer.get("host")
        port = peer.get("port")
        peer_name = self._peer_display_name(peer)
        if not host or not port:
            self.get_logger().error(
                f"[{peer_name}] Skipping connection attempt due to missing host/port: {peer}"
            )
            return
        target_addr = f"{host}:{port}"
        peer_queue = self._peer_queues[peer_name]

        while self._running:
            self.get_logger().info(
                f"Attempting to connect to peer robot [{peer_name}] ({target_addr})..."
            )
            channel = None
            try:
                channel = grpc.insecure_channel(target_addr)
                grpc.channel_ready_future(channel).result(timeout=5.0)

                stub = pb2_grpc.RoboMessengerStub(channel)

                with self._peer_stubs_lock:
                    self._peer_stubs[peer_name] = stub
                    self._peer_channels[peer_name] = channel

                self.get_logger().info(
                    f"Connected to peer robot [{peer_name}]. Establishing ChatStream connection..."
                )

                # 이 피어 전용 요청 생성 제너레이터
                def request_generator() -> Generator[pb2.ChatMessage, None, None]:
                    yield pb2.ChatMessage(
                        sender_id=HANDSHAKE_SENDER_ID,
                        content=HANDSHAKE_CONTENT,
                        timestamp=int(time.time()),
                    )
                    while self._running:
                        try:
                            msg = peer_queue.get(timeout=1.0)
                            yield msg
                        except queue.Empty:
                            continue

                responses = stub.ChatStream(
                    request_generator(), metadata=self._peer_rpc_metadata(peer_name)
                )

                # 상대방으로부터 수신되는 메시지 실시간 중계
                for response in responses:
                    self.get_logger().info(
                        f"[{peer_name} -> gRPC received] sender: {response.sender_id}, content: {response.content}"
                    )
                    # "robot"으로부터의 응답이 아닌 외부 발신 메시지만 로컬 에이전트에 중계
                    if response.sender_id != "robot" and response.content:
                        # 로컬 에이전트에 작업을 보내고 비동기로 처리
                        threading.Thread(
                            target=self._process_received_message,
                            args=(response.content, peer_name),
                            daemon=True,
                        ).start()

            except grpc.FutureTimeoutError:
                self.get_logger().warning(
                    f"[{peer_name}] gRPC server connection timed out ({target_addr}). "
                    "상대 로봇의 channel 노드 기동 여부, GRPC_PEER_TOKEN 설정"
                    "(토큰이 없으면 상대 서버가 127.0.0.1에만 바인딩), 방화벽을 확인하세요. "
                    "Retrying in 5 seconds."
                )
            except grpc.RpcError as e:
                self.get_logger().error(
                    f"[{peer_name}] gRPC connection error: {e}. Retrying in 5 seconds."
                )
            except Exception as e:
                self.get_logger().error(
                    f"[{peer_name}] Unexpected connection exception: {e}. Retrying in 5 seconds."
                )
            finally:
                with self._peer_stubs_lock:
                    self._peer_stubs.pop(peer_name, None)
                    self._peer_channels.pop(peer_name, None)
                if channel:
                    channel.close()

                if self._running:
                    time.sleep(5.0)

    def _process_received_message(self, message: str, peer_name: str) -> None:
        """수신한 메시지를 로컬 에이전트의 자율협동 인박스로 전달하고 결과를 피드백"""
        self.get_logger().info(f"Requesting local agent to handle peer message: {message}")

        # 자율협동 인박스로 전달 (PeerMessage 서비스). 협동 루프가 비동기로 처리한다.
        response_msg = "메시지 수신"
        try:
            if not self._peer_message_client.service_is_ready():
                self.get_logger().warning("PeerMessage service not ready — dropping peer message")
                response_msg = "PeerMessage 서비스가 준비되지 않아 메시지를 거부했습니다."
            else:
                req = PeerMessage.Request()
                req.peer_name = peer_name
                req.message = message
                future = self._peer_message_client.call_async(req)
                ok, resp = self._await_future_result(
                    future,
                    timeout_sec=10.0,
                    timeout_error="협동 메시지 전달 타임아웃",
                    none_error="협동 메시지 응답 없음",
                )
                if ok and resp.success:
                    response_msg = resp.result_message or "메시지 수신"
                else:
                    response_msg = str(resp) if not ok else (resp.error_message or "수신 실패")
        except Exception as e:
            self.get_logger().error(f"PeerMessage delivery error: {e}")
            response_msg = f"수신 처리 오류: {e}"

        self.get_logger().info(f"Local agent peer-message response: {response_msg}")

        # 해당 피어의 gRPC 서버로 처리 결과를 다시 송신
        if peer_name in self._peer_queues:
            self._enqueue_peer_message(
                peer_name,
                self._authorized_peer_message(
                    peer_name,
                    pb2.ChatMessage(
                        sender_id="robot",
                        content=response_msg,
                        timestamp=int(time.time()),
                    ),
                ),
            )

    @staticmethod
    def _file_chunk_generator(file_path: str) -> Generator[pb2.FileChunk, None, None]:
        """파일을 gRPC 스트리밍 업로드용 청크로 분할하는 제너레이터"""
        filename = os.path.basename(file_path)
        yield pb2.FileChunk(
            file_id=filename,
            data=b"",
            is_last=False,
            filename=filename,
        )
        chunk_size = 64 * 1024  # 64KB
        with open(file_path, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                yield pb2.FileChunk(
                    file_id=filename,
                    data=data,
                    is_last=False,
                    filename=filename,
                )
        yield pb2.FileChunk(file_id=filename, data=b"", is_last=True, filename=filename)

    def _upload_file_to_peer(
        self, stub: "pb2_grpc.RoboMessengerStub", file_path: str, peer_name: str
    ):
        """단일 피어 stub으로 파일을 스트리밍 업로드하고 UploadResponse를 반환"""
        return stub.UploadFile(
            self._file_chunk_generator(file_path),
            metadata=self._peer_rpc_metadata(peer_name),
        )

    def _handle_send_message(self, request, response) -> Any:
        """로컬에서 상대 로봇으로 메시지/파일 전송 서비스를 요청했을 때 처리 (연결된 모든 피어에 브로드캐스트)"""
        msg = request.message
        file_path = request.file_path

        active_stubs = {}
        with self._peer_stubs_lock:
            active_stubs = dict(self._peer_stubs)

        if not active_stubs:
            response.success = False
            response.error_message = "연결된 동료 로봇 gRPC 서버가 없습니다."
            return response

        success_count = 0
        error_msgs = []

        for peer_name, stub in active_stubs.items():
            try:
                # 1. 파일 경로가 제공된 경우 파일 업로드 스트리밍
                if file_path:
                    if not os.path.exists(file_path):
                        error_msgs.append(f"[{peer_name}] 파일 없음: {file_path}")
                        continue

                    self.get_logger().info(f"[{peer_name}] Starting file transfer: {file_path}")
                    filename = os.path.basename(file_path)

                    upload_resp = self._upload_file_to_peer(stub, file_path, peer_name)
                    if upload_resp.success:
                        self.get_logger().info(
                            f"[{peer_name}] File transfer succeeded: {upload_resp.file_id}"
                        )
                        chat_msg = self._authorized_peer_message(
                            peer_name,
                            pb2.ChatMessage(
                                sender_id="robot",
                                content=msg or f"파일 업로드 완료: {filename}",
                                timestamp=int(time.time()),
                                metadata={"file_id": upload_resp.file_id, "type": "file"},
                            ),
                        )
                        if peer_name in self._peer_queues:
                            self._enqueue_peer_message(peer_name, chat_msg)
                        success_count += 1
                    else:
                        error_msgs.append(f"[{peer_name}] 파일 업로드 실패: {upload_resp.message}")

                # 2. 텍스트 메시지만 전달하는 경우
                else:
                    if not msg:
                        error_msgs.append(f"[{peer_name}] 전송할 메시지가 비어있습니다.")
                        continue

                    self.get_logger().info(f"[{peer_name}] Message send request: {msg}")
                    chat_msg = self._authorized_peer_message(
                        peer_name,
                        pb2.ChatMessage(
                            sender_id="robot",
                            content=msg,
                            timestamp=int(time.time()),
                        ),
                    )
                    cmd_resp = stub.SendCommand(
                        chat_msg, metadata=self._peer_rpc_metadata(peer_name)
                    )
                    if cmd_resp.success:
                        success_count += 1
                    else:
                        error_msgs.append(f"[{peer_name}] 메시지 전송 거절: {cmd_resp.message}")

            except Exception as e:
                self.get_logger().error(f"[{peer_name}] Error while sending message/file: {e}")
                error_msgs.append(f"[{peer_name}] 에러: {e}")

        if success_count > 0:
            response.success = True
            if error_msgs:
                response.error_message = f"일부 피어 전송 성공. 실패 내역: {'; '.join(error_msgs)}"
        else:
            response.success = False
            response.error_message = f"모든 피어 전송 실패: {'; '.join(error_msgs)}"

        return response

    def _handle_call_peer(self, request, response) -> Any:
        """이름으로 지정한 특정 동료 로봇 한 곳에만 지시/메시지를 전달 (응답 대기 옵션)"""
        peer_name = self._resolve_peer_name(request.peer_name)
        instruction = request.instruction
        file_path = request.file_path
        timeout_sec = request.timeout_sec or self._timeout

        with self._peer_stubs_lock:
            stub = self._peer_stubs.get(peer_name)

        if not stub:
            response.success = False
            response.error_message = f"연결된 피어를 찾을 수 없습니다: {peer_name}"
            return response

        if not instruction and not file_path:
            response.success = False
            response.error_message = "instruction 또는 file_path가 필요합니다."
            return response

        message_type = ""
        envelope: dict[str, Any] = {}
        if instruction.startswith('{"_robo_claw_message_type":'):
            try:
                envelope = json.loads(instruction)
                message_type = str(envelope.get("_robo_claw_message_type", ""))
                instruction = str(envelope.get("content", ""))
            except (TypeError, ValueError, json.JSONDecodeError):
                response.success = False
                response.error_message = "협동 메시지 envelope 형식이 잘못되었습니다."
                return response

        file_error = ""
        if file_path:
            if not os.path.exists(file_path):
                file_error = f"파일 없음: {file_path}"
            else:
                try:
                    self.get_logger().info(f"[{peer_name}] Starting file transfer: {file_path}")
                    upload_resp = self._upload_file_to_peer(stub, file_path, peer_name)
                    if not upload_resp.success:
                        file_error = f"파일 업로드 실패: {upload_resp.message}"
                except Exception as e:
                    file_error = f"파일 업로드 중 오류: {e}"

        if not instruction:
            response.success = not file_error
            if file_error:
                response.error_message = file_error
            else:
                response.result_message = "파일 전송 완료"
            return response

        try:
            self.get_logger().info(f"[{peer_name}] Calling: {instruction}")
            chat_msg = self._authorized_peer_message(
                peer_name,
                pb2.ChatMessage(
                    sender_id=self._sender_id if message_type == "peer_cooperation" else "robot",
                    content=instruction,
                    timestamp=int(time.time()),
                    metadata={"type": message_type} if message_type else {},
                ),
            )
            if message_type == "peer_cooperation":
                chat_msg.metadata["message_id"] = uuid.uuid4().hex
                chat_msg.metadata["task_id"] = str(envelope.get("task_id", ""))
                chat_msg.metadata["created_at"] = str(envelope.get("created_at", "0"))
                chat_msg.metadata["expires_at"] = str(envelope.get("expires_at", "0"))

            if request.wait_for_result:
                cmd_resp = stub.SendCommand(
                    chat_msg,
                    timeout=timeout_sec,
                    metadata=self._peer_rpc_metadata(peer_name),
                )
                response.success = cmd_resp.success
                response.result_message = cmd_resp.message
            else:
                peer_queue = self._peer_queues.get(peer_name)
                if peer_queue is None:
                    response.success = False
                    response.error_message = f"피어 큐를 찾을 수 없습니다: {peer_name}"
                    return response
                response.success = self._enqueue_peer_message(peer_name, chat_msg)
                if response.success:
                    response.result_message = "메시지 전달됨(응답 대기 안 함)"
                else:
                    response.error_message = "동료 메시지 큐가 가득 찼습니다."

            if file_error:
                response.error_message = file_error

        except grpc.RpcError as e:
            response.success = False
            response.error_message = f"gRPC 호출 실패: {e}"
        except Exception as e:
            response.success = False
            response.error_message = f"동료 로봇 호출 중 오류: {e}"

        return response

    def _enqueue_peer_message(self, peer_name: str, message: pb2.ChatMessage) -> bool:
        if "auth_token" not in message.metadata:
            message = self._authorized_peer_message(peer_name, message)
        peer_queue = self._peer_queues.get(peer_name)
        if peer_queue is None:
            return False
        try:
            peer_queue.put_nowait(message)
            return True
        except queue.Full:
            try:
                peer_queue.get_nowait()
                peer_queue.put_nowait(message)
                self.get_logger().warning(
                    f"[{peer_name}] gRPC 송신 큐가 가득 차 오래된 메시지를 폐기했습니다."
                )
                return True
            except queue.Empty:
                return False

    def _handle_list_peers(self, request, response) -> Any:
        """target_peers_json으로 설정된 동료 로봇 목록과 현재 연결 상태를 반환"""
        with self._peer_stubs_lock:
            connected_names = set(self._peer_stubs.keys())

        response.peers = [
            PeerInfo(
                peer_name=self._peer_display_name(peer),
                host=str(peer.get("host") or ""),
                port=int(peer.get("port") or 0),
                connected=self._peer_display_name(peer) in connected_names,
            )
            for peer in self._peers
        ]
        response.success = True
        return response

    def _wait_for_future(self, future: Any, timeout_sec: float) -> bool:
        return wait_for_future(future, timeout_sec)

    def _wait_for_ready(self, wait_fn: Any, timeout_sec: float, error: str) -> str | None:
        return wait_for_ready(wait_fn, timeout_sec, error)

    def _await_future_result(
        self, future: Any, timeout_sec: float, timeout_error: str, none_error: str
    ) -> tuple[bool, Any]:
        return await_future_result(future, timeout_sec, timeout_error, none_error)

    def send_task(self, instruction: str, context_json: str = "") -> dict[str, Any]:
        """ExecuteTask 액션 전송 후 결과 반환 (공통 send_execute_task 사용)"""
        response = send_execute_task(self._task_client, instruction, context_json, self._timeout)
        if not response.get("success"):
            self.get_logger().error(f"send_task failed: {response.get('error')}")
        return response

    def destroy_node(self) -> None:
        self._running = False
        with self._peer_stubs_lock:
            for channel in list(self._peer_channels.values()):
                channel.close()
            self._peer_channels.clear()
            self._peer_stubs.clear()
        super().destroy_node()


def main(args: list | None = None) -> None:
    rclpy.init(args=args)
    node = MessengerClientNode()
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


if __name__ == "__main__":
    main()
