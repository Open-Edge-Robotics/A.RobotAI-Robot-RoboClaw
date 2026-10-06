"""DashboardSnapshot 취합 로직 단위 테스트 — ROS2 의존 없는 순수 도메인."""

import pytest
from robo_claw_dashboard.domain.builder import build_snapshot
from robo_claw_dashboard.domain.models import AGENT_STATE_NAMES, normalize_state_code

pytestmark = pytest.mark.unit


class TestNormalizeStateCode:
    def test_known_codes_map_to_names(self):
        assert normalize_state_code(0) == "IDLE"
        assert normalize_state_code(1) == "THINKING"
        assert normalize_state_code(2) == "EXECUTING"
        assert normalize_state_code(3) == "WAITING"
        assert normalize_state_code(4) == "ERROR"

    def test_unknown_code_maps_to_unknown(self):
        assert normalize_state_code(99) == "UNKNOWN"

    def test_none_maps_to_unknown(self):
        assert normalize_state_code(None) == "UNKNOWN"

    def test_agent_state_names_keys_are_exhaustive_codes(self):
        assert set(AGENT_STATE_NAMES) == {0, 1, 2, 3, 4}


class TestBuildSnapshot:
    def test_state_json_is_parsed_into_state_section(self):
        state_json = '{"agent_id":"r1","skills":["get_status"]}'
        snap = build_snapshot(state_json=state_json)
        assert snap["state"]["agent_id"] == "r1"
        assert snap["state"]["skills"] == ["get_status"]

    def test_state_json_non_string_defaults_to_empty(self):
        snap = build_snapshot(state_json=None)
        assert snap["state"] == {}

    def test_state_json_invalid_string_does_not_raise(self):
        snap = build_snapshot(state_json="{not valid json")
        assert snap["state"] == {}

    def test_agent_status_state_code_is_normalized(self):
        snap = build_snapshot(
            agent_status={"state": 2, "current_skill": "nav", "message": "이동 중"}
        )
        assert snap["agent"]["state"] == "EXECUTING"
        assert snap["agent"]["current_skill"] == "nav"

    def test_agent_status_defaults_when_missing(self):
        snap = build_snapshot()
        assert snap["agent"]["state"] == "UNKNOWN"
        assert snap["agent"]["current_skill"] == ""

    def test_telemetry_passthrough(self):
        telemetry = {"battery": {"percentage": "87.0%"}, "pose": {"x": 1.0}}
        snap = build_snapshot(telemetry=telemetry)
        assert snap["telemetry"]["battery"]["percentage"] == "87.0%"

    def test_sensor_health_and_peers_defaults(self):
        snap = build_snapshot(sensor_health=None, peers=None)
        assert snap["sensor_health"] == {}
        assert snap["peers"] == []

    def test_peers_are_lists(self):
        peers = [{"name": "peer-1", "connected": True}]
        snap = build_snapshot(peers=peers)
        assert snap["peers"][0]["name"] == "peer-1"

    def test_fixed_timestamp_is_used(self):
        from datetime import datetime, timezone

        now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        snap = build_snapshot(now=now)
        assert snap["timestamp"] == "2025-01-02T03:04:05+00:00"


class TestBuildSnapshotStateEnumParity:
    def test_all_state_names_exist_in_normalizer(self):
        # AGENT_STATE_NAMES 값들이 전부 normalize_state_code 로 역산 가능해야 한다
        for value in AGENT_STATE_NAMES.values():
            for code, name in AGENT_STATE_NAMES.items():
                if name == value:
                    assert normalize_state_code(code) == value
