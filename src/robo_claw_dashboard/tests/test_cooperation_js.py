"""협동 패널 JS 회귀 테스트 — cooperation.js 순수 리듀서와 DOM 컨트롤러를 node 로 검증한다.

계약:
- ``/api/status`` 스냅샷(state.cooperation)만 소비하고 스냅샷 렌더에 HTTP 를 추가하지 않는다.
- stream_id+sequence 로 중복을 제거하고, 새 stream 이면 초기화하며, 유실 구간을 표시한다.
- 화면 표시는 textContent 로만 하고 innerHTML 을 쓰지 않는다.
node 가 없으면 건너뛴다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.component

ASSETS = Path(__file__).resolve().parents[1] / "web" / "assets"
COOPERATION_JS = ASSETS / "cooperation.js"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 실행 파일이 없어 JS 협동 패널 테스트를 건너뜁니다")
    return node


def _run(script: str) -> dict:
    result = subprocess.run(
        [_node(), "-e", script, "--", str(COOPERATION_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


REDUCER_SCRIPT = """
const coop = require(process.argv[1]);
const out = {};

function ev(sequence, extra) {
  const base = {
    sequence: sequence,
    event_type: "message_received",
    timestamp: 1700000000 + sequence,
    robot_id: "robot-b",
    peer_id: "robot-b",
    session_id: "sess-1",
    task_id: "task-" + sequence,
    message_id: "msg-" + sequence,
    reply_to: "",
    status: "received",
    content: "content " + sequence,
    details: {},
  };
  return Object.assign(base, extra || {});
}

function snap(events, extra) {
  const base = {
    available: true,
    enabled: true,
    active: true,
    local_robot_id: "robot-a",
    session_id: "sess-1",
    goal: "patrol the corridor",
    stream_id: "stream-1",
    events: events || [],
    tasks: [],
    next_cursor: 0,
    oldest_cursor: 0,
    dropped_events: 0,
    persistence: { enabled: false, error: null },
  };
  return Object.assign(base, extra || {});
}

function reduceAll(raws) {
  let state = coop.emptyState(0);
  for (const raw of raws) state = coop.reduceSnapshot(state, raw, 1000);
  return state;
}

// 1. 결측/기형 계약
const missing = coop.normalizeCooperation(null);
out.missing = {
  available: missing.available,
  enabled: missing.enabled,
  active: missing.active,
  eventCount: missing.events.length,
  hasError: Boolean(missing.error),
};
const malformed = coop.normalizeCooperation({
  available: "yes",
  events: "nope",
  tasks: 5,
  stream_id: 12,
});
out.malformed = {
  available: malformed.available,
  eventCount: malformed.events.length,
  taskCount: malformed.tasks.length,
  hasError: Boolean(malformed.error),
};
const disabled = coop.normalizeCooperation(snap([], { enabled: false, active: true }));
out.disabled = { available: disabled.available, enabled: disabled.enabled, active: disabled.active };

// 2. 중복/순서 뒤바뀜 제거
const dup = reduceAll([snap([ev(1), ev(2), ev(1)])]);
out.duplicate = { sequences: dup.events.map((item) => item.sequence), count: dup.events.length };
const outOfOrder = reduceAll([snap([ev(3)]), snap([ev(1)]), snap([ev(2), ev(3)])]);
out.outOfOrder = outOfOrder.events.map((item) => item.sequence);
out.keysAreUnique = new Set(outOfOrder.events.map((item) => item.key)).size === outOfOrder.events.length;

// 3. 새 stream 초기화
const reset = reduceAll([snap([ev(1), ev(2)]), snap([ev(1, { robot_id: "robot-a" })], { stream_id: "stream-2" })]);
out.reset = {
  streamId: reset.streamId,
  resets: reset.resets,
  sequences: reset.events.map((item) => item.sequence),
};

// 4. 상한: 스냅샷 1건 스캔 상한과 로컬 캐시/렌더 상한
const batch = (start, count) => {
  const list = [];
  for (let i = start; i < start + count; i += 1) list.push(ev(i));
  return list;
};
const scanned = reduceAll([snap(batch(1, 500), { oldest_cursor: 1, next_cursor: 501 })]);
const capped = reduceAll([
  snap(batch(1, 100), { oldest_cursor: 1 }), snap(batch(101, 100), { oldest_cursor: 1 }),
  snap(batch(201, 100), { oldest_cursor: 1 }), snap(batch(301, 100), { oldest_cursor: 1 }),
  snap(batch(401, 100), { oldest_cursor: 1 }), snap(batch(501, 100), { oldest_cursor: 1 }),
]);
out.scan = { scanned: scanned.events.length, maxScanned: coop.MAX_SCAN_EVENTS };
out.cap = {
  cached: capped.events.length,
  firstCached: capped.events[0].sequence,
  rendered: coop.selectEvents(capped, {}).length,
  maxRendered: coop.MAX_RENDERED_EVENTS,
  maxCached: coop.MAX_CACHED_EVENTS,
};

// 5. 유실 구간
const gap = reduceAll([
  snap([ev(1), ev(2)], { oldest_cursor: 1, next_cursor: 3 }),
  snap([ev(10), ev(11)], { oldest_cursor: 10, next_cursor: 12 }),
]);
out.gap = gap.gaps;
const dropped = reduceAll([
  snap([ev(1)], { dropped_events: 0 }),
  snap([ev(2)], { dropped_events: 4 }),
]);
out.droppedGap = dropped.gaps;

// 6. 표시 문자열/톤/방향
out.statusLabels = {
  queued: coop.formatStatusLabel("queued"),
  completed: coop.formatStatusLabel("completed"),
  unknown: coop.formatStatusLabel("exploded"),
  queuedTone: coop.statusTone("queued"),
  deniedTone: coop.statusTone("denied"),
  completedTone: coop.statusTone("completed"),
};
out.labels = {
  sessionStopped: coop.formatEventLabel("session_stopped"),
  taskDenied: coop.formatEventLabel("task_denied"),
  unknownType: coop.formatEventLabel("something_new"),
};
out.direction = {
  queued: coop.eventDirection(coop.normalizeEvent(ev(1, { event_type: "message_queued" })), "robot-a"),
  received: coop.eventDirection(coop.normalizeEvent(ev(1, { event_type: "message_received" })), "robot-a"),
  completed: coop.eventDirection(coop.normalizeEvent(ev(1, { event_type: "task_completed" })), "robot-a"),
};

// 7. 세션/필터 선택과 최근 교환 강조
const mixed = reduceAll([
  snap([
    ev(1),
    ev(2, { event_type: "message_queued", session_id: "sess-2" }),
    ev(3, { event_type: "task_started", session_id: "sess-2" }),
    ev(4, { event_type: "message_received" }),
    ev(5, { event_type: "policy" }),
  ]),
]);
out.sessions = coop.selectEvents(mixed, { sessionId: "sess-2" }).map((item) => item.sequence);
out.taskFilter = coop.selectEvents(mixed, { filterType: "tasks" }).map((item) => item.sequence);
out.messageFilter = coop.selectEvents(mixed, { filterType: "messages" }).map((item) => item.sequence);
out.highlight = coop.latestExchangeKeys(mixed, 2);

// 8. 카드 모델
const cards = coop.buildCards(mixed);
out.cards = {
  localId: cards.local.id,
  peerIds: cards.peers.map((peer) => peer.id),
  peerSent: cards.peers.map((peer) => peer.sent),
  peerReceived: cards.peers.map((peer) => peer.received),
};

// 9. 시크릿 마스킹/상한
const sanitized = coop.sanitizeDetails({
  device_token: "abc",
  nested: { http_control_token: "ctrl", ok: 1 },
  note: "keep",
  blob: "z".repeat(5000),
});
out.sanitize = {
  token: sanitized.device_token,
  nestedToken: sanitized.nested.http_control_token,
  nestedOk: sanitized.nested.ok,
  note: sanitized.note,
  blobLength: sanitized.blob.length,
};

// 10. 쿼리/파일명
out.query = {
  full: coop.buildCooperationQuery({ after: 4, streamId: "stream-1", limit: 50 }),
  minimal: coop.buildCooperationQuery({}),
  nulls: coop.buildCooperationQuery({ after: null, streamId: "", limit: null }),
};
out.fileName = coop.exportFileName(new Date(2026, 9, 23, 12, 5, 9));

// 11. 신선도
let state = coop.reduceSnapshot(coop.emptyState(0), snap([ev(1)]), 1000);
out.fresh = coop.isStale(state, 1000 + coop.STALE_AFTER_MS);
out.stale = coop.isStale(state, 1000 + coop.STALE_AFTER_MS + 1);


// 12. 상태 라벨: 로컬/관측/모호 구분, delivery 확인
out.statusVariants = {
  received: coop.formatStatusLabel("received"),
  running: coop.formatStatusLabel("running"),
  queued: coop.formatStatusLabel("queued"),
  queuedDelivered: coop.formatStatusLabel("queued", { deliveryConfirmed: true }),
  blank: coop.formatStatusLabel(""),
  completedObserved: coop.formatStatusLabel("completed", { observed: true }),
  failedObserved: coop.formatStatusLabel("failed", { observed: true }),
  deniedObserved: coop.formatStatusLabel("denied", { observed: true }),
  partialObserved: coop.formatStatusLabel("partial", { observed: true }),
  completedLocal: coop.formatStatusLabel("completed", { observed: false }),
  completedAmbiguous: coop.formatStatusLabel("completed"),
  unknownLocal: coop.formatStatusLabel("exploded"),
};
out.statusTones = {
  queuedDelivered: coop.statusTone("queued", { deliveryConfirmed: true }),
  completedAmbiguous: coop.statusTone("completed"),
  deniedAmbiguous: coop.statusTone("denied"),
  deniedObserved: coop.statusTone("denied", { observed: true }),
  blank: coop.statusTone(""),
};

// 13. 관측/전달 메타데이터 보존
const metaJournal = coop.normalizeCooperation(
  snap(
    [
      ev(1, { event_type: "task_completed", status: "completed", observed: true }),
      ev(2, { event_type: "task_completed", status: "completed" }),
      ev(3, { event_type: "message_queued", status: "queued", delivery_confirmed: true, outbound_message_id: "m-out" }),
      ev(4, { event_type: "session_started", status: "", details: { state: "active" } }),
    ],
    {
      tasks: [
        {
          task_id: "t1",
          session_id: "sess-1",
          peer_id: "robot-b",
          status: "queued",
          description: "scan",
          observed: true,
          delivery_confirmed: true,
          outbound_message_id: "m-out",
          internal_notes: "nope",
        },
      ],
    }
  )
);
out.metadata = {
  observed: metaJournal.events.map((item) => item.observed),
  deliveryQueued: metaJournal.events[2].deliveryConfirmed,
  outboundId: metaJournal.events[2].outboundMessageId,
  blankStatus: metaJournal.events[3].status,
  task: {
    peerId: metaJournal.tasks[0].peerId,
    sessionId: metaJournal.tasks[0].sessionId,
    observed: metaJournal.tasks[0].observed,
    deliveryConfirmed: metaJournal.tasks[0].deliveryConfirmed,
    outboundId: metaJournal.tasks[0].outboundMessageId,
    hasPrivate: "internal_notes" in metaJournal.tasks[0],
  },
};

// 14. persistence / invalid events 노출
const noPersistence = coop.normalizeCooperation({ available: true, events: [], tasks: [] });
out.persistenceMissing = { reported: noPersistence.persistence.reported, enabled: noPersistence.persistence.enabled };
const persistenceJournal = coop.normalizeCooperation(
  snap([ev(1), { sequence: "bad" }, { event_type: "task_failed" }], {
    persistence: { enabled: false, error: "sqlite3 disk I/O error" },
  })
);
out.persistence = {
  invalidEvents: persistenceJournal.invalidEvents,
  enabled: persistenceJournal.persistence.enabled,
  reported: persistenceJournal.persistence.reported,
  error: persistenceJournal.persistence.error,
};

// 15. dropped 카운터: 서버 유실 + 클라이언트 상한을 각각 누적(중복 합산 금지)
let droppedState = coop.reduceSnapshot(coop.emptyState(0), snap(batch(1, 100), { dropped_events: 0 }), 1000);
droppedState = coop.reduceSnapshot(droppedState, snap(batch(101, 100), { dropped_events: 0 }), 1000);
out.droppedAfterFirst = droppedState.droppedEvents;
droppedState = coop.reduceSnapshot(droppedState, snap(batch(101, 100), { dropped_events: 0 }), 1000);
out.droppedAfterRepeat = droppedState.droppedEvents;
for (const start of [201, 301, 401]) droppedState = coop.reduceSnapshot(droppedState, snap(batch(start, 100), { dropped_events: 0 }), 1000);
out.droppedAfterOverflow = droppedState.droppedEvents;
droppedState = coop.reduceSnapshot(droppedState, snap(batch(501, 100), { dropped_events: 4 }), 1000);
out.droppedAfterSource = droppedState.droppedEvents;

// 16. stream 변경 시 이전 identity/goal fallback 금지, goal empty clearing
let identity = coop.reduceSnapshot(coop.emptyState(0), snap([ev(1)]), 1000);
identity = coop.reduceSnapshot(
  identity,
  snap([ev(1)], { stream_id: "stream-2", local_robot_id: "", session_id: "", goal: "" }),
  1000
);
out.identityAfterStreamChange = {
  streamId: identity.streamId,
  localRobotId: identity.localRobotId,
  sessionId: identity.sessionId,
  goal: identity.goal,
};
let goalCleared = coop.reduceSnapshot(coop.emptyState(0), snap([ev(1)], { goal: "patrol" }), 1000);
goalCleared = coop.reduceSnapshot(goalCleared, snap([ev(2)], { goal: "" }), 1000);
out.goalAfterClear = goalCleared.goal;

// 17. journal 미수신 시 이전 태스크를 현재 상태로 표시하지 않는다
let staleTasks = coop.reduceSnapshot(
  coop.emptyState(0),
  snap([ev(1)], { tasks: [{ task_id: "t1", status: "running" }] }),
  1000
);
out.staleTasksBefore = staleTasks.tasks.length;
staleTasks = coop.reduceSnapshot(staleTasks, null, 1000);
out.staleTasksAfter = { available: staleTasks.available, tasks: staleTasks.tasks.length, events: staleTasks.events.length };

// 18. 기형/거대 배열 상한
const huge = [];
for (let i = 1; i <= 5000; i += 1) huge.push(i % 7 === 0 ? { bad: true } : ev(i));
const hugeState = coop.reduceSnapshot(coop.emptyState(0), snap(huge, { oldest_cursor: 1 }), 1000);
out.huge = {
  scannedInvalid: hugeState.invalidEvents,
  events: hugeState.events.length,
  boundedInvalid: hugeState.invalidEvents <= 200,
};

// 19. 시크릿 강화: auth/authorization/cookie/bearer/privatekey + Bearer inline
const hardened = coop.sanitizeDetails({
  authorization: "Bearer abc",
  cookie: "session=1",
  bearer: "abc",
  privatekey: "BEGIN",
  auth: "basic",
  ok: 1,
});
out.hardened = {
  authorization: hardened.authorization,
  cookie: hardened.cookie,
  bearer: hardened.bearer,
  privatekey: hardened.privatekey,
  auth: hardened.auth,
  ok: hardened.ok,
};
const bearerEvent = coop.normalizeEvent(ev(1, { content: "Authorization: Bearer abc.def.ghi1234" }));
out.bearerContent = bearerEvent.content;

// 20. 태스크 세션 필터/peer 표시
const taskSessionState = coop.reduceSnapshot(
  coop.emptyState(0),
  snap([ev(1)], {
    tasks: [
      { task_id: "t-sess-1", session_id: "sess-1", peer_id: "robot-b", status: "running" },
      { task_id: "t-sess-2", session_id: "sess-2", peer_id: "robot-c", status: "queued" },
      { task_id: "t-unknown", status: "queued" },
    ],
  }),
  1000
);
out.tasksBySession = coop.selectTasks(taskSessionState, { sessionId: "sess-2" }).map((task) => task.taskId);

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def reducer() -> dict:
    return _run(REDUCER_SCRIPT)


class TestMalformedContracts:
    def test_missing_cooperation_is_unavailable(self, reducer):
        assert reducer["missing"]["available"] is False
        assert reducer["missing"]["eventCount"] == 0
        assert reducer["missing"]["hasError"] is True

    def test_malformed_types_do_not_throw(self, reducer):
        data = reducer["malformed"]
        assert data["available"] is False
        assert data["eventCount"] == 0
        assert data["taskCount"] == 0
        assert data["hasError"] is True

    def test_disabled_forces_inactive(self, reducer):
        assert reducer["disabled"] == {"available": True, "enabled": False, "active": False}


class TestReducer:
    def test_duplicate_sequences_are_deduped(self, reducer):
        assert reducer["duplicate"]["sequences"] == [1, 2]

    def test_out_of_order_events_are_sorted(self, reducer):
        assert reducer["outOfOrder"] == [1, 2, 3]
        assert reducer["keysAreUnique"] is True

    def test_new_stream_resets_events(self, reducer):
        data = reducer["reset"]
        assert data["streamId"] == "stream-2"
        assert data["resets"] == 1
        assert data["sequences"] == [1]

    def test_snapshot_scan_is_bounded(self, reducer):
        assert reducer["scan"]["scanned"] == reducer["scan"]["maxScanned"]

    def test_render_is_capped_and_cache_is_bounded(self, reducer):
        data = reducer["cap"]
        assert data["cached"] == data["maxCached"]
        assert data["firstCached"] == 201
        assert data["rendered"] == data["maxRendered"]

    def test_missed_window_is_reported(self, reducer):
        gaps = reducer["gap"]
        assert gaps, "유실 구간이 보고되지 않았습니다"
        assert gaps[-1]["missed"] == 7
        assert gaps[-1]["from"] == 3
        assert gaps[-1]["to"] == 9

    def test_dropped_event_increase_is_reported(self, reducer):
        gaps = reducer["droppedGap"]
        assert gaps, "저장 유실이 보고되지 않았습니다"
        assert gaps[-1]["missed"] == 4


class TestLabels:
    def test_status_labels_state_local_observation(self, reducer):
        labels = reducer["statusLabels"]
        assert "상대 수신 미확인" in labels["queued"]
        assert "알 수 없음" in labels["unknown"]
        assert labels["queuedTone"] == "idle"
        assert labels["deniedTone"] == "idle"

    def test_local_and_observed_status_labels_are_distinct(self, reducer):
        variants = reducer["statusVariants"]
        assert variants["received"] == "로컬 메시지 수신"
        assert variants["running"] == "로컬 실행 중"
        assert "상대 수신 미확인" in variants["queued"]
        assert "상대 회신 확인" in variants["queuedDelivered"]
        assert "실행 결과 미확인" in variants["queuedDelivered"]
        assert variants["blank"] == ""
        assert variants["completedObserved"] == "상대 보고: 완료"
        assert variants["failedObserved"] == "상대 보고: 실패"
        assert variants["deniedObserved"] == "상대 보고: 거부"
        assert variants["partialObserved"] == "상대 보고: 부분 실행"
        assert variants["completedLocal"] == "로컬 실행 완료"
        assert variants["completedAmbiguous"] == "실행 결과 보고"
        assert "알 수 없음" in variants["unknownLocal"]
        assert reducer["statusTones"]["queuedDelivered"] == "info"
        assert reducer["statusTones"]["completedAmbiguous"] == "idle"
        assert reducer["statusTones"]["deniedAmbiguous"] == "idle"
        assert reducer["statusTones"]["deniedObserved"] == "bad"
        assert reducer["statusTones"]["blank"] == "idle"

    def test_event_labels_explain_semantics(self, reducer):
        labels = reducer["labels"]
        assert "로컬" in labels["sessionStopped"]
        assert "취소" in labels["sessionStopped"]
        assert "거부" in labels["taskDenied"]
        assert "something_new" in labels["unknownType"]

    def test_direction_is_keyed_by_event_type(self, reducer):
        assert reducer["direction"] == {"queued": "out", "received": "in", "completed": "local"}


class TestSelection:
    def test_session_filter_uses_bounded_events(self, reducer):
        assert reducer["sessions"] == [2, 3]

    def test_type_filters(self, reducer):
        assert reducer["taskFilter"] == [3]
        assert reducer["messageFilter"] == [1, 2, 4]

    def test_latest_exchanges_are_highlighted(self, reducer):
        keys = reducer["highlight"]
        assert len(keys) == 2
        assert all(isinstance(key, str) and ":" in key for key in keys)

    def test_cards_split_local_and_peers(self, reducer):
        cards = reducer["cards"]
        assert cards["localId"] == "robot-a"
        assert cards["peerIds"] == ["robot-b"]
        assert cards["peerSent"] == [1]
        assert cards["peerReceived"] == [2]


class TestSanitizeAndQuery:
    def test_secret_details_are_masked_and_bounded(self, reducer):
        data = reducer["sanitize"]
        assert data["token"] == "***"
        assert data["nestedToken"] == "***"
        assert data["nestedOk"] == 1
        assert data["note"] == "keep"
        assert data["blobLength"] <= 240

    def test_query_builder_omits_empty_params(self, reducer):
        query = reducer["query"]
        assert query["full"] == "?stream_id=stream-1&after=4&limit=50"
        assert query["minimal"] == ""
        assert query["nulls"] == ""

    def test_export_file_name_is_sortable(self, reducer):
        assert reducer["fileName"] == "robo_claw_cooperation_20261023_120509.jsonl"

    def test_freshness_threshold(self, reducer):
        assert reducer["fresh"] is False
        assert reducer["stale"] is True


class TestObservationMetadata:
    def test_event_and_task_metadata_survive_normalization(self, reducer):
        data = reducer["metadata"]
        assert data["observed"] == [True, None, None, None]
        assert data["deliveryQueued"] is True
        assert data["outboundId"] == "m-out"
        assert data["blankStatus"] == ""
        task = data["task"]
        assert task == {
            "peerId": "robot-b",
            "sessionId": "sess-1",
            "observed": True,
            "deliveryConfirmed": True,
            "outboundId": "m-out",
            "hasPrivate": False,
        }

    def test_tasks_can_be_filtered_per_session(self, reducer):
        # 세션 정보가 없는 legacy task 는 숨기지 않는다(현재 세션 소속으로 단정하지도 않는다).
        assert reducer["tasksBySession"] == ["t-sess-2", "t-unknown"]


class TestPersistenceSignals:
    def test_persistence_and_invalid_events_are_exposed(self, reducer):
        data = reducer["persistence"]
        assert data["invalidEvents"] == 2
        assert data["enabled"] is False
        assert data["reported"] is True
        assert "disk I/O" in data["error"]

    def test_missing_persistence_is_not_reported_as_disabled(self, reducer):
        assert reducer["persistenceMissing"] == {"reported": False, "enabled": False}


class TestDroppedCounters:
    def test_server_drops_and_client_overflow_do_not_double_count(self, reducer):
        assert reducer["droppedAfterFirst"] == 0
        assert reducer["droppedAfterRepeat"] == 0
        assert reducer["droppedAfterOverflow"] == 100
        assert reducer["droppedAfterSource"] == 204


class TestIdentityAndFreshness:
    def test_stream_change_does_not_fall_back_to_previous_identity(self, reducer):
        data = reducer["identityAfterStreamChange"]
        assert data["streamId"] == "stream-2"
        assert data["localRobotId"] == ""
        assert data["sessionId"] == ""
        assert data["goal"] == ""

    def test_empty_goal_clears_previous_goal(self, reducer):
        assert reducer["goalAfterClear"] == ""

    def test_unavailable_journal_does_not_keep_stale_tasks(self, reducer):
        data = reducer["staleTasksAfter"]
        assert data["available"] is False
        assert data["tasks"] == 0
        assert data["events"] == 1

    def test_huge_malformed_event_arrays_are_bounded(self, reducer):
        data = reducer["huge"]
        assert data["events"] <= reducer["cap"]["maxCached"]
        assert data["boundedInvalid"] is True


class TestSecretHardening:
    def test_extended_secret_keys_are_masked(self, reducer):
        hardened = reducer["hardened"]
        for key in ("authorization", "cookie", "bearer", "privatekey", "auth"):
            assert hardened[key] == "***"
        assert hardened["ok"] == 1

    def test_bearer_value_is_masked_in_content(self, reducer):
        content = reducer["bearerContent"]
        assert "abc.def.ghi1234" not in content
        assert "***" in content
