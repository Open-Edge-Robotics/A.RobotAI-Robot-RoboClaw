from collections.abc import Mapping
from typing import Any

from geometry_msgs.msg import PoseStamped

from robo_claw_msgs.srv import ExecuteManipulator

from ._ros_sync import wait_for_future_sync
from .exceptions import ManipulationError, ManipulationRuntimeUnavailableError
from .types import ManipulationConfig, PoseGoal


class MoveItPyBackend:
    """ROS 2 서비스를 이용해 C++ 중계 노드와 연동하는 백엔드"""

    def __init__(self, node: Any, config: ManipulationConfig) -> None:
        self._node = node
        self._config = config

        # ROS 2 서비스 클라이언트 생성
        try:
            self._client = self._node.create_client(
                ExecuteManipulator, "/robo_claw_manipulator_node/execute_manipulator"
            )
        except Exception as exc:
            raise ManipulationRuntimeUnavailableError(
                f"MoveIt 서비스 클라이언트 생성 실패: {exc}"
            ) from exc

    def _call_service(self, request: ExecuteManipulator.Request) -> ExecuteManipulator.Response:
        if not self._client.wait_for_service(timeout_sec=5.0):
            raise ManipulationRuntimeUnavailableError(
                "MoveIt C++ 중계 서비스(execute_manipulator)가 활성화되어 있지 않습니다. "
                "manipulator_node 가 정상 실행 중인지 확인하세요."
            )

        future = self._client.call_async(request)

        # 안전한 동기식 대기(데드락 방지). 공통 헬퍼 사용.
        wait_for_future_sync(
            self._node,
            future,
            60.0,
            "MoveIt 서비스 호출 타임아웃 (60초)",
            error_cls=ManipulationError,
        )

        res = future.result()
        if res is None:
            raise ManipulationError("MoveIt 서비스 응답을 받지 못했습니다.")
        return res

    def move_to_named_pose(self, group_name: str, target_name: str) -> None:
        req = ExecuteManipulator.Request()
        req.type = "named_pose"
        req.group_name = group_name
        req.target_name = target_name

        res = self._call_service(req)
        if not res.success:
            raise ManipulationError(f"named pose '{target_name}' 실행 실패: {res.message}")

    def move_to_joint_target(
        self, group_name: str, joint_values: Mapping[str, float]
    ) -> None:
        req = ExecuteManipulator.Request()
        req.type = "joint"
        req.group_name = group_name
        req.joint_names = list(joint_values.keys())
        req.joint_values = [float(v) for v in joint_values.values()]

        res = self._call_service(req)
        if not res.success:
            raise ManipulationError(f"joint target 실행 실패: {res.message}")

    def move_to_pose_target(
        self,
        group_name: str,
        pose: PoseGoal,
        end_effector_link: str,
        cartesian: bool = False,
    ) -> None:
        del cartesian
        req = ExecuteManipulator.Request()
        req.type = "pose"
        req.group_name = group_name
        req.end_effector_link = end_effector_link

        pose_msg = PoseStamped()
        pose_msg.header.frame_id = pose.frame_id
        pose_msg.pose.position.x = pose.position["x"]
        pose_msg.pose.position.y = pose.position["y"]
        pose_msg.pose.position.z = pose.position["z"]
        pose_msg.pose.orientation.x = pose.orientation["x"]
        pose_msg.pose.orientation.y = pose.orientation["y"]
        pose_msg.pose.orientation.z = pose.orientation["z"]
        pose_msg.pose.orientation.w = pose.orientation["w"]

        req.pose_target = pose_msg

        res = self._call_service(req)
        if not res.success:
            raise ManipulationError(f"pose target 실행 실패: {res.message}")

