"""로깅 헬퍼"""

import logging
from typing import Literal


class LogHelper:
    def __init__(
        self,
        level: Literal["INFO", "WARNING", "ERROR", "CRITICAL", "DEBUG", "EXCEPTION"] = "INFO",
    ) -> None:
        self._level = level

    def get_logger(self, name: str | None = None) -> logging.Logger:
        logger = logging.getLogger(name if name else __name__)

        if logger.hasHandlers():
            return logger

        logger.setLevel(level=self._level)

        handler = logging.StreamHandler()
        handler.setLevel(level=self._level)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        return logger
