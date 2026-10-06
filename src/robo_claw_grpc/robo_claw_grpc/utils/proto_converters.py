"""ROS 메시지 ↔ gRPC proto 변환 공통 헬퍼.

``robot_navigation_service``/``robot_camera_service``/``robot_status_service``
세 서비스에서 반복되던 "ROS Pose → RobotPose proto" 변환과 "call_execute_skill
결과 → ActionResult proto" 변환을 하나로 묶는다.
"""

from typing import Any

from robo_claw_grpc.robo_pb2 import ActionResult, Point, Quaternion, RobotPose
from robo_claw_grpc.utils import generate_index


def ros_pose_to_proto(pose: Any, robot_id: str) -> RobotPose:
    """geometry_msgs/Pose(``position``+``orientation``) → RobotPose proto.

    ``pose`` 는 ``.position``/``.orientation`` 필드를 가진 ROS Pose 객체.
    ``PoseStamped.pose`` 또는 ``PoseWithCovarianceStamped.pose.pose`` 의 inner
    Pose 를 직접 전달한다.
    """
    p = pose.position
    o = pose.orientation
    return RobotPose(
        id=generate_index(),
        robot_id=robot_id,
        position=Point(x=p.x, y=p.y, z=p.z),
        orientation=Quaternion(x=o.x, y=o.y, z=o.z, w=o.w),
    )


def skill_result_to_action_result(
    result: Any, *, success_message: str, fail_prefix: str
) -> ActionResult:
    """``call_execute_skill`` 결과(ExecuteSkill.Response) → ActionResult proto.

    ``result`` 가 None 이거나 ``result.result.code != 0`` 이면 실패 ActionResult 를
    반환한다. 성공 시 ``result.result.message`` 가 비어 있으면 ``success_message``
    를, 실패 시 ``fail_prefix`` 로 접두사를 붙인다.
    """
    if result and result.result.code == 0:
        msg = result.result.message or success_message
        return ActionResult(success=True, message=msg)
    msg = result.result.message if result else "서비스 호출 실패"
    return ActionResult(success=False, message=f"{fail_prefix}: {msg}")
