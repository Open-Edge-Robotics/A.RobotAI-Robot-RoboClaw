/* 지도 좌표 변환 — 월드(m) ↔ 캔버스(px). DOM/ROS 의존 없는 순수 함수 모음. */
(function (global) {
  "use strict";

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

  /** 월드 좌표(m)를 캔버스 좌표(px)로 변환한다. 지도는 Y축이 위로 증가한다. */
  function worldToCanvas(view, map, x, y) {
    const worldH = map.height * map.resolution;
    return {
      x: view.offsetX + (x - map.origin.x) * view.scale,
      y: view.offsetY + (map.origin.y + worldH - y) * view.scale,
    };
  }

  /** 캔버스 좌표(px)를 월드 좌표(m)로 변환한다. */
  function canvasToWorld(view, map, px, py) {
    const worldH = map.height * map.resolution;
    return {
      x: map.origin.x + (px - view.offsetX) / view.scale,
      y: map.origin.y + worldH - (py - view.offsetY) / view.scale,
    };
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

  const api = { layout, worldToCanvas, canvasToWorld, imageRect, mapRowToCanvasY };
  global.RcMapGeometry = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
