"""지도 목표 계약 테스트 — 순수 검증/정규화/좌표 변환 규칙."""

import base64
import math

import pytest
from robo_claw_dashboard.domain.localization import build_map_snapshot, compute_geometry_id
from robo_claw_dashboard.domain.map_goal import (
    adopt_external_record,
    can_submit,
    canonical_coordinate,
    canonical_message,
    cell_value,
    goal_signature,
    is_verified_not_executed,
    merge_records,
    normalize_record,
    parse_map_goal_request,
    validate_map_goal_against_map,
    world_to_cell,
)

pytestmark = pytest.mark.unit

WIDTH, HEIGHT, RES = 4, 3, 0.5
CELLS = [0] * (WIDTH * HEIGHT)
CELLS[5] = 100  # (col=1, row=1) occupied
CELLS[2] = -1  # (col=2, row=0) unknown


def make_map(*, yaw=math.pi / 2.0, origin=(1.0, -2.0), topic="/map", cells=None):
    return build_map_snapshot(
        width=WIDTH,
        height=HEIGHT,
        resolution=RES,
        origin={"x": origin[0], "y": origin[1], "yaw": yaw},
        frame_id="map",
        cells=list(CELLS if cells is None else cells),
        stamp_ns=1,
        topic=topic,
    )


MAP = make_map()
AGENT = {"agent_id": "former0047", "navigation_frame": "map"}


def body(x=0.75, y=-1.75, *, request_id="req-1_A", overrides=None, **top):
    target = {
        "x": x,
        "y": y,
        "frame_id": "map",
        "robot_id": "former0047",
        "geometry_id": MAP["geometry_id"],
    }
    target.update(overrides or {})
    payload = {
        "message": canonical_message(target["robot_id"], target["frame_id"], x, y),
        "request_id": request_id,
        "map_goal": target,
    }
    payload.update(top)
    return payload


class TestGeometryId:
    def test_snapshot_carries_64_lowercase_hex(self):
        assert len(MAP["geometry_id"]) == 64
        assert MAP["geometry_id"] == MAP["geometry_id"].lower()
        int(MAP["geometry_id"], 16)

    def test_cells_and_stamp_do_not_change_id(self):
        changed = make_map(cells=[0] * (WIDTH * HEIGHT))
        changed["stamp_ns"] = 999
        assert compute_geometry_id(changed) == MAP["geometry_id"]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"topic": "/other_map"},
            {"yaw": 0.0},
            {"origin": (1.5, -2.0)},
        ],
    )
    def test_geometry_change_changes_id(self, kwargs):
        assert compute_geometry_id(make_map(**kwargs)) != MAP["geometry_id"]

    def test_yaw_wrap_is_normalized_before_hashing(self):
        assert compute_geometry_id(make_map(yaw=math.pi)) == compute_geometry_id(
            make_map(yaw=-math.pi)
        )

    def test_sub_quantum_float_noise_is_ignored(self):
        noisy = make_map()
        noisy["resolution"] = MAP["resolution"] + 1e-13
        noisy["origin"] = dict(MAP["origin"], x=MAP["origin"]["x"] + 1e-13)
        assert compute_geometry_id(noisy) == MAP["geometry_id"]

    def test_missing_meta_is_unavailable(self):
        assert compute_geometry_id({"available": True}) is None
        assert compute_geometry_id({"available": False}) is None


class TestCanonicalContract:
    def test_message_is_exact(self):
        assert canonical_message("former0047", "map", 1.23456789, -2.0) == (
            "지도에서 선택한 목표로 이동해 주세요. 로봇: former0047. 기준 프레임: map. "
            "목표 좌표: x=1.234568 m, y=-2.000000 m. "
            "필요한 주행 안전 절차를 먼저 적용하고, 목표 좌표와 프레임을 변경하지 마세요."
        )

    def test_message_has_no_skill_marker(self):
        message = canonical_message("former0047", "map", 0.0, 0.0)
        assert "[skill]" not in message
        assert "navigate_to" not in message

    def test_negative_zero_is_normalized_in_message_and_number(self):
        assert canonical_coordinate(-1e-9) == 0.0
        assert str(canonical_coordinate(-0.0)) == "0.0"
        assert "x=0.000000 m, y=0.000000 m" in canonical_message("r", "map", -1e-9, -0.0)

    def test_six_decimal_rounding(self):
        assert canonical_coordinate(1.23456749) == 1.234567
        assert canonical_coordinate(1.2345675) == 1.234568


class TestParseRequest:
    def test_valid_body_normalizes_coordinates(self):
        raw = body(x=1.23456789, y=-2.0000001)
        ok, err, goal = parse_map_goal_request(raw)
        assert ok, err
        assert goal["x"] == 1.234568
        assert goal["y"] == -2.0
        assert goal["request_id"] == "req-1_A"
        assert goal["geometry_id"] == MAP["geometry_id"]

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda p: p["map_goal"].update({"z": 1.0}),
            lambda p: p.update({"context_json": "{}"}),
            lambda p: p.update({"map_goal": None}),
            lambda p: p.update({"map_goal": "x"}),
            lambda p: p["map_goal"].update({"x": True}),
            lambda p: p["map_goal"].update({"x": float("nan")}),
            lambda p: p["map_goal"].update({"y": float("inf")}),
            lambda p: p["map_goal"].update({"x": 1_000_001.0}),
            lambda p: p["map_goal"].update({"frame_id": "map\nodom"}),
            lambda p: p["map_goal"].update({"robot_id": "former 0047"}),
            lambda p: p["map_goal"].update({"geometry_id": "ABC"}),
            lambda p: p["map_goal"].pop("geometry_id"),
            lambda p: p.update({"request_id": "bad id"}),
            lambda p: p.update({"request_id": "x" * 65}),
            lambda p: p.update({"message": "다른 메시지"}),
        ],
    )
    def test_invalid_bodies_are_rejected(self, mutate):
        raw = body()
        mutate(raw)
        ok, err, goal = parse_map_goal_request(raw)
        assert ok is False
        assert err
        assert goal is None

    def test_non_object_body_rejected(self):
        ok, err, goal = parse_map_goal_request("text")
        assert ok is False and err and goal is None


class TestWorldToCell:
    def test_rotated_inverse_transform_maps_cell_centers(self):
        # yaw=90도, origin=(1,-2), res=0.5
        cases = {
            (0, 0): (0.75, -1.75),
            (1, 1): (0.25, -1.25),
            (2, 0): (0.75, -0.75),
            (3, 2): (-0.25, -0.25),
        }
        for cell, (wx, wy) in cases.items():
            assert world_to_cell(MAP, wx, wy) == cell

    def test_half_open_bounds_reject_edges(self):
        flat = make_map(yaw=0.0, origin=(0.0, 0.0))
        assert world_to_cell(flat, 0.0, 0.0) == (0, 0)
        assert world_to_cell(flat, WIDTH * RES, 0.0) is None
        assert world_to_cell(flat, 0.0, HEIGHT * RES) is None
        assert world_to_cell(flat, -0.001, 0.0) is None

    def test_unavailable_map_has_no_cell(self):
        assert world_to_cell({"available": False}, 0.0, 0.0) is None

    def test_cell_value_decodes_free_occupied_unknown(self):
        assert cell_value(MAP, 0, 0) == 0
        assert cell_value(MAP, 1, 1) == 100
        assert cell_value(MAP, 2, 0) == 255

    def test_cell_value_out_of_range(self):
        assert cell_value(MAP, -1, 0) is None
        assert cell_value(MAP, WIDTH, 0) is None

    def test_cell_value_rejects_broken_payload(self):
        broken = make_map()
        broken["data"] = base64.b64encode(b"\x00").decode("ascii")
        assert cell_value(broken, 0, 0) is None


class TestValidateAgainstMap:
    def _goal(self, **overrides):
        ok, err, goal = parse_map_goal_request(body(**overrides))
        assert ok, err
        return goal

    def test_free_cell_is_accepted(self):
        ok, err, kind = validate_map_goal_against_map(self._goal(), MAP, AGENT)
        assert ok, (err, kind)

    @pytest.mark.parametrize(
        "goal_kwargs",
        [
            {"overrides": {"geometry_id": "0" * 64}},
            {"overrides": {"frame_id": "odom"}},
            {"x": 100.0, "y": 100.0},
            {"x": 0.25, "y": -1.25},  # occupied
            {"x": 0.75, "y": -0.75},  # unknown
        ],
    )
    def test_bad_geometry_frame_or_cell_is_invalid(self, goal_kwargs):
        ok, err, kind = validate_map_goal_against_map(self._goal(**goal_kwargs), MAP, AGENT)
        assert ok is False
        assert kind == "invalid"
        assert err

    def test_robot_mismatch_is_invalid(self):
        ok, _, kind = validate_map_goal_against_map(
            self._goal(overrides={"robot_id": "other007"}), MAP, AGENT
        )
        assert ok is False and kind == "invalid"

    def test_agent_navigation_frame_mismatch_is_invalid(self):
        ok, _, kind = validate_map_goal_against_map(
            self._goal(), MAP, {"agent_id": "former0047", "navigation_frame": "odom"}
        )
        assert ok is False and kind == "invalid"

    def test_missing_agent_identity_is_unavailable(self):
        for agent in (None, {}, {"agent_id": ""}):
            ok, _, kind = validate_map_goal_against_map(self._goal(), MAP, agent)
            assert ok is False and kind == "unavailable"

    def test_missing_or_broken_map_is_unavailable(self):
        for snapshot in ({"available": False}, {"available": True, "width": 4}):
            ok, _, kind = validate_map_goal_against_map(self._goal(), snapshot, AGENT)
            assert ok is False and kind == "unavailable"


class TestRecords:
    def test_goal_signature_is_stable_for_equivalent_goals(self):
        _, _, first = parse_map_goal_request(body())
        _, _, second = parse_map_goal_request(body())
        _, _, renamed = parse_map_goal_request(body(request_id="other"))
        _, _, moved = parse_map_goal_request(body(x=0.25, y=-1.75))
        assert goal_signature(first) == goal_signature(second)
        assert goal_signature(first) == goal_signature(renamed)
        assert goal_signature(first) != goal_signature(moved)

    def test_normalize_record_defaults_and_rejects_missing_id(self):
        assert normalize_record({"status": "running"}) is None
        record = normalize_record(
            {
                "request_id": "req-1",
                "status": "RUNNING",
                "target": {"x": 0.5, "y": 1.5, "frame_id": "map"},
                "progress": 40,
            }
        )
        assert record["status"] == "running"
        assert record["progress"] == 40
        assert record["execution_started"] is False
        assert record["cancel_confirmed"] is False
        assert record["can_submit"] is False

    def test_normalize_record_clamps_and_filters_bad_values(self):
        record = normalize_record(
            {
                "request_id": "req-1",
                "status": "weird",
                "progress": 999,
                "target": {"x": float("nan"), "y": 0.0, "frame_id": "map"},
            }
        )
        assert record["status"] == "unknown"
        assert record["progress"] == 100
        assert record["target"] is None

    def test_can_submit_follows_lease(self):
        record = normalize_record({"request_id": "req-1", "status": "running"})
        assert can_submit(record, lease_held=True) is False
        assert can_submit(None, lease_held=False) is True
        assert can_submit(record, lease_held=False) is True


AGENT_FIXTURE = {
    "topic": "/map",
    "frame_id": "map",
    "width": 100,
    "height": 50,
    "resolution": 0.05,
    "origin": {"x": -2.5, "y": -1.25, "yaw": 0.0},
}
#: 실제 에이전트 fixture 파라미터로 계산되는 고정 지문(양 레인 공통 계약).
AGENT_FIXTURE_GEOMETRY_ID = "7566e0b34bb04228c0dc8850c6657f6dd941e70eb9f94189856ed903fa5bb8e2"


class TestAgentFixtureGolden:
    def test_agent_fixture_geometry_id_is_golden(self):
        snapshot = build_map_snapshot(
            width=AGENT_FIXTURE["width"],
            height=AGENT_FIXTURE["height"],
            resolution=AGENT_FIXTURE["resolution"],
            origin=AGENT_FIXTURE["origin"],
            frame_id=AGENT_FIXTURE["frame_id"],
            cells=[0] * (AGENT_FIXTURE["width"] * AGENT_FIXTURE["height"]),
            stamp_ns=1,
            topic=AGENT_FIXTURE["topic"],
        )
        assert snapshot["geometry_id"] == AGENT_FIXTURE_GEOMETRY_ID
        assert compute_geometry_id(dict(snapshot, stamp_ns=999)) == AGENT_FIXTURE_GEOMETRY_ID


class TestExternalLeaseAdoption:
    def _record(self, status="running", *, updated_at=100.0, **extra):
        record = normalize_record(
            {
                "request_id": "req-ext",
                "robot_id": "former0047",
                "status": status,
                "target": {"x": 0.75, "y": -1.75, "frame_id": "map"},
                "geometry_id": MAP["geometry_id"],
                "created_at": 10.0,
                "updated_at": updated_at,
                **extra,
            }
        )
        assert record is not None
        return record

    def test_stale_external_lease_becomes_unknown_without_movement_promise(self):
        adopted = adopt_external_record(self._record("running", updated_at=100.0), now=1000.0)
        assert adopted["status"] == "unknown"
        assert adopted["reason"] == "stale_agent_record"
        assert adopted["execution_started"] is False

    def test_fresh_external_lease_keeps_authoritative_status(self):
        adopted = adopt_external_record(self._record("running", updated_at=995.0), now=1000.0)
        assert adopted["status"] == "running"
        assert adopted["reason"] == ""

    def test_terminal_external_record_is_not_downgraded(self):
        adopted = adopt_external_record(
            self._record("reached", updated_at=100.0, execution_started=True), now=1000.0
        )
        assert adopted["status"] == "reached"

    def test_verified_not_executed_requires_failed_status_and_flag(self):
        assert is_verified_not_executed(
            self._record("failed", reason="navigation_not_executed", execution_started=False)
        )
        assert not is_verified_not_executed(
            self._record("failed", reason="navigation_not_executed", execution_started=True)
        )
        assert not is_verified_not_executed(self._record("unknown", reason="other"))
        assert not is_verified_not_executed(None)


class TestMergeAuthorityGuards:
    def _record(self, status, *, updated_at, **extra):
        return normalize_record(
            {
                "request_id": "req-1",
                "status": status,
                "target": {"x": 0.75, "y": -1.75, "frame_id": "map"},
                "geometry_id": "a" * 64,
                "created_at": 10.0,
                "updated_at": updated_at,
                **extra,
            }
        )

    def test_unknown_local_accepts_newer_agent_terminal(self):
        local = self._record("unknown", updated_at=10.0)
        agent = self._record("reached", updated_at=11.0)
        assert merge_records(local, agent)["status"] == "reached"

    def test_unknown_local_ignores_stale_agent_terminal(self):
        local = self._record("unknown", updated_at=20.0)
        agent = self._record("reached", updated_at=5.0)
        assert merge_records(local, agent)["status"] == "unknown"

    def test_agent_planning_never_claims_execution_started(self):
        local = self._record("queued", updated_at=10.0)
        agent = self._record("planning", updated_at=11.0, execution_started=True)
        merged = merge_records(local, agent)
        assert merged["status"] == "planning"
        assert merged["execution_started"] is False

    def test_agent_running_claims_execution_started(self):
        local = self._record("planning", updated_at=10.0)
        agent = self._record("running", updated_at=11.0, execution_started=True)
        merged = merge_records(local, agent)
        assert merged["status"] == "running"
        assert merged["execution_started"] is True

    @pytest.mark.parametrize(
        "overrides",
        [
            {"target": {"x": 0.25, "y": -1.75, "frame_id": "map"}},
            {"target": {"x": 0.75, "y": -1.75, "frame_id": "odom"}},
            {"geometry_id": "b" * 64},
        ],
    )
    def test_mismatched_agent_record_cannot_transition_local(self, overrides):
        local = self._record("unknown", updated_at=10.0)
        agent = dict(self._record("reached", updated_at=11.0), **overrides)
        merged = merge_records(local, agent)
        assert merged["status"] == "unknown"
        assert merged["target"] == local["target"]
