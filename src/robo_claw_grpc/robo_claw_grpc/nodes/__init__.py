"""robo_claw_grpc ROS2 노드 패키지.

모든 노드는 rclpy 에 의존하므로, 패키지 임포트 시점이 아닌 명시적
접근 시에만 로드되도록 지연 임포트를 사용한다. 이를 통해 테스트 등
ROS2 환경 없이도 상위 services/repositories 모듈을 개별 import 할 수 있다.
"""

_NODE_MAP = {
    "RoboClawGrpcNode": "robo_claw_grpc.nodes.main_node",
    "RoboClawGrpcBatteryNode": "robo_claw_grpc.nodes.battery_node",
    "RoboClawGrpcNavNode": "robo_claw_grpc.nodes.nav_node",
    "RoboClawGrpcCameraNode": "robo_claw_grpc.nodes.camera_node",
    "RoboClawGrpcManipulationNode": "robo_claw_grpc.nodes.manipulation_node",
}


def __getattr__(name: str):
    if name in _NODE_MAP:
        import importlib

        module = importlib.import_module(_NODE_MAP[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(_NODE_MAP.keys())
