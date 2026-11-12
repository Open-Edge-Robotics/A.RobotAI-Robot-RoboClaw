"""지도 좌표 변환 JS 회귀 테스트 — map_geometry.js 를 node 로 실행해 검증한다.

로봇 위치가 캔버스 밖으로 나가던 버그(해상도를 두 번 곱함)를 다시 만들지 않도록
월드↔캔버스 변환의 스케일과 왕복 정합성을 확인한다. node 가 없으면 건너뛴다.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.component

ASSETS = Path(__file__).resolve().parents[1] / "web" / "assets"
GEOMETRY = ASSETS / "map_geometry.js"

NODE_SCRIPT = """
const geometry = require(process.argv[1]);
const map = {
  width: 324,
  height: 307,
  resolution: 0.05,
  origin: { x: -5.95, y: -4.95 },
};
const view = geometry.layout(720, 460, map);
const pose = { x: 4.184, y: 1.582 };
const point = geometry.worldToCanvas(view, map, pose.x, pose.y);
const roundTrip = geometry.canvasToWorld(view, map, point.x, point.y);
const minCorner = geometry.worldToCanvas(view, map, map.origin.x, map.origin.y);
const rect = geometry.imageRect(view, map);
const topRow = geometry.mapRowToCanvasY(view, map, map.height - 1);
const bottomRow = geometry.mapRowToCanvasY(view, map, 0);
const maxCorner = geometry.worldToCanvas(
  view,
  map,
  map.origin.x + map.width * map.resolution,
  map.origin.y + map.height * map.resolution
);
console.log(
  JSON.stringify({ view, point, roundTrip, minCorner, maxCorner, rect, topRow, bottomRow })
);
"""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 실행 파일이 없어 JS 좌표 변환 테스트를 건너뜁니다")
    return node


def _run() -> dict:
    result = subprocess.run(
        [_node(), "-e", NODE_SCRIPT, "--", str(GEOMETRY)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_pose_maps_inside_canvas():
    data = _run()
    point = data["point"]
    assert 0 <= point["x"] <= 720, f"로봇 위치 x 가 캔버스 밖: {point}"
    assert 0 <= point["y"] <= 460, f"로봇 위치 y 가 캔버스 밖: {point}"


def test_map_bounds_fit_inside_canvas():
    data = _run()
    assert 0 <= data["minCorner"]["x"] <= 720
    assert 0 <= data["maxCorner"]["x"] <= 720
    # 지도는 Y축이 위로 증가하므로 원점(좌하단)의 캔버스 y 가 더 크다.
    assert data["minCorner"]["y"] > data["maxCorner"]["y"]


def test_world_to_canvas_round_trip():
    data = _run()
    round_trip = data["roundTrip"]
    assert round_trip["x"] == pytest.approx(4.184, abs=1e-6)
    assert round_trip["y"] == pytest.approx(1.582, abs=1e-6)


def test_map_image_is_drawn_with_y_flipped():
    """지도 row 0(월드 아래쪽)이 캔버스 아래쪽에 그려져야 마커와 방향이 맞는다."""
    data = _run()
    # row 0 의 캔버스 y 는 row 최대값의 캔버스 y 보다 커야 한다(아래쪽).
    assert data["bottomRow"] > data["topRow"]
    # row 중심 기준이므로 이미지 사각형 안쪽 반 칸(0.05 m)만큼 들어간 위치와 같아야 한다.
    half_cell = data["rect"]["height"] / 307 / 2
    assert data["topRow"] == pytest.approx(data["rect"]["y"] + half_cell, abs=1e-6)
    assert data["bottomRow"] == pytest.approx(
        data["rect"]["y"] + data["rect"]["height"] - half_cell, abs=1e-6
    )
    # 월드 원점(좌하단)은 이미지 사각형의 좌하단과 만난다.
    assert data["minCorner"]["x"] == pytest.approx(data["rect"]["x"], abs=1e-6)
    assert data["minCorner"]["y"] == pytest.approx(
        data["rect"]["y"] + data["rect"]["height"], abs=1e-6
    )


YAW_SCRIPT = """
const geometry = require(process.argv[1]);
const base = {
  width: 8,
  height: 6,
  resolution: 0.5,
  origin: { x: -5.95, y: -4.95, yaw: 0 },
  cells: new Array(8 * 6).fill(0),
};
base.cells[3 * base.width + 5] = 255;

function probe(yaw, localX, localY) {
  const map = Object.assign({}, base, { origin: Object.assign({}, base.origin, { yaw }) });
  const world = geometry.localToWorld(map, localX, localY);
  const view = geometry.layout(720, 460, map);
  const canvas = geometry.worldToCanvas(view, map, world.x, world.y);
  const roundTrip = geometry.canvasToWorld(view, map, canvas.x, canvas.y);
  const back = geometry.worldToLocal(map, world.x, world.y);
  const grid = geometry.worldToGrid(map, world.x, world.y);
  return {
    world,
    canvas,
    roundTrip,
    back,
    grid,
    heading: geometry.headingToCanvas(map, 1.0),
    originYaw: geometry.originYaw(map),
    imageRect: geometry.imageRect(view, map),
  };
}

const half = Object.assign({}, base, { origin: Object.assign({}, base.origin, { yaw: Math.PI / 2 }) });
const upper = geometry.worldToGrid(half, geometry.localToWorld(half, base.width * base.resolution, 1.1));
const negative = geometry.worldToGrid(half, geometry.localToWorld(half, -1e-9, 1.1));

console.log(JSON.stringify({
  yaw0: probe(0, 2.1, 1.1),
  yawHalf: probe(Math.PI / 2, 2.1, 1.1),
  yawNegHalf: probe(-Math.PI / 2, 2.1, 1.1),
  upper,
  negative,
  inside: geometry.worldToGrid(
    half,
    geometry.localToWorld(half, 0, 0).x,
    geometry.localToWorld(half, 0, 0).y
  ),
  cellFree: geometry.cellAt(base, 0, 0),
  cellUnknown: geometry.cellAt(base, 5, 3),
  cellOutOfRange: geometry.cellAt(base, -1, 0),
}));
"""


def _run_yaw() -> dict:
    result = subprocess.run(
        [_node(), "-e", YAW_SCRIPT, "--", str(GEOMETRY)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_world_canvas_round_trip_with_rotated_origin():
    """origin.yaw 가 ±π/2 여도 월드↔캔버스 왕복이 원래 월드 좌표로 돌아와야 한다."""
    data = _run_yaw()
    for key in ("yaw0", "yawHalf", "yawNegHalf"):
        probe = data[key]
        assert probe["roundTrip"]["x"] == pytest.approx(probe["world"]["x"], abs=1e-9)
        assert probe["roundTrip"]["y"] == pytest.approx(probe["world"]["y"], abs=1e-9)
        # 역회전이 정확히 local (2.1, 1.1) 로 돌아와야 한다.
        assert probe["back"]["x"] == pytest.approx(2.1, abs=1e-9)
        assert probe["back"]["y"] == pytest.approx(1.1, abs=1e-9)


def test_world_grid_uses_inverse_origin_rotation():
    """world→grid 는 origin.yaw 의 역회전을 적용해야 셀이 맞는다."""
    data = _run_yaw()
    assert data["yawHalf"]["grid"] == {"col": 4, "row": 2}
    assert data["yawNegHalf"]["grid"] == {"col": 4, "row": 2}


def test_world_grid_rejects_outside_and_half_open_upper_bound():
    data = _run_yaw()
    assert data["inside"] == {"col": 0, "row": 0}
    assert data["upper"] is None, "상단 경계(width*resolution)는 포함하면 안 된다"
    assert data["negative"] is None


def test_world_grid_cell_value_is_free_zero_only():
    data = _run_yaw()
    assert data["cellFree"] == 0
    assert data["cellUnknown"] == 255
    assert data["cellOutOfRange"] is None


def test_map_heading_subtracts_origin_yaw_for_display():
    """지도 이미지는 grid-local 축이므로 화면 방향은 origin.yaw 를 뺀 각도다."""
    data = _run_yaw()
    assert data["yaw0"]["heading"] == pytest.approx(1.0, abs=1e-12)
    assert data["yawHalf"]["heading"] == pytest.approx(1.0 - math.pi / 2, abs=1e-12)
    assert data["yawNegHalf"]["heading"] == pytest.approx(1.0 + math.pi / 2, abs=1e-12)
    assert data["yawHalf"]["originYaw"] == pytest.approx(math.pi / 2, abs=1e-12)


def test_map_image_rect_stays_grid_local_with_origin_yaw():
    """지도 이미지는 회전하지 않고 grid-local 축으로 그린다."""
    data = _run_yaw()
    assert data["yawHalf"]["imageRect"] == data["yaw0"]["imageRect"]
