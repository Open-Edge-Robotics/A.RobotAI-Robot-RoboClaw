"""
TaskDecomposer(복합 명령 자동 분해 오케스트레이터) 단위 테스트.

conftest.py 가 agent_node.task_planner 를 rclpy 없이 직접 로드하므로 이 테스트는
ROS2 환경 없이도 실행 가능하다.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from robo_claw_agent.agent_node.task_planner import TaskDecomposer, looks_compound


def _make_node(max_steps=6, max_retries=1):
    node = SimpleNamespace()
    node._llm = MagicMock()
    node._task_decomposition_max_steps = max_steps
    node._task_step_max_retries = max_retries
    node.get_logger = MagicMock(return_value=MagicMock())

    async def _run_blocking(op_name, func, *args, **kwargs):
        return func(*args, **kwargs)

    node._run_blocking = _run_blocking
    node._planner = SimpleNamespace(run_llm_planning_loop=AsyncMock())
    return node


def _messages(instruction):
    return [
        {"role": "system", "content": "[로봇 현재 상태]\n{}"},
        {"role": "user", "content": instruction},
    ]


class TestLooksCompound:
    def test_simple_commands_are_not_compound(self):
        assert looks_compound("컵 집어") is False
        assert looks_compound("10도 회전해") is False
        assert looks_compound("주방으로 이동해") is False
        assert looks_compound("") is False

    def test_noun_ending_in_go_is_not_a_connective(self):
        """'-고'로 끝나는 명사 어절을 연결어미로 오인하면 안 된다.

        오탐 1건의 비용은 분해 LLM 1회가 아니라 "단계 수 x (1+재시도)"만큼의
        계획 루프이므로 관대하게 잡을 수 없다.
        """
        assert looks_compound("냉장고 앞으로 가줘") is False
        assert looks_compound("창고 앞에서 대기해") is False
        assert looks_compound("그거 말고 저거 가져와") is False
        assert looks_compound("참고 자료 읽어줘") is False
        assert looks_compound("최고 속도로 가줘") is False

    def test_sequential_connective_endings_are_compound(self):
        """'-아서/-어서/-가서'는 선행 동작을 전제로 절이 이어지는 순차 연결어미다."""
        assert looks_compound("주방으로 가서 컵 집어줘") is True
        assert looks_compound("회의실로 이동해서 사진 찍어줘") is True
        assert looks_compound("거실로 가면서 주변을 봐") is True

    def test_nouns_ending_in_seo_are_not_connectives(self):
        assert looks_compound("문서 읽어줘") is False
        assert looks_compound("여기서 멈춰") is False
        assert looks_compound("경찰서로 이동해") is False

    def test_sequential_commands_are_compound(self):
        assert looks_compound("회의실로 이동해 카메라를 보고 쓰레기가 있으면 정리해") is True
        assert looks_compound("실험실로 이동해 사람이 있는지 확인하고 메인룸으로 돌아와") is True
        assert looks_compound("거실로 이동한 다음 컵을 집어줘") is True
        assert looks_compound("주방으로 가고 나서 사진을 찍어줘") is True

    def test_multiple_sentence_endings_are_compound(self):
        assert looks_compound("주방으로 이동해줘. 그리고 사진을 찍어줘.") is True


class TestEstimateWaitMargin:
    def test_simple_instruction_uses_single_task_margin(self):
        node = _make_node()
        decomposer = TaskDecomposer(node)
        assert decomposer.estimate_wait_margin_sec("컵 집어") == 300.0

    def test_compound_instruction_scales_with_steps_and_retries(self):
        node = _make_node(max_steps=2, max_retries=0)
        decomposer = TaskDecomposer(node)
        # 300(단계당 마진) * 2(최대 단계) * 1(1+재시도 0회) = 600, 기본 상한(1800) 이내.
        assert decomposer.estimate_wait_margin_sec("1단계 하고 2단계 해") == 600.0

    def test_compound_instruction_is_capped_at_default(self):
        node = _make_node(max_steps=6, max_retries=1)
        decomposer = TaskDecomposer(node)
        # 300*6*2 = 3600 이지만 기본 상한 1800을 넘지 않는다.
        assert decomposer.estimate_wait_margin_sec("1단계 하고 2단계 해") == 1800.0

    def test_compound_instruction_respects_configured_cap(self):
        node = _make_node(max_steps=6, max_retries=1)
        node._task_decomposition_wait_margin_cap_sec = 400.0
        decomposer = TaskDecomposer(node)
        assert decomposer.estimate_wait_margin_sec("1단계 하고 2단계 해") == 400.0


class TestDecompose:
    async def test_decompose_dict_steps(self):
        node = _make_node()
        node._llm.chat = MagicMock(return_value=json.dumps({
            "steps": [
                {"instruction": "회의실로 이동해"},
                {"instruction": "카메라로 관찰해서 쓰레기가 있으면 정리해"},
            ]
        }))
        decomposer = TaskDecomposer(node)
        steps = await decomposer._decompose("회의실로 이동해 카메라를 보고 쓰레기가 있으면 정리해", _messages("x"), 6)
        assert steps == ["회의실로 이동해", "카메라로 관찰해서 쓰레기가 있으면 정리해"]

    async def test_decompose_string_steps(self):
        node = _make_node()
        node._llm.chat = MagicMock(return_value=json.dumps({"steps": ["A", "B"]}))
        decomposer = TaskDecomposer(node)
        steps = await decomposer._decompose("A하고 B해", _messages("x"), 6)
        assert steps == ["A", "B"]

    async def test_decompose_invalid_json_returns_empty(self):
        node = _make_node()
        node._llm.chat = MagicMock(return_value="이건 JSON이 아닙니다")
        decomposer = TaskDecomposer(node)
        steps = await decomposer._decompose("아무 명령", _messages("x"), 6)
        assert steps == []

    async def test_decompose_requests_json_response_format(self):
        """계획 루프와 동일하게 JSON 강제를 걸어야 소형 모델이 산문으로 새지 않는다."""
        node = _make_node()
        node._llm.chat = MagicMock(return_value=json.dumps({"steps": ["A", "B"]}))
        decomposer = TaskDecomposer(node)

        await decomposer._decompose("A하고 B해", _messages("x"), 6)

        assert node._llm.chat.call_args.kwargs.get("response_format") == {
            "type": "json_object"
        }

    async def test_decompose_no_llm_returns_empty(self):
        node = _make_node()
        node._llm = None
        decomposer = TaskDecomposer(node)
        steps = await decomposer._decompose("아무 명령", _messages("x"), 6)
        assert steps == []


class TestRun:
    async def test_all_steps_succeed(self):
        node = _make_node()
        node._llm.chat = MagicMock(return_value=json.dumps({
            "steps": [{"instruction": "회의실로 이동해"}, {"instruction": "쓰레기가 있으면 정리해"}]
        }))
        node._planner.run_llm_planning_loop = AsyncMock(side_effect=[
            ("이동 완료", [SimpleNamespace(success=True)], True, []),
            ("정리 완료", [SimpleNamespace(success=True)], True, []),
        ])
        decomposer = TaskDecomposer(node)

        result_msg, skill_results, task_success, events = await decomposer.run(
            goal_handle=MagicMock(),
            instruction="회의실로 이동해 카메라를 보고 쓰레기가 있으면 정리해",
            messages=_messages("회의실로 이동해 카메라를 보고 쓰레기가 있으면 정리해"),
            robot_summary={},
            timeout=60.0,
            send_fb=MagicMock(),
        )

        assert task_success is True
        assert len(skill_results) == 2
        assert node._planner.run_llm_planning_loop.call_count == 2
        assert "완료" in result_msg

    async def test_each_step_receives_original_instruction_as_context(self):
        """분해된 각 하위 단계는 전체 원본 명령을 시스템 컨텍스트로 함께 받아야 한다."""
        node = _make_node()
        original = "회의실로 이동해 카메라를 보고 쓰레기가 있으면 정리해"
        node._llm.chat = MagicMock(return_value=json.dumps({
            "steps": [{"instruction": "회의실로 이동해"}, {"instruction": "쓰레기가 있으면 정리해"}]
        }))
        node._planner.run_llm_planning_loop = AsyncMock(
            return_value=("완료", [SimpleNamespace(success=True)], True, [])
        )
        decomposer = TaskDecomposer(node)

        await decomposer.run(
            goal_handle=MagicMock(),
            instruction=original,
            messages=_messages(original),
            robot_summary={},
            timeout=60.0,
            send_fb=MagicMock(),
        )

        for call in node._planner.run_llm_planning_loop.await_args_list:
            step_messages = call.args[2]
            assert any(
                m.get("role") == "system" and original in m.get("content", "")
                for m in step_messages
            ), f"단계 메시지에 원본 명령이 없음: {step_messages}"

    async def test_decompose_failure_falls_back_to_single_step(self):
        node = _make_node()
        node._llm.chat = MagicMock(return_value="invalid")
        node._planner.run_llm_planning_loop = AsyncMock(
            return_value=("완료", [SimpleNamespace(success=True)], True, [])
        )
        decomposer = TaskDecomposer(node)

        result_msg, skill_results, task_success, events = await decomposer.run(
            goal_handle=MagicMock(),
            instruction="단순 명령",
            messages=_messages("단순 명령"),
            robot_summary={},
            timeout=60.0,
            send_fb=MagicMock(),
        )

        assert task_success is True
        assert node._planner.run_llm_planning_loop.call_count == 1

    async def test_step_failure_aborts_after_retries(self):
        node = _make_node(max_retries=1)
        node._llm.chat = MagicMock(return_value=json.dumps({
            "steps": [{"instruction": "1단계"}, {"instruction": "2단계"}]
        }))
        # 1단계는 시도 2번(초기+재시도 1회) 모두 실패 -> 중단, 2단계는 호출되지 않아야 함
        node._planner.run_llm_planning_loop = AsyncMock(side_effect=[
            ("1단계 실패", [SimpleNamespace(success=False)], False, []),
            ("1단계 재시도도 실패", [SimpleNamespace(success=False)], False, []),
        ])
        decomposer = TaskDecomposer(node)

        result_msg, skill_results, task_success, events = await decomposer.run(
            goal_handle=MagicMock(),
            instruction="1단계 하고 2단계 해",
            messages=_messages("1단계 하고 2단계 해"),
            robot_summary={},
            timeout=60.0,
            send_fb=MagicMock(),
        )

        assert task_success is False
        assert node._planner.run_llm_planning_loop.call_count == 2
        assert "실패" in result_msg
        assert "1/2" in result_msg or "중단" in result_msg

    async def test_step_retry_then_succeed_continues_to_next_step(self):
        node = _make_node(max_retries=1)
        node._llm.chat = MagicMock(return_value=json.dumps({
            "steps": [{"instruction": "1단계"}, {"instruction": "2단계"}]
        }))
        node._planner.run_llm_planning_loop = AsyncMock(side_effect=[
            ("1단계 첫 시도 실패", [SimpleNamespace(success=False)], False, []),
            ("1단계 재시도 성공", [SimpleNamespace(success=True)], True, []),
            ("2단계 성공", [SimpleNamespace(success=True)], True, []),
        ])
        decomposer = TaskDecomposer(node)

        result_msg, skill_results, task_success, events = await decomposer.run(
            goal_handle=MagicMock(),
            instruction="1단계 하고 2단계 해",
            messages=_messages("1단계 하고 2단계 해"),
            robot_summary={},
            timeout=60.0,
            send_fb=MagicMock(),
        )

        assert task_success is True
        assert node._planner.run_llm_planning_loop.call_count == 3
        # 재시도 실패분은 버려지고 각 단계의 마지막(성공) 시도 결과만 집계된다.
        assert len(skill_results) == 2

    async def test_previous_step_result_data_is_visible_to_next_step(self):
        """조건부 명령의 판단 근거가 되도록 앞 단계의 결과 데이터를 넘겨야 한다."""
        node = _make_node()
        node._llm.chat = MagicMock(return_value=json.dumps({
            "steps": [{"instruction": "컵을 찾아"}, {"instruction": "컵을 집어"}]
        }))
        found = SimpleNamespace(
            success=True,
            skill_name="find_object",
            result_data={"object": "cup", "x": 1.2, "image_base64": "AAAA"},
        )
        node._planner.run_llm_planning_loop = AsyncMock(side_effect=[
            ("컵 발견", [found], True, []),
            ("집기 완료", [SimpleNamespace(success=True, skill_name="grasp", result_data={})], True, []),
        ])
        decomposer = TaskDecomposer(node)

        await decomposer.run(
            goal_handle=MagicMock(),
            instruction="컵을 찾아서 집어",
            messages=_messages("컵을 찾아서 집어"),
            robot_summary={},
            timeout=60.0,
            send_fb=MagicMock(),
        )

        second_step_messages = node._planner.run_llm_planning_loop.await_args_list[1].args[2]
        joined = "\n".join(
            m.get("content", "") for m in second_step_messages if m.get("role") == "system"
        )
        assert "find_object" in joined
        assert "cup" in joined
        # 이미지 원본은 토큰 낭비이므로 생략되어야 한다.
        assert "AAAA" not in joined

    async def test_robot_state_is_refreshed_from_second_step(self):
        """단계가 진행되면 태스크 시작 시점의 낡은 위치/배터리를 그대로 쓰면 안 된다."""
        node = _make_node()
        node._llm.chat = MagicMock(return_value=json.dumps({
            "steps": [{"instruction": "1단계"}, {"instruction": "2단계"}]
        }))
        node._planner.run_llm_planning_loop = AsyncMock(
            return_value=("완료", [SimpleNamespace(success=True, skill_name="s", result_data={})], True, [])
        )
        node._skills = SimpleNamespace(
            _skills={
                "get_status": SimpleNamespace(
                    get_robot_summary=MagicMock(return_value={"pose": {"x": 9.9}})
                )
            }
        )
        node._get_robot_health_state = MagicMock(return_value={"battery": "ok"})
        stale = {"role": "system", "content": '[로봇 현재 상태]\n{"pose": {"x": 0.0}}'}
        decomposer = TaskDecomposer(node)

        await decomposer.run(
            goal_handle=MagicMock(),
            instruction="1단계 하고 2단계 해",
            messages=[stale, {"role": "user", "content": "1단계 하고 2단계 해"}],
            robot_summary={},
            timeout=60.0,
            send_fb=MagicMock(),
        )

        first_messages, second_messages = (
            call.args[2] for call in node._planner.run_llm_planning_loop.await_args_list
        )
        first_state = [m for m in first_messages if m["content"].startswith("[로봇 현재 상태]")]
        second_state = [m for m in second_messages if m["content"].startswith("[로봇 현재 상태]")]
        # 1단계는 collect_task_context 가 이미 갓 수집한 값을 쓰므로 재조회하지 않는다.
        assert "0.0" in first_state[0]["content"]
        # 2단계는 갱신된 값으로 교체되며, 상태 메시지가 중복 누적되지 않는다.
        assert len(second_state) == 1
        assert "9.9" in second_state[0]["content"]
        assert "battery" in second_state[0]["content"]

    async def test_robot_state_refresh_failure_keeps_previous_context(self):
        """상태 재조회가 실패해도 분해 자체를 실패시키지 않는다."""
        node = _make_node()  # _skills / _get_robot_health_state 없음 -> 조회 실패
        node._llm.chat = MagicMock(return_value=json.dumps({
            "steps": [{"instruction": "1단계"}, {"instruction": "2단계"}]
        }))
        node._planner.run_llm_planning_loop = AsyncMock(
            return_value=("완료", [SimpleNamespace(success=True, skill_name="s", result_data={})], True, [])
        )
        stale = {"role": "system", "content": '[로봇 현재 상태]\n{"pose": {"x": 0.0}}'}
        decomposer = TaskDecomposer(node)

        _, _, task_success, _ = await decomposer.run(
            goal_handle=MagicMock(),
            instruction="1단계 하고 2단계 해",
            messages=[stale, {"role": "user", "content": "1단계 하고 2단계 해"}],
            robot_summary={},
            timeout=60.0,
            send_fb=MagicMock(),
        )

        assert task_success is True
        second_messages = node._planner.run_llm_planning_loop.await_args_list[1].args[2]
        assert any("0.0" in m["content"] for m in second_messages)

    async def test_max_steps_cap_enforced(self):
        node = _make_node(max_steps=2)
        node._llm.chat = MagicMock(return_value=json.dumps({
            "steps": [
                {"instruction": "1단계"}, {"instruction": "2단계"},
                {"instruction": "3단계"}, {"instruction": "4단계"},
            ]
        }))
        node._planner.run_llm_planning_loop = AsyncMock(
            return_value=("완료", [SimpleNamespace(success=True)], True, [])
        )
        decomposer = TaskDecomposer(node)

        await decomposer.run(
            goal_handle=MagicMock(),
            instruction="1하고 2하고 3하고 4해",
            messages=_messages("1하고 2하고 3하고 4해"),
            robot_summary={},
            timeout=60.0,
            send_fb=MagicMock(),
        )

        assert node._planner.run_llm_planning_loop.call_count == 2
