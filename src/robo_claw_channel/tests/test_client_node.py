"""
MessengerClientNode 단위 테스트 (ROS2 없이 로직 검증)
"""

import pytest

pytestmark = pytest.mark.ros2

import queue
import threading
from unittest.mock import MagicMock, patch

from robo_claw_channel.client_node import MessengerClientNode

from robo_claw_channel import messenger_pb2 as pb2


class TestMessengerClientNodeUnit:
    """MessengerClientNode 내부 메서드 단위 검증"""

    def setup_method(self):
        """ROS2 초기화 없이 노드 속성만 모킹"""
        # Node.__init__ 및 ActionClient, Service 생성 모킹
        with (
            patch("rclpy.node.Node.declare_parameter"),
            patch("rclpy.node.Node.get_parameter") as mock_get_param,
            patch("rclpy.action.ActionClient"),
            patch("rclpy.node.Node.create_service"),
        ):
            # get_parameter 기본 반환값 설정
            mock_get_param.return_value.get_parameter_value.return_value.string_value = (
                "robo_claw_agent_node"
            )
            mock_get_param.return_value.get_parameter_value.return_value.integer_value = 50051
            mock_get_param.return_value.get_parameter_value.return_value.double_value = 60.0

            # __init__ 호출 우회 및 Node 생성
            with patch.object(MessengerClientNode, "__init__", return_value=None):
                self.node = MessengerClientNode.__new__(MessengerClientNode)
                self.node._target_host = "127.0.0.1"
                self.node._target_port = 50051
                self.node._agent_name = "robo_claw_agent_node"
                self.node._timeout = 60.0
                self.node._agent_ns = "/robo_claw_agent_node"
                self.node._task_client = MagicMock()
                self.node._send_queue = queue.Queue()
                self.node._peer_queues = {"127.0.0.1_50051": self.node._send_queue}
                self.node._peer_stubs = {}
                self.node._peer_stubs_lock = threading.Lock()
                self.node._peer_channels = {}
                self.node._peers = []
                self.node._running = True

                # get_logger() 모킹
                mock_logger = MagicMock()
                self.node.get_logger = MagicMock(return_value=mock_logger)

    def test_imports(self):
        """패키지 임포트 검증"""
        from robo_claw_channel.client_node import MessengerClientNode, main

        assert MessengerClientNode is not None
        assert main is not None

    def test_send_task_no_server(self):
        """로컬 에이전트 액션 서버 연결 없을 때 오류 반환 검증"""
        self.node._task_client.wait_for_server.return_value = False
        result = self.node.send_task("앞으로 1미터 이동")
        assert result["success"] is False
        assert "연결 실패" in result["error"]

    def test_process_received_message(self):
        """수신한 메시지를 협동 인박스로 전달하고 결과 메시지를 gRPC 전송 큐에 넣는지 검증"""
        self.node._peers = [
            {"agent_name": "Stretch3", "host": "1.2.3.4", "port": 50052, "auth_token": "tok"}
        ]
        self.node._peer_queues = {"Stretch3": queue.Queue()}
        peer_response = MagicMock()
        peer_response.success = True
        peer_response.result_message = "인박스 수신"
        self.node._peer_message_client = MagicMock()
        self.node._peer_message_client.service_is_ready.return_value = True
        self.node._peer_message_client.call_async.return_value = MagicMock()
        self.node._await_future_result = MagicMock(return_value=(True, peer_response))

        # 메시지 처리 수행
        self.node._process_received_message("하이 방가방가", "Stretch3")

        # gRPC 전송 큐에 "robot" 명의로 인박스 응답이 생성되었는지 검증
        assert not self.node._peer_queues["Stretch3"].empty()
        queued_msg = self.node._peer_queues["Stretch3"].get()
        assert queued_msg.sender_id == "robot"
        assert queued_msg.content == "인박스 수신"

    def test_process_received_message_attaches_peer_auth_token(self):
        """회신(sender_id='robot')에도 인증 토큰을 실어야 피어가 거부하지 않는다."""
        self.node._sender_id = "Former0047"
        self.node._peers = [
            {"agent_name": "Stretch3", "host": "1.2.3.4", "port": 50052, "auth_token": "tok"}
        ]
        self.node._peer_queues = {"Stretch3": queue.Queue()}
        peer_response = MagicMock()
        peer_response.success = True
        peer_response.result_message = "인박스 수신"
        self.node._peer_message_client = MagicMock()
        self.node._peer_message_client.service_is_ready.return_value = True
        self.node._peer_message_client.call_async.return_value = MagicMock()
        self.node._await_future_result = MagicMock(return_value=(True, peer_response))

        self.node._process_received_message("도와줘", "Stretch3")

        queued_msg = self.node._peer_queues["Stretch3"].get()
        assert queued_msg.metadata["auth_token"] == "tok"

    def test_handle_send_message_attaches_peer_auth_token(self):
        """broadcast_to_peers(SendMessage) 경로도 토큰 없이 보내면 피어가 거부한다."""
        self.node._sender_id = "Former0047"
        stub = MagicMock()
        stub.SendCommand.return_value = pb2.CommandResponse(success=True, message="완료")
        self.node._peer_stubs = {"Stretch3": stub}
        self.node._peers = [
            {"agent_name": "Stretch3", "host": "1.2.3.4", "port": 50052, "auth_token": "tok"}
        ]
        request = MagicMock()
        request.message = "로봇아 안녕"
        request.file_path = ""

        res = self.node._handle_send_message(request, MagicMock())

        assert res.success is True
        sent_msg = stub.SendCommand.call_args.args[0]
        assert sent_msg.metadata["auth_token"] == "tok"
        assert ("x-robo-claw-peer-token", "tok") in stub.SendCommand.call_args.kwargs["metadata"]

    def test_handle_call_peer_plain_message_attaches_peer_auth_token(self):
        """협동 envelope가 아닌 call_peer_robot 메시지도 토큰을 실어 보낸다."""
        self.node._sender_id = "Former0047"
        stub = MagicMock()
        stub.SendCommand.return_value = pb2.CommandResponse(success=True, message="완료")
        self.node._peer_stubs = {"Stretch3": stub}
        self.node._peers = [
            {"agent_name": "Stretch3", "host": "1.2.3.4", "port": 50052, "auth_token": "tok"}
        ]
        request = self._make_call_peer_request(peer_name="Stretch3", wait_for_result=True)

        res = self.node._handle_call_peer(request, MagicMock())

        assert res.success is True
        sent_msg = stub.SendCommand.call_args.args[0]
        assert sent_msg.metadata["auth_token"] == "tok"
        assert ("x-robo-claw-peer-token", "tok") in stub.SendCommand.call_args.kwargs["metadata"]

    def test_grpc_peer_loop_passes_invocation_metadata_to_chat_stream(self):
        """ChatStream 연결에도 스트림 레벨 인증 metadata를 전달한다."""
        import robo_claw_channel.client_node as client_module

        recorded: dict[str, tuple] = {}
        self.node._sender_id = "Former0047"
        peer = {"agent_name": "Stretch3", "host": "127.0.0.1", "port": 50052, "auth_token": "tok"}
        self.node._peers = [peer]
        self.node._peer_queues = {"Stretch3": queue.Queue()}
        self.node._running = True
        node = self.node

        class _FakeStub:
            def ChatStream(self, request_iterator, metadata=()):
                recorded["metadata"] = tuple(metadata)
                node._running = False
                return iter(())

        ready_future = MagicMock()
        ready_future.result.return_value = True
        with (
            patch.object(client_module.grpc, "insecure_channel", return_value=MagicMock()),
            patch.object(client_module.grpc, "channel_ready_future", return_value=ready_future),
            patch.object(client_module.pb2_grpc, "RoboMessengerStub", return_value=_FakeStub()),
        ):
            self.node._grpc_peer_connection_loop(peer)

        assert ("x-robo-claw-peer-token", "tok") in recorded["metadata"]
        assert ("x-robo-claw-peer-id", "Former0047") in recorded["metadata"]

    def test_handle_send_message_no_connection(self):
        """gRPC 연결이 안 되어 있을 때 로컬 송신 서비스 호출 시 에러 메시지 반환 검증"""
        request = MagicMock()
        request.message = "로봇아 안녕"
        request.file_path = ""
        response = MagicMock()

        self.node._peer_stubs = {}
        res = self.node._handle_send_message(request, response)

        assert res.success is False
        assert "연결된 동료 로봇" in res.error_message

    def _make_call_peer_request(self, **overrides):
        request = MagicMock()
        request.peer_name = overrides.get("peer_name", "robot_a")
        request.instruction = overrides.get("instruction", "앞으로 1미터 이동해")
        request.file_path = overrides.get("file_path", "")
        request.wait_for_result = overrides.get("wait_for_result", True)
        request.timeout_sec = overrides.get("timeout_sec", 0.0)
        return request

    def test_handle_call_peer_unknown_peer(self):
        """연결되지 않은 피어를 호출하면 에러를 반환하는지 검증"""
        self.node._peer_stubs = {}
        request = self._make_call_peer_request(peer_name="robot_a")
        response = MagicMock()

        res = self.node._handle_call_peer(request, response)

        assert res.success is False
        assert "robot_a" in res.error_message

    def test_handle_call_peer_wait_for_result(self):
        """wait_for_result=True면 SendCommand를 동기 호출하고 실제 응답을 반환하는지 검증"""
        stub = MagicMock()
        stub.SendCommand.return_value = pb2.CommandResponse(success=True, message="작업 완료")
        self.node._peer_stubs = {"robot_a": stub}

        request = self._make_call_peer_request(peer_name="robot_a", wait_for_result=True)
        response = MagicMock()

        res = self.node._handle_call_peer(request, response)

        assert res.success is True
        assert res.result_message == "작업 완료"
        stub.SendCommand.assert_called_once()

    def test_handle_call_peer_resolves_sender_id_to_single_configured_peer(self):
        """원격 sender_id가 달라도 1:1 설정의 실제 peer 이름으로 회신한다."""
        stub = MagicMock()
        stub.SendCommand.return_value = pb2.CommandResponse(success=True, message="응답")
        self.node._peer_stubs = {"Former0047": stub}
        self.node._peers = [{"agent_name": "Former0047", "agent_id": "robo_claw_agent"}]

        request = self._make_call_peer_request(peer_name="robo_claw_agent", wait_for_result=True)
        response = MagicMock()

        res = self.node._handle_call_peer(request, response)

        assert res.success is True
        stub.SendCommand.assert_called_once()

    def test_handle_call_peer_fire_and_forget(self):
        """wait_for_result=False면 응답을 기다리지 않고 큐에 넣은 뒤 즉시 반환하는지 검증"""
        stub = MagicMock()
        self.node._peer_stubs = {"robot_a": stub}
        self.node._peer_queues = {"robot_a": queue.Queue()}

        request = self._make_call_peer_request(peer_name="robot_a", wait_for_result=False)
        response = MagicMock()

        res = self.node._handle_call_peer(request, response)

        assert res.success is True
        stub.SendCommand.assert_not_called()
        assert not self.node._peer_queues["robot_a"].empty()
        queued_msg = self.node._peer_queues["robot_a"].get()
        assert queued_msg.content == "앞으로 1미터 이동해"

    def test_handle_list_peers_no_peers(self):
        """설정된 피어가 없으면 빈 목록을 반환하는지 검증"""
        self.node._peers = []
        request = MagicMock()
        response = MagicMock()

        res = self.node._handle_list_peers(request, response)

        assert res.success is True
        assert res.peers == []

    def test_handle_list_peers_connected_flag(self):
        """설정된 피어 중 실제로 연결된 것만 connected=True로 표시되는지 검증"""
        self.node._peers = [
            {"agent_name": "robot_a", "host": "192.168.1.10", "port": 50051},
            {"agent_name": "robot_b", "host": "192.168.1.11", "port": 50051},
        ]
        self.node._peer_stubs = {"robot_a": MagicMock()}
        request = MagicMock()
        response = MagicMock()

        res = self.node._handle_list_peers(request, response)

        assert res.success is True
        by_name = {p.peer_name: p for p in res.peers}
        assert by_name["robot_a"].connected is True
        assert by_name["robot_a"].host == "192.168.1.10"
        assert by_name["robot_a"].port == 50051
        assert by_name["robot_b"].connected is False

    def test_warn_duplicate_peer_names_logs_warning(self):
        """중복된 peer_name이 있으면 경고 로그를 남기는지 검증"""
        peers = [
            {"agent_name": "robot_a", "host": "1.2.3.4", "port": 1},
            {"agent_name": "robot_a", "host": "5.6.7.8", "port": 2},
        ]

        self.node._warn_duplicate_peer_names(peers)

        self.node.get_logger().warning.assert_called_once()
        assert "robot_a" in self.node.get_logger().warning.call_args[0][0]

    def test_warn_duplicate_peer_names_no_warning_when_unique(self):
        """모든 peer_name이 고유하면 경고 로그가 없어야 하는지 검증"""
        peers = [
            {"agent_name": "robot_a", "host": "1.2.3.4", "port": 1},
            {"agent_name": "robot_b", "host": "5.6.7.8", "port": 2},
        ]

        self.node._warn_duplicate_peer_names(peers)

        self.node.get_logger().warning.assert_not_called()

    def test_peer_auth_token_falls_back_to_common_token(self):
        """피어 설정에 auth_token이 없어도 공통 grpc_peer_token이 있으면 반환한다."""
        self.node._common_peer_token = "shared-secret"
        self.node._peer_tokens_map = {}
        self.node._peers = [
            {"agent_name": "Stretch3", "host": "192.168.50.64", "port": 50052}
        ]

        token = self.node._peer_auth_token("Stretch3")
        assert token == "shared-secret"

    def test_peer_auth_token_uses_peer_tokens_map(self):
        """grpc_peer_tokens_json에 등록된 특정 피어의 토큰을 대소문자 무관하게 사용한다."""
        self.node._common_peer_token = "shared-secret"
        self.node._peer_tokens_map = {"stretch3": "stretch-specific-token"}
        self.node._peers = [
            {"agent_name": "Stretch3", "host": "192.168.50.64", "port": 50052}
        ]

        token = self.node._peer_auth_token("Stretch3")
        assert token == "stretch-specific-token"

    def test_peer_auth_token_prefers_peer_dict_token(self):
        """피어 dict의 auth_token이 맵이나 공통 토큰보다 최우선 적용된다."""
        self.node._common_peer_token = "shared-secret"
        self.node._peer_tokens_map = {"Stretch3": "map-token"}
        self.node._peers = [
            {"agent_name": "Stretch3", "host": "1.2.3.4", "port": 50052, "auth_token": "dict-token"}
        ]

        token = self.node._peer_auth_token("Stretch3")
        assert token == "dict-token"

    def test_enqueue_peer_message_ensures_auth_token(self):
        """_enqueue_peer_message 호출 시 메시지에 auth_token이 없으면 자동으로 부착한다."""
        self.node._common_peer_token = "shared-secret"
        self.node._peer_tokens_map = {}
        self.node._peers = [
            {"agent_name": "Stretch3", "host": "192.168.50.64", "port": 50052}
        ]
        self.node._peer_queues = {"Stretch3": queue.Queue()}

        raw_msg = pb2.ChatMessage(sender_id="robot", content="알림", timestamp=100)
        self.node._enqueue_peer_message("Stretch3", raw_msg)

        queued = self.node._peer_queues["Stretch3"].get()
        assert queued.metadata["auth_token"] == "shared-secret"
