"""도구 호출의 사용자용 문장을 ROS action 피드백에서 전달하는지 검증합니다."""

from concurrent.futures import Future
from types import SimpleNamespace

import pytest

pytest.importorskip("robo_claw_msgs.action")
from robo_claw_channel.ros_future_utils import send_execute_task

pytestmark = pytest.mark.unit


def ready(value):
    future = Future()
    future.set_result(value)
    return future


def test_assistant_feedback_filters_progress_and_preserves_order():
    delivered = []

    class Client:
        def wait_for_server(self, timeout_sec):
            return True

        def send_goal_async(self, goal, feedback_callback):
            for step in [
                "계획 수립 중",
                "assistant: 먼저 확인합니다.",
                "assistant: ",
                "assistant: 확인했습니다.",
            ]:
                feedback_callback(SimpleNamespace(feedback=SimpleNamespace(current_step=step)))
            assert delivered == ["먼저 확인합니다.", "확인했습니다."]
            return ready(
                SimpleNamespace(
                    accepted=True,
                    get_result_async=lambda: ready(
                        SimpleNamespace(
                            result=SimpleNamespace(
                                success=True,
                                result_message="완료",
                                skill_results=[],
                            )
                        )
                    ),
                )
            )

    result = send_execute_task(Client(), "확인", "", 1, on_assistant=delivered.append)
    assert result["message"] == "완료"


def test_action_without_assistant_callback_keeps_legacy_client_contract():
    class Client:
        def wait_for_server(self, timeout_sec):
            return True

        def send_goal_async(self, goal):
            return ready(
                SimpleNamespace(
                    accepted=True,
                    get_result_async=lambda: ready(
                        SimpleNamespace(
                            result=SimpleNamespace(
                                success=True,
                                result_message="완료",
                                skill_results=[],
                            )
                        )
                    ),
                )
            )

    assert send_execute_task(Client(), "확인", "", 1)["success"] is True
