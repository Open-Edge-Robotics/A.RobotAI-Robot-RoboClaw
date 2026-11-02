"""pytest 전역 설정.

ROS 2 `launch_testing` pytest 플러그인은 `launch.logging.LaunchLogger` 를 전역
로거 클래스로 지정합니다. `LaunchLogger` 는 `propagate=False` 로 생성되므로
pytest 의 `caplog` 핸들러(루트 로거)까지 로그 레코드가 전달되지 않습니다.
그 결과 `caplog` 로 경고를 검증하는 테스트가 코드와 무관하게 실패합니다.

테스트 수집 전에 표준 `logging.Logger` 로 되돌려 `caplog` 동작을 복구합니다.
이미 만들어진 로거는 영향을 받지 않고, 이후 생성되는 로거에만 적용됩니다.
"""

from __future__ import annotations

import logging

import pytest


def _restore_standard_logger_class() -> None:
    if logging.getLoggerClass() is not logging.Logger:
        logging.setLoggerClass(logging.Logger)


def pytest_configure(config: pytest.Config) -> None:
    del config
    _restore_standard_logger_class()
