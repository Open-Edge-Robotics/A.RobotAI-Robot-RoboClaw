/* 상태 스냅샷 표시 규칙 — 배지/배터리/chip 문구를 만드는 순수 함수 모음.
 *
 * 서버 응답 필드 해석(숫자 정규화, 누락 표기)만 담당하고 DOM 조작은 app.js 가 한다.
 */
(function (global) {
  "use strict";

  /** 에이전트 상태 코드 → 배지 클래스. 모르는 상태는 회색 배지. */
  const STATE_BADGE_VARIANTS = {
    IDLE: "text-bg-success",
    THINKING: "text-bg-primary",
    EXECUTING: "text-bg-warning",
    WAITING: "text-bg-secondary",
    ERROR: "text-bg-danger",
  };

  /** 에이전트 상태 코드 → 배지 문구(미수신은 "–"). */
  function stateBadgeText(state) {
    const name = String(state || "UNKNOWN");
    return name === "UNKNOWN" ? "–" : name;
  }

  function stateBadgeClassName(state) {
    const name = String(state || "UNKNOWN");
    return "badge rounded-pill " + (STATE_BADGE_VARIANTS[name] || "text-bg-secondary");
  }

  /** 배터리 잔량을 퍼센트로 정규화한다(0~1 비율 표기 허용). 숫자가 아니면 NaN. */
  function batteryPercent(percentage) {
    if (typeof percentage === "number") return percentage * (percentage <= 1 ? 100 : 1);
    return parseFloat(percentage);
  }

  /** 배터리 게이지 폭(0~100). 값이 없으면 0. */
  function batteryFillPercent(percentage) {
    const percent = batteryPercent(percentage);
    return Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : 0;
  }

  /** 배터리 잔량 문구("82%" 또는 원본 표기, 없으면 "–"). */
  function batteryText(percentage) {
    const percent = batteryPercent(percentage);
    if (Number.isFinite(percent)) return percent.toFixed(0) + "%";
    return percentage == null ? "–" : String(percentage);
  }

  /** 배터리 전압 문구(숫자는 소수 2자리 + V). */
  function batteryVoltageText(voltage) {
    return typeof voltage === "number" ? voltage.toFixed(2) + " V" : voltage;
  }

  /** 충전 상태 문구. */
  function chargingText(isCharging) {
    return isCharging == null ? "–" : isCharging ? "충전 중" : "방전";
  }

  /** 센서 상태 맵 → chip 목록(이름/정상 여부/문구). */
  function sensorChips(sensorHealth) {
    return Object.entries(sensorHealth || {}).map(([name, ok]) => ({
      label: name,
      ok: Boolean(ok),
      text: ok ? "정상" : "미수신",
    }));
  }

  /** 피어 목록 → chip 목록(호스트가 있으면 이름 뒤에 host:port 를 붙인다). */
  function peerChips(peers) {
    return (peers || []).map((peer) => ({
      label: peer.host ? peer.name + " (" + peer.host + ":" + peer.port + ")" : peer.name,
      ok: Boolean(peer.connected),
      text: peer.connected ? "연결됨" : "끊김",
    }));
  }

  const api = {
    stateBadgeText,
    stateBadgeClassName,
    batteryPercent,
    batteryFillPercent,
    batteryText,
    batteryVoltageText,
    chargingText,
    sensorChips,
    peerChips,
  };
  global.RcSnapshotView = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
