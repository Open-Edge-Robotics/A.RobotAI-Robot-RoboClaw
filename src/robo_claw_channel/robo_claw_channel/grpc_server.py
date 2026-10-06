import hmac
import logging
import os
import queue
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent import futures

import grpc

# task proto 태스크(generate_protos.sh)로 생성된 파일들을 임포트
from . import messenger_pb2 as pb2
from . import messenger_pb2_grpc as pb2_grpc
from .task_result import unpack_callback_result

logger = logging.getLogger(__name__)

# MessengerClientNode가 ChatStream 연결 직후 보내는 핸드셰이크 메시지 식별자.
# 에이전트의 ExecuteTask로 전달하면 안 되는 순수 연결 확인용 메시지이므로 별도 필터링한다.
HANDSHAKE_SENDER_ID = "client_node"
HANDSHAKE_CONTENT = "client_connected"
DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
DEFAULT_DEDUP_PATH = "/tmp/robo_claw_peer_dedup.sqlite3"


def rpc_error_summary(exc: grpc.RpcError) -> str:
    """RpcError의 code/details를 안전하게 요약한다.

    str(exc)가 빈 문자열인 종료 상황(클라이언트 연결 끊김 등)에서도 로그만으로
    원인을 구분할 수 있게 한다. code()/details()를 제공하지 않는 예외도 방어한다.
    """
    try:
        code = str(exc.code())
    except Exception:
        code = type(exc).__name__
    try:
        details = str(exc.details() or "-")
    except Exception:
        details = "-"
    return f"code={code} details={details}"


class MessengerServicer(pb2_grpc.RoboMessengerServicer):
    """gRPC 서비스 로직 정의"""

    def __init__(
        self,
        on_message_cb: Callable[[str], str],
        on_peer_message_cb: Callable[[str, str], object] | None = None,
        peer_auth_token: str = "",
        peer_auth_tokens: dict[str, str] | None = None,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        dedup_path: str = DEFAULT_DEDUP_PATH,
    ):
        self._on_message_cb = on_message_cb
        self._on_peer_message_cb = on_peer_message_cb
        self._peer_auth_token = peer_auth_token
        self._peer_auth_tokens = peer_auth_tokens or {}
        self._max_file_bytes = max(1, int(max_file_bytes))
        self._dedup_db = sqlite3.connect(dedup_path, check_same_thread=False)
        self._dedup_db.execute(
            "CREATE TABLE IF NOT EXISTS peer_dedup (key TEXT PRIMARY KEY, expires REAL NOT NULL)"
        )
        self._dedup_db.commit()
        self._dedup_lock = threading.Lock()
        self._active_streams: list[queue.Queue] = []
        self._file_directory = "/tmp/robo_claw_files"
        os.makedirs(self._file_directory, exist_ok=True)

    def ChatStream(self, request_iterator, context):
        """양방향 스트리밍 대화"""
        # 클라이언트에게 보낼 메시지 큐 생성
        msg_queue = queue.Queue()
        self._active_streams.append(msg_queue)

        logger.info("New gRPC ChatStream connection established")

        try:
            # 수신 스레드와 송신 스레드를 동시에 처리하기 위한 제너레이터
            def request_processor():
                try:
                    for req in request_iterator:
                        if (
                            req.sender_id == HANDSHAKE_SENDER_ID
                            and req.content == HANDSHAKE_CONTENT
                        ):
                            logger.info(
                                "[gRPC handshake] Peer connection confirmation message received (not forwarded to agent)"
                            )
                            continue

                        logger.info("[gRPC received] %s", req.content)
                        if not self._is_authorized(req, context):
                            logger.warning(
                                "[gRPC] Unauthorized message rejected [%s]",
                                self._auth_failure_detail(req, context),
                            )
                            is_coop = req.metadata.get("type", "") == "peer_cooperation"
                            error_content = (
                                "협동 메시지 인증 실패"
                                if is_coop
                                else "gRPC 메시지 인증 실패 (인증 토큰 누락 또는 불일치)"
                            )
                            msg_queue.put(
                                pb2.ChatMessage(
                                    sender_id="robot",
                                    content=error_content,
                                    timestamp=int(time.time()),
                                    metadata={"type": "error", "error_code": "AUTH_FAILED"},
                                )
                            )
                            continue
                        message_type = req.metadata.get("type", "")
                        if message_type == "peer_cooperation" and self._is_expired(req):
                            msg_queue.put(
                                pb2.ChatMessage(
                                    sender_id="robot",
                                    content="만료된 협동 메시지를 무시했습니다.",
                                    timestamp=int(time.time()),
                                )
                            )
                            continue
                        if message_type == "peer_cooperation" and self._is_duplicate(req):
                            msg_queue.put(
                                pb2.ChatMessage(
                                    sender_id="robot",
                                    content="중복 협동 메시지를 무시했습니다.",
                                    timestamp=int(time.time()),
                                )
                            )
                            continue
                        if message_type == "peer_cooperation" and self._on_peer_message_cb:
                            res_result = self._on_peer_message_cb(req.sender_id, req.content)
                        else:
                            res_result = self._on_message_cb(req.content)
                        _, res_text, _ = unpack_callback_result(res_result)

                        # 로봇의 응답을 다시 클라이언트에게 전송
                        msg_queue.put(
                            pb2.ChatMessage(  # type: ignore[arg-type]
                                sender_id="robot",
                                content=res_text,
                                timestamp=int(time.time()),
                            )
                        )
                except grpc.RpcError as e:
                    logger.warning(
                        "gRPC receive stream error (connection loss expected): %s",
                        rpc_error_summary(e),
                    )
                except Exception as e:
                    logger.error("Unexpected error while processing gRPC receive: %s", e)
                finally:
                    logger.info("gRPC receive thread terminated")

            # 별도 스레드에서 수신 처리 (단순화를 위해 여기서는 루프 내에서 처리)
            # 실제 운영 환경에서는 비동기 처리가 권장됨
            rx_thread = threading.Thread(target=request_processor, daemon=True)
            rx_thread.start()

            # 큐에 쌓인 메시지를 클라이언트에게 전달
            while context.is_active():
                try:
                    msg = msg_queue.get(timeout=1.0)
                    yield msg
                except queue.Empty:
                    continue
        finally:
            self._active_streams.remove(msg_queue)
            rx_thread.join(timeout=2.0)
            logger.info("gRPC ChatStream connection closed")

    def SendCommand(self, request, context):
        """단발성 명령 전송"""
        logger.info("[gRPC SendCommand] received: %s", request.content)
        if not self._is_authorized(request, context):
            logger.warning(
                "[gRPC SendCommand] Unauthorized command rejected [%s]",
                self._auth_failure_detail(request, context),
            )
            is_coop = request.metadata.get("type", "") == "peer_cooperation"
            error_message = (
                "협동 메시지 인증 실패"
                if is_coop
                else "gRPC 인증 실패 (인증 토큰 누락 또는 불일치)"
            )
            return pb2.CommandResponse(success=False, message=error_message)
        if request.metadata.get("type", "") == "peer_cooperation" and self._is_expired(request):
            return pb2.CommandResponse(success=False, message="만료된 협동 메시지")
        if request.metadata.get("type", "") == "peer_cooperation" and self._is_duplicate(request):
            return pb2.CommandResponse(success=True, message="중복 협동 메시지를 무시했습니다.")
        if request.metadata.get("type", "") == "peer_cooperation" and self._on_peer_message_cb:
            res_result = self._on_peer_message_cb(request.sender_id, request.content)
        else:
            res_result = self._on_message_cb(request.content)
        success, res_text, result_json = unpack_callback_result(res_result)
        logger.info("[gRPC SendCommand] response (success=%s): %s", success, res_text)
        return pb2.CommandResponse(  # type: ignore[arg-type]
            success=success, message=res_text, result_json=result_json
        )

    def _auth_binding(
        self, request: pb2.ChatMessage, context: object | None
    ) -> tuple[str, str, str]:
        """인증에 쓸 (바인딩된 peer_id, 제출된 토큰, 기대 토큰)을 계산한다.

        - 제출된 토큰: 메시지 metadata['auth_token']을 우선하고, 없으면
          RPC metadata('x-robo-claw-peer-token')를 사용한다.
        - 피어 ID: sender_id가 비어있거나 'robot'인 경우 RPC metadata
          ('x-robo-claw-peer-id')로 보정한다.
        - 기대 토큰: peer_auth_tokens 맵(대소문자 무관)을 먼저 찾고, 없으면
          공통 peer_auth_token으로 fallback한다. 공통 토큰도 없을 때
          채팅앱(sender_id='user' 등)이 peer_auth_tokens 중 유효한 토큰을 제출한 경우
          해당 토큰으로 승인한다.
        """
        supplied = str(request.metadata.get("auth_token", "")).strip()
        peer_id = str(request.sender_id or "").strip()
        rpc_token = ""
        rpc_peer_id = ""
        invocation_metadata = getattr(context, "invocation_metadata", None)
        if invocation_metadata is not None:
            for key, value in invocation_metadata():
                if key == "x-robo-claw-peer-token":
                    rpc_token = str(value or "").strip()
                elif key == "x-robo-claw-peer-id":
                    rpc_peer_id = str(value or "").strip()

        if not supplied and rpc_token:
            supplied = rpc_token

        if (not peer_id or peer_id == "robot") and rpc_peer_id:
            peer_id = rpc_peer_id

        expected = ""
        if self._peer_auth_tokens:
            expected = self._peer_auth_tokens.get(peer_id, "")
            if not expected:
                norm_peer = peer_id.lower()
                for k, v in self._peer_auth_tokens.items():
                    if k.lower() == norm_peer:
                        expected = v
                        break

        # 피어별 맵에 해당 피어가 없더라도 공통 토큰이 있으면 fallback으로 허용
        if not expected and self._peer_auth_token:
            expected = self._peer_auth_token
        elif not expected and self._peer_auth_tokens and supplied and peer_id.lower() in ("user", "robo_talk"):
            # 채팅앱(sender_id='user' 등)처럼 피어 맵에 명시되지 않은 일반 발신자가
            # 등록된 유효 피어 토큰 중 하나와 일치하는 토큰을 제출한 경우 승인한다.
            for candidate in self._peer_auth_tokens.values():
                if hmac.compare_digest(supplied, candidate):
                    expected = candidate
                    break

        return peer_id, supplied, expected

    @staticmethod
    def _context_peer(context: object | None) -> str:
        """연결한 피어의 주소를 반환한다(진단용)."""
        peer_fn = getattr(context, "peer", None)
        if not callable(peer_fn):
            return "-"
        try:
            return str(peer_fn() or "-")
        except Exception:
            return "-"

    def _auth_failure_detail(self, request: pb2.ChatMessage, context: object | None) -> str:
        """인증 실패 원인을 로그 한 줄로 요약한다(토큰 값은 남기지 않는다)."""
        peer_id, supplied, expected = self._auth_binding(request, context)
        if not supplied:
            reason = "token_missing"
        elif not expected:
            reason = "unknown_peer_no_token"
        else:
            reason = "token_mismatch"

        return (
            f"sender={request.sender_id or '-'} "
            f"bound_peer_id={peer_id or '-'} "
            f"type={request.metadata.get('type', '') or '-'} "
            f"token_supplied={'yes' if supplied else 'no'} "
            f"token_expected={'yes' if expected else 'no'} "
            f"reason={reason} "
            f"peer_addr={self._context_peer(context)}"
        )

    def _is_authorized(self, request: pb2.ChatMessage, context: object | None) -> bool:
        """설정된 경우 constant-time peer token을 검증한다."""
        if not self._peer_auth_token and not self._peer_auth_tokens:
            return True
        _, supplied, expected = self._auth_binding(request, context)
        if not expected:
            return False
        return hmac.compare_digest(supplied, expected)

    def _is_duplicate(self, request: pb2.ChatMessage) -> bool:
        message_id = str(request.metadata.get("message_id", "")).strip()
        task_id = str(request.metadata.get("task_id", "")).strip()
        if not message_id and not task_id:
            return False
        now = time.time()
        expires = float(request.metadata.get("expires_at", now + 86400) or now + 86400)
        keys = [f"message:{message_id}"] if message_id else []
        if task_id:
            keys.append(f"task:{task_id}")
        with self._dedup_lock:
            self._dedup_db.execute("DELETE FROM peer_dedup WHERE expires <= ?", (now,))
            for key in keys:
                try:
                    self._dedup_db.execute(
                        "INSERT INTO peer_dedup(key, expires) VALUES (?, ?)",
                        (key, max(now + 1.0, expires)),
                    )
                except sqlite3.IntegrityError:
                    self._dedup_db.rollback()
                    return True
            self._dedup_db.commit()
        return False

    @staticmethod
    def _is_expired(request: pb2.ChatMessage) -> bool:
        try:
            expires_at = float(request.metadata.get("expires_at", "0") or "0")
        except (TypeError, ValueError):
            return True
        return expires_at <= time.time()

    def UploadFile(self, request_iterator, context):
        """파일 업로드 스트리밍 (클라이언트 -> 서버)"""
        if not self._context_authorized(context):
            return pb2.UploadResponse(success=False, message="gRPC 인증 실패")
        filename = None
        file_handle = None
        written = 0
        file_path = None
        try:
            for chunk in request_iterator:
                if filename is None:
                    filename = os.path.basename(chunk.filename or chunk.file_id)
                    if not filename:
                        return pb2.UploadResponse(  # type: ignore[arg-type]
                            success=False, file_id="", message="파일명이 없습니다."
                        )
                    file_path = os.path.join(self._file_directory, filename)
                    file_handle = open(file_path, "wb")
                if chunk.data:
                    if written + len(chunk.data) > self._max_file_bytes:
                        file_handle.close()
                        file_handle = None
                        if file_path is not None:
                            try:
                                os.remove(file_path)
                            except OSError:
                                pass
                        return pb2.UploadResponse(
                            success=False,
                            file_id="",
                            message=f"파일 크기 제한 초과: 최대 {self._max_file_bytes} bytes",
                        )
                    file_handle.write(chunk.data)
                    written += len(chunk.data)
                if chunk.is_last:
                    break

            if file_handle is None:
                return pb2.UploadResponse(  # type: ignore[arg-type]
                    success=False, file_id="", message="빈 업로드 요청입니다."
                )

            logger.info("[gRPC upload] File saved: %s (%d bytes)", filename, written)
            return pb2.UploadResponse(  # type: ignore[arg-type]
                success=True, file_id=filename, message="업로드 완료"
            )
        except Exception as e:
            logger.error("Error while processing file upload: %s", e)
            return pb2.UploadResponse(success=False, file_id="", message=str(e))  # type: ignore[arg-type]
        finally:
            if file_handle is not None:
                file_handle.close()
            if file_path is not None and written > self._max_file_bytes:
                try:
                    os.remove(file_path)
                except OSError:
                    pass

    def DownloadFile(self, request, context):
        """파일 다운로드 스트리밍 (서버 -> 클라이언트)"""
        if not self._context_authorized(context):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "gRPC 인증 실패")
            return
        file_id = request.file_id
        # 보안을 위해 경로 검증 (실제 구현 시 더 엄격하게)
        file_path = os.path.join(self._file_directory, os.path.basename(file_id))

        if not os.path.exists(file_path):
            # 파일이 없으면 /tmp에서도 찾아봄 (맵/스냅샷 저장 위치)
            file_path = os.path.join("/tmp", os.path.basename(file_id))

        if not os.path.exists(file_path):
            context.abort(grpc.StatusCode.NOT_FOUND, f"파일을 찾을 수 없습니다: {file_id}")
            return
        if os.path.getsize(file_path) > self._max_file_bytes:
            context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "파일 크기 제한 초과")
            return

        logger.info("[gRPC download] Starting file transfer: %s", file_path)

        chunk_size = 64 * 1024  # 64KB
        try:
            with open(file_path, "rb") as f:
                while True:
                    data = f.read(chunk_size)
                    if not data:
                        break
                    yield pb2.FileChunk(  # type: ignore[arg-type]
                        file_id=file_id,
                        data=data,
                        is_last=False,
                        filename=os.path.basename(file_path),
                    )
            # 마지막 청크 표시
            yield pb2.FileChunk(file_id=file_id, data=b"", is_last=True)  # type: ignore[arg-type]
            logger.info("[gRPC download] File transfer complete")
        except Exception as e:
            logger.error("Error during file transfer: %s", e)
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def _context_authorized(self, context: object) -> bool:
        """파일 RPC의 invocation metadata token을 검증한다."""
        if not self._peer_auth_token and not self._peer_auth_tokens:
            return True
        supplied = ""
        peer_id = ""
        invocation_metadata = getattr(context, "invocation_metadata", None)
        for key, value in invocation_metadata() if invocation_metadata is not None else ():
            if key == "x-robo-claw-peer-token":
                supplied = value
            elif key == "x-robo-claw-peer-id":
                peer_id = value
        if self._peer_auth_token:
            return hmac.compare_digest(supplied, self._peer_auth_token)
        expected = self._peer_auth_tokens.get(peer_id, "")
        return bool(expected) and hmac.compare_digest(supplied, expected)

    def push_message(self, text: str, metadata: dict[str, str] | None = None):
        """연결된 모든 클라이언트에게 메시지 푸시"""
        msg = pb2.ChatMessage(  # type: ignore[arg-type]
            sender_id="robot",
            content=text,
            timestamp=int(time.time()),
            metadata=metadata or {},
        )
        for q in self._active_streams:
            q.put(msg)


class GrpcServer:
    def __init__(
        self,
        port: int,
        on_message_cb: Callable[[str], str],
        on_peer_message_cb: Callable[[str, str], object] | None = None,
        peer_auth_token: str = "",
        peer_auth_tokens: dict[str, str] | None = None,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        dedup_path: str = DEFAULT_DEDUP_PATH,
        bind_host: str = "127.0.0.1",
    ):
        self._port = port
        self._bind_host = bind_host
        self._servicer = MessengerServicer(
            on_message_cb,
            on_peer_message_cb,
            peer_auth_token,
            peer_auth_tokens,
            max_file_bytes,
            dedup_path,
        )
        self._server = None

    def start(self):
        """gRPC 서버 시작"""
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        pb2_grpc.add_RoboMessengerServicer_to_server(self._servicer, self._server)
        self._server.add_insecure_port(f"{self._bind_host}:{self._port}")
        self._server.start()
        logger.info("gRPC server started [bind:%s port:%s]", self._bind_host, self._port)

    def stop(self):
        """gRPC 서버 중지"""
        if self._server:
            self._server.stop(0)
        with self._servicer._dedup_lock:
            self._servicer._dedup_db.close()

    def notify_file_available(self, file_path: str, message: str = ""):
        """파일이 준비되었음을 클라이언트들에게 알림"""
        file_id = os.path.basename(file_path)
        metadata = {
            "file_id": file_id,
            "filename": file_id,
            "type": "image" if file_path.endswith((".png", ".jpg", ".jpeg")) else "file",
        }
        self._servicer.push_message(message or f"파일이 준비되었습니다: {file_id}", metadata)
