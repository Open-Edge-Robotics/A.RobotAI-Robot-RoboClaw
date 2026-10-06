"""robo_claw_grpc DB 모델"""

from sqlmodel import Field, SQLModel


class RobotCameraImage(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    robot_id: str = Field(index=True)
    image: str
    format: str = Field(default="RAW", index=True)
    timestamp: int | None = Field(default=None, index=True)


class RobotHistory(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    robot_id: str = Field(index=True)
    is_connected: bool
    battery_percentage: float
    cpu_usage: float
    ram_usage: float
    disk_usage: float
    network_usage: float
    uptime: int
    last_seen: int

    request_type: str | None = Field(default=None)
    request_payload: str | None = Field(default=None)
    response_payload: str | None = Field(default=None)
    image_snapshot: str | None = Field(default=None)
