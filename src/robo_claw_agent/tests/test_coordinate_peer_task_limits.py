"""coordinate_peer_task 재시도·예산 상한 회귀 테스트 (ROS2 불필요).

AGENTS.md §8 은 peer task delegation 을 안전 관련 변경 대상으로 두고, 실제 로봇
검증 전에 "재시도 횟수가 제한되는가"를 확인하라고 요구한다. 이 파일은 파라미터
검증(음수·비정수·과대값)과 단계 전체 시간 예산이 실제로 강제되는지 검증한다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from robo_claw_agent.skills.cooperate_skill import CoordinatePeerTaskSkill

pytestmark = pytest.mark.unit


def _make_node(llm_resp: str | None = None, peer_success: bool = True):
    llm = MagicMock()
    if llm_resp is not None:
        llm.chat.return_value = llm_resp

    node = SimpleNamespace()
    node._llm = llm
    node.get_node_names = MagicMock(return_value=[])
    node._call_peer_client = MagicMock()
    node._call_peer_client.wait_for_service.return_value = True

    resp = SimpleNamespace(
        success=peer_success,
        result_message="완료" if peer_success else "실패",
        error_message="",
    )
    future = MagicMock()
    future.add_done_callback.side_effect = lambda cb: cb(future)
    future.result.return_value = resp
    node._call_peer_client.call_async.return_value = future
    node._send_msg_client = MagicMock()
    node._send_msg_client.service_is_ready.return_value = False
    return node


def _steps(*instructions: str) -> str:
    return json.dumps({"steps": [{"instruction": text} for text in instructions]})


def _skill(node) -> CoordinatePeerTaskSkill:
    skill = CoordinatePeerTaskSkill()
    skill.set_node(node)
    return skill


@pytest.mark.parametrize("bad", ["abc", None, {}, [1]])
def test_non_integer_max_retries_is_rejected_not_raised(bad):
    node = _make_node(_steps("이동해줘"))
    skill = _skill(node)

    result = skill.execute({"peer_name": "robot_a", "instruction": "이동해줘", "max_retries": bad})

    assert result["success"] is False
    assert result["failure_reason"] == "invalid_parameter"
    assert node._call_peer_client.call_async.call_count == 0


@pytest.mark.parametrize("bad", [-1, -100, 99])
def test_max_retries_outside_range_is_rejected(bad):
    node = _make_node(_steps("이동해줘"))
    skill = _skill(node)

    result = skill.execute({"peer_name": "robot_a", "instruction": "이동해줘", "max_retries": bad})

    assert result["success"] is False
    assert result["failure_reason"] == "invalid_parameter"
    assert node._call_peer_client.call_async.call_count == 0


@pytest.mark.parametrize("bad", ["abc", -5.0, 10_000.0])
def test_timeout_sec_outside_range_is_rejected(bad):
    node = _make_node(_steps("이동해줘"))
    skill = _skill(node)

    result = skill.execute({"peer_name": "robot_a", "instruction": "이동해줘", "timeout_sec": bad})

    assert result["success"] is False
    assert result["failure_reason"] == "invalid_parameter"
    assert node._call_peer_client.call_async.call_count == 0


def test_retry_count_stays_within_limit():
    """실패 시 max_retries+1 회까지만 시도한다."""
    node = _make_node(_steps("이동해줘"), peer_success=False)
    skill = _skill(node)

    result = skill.execute({"peer_name": "robot_a", "instruction": "이동해줘", "max_retries": 2})

    assert result["success"] is False
    assert node._call_peer_client.call_async.call_count == 3


def test_total_budget_stops_further_steps(monkeypatch):
    """단계 전체 예산을 넘기면 남은 단계를 실행하지 않고 부분 결과를 보고한다."""
    node = _make_node(_steps("1단계", "2단계", "3단계"))
    skill = _skill(node)

    clock = {"t": 0.0}
    monkeypatch.setattr(
        "robo_claw_agent.skills.cooperate_skill._monotonic", lambda: clock["t"], raising=False
    )

    def _send(peer, instruction, timeout):
        clock["t"] += 600.0  # 단계마다 예산을 크게 소모
        return {"success": True, "message": "완료"}

    monkeypatch.setattr(skill, "_send_to_peer", _send)

    result = skill.execute(
        {"peer_name": "robot_a", "instruction": "세 단계 작업", "total_budget_sec": 500}
    )

    assert result["success"] is False
    assert result["failure_reason"] == "coordinate_budget_exceeded"
    assert result["steps_completed"] == 1
    assert len(result["results"]) == 1


def test_total_budget_allows_normal_run():
    node = _make_node(_steps("1단계", "2단계"))
    skill = _skill(node)

    result = skill.execute({"peer_name": "robot_a", "instruction": "두 단계 작업"})

    assert result["success"] is True
    assert result["steps_completed"] == 2
