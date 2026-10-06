"""``robo_claw_msgs/SkillResult`` 메시지 빌더.

ExecuteTask(action) 핸들러(execution.py)와 ExecuteSkill(service) 핸들러(node.py)에
걸쳐 복제되던 ``SkillResultMsg`` 생성 보일러플레이트를 한 곳으로 통합한다.
"""

import json
from typing import Any

from robo_claw_msgs.msg import SkillResult as SkillResultMsg

from ..types import SkillResultCode


def build_skill_result_msg(clock: Any, skill_result: Any) -> SkillResultMsg:
    """``SkillResult`` 객체를 ROS ``SkillResultMsg`` 로 변환.

    Args:
        clock: ``node.get_clock()`` 처럼 ``now()`` 를 가진 객체 (header stamp 용).
        skill_result: ``skill_manager.SkillResult`` (skill_name/success/message/
            duration_sec/result_data 필드).
    """
    msg = SkillResultMsg()
    msg.header.stamp = clock.now().to_msg()
    msg.skill_name = str(skill_result.skill_name or "")
    result_data = skill_result.result_data or {}
    status = str(result_data.get("status", "")).strip().lower()
    if status == "timeout" or result_data.get("failure_reason") == "timeout":
        code = SkillResultCode.TIMEOUT
    elif status == "cancelled" or result_data.get("failure_reason") == "cancelled":
        code = SkillResultCode.CANCELLED
    else:
        code = SkillResultCode.SUCCESS if skill_result.success else SkillResultCode.FAILURE
    msg.code = int(code)
    msg.message = str(skill_result.message or "")
    msg.duration_sec = float(skill_result.duration_sec or 0.0)
    msg.result_json = str(json.dumps(result_data, ensure_ascii=False))
    return msg


def build_failure_skill_result_msg(
    clock: Any, skill_name: str, message: str
) -> SkillResultMsg:
    """실패 응답용 ``SkillResultMsg`` 를 간편 생성.

    ExecuteSkill 핸들러에서 파라미터 검증 실패 등 실행 전 단계의 실패 응답에 사용.
    """
    msg = SkillResultMsg()
    msg.header.stamp = clock.now().to_msg()
    msg.skill_name = str(skill_name or "")
    msg.code = int(SkillResultCode.FAILURE)
    msg.message = str(message)
    msg.duration_sec = 0.0
    msg.result_json = "{}"
    return msg
