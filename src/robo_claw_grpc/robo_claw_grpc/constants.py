"""robo_claw_grpc 상수 정의"""

from robo_claw_grpc.models.config_model import RobotType

APP_NAME = "robo_claw_grpc"
APP_VERSION = "0.1.0"

DATABASE_NAME = "robo_claw_grpc.db"

DEFAULT_CONFIG_PATH = "robo_claw_grpc"
DEFAULT_CONFIG_FILE_NAME = "config.yaml"

DEFAULT_SERVER_PORT = 50051
DEFAULT_FILE_TRANSFER_DIR = "transfers"

DEFAULT_ROBOT_ID = "1"
DEFAULT_ROBOT_NAME = "RoboClaw Robot"
DEFAULT_ROBOT_DESCRIPTION = "RoboClaw gRPC Robot"
DEFAULT_ROBOT_TYPE = RobotType.TEST.name
