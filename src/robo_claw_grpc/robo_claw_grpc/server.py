"""robo_claw_grpc gRPC 서버"""

import asyncio
from contextlib import suppress
from threading import ThreadError
from typing import Any

from grpc.aio import server

from robo_claw_grpc.fleet_connector import (
    FleetConnector,
    FleetConnectorConfig,
    RoboClawCommandHandlers,
    TelemetryProvider,
    build_capabilities,
)
from robo_claw_grpc.models import ConfigModel
from robo_claw_grpc.nodes import (
    RoboClawGrpcBatteryNode,
    RoboClawGrpcCameraNode,
    RoboClawGrpcManipulationNode,
    RoboClawGrpcNavNode,
)
from robo_claw_grpc.robo_pb2_grpc import add_RosGrpcServicer_to_server
from robo_claw_grpc.servicer import RoboClawGrpcServicer
from robo_claw_grpc.utils import LogHelper


async def serve(
    config: ConfigModel,
    manipulation_node: RoboClawGrpcManipulationNode | None = None,
    camera_node: RoboClawGrpcCameraNode | None = None,
    nav_node: RoboClawGrpcNavNode | None = None,
    battery_node: RoboClawGrpcBatteryNode | None = None,
    grpc_node: Any | None = None,
) -> None:
    """gRPC 서버 시작"""
    logger = LogHelper(level="DEBUG" if config.debug else "INFO").get_logger(__name__)
    logger.info("Starting gRPC server...")

    grpc_server = server()
    servicer = RoboClawGrpcServicer(
        config=config,
        manipulation_node=manipulation_node,
        camera_node=camera_node,
        nav_node=nav_node,
        battery_node=battery_node,
        grpc_node=grpc_node,
    )
    add_RosGrpcServicer_to_server(servicer=servicer, server=grpc_server)
    await servicer.initialize()

    listen_addr = f"[::]:{config.port}"
    grpc_server.add_insecure_port(listen_addr)
    logger.info(f"Server started: {listen_addr}")

    connector = None
    connector_task = None
    fleet_config = FleetConnectorConfig.from_env()
    if fleet_config.enabled:
        logger.info(
            "Starting outbound FleetControl connector: robot=%s target=%s policy=%s",
            fleet_config.robot_id,
            fleet_config.target,
            fleet_config.disconnect_policy.value,
        )
        connector = FleetConnector(
            fleet_config,
            RoboClawCommandHandlers(grpc_node, camera_node),
            build_capabilities(fleet_config.skills_file),
            TelemetryProvider(fleet_config, battery_node, nav_node),
        )
        connector_task = asyncio.create_task(connector.run())

    try:
        await grpc_server.start()
        await grpc_server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Server terminated by keyboard interrupt")
        await grpc_server.stop(0)
    except ThreadError as e:
        logger.error(f"Thread error: {e}")
        await grpc_server.stop(0)
    except Exception as e:
        logger.error(f"Server error: {e}")
        await grpc_server.stop(0)
    finally:
        if connector is not None:
            await connector.stop()
        if connector_task is not None:
            connector_task.cancel()
            with suppress(asyncio.CancelledError):
                await connector_task
        logger.info("Server shutdown complete")
