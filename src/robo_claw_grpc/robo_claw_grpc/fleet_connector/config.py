"""FleetControl outbound connector configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class DisconnectPolicy(str, Enum):
    """FleetControl disconnect policy (str-Enum for Python 3.10 compatibility)."""

    COMPLETE = "complete"
    STOP = "stop"
    FINISH_ATOMIC = "finish_atomic"

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class FleetConnectorConfig:
    maestro_ip: str
    robot_port: int
    robot_id: str
    site_id: str = ""
    map_id: str = ""
    map_version: str = ""
    map_frame_id: str = "map"
    skills_file: str = ""
    disconnect_policy: DisconnectPolicy = DisconnectPolicy.COMPLETE
    tls: bool = False
    ca_cert: str = ""
    client_cert: str = ""
    client_key: str = ""
    journal_path: str = "/tmp/robo_claw_fleet_commands.sqlite3"
    heartbeat_sec: float = 1.0

    @property
    def enabled(self) -> bool:
        return bool(self.maestro_ip)

    @property
    def target(self) -> str:
        return f"{self.maestro_ip}:{self.robot_port}"

    @classmethod
    def from_env(cls) -> FleetConnectorConfig:
        port = int(os.getenv("ROBOT_PORT", "50053"))
        if not 1 <= port <= 65535:
            raise ValueError("ROBOT_PORT must be between 1 and 65535")
        robot_id = os.getenv("ROBOT_ID", "").strip()
        maestro_ip = os.getenv("MAESTRO_IP", "").strip()
        if maestro_ip and not robot_id:
            raise ValueError("ROBOT_ID is required when MAESTRO_IP is set")
        try:
            policy = DisconnectPolicy(os.getenv("MAESTRO_DISCONNECT_POLICY", "complete").lower())
        except ValueError as exc:
            raise ValueError("invalid MAESTRO_DISCONNECT_POLICY") from exc
        heartbeat = float(os.getenv("FLEET_HEARTBEAT_SEC", "1"))
        if heartbeat <= 0:
            raise ValueError("FLEET_HEARTBEAT_SEC must be positive")
        skills_file = os.getenv("RC_SKILLS_GUIDE_FILE", "").strip()
        if not skills_file:
            for candidate in ("/ros2_ws/docs/SKILLS.md", "docs/SKILLS.md"):
                if Path(candidate).is_file():
                    skills_file = candidate
                    break
        return cls(
            maestro_ip=maestro_ip,
            robot_port=port,
            robot_id=robot_id,
            site_id=os.getenv("ROBOT_SITE_ID", ""),
            map_id=os.getenv("ROBOT_MAP_ID", ""),
            map_version=os.getenv("ROBOT_MAP_VERSION", ""),
            map_frame_id=os.getenv("ROBOT_MAP_FRAME_ID", "map"),
            skills_file=skills_file,
            disconnect_policy=policy,
            tls=os.getenv("FLEET_CONTROL_TLS", "false").lower() in {"1", "true", "yes"},
            ca_cert=os.getenv("FLEET_CONTROL_CA_CERT", ""),
            client_cert=os.getenv("FLEET_CONTROL_CLIENT_CERT", ""),
            client_key=os.getenv("FLEET_CONTROL_CLIENT_KEY", ""),
            journal_path=os.getenv(
                "FLEET_COMMAND_JOURNAL_PATH", "/tmp/robo_claw_fleet_commands.sqlite3"
            ),
            heartbeat_sec=heartbeat,
        )
