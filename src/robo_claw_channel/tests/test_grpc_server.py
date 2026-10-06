"""
MessengerServicer(gRPC 서버) 단위 테스트 — 핸드셰이크 메시지 필터링, 파일 업로드 검증
"""

import socket
import time
from pathlib import Path
from unittest.mock import MagicMock

import grpc
from robo_claw_channel.grpc_server import (
    HANDSHAKE_CONTENT,
    HANDSHAKE_SENDER_ID,
    GrpcServer,
    MessengerServicer,
)

from robo_claw_channel import messenger_pb2 as pb2
from robo_claw_channel import messenger_pb2_grpc as pb2_grpc


def _make_context(max_active_calls: int) -> MagicMock:
    """is_active()가 max_active_calls번 True를 반환한 뒤 False로 전환되는 context 모의 객체"""
    context = MagicMock()
    call_count = {"n": 0}

    def is_active() -> bool:
        call_count["n"] += 1
        return call_count["n"] <= max_active_calls

    context.is_active.side_effect = is_active
    return context


class TestMessengerServicerChatStream:
    def test_peer_token_is_bound_to_sender_id(self):
        servicer = MessengerServicer(
            lambda text: text,
            peer_auth_tokens={"butler": "butler-token"},
        )
        valid = pb2.ChatMessage(sender_id="butler", metadata={"auth_token": "butler-token"})
        forged = pb2.ChatMessage(sender_id="former", metadata={"auth_token": "butler-token"})
        assert servicer._is_authorized(valid, None) is True
        assert servicer._is_authorized(forged, None) is False

    def test_peer_dedup_survives_servicer_restart(self, tmp_path: Path):
        db_path = str(tmp_path / "dedup.sqlite3")
        request = pb2.ChatMessage(
            sender_id="butler",
            metadata={
                "message_id": "persistent-message",
                "task_id": "persistent-task",
                "expires_at": str(time.time() + 60),
            },
        )
        first = MessengerServicer(lambda text: text, dedup_path=db_path)
        second = MessengerServicer(lambda text: text, dedup_path=db_path)
        try:
            assert first._is_duplicate(request) is False
            assert second._is_duplicate(request) is True
        finally:
            first._dedup_db.close()
            second._dedup_db.close()

    def test_peer_cooperation_message_uses_peer_callback(self):
        received = []

        def on_message_cb(text: str) -> str:
            raise AssertionError(f"일반 작업 경로로 라우팅됨: {text}")

        def on_peer_message_cb(sender_id: str, text: str):
            received.append((sender_id, text))
            return True, "인박스 수신", ""

        servicer = MessengerServicer(on_message_cb, on_peer_message_cb)

        def request_iter():
            yield pb2.ChatMessage(
                sender_id="butler",
                content="도와줘",
                timestamp=0,
                metadata={
                    "type": "peer_cooperation",
                    "expires_at": str(time.time() + 30),
                },
            )

        responses = list(servicer.ChatStream(request_iter(), _make_context(5)))

        assert received == [("butler", "도와줘")]
        assert len(responses) == 1
        assert responses[0].sender_id == "robot"

    def test_handshake_message_not_forwarded_to_agent(self):
        """client_node의 'client_connected' 핸드셰이크는 on_message_cb로 전달되지 않아야 한다"""
        received = []

        def on_message_cb(text: str) -> str:
            received.append(text)
            return f"echo:{text}"

        servicer = MessengerServicer(on_message_cb)

        def request_iter():
            yield pb2.ChatMessage(
                sender_id=HANDSHAKE_SENDER_ID, content=HANDSHAKE_CONTENT, timestamp=0
            )
            yield pb2.ChatMessage(sender_id="robot", content="실제 메시지", timestamp=0)

        responses = list(servicer.ChatStream(request_iter(), _make_context(5)))

        assert received == ["실제 메시지"]
        assert len(responses) == 1
        assert responses[0].content == "echo:실제 메시지"

    def test_stream_message_with_invocation_metadata_token_is_authorized(self):
        """ChatStream 호출 metadata(x-robo-claw-peer-token)로도 스트림 메시지를 인증한다.

        client_node는 회신/일반 메시지에도 토큰을 실어 보내므로, 스트림 레벨 토큰을
        무시하면 협동 회신이 '협동 메시지 인증 실패'로 버려진다.
        """
        received = []

        def on_message_cb(text: str) -> str:
            received.append(text)
            return f"echo:{text}"

        servicer = MessengerServicer(on_message_cb, peer_auth_token="shared-token")
        context = _make_context(5)
        context.invocation_metadata.return_value = (("x-robo-claw-peer-token", "shared-token"),)

        def request_iter():
            yield pb2.ChatMessage(sender_id="robot", content="처리 결과 회신", timestamp=0)

        responses = list(servicer.ChatStream(request_iter(), context))

        assert received == ["처리 결과 회신"]
        assert responses[0].content == "echo:처리 결과 회신"

    def test_stream_message_uses_invocation_peer_id_for_per_peer_token(self):
        """sender_id='robot'인 회신도 invocation metadata의 peer id로 피어별 토큰을 검증한다."""
        servicer = MessengerServicer(
            lambda text: text,
            peer_auth_tokens={"Stretch3": "stretch-token"},
        )
        context = _make_context(5)
        context.invocation_metadata.return_value = (
            ("x-robo-claw-peer-token", "stretch-token"),
            ("x-robo-claw-peer-id", "Stretch3"),
        )

        def request_iter():
            yield pb2.ChatMessage(sender_id="robot", content="처리 결과 회신", timestamp=0)

        responses = list(servicer.ChatStream(request_iter(), context))

        assert responses[0].content == "처리 결과 회신"

    def test_stream_message_with_wrong_token_is_rejected_with_general_auth_error(self):
        """일반 채팅 메시지는 '협동'이 아닌 일반 gRPC 메시지 인증 실패로 응답한다."""
        servicer = MessengerServicer(lambda text: text, peer_auth_token="shared-token")
        context = _make_context(5)
        context.invocation_metadata.return_value = (("x-robo-claw-peer-token", "wrong"),)

        def request_iter():
            yield pb2.ChatMessage(sender_id="user", content="일반 채팅 명령", timestamp=0)

        responses = list(servicer.ChatStream(request_iter(), context))

        assert responses[0].content == "gRPC 메시지 인증 실패 (인증 토큰 누락 또는 불일치)"
        assert responses[0].metadata.get("type") == "error"
        assert responses[0].metadata.get("error_code") == "AUTH_FAILED"

    def test_stream_peer_cooperation_with_wrong_token_is_rejected_with_coop_error(self):
        """협동 메시지(type=peer_cooperation)는 '협동 메시지 인증 실패'로 구분되어 응답한다."""
        servicer = MessengerServicer(lambda text: text, peer_auth_token="shared-token")
        context = _make_context(5)
        context.invocation_metadata.return_value = (("x-robo-claw-peer-token", "wrong"),)

        def request_iter():
            yield pb2.ChatMessage(
                sender_id="Stretch3",
                content="도와줘",
                timestamp=0,
                metadata={"type": "peer_cooperation"},
            )

        responses = list(servicer.ChatStream(request_iter(), context))

        assert responses[0].content == "협동 메시지 인증 실패"
        assert responses[0].metadata.get("type") == "error"
        assert responses[0].metadata.get("error_code") == "AUTH_FAILED"

    def test_unauthorized_warning_identifies_sender_and_token_state(self, caplog):
        """거부 경고만으로 원인(토큰 미제출/미설정)을 판별할 수 있어야 한다."""
        servicer = MessengerServicer(lambda text: text, peer_auth_token="shared-token")

        def request_iter():
            yield pb2.ChatMessage(sender_id="Stretch3", content="도와줘", timestamp=0)

        with caplog.at_level("WARNING"):
            list(servicer.ChatStream(request_iter(), _make_context(5)))

        warning = " ".join(
            r.getMessage() for r in caplog.records if "Unauthorized" in r.getMessage()
        )
        assert "sender=Stretch3" in warning
        assert "token_supplied=no" in warning
        assert "token_expected=yes" in warning
        assert "reason=token_missing" in warning

    def test_unauthorized_warning_reports_missing_expected_token_for_unknown_peer(self, caplog):
        """피어별 토큰 맵에 없는 sender는 바인딩 실패로 구분되어야 한다."""
        servicer = MessengerServicer(
            lambda text: text,
            peer_auth_tokens={"Stretch3": "stretch-token"},
        )

        def request_iter():
            yield pb2.ChatMessage(
                sender_id="Butler2",
                content="도와줘",
                timestamp=0,
                metadata={"auth_token": "stretch-token"},
            )

        with caplog.at_level("WARNING"):
            list(servicer.ChatStream(request_iter(), _make_context(5)))

        warning = " ".join(
            r.getMessage() for r in caplog.records if "Unauthorized" in r.getMessage()
        )
        assert "sender=Butler2" in warning
        assert "token_supplied=yes" in warning
        assert "token_expected=no" in warning
        assert "reason=unknown_peer_no_token" in warning

    def test_peer_auth_tokens_falls_back_to_shared_token_when_peer_not_in_map(self):
        """피어별 맵에 해당 피어가 없더라도 공통 토큰이 있으면 인증에 성공한다."""
        servicer = MessengerServicer(
            lambda text: text,
            peer_auth_token="common-fallback-token",
            peer_auth_tokens={"Stretch3": "stretch-token"},
        )
        # 맵에 없는 Butler2 로봇이 공통 토큰으로 인증 요청
        req = pb2.ChatMessage(
            sender_id="Butler2",
            content="협동하자",
            metadata={"auth_token": "common-fallback-token"},
        )
        assert servicer._is_authorized(req, None) is True

    def test_peer_auth_tokens_matches_case_insensitively(self):
        """피어 이름 대소문자가 달라도(예: Stretch3 vs stretch3) 올바르게 매칭한다."""
        servicer = MessengerServicer(
            lambda text: text,
            peer_auth_tokens={"stretch3": "stretch-token"},
        )
        req = pb2.ChatMessage(
            sender_id="Stretch3",
            content="작업 요청",
            metadata={"auth_token": "stretch-token"},
        )
        assert servicer._is_authorized(req, None) is True

    def test_send_command_unauthorized_logs_warning(self, caplog):
        """SendCommand 호출 시 인증 실패하면 경고 로그를 남긴다."""
        servicer = MessengerServicer(lambda text: text, peer_auth_token="expected-token")
        req = pb2.ChatMessage(
            sender_id="Stretch3",
            content="단발 명령",
            metadata={"auth_token": "wrong-token"},
        )
        with caplog.at_level("WARNING"):
            resp = servicer.SendCommand(req, _make_context(1))

        assert resp.success is False
        assert resp.message == "gRPC 인증 실패 (인증 토큰 누락 또는 불일치)"
        assert any(
            "Unauthorized command rejected" in r.getMessage() and "reason=token_mismatch" in r.getMessage()
            for r in caplog.records
        )

    def test_send_command_unauthorized_cooperation_message(self):
        """SendCommand 호출 시 협동 메시지 인증 실패는 '협동 메시지 인증 실패'를 반환한다."""
        servicer = MessengerServicer(lambda text: text, peer_auth_token="expected-token")
        req = pb2.ChatMessage(
            sender_id="Stretch3",
            content="단발 명령",
            metadata={"auth_token": "wrong-token", "type": "peer_cooperation"},
        )
        resp = servicer.SendCommand(req, _make_context(1))
        assert resp.success is False
        assert resp.message == "협동 메시지 인증 실패"

    def test_user_app_with_peer_token_is_authorized_when_peer_map_configured(self):
        """채팅앱(sender_id='user')이 peer_auth_tokens에 등록된 유효 토큰을 보내면 승인된다."""
        servicer = MessengerServicer(
            lambda text: text,
            peer_auth_tokens={"butler": "butler-secret-token"},
        )
        req = pb2.ChatMessage(
            sender_id="user",
            content="안녕하세요",
            metadata={"auth_token": "butler-secret-token"},
        )
        assert servicer._is_authorized(req, None) is True

    def test_rpc_error_summary_never_logs_empty_message(self):
        """스트림 종료 경고에 code/details가 남아야 한다(빈 메시지 로그 방지)."""
        from robo_claw_channel.grpc_server import rpc_error_summary

        bare = grpc.RpcError()
        summary = rpc_error_summary(bare)

        assert "code=" in summary
        assert summary != ""

    def test_stream_error_warning_uses_rpc_error_summary(self, caplog):
        """수신 스트림에서 RpcError가 나면 code/details를 포함해 기록한다."""
        servicer = MessengerServicer(lambda text: text)

        class _BrokenStream:
            def __iter__(self):
                return self

            def __next__(self):
                raise grpc.RpcError()

        with caplog.at_level("WARNING"):
            list(servicer.ChatStream(_BrokenStream(), _make_context(5)))

        assert any("gRPC receive stream error" in r.getMessage() for r in caplog.records)
        assert any("code=" in r.getMessage() for r in caplog.records)

    def test_real_message_with_same_sender_id_but_different_content_is_forwarded(self):
        """sender_id만 우연히 같고 내용이 다르면 정상적으로 에이전트에 전달돼야 한다"""
        received = []

        def on_message_cb(text: str) -> str:
            received.append(text)
            return f"echo:{text}"

        servicer = MessengerServicer(on_message_cb)

        def request_iter():
            yield pb2.ChatMessage(
                sender_id=HANDSHAKE_SENDER_ID, content="진짜 지시사항", timestamp=0
            )

        responses = list(servicer.ChatStream(request_iter(), _make_context(5)))

        assert received == ["진짜 지시사항"]
        assert len(responses) == 1
        assert responses[0].content == "echo:진짜 지시사항"


class TestMessengerServicerUploadFile:
    def _make_servicer(self, tmp_path: Path) -> MessengerServicer:
        servicer = MessengerServicer(lambda x: x)
        servicer._file_directory = str(tmp_path)
        return servicer

    def test_upload_writes_file_to_file_directory(self, tmp_path: Path):
        servicer = self._make_servicer(tmp_path)

        chunks = [
            pb2.FileChunk(file_id="test.txt", data=b"", is_last=False, filename="test.txt"),
            pb2.FileChunk(file_id="test.txt", data=b"hello ", is_last=False, filename="test.txt"),
            pb2.FileChunk(file_id="test.txt", data=b"world", is_last=False, filename="test.txt"),
            pb2.FileChunk(file_id="test.txt", data=b"", is_last=True, filename="test.txt"),
        ]

        resp = servicer.UploadFile(iter(chunks), MagicMock())

        assert resp.success is True
        assert resp.file_id == "test.txt"
        assert (tmp_path / "test.txt").read_bytes() == b"hello world"

    def test_upload_sanitizes_path_traversal_filename(self, tmp_path: Path):
        servicer = self._make_servicer(tmp_path)

        chunks = [
            pb2.FileChunk(
                file_id="../../etc/passwd",
                data=b"",
                is_last=False,
                filename="../../etc/passwd",
            ),
            pb2.FileChunk(file_id="../../etc/passwd", data=b"pwned", is_last=True, filename=""),
        ]

        resp = servicer.UploadFile(iter(chunks), MagicMock())

        assert resp.success is True
        assert resp.file_id == "passwd"
        assert (tmp_path / "passwd").read_bytes() == b"pwned"
        assert not (tmp_path / ".." / ".." / "etc" / "passwd").exists()

    def test_upload_empty_stream_returns_failure(self, tmp_path: Path):
        servicer = self._make_servicer(tmp_path)

        resp = servicer.UploadFile(iter([]), MagicMock())

        assert resp.success is False

    def test_upload_rejects_file_over_limit(self, tmp_path: Path):
        servicer = MessengerServicer(lambda x: x, max_file_bytes=4)
        servicer._file_directory = str(tmp_path)
        chunks = [
            pb2.FileChunk(filename="large.bin", data=b"12345", is_last=True),
        ]
        resp = servicer.UploadFile(iter(chunks), MagicMock())
        assert resp.success is False
        assert "크기 제한" in resp.message
        assert not (tmp_path / "large.bin").exists()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_two_grpc_servers_exchange_peer_messages(tmp_path: Path):
    """ROS 없이 두 실제 gRPC 서버를 연결해 peer 메시지 왕복을 검증한다."""
    received_a: list[tuple[str, str]] = []
    received_b: list[tuple[str, str]] = []
    server_a = GrpcServer(
        _free_port(),
        lambda text: f"general-a:{text}",
        lambda sender, text: received_a.append((sender, text)) or (True, "a-ack", ""),
        dedup_path=str(tmp_path / "a.sqlite3"),
    )
    server_b = GrpcServer(
        _free_port(),
        lambda text: f"general-b:{text}",
        lambda sender, text: received_b.append((sender, text)) or (True, "b-ack", ""),
        dedup_path=str(tmp_path / "b.sqlite3"),
    )
    server_a.start()
    server_b.start()
    channel_a = grpc.insecure_channel(f"127.0.0.1:{server_a._port}")
    channel_b = grpc.insecure_channel(f"127.0.0.1:{server_b._port}")
    try:
        grpc.channel_ready_future(channel_a).result(timeout=2.0)
        grpc.channel_ready_future(channel_b).result(timeout=2.0)
        stub_a = pb2_grpc.RoboMessengerStub(channel_a)
        stub_b = pb2_grpc.RoboMessengerStub(channel_b)
        metadata = {
            "type": "peer_cooperation",
            "message_id": "message-a-1",
            "task_id": "task-a-1",
            "created_at": str(time.time()),
            "expires_at": str(time.time() + 30),
        }
        response_b = stub_b.SendCommand(
            pb2.ChatMessage(sender_id="robot-a", content="도와줘", metadata=metadata)
        )
        response_a = stub_a.SendCommand(
            pb2.ChatMessage(
                sender_id="robot-b",
                content="응답할게",
                metadata={**metadata, "message_id": "message-b-1", "task_id": "task-b-1"},
            )
        )
        assert response_b.success is True
        assert response_a.success is True
        assert received_b == [("robot-a", "도와줘")]
        assert received_a == [("robot-b", "응답할게")]
    finally:
        channel_a.close()
        channel_b.close()
        server_a.stop()
        server_b.stop()


def test_grpc_server_bind_host_configuration():
    default_server = GrpcServer(
        _free_port(),
        lambda text: text,
    )
    assert default_server._bind_host == "127.0.0.1"

    custom_server = GrpcServer(
        _free_port(),
        lambda text: text,
        bind_host="0.0.0.0",
    )
    assert custom_server._bind_host == "0.0.0.0"
