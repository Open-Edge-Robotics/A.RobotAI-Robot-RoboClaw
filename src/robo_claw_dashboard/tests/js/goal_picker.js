/* 목표 선택 모드 JS 검증 러너 — node 로 실행해 JSON 결과를 출력한다.
 *
 * 검증 범위:
 * - canonical 6자리 좌표/표준 메시지/map_goal 본문 계약
 * - 지도 프레임/에이전트/geometry/자유 셀 검증
 * - 모드 배타성, geometry 변경 무효화, 셀 갱신 재검증
 * - 클릭 선택은 POST 하지 않고, 명시적 전송 버튼만 /api/chat 으로 보낸다
 * - 202 접수 != 도착, 중복 전송 차단, 같은 요청 ID 재시도, 409/unknown lease 유지
 * - 키보드 이동/Escape/Enter, /api/status navigation_request 소비
 */
"use strict";

const fixtures = require(process.argv[3]);
const geometry = require(process.argv[4]);
globalThis.RcMapGeometry = geometry;
const goalPicker = require(process.argv[2]);

const checks = [];
const observed = {};

function assert(condition, message) {
  if (!condition) throw new Error(message || "assertion failed");
}

function assertEqual(actual, expected, message) {
  const a = JSON.stringify(actual);
  const b = JSON.stringify(expected);
  if (a !== b) throw new Error((message || "not equal") + ": " + a + " !== " + b);
}

function check(name, fn) {
  try {
    fn();
    checks.push({ name, ok: true });
  } catch (err) {
    checks.push({ name, ok: false, error: String((err && err.stack) || err) });
  }
}

async function asyncCheck(name, fn) {
  try {
    await fn();
    checks.push({ name, ok: true });
  } catch (err) {
    checks.push({ name, ok: false, error: String((err && err.stack) || err) });
  }
}

function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function mount(options) {
  const picked = fixtures.mountPicker(goalPicker, options);
  picked.clickWorld = (map, x, y) => {
    const view = geometry.layout(800, 400, map);
    const pixel = geometry.worldToCanvas(view, map, x, y);
    picked.canvas.dispatch("pointerdown", { clientX: pixel.x, clientY: pixel.y, pointerId: 1 });
  };
  picked.key = (name) => picked.canvas.dispatch("keydown", { key: name, preventDefault() {} });
  return picked;
}

function ready(picked, map, snapshot) {
  picked.controller.applyMap(map || fixtures.makeMap());
  picked.controller.applySnapshot(snapshot || fixtures.makeSnapshot());
}

async function main() {
  // ---- canonical 좌표/메시지 --------------------------------------------
  check("canonical: 6자리 반올림과 음수 0 정규화", () => {
    assertEqual(goalPicker.canonicalCoordinate(1.2345674), 1.234567);
    assertEqual(goalPicker.canonicalCoordinate(1.2345678), 1.234568);
    const zero = goalPicker.canonicalCoordinate(-0.0000001);
    assert(zero === 0 && !Object.is(zero, -0), "음수 0 이 정규화되지 않음: " + zero);
  });
  check("canonical: 유한하지 않거나 bool/범위 초과 좌표 거부", () => {
    for (const bad of [false, true, NaN, Infinity, -Infinity, null, "1.0", 1e6 + 1, -1e6 - 1]) {
      assertEqual(goalPicker.canonicalCoordinate(bad), null, "허용되면 안 되는 값 " + String(bad));
    }
  });
  check("canonical: 좌표는 항상 소수점 6자리로 표시", () => {
    assertEqual(goalPicker.formatCoordinate(2), "2.000000");
    assertEqual(goalPicker.formatCoordinate(-1.5), "-1.500000");
  });

  const canonicalMessage = goalPicker.formatCanonicalMessage({
    robotId: fixtures.ROBOT_ID,
    frameId: fixtures.FRAME_ID,
    x: 3.25,
    y: -1.5,
  });
  observed.canonicalMessage = canonicalMessage;
  check("message: 승인된 표준 문장과 정확히 일치", () => {
    assertEqual(
      canonicalMessage,
      "지도에서 선택한 목표로 이동해 주세요. 로봇: robot-a. 기준 프레임: map. " +
        "목표 좌표: x=3.250000 m, y=-1.500000 m. 필요한 주행 안전 절차를 먼저 적용하고, " +
        "목표 좌표와 프레임을 변경하지 마세요."
    );
    assert(canonicalMessage.indexOf("[") === -1, "skill marker 가 들어감");
  });

  const mapGoal = {
    x: 3.25,
    y: -1.5,
    frame_id: fixtures.FRAME_ID,
    robot_id: fixtures.ROBOT_ID,
    geometry_id: fixtures.GEOMETRY_ID,
  };
  const chat = goalPicker.buildChatBody({
    message: canonicalMessage,
    requestId: "mg_abc-123",
    mapGoal,
  });
  observed.chatBody = chat.ok ? chat.body : chat;
  check("body: message/request_id/map_goal 키 집합이 계약과 같다", () => {
    assertEqual(chat.ok, true, "본문 생성 실패: " + (chat.reason || ""));
    assertEqual(Object.keys(chat.body), ["message", "request_id", "map_goal"]);
    assertEqual(Object.keys(chat.body.map_goal), [
      "x",
      "y",
      "frame_id",
      "robot_id",
      "geometry_id",
    ]);
    assertEqual(chat.body.map_goal, mapGoal);
  });
  check("body: 잘못된 request_id/map_goal/좌표/메시지를 거부", () => {
    const base = { message: canonicalMessage, requestId: "mg_abc-123", mapGoal };
    const bad = [
      { ...base, requestId: "mg id" },
      { ...base, requestId: "x".repeat(65) },
      { ...base, requestId: "" },
      { ...base, mapGoal: { ...mapGoal, extra: 1 } },
      { ...base, mapGoal: { ...mapGoal, geometry_id: fixtures.GEOMETRY_ID.toUpperCase() } },
      { ...base, mapGoal: { ...mapGoal, geometry_id: "a".repeat(63) } },
      { ...base, mapGoal: { ...mapGoal, x: true } },
      { ...base, mapGoal: { ...mapGoal, y: NaN } },
      { ...base, mapGoal: { ...mapGoal, x: 1e6 + 1 } },
      { ...base, mapGoal: { ...mapGoal, robot_id: "robot\na" } },
      { ...base, mapGoal: { ...mapGoal, frame_id: "map\n" } },
      { ...base, message: canonicalMessage + " 추가 지시" },
      { ...base, message: "[navigation] " + canonicalMessage },
    ];
    for (const item of bad) {
      assertEqual(
        goalPicker.buildChatBody(item).ok,
        false,
        "허용되면 안 되는 본문 " + JSON.stringify(item)
      );
    }
  });

  // ---- 선택 검증 ---------------------------------------------------------
  function resolve(map, extra, point) {
    return goalPicker.resolveSelection({
      map: map || fixtures.makeMap(),
      agentId: fixtures.ROBOT_ID,
      navigationFrame: fixtures.FRAME_ID,
      point: point || { x: 5, y: 5 },
      ...(extra || {}),
    });
  }
  observed.selection = resolve();
  check("selection: 자유 셀 선택은 canonical 좌표/메시지/map_goal 을 만든다", () => {
    const selection = resolve(fixtures.makeMap(), {}, { x: 3.2500004, y: 5.0000004 });
    assertEqual(selection.ok, true, selection.reason || "");
    assertEqual(selection.x, 3.25);
    assertEqual(selection.y, 5);
    assertEqual(
      selection.message,
      goalPicker.formatCanonicalMessage({
        robotId: fixtures.ROBOT_ID,
        frameId: fixtures.FRAME_ID,
        x: selection.x,
        y: selection.y,
      })
    );
    assertEqual(selection.mapGoal.x, selection.x);
    assertEqual(selection.mapGoal.y, selection.y);
  });
  check("selection: 지도/로봇/프레임/geometry 누락을 각각 거부", () => {
    assertEqual(resolve(null, { map: null }).ok, false);
    assertEqual(resolve(undefined, { agentId: null }).ok, false);
    assertEqual(resolve(undefined, { agentId: "bad\nid" }).ok, false);
    assertEqual(resolve(undefined, { navigationFrame: null }).ok, false);
    assertEqual(resolve(undefined, { navigationFrame: "map\n" }).ok, false);
    assertEqual(resolve(undefined, { navigationFrame: "odom" }).ok, false);
    assertEqual(resolve(fixtures.makeMap({ geometryId: null })).ok, false);
    assertEqual(resolve(fixtures.makeMap({ geometryId: "A".repeat(64) })).ok, false);
    assertEqual(resolve(fixtures.makeMap({ frameId: "odom" })).ok, false);
  });
  check("selection: 지도 밖/자유 셀 아님(100,255)/bool/범위 초과를 거부", () => {
    assertEqual(resolve(fixtures.makeMap(), {}, { x: 11, y: 2 }).ok, false, "상단 경계");
    assertEqual(resolve(fixtures.makeMap(), {}, { x: 0.9, y: 2 }).ok, false, "좌측 밖");
    assertEqual(resolve(fixtures.makeMap(), {}, { x: true, y: 2 }).ok, false);
    assertEqual(resolve(fixtures.makeMap(), {}, { x: 1e6 + 1, y: 2 }).ok, false);
    const blocked = fixtures.makeMap();
    blocked.cells[6 * blocked.width + 8] = 100;
    assertEqual(resolve(blocked).ok, false);
    const unknown = fixtures.makeMap();
    unknown.cells[6 * unknown.width + 8] = 255;
    assertEqual(resolve(unknown).ok, false);
  });

  // ---- reducer -----------------------------------------------------------
  check("reducer: 모드 view/initial_pose/goal 은 배타적이고 떠나면 미전송 선택을 지운다", () => {
    let state = goalPicker.createState();
    assertEqual(state.mode, "view");
    state = goalPicker.reduce(state, { type: "select", point: { x: 5, y: 5 } });
    assertEqual(state.candidate, null, "context 없이 선택됨");
    state = goalPicker.reduce(state, {
      type: "context",
      context: {
        agentId: fixtures.ROBOT_ID,
        navigationFrame: fixtures.FRAME_ID,
        mapGeometryHint: fixtures.GEOMETRY_ID,
        map: fixtures.makeMap(),
      },
    });
    state = goalPicker.reduce(state, { type: "select", point: { x: 5, y: 5 } });
    assertEqual(state.mode, "goal");
    assert(state.candidate, "유효한 선택이 저장되지 않음");
    state = goalPicker.reduce(state, { type: "mode", mode: "initial_pose" });
    assertEqual(state.mode, "initial_pose");
    assertEqual(state.candidate, null);
    state = goalPicker.reduce(state, { type: "mode", mode: "bogus" });
    assertEqual(state.mode, "initial_pose");
  });
  check("reducer: geometry 변경은 미전송 선택을 지우고, 셀만 바뀌면 같은 좌표를 재검증한다", () => {
    const context = {
      agentId: fixtures.ROBOT_ID,
      navigationFrame: fixtures.FRAME_ID,
      map: fixtures.makeMap(),
    };
    let state = goalPicker.createState();
    state = goalPicker.reduce(state, { type: "context", context });
    state = goalPicker.reduce(state, { type: "select", point: { x: 5, y: 5 } });
    const changedGeometry = fixtures.makeMap({ geometryId: "b".repeat(64) });
    const invalidated = goalPicker.reduce(state, { type: "map", map: changedGeometry });
    assertEqual(invalidated.candidate, null, "geometry 변경 후 선택이 남음");
    const blocked = fixtures.makeMap({ stampNs: 11 });
    blocked.cells[6 * blocked.width + 8] = 100;
    const cleared = goalPicker.reduce(state, { type: "map", map: blocked });
    assertEqual(cleared.candidate, null, "막힌 셀로 바뀐 선택이 남음");
    const shifted = fixtures.makeMap({ stampNs: 12 });
    shifted.cells[0] = 100;
    const kept = goalPicker.reduce(state, { type: "map", map: shifted });
    assertEqual(kept.candidate && kept.candidate.x, 5);
    assertEqual(kept.candidate && kept.candidate.y, 5);
  });

  // ---- controller: 선택과 전송 ------------------------------------------
  await asyncCheck("controller: 목표 모드 클릭은 선택만 하고 POST 하지 않는다", async () => {
    const picked = mount();
    ready(picked);
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    await flush();
    const state = picked.controller.getState();
    assertEqual(state.mode, "goal");
    assertEqual(state.candidate && state.candidate.x, 5);
    assertEqual(picked.fetch.calls.length, 0, "클릭만으로 POST 됨");
    assertEqual(
      picked.nodes["goal-preview"].textContent,
      goalPicker.formatCanonicalMessage({
        robotId: fixtures.ROBOT_ID,
        frameId: fixtures.FRAME_ID,
        x: 5,
        y: 5,
      })
    );
    assertEqual(picked.nodes["goal-coords"].textContent, "5.000000, 5.000000 m");
    assertEqual(picked.nodes["goal-frame"].textContent, fixtures.FRAME_ID);
    assertEqual(picked.nodes["goal-robot"].textContent, fixtures.ROBOT_ID);
    assertEqual(picked.nodes["goal-send"].disabled, false);
    observed.preview = picked.nodes["goal-preview"].textContent;
  });

  await asyncCheck("controller: 전송은 정확한 본문을 /api/chat 으로 control 토큰과 보낸다", async () => {
    const picked = mount();
    ready(picked);
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    await picked.controller.submit();
    assertEqual(picked.fetch.calls.length, 1);
    const call = picked.fetch.calls[0];
    assertEqual(call.url, "/api/chat");
    assertEqual(call.method, "POST");
    assertEqual(call.token, "control-token");
    assertEqual(Object.keys(call.body), ["message", "request_id", "map_goal"]);
    assert(
      /^[A-Za-z0-9_-]{1,64}$/.test(call.body.request_id),
      "request_id 형식: " + call.body.request_id
    );
    assertEqual(call.body.message, observed.preview);
    observed.firstRequestBody = call.body;
    const state = picked.controller.getState();
    assertEqual(state.busy, false);
    assertEqual(state.requestId, call.body.request_id);
    assertEqual(state.record && state.record.status, "queued");
    assert(statusText(picked.nodes).indexOf("도착") === -1, "202 접수를 도착으로 표시함");
    assertEqual(picked.nodes["goal-send"].disabled, true, "진행 중인 요청에서 전송이 열려 있음");
  });

  await asyncCheck("controller: 네트워크/5xx 불확실 응답은 unknown 보류하고 재전송하지 않는다", async () => {
    const fetch = fixtures.makeFetch(() => ({ ok: false, status: 500, data: { error: "boom" } }));
    const picked = mount({ fetch });
    ready(picked);
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    await picked.controller.submit();
    const requestId = picked.controller.getState().requestId;
    assertEqual(picked.fetch.calls.length, 1);
    assertEqual(picked.controller.getState().record.status, "unknown");
    assertEqual(picked.controller.getState().busy, false);
    assertEqual(picked.nodes["goal-send"].disabled, true);
    picked.nodes["goal-send"].click();
    await flush();
    assertEqual(picked.fetch.calls.length, 1, "불확실 응답 후 자동/수동 재전송됨");
    assertEqual(picked.nodes["goal-clear"].disabled, true, "unknown 인데 선택 취소가 열림");
    assert((picked.nodes["goal-clear"].title || "").indexOf("확인") !== -1, "clear 사유 없음: " + picked.nodes["goal-clear"].title);
    observed.networkHold = statusText(picked.nodes);
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: requestId, status: "running", updated_at: 1700000010 }) }));
    assertEqual(picked.controller.getState().record.status, "running");
    assertEqual(picked.nodes["goal-send"].disabled, true);
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: requestId, status: "cancelled", can_submit: true, updated_at: 1700000020 }) }));
    assertEqual(picked.nodes["goal-send"].disabled, false, "확정 종료 후 회복되지 않음");
    observed.networkRecovered = statusText(picked.nodes);
  });

  await asyncCheck("controller: 202 접수 후 상태가 사라지면 unknown 으로 lease 를 붙든다", async () => {
    const picked = mount();
    ready(picked);
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    await picked.controller.submit();
    const requestId = picked.controller.getState().requestId;
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: null }));
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: null }));
    assertEqual(
      picked.controller.getState().record.status,
      "queued",
      "두 번의 미확인으로 아직 unknown 이 되면 안 됨"
    );
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: null }));
    assertEqual(picked.controller.getState().record.status, "unknown");
    assertEqual(picked.nodes["goal-send"].disabled, true, "unknown 상태에서 새 전송이 열림");
    observed.unknownHold = statusText(picked.nodes);
    picked.controller.applySnapshot(
      fixtures.makeSnapshot({
        navigation_request: fixtures.makeRecord({
          request_id: requestId,
          status: "cancelled",
          can_submit: true,
        }),
      })
    );
    assertEqual(picked.controller.getState().record.status, "cancelled");
    assertEqual(picked.nodes["goal-send"].disabled, false, "확정 종료 후 새 전송이 막힘");
  });

  await asyncCheck("controller: 409 가 request 를 주지 않아도 다음 status 의 외부 요청을 추적해 회복한다", async () => {
    const fetch = fixtures.makeFetch(() => ({
      ok: false,
      status: 409,
      data: { success: false, error: "이미 진행 중인 목표 요청이 있습니다" },
    }));
    const picked = mount({ fetch });
    ready(picked);
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    await picked.controller.submit();
    let state = picked.controller.getState();
    assertEqual(state.requestId, null, "409 는 새 요청을 접수하지 않았다");
    assertEqual(state.record, null);
    assertEqual(state.conflictWait, true);
    assertEqual(picked.nodes["goal-send"].disabled, true);
    assertEqual(picked.nodes["goal-clear"].disabled, true);
    assert((picked.nodes["goal-clear"].title || "").indexOf("409") !== -1, "clear 사유 없음: " + picked.nodes["goal-clear"].title);
    observed.conflictWait = statusText(picked.nodes);
    const other = fixtures.makeRecord({ request_id: "mg_foreign_running", status: "running", robot_id: "robot-b", updated_at: 1700000010 });
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: other }));
    state = picked.controller.getState();
    assertEqual(state.conflictWait, false);
    assertEqual(state.foreignRecord.status, "running");
    assertEqual(picked.nodes["goal-send"].disabled, true);
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: null }));
    assertEqual(picked.controller.getState().foreignRecord.request_id, other.request_id);
    assertEqual(picked.nodes["goal-send"].disabled, true);
    assertEqual(picked.nodes["pose-mode"].disabled, true);
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: "mg_foreign_running", status: "running", robot_id: "robot-b", updated_at: 1700000005 }) }));
    assertEqual(picked.controller.getState().foreignRecord.updated_at, 1700000010, "stale foreign 갱신을 되돌림");
    assertEqual(picked.fetch.calls.length, 1, "상태 확인 중 추가 POST 발생");
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: "mg_foreign_running", status: "cancelled", robot_id: "robot-b", can_submit: true, updated_at: 1700000020 }) }));
    state = picked.controller.getState();
    assertEqual(state.foreignRecord, null);
    assertEqual(state.busy, false);
    assertEqual(picked.nodes["goal-send"].disabled, false, "외부 요청 terminal 후 회복 안 됨");
    await picked.controller.submit();
    assertEqual(picked.fetch.calls.length, 2, "회복 후 전송이 되지 않음");
    assert(picked.fetch.calls[1].body.request_id !== picked.fetch.calls[0].body.request_id, "409 거절 후 이전 ID 재사용");
    observed.conflictRecovered = picked.fetch.calls.map((call) => call.body.request_id);
  });

  await asyncCheck("controller: 409 뒤 상태 소멸은 원 요청 종료의 증거가 아니다", async () => {
    const fetch = fixtures.makeFetch(() => ({ ok: false, status: 409, data: { success: false, error: "busy" } }));
    const picked = mount({ fetch });
    ready(picked);
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    await picked.controller.submit();
    assertEqual(picked.controller.getState().conflictWait, true);
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: null }));
    assertEqual(picked.controller.getState().conflictWait, true);
    assertEqual(picked.nodes["goal-send"].disabled, true);
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: null }));
    assertEqual(picked.controller.getState().conflictWait, true);
    assertEqual(picked.nodes["goal-send"].disabled, true);
    assertEqual(picked.nodes["pose-mode"].disabled, true);
    await picked.controller.submit();
    assertEqual(picked.fetch.calls.length, 1);
    observed.conflictNullRecovery = picked.fetch.calls.length;
  });

  await asyncCheck("controller: 새 페이지도 현재 로봇의 최근 도착 결과를 표시한다", async () => {
    const picked = mount();
    ready(picked, fixtures.makeMap(), fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({
      status: "reached", can_submit: true, updated_at: 1700000200,
    }) }));
    assertEqual(picked.controller.getState().record.status, "reached");
    assert(statusText(picked.nodes).indexOf("도착") !== -1);
    assertEqual(picked.fetch.calls.length, 0);
    observed.latestResultRestored = true;
  });

  await asyncCheck("controller: 같은 요청 ID여도 다른 좌표의 도착은 채택하지 않는다", async () => {
    const picked = mount();
    ready(picked);
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    await picked.controller.submit();
    const requestId = picked.controller.getState().requestId;
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({
      request_id: requestId, status: "reached", can_submit: true, updated_at: 1700000200,
      target: { x: 6, y: 5, frame_id: "map" },
    }) }));
    assert(picked.controller.getState().record.status !== "reached");
    assertEqual(picked.nodes["goal-send"].disabled, true);
    observed.wrongTargetRejected = true;
  });

  await asyncCheck("controller: 409 가 다른 active 요청을 알려주면 그 요청의 종료까지 기다린다", async () => {
    const other = fixtures.makeRecord({ request_id: "mg_other_active", status: "running" });
    const fetch = fixtures.makeFetch(() => ({
      ok: false,
      status: 409,
      data: { success: false, error: "이미 진행 중인 목표 요청이 있습니다", request: other },
    }));
    const picked = mount({ fetch });
    ready(picked);
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    await picked.controller.submit();
    assertEqual(picked.controller.getState().requestId, null);
    assertEqual(picked.controller.getState().foreignRecord.status, "running");
    assertEqual(picked.nodes["goal-send"].disabled, true);
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: other }));
    assertEqual(picked.nodes["goal-send"].disabled, true);
    picked.controller.applySnapshot(
      fixtures.makeSnapshot({
        navigation_request: fixtures.makeRecord({ request_id: "mg_other_active", status: "cancelled", can_submit: true }),
      })
    );
    assertEqual(picked.nodes["goal-send"].disabled, false);
  });

  await asyncCheck("controller: nonterminal 이나 can_submit true 는 unknown lease 를 풀지 않는다", async () => {
    const picked = mount();
    ready(picked);
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    await picked.controller.submit();
    const requestId = picked.controller.getState().requestId;
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: requestId, status: "running", can_submit: true, updated_at: 1700000010 }) }));
    assertEqual(picked.controller.getState().record.status, "running");
    assertEqual(picked.nodes["goal-send"].disabled, true, "nonterminal + can_submit true 로 전송이 열림");
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: requestId, status: "unknown", can_submit: true, updated_at: 1700000020 }) }));
    assertEqual(picked.controller.getState().record.status, "unknown");
    assertEqual(picked.nodes["goal-send"].disabled, true, "unknown + can_submit true 로 전송이 열림");
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: requestId, status: "reached", can_submit: true, updated_at: 1700000030 }) }));
    assertEqual(picked.nodes["goal-send"].disabled, false, "정당 terminal matching ID 도 회복하지 못함");
  });

  await asyncCheck("controller: terminal 이후 stale/out-of-order nonterminal 은 record 를 되돌리지 않는다", async () => {
    const picked = mount();
    ready(picked);
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    await picked.controller.submit();
    const requestId = picked.controller.getState().requestId;
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: requestId, status: "running", updated_at: 1700000200 }) }));
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: requestId, status: "queued", updated_at: 1700000100 }) }));
    assertEqual(picked.controller.getState().record.status, "running", "오래된 queued 가 running 을 되돌림");
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: requestId, status: "reached", can_submit: true, updated_at: 1700000300 }) }));
    assertEqual(picked.nodes["goal-send"].disabled, false);
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: requestId, status: "running", updated_at: 1700000400 }) }));
    assertEqual(picked.controller.getState().record.status, "reached", "terminal 이후 nonterminal 로 되돌아감");
    assertEqual(picked.nodes["goal-send"].disabled, false, "terminal lock 이 전송을 다시 막음");
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: requestId, status: "reached", can_submit: true, updated_at: 1700000250 }) }));
    assertEqual(picked.controller.getState().record.updated_at, 1700000300, "오래된 terminal 이 최신 record 를 덮음");
  });

  await asyncCheck("controller: 다른 로봇/프레임/geometry record 는 내 도착으로 채택하지 않는다", async () => {
    const picked = mount();
    ready(picked);
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    await picked.controller.submit();
    const requestId = picked.controller.getState().requestId;
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: requestId, status: "reached", robot_id: "robot-b", updated_at: 1700000010 }) }));
    assertEqual(picked.controller.getState().record.status, "queued", "다른 로봇 record 를 채택함");
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: requestId, status: "reached", target: { x: 5, y: 5, frame_id: "odom" }, updated_at: 1700000020 }) }));
    assertEqual(picked.controller.getState().record.status, "queued", "다른 프레임 record 를 채택함");
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: requestId, status: "reached", geometry_id: "b".repeat(64), updated_at: 1700000030 }) }));
    assertEqual(picked.controller.getState().record.status, "queued", "다른 geometry record 를 채택함");
    assert(statusText(picked.nodes).indexOf("도착") === -1, "다른 신원의 도착을 표시함");
    assertEqual(picked.nodes["goal-send"].disabled, true);
  });

  await asyncCheck("controller: 클라이언트가 다른 로봇/프레임에 연결되면 옛 record 를 도착으로 표시하지 않는다", async () => {
    const picked = mount();
    ready(picked);
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    await picked.controller.submit();
    const requestId = picked.controller.getState().requestId;
    picked.controller.applySnapshot(
      fixtures.makeSnapshot({
        state: { agent_id: "robot-b", navigation_frame: "map" },
        navigation_request: fixtures.makeRecord({ request_id: requestId, status: "reached", can_submit: true, updated_at: 1700000010 }),
      })
    );
    assertEqual(picked.controller.getState().record.status, "queued", "다른 로봇 연결에서 옛 기록을 도착으로 채택함");
    assert(statusText(picked.nodes).indexOf("도착") === -1, "다른 로봇 연결에서 도착을 표시함");
    assertEqual(picked.nodes["goal-send"].disabled, true);
    picked.controller.applySnapshot(
      fixtures.makeSnapshot({
        state: { agent_id: "robot-a", navigation_frame: "odom" },
        navigation_request: fixtures.makeRecord({ request_id: requestId, status: "reached", can_submit: true, updated_at: 1700000020 }),
      })
    );
    assertEqual(picked.controller.getState().record.status, "queued", "다른 프레임 연결에서 옛 기록을 도착으로 채택함");
    assert(statusText(picked.nodes).indexOf("도착") === -1);
  });

  await asyncCheck("controller: partial/invalid status record 는 무시하고 lease 를 유지한다", async () => {
    const picked = mount();
    ready(picked);
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    await picked.controller.submit();
    const requestId = picked.controller.getState().requestId;
    const partials = [
      { request_id: requestId, status: "running" },
      { request_id: requestId, status: "running", robot_id: "robot-a", target: { x: 5, y: 5, frame_id: "map" } },
      fixtures.makeRecord({ request_id: requestId, status: "running", geometry_id: "A".repeat(64) }),
      fixtures.makeRecord({ request_id: requestId, status: "running", robot_id: "bad\nid" }),
      fixtures.makeRecord({ request_id: requestId, status: "running", target: { x: true, y: 5, frame_id: "map" } }),
      "not-a-record",
    ];
    for (const partial of partials) {
      picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: partial }));
      assertEqual(picked.controller.getState().record.status, "queued", "invalid record 를 채택함: " + JSON.stringify(partial));
    }
    assertEqual(picked.nodes["goal-send"].disabled, true);
  });

  await asyncCheck("controller: control 토큰이 비어 있는 NoAuth 로컬 모드에서도 전송한다", async () => {
    const picked = mount({ mount: { getControlToken: () => "" } });
    ready(picked);
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    await picked.controller.submit();
    assertEqual(picked.fetch.calls.length, 1);
    assertEqual(picked.fetch.calls[0].token, "");
    observed.noAuthToken = picked.fetch.calls[0].token;
  });

  await asyncCheck("controller: resize/visibility/Enter 는 POST 없이 선택만 유지한다", async () => {
    const picked = mount();
    const map = fixtures.makeMap();
    ready(picked, map, fixtures.makeSnapshot());
    picked.controller.toggleGoalMode();
    picked.clickWorld(map, 5, 5);
    picked.canvas.width = 1200;
    picked.canvas.height = 600;
    picked.controller.render();
    picked.doc.dispatch("visibilitychange", {});
    picked.key("Enter");
    await flush();
    assertEqual(picked.fetch.calls.length, 0, "resize/visibility/Enter 가 POST 함");
    assertEqual(picked.controller.getState().candidate.x, 5);
  });

  await asyncCheck("controller: own unknown/foreign running/409 wait 동안 initial pose 전환을 막는다", async () => {
    const network = fixtures.makeFetch(() => ({ ok: false, status: 500, data: {} }));
    const own = mount({ fetch: network });
    ready(own);
    own.controller.toggleGoalMode();
    own.clickWorld(fixtures.makeMap(), 5, 5);
    await own.controller.submit();
    own.controller.toggleInitialPose();
    assertEqual(own.controller.getState().mode, "goal");
    assertEqual(own.nodes["pose-mode"].disabled, true);

    const pending = mount();
    ready(pending);
    pending.controller.toggleGoalMode();
    pending.clickWorld(fixtures.makeMap(), 5, 5);
    await pending.controller.submit();
    pending.controller.toggleInitialPose();
    assertEqual(pending.controller.getState().mode, "goal");
    assertEqual(pending.nodes["pose-mode"].disabled, true);

    const foreign = mount();
    ready(foreign, fixtures.makeMap(), fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: "mg_foreign_run", status: "running" }) }));
    foreign.controller.toggleGoalMode();
    foreign.controller.toggleInitialPose();
    assertEqual(foreign.controller.getState().mode, "goal");
    assertEqual(foreign.nodes["pose-mode"].disabled, true);

    const conflict = mount({ fetch: fixtures.makeFetch(() => ({ ok: false, status: 409, data: { error: "busy" } })) });
    ready(conflict);
    conflict.controller.toggleGoalMode();
    conflict.clickWorld(fixtures.makeMap(), 5, 5);
    await conflict.controller.submit();
    conflict.controller.toggleInitialPose();
    assertEqual(conflict.controller.getState().mode, "goal");
    assertEqual(conflict.nodes["pose-mode"].disabled, true);
  });

  check("controller: 목표/초기 위치 모드는 배타적이고 초기 위치 전환이 위임된다", () => {
    const picked = mount();
    ready(picked);
    picked.nodes["goal-mode"].click();
    assertEqual(picked.controller.getState().mode, "goal");
    assertEqual(picked.nodes["goal-mode"].getAttribute("aria-pressed"), "true");
    picked.nodes["pose-mode"].click();
    assertEqual(picked.modes[picked.modes.length - 1], "initial_pose");
    assertEqual(picked.controller.getState().mode, "initial_pose");
    picked.nodes["goal-mode"].click();
    assertEqual(picked.controller.getState().mode, "goal");
    observed.modes = picked.modes;
  });

  check("controller: 선택 전에는 전송 버튼이 비활성이며 안내 문구가 있다", () => {
    const picked = mount();
    ready(picked);
    assertEqual(picked.nodes["goal-send"].disabled, true);
    assert(statusText(picked.nodes).length > 0);
    picked.nodes["goal-clear"].click();
    assertEqual(picked.fetch.calls.length, 0);
  });

  await asyncCheck("controller: 모드 밖 클릭/Enter 는 전송하지 않고 방향키/Escape 만 동작한다", async () => {
    const picked = mount();
    const map = fixtures.makeMap();
    ready(picked, map, fixtures.makeSnapshot());
    picked.clickWorld(map, 5, 5);
    assertEqual(picked.controller.getState().candidate, null, "view 모드 클릭이 선택을 만듦");
    picked.controller.toggleGoalMode();
    picked.clickWorld(map, 5, 5);
    assertEqual(picked.controller.getState().candidate.x, 5);
    picked.key("ArrowRight");
    assertEqual(picked.controller.getState().candidate.x, 5.5);
    picked.key("ArrowUp");
    assertEqual(picked.controller.getState().candidate.y, 5.5);
    picked.key("ArrowLeft");
    assertEqual(picked.controller.getState().candidate.x, 5);
    picked.key("Enter");
    await flush();
    assertEqual(picked.fetch.calls.length, 0, "Enter 가 확인 절차를 건너뛰고 전송함");
    picked.key("Escape");
    assertEqual(picked.controller.getState().mode, "view");
    assertEqual(picked.controller.getState().candidate, null);
    observed.keyboard = "ok";
  });

  await asyncCheck("controller: 지도 갱신은 자동 명령을 만들지 않고 pending 선택을 유지한다", async () => {
    const picked = mount();
    const map = fixtures.makeMap();
    ready(picked, map, fixtures.makeSnapshot());
    picked.controller.toggleGoalMode();
    picked.clickWorld(map, 5, 5);
    const updated = fixtures.makeMap({ stampNs: 99 });
    updated.cells[0] = 100;
    picked.controller.applyMap(updated);
    assertEqual(picked.fetch.calls.length, 0, "지도 갱신이 자동 전송을 만듦");
    assertEqual(picked.controller.getState().candidate.x, 5);
    const ctx = picked.canvas.getContext("2d");
    picked.controller.drawOverlay(ctx, updated, geometry.layout(800, 400, updated));
    assert(
      ctx.calls.some((call) => call.op === "arc"),
      "목표 마커를 그리지 않음"
    );
    const changedGeometry = fixtures.makeMap({ geometryId: "c".repeat(64) });
    picked.controller.applyMap(changedGeometry);
    assertEqual(picked.controller.getState().candidate, null, "geometry 변경 후에도 선택이 남음");
  });

  await asyncCheck("controller: /api/status navigation_request 의 모든 상태를 표시한다", async () => {
    const labels = {
      queued: "대기",
      planning: "계획",
      running: "주행",
      reached: "도착",
      failed: "실패",
      cancel_requested: "취소 요청",
      cancelled: "취소됨",
      unknown: "확인",
    };
    for (const statusValue of Object.keys(labels)) {
      const picked = mount();
      ready(picked);
      picked.controller.toggleGoalMode();
      picked.clickWorld(fixtures.makeMap(), 5, 5);
      await picked.controller.submit();
      const requestId = picked.controller.getState().requestId;
      picked.controller.applySnapshot(
        fixtures.makeSnapshot({
          navigation_request: fixtures.makeRecord({
            request_id: requestId,
            status: statusValue,
            progress: statusValue === "running" ? 42 : null,
            can_submit: ["reached", "failed", "cancelled"].indexOf(statusValue) !== -1,
          }),
        })
      );
      const text = statusText(picked.nodes);
      assert(text.indexOf(labels[statusValue]) !== -1, statusValue + " 상태 문구 없음: " + text);
      if (statusValue === "running") assert(text.indexOf("42") !== -1, "진행률 표시 없음: " + text);
      const active = ["queued", "planning", "running", "cancel_requested", "unknown"];
      assertEqual(
        picked.nodes["goal-send"].disabled,
        active.indexOf(statusValue) !== -1,
        statusValue + " 전송 버튼 상태가 다름"
      );
    }
    observed.statusLabels = labels;
  });

  await asyncCheck("controller: 다른 request_id 의 도착 기록은 내 요청 도착으로 표시하지 않는다", async () => {
    const picked = mount();
    ready(picked);
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    await picked.controller.submit();
    picked.controller.applySnapshot(
      fixtures.makeSnapshot({
        navigation_request: fixtures.makeRecord({ request_id: "mg_other_request", status: "reached" }),
      })
    );
    assertEqual(picked.controller.getState().record.status, "queued");
    assert(statusText(picked.nodes).indexOf("도착") === -1, "다른 요청의 도착을 내 도착으로 표시함");
    assertEqual(picked.nodes["goal-send"].disabled, true);
  });

  await asyncCheck("controller: 서버 active 기록은 로컬 요청 ID 가 없어도 새 전송을 막는다", async () => {
    const picked = mount();
    ready(
      picked,
      fixtures.makeMap(),
      fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ status: "running" }) })
    );
    picked.controller.toggleGoalMode();
    picked.clickWorld(fixtures.makeMap(), 5, 5);
    assertEqual(picked.nodes["goal-send"].disabled, true, "서버 active lease 를 무시함");
  });

  await asyncCheck("controller: DPR/패딩이 있어도 클릭 좌표를 world 로 정확히 변환한다", async () => {
    const picked = mount();
    const map = fixtures.makeMap();
    ready(picked, map, fixtures.makeSnapshot());
    picked.controller.toggleGoalMode();
    const canvas = picked.canvas;
    canvas.width = 1600;
    canvas.height = 800;
    canvas.clientWidth = 800;
    canvas.clientHeight = 400;
    canvas.rect = { left: 40, top: 20, width: 800, height: 400 };
    const internal = geometry.worldToCanvas(geometry.layout(1600, 800, map), map, 5, 5);
    canvas.dispatch("pointerdown", { clientX: 40 + internal.x / 2, clientY: 20 + internal.y / 2, pointerId: 1 });
    assertEqual(picked.controller.getState().candidate.x, 5);
    assertEqual(picked.controller.getState().candidate.y, 5);
    const blocked = fixtures.makeMap({ stampNs: 11 });
    blocked.cells[6 * blocked.width + 8] = 100;
    picked.controller.applyMap(blocked);
    assertEqual(picked.controller.getState().candidate, null, "DPR 선택 지점이 셀 변경 후 재검증되지 않음");
    const next = geometry.worldToCanvas(geometry.layout(1600, 800, map), map, 6, 6);
    canvas.dispatch("pointerdown", { clientX: 40 + next.x / 2, clientY: 20 + next.y / 2, pointerId: 1 });
    const shifted = fixtures.makeMap({ stampNs: 12 });
    shifted.cells[0] = 100;
    picked.controller.applyMap(shifted);
    assertEqual(picked.controller.getState().candidate.x, 6, "무관한 셀 변경이 DPR 선택을 지움");
  });

  await asyncCheck("controller: geometry/프레임이 없으면 선택/전송을 막고 일반 텍스트로 보내지 않는다", async () => {
    const picked = mount();
    const map = fixtures.makeMap({ geometryId: null });
    ready(
      picked,
      map,
      fixtures.makeSnapshot({
        localization: { pose: { available: true }, map: { available: true, frame_id: "map", geometry_id: null } },
      })
    );
    picked.controller.toggleGoalMode();
    picked.clickWorld(map, 5, 5);
    assertEqual(picked.controller.getState().candidate, null);
    assertEqual(picked.nodes["goal-send"].disabled, true);
    assertEqual(picked.fetch.calls.length, 0);
    assert(statusText(picked.nodes).indexOf("geometry") !== -1, "사유 표시 없음: " + statusText(picked.nodes));
  });

  await asyncCheck("controller: 확정 종료 뒤 새 선택은 옛 terminal 기록을 다시 표시하지 않는다", async () => {
    const picked = mount();
    const map = fixtures.makeMap();
    ready(picked, map, fixtures.makeSnapshot());
    picked.controller.toggleGoalMode();
    picked.clickWorld(map, 5, 5);
    await picked.controller.submit();
    const requestId = picked.controller.getState().requestId;
    const terminal = fixtures.makeRecord({ request_id: requestId, status: "reached", can_submit: true });
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: terminal }));
    picked.clickWorld(map, 6, 6);
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: terminal }));
    picked.controller.applySnapshot(fixtures.makeSnapshot({ navigation_request: fixtures.makeRecord({ request_id: requestId, status: "running", updated_at: 1700000400 }) }));
    const state = picked.controller.getState();
    assertEqual(state.candidate && state.candidate.x, 6);
    assertEqual(state.record, null);
    assertEqual(state.foreignRecord, null);
    assertEqual(picked.nodes["goal-send"].disabled, false, "stale cross-request goal 이 새 전송을 막음");
  });

  check("request_id: 허용 문자/길이 경계 안에서 생성된다", () => {
    const ids = [];
    for (let index = 0; index < 20; index += 1) {
      ids.push(goalPicker.generateRequestId(() => 1700000000000 + index, () => 0.5));
    }
    for (const id of ids) {
      assert(/^[A-Za-z0-9_-]{1,64}$/.test(id), "허용되지 않는 request_id: " + id);
    }
    assert(new Set(ids).size >= 2, "request_id 가 항상 같음");
  });

  process.stdout.write(JSON.stringify({ checks, observed }));
}

function statusText(nodes) {
  return nodes["goal-status"].textContent;
}

main().catch((err) => {
  process.stdout.write(
    JSON.stringify({
      checks: checks.concat([
        { name: "harness", ok: false, error: String((err && err.stack) || err) },
      ]),
      observed,
    })
  );
});
