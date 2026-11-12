// 로컬 정리/시연 타임라인 표시 계약 테스트용 node 하네스 — test_cooperation_cleanup_demo_dom.py 가 실행한다.
// 가짜 document 로 cooperation.js mount 결과를 검증한다(추가 HTTP/타이머 없음).
const coop = require(process.argv[1]);
const out = {};

function makeDoc(ids) {
  const registry = {};
  function element(tag) {
    const node = {
      tagName: tag,
      className: "",
      dataset: {},
      children: [],
      listeners: {},
      attributes: {},
      disabled: false,
      hidden: false,
      value: "",
      appendChild(child) { this.children.push(child); return child; },
      remove() {},
      click() { this.dispatch("click", {}); },
      addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); },
      dispatch(name, event) { for (const fn of this.listeners[name] || []) fn(event || {}); },
      setAttribute(name, value) { this.attributes[name] = value; },
      focus() {},
    };
    Object.defineProperty(node, "textContent", {
      get() { return node.__text || ""; },
      set(value) { node.__text = value == null ? "" : String(value); node.children.length = 0; },
    });
    Object.defineProperty(node, "innerHTML", {
      get() { return ""; },
      set() { throw new Error("innerHTML 사용 금지"); },
    });
    return node;
  }
  const doc = {
    body: element("body"),
    documentElement: element("html"),
    fullscreenElement: null,
    createElement: element,
    createDocumentFragment() { return element("#fragment"); },
    getElementById(id) { return registry[id] || null; },
  };
  for (const id of ids) {
    registry[id] = element(id === "cooperation-sessions" || id === "cooperation-filter" ? "select" : "div");
    registry[id].id = id;
  }
  return { doc: doc, nodes: registry };
}

function textOf(node) {
  if (!node) return "";
  let text = node.__text || "";
  for (const child of node.children) text += " " + textOf(child);
  return text.replace(/\s+/g, " ").trim();
}

function items(node) {
  return ((node && node.children) || []).map((child) => {
    const chips = [];
    (function walk(current) {
      for (const inner of current.children || []) {
        if (String(inner.className || "").indexOf("rc-coop-status ") === 0) {
          chips.push({ cls: inner.className, text: textOf(inner) });
        }
        walk(inner);
      }
    })(child);
    return { text: textOf(child), chips: chips, label: (child.attributes || {})["aria-label"] || "" };
  });
}

function badges(node) {
  const found = [];
  (function walk(current) {
    for (const child of current.children || []) {
      if (String(child.className || "").indexOf("rc-coop-badge") === 0) found.push({ cls: child.className, text: textOf(child) });
      walk(child);
    }
  })(node);
  return found;
}

function ev(sequence, extra) {
  const base = {
    sequence: sequence,
    event_type: "cleanup_phase",
    timestamp: 1700000000 + sequence,
    robot_id: "former0047",
    peer_id: "",
    session_id: "cleanup-run-former0047-1",
    task_id: "cleanup-task-1",
    message_id: "",
    reply_to: "",
    status: "",
    content: "",
    details: {},
  };
  return Object.assign(base, extra || {});
}

function snap(events, extra) {
  const base = {
    available: true,
    enabled: true,
    active: false,
    local_robot_id: "former0047",
    session_id: "cleanup-run-former0047-1",
    goal: "거실 정리",
    stream_id: "stream-cleanup",
    activity_source: "tidy_home",
    direct_cooperation_enabled: false,
    events: events || [],
    tasks: [],
    next_cursor: 0,
    oldest_cursor: 0,
    dropped_events: 0,
    persistence: { enabled: false, error: null },
  };
  return Object.assign(base, extra || {});
}

(async function main() {
  const made = makeDoc([
    "cooperation-panel", "cooperation-summary", "cooperation-notice", "cooperation-sessions",
    "cooperation-filter", "cooperation-cards", "cooperation-timeline", "cooperation-tasks",
    "cooperation-gap", "cooperation-export", "cooperation-fullscreen",
  ]);
  const doc = made.doc, nodes = made.nodes;
  let clock = 2000000;
  const originalNow = Date.now;
  Date.now = () => clock;
  const realURL = globalThis.URL;
  globalThis.URL = { createObjectURL: () => "blob:mock", revokeObjectURL() {} };
  const fetchCalls = [];
  async function fakeFetch(url, options) {
    fetchCalls.push({ url: String(url), headers: (options || {}).headers || {} });
    return { ok: true, status: 200, blob: async () => ({ size: 2048 }) };
  }
  const controller = coop.mount({ document: doc, getToken: () => "r-secret", fetchImpl: fakeFetch });
  const apply = (cooperation) => controller.applyStatus({ state: { cooperation: cooperation } });
  const timeline = () => nodes["cooperation-timeline"];
  const filterOptions = () => nodes["cooperation-filter"].children.map((child) => textOf(child));

  // 1. 동료 응답 대기: 요청자(former0047) 관점의 자기 단계 + 거실 + 동료 cloid01
  apply(snap([
    ev(1, { event_type: "cleanup_run_started", details: { source: "local_cleanup", phase: "place_resolution", place: "거실" } }),
    ev(2, { details: { source: "local_cleanup", phase: "navigation", place: "거실" } }),
    ev(3, { details: { source: "local_cleanup", phase: "observation", place: "거실" } }),
    ev(4, { event_type: "cleanup_handoff", details: { source: "local_cleanup", phase: "peer_selection", target: "cloid01", place: "거실" } }),
    ev(5, {
      event_type: "cleanup_delegate_started",
      status: "waiting_for_peer",
      peer_id: "cloid01",
      details: { source: "local_cleanup", phase: "waiting_for_peer", target: "cloid01", transport: "local", request_id: "demo-req-7" },
    }),
  ], { stream_id: "stream-waiting" }));
  out.waiting = {
    text: textOf(timeline()),
    items: items(timeline()),
    notice: textOf(nodes["cooperation-notice"]),
    summary: textOf(nodes["cooperation-summary"]),
    labels: items(timeline()).map((item) => item.label),
  };

  // 2. 모션 요청 응답(동료 보고) 수신 후에도 실제 완료를 단정하지 않는다.
  apply(snap([
    ev(1, {
      event_type: "cleanup_delegate_finished",
      status: "waiting_for_peer",
      peer_id: "cloid01",
      observed: false,
      details: { source: "local_cleanup", phase: "response", target: "cloid01", request_id: "demo-req-7", observed: false },
    }),
    ev(2, {
      event_type: "cleanup_demo_motion_requested",
      status: "motion_requested",
      peer_id: "cloid01",
      observed: true,
      details: {
        source: "cleanup_demo", phase: "motion_request", place: "거실", target: "cloid01",
        request_id: "demo-req-7", observed: true,
        physical_cleanup_confirmed: false, motion_completion_confirmed: false,
      },
    }),
  ], { stream_id: "stream-response", activity_source: "cleanup_demo" }));
  out.responded = {
    text: textOf(timeline()),
    items: items(timeline()),
    notice: textOf(nodes["cooperation-notice"]),
    summary: textOf(nodes["cooperation-summary"]),
  };

  // 3. 취소 요청 / Maestro 보고 상태
  apply(snap([
    ev(1, {
      event_type: "cleanup_phase",
      status: "cancel_requested",
      details: { source: "local_cleanup", phase: "failure", failure_reason: "동료 응답 시간 초과" },
    }),
  ], { stream_id: "stream-cancel" }));
  out.cancel = { text: textOf(timeline()), items: items(timeline()) };

  apply(snap([
    ev(1, { event_type: "cleanup_run_finished", status: "reported_to_maestro", details: { source: "local_cleanup", phase: "response" } }),
  ], { stream_id: "stream-reported" }));
  out.reported = { text: textOf(timeline()), items: items(timeline()) };

  // 4. 로컬 정리 관측 + Maestro 직접 협동 비활성(enabled=true, direct=false)
  apply(snap([
    ev(1, {
      event_type: "cleanup_demo_phase",
      details: { source: "cleanup_demo", phase: "reobservation", place: "거실", device_token: "r-secret" },
    }),
  ], {
    stream_id: "stream-local-demo",
    activity_source: "cleanup_demo",
    direct_cooperation_enabled: false,
    session_id: "cleanup-run-former0047-2",
    goal: "거실 정리 시연 (former0047 → cloid01)",
  }));
  out.localOnly = {
    notice: textOf(nodes["cooperation-notice"]),
    summary: textOf(nodes["cooperation-summary"]),
    badges: badges(nodes["cooperation-summary"]),
    text: textOf(timeline()),
  };

  // 5. 구버전 Maestro 비활성 스냅샷은 표시가 그대로다.
  apply({
    available: true, enabled: false, active: true, local_robot_id: "former0047",
    session_id: "sess-old", goal: "patrol the corridor", stream_id: "stream-old-off",
    events: [ev(1, { status: "received", event_type: "message_received", peer_id: "cloid01", session_id: "sess-old" })],
    tasks: [], next_cursor: 1, oldest_cursor: 1, dropped_events: 0,
    persistence: { enabled: false, error: null },
  });
  out.oldDisabled = {
    notice: textOf(nodes["cooperation-notice"]),
    summary: textOf(nodes["cooperation-summary"]),
    badges: badges(nodes["cooperation-summary"]),
  };

  // 6. 구버전 정상 활성 스냅샷(activity_source 없음)도 그대로 표시된다.
  const oldEnabledSnap = snap([ev(1, { event_type: "task_started", status: "running", peer_id: "cloid01" })], {
    stream_id: "stream-old-on", active: true, goal: "patrol the corridor",
  });
  delete oldEnabledSnap.activity_source;
  delete oldEnabledSnap.direct_cooperation_enabled;
  apply(oldEnabledSnap);
  out.oldEnabled = {
    summary: textOf(nodes["cooperation-summary"]),
    badges: badges(nodes["cooperation-summary"]),
    notice: textOf(nodes["cooperation-notice"]),
  };

  // 7. 정리 실행 종료: active=false 이지만 마지막 목표/세션/결과는 남는다.
  apply(snap([
    ev(1, { event_type: "cleanup_demo_started", details: { phase: "place_resolution", place: "거실" } }),
    ev(2, { event_type: "cleanup_demo_finished", details: { duration_ms: 4200 } }),
  ], { stream_id: "stream-finished", activity_source: "cleanup_demo", active: false }));
  out.finished = {
    summary: textOf(nodes["cooperation-summary"]),
    badges: badges(nodes["cooperation-summary"]),
    text: textOf(timeline()),
  };

  // 8. 기본 타임라인/유형 필터
  apply(snap([
    ev(1, { event_type: "message_received", status: "received", peer_id: "cloid01" }),
    ev(2, { event_type: "cleanup_phase", details: { phase: "staging", place: "거실" } }),
    ev(3, { event_type: "cleanup_demo_phase", details: { phase: "observation", place: "거실" } }),
    ev(4, { event_type: "task_started", status: "running", peer_id: "cloid01" }),
    ev(5, { event_type: "cleanup_delegate_started", status: "waiting_for_peer", peer_id: "cloid01", details: { phase: "waiting_for_peer" } }),
  ], { stream_id: "stream-filter" }));
  const counts = {};
  const selectFilter = (value) => {
    nodes["cooperation-filter"].value = value;
    nodes["cooperation-filter"].dispatch("change", { target: nodes["cooperation-filter"] });
    counts[value || "all"] = timeline().children.length;
  };
  selectFilter("");
  selectFilter("messages");
  selectFilter("tasks");
  selectFilter("demo");
  selectFilter("cleanup");
  selectFilter("");
  out.filters = { counts: counts, options: filterOptions(), fetchCalls: fetchCalls.length };

  out.fetchAfterSnapshots = fetchCalls.length;
  await controller.download();
  out.exportCall = fetchCalls[fetchCalls.length - 1];

  Date.now = originalNow;
  globalThis.URL = realURL;
  console.log(JSON.stringify(out));
})().catch((err) => {
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
});
