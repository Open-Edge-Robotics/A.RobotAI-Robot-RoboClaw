import json

import pytest

pytest.importorskip("rclpy")

"""
cooperate_skill(delegate_task / call_peer_robot / list_peer_robots / broadcast_to_peers /
query_peer_status) 단위 테스트
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import robo_claw_agent.skills.cooperate_skill as cooperate_skill_module
from robo_claw_agent.skills.cooperate_skill import (
    BroadcastToPeersSkill,
    CallPeerRobotSkill,
    CoordinatePeerTaskSkill,
    ListPeerRobotsSkill,
    QueryPeerCapabilitiesSkill,
    QueryPeerStatusSkill,
    _local_wait_sec,
)


def _make_dummy_node(**extra):
    node = SimpleNamespace()
    for key, value in extra.items():
        setattr(node, key, value)
    return node


def _make_skill(skill_cls, node):
    skill = skill_cls()
    skill.set_node(node)
    return skill


def _future_with_result(result):
    future = MagicMock()

    def add_done_callback(cb):
        cb(future)

    future.add_done_callback.side_effect = add_done_callback
    future.result.return_value = result
    return future


class TestCallPeerRobotSkill:
    def test_missing_params(self):
        skill = _make_skill(CallPeerRobotSkill, _make_dummy_node())
        result = skill.execute({})

        assert result["success"] is False
        assert "peer_name" in result["message"]

    def test_no_call_peer_client_configured(self):
        skill = _make_skill(CallPeerRobotSkill, _make_dummy_node())
        result = skill.execute({"peer_name": "robot_a", "instruction": "앞으로 이동해"})

        assert result["success"] is False
        assert "피어 호출 클라이언트" in result["message"]

    def test_service_not_available(self):
        client = MagicMock()
        client.wait_for_service.return_value = False
        node = _make_dummy_node(_call_peer_client=client)

        skill = _make_skill(CallPeerRobotSkill, node)
        result = skill.execute({"peer_name": "robot_a", "instruction": "앞으로 이동해"})

        assert result["success"] is False
        assert "CallPeerRobot 서비스" in result["message"]

    def test_success_response_mapping(self):
        client = MagicMock()
        client.wait_for_service.return_value = True

        resp = SimpleNamespace(success=True, result_message="작업 완료", error_message="")
        client.call_async.return_value = _future_with_result(resp)

        node = _make_dummy_node(_call_peer_client=client)
        skill = _make_skill(CallPeerRobotSkill, node)

        result = skill.execute(
            {"peer_name": "robot_a", "instruction": "앞으로 1미터 이동해"}
        )

        assert result["success"] is True
        assert result["message"] == "작업 완료"
        assert result["peer_name"] == "robot_a"
        assert result["response"] == "작업 완료"

    def test_failure_response_mapping(self):
        client = MagicMock()
        client.wait_for_service.return_value = True

        resp = SimpleNamespace(success=False, result_message="", error_message="연결된 피어를 찾을 수 없습니다: robot_a")
        client.call_async.return_value = _future_with_result(resp)

        node = _make_dummy_node(_call_peer_client=client)
        skill = _make_skill(CallPeerRobotSkill, node)

        result = skill.execute(
            {"peer_name": "robot_a", "instruction": "앞으로 1미터 이동해"}
        )

        assert result["success"] is False
        assert "연결된 피어를 찾을 수 없습니다" in result["message"]


class TestListPeerRobotsSkill:
    def test_no_client_configured(self):
        skill = _make_skill(ListPeerRobotsSkill, _make_dummy_node())
        result = skill.execute({})

        assert result["success"] is False
        assert "피어 목록 조회 클라이언트" in result["message"]

    def test_service_not_available(self):
        client = MagicMock()
        client.wait_for_service.return_value = False
        node = _make_dummy_node(_list_peers_client=client)

        skill = _make_skill(ListPeerRobotsSkill, node)
        result = skill.execute({})

        assert result["success"] is False
        assert "ListPeers 서비스" in result["message"]

    def test_success_response_mapping(self):
        client = MagicMock()
        client.wait_for_service.return_value = True

        peer_a = SimpleNamespace(peer_name="robot_a", host="192.168.1.10", port=50051, connected=True)
        peer_b = SimpleNamespace(peer_name="robot_b", host="192.168.1.11", port=50051, connected=False)
        resp = SimpleNamespace(success=True, peers=[peer_a, peer_b], error_message="")
        client.call_async.return_value = _future_with_result(resp)

        node = _make_dummy_node(_list_peers_client=client)
        skill = _make_skill(ListPeerRobotsSkill, node)

        result = skill.execute({})

        assert result["success"] is True
        assert result["peers"] == [
            {"peer_name": "robot_a", "host": "192.168.1.10", "port": 50051, "connected": True},
            {"peer_name": "robot_b", "host": "192.168.1.11", "port": 50051, "connected": False},
        ]


class TestBroadcastToPeersSkill:
    def test_missing_params(self):
        skill = _make_skill(BroadcastToPeersSkill, _make_dummy_node())
        result = skill.execute({})

        assert result["success"] is False
        assert "message" in result["message"]

    def test_no_client_configured(self):
        skill = _make_skill(BroadcastToPeersSkill, _make_dummy_node())
        result = skill.execute({"message": "모두 정지"})

        assert result["success"] is False
        assert "피어 브로드캐스트 클라이언트" in result["message"]

    def test_success_response_mapping(self):
        client = MagicMock()
        client.wait_for_service.return_value = True

        resp = SimpleNamespace(success=True, error_message="")
        client.call_async.return_value = _future_with_result(resp)

        node = _make_dummy_node(_broadcast_peers_client=client)
        skill = _make_skill(BroadcastToPeersSkill, node)

        result = skill.execute({"message": "모두 정지"})

        assert result["success"] is True

    def test_failure_response_mapping(self):
        client = MagicMock()
        client.wait_for_service.return_value = True

        resp = SimpleNamespace(success=False, error_message="모든 피어 전송 실패")
        client.call_async.return_value = _future_with_result(resp)

        node = _make_dummy_node(_broadcast_peers_client=client)
        skill = _make_skill(BroadcastToPeersSkill, node)

        result = skill.execute({"message": "모두 정지"})

        assert result["success"] is False
        assert "모든 피어 전송 실패" in result["message"]


class TestQueryPeerStatusSkill:
    def test_missing_params(self):
        skill = _make_skill(QueryPeerStatusSkill, _make_dummy_node())
        result = skill.execute({})

        assert result["success"] is False
        assert "peer_name" in result["message"]

    def test_no_client_configured(self):
        skill = _make_skill(QueryPeerStatusSkill, _make_dummy_node())
        result = skill.execute({"peer_name": "robot_a"})

        assert result["success"] is False
        assert "피어 호출 클라이언트" in result["message"]

    def test_success_response_mapping(self):
        client = MagicMock()
        client.wait_for_service.return_value = True

        resp = SimpleNamespace(success=True, result_message="배터리 87%, 주방에 있음", error_message="")
        client.call_async.return_value = _future_with_result(resp)

        node = _make_dummy_node(_call_peer_client=client)
        skill = _make_skill(QueryPeerStatusSkill, node)

        result = skill.execute({"peer_name": "robot_a"})

        assert result["success"] is True
        assert result["status_summary"] == "배터리 87%, 주방에 있음"
        assert result["peer_name"] == "robot_a"

        # 상태 조회 지시문이 CallPeerRobot 요청에 실제로 실려 나갔는지 확인
        sent_request = client.call_async.call_args[0][0]
        assert "상태" in sent_request.instruction
        assert sent_request.wait_for_result is True


class TestLocalWaitSec:
    """timeout_sec 미지정/지정 시 로컬 ROS2 future 대기 상한 계산 검증"""

    def test_default_uses_generous_ceiling(self):
        assert _local_wait_sec(0.0) == cooperate_skill_module._DEFAULT_PEER_CALL_LOCAL_WAIT_SEC

    def test_explicit_timeout_adds_margin(self):
        assert _local_wait_sec(10.0) == 10.0 + cooperate_skill_module._SERVICE_CALL_MARGIN_SEC


class TestPeerCallTimeoutWiring:
    """CallPeerRobotSkill/QueryPeerStatusSkill이 로컬 대기 상한을 실제로 사용하는지 검증"""

    def test_call_peer_robot_default_timeout_uses_local_wait_sec(self):
        client = MagicMock()
        client.wait_for_service.return_value = True
        resp = SimpleNamespace(success=True, result_message="ok", error_message="")
        client.call_async.return_value = _future_with_result(resp)

        node = _make_dummy_node(_call_peer_client=client)
        skill = _make_skill(CallPeerRobotSkill, node)

        with patch.object(
            cooperate_skill_module, "_wait_for_future", wraps=cooperate_skill_module._wait_for_future
        ) as spy:
            skill.execute({"peer_name": "robot_a", "instruction": "상태 확인"})

        call_timeout_sec = spy.call_args[0][1]
        assert call_timeout_sec == cooperate_skill_module._DEFAULT_PEER_CALL_LOCAL_WAIT_SEC

    def test_call_peer_robot_explicit_timeout_adds_margin(self):
        client = MagicMock()
        client.wait_for_service.return_value = True
        resp = SimpleNamespace(success=True, result_message="ok", error_message="")
        client.call_async.return_value = _future_with_result(resp)

        node = _make_dummy_node(_call_peer_client=client)
        skill = _make_skill(CallPeerRobotSkill, node)

        with patch.object(
            cooperate_skill_module, "_wait_for_future", wraps=cooperate_skill_module._wait_for_future
        ) as spy:
            skill.execute(
                {"peer_name": "robot_a", "instruction": "이동해", "timeout_sec": 120.0}
            )

        call_timeout_sec = spy.call_args[0][1]
        assert call_timeout_sec == 120.0 + cooperate_skill_module._SERVICE_CALL_MARGIN_SEC

    def test_query_peer_status_default_timeout_uses_local_wait_sec(self):
        client = MagicMock()
        client.wait_for_service.return_value = True
        resp = SimpleNamespace(success=True, result_message="배터리 87%", error_message="")
        client.call_async.return_value = _future_with_result(resp)

        node = _make_dummy_node(_call_peer_client=client)
        skill = _make_skill(QueryPeerStatusSkill, node)

        with patch.object(
            cooperate_skill_module, "_wait_for_future", wraps=cooperate_skill_module._wait_for_future
        ) as spy:
            skill.execute({"peer_name": "robot_a"})

        call_timeout_sec = spy.call_args[0][1]
        assert call_timeout_sec == cooperate_skill_module._DEFAULT_PEER_CALL_LOCAL_WAIT_SEC


# ---------------------------------------------------------------------------
# QueryPeerCapabilitiesSkill 테스트
# ---------------------------------------------------------------------------


class TestQueryPeerCapabilitiesSkill:
    def test_missing_params(self):
        skill = _make_skill(QueryPeerCapabilitiesSkill, _make_dummy_node())
        result = skill.execute({})

        assert result["success"] is False
        assert "peer_name" in result["message"]

    def test_no_client_configured(self):
        skill = _make_skill(QueryPeerCapabilitiesSkill, _make_dummy_node())
        result = skill.execute({"peer_name": "robot_a"})

        assert result["success"] is False
        assert "피어 호출 클라이언트" in result["message"]

    def test_success_with_json_array(self):
        client = MagicMock()
        client.wait_for_service.return_value = True

        resp = SimpleNamespace(
            success=True,
            result_message='["navigate_to", "grasp", "place"]',
            error_message="",
        )
        client.call_async.return_value = _future_with_result(resp)

        node = _make_dummy_node(_call_peer_client=client)
        skill = _make_skill(QueryPeerCapabilitiesSkill, node)

        result = skill.execute({"peer_name": "robot_a"})

        assert result["success"] is True
        assert "navigate_to" in result["capabilities"]
        assert "grasp" in result["capabilities"]
        assert result["peer_name"] == "robot_a"

    def test_success_with_non_json_fallback(self):
        """동료가 JSON이 아닌 텍스트로 응답해도 capabilities는 빈 리스트로 떨어진다."""
        client = MagicMock()
        client.wait_for_service.return_value = True

        resp = SimpleNamespace(
            success=True,
            result_message="저는 이동과 카메라만 가능합니다.",
            error_message="",
        )
        client.call_async.return_value = _future_with_result(resp)

        node = _make_dummy_node(_call_peer_client=client)
        skill = _make_skill(QueryPeerCapabilitiesSkill, node)

        result = skill.execute({"peer_name": "robot_a"})

        assert result["success"] is True
        assert result["capabilities"] == []

    def test_capabilities_instruction_asks_for_skills(self):
        """지시문에 스킬 목록을 요청하는 내용이 포함되어야 한다."""
        client = MagicMock()
        client.wait_for_service.return_value = True
        resp = SimpleNamespace(success=True, result_message="[]", error_message="")
        client.call_async.return_value = _future_with_result(resp)

        node = _make_dummy_node(_call_peer_client=client)
        skill = _make_skill(QueryPeerCapabilitiesSkill, node)

        skill.execute({"peer_name": "robot_a"})

        sent_request = client.call_async.call_args[0][0]
        assert "스킬" in sent_request.instruction or "skill" in sent_request.instruction.lower()


# ---------------------------------------------------------------------------
# CoordinatePeerTaskSkill 테스트
# ---------------------------------------------------------------------------


class TestCoordinatePeerTaskSkill:
    def test_missing_peer_name(self):
        node = _make_dummy_node(_llm=MagicMock())
        skill = _make_skill(CoordinatePeerTaskSkill, node)
        result = skill.execute({"instruction": "컵을 주방에 옮겨줘"})

        assert result["success"] is False
        assert "peer_name" in result["message"]

    def test_missing_instruction(self):
        node = _make_dummy_node(_llm=MagicMock())
        skill = _make_skill(CoordinatePeerTaskSkill, node)
        result = skill.execute({"peer_name": "robot_a"})

        assert result["success"] is False
        assert "instruction" in result["message"]

    def test_no_llm_configured(self):
        node = _make_dummy_node()
        skill = _make_skill(CoordinatePeerTaskSkill, node)
        result = skill.execute({"peer_name": "robot_a", "instruction": "컵 옮겨줘"})

        assert result["success"] is False
        assert "LLM" in result["message"]

    def test_decompose_and_sequential_send(self):
        """LLM이 분해한 단계를 동료에게 순차 전달한다."""
        llm = MagicMock()
        llm.chat.return_value = json.dumps({
            "steps": [
                {"instruction": "거실로 이동해줘"},
                {"instruction": "컵을 들어줘"},
                {"instruction": "주방으로 가줘"},
                {"instruction": "싱크대에 놔줘"},
            ]
        })
        node = _make_dummy_node(_llm=llm, get_node_names=MagicMock(return_value=[]))

        client = MagicMock()
        client.wait_for_service.return_value = True
        resp = SimpleNamespace(success=True, result_message="완료", error_message="")
        client.call_async.return_value = _future_with_result(resp)
        node._call_peer_client = client

        # send_user_message용 mock
        node._send_msg_client = MagicMock()
        node._send_msg_client.service_is_ready.return_value = False

        skill = _make_skill(CoordinatePeerTaskSkill, node)
        result = skill.execute({"peer_name": "robot_a", "instruction": "거실로 이동해 컵을 들고 주방에 놔줘"})

        assert result["success"] is True
        assert result["steps_completed"] == 4
        assert result["steps_total"] == 4
        # LLM 1회 + call_peer_robot 4회 호출
        assert llm.chat.call_count == 1
        assert client.call_async.call_count == 4

    def test_decompose_fails_returns_error(self):
        """LLM 응답에서 steps를 파싱하지 못하면 실패를 반환한다."""
        llm = MagicMock()
        llm.chat.return_value = "invalid response"
        node = _make_dummy_node(_llm=llm)

        skill = _make_skill(CoordinatePeerTaskSkill, node)
        result = skill.execute({"peer_name": "robot_a", "instruction": "컵 옮겨줘"})

        assert result["success"] is False
        assert "분해" in result["message"]

    def test_step_failure_aborts(self):
        """중간 단계 실패 시 후속 단계를 전달하지 않고 중단한다."""
        llm = MagicMock()
        llm.chat.return_value = json.dumps({
            "steps": [
                {"instruction": "거실로 이동해줘"},
                {"instruction": "컵을 들어줘"},
            ]
        })
        node = _make_dummy_node(_llm=llm, get_node_names=MagicMock(return_value=[]))

        client = MagicMock()
        client.wait_for_service.return_value = True
        # 첫 단계 실패, 두 번째는 호출되지 않아야 함
        fail_resp = SimpleNamespace(success=False, result_message="", error_message="이동 실패")
        client.call_async.return_value = _future_with_result(fail_resp)
        node._call_peer_client = client

        node._send_msg_client = MagicMock()
        node._send_msg_client.service_is_ready.return_value = False

        skill = _make_skill(CoordinatePeerTaskSkill, node)
        result = skill.execute({
            "peer_name": "robot_a",
            "instruction": "거실로 이동해 컵을 들어줘",
            "max_retries": 0,
        })

        assert result["success"] is False
        assert result["steps_completed"] == 0
        # call_peer_robot은 첫 단계만 (재시도 0이므로)
        assert client.call_async.call_count == 1

    def test_decompose_string_steps(self):
        """LLM이 문자열 리스트로 응답해도 정상 처리된다."""
        llm = MagicMock()
        llm.chat.return_value = json.dumps({
            "steps": ["거실로 이동해줘", "컵을 들어줘"]
        })
        node = _make_dummy_node(_llm=llm, get_node_names=MagicMock(return_value=[]))

        client = MagicMock()
        client.wait_for_service.return_value = True
        resp = SimpleNamespace(success=True, result_message="ok", error_message="")
        client.call_async.return_value = _future_with_result(resp)
        node._call_peer_client = client

        node._send_msg_client = MagicMock()
        node._send_msg_client.service_is_ready.return_value = False

        skill = _make_skill(CoordinatePeerTaskSkill, node)
        result = skill.execute({"peer_name": "robot_a", "instruction": "컵 옮겨줘"})

        assert result["success"] is True
        assert result["steps_completed"] == 2
