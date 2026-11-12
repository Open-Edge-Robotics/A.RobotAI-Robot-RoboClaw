"""구조화된 결과 상관 / 전달 확인 / 세션 전파 테스트 (ROS2 불필요).

핵심 규칙:
- 결과(status/kind=result)로 식별되는 메시지는 상관(outbound message_id + peer)이
  맞을 때만 관측 결과로 반영한다. 틀리면 LLM/스킬 실행으로 절대 흘리지 않는다.
- 상관된 단순 회신은 "상대가 받았다"는 확인일 뿐 완료 추론이 아니다.
- 원격 작업 실행 이벤트는 수신 태스크의 peer/session 을 그대로 이어받는다.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

if "robo_claw_agent.agent_node" not in sys.modules:
    _agent_node_pkg = types.ModuleType("robo_claw_agent.agent_node")
    _agent_node_pkg.__path__ = [
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "robo_claw_agent", "agent_node")
        )
    ]
    sys.modules["robo_claw_agent.agent_node"] = _agent_node_pkg

from robo_claw_agent.skills.cooperation_skill import globals as coop_globals
from robo_claw_agent.skills.cooperation_skill.conversation import reset_peer_conversation
from robo_claw_agent.skills.cooperation_skill.cooperate_autonomous import (
    AutonomousCooperateSkill,
    _envelope_from_inbox_item,
)
from robo_claw_agent.skills.cooperation_skill.envelope import (
    KIND_REPLY,
    KIND_RESULT,
    envelope_from_transport,
)
from robo_claw_agent.skills.cooperation_skill.inbox import (
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


def _queued_task_with_outbound(task_id: str = "task-1", outbound: str = "out-1") -> None:
    journal = get_cooperation_journal()
    journal.upsert_task(task_id, peer_id="robot_b", description="정리", status="queued")
    journal.note_outbound_message(task_id, outbound)


def _events() -> list[dict]:
    return get_cooperation_journal().snapshot()["events"]


def _event_types() -> list[str]:
    return [event["event_type"] for event in _events()]


# ---------------------------------------------------------------------------
# 결과 상관
# ---------------------------------------------------------------------------


def test_correlated_result_requires_matching_outbound_message_id():
    node = _make_node()
    node._llm.chat.side_effect = AssertionError("상관된 결과에 LLM을 재호출하면 안 됩니다")
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    _queued_task_with_outbound()
    sent: list[dict] = []
    skill._send_to_peer = lambda peer, message, **kwargs: sent.append(kwargs) or {"success": True}

    result = skill._process_incoming(
        "robot_b",
        "정리 끝",
        envelope_from_transport(
            content="정리 끝",
            message_id="msg-2",
            task_id="task-1",
            reply_to="out-1",
            status="completed",
            result="정리 끝",
            kind=KIND_RESULT,
        ),
    )

    journal = get_cooperation_journal()
    assert result["action"] == "observed_result"
    assert journal.task_status("task-1") == "completed"
    assert sent == []
    assert "task_completed" in _event_types()


def test_result_without_reply_to_is_rejected_without_llm_or_execution():
    node = _make_node(llm_resp=json.dumps({"action": "respond", "response": "확인"}))
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    _queued_task_with_outbound()
    skill._send_to_peer = lambda peer, message, **kwargs: {"success": True}

    result = skill._process_incoming(
        "robot_b",
        "정리 끝",
        envelope_from_transport(
            content="정리 끝", message_id="m", task_id="task-1", status="completed"
        ),
    )

    assert result["action"] == "unconfirmed_result"
    assert node._llm.chat.called is False
    node._skills.execute.assert_not_called()
    assert get_cooperation_journal().task_status("task-1") == "queued"
    rejection = _events()[-1]
    assert rejection["event_type"] == "message_rejected"
    assert rejection["details"]["unconfirmed"] is True
    assert rejection["details"]["reason"] == "reply_to_missing"


def test_result_with_wrong_reply_to_is_rejected_without_llm():
    node = _make_node(llm_resp=json.dumps({"action": "respond", "response": "확인"}))
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    _queued_task_with_outbound()

    result = skill._process_incoming(
        "robot_b",
        "정리 끝",
        envelope_from_transport(
            content="정리 끝",
            message_id="m",
            task_id="task-1",
            reply_to="not-our-message",
            status="completed",
            kind=KIND_RESULT,
        ),
    )

    assert result["action"] == "unconfirmed_result"
    assert node._llm.chat.called is False
    assert get_cooperation_journal().task_status("task-1") == "queued"
    assert _events()[-1]["details"]["reason"] == "reply_to_mismatch"


def test_result_for_inbound_self_created_task_is_rejected():
    """수신이 만든 태스크(우리가 보낸 메시지 없음)는 결과 상관 대상이 아니다."""
    node = _make_node(llm_resp=json.dumps({"action": "respond", "response": "확인"}))
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    receive_peer_message(
        peer_name="robot_b", message="청소해줘", message_id="msg-1", task_id="task-1"
    )
    assert get_cooperation_journal().task_status("task-1") == "received"

    result = skill._process_incoming(
        "robot_b",
        "작업 완료",
        envelope_from_transport(
            content="작업 완료",
            message_id="msg-2",
            task_id="task-1",
            reply_to="msg-1",
            status="completed",
            kind=KIND_RESULT,
        ),
    )

    assert result["action"] == "unconfirmed_result"
    assert node._llm.chat.called is False
    node._skills.execute.assert_not_called()
    assert get_cooperation_journal().task_status("task-1") == "received"


def test_result_for_unknown_task_is_rejected_and_creates_no_task():
    node = _make_node(llm_resp=json.dumps({"action": "respond", "response": "확인"}))
    skill = AutonomousCooperateSkill()
    skill.set_node(node)

    outcome = receive_peer_message(
        peer_name="robot_b",
        message="작업 완료",
        message_id="msg-9",
        task_id="ghost-task",
        status="completed",
        kind=KIND_RESULT,
    )
    assert outcome.accepted is True
    assert get_cooperation_journal().task("ghost-task") is None

    result = skill._process_incoming(
        outcome.inbox_item.peer_name,
        outcome.inbox_item.message,
        _envelope_from_inbox_item(outcome.inbox_item),
    )

    assert result["action"] == "unconfirmed_result"
    assert node._llm.chat.called is False
    node._skills.execute.assert_not_called()
    assert get_cooperation_journal().task("ghost-task") is None
    assert _events()[-1]["details"]["reason"] == "unknown_task"


def test_result_from_a_different_peer_never_reaches_llm_or_execution():
    node = _make_node(llm_resp=json.dumps({"action": "respond", "response": "확인"}))
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    _queued_task_with_outbound()

    result = skill._process_incoming(
        "robot_c",
        "정리 끝",
        envelope_from_transport(
            content="정리 끝",
            message_id="m",
            task_id="task-1",
            reply_to="out-1",
            status="completed",
            kind=KIND_RESULT,
        ),
    )

    assert result["action"] == "unconfirmed_result"
    assert node._llm.chat.called is False
    node._skills.execute.assert_not_called()
    assert get_cooperation_journal().task_status("task-1") == "queued"
    assert _events()[-1]["details"]["reason"] == "peer_mismatch"


def test_result_marked_message_never_triggers_delegated_execution():
    """LLM이 execute_task를 원해도 결과 보고 메시지는 실행 경로로 가지 않는다."""
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

    skill._process_incoming(
        "robot_b",
        "failed: 알 수 없음",
        envelope_from_transport(
            content="failed: 알 수 없음",
            message_id="m",
            task_id="ghost",
            reply_to="whatever",
            status="failed",
            kind=KIND_RESULT,
        ),
    )

    assert node._llm.chat.called is False
    node._skills.execute.assert_not_called()


# ---------------------------------------------------------------------------
# 상관된 단순 회신 = 전달 확인
# ---------------------------------------------------------------------------


def test_correlated_simple_reply_confirms_delivery_without_completing():
    node = _make_node(llm_resp=json.dumps({"action": "respond", "response": "알겠습니다"}))
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    _queued_task_with_outbound()
    skill._send_to_peer = lambda peer, message, **kwargs: {"success": True}

    skill._process_incoming(
        "robot_b",
        "확인했습니다",
        envelope_from_transport(
            content="확인했습니다",
            message_id="reply-1",
            task_id="task-1",
            reply_to="out-1",
            kind=KIND_REPLY,
        ),
    )

    journal = get_cooperation_journal()
    task = journal.task("task-1")
    assert task["delivery_confirmed"] is True
    assert task["status"] == "queued"
    assert "delivery_confirmed" in _event_types()
    # 상관된 일반 회신은 기존 대화 처리(LLM)를 유지한다.
    assert node._llm.chat.called


# ---------------------------------------------------------------------------
# 원격 실행 이벤트의 peer/session 전파
# ---------------------------------------------------------------------------


def test_remote_task_events_inherit_peer_and_remote_session():
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
    journal = get_cooperation_journal()
    local_session = journal.start_session(goal="정리")
    assert local_session != "remote-session"

    skill._process_incoming(
        "robot_b",
        "거실로 이동해줘",
        envelope_from_transport(
            content="거실로 이동해줘",
            message_id="msg-1",
            task_id="task-1",
            session_id="remote-session",
        ),
    )

    execution_events = [
        event
        for event in _events()
        if event["event_type"] in {"policy", "task_started", "task_completed"}
    ]
    assert len(execution_events) == 3
    for event in execution_events:
        assert event["peer_id"] == "robot_b"
        assert event["session_id"] == "remote-session"
        assert event["task_id"] == "task-1"
    # status 는 태스크 상태만 담는다. 정책 판정/LLM 결정은 details 로 간다.
    policy_event = next(event for event in execution_events if event["event_type"] == "policy")
    assert policy_event["status"] == ""
    assert policy_event["details"]["decision"] == "allow_with_limits"
    decision_event = next(event for event in _events() if event["event_type"] == "decision")
    assert decision_event["status"] == ""
    assert decision_event["details"]["action"] == "execute_task"


def test_execution_events_carry_sanitized_params_and_duration(caplog: pytest.LogCaptureFixture):
    node = _make_node(
        llm_resp=json.dumps(
            {
                "action": "execute_task",
                "skill_name": "navigate",
                "skill_params": {"target_name": "거실", "auth_token": "peer-secret"},
                "task_instruction": "거실로 이동해",
            }
        )
    )
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    skill._allow_remote_task_execution = True
    skill._remote_allowed_skills = {"navigate"}
    skill._send_to_peer = lambda peer, message, **kwargs: {"success": True}

    with caplog.at_level(logging.INFO, logger="robo_claw_agent.skills.cooperation_skill.journal"):
        skill._process_incoming(
            "robot_b",
            "거실로 이동해줘",
            envelope_from_transport(content="거실로 이동해줘", message_id="m", task_id="task-1"),
        )

    started = next(event for event in _events() if event["event_type"] == "task_started")
    completed = next(event for event in _events() if event["event_type"] == "task_completed")
    assert started["details"]["skill_params"]["target_name"] == "거실"
    assert started["details"]["skill_params"]["auth_token"] == "***"
    assert isinstance(completed["details"]["duration_ms"], int)
    assert completed["details"]["duration_ms"] >= 0
    assert "peer-secret" not in caplog.text


# ---------------------------------------------------------------------------
# 인박스 소비권 충돌
# ---------------------------------------------------------------------------


def test_inbox_consumer_conflict_stops_session_and_cleans_executor():
    node = _make_node()
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    skill._inbox = get_peer_message_inbox()
    assert skill._inbox.claim_consumer("other-loop") is True
    skill._incoming_executor = MagicMock()
    journal = get_cooperation_journal()
    journal.start_session(goal="정리")
    coop_globals._COOPERATE_ACTIVE.set()

    skill._run_loop(0.01, 1.0, 1)

    assert journal.active is False
    assert _events()[-1]["event_type"] == "session_stopped"
    assert _events()[-1]["details"]["reason"] == "inbox_consumer_conflict"
    assert skill._incoming_executor is None
    assert coop_globals.is_cooperation_active() is False


# ---------------------------------------------------------------------------
# 구버전 생성코드(srv) 필드 감지
# ---------------------------------------------------------------------------


class _OldPeerMessageRequest:
    """재빌드 전 생성코드 — 상관 필드가 아예 없는 요청."""

    __slots__ = ("peer_name", "message")

    def __init__(self) -> None:
        self.peer_name = "robot_b"
        self.message = "안녕"


class _NewPeerMessageRequest:
    __slots__ = (
        "peer_name",
        "message",
        "message_id",
        "task_id",
        "session_id",
        "reply_to",
        "status",
        "result",
        "kind",
    )

    def __init__(self) -> None:
        self.peer_name = "robot_b"
        self.message = "안녕"
        self.message_id = ""
        self.task_id = ""
        self.session_id = ""
        self.reply_to = ""
        self.status = ""
        self.result = ""
        self.kind = ""


def test_missing_request_fields_detects_unrebuilt_service():
    from robo_claw_agent.skills.cooperation_skill.envelope import (
        REBUILD_REQUIRED_ERROR,
        missing_request_fields,
    )

    missing = missing_request_fields(_OldPeerMessageRequest())
    assert "message_id" in missing
    assert missing_request_fields(_NewPeerMessageRequest()) == []
    assert "rebuild required" in REBUILD_REQUIRED_ERROR


# ---------------------------------------------------------------------------
# 결과 출처 표시 (dashboard 계약: observed / origin / outbound_message_id)
# ---------------------------------------------------------------------------


def test_correlated_result_marks_task_as_peer_reported_and_delivered():
    node = _make_node()
    node._llm.chat.side_effect = AssertionError("상관된 결과에 LLM을 재호출하면 안 됩니다")
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    _queued_task_with_outbound()
    skill._send_to_peer = lambda peer, message, **kwargs: {"success": True}

    skill._process_incoming(
        "robot_b",
        "정리 끝",
        envelope_from_transport(
            content="정리 끝",
            message_id="msg-2",
            task_id="task-1",
            reply_to="out-1",
            status="completed",
            result="정리 끝",
            kind=KIND_RESULT,
        ),
    )

    task = get_cooperation_journal().task("task-1")
    assert task["status"] == "completed"
    assert task["observed"] is True
    assert task["origin"] == "peer_report"
    assert task["delivery_confirmed"] is True
    assert task["outbound_message_id"] == "out-1"

    completed = next(event for event in _events() if event["event_type"] == "task_completed")
    assert completed["details"]["observed"] is True
    assert "delivery_confirmed" in _event_types()


def test_local_execution_events_are_marked_observed_false():
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

    task = get_cooperation_journal().task("task-1")
    assert task["status"] == "completed"
    assert task["observed"] is False
    assert task["origin"] == ""
    for event_type in ("task_started", "task_completed"):
        event = next(event for event in _events() if event["event_type"] == event_type)
        assert event["details"]["observed"] is False


def test_delivery_only_reply_does_not_mark_task_observed():
    node = _make_node(llm_resp=json.dumps({"action": "respond", "response": "네"}))
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    _queued_task_with_outbound()
    skill._send_to_peer = lambda peer, message, **kwargs: {"success": True}

    skill._process_incoming(
        "robot_b",
        "확인",
        envelope_from_transport(
            content="확인",
            message_id="reply-1",
            task_id="task-1",
            reply_to="out-1",
            kind=KIND_REPLY,
        ),
    )

    task = get_cooperation_journal().task("task-1")
    assert task["delivery_confirmed"] is True
    assert task["observed"] is False
    assert task["status"] == "queued"
