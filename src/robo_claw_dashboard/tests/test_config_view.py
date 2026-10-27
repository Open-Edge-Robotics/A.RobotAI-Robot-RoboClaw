"""실행 설정 패널 도메인 테스트 — 파라미터 마스킹/정규화 (ROS2 의존 없음)."""

import pytest
from robo_claw_dashboard.domain.config_view import (
    MASK,
    MAX_LIST_ITEMS,
    MAX_VALUE_CHARS,
    build_config_panel,
    flatten_param_docs,
    is_secret_name,
    mask_parameters,
    normalize_value,
    parse_contract_defaults,
    source_of,
    values_match,
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


class TestFlattenParamDocs:
    def test_nested_node_and_wildcard_docs_are_flattened(self):
        docs = [
            {"robo_claw_agent_node": {"ros__parameters": {"llm_model": "gemma4:31b"}}},
            {"/**": {"ros__parameters": {"use_rag": True}}},
        ]
        assert flatten_param_docs(docs) == {"llm_model": "gemma4:31b", "use_rag": True}

    def test_later_docs_override_earlier_values(self):
        docs = [
            {"a": {"ros__parameters": {"camera_topic": "/oakd/image"}}},
            {"b": {"ros__parameters": {"camera_topic": "/former0047/camera"}}},
        ]
        assert flatten_param_docs(docs)["camera_topic"] == "/former0047/camera"

    def test_missing_ros_parameters_is_ignored(self):
        assert flatten_param_docs([{"a": {}}, {}, None]) == {}


class TestValuesMatch:
    def test_int_and_float_are_compared_numerically(self):
        assert values_match(9090, 9090.0) is True
        assert values_match(0.30000000000000004, 0.3) is True

    def test_bool_is_not_equal_to_number(self):
        assert values_match(True, 1) is False
        assert values_match(False, 0) is False
        assert values_match(True, True) is True

    def test_string_and_number_do_not_match(self):
        assert values_match("9090", 9090) is False

    def test_lists_are_compared_element_wise(self):
        assert values_match([1, 2], [1, 2]) is True
        assert values_match([1, 2], [1, 3]) is False


class TestSourceOf:
    def test_equal_value_is_default(self):
        assert source_of("dashboard_port", 9090, {"dashboard_port": 9090}) == "default"

    def test_different_value_is_explicit(self):
        assert source_of("dashboard_port", 9190, {"dashboard_port": 9090}) == "explicit"

    def test_missing_default_is_unknown(self):
        assert source_of("dashboard_port", 9090, {}) == "unknown"
        assert source_of("unknown_param", 1, {"dashboard_port": 9090}) == "unknown"


class TestPanelSources:
    def test_rows_carry_source_and_counts(self):
        panel = build_config_panel(
            agent_node="agent",
            agent_parameters={"llm_model": "gemma4:31b", "use_rag": True},
            agent_defaults={"llm_model": "gpt-4.1", "use_rag": True},
            dashboard_node="dash",
            dashboard_parameters={"dashboard_port": 9090},
            dashboard_defaults={"dashboard_port": 9090},
        )
        rows = {row["name"]: row for row in panel["agent"]["parameters"]}
        assert rows["llm_model"]["source"] == "explicit"
        assert rows["use_rag"]["source"] == "default"
        assert panel["dashboard"]["parameters"][0]["source"] == "default"
        assert panel["default_counts"] == {"agent": 1, "dashboard": 1}

    def test_secret_rows_keep_source(self):
        panel = build_config_panel(
            agent_node="agent",
            agent_parameters={"http_control_token": "abc"},
            agent_defaults={"http_control_token": ""},
            dashboard_node="dash",
            dashboard_parameters={},
        )
        row = panel["agent"]["parameters"][0]
        assert row["secret"] is True
        assert row["source"] == "explicit"


class TestParseContractDefaults:
    def test_contract_fields_map_to_ros_parameter_defaults(self):
        contract = {
            "fields": [
                {
                    "canonical": "dashboard.port",
                    "ros_parameter": "dashboard_port",
                    "default": 9090,
                },
                {"canonical": "rag.enabled", "ros_parameter": "enable_rag", "default": False},
                {"canonical": "host.only", "ros_parameter": "", "default": "/tmp"},
                {"canonical": "no.default", "ros_parameter": "x_param"},
            ]
        }
        assert parse_contract_defaults(contract) == {
            "dashboard_port": 9090,
            "enable_rag": False,
            "x_param": None,
        }

    def test_canonical_keyed_fields_are_supported(self):
        contract = {
            "fields": {
                "dashboard.port": {"ros_parameter": "dashboard_port", "default": 9090},
                "rag.enabled": {"ros_parameter": "enable_rag", "default": False},
            }
        }
        assert parse_contract_defaults(contract) == {
            "dashboard_port": 9090,
            "enable_rag": False,
        }

    def test_broken_contract_returns_empty_mapping(self):
        assert parse_contract_defaults(None) == {}
        assert parse_contract_defaults({"fields": "nope"}) == {}
