"""
AgentNode 기본 동작 테스트 (ROS2 없이 스킬/메모리 로직만 검증)
"""

from robo_claw_agent.agent_node.execution import ExecutionMixin
from robo_claw_agent.memory_manager import MemoryManager
from robo_claw_agent.skill_manager import BaseSkill, SkillManager
from robo_claw_agent.types import AgentState


def test_agent_state_values():
    """AgentState 열거형 값 검증"""
    assert AgentState.IDLE == 0
    assert AgentState.THINKING == 1
    assert AgentState.EXECUTING == 2
    assert AgentState.WAITING == 3
    assert AgentState.ERROR == 4


def test_skill_and_memory_integration(tmp_path):
    """스킬 실행 결과가 메모리에 올바르게 저장되는지 통합 검증"""

    class EchoSkill(BaseSkill):
        name = "echo"
        description = "입력값 반환"

        def execute(self, params):
            return {"success": True, "message": str(params.get("text", ""))}

    mgr = SkillManager()
    mgr.register(EchoSkill())
    mem = MemoryManager(storage_path=str(tmp_path / "mem.json"))

    result = mgr.execute("echo", {"text": "hello"})
    mem.add_event("skill_executed", result.to_dict())

    recent = mem.get_recent(5)
    assert len(recent) == 1
    assert recent[0]["data"]["skill_name"] == "echo"
    assert recent[0]["data"]["success"] is True


def test_direct_cup_pick_routes_to_adaptive_pick_object():
    mixin = ExecutionMixin()

    route = mixin._direct_cup_pick_skill("컵 집어줘")

    assert route == {
        "skill": "adaptive_pick_object",
        "params": {"target_object": "cup"},
        "background": True,
    }


def test_direct_cup_pick_does_not_route_analysis_requests():
    mixin = ExecutionMixin()

    assert mixin._direct_cup_pick_skill("컵 집기 왜 실패했는지 분석해줘") is None
    assert mixin._direct_cup_pick_skill("그리퍼 카메라 확인해줘") is None
    assert mixin._direct_cup_pick_skill("집기 준비자세") is None
    assert mixin._direct_cup_pick_skill("집기 준비 자세 해줘") is None


def test_start_background_direct_skill_returns_immediately(monkeypatch):
    class FakeLogger:
        def info(self, *_args, **_kwargs):
            pass

        def error(self, *_args, **_kwargs):
            pass

    class FakeMemory:
        def __init__(self):
            self.events = []

        def add_event(self, event_type, data):
            self.events.append((event_type, data))

    mixin = ExecutionMixin()
    mixin._memory = FakeMemory()
    mixin._set_state = lambda *args, **kwargs: None
    mixin._start_channel_send = lambda *args, **kwargs: None
    mixin.get_logger = lambda: FakeLogger()
    mixin._skills = type(
        "FakeSkills",
        (),
        {"execute": lambda self, name, params, timeout_sec=0.0: None},
    )()
    monkeypatch.setattr("threading.Thread.start", lambda self: None)

    result = mixin._start_background_direct_skill(
        "컵 집어줘", "adaptive_pick_object", {"target_object": "cup"}
    )

    assert result.success is True
    assert result.result_data["background"] is True
    assert mixin._memory.events[0][0] == "background_task_started"
