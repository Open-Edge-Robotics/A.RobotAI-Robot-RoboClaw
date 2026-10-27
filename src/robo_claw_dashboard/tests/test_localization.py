"""지도/위치 도메인 테스트 — ROS2 의존 없는 순수 규칙."""

import base64
import math

import pytest
from robo_claw_dashboard.domain.localization import (
    UNKNOWN_CELL,
    build_initial_pose,
    build_map_snapshot,
    build_pose_snapshot,
    encode_cells_base64,
    normalize_yaw,
    parse_map_topics,
    validate_initial_pose,
)

pytestmark = pytest.mark.unit

MAP_META = {
    "width": 100,
    "height": 80,
    "resolution": 0.05,
    "origin": {"x": -1.0, "y": -2.0, "yaw": 0.0},
}


class TestEncodeCells:
    def test_unknown_cells_map_to_unknown_value(self):
        assert encode_cells_base64([0, 100, -1, 50]) == base64.b64encode(
            bytes([0, 100, UNKNOWN_CELL, 50])
        ).decode("ascii")

    def test_out_of_range_values_are_clamped(self):
        data = base64.b64decode(encode_cells_base64([-5, 150, 40]))
        assert list(data) == [UNKNOWN_CELL, 100, 40]


class TestBuildMapSnapshot:
    def test_snapshot_carries_meta_and_encoded_cells(self):
        cells = [0] * (MAP_META["width"] * MAP_META["height"])
        snap = build_map_snapshot(frame_id="map", cells=cells, stamp_ns=1234, **MAP_META)

        assert snap["available"] is True
        assert snap["frame_id"] == "map"
        assert snap["stamp_ns"] == 1234
        assert snap["width"] == 100
        assert snap["height"] == 80
        assert snap["resolution"] == 0.05
        assert snap["origin"] == {"x": -1.0, "y": -2.0, "yaw": 0.0}
        assert len(base64.b64decode(snap["data"])) == 100 * 80

    def test_cell_count_mismatch_is_rejected(self):
        with pytest.raises(ValueError):
            build_map_snapshot(frame_id="map", cells=[0, 1, 2], **MAP_META)

    def test_non_positive_resolution_is_rejected(self):
        with pytest.raises(ValueError):
            build_map_snapshot(
                frame_id="map",
                cells=[0] * (MAP_META["width"] * MAP_META["height"]),
                width=100,
                height=80,
                resolution=0.0,
                origin={"x": 0.0, "y": 0.0, "yaw": 0.0},
            )


class TestPoseSnapshot:
    def test_valid_pose_is_normalized(self):
        pose = build_pose_snapshot(x=1.23456, y=-2.0, yaw=math.pi, stamp_ns=10)
        assert pose["available"] is True
        assert pose["frame_id"] == "map"
        assert pose["base_frame"] == "base_link"
        assert pose["x"] == pytest.approx(1.235, abs=1e-3)
        assert pose["yaw"] == pytest.approx(-math.pi)

    def test_non_finite_pose_is_rejected(self):
        assert build_pose_snapshot(x=float("nan"), y=0.0, yaw=0.0) is None
        assert build_pose_snapshot(x=0.0, y=float("inf"), yaw=0.0) is None

    def test_normalize_yaw_wraps_into_range(self):
        assert normalize_yaw(3 * math.pi) == pytest.approx(-math.pi)
        # 범위 규약은 [-pi, pi) 이다.
        assert normalize_yaw(-3 * math.pi) == pytest.approx(-math.pi)


class TestValidateInitialPose:
    def test_valid_payload_returns_normalized_pose(self):
        ok, error, pose = validate_initial_pose({"x": 1.0, "y": 2.0, "yaw": 1.5}, map_meta=MAP_META)
        assert ok is True
        assert error is None
        assert pose == {"x": 1.0, "y": 2.0, "yaw": 1.5, "frame_id": "map"}

    def test_missing_yaw_defaults_to_zero(self):
        ok, _, pose = validate_initial_pose({"x": 1.0, "y": 2.0}, map_meta=MAP_META)
        assert ok is True
        assert pose["yaw"] == 0.0

    def test_payload_must_be_mapping(self):
        ok, error, pose = validate_initial_pose([1, 2], map_meta=MAP_META)
        assert ok is False
        assert "JSON" in error
        assert pose is None

    def test_missing_coordinate_is_rejected(self):
        ok, error, _ = validate_initial_pose({"y": 1.0}, map_meta=MAP_META)
        assert ok is False
        assert "x" in error

    def test_bool_is_not_a_coordinate(self):
        ok, error, _ = validate_initial_pose({"x": True, "y": 1.0}, map_meta=MAP_META)
        assert ok is False
        assert "숫자" in error

    def test_non_finite_coordinate_is_rejected(self):
        ok, error, _ = validate_initial_pose({"x": "nan", "y": 0.0}, map_meta=MAP_META)
        assert ok is False
        assert "유한" in error or "숫자" in error

    def test_coordinate_far_outside_sanity_range_is_rejected(self):
        ok, error, _ = validate_initial_pose({"x": 5000.0, "y": 0.0}, map_meta=MAP_META)
        assert ok is False
        assert "범위" in error

    def test_position_outside_map_extent_is_rejected(self):
        # 지도 범위: x -1.0 ~ 4.0, y -2.0 ~ 2.0 (여유 1 m)
        ok, error, _ = validate_initial_pose({"x": 10.0, "y": 0.0}, map_meta=MAP_META)
        assert ok is False
        assert "지도" in error

    def test_position_in_map_margin_is_accepted(self):
        ok, _, _ = validate_initial_pose({"x": 4.5, "y": 2.5}, map_meta=MAP_META)
        assert ok is True

    def test_without_map_meta_only_sanity_range_applies(self):
        ok, error, pose = validate_initial_pose({"x": 10.0, "y": 0.0}, map_meta=None)
        assert ok is True
        assert pose["x"] == 10.0
        assert error is None


class TestBuildInitialPose:
    def test_covariance_is_applied_to_x_y_yaw(self):
        msg = build_initial_pose(x=1.0, y=2.0, yaw=math.pi / 2, covariance=0.05)
        covariance = msg["covariance"]
        assert len(covariance) == 36
        assert covariance[0] == pytest.approx(0.05)
        assert covariance[7] == pytest.approx(0.05)
        assert covariance[35] == pytest.approx(0.05)
        assert msg["frame_id"] == "map"
        assert msg["orientation_z"] == pytest.approx(math.sin(math.pi / 4))
        assert msg["orientation_w"] == pytest.approx(math.cos(math.pi / 4))

    def test_yaw_is_normalized_before_encoding(self):
        msg = build_initial_pose(x=0.0, y=0.0, yaw=3 * math.pi, covariance=0.25)
        assert msg["yaw"] == pytest.approx(-math.pi)


class TestParseMapTopics:
    def test_comma_separated_list_is_split_and_trimmed(self):
        assert parse_map_topics("/map, /lio/grid_map ") == ["/map", "/lio/grid_map"]

    def test_empty_entries_are_dropped(self):
        assert parse_map_topics("/lio/grid_map,,") == ["/lio/grid_map"]

    def test_single_topic_stays_single(self):
        assert parse_map_topics("/map") == ["/map"]

    def test_blank_value_falls_back_to_default(self):
        # 기본값은 로봇 계열별 지도 토픽 목록이다(former `/map`, CLOiD `/lio/grid_map`).
        assert parse_map_topics("") == ["/map", "/lio/grid_map"]
        assert parse_map_topics(None) == ["/map", "/lio/grid_map"]

    def test_duplicates_are_removed_preserving_order(self):
        assert parse_map_topics("/map,/map,/lio/grid_map") == ["/map", "/lio/grid_map"]


class TestMapSnapshotTopic:
    def test_snapshot_records_source_topic(self):
        cells = [0] * (MAP_META["width"] * MAP_META["height"])
        snap = build_map_snapshot(
            frame_id="map", cells=cells, stamp_ns=1, topic="/lio/grid_map", **MAP_META
        )
        assert snap["topic"] == "/lio/grid_map"
