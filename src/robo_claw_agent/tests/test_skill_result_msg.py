import json
from types import SimpleNamespace

import pytest

pytest.importorskip("robo_claw_msgs.msg")

from robo_claw_agent.agent_node.skill_result_msg import build_skill_result_msg
from robo_claw_agent.skill_manager import SkillResult
from robo_claw_agent.types import SkillResultCode


class _Clock:
    def now(self):
        return SimpleNamespace(
            to_msg=lambda: SimpleNamespace(sec=1, nanosec=0),
        )


@pytest.mark.unit
def test_build_skill_result_msg_preserves_timeout_code_and_details():
    result = SkillResult(
        "navigate_to",
        False,
        "timeout",
        1.5,
        {
            "success": False,
            "status": "timeout",
            "failure_reason": "timeout",
            "cancel_requested": True,
            "cancel_confirmed": False,
        },
    )

    msg = build_skill_result_msg(_Clock(), result)

    assert msg.code == int(SkillResultCode.TIMEOUT)
    assert json.loads(msg.result_json)["cancel_requested"] is True
    assert json.loads(msg.result_json)["cancel_confirmed"] is False


@pytest.mark.unit
def test_build_skill_result_msg_preserves_cancelled_code():
    result = SkillResult(
        "stop",
        False,
        "cancelled",
        0.2,
        {"success": False, "status": "cancelled", "failure_reason": "cancelled"},
    )

    msg = build_skill_result_msg(_Clock(), result)

    assert msg.code == int(SkillResultCode.CANCELLED)
