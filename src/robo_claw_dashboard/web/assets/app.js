/* RoboClaw Dashboard — 상태 폴링 / 지도·위치 / 실행 설정 / 채팅·초기 위치 */
(function () {
  "use strict";

  const STATUS_INTERVAL_MS = 2000;
  const CONFIG_RETRY_MS = 8000;
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
      label: peer.name,
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
        data.frame_id + " · " + data.width + "×" + data.height + " · (" +
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

  function layout(canvas, map) {
    const worldW = map.width * map.resolution;
    const worldH = map.height * map.resolution;
    const scale = Math.min(canvas.width / worldW, canvas.height / worldH) * 0.98;
    state.scale = scale;
    state.offsetX = (canvas.width - worldW * scale) / 2;
    state.offsetY = (canvas.height - worldH * scale) / 2;
    return { worldW, worldH, scale };
  }

  function worldToCanvas(map, x, y) {
    const worldH = map.height * map.resolution;
    return {
      x: state.offsetX + ((x - map.origin.x) / map.resolution) * state.scale,
      y: state.offsetY + ((map.origin.y + worldH - y) / map.resolution) * state.scale,
    };
  }

  function canvasToWorld(map, px, py) {
    const worldH = map.height * map.resolution;
    return {
      x: map.origin.x + ((px - state.offsetX) / state.scale) * map.resolution,
      y: map.origin.y + worldH - ((py - state.offsetY) / state.scale) * map.resolution,
    };
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

    const layoutInfo = layout(canvas, map);
    const off = document.createElement("canvas");
    off.width = map.width;
    off.height = map.height;
    const offCtx = off.getContext("2d");
    const image = offCtx.createImageData(map.width, map.height);
    for (let index = 0; index < map.cells.length; index += 1) {
      const value = map.cells[index];
      const offset = index * 4;
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
    ctx.drawImage(
      off,
      state.offsetX,
      state.offsetY,
      layoutInfo.worldW * layoutInfo.scale,
      layoutInfo.worldH * layoutInfo.scale
    );
    ctx.strokeStyle = "#2a3446";
    ctx.strokeRect(
      state.offsetX,
      state.offsetY,
      layoutInfo.worldW * layoutInfo.scale,
      layoutInfo.worldH * layoutInfo.scale
    );

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
      setText(
        "config-summary",
        "에이전트 " + state.configRows.agent.length + "개 · 대시보드 " +
          state.configRows.dashboard.length + "개 · 마스킹 " + secretCount + "개"
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
    try {
      const { data, status } = await fetchJson("/api/status", token);
      if (status !== 200) throw new Error("status " + status);
      renderSnapshot(data);
      node.textContent = "연결됨";
      node.className = "small text-success";
    } catch (err) {
      node.textContent = "오류: " + err.message;
      node.className = "small text-danger";
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

  function startPolling() {
    const tokens = loadTokens();
    refreshStatus(tokens.readonly).then(() => {
      setInterval(() => refreshStatus(tokens.readonly), STATUS_INTERVAL_MS);
    });
  }

  window.addEventListener("resize", () => draw());

  document.addEventListener("DOMContentLoaded", () => {
    setupTokens();
    setupConfigPanel();
    setupChat();
    setupMapInteraction();
    startPolling();
    loadConfig().then(() => {
      // 에이전트 노드가 늦게 뜨는 경우를 한 번만 더 시도한다.
      if (state.configRows.agent.length === 0) {
        setTimeout(() => loadConfig(), CONFIG_RETRY_MS);
      }
    });
  });
})();
