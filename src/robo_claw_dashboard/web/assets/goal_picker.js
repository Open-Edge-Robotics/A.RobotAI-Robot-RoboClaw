/* 목표 선택 모드 — 지도 클릭으로 후보만 고르고, 명시적 버튼으로만 /api/chat 에 보낸다.
 * 주행 goal/skill 을 직접 실행하지 않는다. 표준 자연어 메시지와 검증된 map_goal 만
 * 기존 /api/chat 으로 보내고, 진행/도착 표시는 기존 /api/status 의 navigation_request 만 쓴다.
 * (추가 타이머/폴링 없음) 좌표/프레임은 지시가 아니라 데이터다. */
(function (global) {
  "use strict";
  const STATES = ["queued", "planning", "running", "reached", "failed", "cancel_requested", "cancelled", "unknown"];
  const TERMINAL = { reached: true, failed: true, cancelled: true };
  const LABELS = {
    queued: "대기열", planning: "경로 계획 중", running: "주행 중", reached: "도착 확인됨",
    failed: "실패", cancel_requested: "취소 요청됨", cancelled: "취소됨", unknown: "상태 미확인",
  };
  const MAX_COORDINATE = 1e6;
  const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/;
  const GEOMETRY_ID = /^[0-9a-f]{64}$/;
  const REQUEST_ID = /^[A-Za-z0-9_-]{1,64}$/;
  const UNCONFIRMED_STATUS_POLLS = 3;
  const RETIRED_LIMIT = 8;
  function mapApi() { return global.RcMapGeometry || null; }
  function bad(reason) { return { ok: false, reason }; }
  function isSafeIdentifier(value) { return typeof value === "string" && SAFE_ID.test(value); }
  function isGeometryId(value) { return typeof value === "string" && GEOMETRY_ID.test(value); }
  function isRequestId(value) { return typeof value === "string" && REQUEST_ID.test(value); }
  function notice(tone, text) { return { tone, text }; }
  function formatCoordinate(value) { return (Object.is(value, -0) ? 0 : value).toFixed(6); }
  function canonicalCoordinate(value) {
    if (typeof value !== "number" || !Number.isFinite(value) || Math.abs(value) > MAX_COORDINATE) return null;
    const rounded = Number(value.toFixed(6));
    return Object.is(rounded, -0) ? 0 : rounded;
  }
  function canonicalPoint(point) {
    if (!point) return null;
    const x = canonicalCoordinate(point.x);
    const y = canonicalCoordinate(point.y);
    return x === null || y === null ? null : { x, y };
  }
  function formatCanonicalMessage(input) {
    const data = input || {};
    return "지도에서 선택한 목표로 이동해 주세요. 로봇: " + data.robotId +
      ". 기준 프레임: " + data.frameId +
      ". 목표 좌표: x=" + formatCoordinate(data.x) + " m, y=" + formatCoordinate(data.y) +
      " m. 필요한 주행 안전 절차를 먼저 적용하고, 목표 좌표와 프레임을 변경하지 마세요.";
  }
  function isRecord(record) {
    if (!record || typeof record !== "object" || !isRequestId(record.request_id)) return false;
    if (STATES.indexOf(record.status) === -1) return false;
    const target = record.target || {};
    if (!Number.isFinite(target.x) || !Number.isFinite(target.y) || typeof target.frame_id !== "string") return false;
    if (!isSafeIdentifier(record.robot_id) || !isGeometryId(record.geometry_id)) return false;
    if (!isSafeIdentifier(target.frame_id) || Math.abs(target.x) > MAX_COORDINATE || Math.abs(target.y) > MAX_COORDINATE) return false;
    if (typeof record.execution_started !== "boolean" || typeof record.cancel_confirmed !== "boolean") return false;
    if (typeof record.can_submit !== "boolean") return false;
    if (record.progress !== null && (!Number.isInteger(record.progress) || record.progress < 0 || record.progress > 100)) return false;
    return Number.isFinite(record.created_at) && Number.isFinite(record.updated_at);
  }
  function resolveSelection(input) {
    const data = input || {};
    const map = data.map;
    if (!map || !(map.width > 0) || !(map.height > 0) || !(map.resolution > 0) || !map.cells) return bad("지도를 아직 사용할 수 없습니다.");
    if (!isSafeIdentifier(data.agentId)) return bad("로봇 ID(agent_id)를 확인할 수 없습니다.");
    if (!isSafeIdentifier(data.navigationFrame)) return bad("주행 기준 프레임(navigation_frame)을 확인할 수 없습니다.");
    if (map.frameId !== data.navigationFrame) return bad("지도 프레임(" + (map.frameId || "없음") + ")과 주행 프레임(" + data.navigationFrame + ")이 다릅니다.");
    if (!isGeometryId(map.geometryId)) return bad("지도 geometry ID를 확인할 수 없습니다.");
    if (data.mapGeometryHint && data.mapGeometryHint !== map.geometryId) return bad("서버 지도 메타데이터가 갱신되었습니다. 지도 새로고침을 기다립니다.");
    const point = canonicalPoint(data.point);
    if (!point) return bad("목표 좌표가 유효하지 않습니다.");
    const api = mapApi();
    if (!api || typeof api.worldToGrid !== "function" || typeof api.cellAt !== "function") return bad("지도 좌표 변환을 사용할 수 없습니다.");
    const cell = api.worldToGrid(map, point.x, point.y);
    if (!cell) return bad("지도 범위를 벗어난 지점입니다.");
    if (api.cellAt(map, cell.col, cell.row) !== 0) return bad("자유 셀(0)이 아닌 지점입니다. 알 수 없음/점유 셀은 목표로 보낼 수 없습니다.");
    const message = formatCanonicalMessage({ robotId: data.agentId, frameId: data.navigationFrame, x: point.x, y: point.y });
    return {
      ok: true, x: point.x, y: point.y, robotId: data.agentId, frameId: data.navigationFrame, geometryId: map.geometryId, message,
      mapGoal: { x: point.x, y: point.y, frame_id: data.navigationFrame, robot_id: data.agentId, geometry_id: map.geometryId },
    };
  }
  function buildChatBody(input) {
    const data = input || {};
    if (!isRequestId(data.requestId)) return bad("request_id 형식이 올바르지 않습니다.");
    const goal = data.mapGoal;
    if (!goal || typeof goal !== "object") return bad("map_goal 이 없습니다.");
    if (Object.keys(goal).sort().join(",") !== "frame_id,geometry_id,robot_id,x,y") return bad("map_goal 키가 계약과 다릅니다.");
    const x = canonicalCoordinate(goal.x);
    const y = canonicalCoordinate(goal.y);
    if (x === null || x !== goal.x) return bad("x 좌표가 6자리 canonical 값이 아닙니다.");
    if (y === null || y !== goal.y) return bad("y 좌표가 6자리 canonical 값이 아닙니다.");
    if (!isSafeIdentifier(goal.robot_id)) return bad("robot_id 가 안전한 형식이 아닙니다.");
    if (!isSafeIdentifier(goal.frame_id)) return bad("frame_id 가 안전한 형식이 아닙니다.");
    if (!isGeometryId(goal.geometry_id)) return bad("geometry_id 가 64자리 소문자 hex 가 아닙니다.");
    const message = formatCanonicalMessage({ robotId: goal.robot_id, frameId: goal.frame_id, x, y });
    if (data.message !== message) return bad("메시지가 표준 목표 메시지와 다릅니다.");
    return { ok: true, body: { message, request_id: data.requestId, map_goal: goal } };
  }
  function generateRequestId(clock, random) {
    const now = typeof clock === "function" ? clock() : Date.now();
    const draw = typeof random === "function" ? random : Math.random;
    const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
    let suffix = "";
    for (let index = 0; index < 10; index += 1) {
      suffix += alphabet[Math.min(alphabet.length - 1, Math.max(0, Math.floor(draw() * alphabet.length)))];
    }
    return "mg_" + Math.floor(now % 100000000).toString(36) + "_" + suffix;
  }
  function createState() {
    return {
      mode: "view", context: { agentId: null, navigationFrame: null, map: null, mapGeometryHint: null },
      candidate: null, record: null, foreignRecord: null, requestId: null, busy: false, statusMisses: 0, notice: null, conflictWait: false, conflictWaits: 0, retiredIds: [] };
  }
  function ownLease(state) { return Boolean(state.record && !TERMINAL[state.record.status]); }
  function foreignLease(state) { return Boolean(state.foreignRecord && !TERMINAL[state.foreignRecord.status]); }
  function leaseHeld(state) { return ownLease(state) || foreignLease(state); }
  function blocked(state) { return leaseHeld(state) || state.conflictWait; }
  function retire(state, requestId) {
    return state.retiredIds.filter((item) => item !== requestId).concat(requestId).slice(-RETIRED_LIMIT);
  }
  function identityMatches(state, record) {
    const identity = (state.candidate && state.candidate.identity) || {};
    return (!identity.robotId || record.robot_id === identity.robotId) && (!identity.frameId || record.target.frame_id === identity.frameId) &&
      (!identity.geometryId || record.geometry_id === identity.geometryId) &&
      (!state.candidate || (Math.abs(record.target.x - state.candidate.x) <= 1e-6 && Math.abs(record.target.y - state.candidate.y) <= 1e-6)) &&
      (!state.context.agentId || record.robot_id === state.context.agentId) &&
      (!state.context.navigationFrame || record.target.frame_id === state.context.navigationFrame);
  }
  function clearReason(state) {
    if (state.record && state.record.status === "unknown") return "요청 접수/진행 상태를 확인할 수 없어 선택을 지울 수 없습니다. 확정 상태를 기다립니다.";
    if (ownLease(state) || state.conflictWait) return "진행 중이거나 409로 거절된 원 요청의 상태를 확인하는 동안 선택을 지울 수 없습니다.";
    if (foreignLease(state)) return "서버의 다른 목표 요청이 진행 중이어서 선택을 지울 수 없습니다.";
    return "";
  }
  function releaseForeign(state, message, requestId) {
    return { ...state, conflictWait: false, conflictWaits: 0, foreignRecord: null, record: null, requestId: null, statusMisses: 0,
      retiredIds: requestId ? retire(state, requestId) : state.retiredIds, notice: notice("info", message) };
  }
  function selectionFor(state) {
    return resolveSelection({ map: state.context.map, agentId: state.context.agentId,
      navigationFrame: state.context.navigationFrame, mapGeometryHint: state.context.mapGeometryHint, point: state.candidate });
  }
  function pendingRecord(state, reason) {
    const candidate = state.candidate || {};
    const identity = candidate.identity || {};
    return { request_id: state.requestId || "", robot_id: identity.robotId || "", status: "unknown", message: "",
      target: { x: candidate.x, y: candidate.y, frame_id: identity.frameId || "" }, geometry_id: identity.geometryId || "", execution_started: false,
      cancel_confirmed: false, reason: reason || "", progress: null, created_at: 0, updated_at: 0, can_submit: false };
  }
  function updateContext(state, context) {
    const next = { ...state.context };
    if (context.agentId !== undefined) next.agentId = context.agentId || null;
    if (context.navigationFrame !== undefined) next.navigationFrame = context.navigationFrame || null;
    if (context.map !== undefined) next.map = context.map || null;
    if (context.mapGeometryHint !== undefined) next.mapGeometryHint = context.mapGeometryHint || null;
    let candidate = state.candidate;
    let message = state.notice;
    if (candidate) {
      const identity = candidate.identity || {};
      const changed = identity.robotId !== next.agentId || identity.frameId !== next.navigationFrame ||
        (next.map && (identity.geometryId !== next.map.geometryId || identity.topic !== next.map.topic));
      if (changed && !ownLease(state)) { candidate = null; message = notice("info", "로봇/프레임/지도 정보가 바뀌어 선택을 지웠습니다."); }
      else if (changed) message = notice("danger", "연결된 로봇/프레임/지도 신원이 바뀌었습니다. 기존 요청의 도착은 표시하지 않습니다.");
    }
    return { ...state, context: next, candidate, notice: message };
  }
  function updateMap(state, map) {
    const nextMap = map || null;
    const context = { ...state.context, map: nextMap };
    let candidate = state.candidate;
    let message = state.notice;
    if (!nextMap) {
      if (candidate && !ownLease(state)) { candidate = null; message = notice("info", "지도를 사용할 수 없어 선택을 지웠습니다."); }
    } else if (candidate && !ownLease(state)) {
      const identity = candidate.identity || {};
      const changed = identity.geometryId !== nextMap.geometryId || identity.topic !== nextMap.topic || identity.frameId !== nextMap.frameId;
      if (changed) { candidate = null; message = notice("info", "지도(geometry/topic/frame)가 바뀌어 선택을 지웠습니다."); }
      else {
        const result = selectionFor({ ...state, context, candidate });
        if (!result.ok) { candidate = null; message = notice("danger", "지도 갱신 후 목표를 다시 확인할 수 없습니다: " + result.reason); }
        else candidate = { x: result.x, y: result.y, identity: candidate.identity };
      }
    }
    return { ...state, context, candidate, notice: message };
  }
  function updateMode(state, mode) {
    if (["view", "initial_pose", "goal"].indexOf(mode) === -1 || mode === state.mode) return state;
    return { ...state, mode, candidate: mode === "goal" || blocked(state) ? state.candidate : null };
  }
  function selectPoint(state, point) {
    if (blocked(state)) return { ...state, notice: notice("danger", "진행 중이거나 상태 미확인인 목표 요청이 있어 새 목표를 선택할 수 없습니다.") };
    const result = selectionFor({ ...state, candidate: point });
    if (!result.ok) return { ...state, notice: notice("danger", result.reason) };
    const identity = { robotId: result.robotId, frameId: result.frameId, geometryId: result.geometryId, topic: state.context.map.topic };
    return { ...state, mode: "goal", record: null, requestId: null, statusMisses: 0, candidate: { x: result.x, y: result.y, identity }, notice: notice("info", "목표 지점을 선택했습니다. '이동 메시지 보내기'를 눌러야 전송됩니다.") };
  }
  function clearSelection(state) {
    if (blocked(state)) return { ...state, notice: notice("danger", clearReason(state)) };
    return { ...state, candidate: null, requestId: null, mode: state.mode === "goal" ? "view" : state.mode, notice: notice("info", "목표 선택을 취소했습니다.") };
  }
  function acceptSubmission(state, record) {
    if (!isRecord(record) || record.request_id !== state.requestId || !identityMatches(state, record)) {
      return { ...state, busy: false, statusMisses: 0, record: pendingRecord(state, "요청 접수 응답을 확인할 수 없습니다. 상태 폴링을 기다립니다."), notice: notice("danger", "요청 접수 응답을 확인할 수 없습니다.") };
    }
    const accepted = record.status === "reached" ? "에이전트가 도착을 확인했습니다." : "요청이 접수되었습니다(202). 도착 여부는 상태 폴링에서만 확인합니다.";
    const next = { ...state, busy: false, record, statusMisses: 0, conflictWait: false, notice: notice("info", accepted) };
    if (TERMINAL[record.status]) next.retiredIds = retire(state, record.request_id);
    return next;
  }
  function adoptOwn(state, record) {
    const next = { ...state, conflictWait: false, conflictWaits: 0, record, foreignRecord: null, statusMisses: 0, notice: notice("info", "요청이 접수된 것으로 확인되었습니다.") };
    return TERMINAL[record.status] ? { ...next, retiredIds: retire(state, record.request_id) } : next;
  }
  function applyStatus(state, incoming) {
    if (!incoming) {
      if (state.conflictWait) {
        return { ...state, conflictWaits: Math.min(255, state.conflictWaits + 1), notice: notice("danger", "원 요청 상태를 확인하지 못해 이동과 초기 위치 설정을 보류합니다.") };
      }
      if (!state.requestId) return state.foreignRecord ? { ...state, foreignRecord: { ...state.foreignRecord, status: "unknown", can_submit: false, reason: "원 요청의 종료를 확인하지 못했습니다." } } : state;
      if (state.record && TERMINAL[state.record.status]) return state;
      const misses = state.statusMisses + 1; if (misses < UNCONFIRMED_STATUS_POLLS) return { ...state, statusMisses: misses };
      const base = state.record || pendingRecord(state, "상태 미확인");
      return { ...state, statusMisses: misses, record: { ...base, status: "unknown", reason: base.reason || "에이전트 상태에서 요청을 확인할 수 없습니다.", can_submit: false } };

    }
    if (!isRecord(incoming) || state.retiredIds.indexOf(incoming.request_id) !== -1) return state;
    if (state.conflictWait) {
      if (state.requestId && incoming.request_id === state.requestId && identityMatches(state, incoming)) return adoptOwn(state, incoming);
      if (TERMINAL[incoming.status]) return releaseForeign(state, "진행 중이던 목표 요청이 종료되었습니다. 선택 지점을 확인한 뒤 다시 전송할 수 있습니다.", incoming.request_id);
      return { ...state, conflictWait: false, conflictWaits: 0, foreignRecord: incoming, statusMisses: 0, notice: notice("danger", "진행 중인 목표 요청(" + incoming.request_id + ")의 종료를 기다립니다.") };
    }
    if (state.requestId) {
      if (incoming.request_id !== state.requestId || !identityMatches(state, incoming)) return state;
      if (state.record && TERMINAL[state.record.status] && !TERMINAL[incoming.status]) return state;
      if (state.record && incoming.updated_at < state.record.updated_at) return state;
      const next = { ...state, record: incoming, statusMisses: 0, foreignRecord: null };
      return TERMINAL[incoming.status] ? { ...next, retiredIds: retire(state, incoming.request_id) } : next;
    }
    if (state.foreignRecord && state.foreignRecord.request_id === incoming.request_id && incoming.updated_at < state.foreignRecord.updated_at) return state;
    if (!state.foreignRecord && !state.candidate && TERMINAL[incoming.status] &&
      state.context.agentId && state.context.navigationFrame && identityMatches(state, incoming)) {
      return { ...state, record: incoming, retiredIds: retire(state, incoming.request_id),
        notice: notice("info", "현재 로봇의 최근 이동 결과를 표시합니다.") };
    }
    if (TERMINAL[incoming.status]) return releaseForeign(state, "진행 중이던 목표 요청이 종료되었습니다. 선택 지점을 확인한 뒤 다시 전송할 수 있습니다.", incoming.request_id);
    return { ...state, foreignRecord: incoming, statusMisses: 0 };
  }
  function reduce(state, action) {
    if (!action || typeof action.type !== "string") return state;
    switch (action.type) {
      case "mode": return updateMode(state, action.mode);
      case "context": return updateContext(state, action.context || {});
      case "map": return updateMap(state, action.map);
      case "select": return selectPoint(state, action.point);
      case "clear": return clearSelection(state);
      case "submit-start":
        return { ...state, busy: true, requestId: action.requestId, statusMisses: 0, notice: notice("info", "목표 이동 요청을 전송했습니다. 접수 응답을 기다립니다…") };
      case "submit-accepted": return acceptSubmission(state, action.record);
      case "submit-error":
        if (action.kind === "retryable") return { ...state, busy: false, statusMisses: 0, record: pendingRecord(state, "네트워크/서버 오류로 요청 접수 여부를 확인할 수 없습니다."),
          notice: notice("danger", "요청 접수 여부를 확인할 수 없습니다. 상태 확인 전까지 다시 보낼 수 없습니다. (" + action.reason + ")") };
        if (action.kind === "conflict") {
          const incoming = isRecord(action.record) ? action.record : null;
          const reason = action.reason || "이미 진행 중인 목표 요청이 있습니다.";
          if (incoming && state.requestId && incoming.request_id === state.requestId && identityMatches(state, incoming)) return { ...state, busy: false, record: incoming, statusMisses: 0, notice: notice("danger", reason) };
          if (incoming && TERMINAL[incoming.status]) return releaseForeign({ ...state, busy: false }, "진행 중이던 목표 요청이 이미 종료되었습니다. 선택 지점을 확인한 뒤 다시 전송할 수 있습니다.", incoming.request_id);
          if (incoming) return { ...state, busy: false, requestId: null, record: null, conflictWait: false, conflictWaits: 0, foreignRecord: incoming, statusMisses: 0, notice: notice("danger", reason + " (진행 중인 요청 " + incoming.request_id + " 종료 대기)") };
          return { ...state, busy: false, requestId: null, record: null, foreignRecord: null, conflictWait: true, conflictWaits: 0, statusMisses: 0, notice: notice("danger", reason + " 409: 다음 상태 확인에서 원 요청을 추적합니다.") };
        }
        return { ...state, busy: false, requestId: null, notice: notice("danger", action.reason || "요청이 거부되었습니다.") };
      case "status": return applyStatus(state, action.record);
      default: return state;
    }
  }
  function canSubmit(state) {
    if (state.busy) return bad("전송 중입니다…");
    if (state.conflictWait) return bad("409로 거절된 요청의 원 요청 상태를 기다리고 있습니다.");
    if (!state.candidate) return bad("지도에서 목표 지점을 먼저 선택하세요.");
    if (state.context.mapGeometryHint && state.context.map && state.context.mapGeometryHint !== state.context.map.geometryId) return bad("서버 지도 메타데이터가 갱신되었습니다. 지도 새로고침을 기다립니다.");
    if (state.record) {
      if (state.record.status === "unknown") return bad("요청 상태를 확인할 수 없습니다. 에이전트의 확정 상태를 기다리는 동안 새 목표를 보낼 수 없습니다.");
      if (!TERMINAL[state.record.status]) return bad("이전 목표 요청이 " + (LABELS[state.record.status] || state.record.status) + " 상태입니다. can_submit 값과 관계없이 종료를 기다리세요.");
      if (state.record.can_submit === false) return bad("서버가 새 목표 요청을 허용하지 않았습니다(진행 중인 요청 있음).");
    }
    if (foreignLease(state)) return bad("서버에 진행 중이거나 상태 미확인인 목표 요청이 있어 새 목표를 보낼 수 없습니다.");
    const selection = selectionFor(state);
    return selection.ok ? { ok: true, selection } : bad(selection.reason);
  }
  function describeRecord(record) {
    if (!record) return { label: "", progress: null };
    const progress = typeof record.progress === "number" ? Math.max(0, Math.min(100, record.progress)) : null;
    return { label: LABELS[record.status] || "알 수 없는 상태", progress };
  }
  function themeColor(doc, id, fallback) {
    try {
      const node = doc.getElementById(id);
      const view = doc.defaultView || global;
      if (!node || !view || typeof view.getComputedStyle !== "function") return fallback;
      return view.getComputedStyle(node).color || fallback;
    } catch (err) { return fallback; }
  }
  function mount(options) {
    const opts = options || {};
    const doc = opts.document || (typeof document !== "undefined" ? document : null);
    if (!doc) throw new Error("RcGoalPicker: document 가 필요합니다");
    const byId = (id) => doc.getElementById(id);
    const getCanvas = () => (typeof opts.canvas === "function" ? opts.canvas() : byId("map-canvas"));
    const getToken = () => (typeof opts.getControlToken === "function" ? opts.getControlToken() : "");
    const fetchJson = typeof opts.fetchJson === "function" ? opts.fetchJson : null;
    let state = createState();
    function redraw() { if (typeof opts.draw === "function") opts.draw(); }
    function notifyMode() { if (typeof opts.onModeChange === "function") opts.onModeChange(state.mode); }
    function setText(id, value) { const node = byId(id); if (node) node.textContent = value; }
    function commit() { render(); redraw(); }
    function render() {
      const canvas = getCanvas();
      if (canvas) canvas.classList.toggle("goal-mode", state.mode === "goal");
      const goalButton = byId("goal-mode");
      if (goalButton) { goalButton.classList.toggle("active", state.mode === "goal"); goalButton.setAttribute("aria-pressed", state.mode === "goal" ? "true" : "false"); }
      const poseButton = byId("pose-mode");
      if (poseButton) poseButton.disabled = blocked(state);
      const clearButton = byId("goal-clear");
      if (clearButton) {
        clearButton.disabled = !state.candidate || blocked(state);
        clearButton.title = clearButton.disabled && state.candidate ? clearReason(state) : "선택한 목표 지점을 지웁니다.";
      }
      const gate = canSubmit(state);
      const sendButton = byId("goal-send");
      if (sendButton) { sendButton.disabled = !gate.ok; sendButton.setAttribute("aria-disabled", gate.ok ? "false" : "true"); sendButton.title = gate.ok ? "미리보기 메시지를 /api/chat 으로 전송합니다." : gate.reason; }
      const selection = state.candidate ? selectionFor(state) : null;
      setText("goal-preview", !state.candidate ? "지도에서 목표 지점을 선택하세요." : selection && selection.ok ? selection.message
        : "선택한 지점을 전송할 수 없습니다: " + (selection ? selection.reason : "지도 정보 없음"));
      setText("goal-coords", state.candidate ? formatCoordinate(state.candidate.x) + ", " + formatCoordinate(state.candidate.y) + " m" : "–");
      setText("goal-frame", (selection && selection.ok ? selection.frameId : state.context.navigationFrame) || "–"); setText("goal-robot", (selection && selection.ok ? selection.robotId : state.context.agentId) || "–");
      const activeRecord = state.record || state.foreignRecord;
      const described = activeRecord ? describeRecord(activeRecord) : null;
      const progressValue = described ? described.progress : null;
      const wrap = byId("goal-progress-wrap");
      const bar = byId("goal-progress");
      if (wrap && bar) {
        wrap.hidden = progressValue === null;
        if (progressValue !== null) { bar.style.width = progressValue + "%"; bar.setAttribute("aria-valuenow", String(progressValue)); }
      }
      const status = byId("goal-status");
      if (status) {
        let text = state.notice ? state.notice.text : "목표를 선택하지 않았습니다.";
        if (activeRecord) {
          const parts = [described.label];
          if (progressValue !== null) parts.push(progressValue + "%");
          if (activeRecord.reason) parts.push(activeRecord.reason);
          if (state.foreignRecord === activeRecord) parts.unshift("서버 목표 요청");
          text = parts.join(" · ");
        }
        status.textContent = text;
        const tone = state.notice && !activeRecord ? state.notice.tone : "info";
        status.className = "small mt-2 " + (tone === "danger" ? "text-danger" : tone === "success" ? "text-success" : "text-body-secondary");
      }
    }
    function setMode(mode) { state = reduce(state, { type: "mode", mode }); notifyMode(); commit(); }
    function applySnapshot(snapshot) {
      const snapState = (snapshot && snapshot.state) || {};
      const mapMeta = (snapshot && snapshot.localization && snapshot.localization.map) || {};
      state = reduce(state, { type: "context", context: {
        agentId: isSafeIdentifier(snapState.agent_id) ? snapState.agent_id : null,
        navigationFrame: isSafeIdentifier(snapState.navigation_frame) ? snapState.navigation_frame : null,
        mapGeometryHint: isGeometryId(mapMeta.geometry_id) ? mapMeta.geometry_id : null } });
      state = reduce(state, { type: "status", record: snapshot ? snapshot.navigation_request : null });
      commit();
    }
    function applyMap(map) { state = reduce(state, { type: "map", map: map || null }); commit(); }
    function toggleGoalMode() { setMode(state.mode === "goal" ? "view" : "goal"); }
    function selectAt(point) { state = reduce(state, { type: "select", point }); commit(); }
    function toggleInitialPose() {
      if (blocked(state)) { state = { ...state, notice: notice("danger", "목표 요청이 진행 중이거나 상태 미확인입니다. 확정 상태 전에는 초기 위치 모드로 전환할 수 없습니다.") }; render(); return; }
      setMode(state.mode === "initial_pose" ? "view" : "initial_pose");
    }
    function clearSelection() { state = reduce(state, { type: "clear" }); notifyMode(); commit(); }
    async function submit() {
      const gate = canSubmit(state);
      if (!gate.ok) { state = { ...state, notice: notice("danger", gate.reason) }; render(); return false; }
      const requestId = state.requestId && !state.record ? state.requestId : generateRequestId();
      const built = buildChatBody({ message: gate.selection.message, requestId, mapGoal: gate.selection.mapGoal });
      if (!built.ok) { state = { ...state, notice: notice("danger", built.reason) }; render(); return false; }
      state = reduce(state, { type: "submit-start", requestId });
      commit();
      try {
        const result = await fetchJson("/api/chat", getToken(), { method: "POST", body: JSON.stringify(built.body) });
        const data = (result && result.data) || {};
        const status = result ? result.status : 0;
        if (status === 202 && result.ok) state = reduce(state, { type: "submit-accepted", record: data.success ? data.request : null });
        else if (status === 409) state = reduce(state, { type: "submit-error", kind: "conflict", reason: data.error || "이미 진행 중인 목표 요청이 있습니다.", record: data.request });
        else if (status >= 500 || status === 0) state = reduce(state, { type: "submit-error", kind: "retryable", reason: "서버 오류(" + status + ")" });
        else state = reduce(state, { type: "submit-error", kind: "rejected", reason: data.error || "요청이 거부되었습니다(" + status + ")" });
      } catch (err) {
        const code = err && typeof err.status === "number" ? err.status : 0;
        const kind = code >= 400 && code < 500 && code !== 409 ? "rejected" : "retryable";
        state = reduce(state, { type: "submit-error", kind, reason: (err && err.message) || "네트워크 오류" });
      }
      commit();
      return true;
    }
    function drawOverlay(ctx, map, view) {
      const api = mapApi();
      if (!ctx || !map || !state.candidate || !api) return;
      const point = api.worldToCanvas(view, map, state.candidate.x, state.candidate.y);
      const pending = blocked(state);
      ctx.save();
      ctx.strokeStyle = themeColor(doc, pending ? "goal-marker-pending-swatch" : "goal-marker-swatch", pending ? "Highlight" : "CanvasText");
      ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(point.x, point.y, 10, 0, Math.PI * 2); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(point.x - 14, point.y); ctx.lineTo(point.x + 14, point.y);
      ctx.moveTo(point.x, point.y - 14); ctx.lineTo(point.x, point.y + 14); ctx.stroke(); ctx.restore();
    }
    function onPointerDown(ev) {
      const canvas = getCanvas();
      const api = mapApi();
      const map = state.context.map;
      if (state.mode !== "goal" || !map || !canvas || !api) return;
      const rect = canvas.getBoundingClientRect();
      const view = api.layout(canvas.width, canvas.height, map);
      const scaleX = rect.width ? canvas.width / rect.width : 1;
      const scaleY = rect.height ? canvas.height / rect.height : 1;
      if (typeof canvas.focus === "function") canvas.focus();
      selectAt(api.canvasToWorld(view, map, (ev.clientX - rect.left) * scaleX, (ev.clientY - rect.top) * scaleY));
    }
    function onKeyDown(ev) {
      if (state.mode !== "goal") return;
      const map = state.context.map; const api = mapApi();
      if (!map || !api) return;
      if (ev.key === "Escape") { state = reduce(state, { type: "clear" }); notifyMode(); commit(); return; }
      // Enter 는 전송 확인 절차를 건너뛰지 않는다. 전송은 버튼 클릭으로만 한다.
      if (ev.key === "Enter") return;
      const steps = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] };
      const step = steps[ev.key];
      if (!step) return;
      if (typeof ev.preventDefault === "function") ev.preventDefault();
      const base = state.candidate || api.localToWorld(map, (map.width * map.resolution) / 2, (map.height * map.resolution) / 2);
      const local = api.worldToLocal(map, base.x, base.y);
      selectAt(api.localToWorld(map, local.x + step[0] * map.resolution, local.y + step[1] * map.resolution));
    }
    if (byId("goal-mode")) byId("goal-mode").addEventListener("click", toggleGoalMode);
    if (byId("goal-clear")) byId("goal-clear").addEventListener("click", clearSelection);
    if (byId("goal-send")) byId("goal-send").addEventListener("click", () => submit());
    if (byId("pose-mode")) byId("pose-mode").addEventListener("click", toggleInitialPose);
    const canvas = getCanvas();
    if (canvas) { canvas.addEventListener("pointerdown", onPointerDown); canvas.addEventListener("keydown", onKeyDown); }
    commit();
    notifyMode();
    return { applySnapshot, applyMap, toggleGoalMode, toggleInitialPose, clearSelection, selectAt, submit, drawOverlay, render, getState: () => state, isGoalMode: () => state.mode === "goal" };
  }
  const api = {
    STATES, TERMINAL, canonicalCoordinate, canonicalPoint, formatCoordinate, formatCanonicalMessage,
    isSafeIdentifier, isGeometryId, isRequestId, isRecord, resolveSelection, buildChatBody, generateRequestId,
    createState, reduce, canSubmit, describeRecord, mount,
  };
  global.RcGoalPicker = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
