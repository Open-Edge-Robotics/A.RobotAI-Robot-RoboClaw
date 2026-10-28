/* 로그 패널 — /api/logs 폴링, 레벨/검색 필터, 파일 저장. DOM 의존은 mount() 안에만 둔다. */
(function (global) {
  "use strict";

  const LEVEL_CLASSES = {
    debug: "rc-log-debug",
    info: "rc-log-info",
    warn: "rc-log-warn",
    error: "rc-log-error",
    fatal: "rc-log-fatal",
  };
  const HEADER_TOKEN = "X-RoboClaw-Token";
  const DEFAULT_INTERVAL_MS = 3000;
  const DEFAULT_LIMIT = 200;

  function pad(value, width) {
    return String(value).padStart(width, "0");
  }

  /** 밀리초 epoch 를 화면/파일용 시각(HH:MM:SS.mmm, 로컬 시간)으로 만든다. */
  function formatClock(msEpoch) {
    const date = new Date(msEpoch);
    if (Number.isNaN(date.getTime())) return "--:--:--.---";
    return (
      pad(date.getHours(), 2) + ":" + pad(date.getMinutes(), 2) + ":" + pad(date.getSeconds(), 2) +
      "." + pad(date.getMilliseconds(), 3)
    );
  }

  /** 로그 한 줄을 한 줄 텍스트로 만든다(복사/툴팁용). */
  function formatLine(entry) {
    const stamp =
      typeof entry.stamp_sec === "number" && entry.stamp_sec > 0
        ? formatClock(entry.stamp_sec * 1000)
        : String(entry.time || "");
    return (
      stamp + " " + String(entry.level || "info").toUpperCase() + " " +
      (entry.node || "-") + ": " + (entry.message || "")
    ).trim();
  }

  /** 로그 레벨에 대응하는 색상 클래스(모르는 레벨은 info 로 표시). */
  function levelClass(level) {
    return LEVEL_CLASSES[String(level || "").toLowerCase()] || LEVEL_CLASSES.info;
  }

  /** 저장 파일명 — 이름만 보고 시간 순으로 정렬되게 만든다. */
  function exportFileName(now) {
    const date = now instanceof Date ? now : new Date();
    const stamp =
      date.getFullYear() + pad(date.getMonth() + 1, 2) + pad(date.getDate(), 2) + "_" +
      pad(date.getHours(), 2) + pad(date.getMinutes(), 2) + pad(date.getSeconds(), 2);
    return "robo_claw_log_" + stamp + ".log";
  }

  /** 서버 쿼리 문자열을 만든다(빈 필터는 주소를 어지럽히지 않도록 생략). */
  function buildLogQuery(filters) {
    const source = filters || {};
    const params = [];
    const level = String(source.level || "").trim();
    if (level) params.push("level=" + encodeURIComponent(level));
    const query = String(source.query || "").trim();
    if (query) params.push("q=" + encodeURIComponent(query));
    if (source.limit) params.push("limit=" + encodeURIComponent(source.limit));
    if (source.format) params.push("format=" + encodeURIComponent(source.format));
    return params.length ? "?" + params.join("&") : "";
  }

  function renderLine(entry, doc) {
    const line = doc.createElement("div");
    line.className = "rc-log-line";
    line.title = formatLine(entry);

    const time = doc.createElement("span");
    time.className = "rc-log-time";
    time.textContent =
      typeof entry.stamp_sec === "number" && entry.stamp_sec > 0
        ? formatClock(entry.stamp_sec * 1000)
        : String(entry.time || "");

    const level = doc.createElement("span");
    level.className = "rc-log-level " + levelClass(entry.level);
    level.textContent = String(entry.level || "info").toUpperCase();

    const node = doc.createElement("span");
    node.className = "rc-log-node";
    node.textContent = entry.node || "-";

    const message = doc.createElement("span");
    message.className = "rc-log-msg";
    message.textContent = entry.message || "";

    line.append(time, level, node, message);
    return line;
  }

  /**
   * 로그 패널을 화면에 붙인다.
   * @param {{getToken?: () => string, intervalMs?: number, limit?: number, document?: Document}} options
   */
  function mount(options) {
    const opts = options || {};
    const doc = opts.document || (typeof document !== "undefined" ? document : null);
    if (!doc) return null;
    const getToken = opts.getToken || (() => "");
    const intervalMs = opts.intervalMs || DEFAULT_INTERVAL_MS;
    const limit = opts.limit || DEFAULT_LIMIT;

    const view = doc.getElementById("log-view");
    if (!view) return null;
    const nodes = {
      level: doc.getElementById("log-level"),
      search: doc.getElementById("log-search"),
      follow: doc.getElementById("log-follow"),
      refresh: doc.getElementById("log-refresh"),
      download: doc.getElementById("log-download"),
      summary: doc.getElementById("log-summary"),
      result: doc.getElementById("log-result"),
      view: view,
    };

    const state = { timer: null, inFlight: false, downloading: false };

    function filters(extra) {
      return Object.assign(
        {
          level: nodes.level ? nodes.level.value : "",
          query: nodes.search ? nodes.search.value : "",
          limit: limit,
        },
        extra || {}
      );
    }

    function authHeaders() {
      const token = getToken();
      return token ? { [HEADER_TOKEN]: token } : {};
    }

    function showResult(text, cls) {
      if (!nodes.result) return;
      nodes.result.className = "small " + (cls || "text-body-secondary");
      nodes.result.textContent = text || "";
    }

    function nearBottom() {
      return nodes.view.scrollHeight - nodes.view.scrollTop - nodes.view.clientHeight < 40;
    }

    function setSummary(data) {
      if (!nodes.summary) return;
      const parts = [
        "버퍼 " + (data.stored || 0) + "/" + (data.capacity || 0) + "줄",
        "표시 " + (data.returned || 0) + "줄",
        "레벨 " + (data.level || "전체"),
      ];
      if (data.topic) parts.unshift(data.topic);
      if (data.truncated) parts.push("최근 " + (data.limit || 0) + "줄만 표시");
      if (data.available === false) parts.push(data.error || "로그 미수신");
      nodes.summary.textContent = parts.join(" · ");
    }

    function render(data) {
      const entries = Array.isArray(data.entries) ? data.entries : [];
      const keepBottom = nodes.follow && nodes.follow.checked ? true : nearBottom();
      nodes.view.textContent = "";
      if (!entries.length) {
        const empty = doc.createElement("div");
        empty.className = "rc-log-empty";
        empty.textContent =
          data.available === false
            ? data.error || "로그를 사용할 수 없습니다"
            : "표시할 로그가 없습니다";
        nodes.view.appendChild(empty);
      } else {
        const fragment = doc.createDocumentFragment();
        for (const entry of entries) fragment.appendChild(renderLine(entry, doc));
        nodes.view.appendChild(fragment);
      }
      if (keepBottom) nodes.view.scrollTop = nodes.view.scrollHeight;
      setSummary(data);
    }

    async function refresh() {
      if (state.inFlight) return;
      state.inFlight = true;
      try {
        const resp = await fetch("/api/logs" + buildLogQuery(filters()), {
          headers: authHeaders(),
        });
        if (resp.status === 401 || resp.status === 403) {
          throw new Error("권한 없음: 읽기 토큰을 확인하세요. (" + resp.status + ")");
        }
        if (!resp.ok) throw new Error("status " + resp.status);
        render(await resp.json());
        if (nodes.follow && nodes.follow.checked) showResult("", "");
      } catch (err) {
        showResult("로그 오류: " + err.message, "text-danger");
      } finally {
        state.inFlight = false;
      }
    }

    async function download() {
      if (state.downloading) return;
      state.downloading = true;
      showResult("파일 준비 중…", "text-body-secondary");
      try {
        const resp = await fetch(
          "/api/logs/export" + buildLogQuery(filters({ format: "txt", limit: 2000 })),
          { headers: authHeaders() }
        );
        if (!resp.ok) throw new Error("status " + resp.status);
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const link = doc.createElement("a");
        link.href = url;
        link.download = exportFileName(new Date());
        doc.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        showResult(
          "파일 저장 완료 (" + Math.max(1, Math.round(blob.size / 1024)) + " KB)",
          "text-success"
        );
      } catch (err) {
        showResult("저장 실패: " + err.message, "text-danger");
      } finally {
        state.downloading = false;
      }
    }

    function schedule() {
      if (state.timer) clearTimeout(state.timer);
      state.timer = setTimeout(async () => {
        if (!nodes.follow || nodes.follow.checked) {
          await refresh();
        }
        schedule();
      }, intervalMs);
    }

    if (nodes.refresh) nodes.refresh.addEventListener("click", () => refresh());
    if (nodes.download) nodes.download.addEventListener("click", () => download());
    if (nodes.level) nodes.level.addEventListener("change", () => refresh());
    if (nodes.search) {
      nodes.search.addEventListener("change", () => refresh());
      nodes.search.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") refresh();
      });
    }

    refresh();
    schedule();
    return { refresh, download };
  }

  const api = {
    formatClock,
    formatLine,
    levelClass,
    exportFileName,
    buildLogQuery,
    mount,
  };
  global.RcLogs = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
