"""robo_claw_grpc gRPC 데코레이터"""

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import grpc

from robo_claw_grpc.exceptions import (
    ConfigurationError,
    DatabaseError,
    ResourceNotFoundError,
    RoboClawGrpcError,
)


def handle_grpc_errors(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """gRPC 예외 처리 데코레이터"""

    @functools.wraps(func)
    async def wrapper(self, request: Any, context: grpc.aio.ServicerContext, *args, **kwargs) -> Any:
        logger = getattr(self, "_logger", logging.getLogger(__name__))
        try:
            return await func(self, request, context, *args, **kwargs)
        except ResourceNotFoundError as e:
            logger.warning(f"Resource not found in {func.__name__}: {e}")
            await context.abort(grpc.StatusCode.NOT_FOUND, str(e))
        except (DatabaseError, ConfigurationError) as e:
            logger.error(f"Internal error in {func.__name__}: {e}")
            await context.abort(grpc.StatusCode.INTERNAL, "Internal Server Error")
        except RoboClawGrpcError as e:
            logger.error(f"robo_claw_grpc error in {func.__name__}: {e}")
            await context.abort(grpc.StatusCode.UNKNOWN, str(e))
        except Exception as e:
            logger.exception(f"Unexpected error in {func.__name__}: {e}")
            await context.abort(grpc.StatusCode.UNKNOWN, "An unexpected error occurred")

    return wrapper


def log_to_history(
    include_request: bool = True,
    include_response: bool = True,
    capture_image: bool = False,
):
    """gRPC 요청/응답을 robot history에 기록하는 데코레이터"""

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapper(self, request: Any, context: grpc.aio.ServicerContext, *args, **kwargs) -> Any:
            import json
            import time

            from google.protobuf.json_format import MessageToDict

            from robo_claw_grpc.models.robot_history import RobotHistory
            from robo_claw_grpc.utils.system_stats import get_system_stats

            logger = getattr(self, "_logger", logging.getLogger(__name__))
            repo = getattr(self, "_repo", None)
            robot_id = getattr(self, "_robot_id", "unknown")
            battery_node = getattr(self, "_battery_node", None)

            if repo is None:
                return await func(self, request, context, *args, **kwargs)

            request_payload = ""
            response_payload = ""

            if include_request:
                try:
                    request_dict = MessageToDict(request, preserving_proto_field_name=True)
                    request_payload = json.dumps(request_dict, ensure_ascii=False)
                except Exception:
                    request_payload = str(request)

            response = await func(self, request, context, *args, **kwargs)

            if include_response and response is not None:
                try:
                    response_dict = MessageToDict(response, preserving_proto_field_name=True)
                    response_payload = json.dumps(response_dict, ensure_ascii=False)
                except Exception:
                    response_payload = str(response)

            image_snapshot = None
            if capture_image:
                camera_node = getattr(self, "_camera_node", None)
                if camera_node:
                    try:
                        img, _, _ = camera_node.get_latest_image()
                        image_snapshot = img
                    except Exception:
                        pass

            try:
                stats = get_system_stats()
                battery_pct = 0.0
                if battery_node:
                    try:
                        battery_pct = battery_node.get_battery_status()
                    except Exception:
                        pass

                history = RobotHistory(
                    robot_id=robot_id,
                    is_connected=True,
                    battery_percentage=battery_pct,
                    cpu_usage=stats["cpu_usage"],
                    ram_usage=stats["ram_usage"],
                    disk_usage=stats["disk_usage"],
                    network_usage=0.0,
                    uptime=stats["uptime"],
                    last_seen=int(time.time()),
                    request_type=func.__name__,
                    request_payload=request_payload,
                    response_payload=response_payload,
                    image_snapshot=image_snapshot,
                )
                await repo.add_history(history)
            except Exception as e:
                logger.warning(f"History logging failed in {func.__name__}: {e}")

            return response

        return wrapper

    return decorator
