"""자율협동(cooperation_skill) 단위 테스트 (ROS2 불필요).

conversation / inbox / bt_nodes 는 ROS2 의존이 없고, cooperate_autonomous 는
lazy import 를 사용하므로 rclpy 없이도 테스트할 수 있다.
"""

import json
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# robo_claw_msgs stub (cooperate_autonomous 의 lazy import 용)
_STUB_KEYS: list[str] = []


def _stub_mod(name: str, **attrs) -> None:
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    _STUB_KEYS.append(name)


def _stub_mock(name: str, *attrs) -> None:
    if name in sys.modules:
        return
    mod = MagicMock(spec=types.ModuleType)
    for attr in attrs:
        setattr(mod, attr, MagicMock())
    sys.modules[name] = mod
    _STUB_KEYS.append(name)


# robo_claw_agent.agent_node 를 네임스페이스 패키지로 등록해 __init__(node.py -> rclpy)을
# 실행하지 않으면서, rclpy 의존이 없는 하위 모듈(utils 등)은 실제 파일에서 로드한다.
if "robo_claw_agent.agent_node" not in sys.modules:
    _agent_node_pkg = types.ModuleType("robo_claw_agent.agent_node")
    _agent_node_pkg.__path__ = [
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "robo_claw_agent", "agent_node")
        )
    ]
    sys.modules["robo_claw_agent.agent_node"] = _agent_node_pkg
    _STUB_KEYS.append("robo_claw_agent.agent_node")


@pytest.fixture(autouse=True, scope="module")
def _cleanup_stubs():
    # Only remove the local agent_node namespace shim. Never replace the
    # generated robo_claw_msgs package globally; other test modules need its
    # complete msg/srv/action namespace during collection and execution.
    yield
    for _key in _STUB_KEYS:
        sys.modules.pop(_key, None)
    _STUB_KEYS.clear()


from robo_claw_agent.skills.autonomous_skill.core import (
    BTNode,
    NodeStatus,
    SequenceNode,
)
from robo_claw_agent.skills.cooperation_skill.bt_nodes import (
    ProcessPeerMessages,
    build_peer_conversation_context,
)
from robo_claw_agent.skills.cooperation_skill.conversation import (
    PeerConversationManager,
    get_peer_conversation,
    reset_peer_conversation,
)
from robo_claw_agent.skills.cooperation_skill.cooperate_autonomous import (
    _DEFAULT_REMOTE_ALLOWED_SKILLS,
    AutonomousCooperateSkill,
    StopAutonomousCooperateSkill,
    cooperation_start_params,
)
from robo_claw_agent.skills.cooperation_skill.inbox import (
    PeerMessageInbox,
    get_peer_message_inbox,
    reset_peer_message_inbox,
)

# ---------------------------------------------------------------------------
# PeerConversationManager
# ---------------------------------------------------------------------------


def test_conversation_add_and_context():
    cm = PeerConversationManager()
    cm.add_message("butler", "peer", "도와줘")
    cm.add_message("butler", "self", "무엇을 도와줄까?")
    ctx = cm.get_context("butler")
    assert "도와줘" in ctx
    assert "무엇을 도와줄까?" in ctx
    assert "동료" in ctx and "나" in ctx


def test_conversation_history_limit():
    cm = PeerConversationManager(max_history_per_peer=3)
    for i in range(5):
        cm.add_message("butler", "peer", f"msg{i}")
    history = cm.get_history("butler")
    assert len(history) == 3
    assert history[-1]["content"] == "msg4"


def test_conversation_tasks():
    cm = PeerConversationManager()
    tid = cm.start_task("butler", "컵 옮기기")
    assert cm.get_active_tasks("butler")[0]["description"] == "컵 옮기기"
    cm.complete_task(tid, "완료")
    assert cm.get_active_tasks("butler") == []
    assert cm.get_tasks_context("butler") == "  • 진행 중인 협업 태스크 없음"


def test_conversation_expire_tasks_marks_stale_work():
    cm = PeerConversationManager()
    tid = cm.start_task("butler", "오래된 작업")
    cm._tasks[tid]["updated_ts"] = 0

    assert cm.expire_tasks(max_age_sec=10) == 1
    assert cm.snapshot()["tasks"][tid]["status"] == "expired"


def test_conversation_clear():
    cm = PeerConversationManager()
    cm.add_message("butler", "peer", "hi")
    cm.start_task("butler", "task")
    cm.clear()
    assert cm.get_history("butler") == []
    assert cm.get_active_tasks() == []


# ---------------------------------------------------------------------------
# PeerMessageInbox
# ---------------------------------------------------------------------------


def test_inbox_push_drain():
    ib = PeerMessageInbox()
    assert ib.push("butler", "hello") is True
    assert ib.push("", "") is False
    assert ib.size() == 1
    items = ib.drain()
    assert items == [("butler", "hello")]
    assert ib.size() == 0


def test_inbox_has_single_consumer_owner():
    ib = PeerMessageInbox()
    assert ib.claim_consumer("bt") is True
    assert ib.claim_consumer("cooperate") is False
    ib.push("butler", "hello")
    assert ib.drain(consumer="cooperate") == []
    assert ib.drain(consumer="bt") == [("butler", "hello")]
    ib.release_consumer("bt")
    assert ib.claim_consumer("cooperate") is True


def test_inbox_global_singleton():
    reset_peer_message_inbox()
    a = get_peer_message_inbox()
    b = get_peer_message_inbox()
    assert a is b
    reset_peer_message_inbox()


# ---------------------------------------------------------------------------
# AutonomousCooperateSkill — LLM 판단
# ---------------------------------------------------------------------------


def _make_node(llm_resp=None):
    node = MagicMock()
    node._llm = MagicMock()
    if llm_resp is not None:
        node._llm.chat.return_value = llm_resp
    node._call_peer_client = MagicMock()
    node._call_peer_client.wait_for_service.return_value = True
    # call_async future 가 done callback 을 즉시 호출하도록 설정 (hang 방지)
    resp = SimpleNamespace(success=True, result_message="전달됨", error_message="")
    future = MagicMock()

    def add_done_callback(cb):
        cb(future)

    future.add_done_callback.side_effect = add_done_callback
    future.result.return_value = resp
    node._call_peer_client.call_async.return_value = future
    node._skills = MagicMock()
    node._skills.execute.return_value = SimpleNamespace(message="완료", success=True)
    node._memory = MagicMock()
    node._memory.get_all_objects.return_value = []
    return node


def test_parse_decision_json():
    skill = AutonomousCooperateSkill()
    decision = skill._parse_decision('```json\n{"action": "respond", "response": "ok"}\n```')
    assert decision["action"] == "respond"
    assert decision["response"] == "ok"


def test_parse_decision_invalid():
    skill = AutonomousCooperateSkill()
    assert skill._parse_decision("not json") == {}


def test_invalid_cooperation_params_are_rejected_before_thread_start():
    skill = AutonomousCooperateSkill()
    skill.set_node(MagicMock())
    result = skill.execute({"cycle_interval_sec": 0.0})
    assert result["success"] is False
    assert "파라미터 범위" in result["message"]


def test_default_remote_allowlist_contains_cleanup_skills():
    assert "analyze_scene" in _DEFAULT_REMOTE_ALLOWED_SKILLS
    assert "navigate_to" in _DEFAULT_REMOTE_ALLOWED_SKILLS
    assert "adaptive_pick_object" in _DEFAULT_REMOTE_ALLOWED_SKILLS
    assert "grasp" in _DEFAULT_REMOTE_ALLOWED_SKILLS
    assert "place" in _DEFAULT_REMOTE_ALLOWED_SKILLS


def test_cooperation_start_params_extract_goal_from_peer_message():
    assert cooperation_start_params(
        "자율협동을 시작했습니다. 협동 목표는 '주변 환경 정리하기'입니다."
    ) == {"goal": "주변 환경 정리하기"}
    assert cooperation_start_params("일반 협동 메시지") == {}


def test_semantic_sweep_places_reads_memory_and_deduplicates():
    node = _make_node()
    node._memory.get_all_objects.return_value = [
        {"name": "거실", "position": {"x": 1, "y": 2}},
        {"name": "거실", "position": {"x": 1, "y": 2}},
    ]
    skill = AutonomousCooperateSkill()
    skill.set_node(node)

    places = skill._semantic_sweep_places()

    assert places == [{"name": "거실", "x": 1.0, "y": 2.0}]


def test_place_sweep_starts_without_cleanup_goal(monkeypatch):
    skill = AutonomousCooperateSkill()
    skill.set_node(_make_node())
    skill._goal = ""
    started = []
    monkeypatch.setattr(skill, "_run_place_sweep", lambda: started.append(True))

    skill._start_place_sweep_if_needed()
    skill._start_place_sweep_if_needed()
    skill._thread.join(timeout=1) if skill._thread else None

    import time

    deadline = time.monotonic() + 1.0
    while not started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert started == [True]
    assert skill._place_sweep_started is True


def test_cooperation_loop_does_not_stop_for_battery_status():
    skill = AutonomousCooperateSkill()
    skill.set_node(_make_node())
    skill._inbox = MagicMock()
    skill._inbox.drain.return_value = []
    skill._proactive_check = MagicMock()
    skill._battery_ok = MagicMock(side_effect=AssertionError("battery check must be disabled"))

    from robo_claw_agent.skills.cooperation_skill.globals import _COOPERATE_ACTIVE

    _COOPERATE_ACTIVE.set()
    skill._run_loop(0.01, 1.0, 1)
    assert not _COOPERATE_ACTIVE.is_set()


def test_remote_task_execution_is_blocked_by_default():
    skill = AutonomousCooperateSkill()
    skill.set_node(_make_node())
    assert skill._execute_task("앞으로 이동해")["success"] is False


def test_remote_action_uses_bounded_execution_policy():
    skill = AutonomousCooperateSkill()
    node = _make_node()
    remote_skill = SimpleNamespace(risk_level="action", remote_max_duration_sec=12.0)
    node._skills.get_skill.return_value = remote_skill
    node._skills.execute.return_value = SimpleNamespace(success=True, message="이동 완료")
    skill.set_node(node)
    skill._allow_remote_task_execution = True
    skill._remote_allowed_skills = {"navigate"}

    result = skill._execute_task("거실로 이동해", "navigate", {})

    assert result["success"] is True
    assert result["policy_decision"] == "allow_with_limits"
    assert result["execution_limits"]["max_duration_sec"] == 12.0
    node._skills.execute.assert_called_once_with("navigate", {}, timeout_sec=12.0)


def test_remote_write_skill_returns_safe_fallback():
    skill = AutonomousCooperateSkill()
    node = _make_node()
    node._skills.get_skill.return_value = SimpleNamespace(risk_level="write")
    skill.set_node(node)
    skill._allow_remote_task_execution = True
    skill._remote_allowed_skills = {"ros_command"}

    result = skill._execute_task("명령 실행", "ros_command", {})

    assert result["success"] is False
    assert result["policy_decision"] == "deny"
    assert result["fallback_action"] == "report_and_request_local_confirmation"


def test_decide_incoming_response_respond():
    node = _make_node(llm_resp=json.dumps({"action": "respond", "response": "도와드릴게요"}))
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    decision = skill._decide_incoming_response("butler", "도와줘")
    assert decision["action"] == "respond"
    assert decision["response"] == "도와드릴게요"


def test_decide_incoming_response_no_llm():
    skill = AutonomousCooperateSkill()
    skill.set_node(MagicMock(_llm=None))
    decision = skill._decide_incoming_response("butler", "hi")
    assert decision["action"] == "respond"


def test_proactive_check_introduces_peer_without_llm(monkeypatch):
    skill = AutonomousCooperateSkill()
    skill.set_node(_make_node())
    skill.node._llm = None
    skill._goal = "정리"
    monkeypatch.setattr(skill, "_discover_peers", lambda: ["robot_b"])
    monkeypatch.setattr(skill, "_send_to_peer", lambda peer, message: {"success": True})

    result = skill._proactive_check()

    assert result[0]["peer"] == "robot_b"
    assert "자율협동을 시작했습니다" in result[0]["content"]
    assert "협동 목표는 '정리'입니다" in result[0]["content"]
    assert "analyze_scene" in result[0]["content"]
    assert skill._introduced_peers == {"robot_b"}


def test_incoming_decision_prompt_contains_cooperation_goal():
    node = _make_node(llm_resp=json.dumps({"action": "respond", "response": "정리하겠습니다."}))
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    skill._goal = "정리"

    decision = skill._decide_incoming_response("robot_b", "어느 구역을 맡을까요?")

    assert decision["response"] == "정리하겠습니다."
    prompt = node._llm.chat.call_args.kwargs["system_prompt"]
    assert "현재 협동 목표" in prompt
    assert "정리" in prompt


def test_invalid_incoming_llm_response_gets_fallback_reply():
    node = _make_node(llm_resp="깨진 응답")
    skill = AutonomousCooperateSkill()
    skill.set_node(node)

    decision = skill._decide_incoming_response("robot_b", "작업 상태를 알려줘")

    assert decision["action"] == "respond"
    assert "이해하지 못했습니다" in decision["response"]


def test_incoming_decision_without_response_gets_acknowledgement(monkeypatch):
    node = _make_node(llm_resp=json.dumps({"action": "respond", "response": ""}))
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    monkeypatch.setattr(skill, "_send_to_peer", lambda peer, message: {"success": True})

    result = skill._process_incoming("robot_b", "현재 상태를 알려줘")

    assert (
        result["reply"] == "메시지를 받았습니다. 현재 협동 목표와 다음 작업을 계속 조율하겠습니다."
    )


def test_process_incoming_respond_sends_to_peer():
    node = _make_node(llm_resp=json.dumps({"action": "respond", "response": "알겠어"}))
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    result = skill._process_incoming("butler", "컵 좀 옮겨줘")
    assert result["action"] == "respond"
    assert result["reply"] == "알겠어"
    # 대화 이력에 기록됐는지 확인
    assert len(skill._conversation.get_history("butler")) == 2


def test_process_incoming_execute_task():
    node = _make_node(
        llm_resp=json.dumps(
            {
                "action": "execute_task",
                "skill_name": "navigate",
                "skill_params": {"target_name": "거실"},
                "task_instruction": "거실로 이동해",
                "response": "",
            }
        )
    )
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    skill._allow_remote_task_execution = True
    skill._remote_allowed_skills = {"navigate"}
    result = skill._process_incoming("butler", "거실로 이동해줘")
    assert result["action"] == "execute_task"
    assert "완료" in result["reply"]
    node._skills.execute.assert_called_once_with(
        "navigate", {"target_name": "거실"}, timeout_sec=120.0
    )


def test_remote_execute_rejects_unregistered_skill():
    node = _make_node()
    node._skills.get_skill.return_value = None
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    skill._allow_remote_task_execution = True
    skill._remote_allowed_skills = {"navigate"}

    result = skill._execute_task(
        "거실로 이동해",
        "navigate",
        {"target_name": "거실"},
    )

    assert result["success"] is False
    assert "등록되어 있지" in result["message"]
    node._skills.execute.assert_not_called()


def test_remote_execute_rejects_dangerous_skill():
    node = _make_node()
    node._skills.get_skill.return_value = SimpleNamespace(risk_level="dangerous")
    skill = AutonomousCooperateSkill()
    skill.set_node(node)
    skill._allow_remote_task_execution = True
    skill._remote_allowed_skills = {"emergency_stop"}

    result = skill._execute_task("정지해", "emergency_stop", {})

    assert result["success"] is False
    assert "위험도가 높은" in result["message"]
    node._skills.execute.assert_not_called()


# ---------------------------------------------------------------------------
# ProcessPeerMessages BT 노드
# ---------------------------------------------------------------------------


def test_process_peer_messages_drains_inbox():
    reset_peer_message_inbox()
    reset_peer_conversation()
    inbox = get_peer_message_inbox()
    inbox.push("butler", "도와줘")

    skill = MagicMock()
    node = ProcessPeerMessages(skill)
    blackboard = {}
    node._blackboard = blackboard

    from robo_claw_agent.skills.autonomous_skill.core import NodeStatus

    status = node.tick()
    assert status == NodeStatus.SUCCESS
    assert blackboard.get("pending_peer_messages") == [("butler", "도와줘")]
    # 대화 이력에도 기록됨
    assert len(get_peer_conversation().get_history("butler")) == 1
    reset_peer_message_inbox()
    reset_peer_conversation()


def test_process_peer_messages_supports_halt():
    skill = MagicMock()
    node = ProcessPeerMessages(skill)
    assert hasattr(node, "halt")
    node.halt()

    assert isinstance(node, BTNode)

    class FailingNode(BTNode):
        def tick(self):
            return NodeStatus.FAILURE

    seq = SequenceNode("TestSeq", [node, FailingNode("Fail")])
    status = seq.tick()
    assert status == NodeStatus.FAILURE


def test_build_peer_conversation_context():
    reset_peer_conversation()
    cm = get_peer_conversation()
    cm.add_message("butler", "peer", "도와줘")
    ctx = build_peer_conversation_context({"pending_peer_messages": [("butler", "도와줘")]})
    assert "도와줘" in ctx
    assert "butler" in ctx
    reset_peer_conversation()


# ---------------------------------------------------------------------------
# StopAutonomousCooperateSkill
# ---------------------------------------------------------------------------


def test_stop_when_not_running():
    skill = StopAutonomousCooperateSkill()
    result = skill.execute({})
    assert result["success"] is False
