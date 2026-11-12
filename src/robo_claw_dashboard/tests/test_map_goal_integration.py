"""Exercise rotated-map chat submission and its context/proof contract without hardware."""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import pytest
from robo_claw_dashboard.application.http_router import route_http
from robo_claw_dashboard.application.service import DashboardService
from robo_claw_dashboard.domain.localization import build_map_snapshot

pytestmark = pytest.mark.integration
WEB = Path(__file__).resolve().parents[1] / "web"


def message(x, y):
    return (
        "지도에서 선택한 목표로 이동해 주세요. 로봇: former0047. 기준 프레임: map. "
        f"목표 좌표: x={x:.6f} m, y={y:.6f} m. "
        "필요한 주행 안전 절차를 먼저 적용하고, 목표 좌표와 프레임을 변경하지 마세요."
    )


class MapBackend:
    def __init__(self):
        self.map = build_map_snapshot(
            width=4,
            height=4,
            resolution=0.5,
            origin={"x": 0.0, "y": 0.0, "yaw": math.pi / 2},
            frame_id="map",
            cells=[0] * 16,
            stamp_ns=1,
            topic="/map",
        )
        self.agent = {
            "agent_id": "former0047",
            "navigation_frame": "map",
            "map_navigation_request": None,
        }
        self.calls = []
        self.entered, self.release, self.finished = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )

    def query_state(self):
        return json.dumps(self.agent)

    def latest_agent_status(self):
        return {}

    def latest_telemetry(self):
        return {}

    def latest_sensor_health(self):
        return {}

    def list_peers(self):
        return []

    def latest_map(self):
        return self.map

    def latest_pose(self):
        return {"available": True, "frame_id": "map", "x": -0.5, "y": 0.5, "yaw": 0.0}

    def send_chat(self, text, **kwargs):
        self.calls.append((text, kwargs))
        self.entered.set()
        if not self.release.wait(timeout=5):
            self.finished.set()
            return {"success": False, "error": "fake result gate timed out"}
        ctx = json.loads(kwargs["context_json"])
        proof = {
            "request_id": ctx["request_id"],
            "robot_id": ctx["robot_id"],
            "status": "reached",
            "message": "fake target reached",
            "target": {"x": ctx["x"], "y": ctx["y"], "frame_id": ctx["frame_id"]},
            "geometry_id": ctx["geometry_id"],
            "execution_started": True,
            "cancel_confirmed": False,
            "reason": "",
            "progress": 100,
            "created_at": ctx["created_at"],
            "updated_at": time.time(),
            "can_submit": True,
        }
        self.agent["map_navigation_request"] = proof
        self.finished.set()
        return {
            "success": True,
            "message": "fake navigation completed",
            "skill_results": [
                {
                    "skill_name": "navigate_to",
                    "code": 0,
                    "result_json": json.dumps(
                        {"x": ctx["x"], "y": ctx["y"], "map_goal_request": proof}
                    ),
                }
            ],
        }


def request(service, body):
    return route_http(
        "POST", "/api/chat", static_root=WEB, service=service, body=json.dumps(body).encode()
    )


def test_map_goal_chat_is_async_and_preserves_the_selected_rotated_world_point():
    backend = MapBackend()
    service = DashboardService(backend)
    service.status()
    snapshot = service.map_snapshot()
    # Local grid cell (col=1,row=0) is world (-0.25,+0.75) at origin yaw pi/2.
    body = {
        "message": message(-0.25, 0.75),
        "request_id": "map-integration-1",
        "map_goal": {
            "x": -0.25,
            "y": 0.75,
            "frame_id": "map",
            "robot_id": "former0047",
            "geometry_id": snapshot["geometry_id"],
        },
    }
    try:
        result = request(service, body)
        assert result.status == 202
        response = json.loads(result.body)
        assert response["request"]["request_id"] == "map-integration-1"
        assert response["request"]["status"] != "reached"
        assert backend.entered.wait(timeout=2)
        text, kwargs = backend.calls[0]
        assert text == body["message"]
        assert "[skill]" not in text and "navigate_to" not in text
        ctx = json.loads(kwargs["context_json"])
        assert ctx["type"] == "dashboard_map_goal_v1"
        assert ctx["x"] == -0.25 and ctx["y"] == 0.75
        assert ctx["frame_id"] == "map" and ctx["map_topic"] == "/map"
        assert ctx["geometry_id"] == snapshot["geometry_id"]
        assert kwargs["timeout_sec"] == 180
        conflict = request(service, {**body, "request_id": "map-integration-2"})
        assert conflict.status == 409
        assert len(backend.calls) == 1
        backend.release.set()
        assert backend.finished.wait(timeout=2)
        final = service.status()["navigation_request"]
        assert final["status"] == "reached"
        assert final["target"] == {"x": -0.25, "y": 0.75, "frame_id": "map"}
    finally:
        backend.release.set()
        if hasattr(service, "close"):
            service.close()


def test_wrong_frame_cannot_fall_back_to_ordinary_chat():
    backend = MapBackend()
    service = DashboardService(backend)
    service.status()
    snapshot = service.map_snapshot()
    body = {
        "message": message(-0.25, 0.75),
        "request_id": "map-invalid-frame",
        "map_goal": {
            "x": -0.25,
            "y": 0.75,
            "frame_id": "odom",
            "robot_id": "former0047",
            "geometry_id": snapshot["geometry_id"],
        },
    }
    try:
        result = request(service, body)
        assert result.status == 400
        assert not backend.calls
    finally:
        backend.release.set()
        if hasattr(service, "close"):
            service.close()
