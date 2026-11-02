/* RoboClaw Dashboard — 상태 폴링 / 지도·위치 / 실행 설정 / 채팅·초기 위치 */
(function () {
  "use strict";

  const STATUS_INTERVAL_MS = 3000;
  const STATUS_MAX_BACKOFF_MS = 15000;
  const CONFIG_RETRY_MS = 8000;
  const LOG_INTERVAL_MS = 3000;
  const LOG_LIMIT = 200;
  const READONLY_KEY = "rc.dash.readonly";
  const CONTROL_KEY = "rc.dash.control";
  const HEADER_TOKEN = "X-RoboClaw-Token";
  const UNKNOWN_CELL = 255;

  const state = {
    map: null, // { width, height, resolution, origin, frameId, stampNs, cells: Uint8Array }
    mapStampNs: null,
    mapLoading: false,
    pose: null,
    scale: 1,
    offsetX: 0,
    offsetY: 0,
    poseMode: false,
    drag: null,
    configRows: { agent: [], dashboard: [] },
    configLoaded: false,
    statusInFlight: false,
    pollDelay: STATUS_INTERVAL_MS,
    pollTimer: null,
    currentScenario: null,
    customScenarioObj: null,
    scenarioPollTimer: null,
  };

  function el(id) {
    return document.getElementById(id);
  }

  function loadTokens() {
    return {
      readonly: localStorage.getItem(READONLY_KEY) || "",
      control: localStorage.getItem(CONTROL_KEY) || "",
    };
  }

  function getReadToken() {
    const tokens = loadTokens();
    return tokens.readonly || tokens.control;
  }

  function getControlToken() {
    const tokens = loadTokens();
    return tokens.control;
  }

  function escapeHtml(str) {
    if (str == null) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function authHeaders(token) {
    return token ? { [HEADER_TOKEN]: token } : {};
  }

  async function fetchJson(url, token, options) {
    const resp = await fetch(url, {
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      ...(options || {}),
    });
    if (resp.status === 401 || resp.status === 403) {
      throw new Error("권한 없음: 토큰을 확인하세요. (" + resp.status + ")");
    }
    const data = await resp.json().catch(() => ({}));
    return { ok: resp.ok, status: resp.status, data };
  }

  function setText(id, value) {
    const node = el(id);
    if (node) node.textContent = value == null || value === "" ? "–" : value;
  }

  // ---- 상태 스냅샷 -------------------------------------------------------

  function renderSnapshot(snap) {
    const agent = snap.agent || {};
    const tel = snap.telemetry || {};
    const battery = tel.battery || {};
    const localization = snap.localization || {};
    const agentId = (snap.state || {}).agent_id;

    setText("agent-id", agentId);
    renderStateBadge(agent.state);
    setText("agent-state", agent.state);
    setText("current-skill", agent.current_skill);
    setText("agent-message", agent.message);
    setText("last-updated", snap.timestamp ? new Date(snap.timestamp).toLocaleTimeString() : "–");
    renderBattery(battery);
    renderChips("sensor-health", Object.entries(snap.sensor_health || {}).map(([name, ok]) => ({
      label: name,
      ok: Boolean(ok),
      text: ok ? "정상" : "미수신",
    })));
    renderChips("peers", (snap.peers || []).map((peer) => ({
      label: peer.host ? peer.name + " (" + peer.host + ":" + peer.port + ")" : peer.name,
      ok: Boolean(peer.connected),
      text: peer.connected ? "연결됨" : "끊김",
    })));

    state.pose = localization.pose || null;
    renderPose();
    syncMap(localization.map || { available: false });
  }

  const STATE_BADGE_VARIANTS = {
    IDLE: "text-bg-success",
    THINKING: "text-bg-primary",
    EXECUTING: "text-bg-warning",
    WAITING: "text-bg-secondary",
    ERROR: "text-bg-danger",
  };

  function renderStateBadge(value) {
    const badge = el("state-badge");
    if (!badge) return;
    const stateName = String(value || "UNKNOWN");
    badge.textContent = stateName === "UNKNOWN" ? "–" : stateName;
    badge.className =
      "badge rounded-pill " + (STATE_BADGE_VARIANTS[stateName] || "text-bg-secondary");
  }

  function renderBattery(battery) {
    const raw = battery.percentage;
    const percent = typeof raw === "number" ? raw * (raw <= 1 ? 100 : 1) : parseFloat(raw);
    const fill = el("battery-fill");
    if (fill) {
      const value = Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : 0;
      fill.style.width = value + "%";
      fill.classList.toggle("bg-success", value >= 25);
      fill.classList.toggle("bg-danger", value < 25);
    }
    setText(
      "battery",
      Number.isFinite(percent) ? percent.toFixed(0) + "%" : raw == null ? "–" : String(raw)
    );
    setText(
      "battery-voltage",
      typeof battery.voltage === "number" ? battery.voltage.toFixed(2) + " V" : battery.voltage
    );
    setText(
      "battery-charging",
      battery.is_charging == null ? "–" : battery.is_charging ? "충전 중" : "방전"
    );
  }

  function renderChips(id, entries) {
    const node = el(id);
    if (!node) return;
    node.innerHTML = "";
    if (!entries.length) {
      const span = document.createElement("span");
      span.className = "badge rounded-pill text-bg-dark border";
      span.textContent = "없음";
      node.appendChild(span);
      return;
    }
    for (const entry of entries) {
      const chip = document.createElement("span");
      chip.className =
        "badge rounded-pill " +
        (entry.ok
          ? "bg-success-subtle text-success-emphasis border border-success-subtle"
          : "bg-danger-subtle text-danger-emphasis border border-danger-subtle");
      chip.textContent = entry.label + " · " + entry.text;
      node.appendChild(chip);
    }
  }

  // ---- 지도/위치 ---------------------------------------------------------

  function renderPose() {
    const pose = state.pose;
    if (!pose || !pose.available) {
      setText("pose-xy", "위치 미수신");
      setText("pose-yaw", "–");
    } else {
      setText("pose-xy", pose.x.toFixed(2) + ", " + pose.y.toFixed(2) + " m");
      setText("pose-yaw", ((pose.yaw * 180) / Math.PI).toFixed(1) + "°");
    }
    draw();
  }

  async function syncMap(meta) {
    const status = el("map-status");
    if (!meta || !meta.available) {
      if (status) status.textContent = "지도 미수신";
      if (state.map) {
        state.map = null;
        state.mapStampNs = null;
        draw();
      }
      return;
    }
    if (status) {
      status.textContent =
        (meta.topic ? meta.topic + " · " : "") +
        meta.frame_id + " · " + meta.width + "×" + meta.height + " · " + meta.resolution + " m/cell";
    }
    if (state.mapStampNs === meta.stamp_ns) return;
    await loadMap();
  }

  async function loadMap() {
    if (state.mapLoading) return;
    state.mapLoading = true;
    const result = el("map-result");
    try {
      const { ok, data } = await fetchJson("/api/map", loadTokens().readonly);
      if (!ok || !data || !data.available) {
        if (result) {
          result.className = "small mt-2 text-danger";
          result.textContent = "지도를 불러오지 못했습니다";
        }
        return;
      }
      state.map = {
        width: data.width,
        height: data.height,
        resolution: data.resolution,
        origin: data.origin || { x: 0, y: 0, yaw: 0 },
        frameId: data.frame_id,
        stampNs: data.stamp_ns,
        cells: decodeCells(data.data),
      };
      state.mapStampNs = data.stamp_ns;
      setText(
        "map-info",
        (data.topic ? data.topic + " · " : "") + data.frame_id + " · " +
          data.width + "×" + data.height + " · (" +
          data.origin.x.toFixed(2) + ", " + data.origin.y.toFixed(2) + ")"
      );
      draw();
    } catch (err) {
      if (result) {
        result.className = "small mt-2 text-danger";
        result.textContent = "지도 오류: " + err.message;
      }
    } finally {
      state.mapLoading = false;
    }
  }

  function decodeCells(base64) {
    const binary = atob(base64 || "");
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  }

  function resizeCanvas() {
    const canvas = el("map-canvas");
    if (!canvas) return;
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(canvas.clientWidth * ratio));
    const height = Math.max(1, Math.round(canvas.clientHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
  }

  // 좌표 변환은 map_geometry.js 순수 함수가 담당한다(스케일은 px/m).
  function layout(canvas, map) {
    const view = RcMapGeometry.layout(canvas.width, canvas.height, map);
    state.scale = view.scale;
    state.offsetX = view.offsetX;
    state.offsetY = view.offsetY;
    return view;
  }

  function worldToCanvas(map, x, y) {
    return RcMapGeometry.worldToCanvas(
      { scale: state.scale, offsetX: state.offsetX, offsetY: state.offsetY },
      map,
      x,
      y
    );
  }

  function canvasToWorld(map, px, py) {
    return RcMapGeometry.canvasToWorld(
      { scale: state.scale, offsetX: state.offsetX, offsetY: state.offsetY },
      map,
      px,
      py
    );
  }

  function draw() {
    const canvas = el("map-canvas");
    if (!canvas) return;
    resizeCanvas();
    const ctx = canvas.getContext("2d");
    const map = state.map;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!map) {
      ctx.fillStyle = "#9aa7b8";
      ctx.font = "14px sans-serif";
      ctx.fillText("지도를 기다리는 중입니다", 16, 28);
      return;
    }

    layout(canvas, map); // state.scale/offset 갱신
    const off = document.createElement("canvas");
    off.width = map.width;
    off.height = map.height;
    const offCtx = off.getContext("2d");
    const image = offCtx.createImageData(map.width, map.height);
    // 지도 데이터는 row 0 이 월드 아래쪽(ROS OccupancyGrid 규약)이고 캔버스는 위가 0 이므로
    // 세로로 뒤집어 넣는다. 뒤집지 않으면 마커가 지도에 대해 상하 반대로 보인다.
    for (let index = 0; index < map.cells.length; index += 1) {
      const value = map.cells[index];
      const row = Math.floor(index / map.width);
      const col = index % map.width;
      const offset = ((map.height - 1 - row) * map.width + col) * 4;
      if (value === UNKNOWN_CELL) {
        image.data[offset] = 160;
        image.data[offset + 1] = 170;
        image.data[offset + 2] = 185;
        image.data[offset + 3] = 90;
      } else {
        const shade = 255 - Math.round((Math.min(value, 100) / 100) * 255);
        image.data[offset] = shade;
        image.data[offset + 1] = shade;
        image.data[offset + 2] = shade;
        image.data[offset + 3] = 255;
      }
    }
    offCtx.putImageData(image, 0, 0);

    ctx.imageSmoothingEnabled = false;
    const rect = RcMapGeometry.imageRect(
      { scale: state.scale, offsetX: state.offsetX, offsetY: state.offsetY },
      map
    );
    ctx.drawImage(off, rect.x, rect.y, rect.width, rect.height);
    ctx.strokeStyle = "#2a3446";
    ctx.strokeRect(rect.x, rect.y, rect.width, rect.height);

    drawPose(ctx, map);
    if (state.drag) drawCandidate(ctx, map, state.drag);
  }

  function drawArrow(ctx, px, py, yaw, color) {
    ctx.save();
    ctx.translate(px, py);
    ctx.rotate(-yaw);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(14, 0);
    ctx.lineTo(-8, 8);
    ctx.lineTo(-4, 0);
    ctx.lineTo(-8, -8);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  function drawPose(ctx, map) {
    const pose = state.pose;
    if (!pose || !pose.available) return;
    const point = worldToCanvas(map, pose.x, pose.y);
    ctx.beginPath();
    ctx.arc(point.x, point.y, 11, 0, Math.PI * 2);
    ctx.strokeStyle = "#4f9cff";
    ctx.lineWidth = 2;
    ctx.stroke();
    drawArrow(ctx, point.x, point.y, pose.yaw, "#4f9cff");
  }

  function drawCandidate(ctx, map, candidate) {
    const point = worldToCanvas(map, candidate.x, candidate.y);
    ctx.beginPath();
    ctx.arc(point.x, point.y, 9, 0, Math.PI * 2);
    ctx.strokeStyle = "#3ecf7a";
    ctx.lineWidth = 2;
    ctx.stroke();
    drawArrow(ctx, point.x, point.y, candidate.yaw, "#3ecf7a");
  }

  function eventToWorld(canvas, ev) {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    return canvasToWorld(
      state.map,
      (ev.clientX - rect.left) * scaleX,
      (ev.clientY - rect.top) * scaleY
    );
  }

  function setupMapInteraction() {
    const canvas = el("map-canvas");
    const modeButton = el("pose-mode");
    const refreshButton = el("map-refresh");
    if (!canvas) return;

    modeButton.addEventListener("click", () => {
      state.poseMode = !state.poseMode;
      state.drag = null;
      modeButton.classList.toggle("active", state.poseMode);
      canvas.classList.toggle("pose-mode", state.poseMode);
      if (!state.poseMode) draw();
    });

    refreshButton.addEventListener("click", () => {
      state.mapStampNs = null;
      loadMap();
    });

    canvas.addEventListener("pointerdown", (ev) => {
      if (!state.poseMode || !state.map) return;
      const world = eventToWorld(canvas, ev);
      state.drag = { x: world.x, y: world.y, yaw: 0 };
      canvas.setPointerCapture(ev.pointerId);
      draw();
    });

    canvas.addEventListener("pointermove", (ev) => {
      if (!state.drag) return;
      const world = eventToWorld(canvas, ev);
      const dx = world.x - state.drag.x;
      const dy = world.y - state.drag.y;
      if (Math.hypot(dx, dy) > state.map.resolution) {
        state.drag.yaw = Math.atan2(dy, dx);
      }
      draw();
    });

    canvas.addEventListener("pointerup", () => {
      if (!state.drag) return;
      const candidate = state.drag;
      state.drag = null;
      draw();
      sendInitialPose(candidate);
    });

    canvas.addEventListener("pointercancel", () => {
      state.drag = null;
      draw();
    });
  }

  async function sendInitialPose(candidate) {
    const result = el("map-result");
    const yawDeg = ((candidate.yaw * 180) / Math.PI).toFixed(1);
    const label =
      "초기 위치를 (" + candidate.x.toFixed(2) + ", " + candidate.y.toFixed(2) + "), " +
      yawDeg + "° 로 설정할까요?";
    if (!window.confirm(label)) return;

    if (result) {
      result.className = "small mt-2";
      result.textContent = "초기 위치 전송 중…";
    }
    try {
      const { ok, status, data } = await fetchJson("/api/initial_pose", loadTokens().control, {
        method: "POST",
        body: JSON.stringify({ x: candidate.x, y: candidate.y, yaw: candidate.yaw }),
      });
      if (!ok || !data.success) {
        if (result) {
          result.className = "small mt-2 text-danger";
          result.textContent =
            "실패(" + status + "): " + (data.error || "초기 위치 설정 실패");
        }
        return;
      }
      if (result) {
        result.className = "small mt-2 text-success";
        result.textContent =
          "초기 위치를 설정했습니다: (" + data.x.toFixed(2) + ", " + data.y.toFixed(2) + "), " +
          data.yaw_deg + "°";
      }
    } catch (err) {
      if (result) {
        result.className = "small mt-2 text-danger";
        result.textContent = "실패: " + err.message;
      }
    }
  }

  // ---- 실행 설정 패널 ----------------------------------------------------

  async function loadConfig() {
    const result = el("config-result");
    try {
      const { ok, data } = await fetchJson("/api/config", loadTokens().readonly);
      if (!ok) {
        if (result) {
          result.className = "small mt-2 text-danger";
          result.textContent = "설정을 불러오지 못했습니다";
        }
        return;
      }
      state.configLoaded = true;
      state.configRows = {
        agent: (data.agent || {}).parameters || [],
        dashboard: (data.dashboard || {}).parameters || [],
      };
      setText(
        "config-agent-node",
        (data.agent || {}).available ? "" : "· 파라미터 서비스 응답 없음"
      );
      setText("config-dashboard-node", "");
      const secretCount =
        state.configRows.agent.filter((row) => row.secret).length +
        state.configRows.dashboard.filter((row) => row.secret).length;
      const defaultCount =
        ((data.default_counts || {}).agent || 0) + ((data.default_counts || {}).dashboard || 0);
      setText(
        "config-summary",
        "에이전트 " + state.configRows.agent.length + "개 · 대시보드 " +
          state.configRows.dashboard.length + "개 · 마스킹 " + secretCount + "개 · " +
          "기본값 " + defaultCount + "개(흐림)"
      );
      renderConfigTable("config-agent-body", state.configRows.agent);
      renderConfigTable("config-dashboard-body", state.configRows.dashboard);
      applyConfigFilter(el("config-filter").value);
      if (!data.available && result) {
        result.className = "small mt-2 text-danger";
        result.textContent = "실행 설정을 읽지 못했습니다(노드 파라미터 서비스 미응답)";
      } else if (result) {
        result.className = "small mt-2";
        result.textContent = "";
      }
    } catch (err) {
      if (result) {
        result.className = "small mt-2 text-danger";
        result.textContent = "설정 오류: " + err.message;
      }
    }
  }

  function formatConfigValue(value) {
    if (value == null) return "–";
    if (Array.isArray(value)) return value.join(", ");
    if (typeof value === "boolean") return value ? "true" : "false";
    return String(value);
  }

  function renderConfigTable(id, rows) {
    const body = el(id);
    if (!body) return;
    body.innerHTML = "";
    if (!rows.length) {
      const tr = document.createElement("tr");
      tr.className = "rc-empty";
      const td = document.createElement("td");
      td.colSpan = 2;
      td.textContent = "표시할 설정이 없습니다";
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    for (const row of rows) {
      const tr = document.createElement("tr");
      tr.dataset.search = (row.name + " " + formatConfigValue(row.value)).toLowerCase();
      if (row.source === "default") tr.classList.add("rc-default");
      const nameCell = document.createElement("td");
      nameCell.textContent = row.name;
      const valueCell = document.createElement("td");
      valueCell.className = row.secret ? "rc-secret" : "";
      valueCell.textContent = formatConfigValue(row.value);
      tr.append(nameCell, valueCell);
      body.appendChild(tr);
    }
  }

  function applyConfigFilter(text) {
    const needle = String(text || "").trim().toLowerCase();
    for (const id of ["config-agent-body", "config-dashboard-body"]) {
      const body = el(id);
      if (!body) continue;
      for (const row of body.querySelectorAll("tr[data-search]")) {
        row.style.display = !needle || row.dataset.search.includes(needle) ? "" : "none";
      }
    }
  }

  // ---- 공통 --------------------------------------------------------------

  async function refreshStatus(token) {
    const node = el("connection");
    if (state.statusInFlight) return null;
    state.statusInFlight = true;
    try {
      const { data, status } = await fetchJson("/api/status", token);
      if (status === 429) {
        // 분당 요청 한도에 걸리면 화면을 멈추지 않고 간격을 늘려 재시도한다.
        node.textContent = "요청 한도 초과 — " + Math.round(state.pollDelay / 1000) + "초 후 재시도";
        node.className = "small text-warning";
        return Math.min(state.pollDelay * 2, STATUS_MAX_BACKOFF_MS);
      }
      if (status !== 200) throw new Error("status " + status);
      renderSnapshot(data);
      node.textContent = "연결됨";
      node.className = "small text-success";
      return STATUS_INTERVAL_MS;
    } catch (err) {
      node.textContent = "오류: " + err.message;
      node.className = "small text-danger";
      return Math.min(state.pollDelay * 2, STATUS_MAX_BACKOFF_MS);
    } finally {
      state.statusInFlight = false;
    }
  }

  function setupTokens() {
    const tokens = loadTokens();
    el("readonly-token").value = tokens.readonly;
    el("control-token").value = tokens.control;
    el("save-tokens").addEventListener("click", () => {
      localStorage.setItem(READONLY_KEY, el("readonly-token").value);
      localStorage.setItem(CONTROL_KEY, el("control-token").value);
      el("readonly-token").title = "저장됨";
    });
  }

  function setupConfigPanel() {
    const refresh = el("config-refresh");
    const filter = el("config-filter");
    if (refresh) refresh.addEventListener("click", () => loadConfig());
    if (filter) filter.addEventListener("input", (ev) => applyConfigFilter(ev.target.value));
  }

  function setupChat() {
    const form = el("chat-form");
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const message = el("chat-input").value.trim();
      const result = el("chat-result");
      if (!message) return;
      const token = loadTokens().control;
      result.className = "small mt-2";
      result.className = "small mt-2";
      result.textContent = "전송 중…";
      try {
        const { ok, status, data } = await fetchJson("/api/chat", token, {
          method: "POST",
          body: JSON.stringify({ message }),
        });
        if (!ok) {
          result.className = "small mt-2 text-danger";
          result.textContent = "실패(" + status + "): " + (data.error || "요청 실패");
          return;
        }
        result.className = "small mt-2 text-success";
        result.textContent =
          (data.success ? "성공: " : "실패: ") + (data.message || data.error || "완료");
      } catch (err) {
        result.className = "small mt-2 text-danger";
        result.textContent = "실패: " + err.message;
      }
    });
  }

  function setupLogPanel() {
    if (!window.RcLogs) return;
    window.RcLogs.mount({
      getToken: () => loadTokens().readonly,
      intervalMs: LOG_INTERVAL_MS,
      limit: LOG_LIMIT,
    });
  }

  // ---- 테스트 시나리오 검증 패널 ------------------------------------------

  async function loadScenario(refresh) {
    const token = getReadToken();
    try {
      const url = "/api/scenarios" + (refresh ? "?refresh=1" : "");
      const { ok, status, data } = await fetchJson(url, token);
      if (!ok) {
        setText("scenario-source", "(로드 실패)");
        setText("scenario-desc", "시나리오를 가져오지 못했습니다. (" + status + ")");
        return;
      }
      state.currentScenario = data.scenario || data;
      const source = data.source || state.currentScenario.source || "기본 내장";
      setText("scenario-source", "(" + source + ")");
      setText(
        "scenario-title",
        state.currentScenario.title || state.currentScenario.name || "테스트 시나리오"
      );
      setText("scenario-desc", state.currentScenario.description || "등록된 설명이 없습니다.");
      renderScenarioTable(state.currentScenario.test_cases || []);
    } catch (err) {
      setText("scenario-source", "(연결 대기)");
      setText("scenario-desc", "시나리오 서비스 연결 대기 중: " + err.message);
    }
  }

  function renderScenarioTable(testCases) {
    const tbody = el("scenario-tbody");
    if (!tbody) return;
    tbody.innerHTML = "";

    if (!testCases || !testCases.length) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        '<td colspan="7" class="text-center text-body-secondary py-3">등록된 테스트 케이스가 없습니다.</td>';
      tbody.appendChild(tr);
      return;
    }

    const runNav = el("opt-run-navigation") ? el("opt-run-navigation").checked : false;
    const runManip = el("opt-run-manipulation") ? el("opt-run-manipulation").checked : false;

    testCases.forEach((tc, idx) => {
      const tr = document.createElement("tr");
      tr.id = "tc-row-" + tc.id;
      tr.dataset.tcId = tc.id;

      let isChecked = tc.enabled !== false;
      if (tc.type === "navigation" && !runNav) isChecked = false;
      if (tc.type === "manipulation" && !runManip) isChecked = false;

      const stepNum = tc.step != null ? escapeHtml(tc.step) : String(idx + 1);
      const safeName = escapeHtml(tc.name || tc.id);
      const safeId = escapeHtml(tc.id);
      const safeType = escapeHtml(tc.type || "custom");

      tr.innerHTML = `
        <td class="text-center">
          <input type="checkbox" class="form-check-input tc-check" data-id="${safeId}" data-type="${safeType}" ${isChecked ? "checked" : ""}>
        </td>
        <td class="text-body-secondary">${stepNum}</td>
        <td>
          <div class="d-flex flex-column">
            <span class="fw-medium">${safeName}</span>
            <small class="text-body-secondary font-monospace">${safeId}</small>
          </div>
        </td>
        <td><span class="badge text-bg-dark border font-monospace">${safeType}</span></td>
        <td id="tc-status-${safeId}"><span class="badge text-bg-secondary">PENDING</span></td>
        <td id="tc-duration-${safeId}" class="font-monospace text-body-secondary">–</td>
        <td><div class="rc-tc-detail" id="tc-detail-${safeId}">–</div></td>
      `;
      tbody.appendChild(tr);
    });
  }

  function scheduleScenarioPoll() {
    if (state.scenarioPollTimer) clearTimeout(state.scenarioPollTimer);
    state.scenarioPollTimer = setTimeout(() => {
      refreshScenarioStatus();
    }, 1500);
  }

  async function refreshScenarioStatus(token) {
    const t = token !== undefined ? token : getReadToken();
    try {
      const resp = await fetch("/api/scenarios/status", {
        headers: { "Content-Type": "application/json", ...authHeaders(t) },
      });
      if (!resp.ok) return;
      const data = await resp.json().catch(() => ({}));
      renderScenarioStatus(data);
    } catch (_err) {
      // 주기적 폴링 실패 시 무시
    }
  }

  function renderScenarioStatus(data) {
    if (!data) return;
    const runState = (data.state || data.status || "IDLE").toUpperCase();
    const isRunning = runState === "RUNNING";

    const runBtn = el("scenario-run-btn");
    const cancelBtn = el("scenario-cancel-btn");
    if (runBtn) runBtn.disabled = isRunning;
    if (cancelBtn) cancelBtn.disabled = !isRunning;

    const hasReport = !!data.has_report;
    const dlMd = el("scenario-download-md");
    const dlJson = el("scenario-download-json");
    if (dlMd) dlMd.disabled = !hasReport;
    if (dlJson) dlJson.disabled = !hasReport;

    const statusBanner = el("scenario-run-status");
    if (statusBanner) {
      let stateLabel = runState;
      let badgeVariant = "text-bg-secondary";
      if (runState === "RUNNING") {
        stateLabel = "실행 중";
        badgeVariant = "text-bg-primary";
      } else if (runState === "COMPLETED") {
        stateLabel = "완료";
        badgeVariant = "text-bg-success";
      } else if (runState === "CANCELLED") {
        stateLabel = "중단됨";
        badgeVariant = "text-bg-warning";
      } else if (runState === "FAILED") {
        stateLabel = "실패";
        badgeVariant = "text-bg-danger";
      } else if (runState === "IDLE") {
        stateLabel = "대기 중";
        badgeVariant = "text-bg-secondary";
      }

      const summary = data.summary || {};
      const total = summary.total != null ? summary.total : data.total_steps || 0;
      const pass = summary.pass || 0;
      const fail = summary.fail || 0;
      const warn = summary.warning != null ? summary.warning : summary.warn || 0;
      const skip = summary.skip || 0;

      statusBanner.innerHTML = `
        <span class="badge ${badgeVariant}">${stateLabel}</span>
        <span class="ms-2">
          PASS: <strong>${pass}</strong> · FAIL: <strong>${fail}</strong> · WARN: <strong>${warn}</strong> · SKIP: <strong>${skip}</strong>
          ${total ? ` (총 ${total}개)` : ""}
        </span>
      `;
    }

    const progressFill = document.querySelector("#scenario-progress-bar .progress-fill");
    const progressLabel = document.querySelector("#scenario-progress-bar .progress-label");
    if (progressFill && progressLabel) {
      let percent = 0;
      if (data.progress_percent != null) {
        percent = Math.min(100, Math.max(0, Math.round(data.progress_percent)));
      } else if (data.total_steps > 0) {
        const finished =
          (data.summary
            ? data.summary.pass + data.summary.fail + (data.summary.warning || 0) + data.summary.skip
            : data.current_step) || 0;
        percent = Math.min(100, Math.max(0, Math.round((finished / data.total_steps) * 100)));
      }
      progressFill.style.width = percent + "%";
      progressLabel.textContent = percent + "%";
    }

    const results = data.steps || data.step_results || data.results || [];
    for (const res of results) {
      if (!res || !res.id) continue;
      const statusCell = el("tc-status-" + res.id);
      const durationCell = el("tc-duration-" + res.id);
      const detailCell = el("tc-detail-" + res.id);

      if (statusCell) {
        const s = (res.status || "PENDING").toUpperCase();
        let badgeVariant = "text-bg-secondary";
        if (s === "PASS") badgeVariant = "text-bg-success";
        else if (s === "FAIL") badgeVariant = "text-bg-danger";
        else if (s === "WARNING" || s === "WARN") badgeVariant = "text-bg-warning";
        else if (s === "SKIP") badgeVariant = "text-bg-dark border";
        else if (s === "RUNNING") badgeVariant = "text-bg-primary";
        statusCell.innerHTML = `<span class="badge ${badgeVariant}">${escapeHtml(s)}</span>`;
      }

      if (durationCell) {
        durationCell.textContent = res.duration_ms != null ? res.duration_ms + " ms" : "–";
      }

      if (detailCell) {
        detailCell.textContent = res.detail || "–";
      }
    }

    if (isRunning) {
      scheduleScenarioPoll();
    } else if (state.scenarioPollTimer) {
      clearTimeout(state.scenarioPollTimer);
      state.scenarioPollTimer = null;
    }
  }

  async function downloadReport(format) {
    const token = getReadToken();
    try {
      const resp = await fetch("/api/scenarios/report?format=" + encodeURIComponent(format), {
        headers: authHeaders(token),
      });
      if (!resp.ok) {
        throw new Error("보고서 다운로드 실패 (" + resp.status + ")");
      }
      const blob = await resp.blob();
      const disposition = resp.headers.get("Content-Disposition");
      let filename = "scenario_report." + (format === "json" ? "json" : "md");
      if (disposition && disposition.includes("filename=")) {
        const match = disposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
        if (match && match[1]) filename = match[1].replace(/['"]/g, "");
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert("다운로드 오류: " + err.message);
    }
  }

  function setupScenarios() {
    const refreshBtn = el("scenario-refresh");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", async () => {
        state.customScenarioObj = null;
        setText("scenario-source", "(동기화 중…)");
        await loadScenario(true);
      });
    }

    const fileInput = el("scenario-file-input");
    if (fileInput) {
      fileInput.addEventListener("change", (ev) => {
        const file = ev.target.files && ev.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (e) => {
          try {
            const json = JSON.parse(e.target.result);
            state.customScenarioObj = json;
            state.currentScenario = json.scenario || json;
            setText("scenario-source", "(커스텀 JSON: " + file.name + ")");
            setText(
              "scenario-title",
              state.currentScenario.title || state.currentScenario.name || file.name
            );
            setText("scenario-desc", state.currentScenario.description || "사용자 로컬 JSON 시나리오");
            renderScenarioTable(state.currentScenario.test_cases || []);
          } catch (err) {
            alert("JSON 파싱 실패: " + err.message);
          }
        };
        reader.readAsText(file);
        fileInput.value = "";
      });
    }

    const navOpt = el("opt-run-navigation");
    if (navOpt) {
      navOpt.addEventListener("change", () => {
        const checks = document.querySelectorAll('.tc-check[data-type="navigation"]');
        checks.forEach((cb) => {
          cb.checked = navOpt.checked;
        });
      });
    }

    const manipOpt = el("opt-run-manipulation");
    if (manipOpt) {
      manipOpt.addEventListener("change", () => {
        const checks = document.querySelectorAll('.tc-check[data-type="manipulation"]');
        checks.forEach((cb) => {
          cb.checked = manipOpt.checked;
        });
      });
    }

    const runBtn = el("scenario-run-btn");
    if (runBtn) {
      runBtn.addEventListener("click", async () => {
        const token = getControlToken();
        const checkedBoxes = Array.from(document.querySelectorAll(".tc-check:checked"));
        const enabledIds = checkedBoxes.map((cb) => cb.dataset.id);
        const runNav = el("opt-run-navigation") ? el("opt-run-navigation").checked : false;
        const runManip = el("opt-run-manipulation") ? el("opt-run-manipulation").checked : false;

        const body = {
          enabled_ids: enabledIds,
          run_navigation: runNav,
          run_manipulation: runManip,
        };
        if (state.customScenarioObj) {
          body.scenario = state.customScenarioObj;
        }

        runBtn.disabled = true;
        const statusBanner = el("scenario-run-status");
        if (statusBanner) {
          statusBanner.textContent = "상태: 시나리오 실행 요청 중…";
        }

        try {
          const { ok, status, data } = await fetchJson("/api/scenarios/run", token, {
            method: "POST",
            body: JSON.stringify(body),
          });
          if (!ok) {
            alert("실행 요청 실패 (" + status + "): " + (data.error || "요청 거부됨"));
            runBtn.disabled = false;
            return;
          }
          await refreshScenarioStatus();
          scheduleScenarioPoll();
        } catch (err) {
          alert("실행 오류: " + err.message);
          runBtn.disabled = false;
        }
      });
    }

    const cancelBtn = el("scenario-cancel-btn");
    if (cancelBtn) {
      cancelBtn.addEventListener("click", async () => {
        const token = getControlToken();
        cancelBtn.disabled = true;
        try {
          const { ok, status, data } = await fetchJson("/api/scenarios/cancel", token, {
            method: "POST",
            body: JSON.stringify({}),
          });
          if (!ok) {
            alert("중단 요청 실패 (" + status + "): " + (data.error || "요청 거부됨"));
          }
          await refreshScenarioStatus();
        } catch (err) {
          alert("중단 오류: " + err.message);
        }
      });
    }

    const dlMd = el("scenario-download-md");
    if (dlMd) {
      dlMd.addEventListener("click", () => downloadReport("md"));
    }

    const dlJson = el("scenario-download-json");
    if (dlJson) {
      dlJson.addEventListener("click", () => downloadReport("json"));
    }
  }

  function schedulePolling(token) {
    state.pollTimer = setTimeout(async () => {
      const nextDelay = await refreshStatus(token);
      state.pollDelay = nextDelay || STATUS_INTERVAL_MS;
      schedulePolling(token);
    }, state.pollDelay);
  }

  function startPolling() {
    const tokens = loadTokens();
    refreshStatus(tokens.readonly).then((delay) => {
      state.pollDelay = delay || STATUS_INTERVAL_MS;
      schedulePolling(tokens.readonly);
    });
  }

  window.addEventListener("resize", () => draw());

  document.addEventListener("DOMContentLoaded", () => {
    setupTokens();
    setupConfigPanel();
    setupChat();
    setupMapInteraction();
    setupLogPanel();
    setupScenarios();
    loadScenario();
    refreshScenarioStatus();
    startPolling();
    loadConfig().then(() => {
      // 에이전트 노드가 늦게 뜨는 경우를 한 번만 더 시도한다.
      if (state.configRows.agent.length === 0) {
        setTimeout(() => loadConfig(), CONFIG_RETRY_MS);
      }
    });
  });
})();
