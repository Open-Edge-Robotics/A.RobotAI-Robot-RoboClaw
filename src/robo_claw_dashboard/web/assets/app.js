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
  const SCENARIO_CONFIG_KEY = "rc.scenario.config";
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
    dragPointerId: null,
    mapGoal: null,
    configRows: { agent: [], dashboard: [] },
    configLoaded: false,
    statusInFlight: false,
    pollDelay: STATUS_INTERVAL_MS,
    pollTimer: null,
    cooperation: null,
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

  function authHeaders(token) {
    return token ? { [HEADER_TOKEN]: token } : {};
  }

  async function fetchJson(url, token, options) {
    const resp = await fetch(url, {
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      ...(options || {}),
    });
    if (resp.status === 401 || resp.status === 403) {
      const err = new Error("권한 없음: 토큰을 확인하세요. (" + resp.status + ")");
      err.status = resp.status;
      throw err;
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
    renderChips("sensor-health", RcSnapshotView.sensorChips(snap.sensor_health));
    renderChips("peers", RcSnapshotView.peerChips(snap.peers));

    state.pose = localization.pose || null;
    renderPose();
    syncMap(localization.map || { available: false });
    if (state.mapGoal) state.mapGoal.applySnapshot(snap);
    // 협동 패널은 같은 스냅샷(state.cooperation)만 소비한다(추가 HTTP/타이머 없음).
    if (state.cooperation) state.cooperation.applyStatus(snap);
  }

  function renderStateBadge(value) {
    const badge = el("state-badge");
    if (!badge) return;
    badge.textContent = RcSnapshotView.stateBadgeText(value);
    badge.className = RcSnapshotView.stateBadgeClassName(value);
  }

  function renderBattery(battery) {
    const fill = el("battery-fill");
    if (fill) {
      const value = RcSnapshotView.batteryFillPercent(battery.percentage);
      fill.style.width = value + "%";
      fill.classList.toggle("bg-success", value >= 25);
      fill.classList.toggle("bg-danger", value < 25);
    }
    setText("battery", RcSnapshotView.batteryText(battery.percentage));
    setText("battery-voltage", RcSnapshotView.batteryVoltageText(battery.voltage));
    setText("battery-charging", RcSnapshotView.chargingText(battery.is_charging));
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
        if (state.mapGoal) state.mapGoal.applyMap(null);
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
        frameId: typeof data.frame_id === "string" ? data.frame_id : "",
        topic: typeof data.topic === "string" ? data.topic : "",
        geometryId: typeof data.geometry_id === "string" ? data.geometry_id : null,
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
      if (state.mapGoal) state.mapGoal.applyMap(state.map);
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
    if (state.mapGoal) {
      state.mapGoal.drawOverlay(ctx, map, {
        scale: state.scale,
        offsetX: state.offsetX,
        offsetY: state.offsetY,
      });
    }
  }

  function drawArrow(ctx, px, py, worldYaw, color, map) {
    const yaw = map ? RcMapGeometry.headingToCanvas(map, worldYaw) : worldYaw;
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
    drawArrow(ctx, point.x, point.y, pose.yaw, "#4f9cff", map);
  }

  function drawCandidate(ctx, map, candidate) {
    const point = worldToCanvas(map, candidate.x, candidate.y);
    ctx.beginPath();
    ctx.arc(point.x, point.y, 9, 0, Math.PI * 2);
    ctx.strokeStyle = "#3ecf7a";
    ctx.lineWidth = 2;
    ctx.stroke();
    drawArrow(ctx, point.x, point.y, candidate.yaw, "#3ecf7a", map);
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
      // 목표 선택 컨트롤러가 있으면 모드 전환(초기 위치/목표/보기)을 그쪽이 소유한다.
      if (state.mapGoal) return;
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
      state.dragPointerId = ev.pointerId;
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
      releaseDragCapture(canvas);
      draw();
      sendInitialPose(candidate);
    });

    canvas.addEventListener("pointercancel", () => {
      releaseDragCapture(canvas);
      draw();
    });
  }

  function releaseDragCapture(canvas) {
    if (state.dragPointerId != null && canvas && typeof canvas.releasePointerCapture === "function") {
      try {
        canvas.releasePointerCapture(state.dragPointerId);
      } catch (err) {
        // 이미 해제된 포인터는 무시한다.
      }
    }
    state.dragPointerId = null;
    state.drag = null;
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
      tr.dataset.search = (row.name + " " + RcRenderUtils.formatConfigValue(row.value)).toLowerCase();
      if (row.source === "default") tr.classList.add("rc-default");
      const nameCell = document.createElement("td");
      nameCell.textContent = row.name;
      const valueCell = document.createElement("td");
      valueCell.className = row.secret ? "rc-secret" : "";
      valueCell.textContent = RcRenderUtils.formatConfigValue(row.value);
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
        // 협동 패널도 이 실패 구간에서 "관측 지연"을 표시할 수 있게 다시 그린다(추가 타이머 없음).
        if (state.cooperation) state.cooperation.renderNow();
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
      // 상태 폴링이 끊기면 협동 패널이 마지막 관측을 "지연"으로 표시하게 한다.
      if (state.cooperation) state.cooperation.renderNow();
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

  function setupGoalPicker() {
    if (!window.RcGoalPicker) return;
    state.mapGoal = window.RcGoalPicker.mount({
      document: document,
      getControlToken: () => getControlToken(),
      fetchJson: fetchJson,
      canvas: () => el("map-canvas"),
      draw: () => draw(),
      onModeChange: (mode) => {
        state.poseMode = mode === "initial_pose";
        if (!state.poseMode) releaseDragCapture(el("map-canvas"));
        const poseButton = el("pose-mode");
        if (poseButton) poseButton.classList.toggle("active", state.poseMode);
        const canvas = el("map-canvas");
        if (canvas) canvas.classList.toggle("pose-mode", state.poseMode);
        draw();
      },
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

  function setupCooperationPanel() {
    if (!window.RcCooperation) return;
    state.cooperation = window.RcCooperation.mount({
      getToken: () => getReadToken(),
    });
  }

  // ---- 테스트 시나리오 검증 패널 ------------------------------------------

  async function loadScenario(refresh) {
    const token = getReadToken();
    const config = readScenarioConfigInputs();
    saveScenarioConfig(config);

    if (refresh && !RcScenarioView.isScenarioConfigComplete(config)) {
      setScenarioWarning("AI Config 서버 URL·로봇 이름·환경을 먼저 입력하세요. (서버 설정)");
      return;
    }

    const refreshBtn = el("scenario-refresh");
    if (refreshBtn) refreshBtn.disabled = true;
    try {
      let url = "/api/scenarios" + (refresh ? "?refresh=1" : "");
      const options = {};
      if (refresh) {
        // 디바이스 토큰이 본문에 실리므로 쿼리가 아닌 POST body 로만 전송한다.
        url = "/api/scenarios/sync";
        options.method = "POST";
        options.body = JSON.stringify(RcScenarioView.scenarioSyncBody(config));
      }
      const { ok, status, data } = await fetchJson(url, token, options);
      if (!ok) {
        setScenarioWarning(
          "시나리오를 가져오지 못했습니다 (" + status + "): " + (data.error || "요청 거부됨")
        );
        return;
      }
      renderScenario(data);
    } catch (err) {
      setScenarioWarning("시나리오 동기화 실패: " + err.message);
    } finally {
      if (refreshBtn) refreshBtn.disabled = false;
    }
  }

  function renderScenario(data) {
    state.currentScenario = data.scenario || data;
    const source = data.source || state.currentScenario.source || "기본 내장";
    const sourceLabel = RcScenarioView.sourceLabel(source);
    const stamp = new Date().toLocaleTimeString("ko-KR", { hour12: false });
    setText("scenario-source", "(" + sourceLabel + " · " + stamp + ")");
    setText("scenario-title", RcScenarioView.scenarioTitle(state.currentScenario));
    setText("scenario-desc", RcScenarioView.scenarioDescription(state.currentScenario));
    setScenarioWarning(data.warning || "");
    renderScenarioTable(state.currentScenario.test_cases || []);
  }

  function setScenarioWarning(message) {
    const node = el("scenario-warning");
    if (!node) return;
    if (!message) {
      node.classList.add("d-none");
      node.textContent = "";
      return;
    }
    node.classList.remove("d-none");
    node.textContent = message;
  }

  // ---- AI Config 서버 설정 입력 (브라우저에만 저장) ------------------------

  function readScenarioConfigInputs() {
    const value = (id) => (el(id) ? String(el(id).value || "").trim() : "");
    return {
      url: value("scenario-config-url"),
      robot: value("scenario-config-robot"),
      environment: value("scenario-config-env"),
      token: value("scenario-config-token"),
    };
  }

  function loadScenarioConfig() {
    return RcScenarioView.parseScenarioConfig(localStorage.getItem(SCENARIO_CONFIG_KEY) || "{}");
  }

  function saveScenarioConfig(config) {
    try {
      localStorage.setItem(SCENARIO_CONFIG_KEY, JSON.stringify(config));
    } catch (_err) {
      // 저장 불가(사생활 보호 모드 등) 상태에서도 화면 동작은 유지한다.
    }
  }

  function applyScenarioConfigToInputs(config) {
    const assign = (id, value) => {
      if (el(id)) el(id).value = value;
    };
    assign("scenario-config-url", config.url);
    assign("scenario-config-robot", config.robot);
    assign("scenario-config-env", config.environment);
    assign("scenario-config-token", config.token);
  }

  function toggleScenarioConfigPanel(forceOpen) {
    const panel = el("scenario-config-panel");
    const toggle = el("scenario-config-toggle");
    if (!panel) return;
    const open = forceOpen !== undefined ? forceOpen : panel.classList.contains("d-none");
    panel.classList.toggle("d-none", !open);
    if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function renderScenarioTable(testCases) {
    const tbody = el("scenario-tbody");
    if (!tbody) return;
    tbody.innerHTML = "";

    if (!testCases || !testCases.length) {
      const tr = document.createElement("tr");
      tr.innerHTML = RcScenarioView.emptyScenarioRowHtml();
      tbody.appendChild(tr);
      return;
    }

    const runNav = el("opt-run-navigation") ? el("opt-run-navigation").checked : false;
    const runManip = el("opt-run-manipulation") ? el("opt-run-manipulation").checked : false;

    testCases.forEach((tc, idx) => {
      const tr = document.createElement("tr");
      tr.id = "tc-row-" + tc.id;
      tr.dataset.tcId = tc.id;
      tr.innerHTML = RcScenarioView.scenarioRowHtml(tc, idx, runNav, runManip);
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
      statusBanner.innerHTML = RcScenarioView.statusBannerHtml(data);
    }

    const progressFill = document.querySelector("#scenario-progress-bar .progress-fill");
    const progressLabel = document.querySelector("#scenario-progress-bar .progress-label");
    if (progressFill && progressLabel) {
      const percent = RcScenarioView.progressPercent(data);
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
        statusCell.innerHTML = RcScenarioView.stepStatusHtml(res.status);
      }

      if (durationCell) {
        durationCell.textContent = RcScenarioView.durationText(res.duration_ms);
      }

      if (detailCell) {
        detailCell.textContent = RcScenarioView.detailText(res.detail);
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
    const storedConfig = loadScenarioConfig();
    applyScenarioConfigToInputs(storedConfig);
    if (!RcScenarioView.isScenarioConfigComplete(storedConfig)) toggleScenarioConfigPanel(true);

    const configToggle = el("scenario-config-toggle");
    if (configToggle) {
      configToggle.addEventListener("click", () => toggleScenarioConfigPanel());
    }

    [
      "scenario-config-url",
      "scenario-config-robot",
      "scenario-config-env",
      "scenario-config-token",
    ].forEach((id) => {
      const input = el(id);
      if (!input) return;
      input.addEventListener("change", () => {
        saveScenarioConfig(readScenarioConfigInputs());
      });
    });

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
            setScenarioWarning("");
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
    setupGoalPicker();
    setupMapInteraction();
    setupLogPanel();
    setupCooperationPanel();
    setupScenarios();
    // 저장된 서버 설정이 온전하면 첫 로드부터 원격 시나리오를 가져오고,
    // 아니면 내장 기본값을 보여준 뒤 설정 누락 사유를 경고로 안내한다.
    loadScenario(RcScenarioView.isScenarioConfigComplete(readScenarioConfigInputs()));
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
