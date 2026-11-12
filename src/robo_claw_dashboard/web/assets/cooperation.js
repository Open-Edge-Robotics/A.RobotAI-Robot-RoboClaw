/* 협동 관측 패널 — /api/status 스냅샷(state.cooperation)만 소비한다(추가 타이머/HTTP 없음).
 * 모든 문구는 로컬 관측 기준이며 상대 로봇의 실제 동작을 보증하지 않는다. */
(function (global) {
  "use strict";
  const HEADER_TOKEN = "X-RoboClaw-Token";
  const MAX_RENDERED_EVENTS = 300, MAX_CACHED_EVENTS = 400, MAX_SCAN_EVENTS = 100, MAX_SCAN_TASKS = 100;
  const MAX_TASKS = 50, MAX_RECENT_GAPS = 5, MAX_TEXT = 240, MAX_KEY_LENGTH = 64;
  const MAX_DETAIL_ITEMS = 20, MAX_DETAIL_DEPTH = 3, MAX_DETAIL_TEXT = 200;
  const HIGHLIGHT_EXCHANGES = 2, STALE_AFTER_MS = 15000, MASK = "***";
  const SECRET_HINTS = "token secret password passwd api_key apikey access_key private_key privatekey credential auth authorization cookie bearer".split(" ");
  const OBSERVED_ORIGINS = ["peer_report", "peer-reported", "peer", "remote", "observed"];
  const INLINE_SECRET_RE =
    /([A-Za-z0-9_-]*(?:authorization|private[_-]?key|token|secret|password|passwd|api[_-]?key|bearer|cookie|auth)[A-Za-z0-9_-]*)(\s*[:=]\s*)(?!bearer\b)\S+/gi;
  const BEARER_RE = /\b(bearer)(\s+)([A-Za-z0-9._~+/-]{4,}=*)/gi;
  const STATUS_META = {
    queued: { label: "송신 큐 등록 · 상대 수신 미확인", tone: "idle", icon: "…" }, received: { label: "로컬 메시지 수신", tone: "info", icon: "↓" },
    running: { label: "로컬 실행 중", tone: "warn", icon: "▶" }, completed: { label: "로컬 실행 완료", tone: "ok", icon: "✓" },
    failed: { label: "로컬 실행 실패", tone: "bad", icon: "✕" }, denied: { label: "로컬 정책 거부", tone: "bad", icon: "⊘" },
    partial: { label: "로컬 부분 실행", tone: "warn", icon: "◐" }, unknown: { label: "알 수 없음 (로컬 관측)", tone: "idle", icon: "?" },
  };
  const OBSERVED_LABELS = { completed: "상대 보고: 완료", failed: "상대 보고: 실패", denied: "상대 보고: 거부", partial: "상대 보고: 부분 실행" };
  const AMBIGUOUS_RESULT_LABEL = "실행 결과 보고";
  const DELIVERY_CONFIRMED_LABEL = "상대 회신 확인 · 실행 결과 미확인";
  const STATUS_EVENT_TYPES = "task_started task_completed task_failed task_denied task_partial message_queued message_received message_send_failed message_rejected reply_queued".split(" ");
  const EVENT_LABELS = {
    session_started: "세션 시작", session_stopped: "로컬 협동 종료 (원격 취소 아님)", message_queued: "메시지 송신 큐 등록",
    message_received: "메시지 수신", message_send_failed: "메시지 송신 실패", message_rejected: "메시지 거부",
    decision: "에이전트 판단", policy: "정책 판정", task_started: "태스크 시작", task_completed: "태스크 완료",
    task_failed: "태스크 실패", task_denied: "태스크 거부", task_partial: "태스크 부분 실행", reply_queued: "회신 큐 등록",
  };
  const FILTER_TYPES = {
    messages: ["message_queued", "message_received", "message_send_failed", "message_rejected", "reply_queued"],
    tasks: ["task_started", "task_completed", "task_failed", "task_denied", "task_partial"], policy: ["decision", "policy"],
  };
  const FILTER_OPTIONS = [{ value: "messages", label: "메시지 교환" }, { value: "tasks", label: "태스크" }, { value: "policy", label: "정책/판단" }];
  const pad = (value, width) => String(value).padStart(width, "0");
  const finite = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);
  function formatClock(msEpoch) {
    const date = new Date(Number(msEpoch) || 0);
    return Number.isNaN(date.getTime()) ? "--:--:--" : [date.getHours(), date.getMinutes(), date.getSeconds()].map((p) => pad(p, 2)).join(":");
  }
  const asInt = (value, fallback) => (Number.isFinite(Number(value)) ? Math.trunc(Number(value)) : fallback);
  function asText(value, limit) {
    const text = value == null ? "" : String(value), bound = limit || MAX_TEXT;
    return text.length > bound ? text.slice(0, bound) + "…" : text;
  }
  const maskText = (text) => String(text == null ? "" : text)
    .replace(BEARER_RE, (m, key, sep) => key + sep + MASK)
    .replace(INLINE_SECRET_RE, (m, key, sep) => key + sep + MASK);
  const isSecretKey = (key) => SECRET_HINTS.some((hint) => String(key || "").toLowerCase().indexOf(hint) >= 0);
  function sanitizeValue(value, depth) {
    if (depth >= MAX_DETAIL_DEPTH) return MASK;
    if (Array.isArray(value)) return value.slice(0, MAX_DETAIL_ITEMS).map((item) => sanitizeValue(item, depth + 1));
    if (value && typeof value === "object") return sanitizeDetails(value, depth);
    if (typeof value === "string") return maskText(asText(value, MAX_DETAIL_TEXT));
    return typeof value === "number" || typeof value === "boolean" ? value : asText(value, MAX_DETAIL_TEXT);
  }
  /** details 는 펼쳐 보는 값이므로 깊이/항목/키/문자 수를 제한하고 시크릿 키를 마스킹한다. */
  function sanitizeDetails(details, depth) {
    const level = depth || 0;
    if (!details || typeof details !== "object" || Array.isArray(details) || level > MAX_DETAIL_DEPTH) return {};
    const out = {};
    for (const key of Object.keys(details).slice(0, MAX_DETAIL_ITEMS)) {
      const name = asText(key, MAX_KEY_LENGTH);
      if (name) out[name] = isSecretKey(name) ? MASK : sanitizeValue(details[key], level + 1);
    }
    return out;
  }
  function normalizeStatus(raw) {
    const status = asText(raw, 24).trim().toLowerCase();
    return !status ? "" : STATUS_META[status] ? status : "unknown";
  }
  function statusMeta(status, options) {
    const key = normalizeStatus(status), opts = options || {};
    if (!key) return { label: "", tone: "idle", icon: "" };
    if (key === "queued" && opts.deliveryConfirmed) return { label: DELIVERY_CONFIRMED_LABEL, tone: "info", icon: "✓" };
    if (!OBSERVED_LABELS[key]) return STATUS_META[key];
    if (opts.observed === true) return { label: OBSERVED_LABELS[key], tone: STATUS_META[key].tone, icon: "◇" };
    return opts.observed === false ? STATUS_META[key] : { label: AMBIGUOUS_RESULT_LABEL, tone: "idle", icon: "…" };
  }
  const formatStatusLabel = (status, options) => statusMeta(status, options).label;
  const statusTone = (status, options) => statusMeta(status, options).tone;
  const formatEventLabel = (eventType) => EVENT_LABELS[String(eventType || "")] || String(eventType || "알 수 없는 이벤트");
  const stringField = (raw, key) => asText(raw[key], 96);
  function flags(raw) {
    const origin = typeof raw.origin === "string" ? raw.origin.trim().toLowerCase() : "";
    const observed = typeof raw.observed === "boolean" ? raw.observed
      : raw.details && typeof raw.details.observed === "boolean" ? raw.details.observed
        : OBSERVED_ORIGINS.indexOf(origin) >= 0 ? true : null;
    return { observed: observed, deliveryConfirmed: raw.delivery_confirmed === true, outboundMessageId: asText(raw.outbound_message_id, 96) };
  }
  function normalizeEvent(raw) {
    if (!raw || typeof raw !== "object" || asInt(raw.sequence, -1) < 1) return null;
    return Object.assign({
      sequence: asInt(raw.sequence, 0), eventType: asText(raw.event_type, 48) || "unknown", timestamp: finite(raw.timestamp),
      robotId: stringField(raw, "robot_id"), peerId: stringField(raw, "peer_id"), taskId: stringField(raw, "task_id"),
      sessionId: stringField(raw, "session_id"), messageId: stringField(raw, "message_id"), replyTo: stringField(raw, "reply_to"),
      status: normalizeStatus(raw.status), content: maskText(asText(raw.content, MAX_TEXT)), details: sanitizeDetails(raw.details, 0),
    }, flags(raw));
  }
  function normalizeTask(raw) {
    if (!raw || typeof raw !== "object" || !asText(raw.task_id, 96)) return null;
    return Object.assign({
      taskId: asText(raw.task_id, 96), status: normalizeStatus(raw.status) || "unknown",
      sessionId: stringField(raw, "session_id"), peerId: stringField(raw, "peer_id"), updatedAt: finite(raw.updated_at),
      description: maskText(asText(raw.description, MAX_TEXT)), result: maskText(asText(raw.result, MAX_TEXT)),
    }, flags(raw));
  }
  function normalizeCooperation(raw) {
    const empty = { available: false, enabled: false, active: false, localRobotId: "", sessionId: "", streamId: "", goal: "",
      events: [], tasks: [], invalidEvents: 0, oldestCursor: 0, droppedEvents: 0,
      persistence: { enabled: false, error: null, reported: false }, error: "협동 관측 정보를 사용할 수 없습니다" };
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return empty;
    if (raw.available !== true) {
      empty.error = asText(raw.error, MAX_TEXT) || "에이전트가 협동 관측 정보를 제공하지 않습니다";
      return empty;
    }
    const events = [];
    let invalid = 0;
    for (const item of Array.isArray(raw.events) ? raw.events.slice(-MAX_SCAN_EVENTS) : []) {
      const event = normalizeEvent(item);
      if (event) events.push(event); else invalid += 1;
    }
    events.sort((a, b) => a.sequence - b.sequence);
    const streamId = asText(raw.stream_id, 96);
    for (const event of events) event.key = (streamId || "stream") + ":" + event.sequence;
    const enabled = raw.enabled === true, persistence = raw.persistence && typeof raw.persistence === "object" ? raw.persistence : null;
    return Object.assign(empty, { available: true, enabled: enabled, active: enabled && raw.active === true,
      localRobotId: stringField(raw, "local_robot_id"), sessionId: stringField(raw, "session_id"), error: "",
      goal: maskText(asText(raw.goal, MAX_TEXT)), streamId: streamId, events: events, invalidEvents: invalid,
      tasks: (Array.isArray(raw.tasks) ? raw.tasks.slice(0, MAX_SCAN_TASKS) : []).map(normalizeTask).filter(Boolean).slice(0, MAX_TASKS),
      oldestCursor: Math.max(0, asInt(raw.oldest_cursor, 0)), droppedEvents: Math.max(0, asInt(raw.dropped_events, 0)),
      persistence: { reported: Boolean(persistence), enabled: Boolean(persistence && persistence.enabled === true),
        error: persistence && persistence.error ? maskText(asText(persistence.error, MAX_TEXT)) : null } });
  }
  const emptyState = (nowMs) => Object.assign(normalizeCooperation(null), { events: [], tasks: [], gaps: [], lastSequence: 0,
    updatedAtMs: 0, resets: 0, receivedAtMs: asInt(nowMs, 0), sourceDroppedEvents: 0, clientOverflowEvents: 0 });
  function detectGaps(existing, previousLast, previousDropped, incoming) {
    const gaps = (existing || []).slice(), last = asInt(previousLast, 0), oldest = asInt(incoming.oldestCursor, 0);
    if (oldest > last + 1) gaps.push({ kind: "window", from: last + 1, to: oldest - 1, missed: oldest - 1 - last });
    const delta = asInt(incoming.droppedEvents, 0) - asInt(previousDropped, 0);
    if (delta > 0) gaps.push({ kind: "dropped", from: null, to: null, missed: delta });
    return gaps;
  }
  /** 새 스냅샷을 로컬 캐시에 병합한다(중복 제거·정렬·상한·stream 재시작·유실 구간). */
  function reduceSnapshot(state, raw, nowMs) {
    const base = state || emptyState(nowMs);
    const now = asInt(nowMs, base.receivedAtMs || 0);
    const incoming = normalizeCooperation(raw);
    if (!incoming.available) {
      // 관측이 끊긴 스냅샷의 태스크를 "현재 상태"로 보여주지 않는다(이벤트는 이력으로 유지).
      return Object.assign({}, base, { available: false, enabled: false, active: false, tasks: [],
        error: incoming.error || base.error, updatedAtMs: base.updatedAtMs || now, receivedAtMs: now });
    }
    const changed = Boolean(incoming.streamId && base.streamId && incoming.streamId !== base.streamId);
    const previousKeys = {}, seen = {}, events = [];
    for (const item of (changed ? [] : base.events) || []) previousKeys[item.key] = true;
    for (const item of (changed ? [] : base.events).concat(incoming.events)) {
      if (item && !seen[item.key]) events.push(item);
      if (item) seen[item.key] = true;
    }
    events.sort((a, b) => a.sequence - b.sequence);
    let overflow = 0;
    while (events.length > MAX_CACHED_EVENTS) if (previousKeys[events.shift().key]) overflow += 1;
    const previousSource = changed ? 0 : asInt(base.sourceDroppedEvents, 0), previousOverflow = changed ? 0 : asInt(base.clientOverflowEvents, 0);
    const gaps = detectGaps(changed ? [] : base.gaps, changed ? 0 : base.lastSequence, previousSource, incoming);
    if (overflow > 0) gaps.push({ kind: "dropped", from: null, to: null, missed: overflow });
    const sourceDropped = Math.max(previousSource, asInt(incoming.droppedEvents, 0));
    const clientOverflow = previousOverflow + overflow;
    return { available: true, enabled: incoming.enabled, active: incoming.active, error: "",
      localRobotId: incoming.localRobotId, sessionId: incoming.sessionId, goal: incoming.goal,
      streamId: incoming.streamId || base.streamId, events: events, tasks: incoming.tasks,
      gaps: gaps.slice(-MAX_RECENT_GAPS), oldestCursor: incoming.oldestCursor, persistence: incoming.persistence,
      sourceDroppedEvents: sourceDropped, clientOverflowEvents: clientOverflow, invalidEvents: incoming.invalidEvents,
      droppedEvents: sourceDropped + clientOverflow, updatedAtMs: now, receivedAtMs: now,
      lastSequence: events.length ? events[events.length - 1].sequence : 0, resets: (base.resets || 0) + (changed ? 1 : 0) };
  }
  function isStale(state, nowMs) {
    const now = asInt(nowMs, Date.now());
    return !state || !state.available ? true : now - asInt(state.receivedAtMs, now) > STALE_AFTER_MS;
  }
  function eventDirection(event) {
    const type = String((event && event.eventType) || "");
    if (type === "message_queued" || type === "reply_queued" || type === "message_send_failed") return "out";
    if (type === "message_received" || type === "message_rejected") return "in";
    return "local";
  }
  const directionArrow = (direction) => (direction === "out" ? "→" : direction === "in" ? "←" : "•");
  const directionText = (direction) =>
    direction === "out" ? "로컬 → 상대 송신" : direction === "in" ? "상대 → 로컬 수신" : "로컬 관측";
  function selectEvents(state, options) {
    const opts = options || {}, sessionId = asText(opts.sessionId, 96), allowed = FILTER_TYPES[opts.filterType] || null;
    let list = (state && state.events) || [];
    if (sessionId) list = list.filter((item) => !item.sessionId || item.sessionId === sessionId);
    if (allowed) list = list.filter((item) => allowed.indexOf(item.eventType) >= 0);
    const limit = Math.min(Math.max(asInt(opts.limit, MAX_RENDERED_EVENTS), 1), MAX_RENDERED_EVENTS);
    return list.length > limit ? list.slice(-limit) : list.slice();
  }
  function selectTasks(state, options) {
    const sessionId = asText((options || {}).sessionId, 96), list = (state && state.tasks) || [];
    return (sessionId ? list.filter((item) => !item.sessionId || item.sessionId === sessionId) : list).slice(0, MAX_TASKS);
  }
  function sessionOptions(state) {
    const seen = {}, options = [];
    for (const { sessionId } of (state && state.events) || []) {
      if (sessionId && !seen[sessionId]) options.push({ value: sessionId, label: sessionId });
      seen[sessionId] = true;
    }
    return options;
  }
  function latestExchangeKeys(state, count) {
    const wanted = Math.max(1, asInt(count, HIGHLIGHT_EXCHANGES)), keys = [], events = (state && state.events) || [];
    for (let i = events.length - 1; i >= 0 && keys.length < wanted; i -= 1) if (eventDirection(events[i]) !== "local") keys.push(events[i].key);
    return keys;
  }
  function buildCards(state) {
    const localId = asText(state && state.localRobotId, 96), peers = {};
    let total = 0;
    for (const event of (state && state.events) || []) {
      total += 1;
      const peerId = event.peerId || (event.robotId && event.robotId !== localId ? event.robotId : ""), direction = eventDirection(event);
      if (!peerId) continue;
      const entry = (peers[peerId] = peers[peerId] || { id: peerId, sent: 0, received: 0, lastEventType: "", lastSequence: 0 });
      if (direction !== "local") entry[direction === "out" ? "sent" : "received"] += 1;
      if (event.sequence >= entry.lastSequence) Object.assign(entry, { lastSequence: event.sequence, lastEventType: event.eventType });
    }
    const list = Object.keys(peers).map((key) => peers[key]).sort((a, b) => b.lastSequence - a.lastSequence).slice(0, 6);
    return { local: { id: localId, events: total, active: Boolean(state && state.active) }, peers: list };
  }
  function exportFileName(now) {
    const date = now instanceof Date ? now : new Date();
    const day = [date.getFullYear(), date.getMonth() + 1, date.getDate()].map((part) => pad(part, 2)).join("");
    return "robo_claw_cooperation_" + day + "_" + [date.getHours(), date.getMinutes(), date.getSeconds()].map((part) => pad(part, 2)).join("") + ".jsonl";
  }
  function buildCooperationQuery(options) {
    const source = options || {}, params = [];
    for (const [key, value] of [["stream_id", source.streamId], ["after", source.after], ["limit", source.limit]]) if (value != null && value !== "") params.push(key + "=" + encodeURIComponent(value));
    return params.length ? "?" + params.join("&") : "";
  }
  function el(doc, tag, className, text) {
    const element = doc.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = text;
    return element;
  }
  function eventDescriptor(event) {
    const d = event.details || {}, parts = [];
    if (d.state) parts.push("세션 상태: " + asText(d.state, 96));
    if (d.decision) parts.push("판단: " + asText(d.decision, 96));
    if (d.action) parts.push("정책 액션: " + asText(d.action, 96));
    if (d.reason) parts.push("이유: " + asText(d.reason, 160));
    if (d.limits) parts.push("제한: " + asText(d.limits, 160));
    return parts.join(" · ");
  }
  function noticeLines(state, stale, nowMs) {
    const lines = [];
    if (!state.available) {
      lines.push(!state.updatedAtMs
        ? { text: "협동 관측 정보를 기다리는 중입니다…", tone: "info" }
        : { text: (state.error || "협동 관측 정보를 사용할 수 없습니다") + " — 관측 정보가 없는 에이전트이거나 아직 수신하지 못했습니다", tone: "warn" });
      return lines;
    }
    if (!state.enabled) lines.push({ text: "Maestro 관제로 협동이 비활성화되었습니다 · 송신/수신/실행 표시를 갱신하지 않습니다", tone: "warn" });
    else if (!state.events.length) lines.push({ text: "표시할 협동 이벤트가 없습니다 (최근 관측 범위)", tone: "info" });
    else if (stale) lines.push({ text: "관측 지연: 마지막 갱신 후 " + Math.max(0, Math.round((nowMs - state.receivedAtMs) / 1000)) + "초 경과 — 표시 값이 오래되었습니다", tone: "warn" });
    const persistence = state.persistence || {};
    if (persistence.reported && !persistence.enabled) lines.push({ text: "관측 저장 비활성 — 메모리에서만 유지됩니다", tone: "warn" });
    if (persistence.error) lines.push({ text: "저장 실패: " + persistence.error + " (기록은 계속되지만 파일에 남지 않습니다)", tone: "warn" });
    if (state.invalidEvents > 0) lines.push({ text: "형식이 맞지 않는 이벤트 " + state.invalidEvents + "건을 무시했습니다", tone: "warn" });
    return lines;
  }
  function renderSummary(doc, node, state, stale, nowMs) {
    if (!node) return;
    node.textContent = "";
    const rows = [["목표", state.goal || "–"], ["로컬 로봇", state.localRobotId || "–"], ["최근 이벤트", state.events.length + "건 (로컬 캐시)"],
      ["세션", state.sessionId ? asText(state.sessionId, 24) : "–"]];
    for (const [label, value] of rows) {
      const item = el(doc, "span", "rc-coop-kv");
      item.appendChild(el(doc, "b", "", label));
      item.appendChild(el(doc, "span", "", value));
      node.appendChild(item);
    }
    if (state.available) {
      const tone = state.enabled ? (state.active ? "ok" : "idle") : "warn";
      const label = state.enabled ? (state.active ? "협동 관측 활성" : "협동 관측 대기") : "협동 비활성 (Maestro 관제)";
      node.appendChild(el(doc, "span", "rc-coop-badge rc-coop-" + tone, label));
    }
    const stamp = "마지막 갱신 " + formatClock(nowMs) + (stale ? " · 갱신 지연" : "");
    node.appendChild(el(doc, "span", "rc-coop-kv rc-coop-stamp" + (stale ? " rc-coop-warn" : ""), stamp));
  }
  function renderSelect(doc, node, options, selected, emptyLabel) {
    if (!node) return;
    node.textContent = "";
    node.appendChild(el(doc, "option", "", emptyLabel)).value = "";
    for (const option of options) node.appendChild(el(doc, "option", "", option.label)).value = option.value;
    node.value = selected || "";
  }
  function renderCards(doc, node, state) {
    if (!node) return;
    const cards = buildCards(state); node.textContent = "";
    const local = el(doc, "div", "rc-coop-card rc-coop-card-local");
    local.appendChild(el(doc, "div", "rc-coop-card-title", "로컬 " + (cards.local.id || "로봇")));
    local.appendChild(el(doc, "div", "rc-coop-card-meta", "관측 이벤트 " + cards.local.events + "건 · 로컬 실행 기준"));
    node.appendChild(local);
    for (const peer of cards.peers) {
      const card = el(doc, "div", "rc-coop-card");
      card.appendChild(el(doc, "div", "rc-coop-card-title", "상대 " + peer.id));
      card.appendChild(el(doc, "div", "rc-coop-card-meta", "송신 " + peer.sent + " · 수신 " + peer.received + " · 최근 " + formatEventLabel(peer.lastEventType)));
      node.appendChild(card);
    }
  }
  function renderTimeline(doc, node, state, view) {
    if (!node) return;
    const highlight = latestExchangeKeys(state, HIGHLIGHT_EXCHANGES); node.textContent = "";
    for (const event of selectEvents(state, view)) {
      const direction = eventDirection(event), latest = highlight.indexOf(event.key) >= 0;
      const meta = statusMeta(event.status, { observed: event.observed, deliveryConfirmed: event.deliveryConfirmed });
      const showChip = Boolean(meta.label) && STATUS_EVENT_TYPES.indexOf(event.eventType) >= 0, descriptor = eventDescriptor(event);
      const item = el(doc, "li", "rc-coop-event rc-coop-" + direction + (latest ? " rc-coop-latest" : ""));
      item.setAttribute("aria-label", formatClock(event.timestamp * 1000) + " " + directionText(direction) + " " + formatEventLabel(event.eventType) + " · " + (meta.label || descriptor || "상세 없음"));
      const arrow = el(doc, "span", "rc-coop-arrow", directionArrow(direction));
      arrow.setAttribute("aria-hidden", "true");
      item.appendChild(el(doc, "time", "rc-coop-time", formatClock(event.timestamp * 1000))); item.appendChild(arrow);
      item.appendChild(el(doc, "span", "rc-coop-head", formatEventLabel(event.eventType) + " · " + directionText(direction)));
      if (showChip) item.appendChild(el(doc, "span", "rc-coop-status rc-coop-status-" + meta.tone, meta.icon + " " + meta.label));
      const body = [descriptor, event.content].filter(Boolean).join(" · ");
      if (body) item.appendChild(el(doc, "span", "rc-coop-body", body));
      if (latest) item.appendChild(el(doc, "span", "rc-coop-badge rc-coop-latest-badge", "최근 교환"));
      const keys = Object.keys(event.details || {});
      const details = el(doc, "details", "rc-coop-details");
      if (keys.length) {
        details.appendChild(el(doc, "summary", "", "상세 " + keys.length + "항목 (시크릿 마스킹)"));
        details.appendChild(el(doc, "pre", "", JSON.stringify(event.details, null, 2)));
        item.appendChild(details);
      }
      node.appendChild(item);
    }
  }
  function renderTasks(doc, node, state, view) {
    if (!node) return;
    const tasks = selectTasks(state, view); node.textContent = "";
    if (!tasks.length) node.appendChild(el(doc, "div", "rc-coop-empty", "표시할 태스크가 없습니다"));
    for (const task of tasks) {
      const meta = statusMeta(task.status, { observed: task.observed, deliveryConfirmed: task.deliveryConfirmed });
      const row = el(doc, "div", "rc-coop-task");
      for (const [cls, text] of [["rc-coop-task-id", task.taskId], ["rc-coop-status rc-coop-status-" + meta.tone, meta.label],
        ["rc-coop-task-peer", task.peerId ? "상대 " + task.peerId : "상대 미지정"], ["rc-coop-task-desc", task.description || task.result || "–"]]) row.appendChild(el(doc, "span", cls, text));
      node.appendChild(row);
    }
  }
  function render(doc, nodes, state, view, nowMs) {
    const stale = isStale(state, nowMs);
    renderSummary(doc, nodes.summary, state, stale, nowMs);
    if (nodes.notice) {
      nodes.notice.textContent = "";
      for (const line of noticeLines(state, stale, nowMs)) nodes.notice.appendChild(el(doc, "div", "rc-coop-notice rc-coop-" + line.tone, line.text));
    }
    renderSelect(doc, nodes.sessions, sessionOptions(state), view.sessionId, "전체 세션");
    renderSelect(doc, nodes.filters, FILTER_OPTIONS, view.filterType, "전체 이벤트");
    renderCards(doc, nodes.cards, state);
    if (nodes.gap) {
      const last = (state.gaps || []).slice(-1)[0];
      nodes.gap.hidden = !last; nodes.gap.textContent = !last ? "" : last.kind === "dropped" ? "관측 유실 " + last.missed + "건 (보관 범위 초과) — 일부 이벤트를 표시할 수 없습니다"
        : "건너뛴 구간 " + last.from + "~" + last.to + " (유실 " + last.missed + "건) — 보관 범위 밖 이벤트입니다";
    }
    renderTimeline(doc, nodes.timeline, state, view); renderTasks(doc, nodes.tasks, state, view);
  }
  /**
   * 협동 관측 패널을 붙인다. 스냅샷 렌더에는 HTTP 를 쓰지 않는다.
   * @param {{document?: Document, getToken?: () => string, fetchImpl?: Function, createObjectURL?: Function}} options
   */
  function mount(options) {
    const opts = options || {}, doc = opts.document || (typeof document !== "undefined" ? document : null);
    if (!doc) return null;
    const ids = { panel: "cooperation-panel", summary: "cooperation-summary", notice: "cooperation-notice", gap: "cooperation-gap",
      sessions: "cooperation-sessions", filters: "cooperation-filter", cards: "cooperation-cards", tasks: "cooperation-tasks",
      timeline: "cooperation-timeline", exportButton: "cooperation-export", fullscreenButton: "cooperation-fullscreen" };
    const nodes = {};
    for (const key of Object.keys(ids)) nodes[key] = doc.getElementById(ids[key]);
    if (!nodes.timeline) return null;
    const getToken = opts.getToken || (() => ""), fetchImpl = opts.fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
    const current = { state: emptyState(Date.now()), view: { sessionId: "", filterType: "" }, downloading: false };
    const setNotice = (text, tone) => {
      if (!nodes.notice) return;
      nodes.notice.className = "rc-coop-notice rc-coop-" + (tone || "info");
      nodes.notice.textContent = text || "";
    };
    const renderNow = () => render(doc, nodes, current.state, current.view, Date.now());
    /** app.js 의 renderSnapshot 에서 호출한다(추가 HTTP/타이머 없음). */
    function applyStatus(snapshot) {
      current.state = reduceSnapshot(current.state, snapshot && snapshot.state ? snapshot.state.cooperation : null, Date.now());
      renderNow();
    }
    async function download() {
      if (current.downloading) return;
      if (!fetchImpl) return setNotice("이 브라우저 환경에서는 내보내기를 지원하지 않습니다", "warn");
      current.downloading = true; setNotice("협동 관측 내보내기 준비 중…", "info");
      try {
        const token = getToken(), response = await fetchImpl("/api/cooperation/export", { headers: token ? { [HEADER_TOKEN]: token } : {} });
        if (response.status === 401 || response.status === 403) throw new Error("권한 없음: 읽기 토큰을 확인하세요. (" + response.status + ")");
        if (!response.ok) throw new Error("status " + response.status);
        const blob = await response.blob(), urlApi = opts.createObjectURL || global.URL || {};
        const url = urlApi.createObjectURL ? urlApi.createObjectURL(blob) : "";
        const link = doc.createElement("a");
        link.href = url; link.download = exportFileName(new Date());
        doc.body.appendChild(link); link.click(); link.remove();
        if (url && urlApi.revokeObjectURL) urlApi.revokeObjectURL(url);
        setNotice("내보내기 완료 (" + Math.max(1, Math.round((blob.size || 0) / 1024)) + " KB · 최근 관측 범위)", "ok");
      } catch (err) {
        setNotice("내보내기 실패: " + (err && err.message ? err.message : "알 수 없는 오류"), "warn");
      }
      current.downloading = false;
    }
    async function toggleFullscreen() {
      if (!nodes.panel) return;
      const pressed = (value) => nodes.fullscreenButton && nodes.fullscreenButton.setAttribute("aria-pressed", value ? "true" : "false");
      try {
        if (doc.fullscreenElement) {
          if (typeof doc.exitFullscreen === "function") await doc.exitFullscreen();
          pressed(false);
          return setNotice("전체 화면을 종료했습니다 (표시 전용)", "info");
        }
        if (typeof nodes.panel.requestFullscreen !== "function") throw new Error("브라우저가 전체 화면을 지원하지 않습니다");
        await nodes.panel.requestFullscreen();
        pressed(true);
        setNotice("전체 화면 표시 중 (표시 전용 · 로봇 제어 아님)", "ok");
      } catch (err) {
        pressed(false);
        setNotice("전체 화면 전환 실패: " + (err && err.message ? err.message : "브라우저가 거부했습니다"), "warn");
      }
    }
    for (const [key, field] of [["sessions", "sessionId"], ["filters", "filterType"]]) {
      if (nodes[key]) nodes[key].addEventListener("change", (event) => {
        current.view[field] = (event && event.target && event.target.value) || "";
        renderNow();
      });
    }
    if (nodes.exportButton) nodes.exportButton.addEventListener("click", () => download());
    if (nodes.fullscreenButton) nodes.fullscreenButton.addEventListener("click", () => toggleFullscreen());
    renderNow();
    return { applyStatus: applyStatus, renderNow: renderNow, download: download, toggleFullscreen: toggleFullscreen };
  }
  const api = { MAX_RENDERED_EVENTS, MAX_CACHED_EVENTS, MAX_SCAN_EVENTS, STALE_AFTER_MS, normalizeEvent, emptyState,
    normalizeCooperation, reduceSnapshot, isStale, formatStatusLabel, statusTone, formatEventLabel, eventDirection,
    selectEvents, selectTasks, sessionOptions, latestExchangeKeys, buildCards, sanitizeDetails,
    buildCooperationQuery, exportFileName, mount };
  global.RcCooperation = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
