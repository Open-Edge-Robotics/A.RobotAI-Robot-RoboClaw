/* 시나리오/스냅샷 표시 모듈 실행 하네스 — production 코드 아님.
 *
 * 사용: node scenario_view.js <render_utils.js> <scenario_view.js> <snapshot_view.js>
 * 각 순수 함수의 결과를 JSON 으로 출력해 pytest 가 단언한다.
 */
"use strict";

const path = require("path");

const renderUtilsPath = process.argv[2];
const scenarioViewPath = process.argv[3];
const snapshotViewPath = process.argv[4];

// scenario_view.js 는 브라우저에서 render_utils 를 전역으로 먼저 로드한다.
globalThis.RcRenderUtils = require(path.resolve(renderUtilsPath));
const view = require(path.resolve(scenarioViewPath));
const snapshot = require(path.resolve(snapshotViewPath));

const HOSTILE = {
  id: 'tc_1"><img src=x onerror=alert(1)>',
  name: "<script>alert('x')</script>",
  step: 'S1"><b>',
  type: 'ping" onmouseover="alert(1)',
};

const output = {
  bannerHostileState: view.statusBannerHtml({ state: '<img src=x onerror=alert(1)>' }),
  rowHostile: view.scenarioRowHtml(HOSTILE, 0, true, true),
  rowEnabledFalse: view.scenarioRowHtml({ id: "a", name: "A", step: "S1", type: "ping" }, 0, false, false),
  rowDisabled: view.scenarioRowHtml(
    { id: "b", name: "B", step: "S2", type: "ping", enabled: false },
    0,
    true,
    true
  ),
  rowNavigationGated: view.scenarioRowHtml(
    { id: "c", name: "C", step: "S3", type: "navigation" },
    1,
    false,
    true
  ),
  rowNavigationAllowed: view.scenarioRowHtml(
    { id: "d", name: "D", step: "S4", type: "navigation" },
    2,
    true,
    false
  ),
  rowManipulationGated: view.scenarioRowHtml(
    { id: "e", name: "E", step: "S5", type: "manipulation" },
    3,
    true,
    false
  ),
  rowNoStep: view.scenarioRowHtml({ id: "f", name: "F", type: "ping" }, 4, true, true),
  rowNoNameType: view.scenarioRowHtml({ id: "g" }, 5, true, true),
  emptyRow: view.emptyScenarioRowHtml(),
  bannerRunning: view.statusBannerHtml({
    state: "running",
    summary: { total: 5, pass: 1, fail: 2, warning: 1, skip: 1 },
  }),
  bannerCompletedNoSummary: view.statusBannerHtml({ state: "COMPLETED", total_steps: 3 }),
  bannerCancelledWarnOnly: view.statusBannerHtml({
    status: "CANCELLED",
    summary: { pass: 0, fail: 0, warn: 2, skip: 1 },
  }),
  bannerIdleZero: view.statusBannerHtml({}),
  bannerUnknownState: view.statusBannerHtml({ state: "paused" }),
  badge: {
    running: view.runStateBadge("RUNNING"),
    unknown: view.runStateBadge("MIGRATING"),
  },
  counts: view.summaryCounts({ total_steps: 4, summary: { pass: 2, fail: 1, warning: 1, skip: 0 } }),
  countsNoSummary: view.summaryCounts({ total_steps: 7 }),
  progress: {
    explicit: view.progressPercent({ progress_percent: 42.6 }),
    clampedHigh: view.progressPercent({ progress_percent: 180 }),
    clampedLow: view.progressPercent({ progress_percent: -5 }),
    computed: view.progressPercent({
      total_steps: 4,
      summary: { pass: 1, fail: 1, warning: 0, skip: 0 },
    }),
    computedCurrentStep: view.progressPercent({ total_steps: 4, current_step: 1 }),
    empty: view.progressPercent({}),
  },
  stepStatus: {
    pass: view.stepStatusHtml("pass"),
    warn: view.stepStatusHtml("WARN"),
    running: view.stepStatusHtml("RUNNING"),
    skip: view.stepStatusHtml("SKIP"),
    unknown: view.stepStatusHtml("<img src=x>"),
    missing: view.stepStatusHtml(undefined),
  },
  duration: { value: view.durationText(1533), zero: view.durationText(0), missing: view.durationText(null) },
  detail: { value: view.detailText("상세"), empty: view.detailText(""), missing: view.detailText(null) },
  source: {
    remote: view.sourceLabel("remote"),
    cache: view.sourceLabel("cache"),
    file: view.sourceLabel("file"),
    fallbackBuiltin: view.sourceLabel("기본 내장"),
  },
  title: {
    explicit: view.scenarioTitle({ title: "T", name: "N" }),
    name: view.scenarioTitle({ name: "N" }),
    missing: view.scenarioTitle({}),
  },
  description: { value: view.scenarioDescription({ description: "D" }), missing: view.scenarioDescription({}) },
  config: {
    parsed: view.parseScenarioConfig(
      JSON.stringify({ url: " http://10.0.0.5:30180 ", robot: "former", environment: "kon47", token: "t" })
    ),
    broken: view.parseScenarioConfig("{not json"),
    missing: view.parseScenarioConfig(""),
    extraKeys: view.parseScenarioConfig(JSON.stringify({ url: "u", extra: "x" })),
  },
  complete: {
    full: view.isScenarioConfigComplete({ url: "u", robot: "r", environment: "e" }),
    noToken: view.isScenarioConfigComplete({ url: "u", robot: "r", environment: "e" }),
    noEnv: view.isScenarioConfigComplete({ url: "u", robot: "r" }),
  },
  syncBody: view.scenarioSyncBody({
    url: "http://10.0.0.5:30180",
    robot: "former",
    environment: "kon47",
    token: "dev",
  }),
  snapshot: {
    stateText: {
      idle: snapshot.stateBadgeText("IDLE"),
      unknown: snapshot.stateBadgeText(undefined),
    },
    stateClass: {
      error: snapshot.stateBadgeClassName("ERROR"),
      unknown: snapshot.stateBadgeClassName("BOOTING"),
    },
    battery: {
      ratio: snapshot.batteryText(0.82),
      percent: snapshot.batteryText(82),
      text: snapshot.batteryText("85.0%"),
      unknownText: snapshot.batteryText("UNKNOWN"),
      missing: snapshot.batteryText(null),
      fillHigh: snapshot.batteryFillPercent(150),
      fillLow: snapshot.batteryFillPercent(-4),
      fillMissing: snapshot.batteryFillPercent(null),
    },
    voltage: { number: snapshot.batteryVoltageText(24.5), text: snapshot.batteryVoltageText("24V") },
    charging: {
      charging: snapshot.chargingText(true),
      discharging: snapshot.chargingText(false),
      missing: snapshot.chargingText(null),
    },
    sensors: snapshot.sensorChips({ camera: true, map: false }),
    peers: snapshot.peerChips([
      { name: "cloid01", host: "10.0.0.7", port: 9090, connected: true },
      { name: "cloid02", connected: false },
    ]),
    emptySensors: snapshot.sensorChips(undefined),
  },
  renderUtils: {
    escape: globalThis.RcRenderUtils.escapeHtml("<a href=\"x\">&'</a>"),
    escapeNull: globalThis.RcRenderUtils.escapeHtml(null),
    configArray: globalThis.RcRenderUtils.formatConfigValue(["a", 1, true]),
    configBool: globalThis.RcRenderUtils.formatConfigValue(false),
    configNull: globalThis.RcRenderUtils.formatConfigValue(null),
    configNumber: globalThis.RcRenderUtils.formatConfigValue(3.5),
  },
};

console.log(JSON.stringify(output));
