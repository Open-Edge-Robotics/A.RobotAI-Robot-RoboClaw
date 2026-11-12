"""협동 패널 DOM/정적 계약 테스트 — index.html, app.js 연동, cooperation.js 렌더.

- 패널이 요구하는 element id 가 index.html 에 실제로 존재하는지
- 협동 뷰가 추가 폴링 타이머나 스냅샷당 HTTP 호출을 만들지 않는지
- 표시가 textContent 삽입만 쓰고 innerHTML 을 쓰지 않는지(마스킹/이스케이프)
- 비활성/미수신/오래됨/유실/전체 화면 실패 같은 상태가 문구로 드러나는지
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.component

WEB = Path(__file__).resolve().parents[1] / "web"
ASSETS = WEB / "assets"
APP_JS = ASSETS / "app.js"
COOPERATION_JS = ASSETS / "cooperation.js"
STYLE_CSS = ASSETS / "style.css"
INDEX_HTML = WEB / "index.html"

_EL_CALL_RE = re.compile(r"el\(\"([^\"]+)\"\)")
_ID_ATTR_RE = re.compile(r"id=\"([^\"]+)\"")

PANEL_IDS = [
    "cooperation-panel",
    "cooperation-summary",
    "cooperation-notice",
    "cooperation-sessions",
    "cooperation-filter",
    "cooperation-cards",
    "cooperation-timeline",
    "cooperation-tasks",
    "cooperation-gap",
    "cooperation-export",
    "cooperation-fullscreen",
]


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 실행 파일이 없어 JS 협동 패널 테스트를 건너뜁니다")
    return node


class TestStaticContract:
    def test_panel_ids_exist_in_html(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        declared = set(_ID_ATTR_RE.findall(html))
        missing = [element_id for element_id in PANEL_IDS if element_id not in declared]
        assert missing == [], f"index.html 에 없는 협동 패널 id: {missing}"

    def test_cooperation_script_loads_before_app(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        cooperation = html.index("/assets/cooperation.js")
        app = html.index("/assets/app.js")
        assert cooperation < app

    def test_no_third_polling_timer_in_cooperation_view(self):
        script = COOPERATION_JS.read_text(encoding="utf-8")
        assert "setInterval(" not in script
        app = APP_JS.read_text(encoding="utf-8")
        # 협동 패널은 기존 /api/status 폴링 결과(state.cooperation)만 소비한다.
        assert "state.cooperation.applyStatus(snap)" in app
        assert "/api/cooperation" not in app

    def test_app_marks_view_stale_when_status_poll_fails(self):
        app = APP_JS.read_text(encoding="utf-8")
        assert "renderNow" in app

    def test_display_uses_text_insertion_only(self):
        script = COOPERATION_JS.read_text(encoding="utf-8")
        for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write", "outerHTML"):
            assert forbidden not in script, f"협동 패널이 {forbidden} 를 사용합니다"
        assert "textContent" in script

    def test_theme_tokens_and_accessibility_css(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        assert "prefers-reduced-motion" in css
        assert "rc-coop" in css
        # 키보드/터치 목표 크기 하한
        assert "min-height: 48px" in css or "min-height:48px" in css


DOM_SCRIPT = """
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
      title: "",
      href: "",
      download: "",
      append(...items) { for (const item of items) this.appendChild(item); },
      appendChild(child) {
        if (child && child.tagName === "#fragment") {
          for (const inner of child.children) this.children.push(inner);
          child.children.length = 0;
          return child;
        }
        this.children.push(child);
        return child;
      },
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
  return text.replace(/\\s+/g, " ").trim();
}

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

(async function main() {
  const made = makeDoc([
    "cooperation-panel",
    "cooperation-summary",
    "cooperation-notice",
    "cooperation-sessions",
    "cooperation-filter",
    "cooperation-cards",
    "cooperation-timeline",
    "cooperation-tasks",
    "cooperation-gap",
    "cooperation-export",
    "cooperation-fullscreen",
  ]);
  const doc = made.doc;
  const nodes = made.nodes;

  let clock = 1000000;
  const originalNow = Date.now;
  Date.now = () => clock;
  const realURL = globalThis.URL;
  globalThis.URL = { createObjectURL: () => "blob:mock", revokeObjectURL() {} };

  const fetchCalls = [];
  async function fakeFetch(url, options) {
    fetchCalls.push({ url: String(url), headers: (options || {}).headers || {} });
    if (String(url).indexOf("/export") !== -1) {
      return { ok: true, status: 200, blob: async () => ({ size: 2048 }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  }

  const controller = coop.mount({
    document: doc,
    getToken: () => "r-secret",
    fetchImpl: fakeFetch,
  });
  out.mounted = Boolean(controller);
  out.fetchAfterMount = fetchCalls.length;

  controller.applyStatus({
    state: {
      cooperation: snap([
        ev(1, {
          event_type: "message_queued",
          status: "queued",
          content: "<img src=x onerror=alert(1)>",
        }),
        ev(2, { event_type: "message_received", status: "received" }),
        ev(3, {
          event_type: "task_completed",
          status: "completed",
          details: { device_token: "abc", note: "visible" },
        }),
      ]),
    },
  });
  out.fetchAfterSnapshot = fetchCalls.length;
  out.timelineCount = nodes["cooperation-timeline"].children.length;
  out.timelineText = textOf(nodes["cooperation-timeline"]);
  out.timelineLabels = nodes["cooperation-timeline"].children.map(
    (item) => item.attributes["aria-label"] || ""
  );
  out.summaryText = textOf(nodes["cooperation-summary"]);
  out.cardsText = textOf(nodes["cooperation-cards"]);
  out.noticeText = textOf(nodes["cooperation-notice"]);
  out.sessionOptions = nodes["cooperation-sessions"].children.length;
  out.filterOptions = nodes["cooperation-filter"].children.length;

  // 세션 선택은 최근 캐시 안에서만 동작한다(추가 HTTP 없음).
  controller.applyStatus({
    state: {
      cooperation: snap([
        ev(4, { event_type: "message_received", session_id: "sess-2" }),
        ev(5, { event_type: "message_queued", session_id: "sess-2", status: "queued" }),
      ]),
    },
  });
  nodes["cooperation-sessions"].value = "sess-2";
  nodes["cooperation-sessions"].dispatch("change", { target: nodes["cooperation-sessions"] });
  out.sessionFilteredCount = nodes["cooperation-timeline"].children.length;
  out.fetchAfterSessionSelect = fetchCalls.length;

  // 유실 구간 표시
  controller.applyStatus({
    state: {
      cooperation: snap([ev(40, { event_type: "message_received" })], {
        oldest_cursor: 40,
        next_cursor: 41,
      }),
    },
  });
  out.gapText = textOf(nodes["cooperation-gap"]);

  // Maestro 비활성
  controller.applyStatus({ state: { cooperation: snap([], { enabled: false, active: true }) } });
  out.disabledNotice = textOf(nodes["cooperation-notice"]);

  // 관측 정보 없음(구버전 에이전트)
  controller.applyStatus({ state: {} });
  out.unavailableNotice = textOf(nodes["cooperation-notice"]);

  // 이벤트 없음(새 stream)
  controller.applyStatus({ state: { cooperation: snap([], { stream_id: "stream-empty" }) } });
  out.emptyNotice = textOf(nodes["cooperation-notice"]);
  nodes["cooperation-sessions"].value = "";
  nodes["cooperation-sessions"].dispatch("change", { target: nodes["cooperation-sessions"] });

  // 오래된 관측
  controller.applyStatus({ state: { cooperation: snap([ev(60)]) } });
  clock += coop.STALE_AFTER_MS + 1500;
  controller.renderNow();
  out.staleNotice = textOf(nodes["cooperation-notice"]);

  // 백엔드 실제 형태: session/decision/policy 는 빈 status + details 로 온다.
  controller.applyStatus({
    state: {
      cooperation: snap(
        [
          ev(100, { event_type: "session_started", status: "", details: { state: "active" } }),
          ev(101, { event_type: "decision", status: "", details: { decision: "refuse", reason: "peer not allowed" } }),
          ev(102, { event_type: "policy", status: "", details: { action: "deny", reason: "blocked skill", limits: "max 2" } }),
        ],
        { stream_id: "stream-raw" }
      ),
    },
  });
  out.rawEventText = textOf(nodes["cooperation-timeline"]);
  out.rawEventCount = nodes["cooperation-timeline"].children.length;

  // 정상 수명주기: 상태 칩/문구가 상대 성공을 단정하지 않아야 한다.
  controller.applyStatus({
    state: {
      cooperation: snap(
        [
          ev(150, { event_type: "session_started", status: "", details: { state: "active" } }),
          ev(151, { event_type: "message_queued", status: "queued", peer_id: "robot-b" }),
          ev(152, { event_type: "message_received", status: "received", peer_id: "robot-b" }),
          ev(153, { event_type: "task_started", status: "running", task_id: "t1", peer_id: "robot-b" }),
          ev(154, { event_type: "task_completed", status: "completed", task_id: "t1", peer_id: "robot-b" }),
        ],
        { stream_id: "stream-lifecycle" }
      ),
    },
  });
  out.lifecycleText = textOf(nodes["cooperation-timeline"]);

  // persistence/invalid 경고
  controller.applyStatus({
    state: {
      cooperation: snap([ev(110), { sequence: "bad" }, { event_type: "task_failed" }], {
        stream_id: "stream-warn",
        persistence: { enabled: false, error: "sqlite3 disk I/O error" },
      }),
    },
  });
  out.persistenceNotice = textOf(nodes["cooperation-notice"]);

  // persistence 필드가 아예 없는 구버전 저널은 "저장 비활성"으로 단정하지 않는다.
  controller.applyStatus({
    state: { cooperation: Object.assign(snap([ev(115)], { stream_id: "stream-no-persist" }), { persistence: undefined }) },
  });
  out.persistenceMissingNotice = textOf(nodes["cooperation-notice"]);

  // 태스크: 세션 필터 + peer 표시 + 관측 라벨
  controller.applyStatus({
    state: {
      cooperation: snap([ev(120, { session_id: "sess-1" }), ev(121, { session_id: "sess-2" })], {
        stream_id: "stream-tasks",
        tasks: [
          { task_id: "t1", session_id: "sess-1", peer_id: "robot-b", status: "queued", description: "scan shelf" },
          { task_id: "t2", session_id: "sess-2", peer_id: "robot-c", status: "completed", observed: true },
        ],
      }),
    },
  });
  out.tasksAll = textOf(nodes["cooperation-tasks"]);
  nodes["cooperation-sessions"].value = "sess-2";
  nodes["cooperation-sessions"].dispatch("change", { target: nodes["cooperation-sessions"] });
  out.tasksSession2 = textOf(nodes["cooperation-tasks"]);
  nodes["cooperation-sessions"].value = "";
  nodes["cooperation-sessions"].dispatch("change", { target: nodes["cooperation-sessions"] });

  // 비활성/미수신은 오래되어도 Maestro/미수신 문구가 지연보다 우선한다.
  controller.applyStatus({ state: { cooperation: snap([], { enabled: false, stream_id: "stream-off" }) } });
  clock += coop.STALE_AFTER_MS + 5000;
  controller.renderNow();
  out.disabledStaleNotice = textOf(nodes["cooperation-notice"]);
  controller.applyStatus({ state: {} });
  clock += coop.STALE_AFTER_MS + 5000;
  controller.renderNow();
  out.unavailableStaleNotice = textOf(nodes["cooperation-notice"]);

  // 내보내기: 읽기 토큰은 헤더로만 보내고 URL 에 넣지 않는다.
  await controller.download();
  out.exportCall = fetchCalls[fetchCalls.length - 1];
  out.exportNotice = textOf(nodes["cooperation-notice"]);

  // 전체 화면 실패 처리
  nodes["cooperation-panel"].requestFullscreen = () => Promise.reject(new Error("blocked"));
  await controller.toggleFullscreen();
  out.fullscreenFailNotice = textOf(nodes["cooperation-notice"]);
  nodes["cooperation-panel"].requestFullscreen = async () => {
    doc.fullscreenElement = nodes["cooperation-panel"];
  };
  await controller.toggleFullscreen();
  out.fullscreenOkNotice = textOf(nodes["cooperation-notice"]);
  out.fullscreenPressed = nodes["cooperation-fullscreen"].attributes["aria-pressed"];

  // 필터 select 는 이벤트 유형만 좁힌다(HTTP 없음).
  controller.applyStatus({
    state: {
      cooperation: snap([
        ev(70, { event_type: "message_received" }),
        ev(71, { event_type: "task_started", status: "running" }),
      ]),
    },
  });
  out.fetchBeforeFilter = fetchCalls.length;
  nodes["cooperation-filter"].value = "tasks";
  nodes["cooperation-filter"].dispatch("change", { target: nodes["cooperation-filter"] });
  out.filteredCount = nodes["cooperation-timeline"].children.length;
  out.fetchAfterFilter = fetchCalls.length;

  Date.now = originalNow;
  globalThis.URL = realURL;
  console.log(JSON.stringify(out));
})().catch((err) => {
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
});
"""


@pytest.fixture(scope="module")
def dom() -> dict:
    result = subprocess.run(
        [_node(), "-e", DOM_SCRIPT, "--", str(COOPERATION_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestNoExtraRequests:
    def test_mount_and_snapshot_render_do_not_call_http(self, dom):
        assert dom["mounted"] is True
        assert dom["fetchAfterMount"] == 0
        assert dom["fetchAfterSnapshot"] == 0

    def test_session_and_type_filter_do_not_call_http(self, dom):
        assert dom["fetchAfterSessionSelect"] == 0
        assert dom["fetchAfterFilter"] == dom["fetchBeforeFilter"]


class TestTimelineRendering:
    def test_events_are_rendered_as_text_nodes(self, dom):
        assert dom["timelineCount"] == 3
        # HTML 로 해석되지 않고 문자 그대로 남는다(innerHTML 미사용).
        assert "<img src=x onerror=alert(1)>" in dom["timelineText"]

    def test_direction_and_status_are_explained_in_text(self, dom):
        text = dom["timelineText"]
        assert "→" in text
        assert "←" in text
        assert "상대 수신 미확인" in text
        assert "로컬 관측" in text

    def test_items_expose_semantic_labels(self, dom):
        labels = [label for label in dom["timelineLabels"] if label]
        assert len(labels) == dom["timelineCount"]
        assert all("관측" in label or "수신" in label or "송신" in label for label in labels)

    def test_details_are_masked(self, dom):
        text = dom["timelineText"]
        assert "***" in text
        assert "abc" not in text

    def test_summary_shows_goal_and_local_identity(self, dom):
        summary = dom["summaryText"]
        assert "patrol the corridor" in summary
        assert "robot-a" in summary

    def test_cards_show_local_and_peer(self, dom):
        cards = dom["cardsText"]
        assert "robot-a" in cards
        assert "robot-b" in cards


class TestStates:
    def test_disabled_state_is_explicit(self, dom):
        notice = dom["disabledNotice"]
        assert "Maestro" in notice
        assert "비활성" in notice

    def test_unavailable_state_is_explicit(self, dom):
        assert dom["unavailableNotice"]

    def test_empty_state_is_explicit(self, dom):
        assert "이벤트" in dom["emptyNotice"]

    def test_stale_state_is_explicit(self, dom):
        notice = dom["staleNotice"]
        assert "지연" in notice or "오래" in notice

    def test_gap_state_is_explicit(self, dom):
        gap = dom["gapText"]
        assert "유실" in gap or "건너" in gap


class TestExportAndFullscreen:
    def test_export_uses_header_token_and_no_url_token(self, dom):
        call = dom["exportCall"]
        assert call["url"] == "/api/cooperation/export"
        assert call["headers"]["X-RoboClaw-Token"] == "r-secret"
        assert "r-secret" not in call["url"]
        assert "완료" in dom["exportNotice"]

    def test_fullscreen_failure_is_handled(self, dom):
        assert "실패" in dom["fullscreenFailNotice"]

    def test_fullscreen_toggle_marks_aria_pressed(self, dom):
        assert "전체 화면" in dom["fullscreenOkNotice"]
        assert dom["fullscreenPressed"] in ("true", "false")


class TestFilters:
    def test_session_selection_narrows_timeline(self, dom):
        assert dom["sessionOptions"] >= 2
        assert dom["sessionFilteredCount"] == 2

    def test_type_filter_narrows_timeline(self, dom):
        assert dom["filterOptions"] >= 2
        assert dom["filteredCount"] == 1


class TestStatuslessEvents:
    """session/decision/policy 는 status 가 비어 있고 details 로 의미를 전달한다."""

    def test_renders_type_and_details_without_unknown_chip(self, dom):
        text = dom["rawEventText"]
        assert "알 수 없음" not in text
        assert "세션 시작" in text
        assert "세션 상태: active" in text
        assert "판단: refuse" in text
        assert "peer not allowed" in text
        assert "정책 액션: deny" in text
        assert "blocked skill" in text
        assert "max 2" in text

    def test_statusless_events_are_all_rendered(self, dom):
        assert dom["rawEventCount"] == 3


class TestPersistenceAndInvalid:
    def test_persistence_and_invalid_warnings_are_shown(self, dom):
        notice = dom["persistenceNotice"]
        assert "메모리" in notice
        assert "저장 실패" in notice
        assert "disk I/O" in notice
        assert "무시" in notice

    def test_missing_persistence_is_not_shown_as_disabled(self, dom):
        assert "메모리" not in dom["persistenceMissingNotice"]


class TestTasks:
    def test_tasks_show_peer_and_session_scoped(self, dom):
        assert "robot-b" in dom["tasksAll"] and "robot-c" in dom["tasksAll"]
        assert "t2" in dom["tasksSession2"]
        assert "t1" not in dom["tasksSession2"]
        assert "robot-c" in dom["tasksSession2"]
        assert "robot-b" not in dom["tasksSession2"]

    def test_observed_task_status_is_labelled_as_peer_report(self, dom):
        assert "상대 보고: 완료" in dom["tasksAll"]
        assert "상대 수신 미확인" in dom["tasksAll"]


class TestStalePriority:
    def test_disabled_state_wins_over_stale(self, dom):
        notice = dom["disabledStaleNotice"]
        assert "Maestro" in notice
        assert "지연" not in notice

    def test_unavailable_state_wins_over_stale(self, dom):
        notice = dom["unavailableStaleNotice"]
        assert "지연" not in notice


class TestPollSchedulerStaleness:
    def test_429_backoff_branch_refreshes_cooperation_staleness(self):
        app = APP_JS.read_text(encoding="utf-8")
        branch = app[app.index("status === 429") : app.index("status === 429") + 500]
        assert "renderNow()" in branch


class TestNormalLifecycle:
    """정상 수명주기에서 상태 문구가 상대의 실제 완료를 단정하면 안 된다."""

    def test_lifecycle_has_no_unknown_chip(self, dom):
        text = dom["lifecycleText"]
        assert "알 수 없음" not in text
        assert "세션 시작" in text
        assert "세션 상태: active" in text

    def test_lifecycle_states_local_and_unconfirmed(self, dom):
        text = dom["lifecycleText"]
        assert "상대 수신 미확인" in text
        assert "로컬 메시지 수신" in text
        assert "로컬 실행 중" in text
        # observed 메타데이터가 없으면 완료를 상대 보고/로컬 완료로 단정하지 않는다.
        assert "실행 결과 보고" in text
        assert "상대 보고: 완료" not in text
        assert "로컬 실행 완료" not in text
