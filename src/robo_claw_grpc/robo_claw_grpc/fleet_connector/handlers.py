"""Adapters from FleetControl commands to existing Robo-Claw ROS clients."""

from __future__ import annotations

import json
import time
from typing import Any


class RoboClawCommandHandlers:
    def __init__(self, grpc_node, camera_node=None) -> None:
        self.grpc_node = grpc_node
        self.camera_node = camera_node

    async def execute_task(self, instruction: str, timeout_sec: float) -> dict[str, Any]:
        result = await self.grpc_node.call_execute_task(instruction, timeout_sec=timeout_sec)
        if result is None:
            return {"success": False, "message": "ExecuteTask returned no result"}
        value = result.result if hasattr(result, "result") else result
        return {
            "success": bool(getattr(value, "success", False)),
            "message": str(getattr(value, "result_message", "")),
        }

    async def execute_skill(self, name: str, params: dict, timeout_sec: float) -> dict[str, Any]:
        result = await self.grpc_node.call_execute_skill(name, params, timeout_sec)
        if result is None:
            return {"success": False, "message": "ExecuteSkill returned no result"}
        value = getattr(result, "result", result)
        result_json = str(getattr(value, "result_json", "") or "")
        try:
            detail = json.loads(result_json) if result_json else {}
        except json.JSONDecodeError:
            detail = {"raw_result": result_json}
        return {
            "success": int(getattr(value, "code", 1)) == 0,
            "message": str(getattr(value, "message", "")),
            "result": detail,
        }

    async def emergency_stop(self, reason: str) -> dict[str, Any]:
        result = await self.execute_skill("stop", {}, 10.0)
        if reason:
            result.setdefault("reason", reason)
        return result

    async def request_snapshot(self) -> dict[str, Any]:
        if self.camera_node is None:
            return {"success": False, "message": "camera is unavailable"}
        image, image_format, timestamp = self.camera_node.get_latest_image(1)
        return {
            "success": bool(image),
            "message": "snapshot captured" if image else "snapshot unavailable",
            "image_base64": image or "",
            "image_format": image_format or "image/jpeg",
            "timestamp_ms": int((timestamp or time.time()) * 1000),
        }

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        return {
            "success": False,
            "message": f"cancel is not supported for task {task_id}",
            "error_code": "CANCEL_UNSUPPORTED",
        }
