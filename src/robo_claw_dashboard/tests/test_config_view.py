"""실행 설정 패널 도메인 테스트 — 파라미터 마스킹/정규화 (ROS2 의존 없음)."""

import pytest
from robo_claw_dashboard.domain.config_view import (
    MASK,
    MAX_LIST_ITEMS,
    MAX_VALUE_CHARS,
    build_config_panel,
    is_secret_name,
    mask_parameters,
    normalize_value,
)

pytestmark = pytest.mark.unit


class TestSecretDetection:
    @pytest.mark.parametrize(
        "name",
        [
            "http_control_token",
            "http_readonly_token",
            "grpc_peer_token",
            "qdrant_api_key",
            "llm_embedding_api_key",
            "azure_api_key",
            "admin_password",
            "aws_access_key",
            "client_secret",
        ],
    )
    def test_secret_names_are_detected(self, name):
        assert is_secret_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "llm_model",
            "use_rag",
            "http_port",
            "map_topic",
            "monkey_count",
            "keyboard_topic",
            "skill_learning_success_sample_rate",
        ],
    )
    def test_non_secret_names_are_not_flagged(self, name):
        assert is_secret_name(name) is False


class TestNormalizeValue:
    def test_scalars_pass_through(self):
        assert normalize_value(True) == (True, False)
        assert normalize_value(8080) == (8080, False)
        assert normalize_value(0.5) == (0.5, False)
        assert normalize_value("map") == ("map", False)

    def test_long_string_is_truncated(self):
        raw = "x" * (MAX_VALUE_CHARS + 40)
        value, truncated = normalize_value(raw)
        assert truncated is True
        assert len(value) == MAX_VALUE_CHARS + 1  # 말줄임표 포함
        assert value.endswith("…")

    def test_long_list_is_capped(self):
        raw = list(range(MAX_LIST_ITEMS + 10))
        value, truncated = normalize_value(raw)
        assert truncated is True
        assert len(value) == MAX_LIST_ITEMS

    def test_nested_values_are_stringified(self):
        value, _ = normalize_value({"a": 1})
        assert isinstance(value, str)


class TestMaskParameters:
    def test_secret_value_is_masked(self):
        rows = mask_parameters({"http_control_token": "s3cret", "llm_model": "llama3"})
        by_name = {row["name"]: row for row in rows}
        assert by_name["http_control_token"]["value"] == MASK
        assert by_name["http_control_token"]["secret"] is True
        assert by_name["llm_model"]["value"] == "llama3"
        assert by_name["llm_model"]["secret"] is False

    def test_empty_secret_stays_empty(self):
        rows = mask_parameters({"grpc_peer_token": ""})
        assert rows[0]["value"] == ""
        assert rows[0]["secret"] is True

    def test_rows_are_sorted_by_name(self):
        rows = mask_parameters({"z_param": 1, "a_param": 2, "m_param": 3})
        assert [row["name"] for row in rows] == ["a_param", "m_param", "z_param"]


class TestBuildConfigPanel:
    def test_panel_combines_agent_and_dashboard_sections(self):
        panel = build_config_panel(
            agent_node="robo_claw_agent_node",
            agent_parameters={"llm_model": "gemma4:31b", "use_rag": True},
            dashboard_node="robo_claw_dashboard_node",
            dashboard_parameters={"dashboard_port": 9090, "http_control_token": "abc"},
        )
        assert panel["available"] is True
        assert panel["agent"]["node"] == "robo_claw_agent_node"
        assert panel["agent"]["available"] is True
        assert panel["agent"]["parameters"][0]["name"] == "llm_model"
        assert panel["dashboard"]["parameters"][1]["value"] == MASK

    def test_agent_unavailable_is_reported_without_failure(self):
        panel = build_config_panel(
            agent_node="robo_claw_agent_node",
            agent_parameters=None,
            dashboard_node="robo_claw_dashboard_node",
            dashboard_parameters={"dashboard_port": 9090},
        )
        assert panel["available"] is True
        assert panel["agent"]["available"] is False
        assert panel["agent"]["parameters"] == []

    def test_both_unavailable_reports_not_available(self):
        panel = build_config_panel(
            agent_node="robo_claw_agent_node",
            agent_parameters=None,
            dashboard_node="robo_claw_dashboard_node",
            dashboard_parameters=None,
        )
        assert panel["available"] is False

    def test_truncated_values_are_flagged(self):
        panel = build_config_panel(
            agent_node="agent",
            agent_parameters={"long_value": "y" * (MAX_VALUE_CHARS + 5)},
            dashboard_node="dash",
            dashboard_parameters={},
        )
        row = panel["agent"]["parameters"][0]
        assert row["truncated"] is True
