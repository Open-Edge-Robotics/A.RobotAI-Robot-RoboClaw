"""CLOiD 포장(최소 부피) boxing 자세 실행 스킬.

CLOiD 는 출하 포장 시 부피를 줄이기 위해 가장 움츠린 boxing 자세
(``cloid.move_boxing``: 허리를 한계까지 숙이고 양팔을 앞으로 접음)를 사용한다. 이
스킬은 로봇 측 tofu 의 script_runner action(``/script_runner/run_script``)에 그
실행을 요청하고, 자식 스크립트가 정상 종료했는지만 확인한다. 실제 자세 도달은
확인하지 않으며, 정지 상태에서만 사용한다.

ROS 인터페이스(``tofu_msgs``, ``rclpy``)는 지연 import 하여 이 모듈이 ROS 없이도
import 되도록 유지한다.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from robo_claw_agent.ros_future import wait_for_future as _wait_for_future_common
from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills._param_utils import bounded_timeout as _bounded_timeout

logger = logging.getLogger(__name__)

SCRIPT_RUNNER_ACTION = "/script_runner/run_script"
TOFUPY_PACKAGE = "tofupy"
PACKING_POSE_EXECUTABLE = "cloid.move_boxing"

_DEFAULT_TIMEOUT_SEC = 60.0
_MIN_TIMEOUT_SEC = 5.0
_MAX_TIMEOUT_SEC = 180.0
_SERVER_WAIT_SEC = 5.0
_GOAL_ACCEPT_TIMEOUT_SEC = 10.0
_RESULT_MARGIN_SEC = 5.0


def _wait_for_future(future: Any, timeout_sec: float) -> tuple[bool, Any]:
    """executor 가 이미 spin 중이므로 future 완료만 이벤트로 대기한다."""
    return _wait_for_future_common(
        future,
        timeout_sec,
        label="CLOiD packing pose action",
        return_timeout_value_on_error=True,
    )


def _create_run_script_client(node: Any) -> tuple[Any, type[Any]]:
    """tofu script_runner action client 를 지연 생성한다(ROS-free import 유지)."""
    from rclpy.action import ActionClient

    from tofu_msgs.action import RunScript

    return ActionClient(node, RunScript, SCRIPT_RUNNER_ACTION), RunScript


def request_packing_pose(skill: Any, timeout_sec: float) -> dict[str, Any]:
    """tofu script_runner 로 ``cloid.move_boxing`` 실행을 요청하고 스크립트 종료만 확인한다.

    공개 스킬(``set_cloid_packing_pose``)과 주행 전 prelude 스킬
    (``cloid_navigation_pose``)이 같은 tofu 실행 계약을 공유하도록 분리한 헬퍼다.
    confirm/timeout/동시 실행 잠금 검증은 호출한 스킬이 담당하며, 이 함수는 검증 없이
    요청과 결과 대기만 수행한다.
    """
    client = None
    try:
        try:
            client, message_type = _create_run_script_client(skill.node)
        except (ImportError, AttributeError) as exc:
            logger.error("CLOiD script_runner interface is unavailable: %s", exc)
            return skill.fail_result(
                "tofu_msgs/action/RunScript 인터페이스를 사용할 수 없습니다. "
                "CLOiD 메시지 패키지를 RoboClaw 런타임에 설치해야 합니다.",
                failure_reason="motion_interface_unavailable",
            )

        if not client.wait_for_server(timeout_sec=_SERVER_WAIT_SEC):
            return skill.fail_result(
                f"tofu script_runner 서버를 찾을 수 없습니다: {SCRIPT_RUNNER_ACTION}",
                failure_reason="script_runner_unavailable",
                executable=PACKING_POSE_EXECUTABLE,
            )

        goal = message_type.Goal()
        goal.package = TOFUPY_PACKAGE
        goal.executable = PACKING_POSE_EXECUTABLE
        goal.args = []
        goal.timeout = timeout_sec
        goal.stream_output = False

        ok, goal_handle = _wait_for_future(client.send_goal_async(goal), _GOAL_ACCEPT_TIMEOUT_SEC)
        if not ok:
            return skill.fail_result(
                "CLOiD 포장 자세 요청 전송 시간이 초과되었습니다.",
                failure_reason="action_timeout",
                status="timeout",
                executable=PACKING_POSE_EXECUTABLE,
                completion_confirmed=False,
                recoverable=True,
            )
        if goal_handle is None or not goal_handle.accepted:
            return skill.fail_result(
                "CLOiD script_runner 가 포장 자세 요청을 거절했습니다.",
                failure_reason="goal_rejected",
                executable=PACKING_POSE_EXECUTABLE,
            )

        ok, wrapped = _wait_for_future(
            goal_handle.get_result_async(), timeout_sec + _RESULT_MARGIN_SEC
        )
        if not ok or wrapped is None:
            return skill.fail_result(
                "CLOiD 포장 자세 실행 결과를 시간 내에 받지 못했습니다. "
                "이미 시작된 동작의 상태는 별도로 확인해야 합니다.",
                failure_reason="action_timeout",
                status="timeout",
                executable=PACKING_POSE_EXECUTABLE,
                completion_confirmed=False,
                recoverable=True,
            )

        result = wrapped.result
        result_message = str(getattr(result, "message", ""))
        exit_code = int(getattr(result, "exit_code", -1))
        output_tail = str(getattr(result, "output_tail", ""))

        if not bool(getattr(result, "success", False)):
            return skill.fail_result(
                f"CLOiD 포장 자세 실행이 실패했습니다: {result_message or '알 수 없는 오류'}",
                failure_reason=(
                    "action_timeout" if "timeout" in result_message.lower() else "motion_failed"
                ),
                status="failed",
                executable=PACKING_POSE_EXECUTABLE,
                exit_code=exit_code,
                output_tail=output_tail,
                completion_confirmed=False,
            )

        return skill.success_result(
            "CLOiD 포장용 최소(boxing) 자세 실행 요청을 완료했습니다. "
            "스크립트는 정상 종료했지만 실제 자세 도달은 확인하지 않았습니다. "
            "팔을 움직이는 모션이 실행되면 자세가 풀립니다.",
            status="completed",
            pose="packing",
            executable=PACKING_POSE_EXECUTABLE,
            exit_code=exit_code,
            output_tail=output_tail,
            completion_confirmed=False,
        )
    finally:
        if client is not None:
            try:
                client.destroy()
            except Exception as exc:  # noqa: BLE001
                logger.debug("CLOiD script_runner client destroy failed: %s", exc)


class SetCloidPackingPoseSkill(BaseSkill):
    """CLOiD 를 포장용 최소 부피(boxing) 자세로 만드는 실행을 요청한다."""

    name = "set_cloid_packing_pose"
    risk_level = "dangerous"
    allow_with_others = False
    answer_mode = "action"
    side_effects = ("arm_motion",)
    exclusive_resources = ("cloid_motion",)
    input_schema = {
        "type": "object",
        "properties": {
            "confirm": {"type": "boolean"},
            "timeout_sec": {
                "type": "number",
                "minimum": _MIN_TIMEOUT_SEC,
                "maximum": _MAX_TIMEOUT_SEC,
            },
        },
        "required": ["confirm"],
        "additionalProperties": False,
    }
    description = (
        "CLOiD 를 포장용 최소 부피 자세(boxing, 허리·양팔 최대 접힘)로 만드는 실행을 "
        "요청합니다. 사용자의 명시적 요청(confirm=true)이 필요하며 정지 상태에서만 "
        "사용합니다. 스크립트 정상 종료만 확인하고 실제 자세 도달은 확인하지 않습니다."
    )
    preconditions = (
        "CLOi cloid 프로필에서만 등록됩니다.",
        "tofu_control 과 script_runner 가 실행 중이어야 합니다.",
        "주행 중이 아닌 정지 상태에서만 실행합니다.",
    )
    postconditions = ("CLOiD boxing 자세 실행 요청이 발행되며 실제 자세 도달은 확인하지 않습니다.",)

    def __init__(self) -> None:
        super().__init__()
        self._publish_lock = threading.Lock()

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if (error := self.require_node()) is not None:
            return error

        if params.get("confirm") is not True:
            return self.fail_result(
                "포장 자세 실행은 사용자의 명시적 요청이 필요합니다 (confirm=true).",
                failure_reason="safety_rejected",
            )

        timeout_sec = _bounded_timeout(
            params.get("timeout_sec"),
            default=_DEFAULT_TIMEOUT_SEC,
            minimum=_MIN_TIMEOUT_SEC,
            maximum=_MAX_TIMEOUT_SEC,
        )
        if timeout_sec is None:
            return self.fail_result(
                f"실행 타임아웃은 {_MIN_TIMEOUT_SEC:.0f}~{_MAX_TIMEOUT_SEC:.0f}초 범위여야 합니다.",
                failure_reason="invalid_timeout",
            )

        if not self._publish_lock.acquire(blocking=False):
            return self.fail_result(
                "다른 CLOiD 포장 자세 요청을 처리 중입니다.",
                failure_reason="motion_busy",
                recoverable=True,
            )

        try:
            return request_packing_pose(self, timeout_sec)
        finally:
            self._publish_lock.release()
