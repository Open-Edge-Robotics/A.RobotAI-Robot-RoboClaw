"""robo_claw_grpc 서비스 패키지.

일부 서비스는 ROS2 노드(rclpy/cv_bridge) 에 의존하므로, 패키지 임포트
시점이 아닌 명시적 접근 시에만 로드되도록 지연 임포트를 사용한다.
"""

_SERVICE_MAP = {
    "PingService": "robo_claw_grpc.services.ping_service",
    "RobotInfoService": "robo_claw_grpc.services.robot_info_service",
    "RobotCameraService": "robo_claw_grpc.services.robot_camera_service",
    "RobotHistoryService": "robo_claw_grpc.services.robot_history_service",
    "RobotNavigationService": "robo_claw_grpc.services.robot_navigation_service",
    "RobotStatusService": "robo_claw_grpc.services.robot_status_service",
    "RobotCommandService": "robo_claw_grpc.services.robot_command_service",
    "RobotManipulationService": "robo_claw_grpc.services.robot_manipulation_service",
    "FileTransferService": "robo_claw_grpc.services.file_transfer_service",
    "RobotSkillService": "robo_claw_grpc.services.robot_skill_service",
    "RobotTaskService": "robo_claw_grpc.services.robot_task_service",
}


def __getattr__(name: str):
    if name in _SERVICE_MAP:
        import importlib

        module = importlib.import_module(_SERVICE_MAP[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(_SERVICE_MAP.keys())
