"""robo_claw_grpc 설정 관리"""

import os
import pathlib

import yaml

from robo_claw_grpc.constants import (
    DEFAULT_CONFIG_FILE_NAME,
    DEFAULT_CONFIG_PATH,
    DEFAULT_FILE_TRANSFER_DIR,
    DEFAULT_ROBOT_DESCRIPTION,
    DEFAULT_ROBOT_ID,
    DEFAULT_ROBOT_NAME,
    DEFAULT_ROBOT_TYPE,
    DEFAULT_SERVER_PORT,
)
from robo_claw_grpc.models import ConfigModel
from robo_claw_grpc.utils import LogHelper


class RoboClawGrpcConfig:
    """robo_claw_grpc 설정 관리 클래스"""

    def __init__(self, path: str | None = None):
        self._logger = LogHelper().get_logger(__name__)
        self._config: ConfigModel

        self.__config_path = os.path.join(
            pathlib.Path.home(),
            ".config",
            path if path is not None else DEFAULT_CONFIG_PATH,
        )
        self.__config_file_path = os.path.join(self.__config_path, DEFAULT_CONFIG_FILE_NAME)
        self._logger.debug(f"Config file path: {self.__config_file_path}")

        if not os.path.exists(self.__config_path):
            os.makedirs(self.__config_path)
            self.__create_default_config()
        else:
            self._config = self.__load_config()

        if hasattr(self._config, "file_transfer_dir") and not os.path.exists(self._config.file_transfer_dir):
            os.makedirs(self._config.file_transfer_dir, exist_ok=True)

    def __create_default_config(self) -> ConfigModel:
        try:
            self._config = ConfigModel(
                port=DEFAULT_SERVER_PORT,
                robot_id=DEFAULT_ROBOT_ID,
                robot_name=DEFAULT_ROBOT_NAME,
                robot_description=DEFAULT_ROBOT_DESCRIPTION,
                robot_type=DEFAULT_ROBOT_TYPE,
                debug=False,
                max_history_count=10000,
                max_db_size_mb=1000,
                file_transfer_dir=os.path.join(self.__config_path, DEFAULT_FILE_TRANSFER_DIR),
            )
            with open(self.__config_file_path, "w") as f:
                yaml.dump(self._config.__dict__, f)
            self._logger.info(f"Default config file created: {self.__config_file_path}")
            return self._config
        except Exception as e:
            raise e

    def __load_config(self) -> ConfigModel:
        try:
            with open(self.__config_file_path) as f:
                config_yaml = yaml.load(f, Loader=yaml.FullLoader)

            if not config_yaml:
                return self.__create_default_config()

            if "file_transfer_dir" not in config_yaml:
                config_yaml["file_transfer_dir"] = os.path.join(
                    self.__config_path, DEFAULT_FILE_TRANSFER_DIR
                )

            return ConfigModel(**config_yaml)
        except FileNotFoundError:
            return self.__create_default_config()
        except yaml.YAMLError as e:
            self._logger.error(f"YAML parsing error: {e}")
            raise e
        except TypeError as e:
            self._logger.warning(f"Config type error, regenerating with defaults: {e}")
            return self.__create_default_config()
        except Exception as e:
            self._logger.error(f"Failed to load config: {e}")
            raise e

    def get_config(self) -> ConfigModel:
        return self._config
