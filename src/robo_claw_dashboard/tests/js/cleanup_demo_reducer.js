// 로컬 정리/시연 표시 계약 테스트용 node 하네스 — test_cooperation_cleanup_demo.py 가 실행한다.
// cooperation.js 의 순수 리듀서/라벨/필터/정규화만 검증한다(브라우저/DOM 없음).
const coop = require(process.argv[1]);
const out = {};
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
    goal: "거실 정리 (마지막 정리 실행)",
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
const CLEANUP_TYPES = ["cleanup_run_started", "cleanup_phase", "cleanup_run_finished", "cleanup_handoff",
  "cleanup_delegate_started", "cleanup_delegate_finished"];
const DEMO_TYPES = ["cleanup_demo_started", "cleanup_demo_phase", "cleanup_demo_motion_requested",
  "cleanup_demo_failed", "cleanup_demo_finished"];
const allTypes = CLEANUP_TYPES.concat(DEMO_TYPES);
const events = allTypes.map((type, index) => ev(index + 1, { event_type: type }));
const state = coop.reduceSnapshot(coop.emptyState(0), snap(events), 1000);

const statusCase = (status) => ({ label: coop.formatStatusLabel(status), tone: coop.statusTone(status) });
out.statuses = {
  motionRequested: statusCase("motion_requested"),
  waitingForPeer: statusCase("waiting_for_peer"),
  cancelRequested: statusCase("cancel_requested"),
  reportedToMaestro: statusCase("reported_to_maestro"),
  motionRequestedObserved: statusCase("motion_requested"),
};
out.normalizedStatus = {
  motionRequested: coop.normalizeCooperation(snap([ev(1, { event_type: "cleanup_demo_motion_requested", status: "MOTION_REQUESTED" })])).events[0].status,
  waitingForPeer: coop.normalizeCooperation(snap([ev(1, { status: "waiting_for_peer" })])).events[0].status,
  cancelRequested: coop.normalizeCooperation(snap([ev(1, { status: "cancel_requested" })])).events[0].status,
  reportedToMaestro: coop.normalizeCooperation(snap([ev(1, { status: "reported_to_maestro" })])).events[0].status,
};

out.activity = {
  source: state.activitySource,
  direct: state.directCooperationEnabled,
  enabled: state.enabled,
  active: state.active,
  goal: state.goal,
  sessionId: state.sessionId,
};
const legacy = coop.normalizeCooperation({ available: true, enabled: true, active: true, events: [], tasks: [] });
out.legacyActivity = { source: legacy.activitySource, direct: legacy.directCooperationEnabled };
const odd = coop.normalizeCooperation(snap([], { activity_source: "REMOTE", direct_cooperation_enabled: "yes" }));
out.oddActivity = { source: odd.activitySource, direct: odd.directCooperationEnabled };
const demoSource = coop.normalizeCooperation(snap([], { activity_source: "CLEANUP_DEMO" }));
out.demoActivity = { source: demoSource.activitySource };

out.filters = {
  all: coop.selectEvents(state, {}).length,
  cleanup: coop.selectEvents(state, { filterType: "cleanup" }).map((item) => item.eventType),
  demo: coop.selectEvents(state, { filterType: "demo" }).map((item) => item.eventType),
  tasks: coop.selectEvents(state, { filterType: "tasks" }).map((item) => item.eventType),
  messages: coop.selectEvents(state, { filterType: "messages" }).map((item) => item.eventType),
};

out.labels = {};
for (const type of allTypes) out.labels[type] = coop.formatEventLabel(type);

out.phases = {
  place: coop.phaseLabel("place_resolution"),
  navigation: coop.phaseLabel("navigation"),
  observation: coop.phaseLabel("observation"),
  target: coop.phaseLabel("target_selection"),
  peer: coop.phaseLabel("peer_selection"),
  staging: coop.phaseLabel("staging"),
  waiting: coop.phaseLabel("waiting_for_peer"),
  response: coop.phaseLabel("response"),
  reobservation: coop.phaseLabel("reobservation"),
  motionRequest: coop.phaseLabel("motion_request"),
  korean: coop.phaseLabel("거실 관측 중"),
  blank: coop.phaseLabel(""),
};

const kept = coop.normalizeEvent(ev(2, {
  event_type: "cleanup_demo_motion_requested",
  status: "motion_requested",
  observed: false,
  details: {
    source: "cleanup_demo",
    phase: "motion_request",
    place: "거실",
    target: "cloid01",
    transport: "local",
    request_id: "demo-req-7",
    observed: false,
    duration_ms: 1200,
    physical_cleanup_confirmed: false,
    motion_completion_confirmed: false,
  },
}));
out.provenance = { status: kept.status, observed: kept.observed, details: kept.details };

const finished = coop.reduceSnapshot(coop.emptyState(0), snap([ev(1, { event_type: "cleanup_demo_finished", status: "" })]), 1000);
out.finished = { active: finished.active, goal: finished.goal, sessionId: finished.sessionId, events: finished.events.length };

console.log(JSON.stringify(out));
