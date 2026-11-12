// 실 백엔드 activity 형태의 표시 계약 테스트용 node 하네스.
// test_cooperation_cleanup_activity_js.py 가 실행한다(브라우저 없이 가짜 document 사용).
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

function act(extra) {
  return Object.assign({
    source: "cleanup_demo",
    run_id: "cleanup-run-former0047-7",
    session_id: "cleanup-run-former0047-7",
    robot_id: "former0047",
    task_id: "cleanup-task-7",
    goal: "거실 정리 시연",
    phase: "demo_motion",
    phase_label: "시연 모션 요청",
    place: "거실",
    target: "테이블 위 컵",
    peer: "cloid01",
    transport: "local",
    status: "motion_requested",
    task_status: "",
    result: "정리 시연 모션 요청을 전달했습니다. 실제 정리와 모션 완료는 확인되지 않았습니다.",
    failure_reason: "",
    observed: false,
    physical_cleanup_confirmed: false,
    motion_completion_confirmed: false,
    active: true,
    started_at: 1700000000,
    updated_at: 1700000010,
    finished_at: 0,
    duration_ms: 10000,
  }, extra || {});
}

function snap(extra) {
  const base = {
    available: true,
    enabled: true,
    active: true,
    activity_source: "cleanup_demo",
    direct_cooperation_enabled: false,
    local_robot_id: "former0047",
    session_id: "cleanup-run-former0047-7",
    goal: "거실 정리 시연",
    stream_id: "stream-activity",
    activity: act(),
    events: [],
    tasks: [],
    next_cursor: 0,
    oldest_cursor: 0,
    dropped_events: 0,
    persistence: { enabled: false, error: null },
  };
  return Object.assign(base, extra || {});
}

const activityState = coop.reduceSnapshot(coop.emptyState(0), snap(), 1000);
out.activity = {
  source: activityState.activity.source,
  runId: activityState.activity.runId,
  sessionId: activityState.activity.sessionId,
  taskId: activityState.activity.taskId,
  phase: activityState.activity.phase,
  phaseLabel: activityState.activity.phaseLabel,
  peer: activityState.activity.peer,
  status: activityState.activity.status,
  active: activityState.activity.active,
  observed: activityState.activity.observed,
  physical: activityState.activity.physicalCleanupConfirmed,
  motion: activityState.activity.motionCompletionConfirmed,
  updatedAt: activityState.activity.updatedAt,
  durationMs: activityState.activity.durationMs,
};
out.activityAbsent = {
  missing: coop.normalizeCooperation({ available: true, enabled: true, events: [], tasks: [] }).activity,
  malformed: coop.normalizeCooperation(snap({ activity: "nope" })).activity,
};
const bounded = coop.normalizeCooperation(snap({
  activity: act({ goal: "x".repeat(5000), place: "y".repeat(500), secret_note: "drop" }),
})).activity;
out.bounds = {
  goalLength: bounded.goal.length,
  placeLength: bounded.place.length,
  hasExtra: Object.prototype.hasOwnProperty.call(bounded, "secret_note"),
};
const coerced = coop.normalizeCooperation(snap({
  activity: act({ observed: "yes", active: 1, physical_cleanup_confirmed: false, motion_completion_confirmed: true }),
})).activity;
out.booleans = {
  observed: coerced.observed,
  active: coerced.active,
  physical: coerced.physicalCleanupConfirmed,
  motion: coerced.motionCompletionConfirmed,
};
let afterAbsent = coop.reduceSnapshot(coop.emptyState(0), snap(), 1000);
afterAbsent = coop.reduceSnapshot(afterAbsent, snap({ stream_id: "stream-2", activity: null }), 1000);
let afterUnavailable = coop.reduceSnapshot(coop.emptyState(0), snap(), 1000);
afterUnavailable = coop.reduceSnapshot(afterUnavailable, null, 1000);
out.reduced = {
  afterAbsent: afterAbsent.activity,
  unavailableActivity: afterUnavailable.activity,
  unavailableTasks: afterUnavailable.tasks.length,
  unavailableAvailable: afterUnavailable.available,
};

(async function main() {
  const made = makeDoc([
    "cooperation-panel", "cooperation-summary", "cooperation-notice", "cooperation-sessions",
    "cooperation-filter", "cooperation-cards", "cooperation-timeline", "cooperation-tasks",
    "cooperation-gap", "cooperation-export", "cooperation-fullscreen",
  ]);
  const doc = made.doc, nodes = made.nodes;
  let clock = 3000000;
  const originalNow = Date.now;
  Date.now = () => clock;
  const fetchCalls = [];
  async function fakeFetch(url) { fetchCalls.push(String(url)); return { ok: true, status: 200, blob: async () => ({ size: 1 }) }; }
  const controller = coop.mount({ document: doc, getToken: () => "t", fetchImpl: fakeFetch });
  const apply = (cooperation) => controller.applyStatus({ state: { cooperation: cooperation } });
  const summary = () => textOf(nodes["cooperation-summary"]);
  const tasksText = () => textOf(nodes["cooperation-tasks"]);
  const timelineText = () => textOf(nodes["cooperation-timeline"]);

  // 1. activity + 빈 journal tasks: 현재 단계/동료/상태가 요약과 태스크 목록에 보인다.
  apply(snap({
    events: [
      {
        sequence: 1, event_type: "cleanup_demo_motion_requested", timestamp: 1700000010, robot_id: "former0047",
        peer_id: "cloid01", session_id: "cleanup-run-former0047-7", task_id: "cleanup-task-7",
        message_id: "", reply_to: "", status: "motion_requested", observed: false, content: "",
        details: { source: "cleanup_demo", phase: "demo_motion", phase_label: "시연 모션 요청", place: "거실", target: "cloid01", request_id: "demo-req-7", observed: false },
      },
    ],
    tasks: [{ task_id: "cleanup-task-7", session_id: "cleanup-run-former0047-7", peer_id: "cloid01", description: "거실 정리 시연 · 거실 · 테이블 위 컵", status: "motion_requested", observed: false, source: "local_activity", projected: true, activity_source: "cleanup_demo", physical_cleanup_confirmed: false, motion_completion_confirmed: false, updated_at: 1700000010 }],
  }));
  out.current = {
    summary: summary(),
    tasks: tasksText(),
    timeline: timelineText(),
    notice: textOf(nodes["cooperation-notice"]),
  };

  // 1-1. 세션 필터가 activity 투영 태스크에도 동작한다.
  const selectSession = (value) => {
    nodes["cooperation-sessions"].value = value;
    nodes["cooperation-sessions"].dispatch("change", { target: nodes["cooperation-sessions"] });
  };
  selectSession("cleanup-run-former0047-7");
  out.sessionScoped = tasksText();
  selectSession("coop-session-other");
  out.sessionScopedOther = tasksText();
  selectSession("");

  // 2. 원래 agent 저널 태스크(투영 아님)는 로컬 활동 라벨을 붙이지 않는다.
  apply(snap({
    stream_id: "stream-journal",
    activity: null,
    events: [],
    tasks: [{ task_id: "task-9", session_id: "coop-session", peer_id: "cloid01", description: "원격 위임 태스크", status: "running" }],
  }));
  out.journalTask = { tasks: tasksText(), summary: summary() };

  // 3. phase_label 이 없으면 실제 토큰 fallback 으로 표시한다.
  apply(snap({ stream_id: "stream-token-1", activity: act({ phase: "demo_reobserve", phase_label: "" }) }));
  out.phaseTokenDemo = summary();
  apply(snap({ stream_id: "stream-token-2", activity: act({ phase: "wait_peer", phase_label: "", status: "waiting_for_peer" }) }));
  out.phaseTokenWait = summary();
  apply(snap({ stream_id: "stream-token-3", activity: act({ phase: "resolve_place", phase_label: "", status: "running" }) }));
  out.phaseTokenStart = summary();

  // 4. 이벤트 phase_label 한국어 우선, 토큰 fallback.
  apply(snap({
    stream_id: "stream-events",
    activity: null,
    events: [
      { sequence: 1, event_type: "cleanup_demo_phase", timestamp: 1700000020, robot_id: "former0047", peer_id: "cloid01", session_id: "cleanup-run-former0047-7", task_id: "", message_id: "", reply_to: "", status: "", observed: false, content: "", details: { phase: "demo_motion", phase_label: "시연 모션 요청" } },
      { sequence: 2, event_type: "cleanup_phase", timestamp: 1700000021, robot_id: "former0047", peer_id: "", session_id: "cleanup-run-former0047-7", task_id: "", message_id: "", reply_to: "", status: "", observed: false, content: "", details: { phase: "resolve_place" } },
    ],
  }));
  out.events = timelineText();

  // 5. waiting_for_peer 는 실제 active 대기일 때만 표시한다.
  apply(snap({ stream_id: "stream-wait-off", activity: act({ status: "waiting_for_peer", active: false, phase: "peer_result", phase_label: "" }) }));
  out.waitingInactive = summary();
  apply(snap({ stream_id: "stream-wait-on", activity: act({ status: "waiting_for_peer", active: true, phase: "wait_peer", phase_label: "" }) }));
  out.waitingActive = summary();

  // 5-1. 라이브 activity 의 source 가 스냅샷 메타데이터보다 우선한다.
  apply(snap({ stream_id: "stream-live-source", activity_source: "cleanup_demo", activity: act({ source: "tidy_home", phase: "observe", phase_label: "" }) }));
  out.liveSource = summary();

  // A real live cooperation session remains primary during local cleanup.
  apply(snap({
    stream_id: "stream-concurrent-cooperation", active: true,
    direct_cooperation_enabled: true, activity_source: "autonomous_cooperate",
    goal: "동료와 협동", session_id: "cooperation-session",
    activity: act({ source: "tidy_home", phase: "navigate", phase_label: "이동", status: "running", task_status: "", goal: "거실 정리", active: true }),
  }));
  out.liveCooperation = summary();

  // The HTTP payload has a generic ended status and a specific task outcome.
  apply(snap({
    stream_id: "stream-specific-task-status", active: false,
    activity: act({ active: false, status: "unknown", task_status: "motion_requested", phase: "peer_result" }),
  }));
  out.statusPriority = summary();

  // 6. Maestro 비활성/빈 스냅샷은 이전 activity/task/goal 을 되살리지 않는다.
  apply(snap({ stream_id: "stream-off", enabled: false, active: false, goal: "", session_id: "", activity_source: "", activity: null, events: [], tasks: [] }));
  out.disabled = { summary: summary(), tasks: tasksText(), notice: textOf(nodes["cooperation-notice"]) };

  out.fetchCalls = fetchCalls.length;
  Date.now = originalNow;
  console.log(JSON.stringify(out));
})().catch((err) => {
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
});
