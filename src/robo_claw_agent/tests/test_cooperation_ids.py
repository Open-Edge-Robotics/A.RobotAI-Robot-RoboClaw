"""협동 ID 전파 / 태스크 수명주기 테스트 (ROS2 불필요).

envelope(JSON) → 인박스 → 실행 → 회신 경로에서 message_id/task_id/session_id/reply_to
가 보존되는지, 구조화된 결과가 LLM 재호출 없이 관측 결과로만 기록되는지 검증한다.
"""

from __future__ import annotations

import json
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# robo_claw_agent.agent_node 를 네임스페이스 패키지로 등록해 __init__(node.py -> rclpy)을
# 실행하지 않으면서 rclpy 의존이 없는 하위 모듈만 사용한다.
if "robo_claw_agent.agent_node" not in sys.modules:
    _agent_node_pkg = types.ModuleType("robo_claw_agent.agent_node")
    _agent_node_pkg.__path__ = [
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "robo_claw_agent", "agent_node")
        )
    ]
    sys.modules["robo_claw_agent.agent_node"] = _agent_node_pkg

from robo_claw_agent.skills.cooperation_skill import globals as coop_globals
from robo_claw_agent.skills.cooperation_skill.bt_nodes import ProcessPeerMessages
from robo_claw_agent.skills.cooperation_skill.conversation import (
    PeerConversationManager,
    reset_peer_conversation,
)
from robo_claw_agent.skills.cooperation_skill.cooperate_autonomous import (
    AutonomousCooperateSkill,
    _envelope_from_inbox_item,
)
from robo_claw_agent.skills.cooperation_skill.envelope import (
    KIND_REPLY,
    KIND_REQUEST,
    KIND_RESULT,
    PeerEnvelope,
    envelope_from_transport,
    new_id,
    parse_envelope,
)
from robo_claw_agent.skills.cooperation_skill.inbox import (
    InboxMessage,
    PeerMessageInbox,
    get_peer_message_inbox,
    reset_peer_message_inbox,
)
from robo_claw_agent.skills.cooperation_skill.journal import (
    get_cooperation_journal,
    reset_cooperation_journal,
)
from robo_claw_agent.skills.cooperation_skill.receive import receive_peer_message

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_state():
    reset_peer_message_inbox()
    reset_cooperation_journal()
    reset_peer_conversation()
    yield
    reset_peer_message_inbox()
    reset_cooperation_journal()
    reset_peer_conversation()
    coop_globals._COOPERATE_ACTIVE.clear()


def _event_types() -> list[str]:
    return [event["event_type"] for event in get_cooperation_journal().snapshot()["events"]]


def _make_node(llm_resp=None):
    node = MagicMock()
    node._llm = MagicMock()
    if llm_resp is not None:
        node._llm.chat.return_value = llm_resp
    node._call_peer_client = MagicMock()
    node._call_peer_client.wait_for_service.return_value = True
    node._skills = MagicMock()
    node._skills.get_skill.return_value = SimpleNamespace(risk_level="action")
    node._skills.execute.return_value = SimpleNamespace(message="완료", success=True)
    node._memory = MagicMock()
    node._memory.get_all_objects.return_value = []
    return node


def _peer_response(success: bool, result_message: str = "", error_message: str = ""):
    future = MagicMock()

    def add_done_callback(callback):
        callback(future)

    future.add_done_callback.side_effect = add_done_callback
    future.result.return_value = SimpleNamespace(
        success=success, result_message=result_message, error_message=error_message
    )
    return future


# ---------------------------------------------------------------------------
# envelope
# ---------------------------------------------------------------------------


def test_envelope_round_trip_preserves_ids():
    envelope = PeerEnvelope(
        content="거실로 이동해줘",
        message_id="msg-1",
        task_id="task-1",
        session_id="session-1",
        reply_to="msg-0",
        kind=KIND_REQUEST,
        status="",
        created_at=10.0,
        expires_at=310.0,
    )

    parsed = parse_envelope(json.dumps(envelope.to_payload(), ensure_ascii=False))

    assert parsed is not None
    assert parsed.content == "거실로 이동해줘"
    assert parsed.message_id == "msg-1"
    assert parsed.task_id == "task-1"
    assert parsed.session_id == "session-1"
    assert parsed.reply_to == "msg-0"
    assert parsed.kind == KIND_REQUEST


def test_legacy_envelope_without_ids_is_accepted():
    legacy = json.dumps(
        {
            "_robo_claw_message_type": "peer_cooperation",
            "content": "옛 형식 메시지",
            "task_id": "task-legacy",
            "created_at": 1.0,
            "expires_at": 2.0,
        },
        ensure_ascii=False,
    )

    parsed = parse_envelope(legacy)

    assert parsed is not None
    assert parsed.content == "옛 형식 메시지"
    assert parsed.message_id == ""
    assert parsed.task_id == "task-legacy"
    assert parsed.session_id == ""
    assert parsed.kind == ""


def test_plain_message_is_not_an_envelope():
    assert parse_envelope("그냥 자연어 메시지") is None
    assert parse_envelope('{"content": "envelope 표식 없음"}') is None
    assert parse_envelope('{"_robo_claw_message_type": "peer_cooperation"}') is None


def test_envelope_status_is_normalized():
    envelope = envelope_from_transport(
        content="완료", task_id="task-1", message_id="msg-2", status="Completed", kind=KIND_RESULT
    )

    assert envelope.status == "completed"
    assert envelope.kind == KIND_RESULT


# ---------------------------------------------------------------------------
# 수신 인박스 / 저널
# ---------------------------------------------------------------------------


def test_receive_peer_message_preserves_transport_ids():
    outcome = receive_peer_message(
        peer_name="robot_b",
        message="컵을 옮겨주세요",
        message_id="msg-1",
        task_id="task-1",
        session_id="session-1",
        reply_to="msg-0",
    )

    assert outcome.accepted is True
    item = outcome.inbox_item
    assert isinstance(item, InboxMessage)
    assert (item.peer_name, item.message) == ("robot_b", "컵을 옮겨주세요")
    assert item.message_id == "msg-1"
    assert item.task_id == "task-1"
    assert item.session_id == "session-1"
    assert item.reply_to == "msg-0"

    journal = get_cooperation_journal()
    event = journal.snapshot()["events"][0]
    assert event["event_type"] == "message_received"
    assert event["message_id"] == "msg-1"
    assert event["task_id"] == "task-1"
    assert journal.task_status("task-1") == "received"


def test_receive_peer_message_generates_ids_for_legacy_plain_messages():
    outcome = receive_peer_message(peer_name="robot_b", message="도와줘")

    assert outcome.accepted is True
    item = outcome.inbox_item
    assert item.message_id
    assert item.task_id
    assert item.session_id == ""
    journal = get_cooperation_journal()
    assert journal.task_status(item.task_id) == "received"


def test_receive_peer_message_rejects_empty_payload():
    outcome = receive_peer_message(peer_name="", message="도와줘")

    assert outcome.accepted is False
    assert outcome.error_message


def test_inbox_structured_and_legacy_apis_share_one_queue():
    inbox = PeerMessageInbox()
    assert inbox.push("butler", "legacy") is True
    assert inbox.push_message(
        InboxMessage(peer_name="butler", message="structured", message_id="m", task_id="t")
    )

    assert inbox.drain() == [("butler", "legacy"), ("butler", "structured")]
    assert inbox.size() == 0


def test_inbox_drain_messages_keeps_structured_fields():
    inbox = PeerMessageInbox()
    inbox.push_message(InboxMessage(peer_name="butler", message="hi", task_id="t-1"))

    items = inbox.drain_messages()

    assert [item.task_id for item in items] == ["t-1"]


# ---------------------------------------------------------------------------
# 수신 처리 → 회신
# ---------------------------------------------------------------------------


def test_incoming_reply_preserves_task_and_reply_to():
    node = _make_node(llm_resp=json.dumps({"action": "respond", "response": "알겠어"}))
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    sent: list[tuple[str, str, dict]] = []
    skill._send_to_peer = lambda peer, message, **kwargs: (
        sent.append((peer, message, kwargs)) or {"success": True}
    )

    result = skill._process_incoming(
        "robot_b",
        "컵을 옮겨줘",
        envelope_from_transport(
            content="컵을 옮겨줘",
            message_id="msg-1",
            task_id="task-1",
            session_id="session-1",
        ),
    )

    assert result["reply"] == "알겠어"
    peer, _message, kwargs = sent[0]
    assert peer == "robot_b"
    assert kwargs["task_id"] == "task-1"
    assert kwargs["reply_to"] == "msg-1"
    assert kwargs["session_id"] == "session-1"
    assert kwargs["kind"] == KIND_REPLY


def test_execute_task_success_records_lifecycle_events():
    node = _make_node(
        llm_resp=json.dumps(
            {
                "action": "execute_task",
                "skill_name": "navigate",
                "skill_params": {"target_name": "거실"},
                "task_instruction": "거실로 이동해",
            }
        )
    )
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    skill._allow_remote_task_execution = True
    skill._remote_allowed_skills = {"navigate"}
    sent: list[dict] = []
    skill._send_to_peer = lambda peer, message, **kwargs: sent.append(kwargs) or {"success": True}

    result = skill._process_incoming(
        "robot_b",
        "거실로 이동해줘",
        envelope_from_transport(
            content="거실로 이동해줘", message_id="msg-1", task_id="task-1", session_id="s-1"
        ),
    )

    events = _event_types()
    assert result["action"] == "execute_task"
    assert events == [
        "message_received",
        "decision",
        "policy",
        "task_started",
        "task_completed",
    ]
    journal = get_cooperation_journal()
    assert journal.task_status("task-1") == "completed"
    assert sent[0]["status"] == "completed"
    assert sent[0]["kind"] == KIND_RESULT
    assert sent[0]["task_id"] == "task-1"
    assert sent[0]["reply_to"] == "msg-1"


def test_execute_task_records_conversation_task_with_transport_id():
    """대화 태스크 저장소도 전송 task_id 로 기록돼 LLM 컨텍스트와 상관이 맞는다."""
    node = _make_node(
        llm_resp=json.dumps(
            {
                "action": "execute_task",
                "skill_name": "navigate",
                "skill_params": {"target_name": "거실"},
                "task_instruction": "거실로 이동해",
            }
        )
    )
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    skill._allow_remote_task_execution = True
    skill._remote_allowed_skills = {"navigate"}
    skill._send_to_peer = lambda peer, message, **kwargs: {"success": True}

    skill._process_incoming(
        "robot_b",
        "거실로 이동해줘",
        envelope_from_transport(content="거실로 이동해줘", message_id="m", task_id="task-1"),
    )

    task = skill._conversation.snapshot()["tasks"]["task-1"]
    assert task["status"] == "completed"
    assert task["peer"] == "robot_b"


def test_policy_denied_is_recorded_and_reply_marks_denied():
    node = _make_node(
        llm_resp=json.dumps(
            {
                "action": "execute_task",
                "skill_name": "navigate",
                "skill_params": {"target_name": "거실"},
                "task_instruction": "거실로 이동해",
            }
        )
    )
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    sent: list[dict] = []
    skill._send_to_peer = lambda peer, message, **kwargs: sent.append(kwargs) or {"success": True}

    skill._process_incoming(
        "robot_b",
        "거실로 이동해줘",
        envelope_from_transport(content="거실로 이동해줘", message_id="m", task_id="task-1"),
    )

    assert "task_denied" in _event_types()
    assert get_cooperation_journal().task_status("task-1") == "denied"
    assert sent[0]["status"] == "denied"
    node._skills.execute.assert_not_called()


def test_execution_failure_is_recorded_as_failed():
    node = _make_node(
        llm_resp=json.dumps(
            {
                "action": "execute_task",
                "skill_name": "navigate",
                "skill_params": {"target_name": "거실"},
                "task_instruction": "거실로 이동해",
            }
        )
    )
    node._skills.execute.return_value = SimpleNamespace(message="이동 실패", success=False)
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    skill._allow_remote_task_execution = True
    skill._remote_allowed_skills = {"navigate"}
    sent: list[dict] = []
    skill._send_to_peer = lambda peer, message, **kwargs: sent.append(kwargs) or {"success": True}

    skill._process_incoming(
        "robot_b",
        "거실로 이동해줘",
        envelope_from_transport(content="거실로 이동해줘", message_id="m", task_id="task-1"),
    )

    assert "task_failed" in _event_types()
    assert get_cooperation_journal().task_status("task-1") == "failed"
    assert sent[0]["status"] == "failed"


# ---------------------------------------------------------------------------
# 구조화된 결과 / ack — LLM 재호출 금지
# ---------------------------------------------------------------------------


def test_structured_result_skips_llm_and_records_observed_status():
    node = _make_node()
    node._llm.chat.side_effect = AssertionError("구조화된 결과에 LLM을 재호출하면 안 됩니다")
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    journal = get_cooperation_journal()
    journal.upsert_task("task-1", peer_id="robot_b", description="정리", status="queued")
    journal.note_outbound_message("task-1", "out-1")
    sent: list[dict] = []
    skill._send_to_peer = lambda peer, message, **kwargs: sent.append(kwargs) or {"success": True}

    result = skill._process_incoming(
        "robot_b",
        "작업 완료: 정리 끝",
        envelope_from_transport(
            content="작업 완료: 정리 끝",
            message_id="msg-2",
            task_id="task-1",
            reply_to="out-1",
            status="completed",
            result="정리 끝",
            kind=KIND_RESULT,
        ),
    )

    assert result["action"] == "observed_result"
    assert journal.task_status("task-1") == "completed"
    assert journal.task("task-1")["result"] == "정리 끝"
    assert sent == []
    assert "task_completed" in _event_types()


def test_structured_result_with_unknown_task_is_rejected_without_llm():
    node = _make_node(llm_resp=json.dumps({"action": "respond", "response": "확인했습니다"}))
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    sent: list[dict] = []
    skill._send_to_peer = lambda peer, message, **kwargs: sent.append(kwargs) or {"success": True}

    result = skill._process_incoming(
        "robot_b",
        "작업 완료",
        envelope_from_transport(
            content="작업 완료",
            message_id="msg-2",
            task_id="unknown-task",
            status="completed",
            kind=KIND_RESULT,
        ),
    )

    assert result["action"] == "unconfirmed_result"
    assert node._llm.chat.called is False
    assert get_cooperation_journal().task("unknown-task") is None
    assert sent == []


def test_service_correlation_status_flows_to_observed_result():
    """PeerMessage 서비스 필드(status/result)로 온 결과가 실행 없이 관측 결과로 기록된다."""
    node = _make_node()
    node._llm.chat.side_effect = AssertionError("구조화된 결과에 LLM을 재호출하면 안 됩니다")
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    journal = get_cooperation_journal()
    journal.upsert_task("task-1", peer_id="robot_b", description="정리", status="queued")
    journal.note_outbound_message("task-1", "out-1")
    sent: list[dict] = []
    skill._send_to_peer = lambda peer, message, **kwargs: sent.append(kwargs) or {"success": True}

    outcome = receive_peer_message(
        peer_name="robot_b",
        message="정리 끝났습니다",
        message_id="msg-2",
        task_id="task-1",
        reply_to="out-1",
        status="completed",
        result="정리 끝",
        kind=KIND_RESULT,
    )
    assert outcome.accepted is True
    item = outcome.inbox_item
    assert item.status == "completed"

    result = skill._process_incoming(item.peer_name, item.message, _envelope_from_inbox_item(item))

    assert result["action"] == "observed_result"
    assert journal.task_status("task-1") == "completed"
    assert journal.task("task-1")["result"] == "정리 끝"
    assert sent == []


def test_structured_result_from_a_different_peer_is_not_correlated():
    node = _make_node(llm_resp=json.dumps({"action": "respond", "response": "확인했습니다"}))
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    journal = get_cooperation_journal()
    journal.upsert_task("task-1", peer_id="robot_b", description="정리", status="queued")
    journal.note_outbound_message("task-1", "out-1")
    skill._send_to_peer = lambda peer, message, **kwargs: {"success": True}

    result = skill._process_incoming(
        "robot_c",
        "작업 완료",
        envelope_from_transport(
            content="작업 완료",
            message_id="m",
            task_id="task-1",
            reply_to="out-1",
            status="completed",
            kind=KIND_RESULT,
        ),
    )

    assert result["action"] == "unconfirmed_result"
    assert journal.task_status("task-1") == "queued"
    assert node._llm.chat.called is False


# ---------------------------------------------------------------------------
# 송신 경로의 queued/failed 상태
# ---------------------------------------------------------------------------


class _FakeCallPeerRequest:
    """CallPeerRobot.Request 대역 — 전송된 envelope 를 검사할 수 있게 보관한다."""

    instances: list[_FakeCallPeerRequest] = []

    def __init__(self) -> None:
        self.peer_name = ""
        self.instruction = ""
        self.file_path = ""
        self.wait_for_result = False
        self.timeout_sec = 0.0
        _FakeCallPeerRequest.instances.append(self)


@pytest.fixture(autouse=True)
def _reset_call_peer_requests():
    _FakeCallPeerRequest.instances.clear()
    yield
    _FakeCallPeerRequest.instances.clear()


def _install_call_peer_stub(monkeypatch, node) -> None:
    module = types.ModuleType("robo_claw_msgs.srv")
    module.CallPeerRobot = SimpleNamespace(Request=_FakeCallPeerRequest)
    monkeypatch.setitem(sys.modules, "robo_claw_msgs.srv", module)
    node._call_peer_client.call_async.return_value = _peer_response(True, result_message="전달됨")


def test_outbound_message_is_queued_and_keeps_session_id(monkeypatch):
    node = _make_node()
    _install_call_peer_stub(monkeypatch, node)
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    journal = get_cooperation_journal()
    session_id = journal.start_session(goal="정리")
    coop_globals._COOPERATE_ACTIVE.set()

    result = skill._send_to_peer("robot_b", "거실을 맡아주세요", kind=KIND_REQUEST)

    assert result["success"] is True
    envelope = parse_envelope(_FakeCallPeerRequest.instances[-1].instruction)
    assert envelope is not None
    assert envelope.content == "거실을 맡아주세요"
    assert envelope.session_id == session_id
    assert envelope.kind == KIND_REQUEST
    assert envelope.message_id
    task = journal.task(envelope.task_id)
    assert task["status"] == "queued"
    assert [event["event_type"] for event in journal.snapshot()["events"]] == [
        "session_started",
        "message_queued",
    ]


def test_outbound_send_failure_marks_task_failed(monkeypatch):
    node = _make_node()
    _install_call_peer_stub(monkeypatch, node)
    node._call_peer_client.call_async.return_value = _peer_response(
        False, error_message="연결된 피어 없음"
    )
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    journal = get_cooperation_journal()
    journal.start_session(goal="정리")
    coop_globals._COOPERATE_ACTIVE.set()

    result = skill._send_to_peer("robot_b", "거실을 맡아주세요")

    assert result["success"] is False
    envelope = parse_envelope(_FakeCallPeerRequest.instances[-1].instruction)
    assert envelope is not None
    assert journal.task_status(envelope.task_id) == "failed"
    assert _event_types()[-1] == "message_send_failed"
    assert "message_received" not in _event_types()


# ---------------------------------------------------------------------------
# 세션 / 대화 태스크 저장소
# ---------------------------------------------------------------------------


def test_conversation_task_accepts_external_id_and_bounds_store():
    conversation = PeerConversationManager(max_tasks=3)

    assert conversation.start_task("robot_b", "정리", task_id="task-1") == "task-1"

    for index in range(5):
        conversation.start_task("robot_b", f"추가 {index}")

    assert len(conversation.snapshot()["tasks"]) <= 3


def test_bt_process_peer_messages_records_received_events():
    inbox = get_peer_message_inbox()
    assert inbox.push_message(
        InboxMessage(
            peer_name="butler",
            message="도와줘",
            message_id="msg-1",
            task_id="task-1",
            session_id="session-1",
        )
    )
    node = ProcessPeerMessages(MagicMock())
    node._blackboard = {}

    node.tick()

    assert node._blackboard["pending_peer_messages"] == [("butler", "도와줘")]
    journal = get_cooperation_journal()
    event = journal.snapshot()["events"][0]
    assert event["event_type"] == "message_received"
    assert event["message_id"] == "msg-1"
    assert journal.task_status("task-1") == "received"


def test_new_id_is_unique():
    assert new_id() != new_id()
