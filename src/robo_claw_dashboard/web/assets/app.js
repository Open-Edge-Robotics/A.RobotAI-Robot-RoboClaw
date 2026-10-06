/* RoboClaw Dashboard — /api/status 폴링 + /api/chat 전송 */
(function () {
  "use strict";

  const STATUS_INTERVAL_MS = 2000;
  const READONLY_KEY = "rc.dash.readonly";
  const CONTROL_KEY = "rc.dash.control";
  const HEADER_TOKEN = "X-RoboClaw-Token";

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

  function renderSnapshot(snap) {
    const agent = snap.agent || {};
    const tel = snap.telemetry || {};
    const battery = tel.battery || {};

    setText("agent-state", agent.state);
    setText("current-skill", agent.current_skill);
    setText("agent-message", agent.message);
    setText("battery", battery.percentage);
    setText("battery-voltage", battery.voltage);
    setText("battery-charging", battery.is_charging == null ? "–" : battery.is_charging ? "충전 중" : "방전");
    setText("last-updated", snap.timestamp ? new Date(snap.timestamp).toLocaleTimeString() : "–");

    renderList("sensor-health", Object.entries(snap.sensor_health || {}).map(([k, v]) => [k, v]).map((e) => [e[0], e[1] ? "정상" : "미수신"]));
    renderList("peers", (snap.peers || []).map((p) => [p.name, p.connected ? "연결됨" : "끊김"]));
  }

  function renderList(id, entries) {
    const node = el(id);
    if (!node) return;
    node.innerHTML = "";
    if (!entries.length) {
      const li = document.createElement("li");
      li.textContent = "없음";
      node.appendChild(li);
      return;
    }
    for (const [k, v] of entries) {
      const li = document.createElement("li");
      const key = document.createElement("span");
      key.className = "k";
      key.textContent = k;
      const val = document.createElement("span");
      val.textContent = v;
      li.append(key, val);
      node.appendChild(li);
    }
  }

  async function refreshStatus(token) {
    const node = el("connection");
    try {
      const { data, status } = await fetchJson("/api/status", token);
      if (status !== 200) throw new Error("status " + status);
      renderSnapshot(data);
      node.textContent = "연결됨";
      node.className = "connection ok";
    } catch (err) {
      node.textContent = "오류: " + err.message;
      node.className = "connection err";
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

  function setupChat() {
    const form = el("chat-form");
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const message = el("chat-input").value.trim();
      const result = el("chat-result");
      if (!message) return;
      const token = loadTokens().control;
      result.className = "result";
      result.textContent = "전송 중…";
      try {
        const { ok, status, data } = await fetchJson(
          "/api/chat",
          token,
          { method: "POST", body: JSON.stringify({ message }) }
        );
        if (!ok) {
          result.className = "result err";
          result.textContent = "실패(" + status + "): " + (data.error || "요청 실패");
          return;
        }
        result.className = "result ok";
        result.textContent = (data.success ? "성공: " : "실패: ") + (data.message || data.error || "완료");
      } catch (err) {
        result.className = "result err";
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

  document.addEventListener("DOMContentLoaded", () => {
    setupTokens();
    setupChat();
    startPolling();
  });
})();
