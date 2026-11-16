"""시나리오 실행기 모듈 분리 경계 테스트.

``scenario_runner`` 는 실행 세션(상태·스레드·보고서)만 소유하고 설정 검증/원격 조회/step
핸들러는 각 모듈이 소유한다. 기존 import 경로가 그대로 동작해야 하므로 재노출 정체성을
고정한다.
"""

from __future__ import annotations

import pytest
from robo_claw_dashboard.application import (
    scenario_config,
    scenario_remote,
    scenario_runner,
    scenario_steps,
)

pytestmark = pytest.mark.unit


def test_runner_reexports_config_names_from_scenario_config():
    assert scenario_runner.ScenarioConfig is scenario_config.ScenarioConfig
    assert scenario_runner.parse_user_server_config is scenario_config.parse_user_server_config
    assert scenario_runner.normalize_host_entry is scenario_config.normalize_host_entry
    assert (
        scenario_runner.resolve_cli_config_defaults is scenario_config.resolve_cli_config_defaults
    )
    assert scenario_runner.USER_CONFIG_KEYS is scenario_config.USER_CONFIG_KEYS


def test_public_names_are_importable_from_legacy_path():
    """기존 코드/테스트가 쓰던 import 문이 계속 동작한다."""
    from robo_claw_dashboard.application.scenario_runner import (
        USER_CONFIG_KEYS,
        ScenarioConfig,
        ScenarioRunnerService,
        normalize_host_entry,
        parse_user_server_config,
        resolve_cli_config_defaults,
    )

    assert USER_CONFIG_KEYS == (
        "config_server_url",
        "robot_name",
        "environment",
        "device_token",
    )
    assert ScenarioConfig().cache_dir == ""
    assert callable(parse_user_server_config)
    assert callable(normalize_host_entry)
    assert callable(resolve_cli_config_defaults)
    assert isinstance(ScenarioRunnerService, type)


def test_responsibilities_live_in_their_own_modules():
    assert scenario_runner.ScenarioRunnerService.__module__ == scenario_runner.__name__
    assert scenario_steps.ScenarioStepExecutor.__module__ == scenario_steps.__name__
    assert scenario_remote.ScenarioLoad.__module__ == scenario_remote.__name__
    assert scenario_remote.load_scenario.__module__ == scenario_remote.__name__
    assert scenario_config.missing_config_warning.__module__ == scenario_config.__name__


def test_step_executor_is_composed_not_copied():
    """실행기는 step 핸들러를 직접 갖지 않고 시나리오 step 실행기에 위임한다."""
    assert not hasattr(scenario_runner.ScenarioRunnerService, "_step_ping")
    assert hasattr(scenario_steps.ScenarioStepExecutor, "_step_ping")
