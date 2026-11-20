"""지도 선택/위치추정 모드 전환 도메인 테스트 — ROS2 의존 없는 순수 규칙."""

import math

import pytest
from robo_claw_dashboard.domain.localization_mode import (
    MODE_LOCALIZATION,
    MODE_MAPPING,
    normalize_lio_status,
    normalize_map_listing,
    parse_initial_pose_mode,
    parse_map_names,
    resolve_map_path,
    unavailable_lio_status,
    validate_map_name,
    validate_switch_request,
)

pytestmark = pytest.mark.unit

MAP_ROOT = "/ws/maps"
MAP_NAMES = ["FACTORYMAP", "konjiam_L_107"]


class TestParseMapNames:
    def test_json_array_is_normalized_and_deduplicated(self):
        assert parse_map_names('["FACTORYMAP", "konjiam_L_107", "FACTORYMAP"]') == [
            "FACTORYMAP",
            "konjiam_L_107",
        ]

    def test_comma_separated_string_is_accepted(self):
        assert parse_map_names(" FACTORYMAP , konjiam_L_107 , ") == [
            "FACTORYMAP",
            "konjiam_L_107",
        ]

    def test_empty_and_invalid_input_yields_empty_list(self):
        assert parse_map_names(None) == []
        assert parse_map_names("") == []
        assert parse_map_names("{not json") == []
        assert parse_map_names(12) == []

    def test_invalid_entries_are_dropped(self):
        assert parse_map_names('["ok_map", "../etc", "", 5, "with space"]') == ["ok_map"]


class TestValidateMapName:
    @pytest.mark.parametrize("name", ["FACTORYMAP", "konjiam_L_107", "map.v2", "a1"])
    def test_safe_names_are_accepted(self, name):
        ok, error = validate_map_name(name)
        assert ok is True
        assert error is None

    @pytest.mark.parametrize(
        "name",
        ["", " ", ".", "..", "a/b", "a\\b", "../etc/passwd", "map name", "-leading", "x" * 65],
    )
    def test_unsafe_names_are_rejected(self, name):
        ok, error = validate_map_name(name)
        assert ok is False
        assert isinstance(error, str) and error


class TestResolveMapPath:
    def test_path_is_joined_under_root(self):
        assert resolve_map_path("konjiam_L_107", MAP_ROOT) == "/ws/maps/konjiam_L_107"

    def test_trailing_slash_in_root_is_normalized(self):
        assert resolve_map_path("FACTORYMAP", "/ws/maps/") == "/ws/maps/FACTORYMAP"

    def test_invalid_name_returns_none(self):
        assert resolve_map_path("../etc", MAP_ROOT) is None


class TestNormalizeMapListing:
    def test_items_keep_config_order_and_carry_path(self):
        items = normalize_map_listing(MAP_NAMES, MAP_ROOT)
        assert [item["name"] for item in items] == MAP_NAMES
        assert items[1]["path"] == "/ws/maps/konjiam_L_107"

    def test_invalid_names_are_dropped(self):
        assert normalize_map_listing(["ok", "../evil"], MAP_ROOT) == [
            {"name": "ok", "path": "/ws/maps/ok"}
        ]


class TestNormalizeLioStatus:
    def test_ready_is_terminal_localization_state(self):
        status = normalize_lio_status("ready", stamp_ns=123)
        assert status["available"] is True
        assert status["raw"] == "ready"
        assert status["phase"] == "ready"
        assert status["mode"] == MODE_LOCALIZATION
        assert status["active"] is False
        assert status["failed"] is False
        assert status["label"] == "위치추정 준비 완료"
        assert status["stamp_ns"] == 123

    @pytest.mark.parametrize(
        "raw", ["loading_map", "relocalizing_pose", "relocalizing_gkr", "loading_grid_map"]
    )
    def test_relocalization_states_are_active(self, raw):
        status = normalize_lio_status(raw)
        assert status["active"] is True
        assert status["mode"] == MODE_LOCALIZATION

    def test_mapping_states_report_mapping_mode(self):
        for raw in ("mapping", "saving_map"):
            status = normalize_lio_status(raw)
            assert status["mode"] == MODE_MAPPING

    def test_failed_state_is_reported_as_error(self):
        status = normalize_lio_status("failed")
        assert status["failed"] is True
        assert status["active"] is False

    def test_unknown_status_keeps_raw_text(self):
        status = normalize_lio_status("something_new")
        assert status["raw"] == "something_new"
        assert status["phase"] == "unknown"
        assert status["mode"] == "unknown"

    def test_empty_status_is_unavailable(self):
        for raw in ("", "   ", None):
            status = normalize_lio_status(raw)
            assert status["available"] is False
            assert status["phase"] == "unknown"

    def test_unavailable_helper_keeps_reason(self):
        status = unavailable_lio_status("상태 미수신")
        assert status["available"] is False
        assert status["reason"] == "상태 미수신"
        assert status["label"] == "상태 미수신"


class TestValidateSwitchRequest:
    def _payload(self, **overrides):
        payload = {"map": "konjiam_L_107", "confirm": True}
        payload.update(overrides)
        return payload

    def test_localization_switch_requires_confirmation(self):
        ok, error, plan = validate_switch_request(
            {"map": "konjiam_L_107"}, maps=MAP_NAMES, map_root=MAP_ROOT
        )
        assert ok is False
        assert plan is None
        assert "확인" in error

    def test_unknown_mode_is_rejected(self):
        ok, error, _ = validate_switch_request(
            self._payload(mode="teleport"), maps=MAP_NAMES, map_root=MAP_ROOT
        )
        assert ok is False
        assert "mode" in error

    def test_localization_requires_map_name(self):
        ok, error, _ = validate_switch_request({}, maps=MAP_NAMES, map_root=MAP_ROOT)
        assert ok is False
        assert "지도" in error
        plan_payload = self._payload(map="")
        ok, error, _ = validate_switch_request(plan_payload, maps=MAP_NAMES, map_root=MAP_ROOT)
        assert ok is False

    def test_map_outside_allowlist_is_rejected(self):
        ok, error, _ = validate_switch_request(
            self._payload(map="unknown_map"), maps=MAP_NAMES, map_root=MAP_ROOT
        )
        assert ok is False
        assert "unknown_map" in error

    def test_empty_allowlist_rejects_every_map(self):
        ok, error, _ = validate_switch_request(self._payload(), maps=[], map_root=MAP_ROOT)
        assert ok is False
        assert "목록" in error

    def test_path_like_map_value_is_rejected(self):
        ok, error, _ = validate_switch_request(
            self._payload(map="../etc/passwd"), maps=MAP_NAMES, map_root=MAP_ROOT
        )
        assert ok is False
        assert ok is False and error

    def test_localization_switch_without_seed_uses_gkr_path(self):
        ok, error, plan = validate_switch_request(
            self._payload(), maps=MAP_NAMES, map_root=MAP_ROOT
        )
        assert ok is True, error
        assert plan["mode"] == MODE_LOCALIZATION
        assert plan["map"] == "konjiam_L_107"
        assert plan["map_path"] == "/ws/maps/konjiam_L_107"
        assert plan["set_initial_pose"] is False
        assert plan["pose"] is None

    def test_localization_switch_with_seed_normalizes_pose(self):
        ok, error, plan = validate_switch_request(
            self._payload(set_initial_pose=True, x=1.25, y=-0.4, yaw=4.0),
            maps=MAP_NAMES,
            map_root=MAP_ROOT,
        )
        assert ok is True, error
        assert plan["set_initial_pose"] is True
        assert plan["pose"]["x"] == 1.25
        assert plan["pose"]["y"] == -0.4
        assert plan["pose"]["frame_id"] == "map"
        assert math.isclose(plan["pose"]["yaw"], 4.0 - 2.0 * math.pi, abs_tol=1e-9)

    def test_seed_requires_coordinates(self):
        ok, error, _ = validate_switch_request(
            self._payload(set_initial_pose=True, x=1.0), maps=MAP_NAMES, map_root=MAP_ROOT
        )
        assert ok is False
        assert "y" in error

    def test_seed_rejects_non_finite_coordinates(self):
        ok, error, _ = validate_switch_request(
            self._payload(set_initial_pose=True, x=float("nan"), y=0.0),
            maps=MAP_NAMES,
            map_root=MAP_ROOT,
        )
        assert ok is False

    def test_seed_rejects_coordinates_beyond_limit(self):
        ok, error, _ = validate_switch_request(
            self._payload(set_initial_pose=True, x=5000.0, y=0.0),
            maps=MAP_NAMES,
            map_root=MAP_ROOT,
        )
        assert ok is False

    def test_seed_ignores_current_map_bounds(self):
        # 맵을 바꾸는 중에는 현재 표시 중인 지도 경계로 시드를 막지 않는다.
        map_meta = {
            "available": True,
            "width": 4,
            "height": 4,
            "resolution": 0.05,
            "origin": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        }
        ok, error, plan = validate_switch_request(
            self._payload(set_initial_pose=True, x=40.0, y=40.0),
            maps=MAP_NAMES,
            map_root=MAP_ROOT,
            map_meta=map_meta,
        )
        assert ok is True, error
        assert plan["pose"]["x"] == 40.0

    def test_mapping_mode_drops_map_and_seed(self):
        ok, error, plan = validate_switch_request(
            self._payload(mode="mapping", set_initial_pose=True, x=1.0, y=2.0),
            maps=MAP_NAMES,
            map_root=MAP_ROOT,
        )
        assert ok is True, error
        assert plan["mode"] == MODE_MAPPING
        assert plan["map_path"] == ""
        assert plan["set_initial_pose"] is False
        assert plan["pose"] is None

    def test_non_mapping_payload_is_rejected(self):
        ok, error, _ = validate_switch_request(None, maps=MAP_NAMES, map_root=MAP_ROOT)
        assert ok is False
        assert "JSON" in error


class TestInitialPoseMode:
    """초기 위치 적용 경로는 토픽/서비스 존재가 아니라 설정으로 정한다."""

    def test_auto_resolves_from_robot_profile(self):
        assert parse_initial_pose_mode("auto", "cloid") == "lio"
        assert parse_initial_pose_mode("auto", "CLOiD") == "lio"
        assert parse_initial_pose_mode("auto", "former") == "amcl"
        assert parse_initial_pose_mode("auto", "stretch3") == "amcl"

    def test_auto_without_profile_falls_back_to_amcl(self):
        assert parse_initial_pose_mode("auto", "") == "amcl"
        assert parse_initial_pose_mode(None, None) == "amcl"
        assert parse_initial_pose_mode("", "  ") == "amcl"

    def test_explicit_mode_overrides_profile(self):
        assert parse_initial_pose_mode("amcl", "cloid") == "amcl"
        assert parse_initial_pose_mode("lio", "former") == "lio"
        assert parse_initial_pose_mode(" LIO ", "former") == "lio"

    def test_unknown_mode_is_not_guessed(self):
        # 오타나 미지원 값은 auto 로 해석한다. AMCL 로봇이면 그대로 amcl 이므로 안전하고,
        # CLOiD 프로필이면 lio_node 에 맞는 경로로 간다(오타로 기능이 깨지지 않는다).
        assert parse_initial_pose_mode("lio_fr", "former") == "amcl"
        assert parse_initial_pose_mode("topic", "cloid") == "lio"
        assert parse_initial_pose_mode("whatever", "") == "amcl"
