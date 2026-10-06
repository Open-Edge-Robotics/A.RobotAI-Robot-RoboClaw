"""robo_claw_grpc 메인 엔트리 포인트"""

import asyncio
from threading import Thread, ThreadError

import rclpy
from rclpy.executors import MultiThreadedExecutor

from robo_claw_grpc import serve
from robo_claw_grpc.config import RoboClawGrpcConfig
from robo_claw_grpc.nodes import (
    RoboClawGrpcBatteryNode,
    RoboClawGrpcCameraNode,
    RoboClawGrpcManipulationNode,
    RoboClawGrpcNavNode,
    RoboClawGrpcNode,
)
from robo_claw_grpc.utils import LogHelper, param_string


def main(args=None):
    """robo_claw_grpc 서버 및 ROS2 노드 실행"""
    logger = LogHelper().get_logger(__name__)
    logger.info("Initializing robo_claw_grpc...")

    rclpy.init(args=args)

    config = RoboClawGrpcConfig().get_config()
    config.port = 50051 if config.port is None else config.port
    logger.info(f"Config loaded: {config}")

    main_node = RoboClawGrpcNode()

    camera_topic = param_string(main_node, "camera_topic")
    camera_compressed_topic = param_string(main_node, "camera_compressed_topic")
    battery_topic = param_string(main_node, "battery_topic")
    pose_topic = param_string(main_node, "pose_topic")
    navigate_to_topic = param_string(main_node, "navigate_to_topic")
    map_topic = param_string(main_node, "map_topic")

    battery_node = RoboClawGrpcBatteryNode(battery_topic=battery_topic)
    nav_node = RoboClawGrpcNavNode(
        pose_topic=pose_topic,
        navigate_to_topic=navigate_to_topic,
        map_topic=map_topic,
    )
    camera_node = RoboClawGrpcCameraNode(
        camera_topic=camera_topic,
        camera_compressed_topic=camera_compressed_topic,
    )
    manipulation_node = RoboClawGrpcManipulationNode()

    executor = MultiThreadedExecutor()
    executor.add_node(main_node)
    executor.add_node(battery_node)
    executor.add_node(nav_node)
    executor.add_node(camera_node)
    executor.add_node(manipulation_node)

    ros_thread = Thread(target=executor.spin, daemon=True)
    server_thread = Thread(
        target=asyncio.run,
        args=(
            serve(
                config=config,
                manipulation_node=manipulation_node,
                camera_node=camera_node,
                nav_node=nav_node,
                battery_node=battery_node,
                grpc_node=main_node,
            ),
        ),
        daemon=True,
    )

    try:
        ros_thread.start()
        server_thread.start()
        ros_thread.join()
        server_thread.join()
    except KeyboardInterrupt:
        logger.info("Terminated by keyboard interrupt")
    except ThreadError as e:
        logger.error(f"Thread error: {e}")
    except Exception as e:
        logger.error(f"Error occurred: {e}")
    finally:
        main_node.destroy_node()
        battery_node.destroy_node()
        nav_node.destroy_node()
        camera_node.destroy_node()
        manipulation_node.destroy_node()
        executor.shutdown()
        logger.info("robo_claw_grpc shutdown complete")


if __name__ == "__main__":
    main()
