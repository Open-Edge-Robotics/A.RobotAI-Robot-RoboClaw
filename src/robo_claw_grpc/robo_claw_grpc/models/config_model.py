"""robo_claw_grpc 설정 모델"""

from dataclasses import dataclass
from enum import Enum


class RobotType(Enum):
    TEST = 0
    FORMER = 1
    STRETCH = 2
    EXTRA = 3


@dataclass
class ConfigModel:
    """robo_claw_grpc 설정 데이터클래스"""

    port: int
    robot_id: str
    robot_name: str
    robot_description: str
    robot_type: str
    debug: bool
    max_history_count: int
    max_db_size_mb: int
    file_transfer_dir: str
