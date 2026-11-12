/* 지도 좌표 변환 — 월드(m) ↔ 캔버스(px). DOM/ROS 의존 없는 순수 함수 모음.
 *
 * 지도 이미지는 grid-local 축(origin 에서 회전 없는 셀 사각형)으로 그린다.
 * 월드 좌표는 origin.yaw 만큼 회전해 grid-local 로 옮긴 뒤 캔버스로 보낸다.
 * 따라서 화면 표시 각도/마커는 origin.yaw 를 뺀 grid-local 각도를 쓴다.
 */
(function (global) {
  "use strict";

  function originYaw(map) {
    const yaw = map && map.origin ? map.origin.yaw : 0;
    return typeof yaw === "number" && Number.isFinite(yaw) ? yaw : 0;
  }

  function originOf(map) {
    const origin = (map && map.origin) || {};
    return { x: origin.x || 0, y: origin.y || 0 };
  }

  /** 월드 좌표를 origin 회전을 되돌린 grid-local 좌표로 변환한다. */
  function worldToLocal(map, x, y) {
    const origin = originOf(map);
    const yaw = originYaw(map);
    const cos = Math.cos(yaw);
    const sin = Math.sin(yaw);
    const dx = x - origin.x;
    const dy = y - origin.y;
    return { x: cos * dx + sin * dy, y: -sin * dx + cos * dy };
  }

  /** grid-local 좌표를 origin 회전을 적용한 월드 좌표로 변환한다. */
  function localToWorld(map, localX, localY) {
    const origin = originOf(map);
    const yaw = originYaw(map);
    const cos = Math.cos(yaw);
    const sin = Math.sin(yaw);
    return {
      x: origin.x + cos * localX - sin * localY,
      y: origin.y + sin * localX + cos * localY,
    };
  }

  /** grid-local 좌표를 캔버스 좌표로 변환한다. 지도는 Y축이 위로 증가한다. */
  function localToCanvas(view, map, localX, localY) {
    const worldH = map.height * map.resolution;
    return {
      x: view.offsetX + localX * view.scale,
      y: view.offsetY + (worldH - localY) * view.scale,
    };
  }

  /** 캔버스 좌표를 grid-local 좌표로 변환한다. */
  function canvasToLocal(view, map, px, py) {
    const worldH = map.height * map.resolution;
    return {
      x: (px - view.offsetX) / view.scale,
      y: worldH - (py - view.offsetY) / view.scale,
    };
  }

  /**
   * 지도와 캔버스 크기로부터 그리기 영역(px/m 스케일과 여백)을 계산한다.
   * @returns {{worldW: number, worldH: number, scale: number, offsetX: number, offsetY: number}}
   */
  function layout(canvasWidth, canvasHeight, map) {
    const worldW = map.width * map.resolution;
    const worldH = map.height * map.resolution;
    const scale = Math.min(canvasWidth / worldW, canvasHeight / worldH) * 0.98;
    return {
      worldW,
      worldH,
      scale,
      offsetX: (canvasWidth - worldW * scale) / 2,
      offsetY: (canvasHeight - worldH * scale) / 2,
    };
  }

  /** 월드 좌표(m)를 캔버스 좌표(px)로 변환한다. */
  function worldToCanvas(view, map, x, y) {
    const local = worldToLocal(map, x, y);
    return localToCanvas(view, map, local.x, local.y);
  }

  /** 캔버스 좌표(px)를 월드 좌표(m)로 변환한다. */
  function canvasToWorld(view, map, px, py) {
    const local = canvasToLocal(view, map, px, py);
    return localToWorld(map, local.x, local.y);
  }

  /** 화면에 그릴 방향각 — 지도 이미지가 grid-local 축이므로 origin.yaw 를 뺀다. */
  function headingToCanvas(map, yaw) {
    const value = typeof yaw === "number" && Number.isFinite(yaw) ? yaw : 0;
    return value - originYaw(map);
  }

  /**
   * 월드 좌표가 속한 grid 셀. 지도 경계는 반개 구간 [0, size*resolution) 이다.
   * @returns {{col: number, row: number}|null}
   */
  function worldToGrid(map, x, y) {
    if (!map || !Number.isFinite(x) || !Number.isFinite(y)) return null;
    const local = worldToLocal(map, x, y);
    const width = map.width * map.resolution;
    const height = map.height * map.resolution;
    if (!(local.x >= 0 && local.x < width && local.y >= 0 && local.y < height)) {
      return null;
    }
    return { col: Math.floor(local.x / map.resolution), row: Math.floor(local.y / map.resolution) };
  }

  /** grid 셀 값(OccupancyGrid 0=free, 100=occupied, 255=unknown). 범위 밖은 null. */
  function cellAt(map, col, row) {
    if (!map || !map.cells) return null;
    if (!Number.isInteger(col) || !Number.isInteger(row)) return null;
    if (col < 0 || row < 0 || col >= map.width || row >= map.height) return null;
    const value = map.cells[row * map.width + col];
    return typeof value === "number" ? value : null;
  }

  /**
   * 지도 이미지를 그릴 캔버스 사각형(px). 지도 데이터 row 0 은 월드 아래쪽이므로
   * 이미지를 그릴 때 세로로 뒤집어야 캔버스 위쪽이 월드 +Y 가 된다.
   */
  function imageRect(view, map) {
    return {
      x: view.offsetX,
      y: view.offsetY,
      width: map.width * map.resolution * view.scale,
      height: map.height * map.resolution * view.scale,
    };
  }

  /** 지도 데이터 row(0 = 월드 아래쪽)의 중심이 그려지는 캔버스 y(px). */
  function mapRowToCanvasY(view, map, row) {
    return view.offsetY + (map.height - 1 - row + 0.5) * map.resolution * view.scale;
  }

  const api = {
    originYaw,
    worldToLocal,
    localToWorld,
    localToCanvas,
    canvasToLocal,
    layout,
    worldToCanvas,
    canvasToWorld,
    headingToCanvas,
    worldToGrid,
    cellAt,
    imageRect,
    mapRowToCanvasY,
  };
  global.RcMapGeometry = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
