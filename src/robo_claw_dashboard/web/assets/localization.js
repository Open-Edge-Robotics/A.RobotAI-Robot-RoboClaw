/* CLOiD 지도 선택 · 위치추정 시작 — /api/localization 스냅샷 표시와
 * /api/localization/switch 요청만 담당한다. 순수 함수(formatStatus/describeControl/
 * buildSwitchPayload/applySnapshot)는 DOM 없이 브라우저와 node 양쪽에서 동작하고,
 * DOM 의존은 mount() 안에만 둔다. 상태 폴링 타이머는 app.js 가 소유한다. */
(function (global) {
  "use strict";

  const DEFAULT_MODE = "localization";
  const READY_REASON = "지도 적용 가능";
  const TONE_CLASS = {
    ok: "rc-loc-ok",
    active: "rc-loc-active",
    warn: "rc-loc-warn",
    error: "rc-loc-error",
    muted: "rc-loc-muted",
  };
  const SWITCH_LABELS = { queued: "대기열", running: "실행 중" };

  function isFiniteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  /** 위치추정 상태 → 화면 문구와 색조. active/failed/미수신을 tone 으로 구분한다. */
  function formatStatus(status) {
    if (!status || typeof status !== "object") {
      return { text: "상태 미수신", tone: "muted" };
    }
    const label =
      typeof status.label === "string" && status.label.trim() ? status.label.trim() : "";
    if (status.available === false) {
      return { text: label || "상태 미수신", tone: "warn" };
    }
    if (status.failed === true) {
      return { text: label || "위치추정 실패", tone: "error" };
    }
    if (status.active === true) {
      const alreadyActive = /(진행|중)/.test(label);
      return { text: alreadyActive || !label ? label || "진행 중" : label + " · 진행 중", tone: "active" };
    }
    return { text: label || "위치추정 준비 완료", tone: "ok" };
  }

  /** 제어 가능/불가/진행 중을 사용자 문구 하나로 요약한다. */
  function describeControl(snapshot) {
    const snap = snapshot && typeof snapshot === "object" ? snapshot : {};
    const control = snap.control && typeof snap.control === "object" ? snap.control : {};
    if (control.available === false) {
      const reason =
        typeof control.reason === "string" && control.reason.trim()
          ? control.reason.trim()
          : "제어를 사용할 수 없습니다";
      return "제어 불가: " + reason;
    }
    if (control.busy === true) {
      return "진행 중 — 이전 지도 적용 요청이 끝날 때까지 기다립니다.";
    }
    const maps = Array.isArray(snap.maps) ? snap.maps : [];
    if (snap.maps_available === false || maps.length === 0) {
      return "지도 목록을 불러올 수 없습니다.";
    }
    return READY_REASON;
  }

  /** 선택한 지도/초기 위치/모드 → /api/localization/switch 본문. */
  function buildSwitchPayload(input) {
    const data = input && typeof input === "object" ? input : {};
    const mode = data.mode === "mapping" ? "mapping" : DEFAULT_MODE;
    const payload = { mode: mode };
    if (mode === DEFAULT_MODE) {
      payload.map = typeof data.map === "string" ? data.map.trim() : "";
    }
    const pose = data.pose && typeof data.pose === "object" ? data.pose : null;
    const seed = Boolean(data.seed) && Boolean(pose) && isFiniteNumber(pose.x) && isFiniteNumber(pose.y);
    payload.set_initial_pose = seed;
    if (seed) {
      payload.x = pose.x;
      payload.y = pose.y;
      payload.yaw = isFiniteNumber(pose.yaw) ? pose.yaw : 0;
    }
    payload.confirm = true;
    return payload;
  }

  function createState() {
    return {
      available: false,
      maps: [],
      mapRoot: "",
      mapsAvailable: false,
      currentMap: "",
      selectedMap: "",
      status: null,
      control: {},
      lastSwitch: null,
      initialPoseSource: "",
      signature: "",
    };
  }

  /**
   * 로봇의 초기 위치 적용 방식(`lio`|`amcl`|확인 중)을 스냅샷에서 읽는다(순수 함수).
   * 서버가 보내지 않으면 "" 을 돌려주고, UI 는 이전 표시를 유지한다.
   */
  function resolveInitialPoseSource(snapshot) {
    const snap = snapshot && typeof snapshot === "object" ? snapshot : {};
    const raw = typeof snap.initial_pose_source === "string" ? snap.initial_pose_source.trim().toLowerCase() : "";
    return raw === "lio" || raw === "amcl" ? raw : "";
  }

  /** 위치추정 방식 배지 문구(순수 함수). */
  function describeRobotMode(source) {
    if (source === "lio") {
      return {
        text: "CLOiD LIO 위치추정",
        title: "초기 위치는 현재 지도에 시드로 주입되며(/switch_mode), 위치추정이 다시 시작됩니다.",
      };
    }
    if (source === "amcl") {
      return {
        text: "AMCL 위치추정",
        title: "초기 위치는 /initialpose 발행으로 AMCL 파티클을 재배치합니다.",
      };
    }
    return { text: "위치추정 확인 중", title: "로봇의 초기 위치 적용 방식을 확인하는 중입니다." };
  }

  /**
   * 서버 스냅샷을 화면 상태로 병합한다(순수 함수).
   * 선택한 지도가 목록에 없으면 current_map, 그다음 첫 항목으로 되돌린다.
   */
  function applySnapshot(snapshot, state) {
    const snap = snapshot && typeof snapshot === "object" ? snapshot : {};
    const prev = state && typeof state === "object" ? state : createState();
    const maps = (Array.isArray(snap.maps) ? snap.maps : [])
      .filter((map) => map && typeof map.name === "string" && map.name.trim())
      .map((map) => ({
        name: map.name.trim(),
        path: typeof map.path === "string" ? map.path : "",
      }));
    const names = maps.map((map) => map.name);
    let selectedMap = typeof prev.selectedMap === "string" ? prev.selectedMap : "";
    if (names.indexOf(selectedMap) === -1) {
      const current = typeof snap.current_map === "string" ? snap.current_map : "";
      selectedMap = names.indexOf(current) !== -1 ? current : names.length ? names[0] : "";
    }
    const status =
      snap.status && typeof snap.status === "object" ? snap.status : prev.status || null;
    const control =
      snap.control && typeof snap.control === "object" ? snap.control : prev.control || {};
    const lastSwitch = Object.prototype.hasOwnProperty.call(snap, "last_switch")
      ? snap.last_switch || null
      : prev.lastSwitch || null;
    const initialPoseSource = resolveInitialPoseSource(snap) || prev.initialPoseSource || "";
    return {
      available: snap.available === true,
      maps: maps,
      mapRoot: typeof snap.map_root === "string" ? snap.map_root : prev.mapRoot || "",
      mapsAvailable: snap.maps_available !== false && maps.length > 0,
      currentMap: typeof snap.current_map === "string" ? snap.current_map : prev.currentMap || "",
      selectedMap: selectedMap,
      status: status,
      control: control,
      lastSwitch: lastSwitch,
      initialPoseSource: initialPoseSource,
      signature: names.join("\u0000"),
    };
  }

  /**
   * 지도 목록 select 를 열 수 있는지 판정한다(순수 함수).
   * HTML 초기 상태가 disabled 이므로, 목록이 준비되면 이 값으로 풀어 준다.
   */
  function isMapSelectUsable(snapshot) {
    const snap = snapshot && typeof snapshot === "object" ? snapshot : {};
    const maps = Array.isArray(snap.maps) ? snap.maps : [];
    return maps.length > 0;
  }

  /** queued/running 인 요청은 아직 끝나지 않았으므로 화면에 진행 중으로 표시한다. */
  function isSwitchActive(record) {
    return Boolean(record && (record.status === "queued" || record.status === "running"));
  }

  function confirmMessage(payload) {
    const lines = [
      "지도 '" + (payload.map || "") + "' 를 적용하고 위치추정(" + payload.mode + ")을 시작할까요?",
    ];
    if (payload.set_initial_pose) {
      lines.push(
        "초기 위치: x=" + Number(payload.x).toFixed(2) + " m, y=" + Number(payload.y).toFixed(2) +
          " m, yaw=" + Number(payload.yaw).toFixed(2) + " rad"
      );
    } else {
      lines.push("초기 위치는 설정하지 않습니다.");
    }
    lines.push("로봇이 정지 상태일 때만 진행하세요.");
    return lines.join("\n");
  }

  /**
   * 지도 선택 패널을 화면에 붙인다. 스냅샷은 app.js 의 기존 폴링이
   * applySnapshot() 으로 밀어 넣는다(추가 타이머 없음).
   */
  function mount(options) {
    const opts = options || {};
    const doc = opts.document || (typeof document !== "undefined" ? document : null);
    if (!doc) return null;
    const byId = (id) => doc.getElementById(id);
    const getControlToken =
      typeof opts.getControlToken === "function" ? opts.getControlToken : () => "";
    const fetchJson = typeof opts.fetchJson === "function" ? opts.fetchJson : null;
    const getPose = typeof opts.getPose === "function" ? opts.getPose : () => null;
    const confirmFn =
      typeof opts.confirm === "function"
        ? opts.confirm
        : (message) => (typeof global.confirm === "function" ? global.confirm(message) : false);

    const nodes = {
      panel: byId("localization-panel"),
      robotMode: byId("robot-mode"),
      select: byId("map-select"),
      seed: byId("map-seed"),
      apply: byId("map-apply"),
      status: byId("localization-status"),
      result: byId("localization-result"),
      help: byId("localization-help"),
    };
    if (!nodes.apply) return null;

    let state = createState();
    let submitting = false;

    function usablePose() {
      const pose = getPose();
      if (!pose || pose.available === false) return null;
      if (!isFiniteNumber(pose.x) || !isFiniteNumber(pose.y)) return null;
      return { x: pose.x, y: pose.y, yaw: isFiniteNumber(pose.yaw) ? pose.yaw : 0 };
    }

    function gate() {
      const reason = describeControl({
        control: state.control,
        maps: state.maps,
        maps_available: state.mapsAvailable,
      });
      return reason === READY_REASON ? { ok: true, reason: "" } : { ok: false, reason: reason };
    }

    function setResult(text, cls) {
      if (!nodes.result) return;
      nodes.result.className = "small mt-1 " + (cls || "text-body-secondary");
      nodes.result.textContent = text || "";
    }

    function renderMaps() {
      if (!nodes.select) return;
      if (nodes.select.dataset.signature !== state.signature) {
        nodes.select.textContent = "";
        if (!state.maps.length) {
          const empty = doc.createElement("option");
          empty.value = "";
          empty.textContent = "지도 없음";
          nodes.select.appendChild(empty);
        } else {
          for (const map of state.maps) {
            const option = doc.createElement("option");
            option.value = map.name;
            option.textContent = map.name;
            nodes.select.appendChild(option);
          }
        }
        nodes.select.dataset.signature = state.signature;
      }
      nodes.select.value = state.selectedMap || "";
    }

    function renderStatus() {
      if (!nodes.status) return;
      if (!state.status && !isSwitchActive(state.lastSwitch)) return;
      const info = formatStatus(state.status);
      let text = info.text;
      let tone = info.tone;
      if (isSwitchActive(state.lastSwitch)) {
        const label = SWITCH_LABELS[state.lastSwitch.status] || "진행 중";
        text = (text ? text + " · " : "") + "요청 " + label + " — 진행 중";
        tone = "active";
      }
      nodes.status.textContent = text;
      nodes.status.className = "small " + (TONE_CLASS[tone] || TONE_CLASS.muted);
    }

    function renderRobotMode() {
      const mode = describeRobotMode(state.initialPoseSource);
      if (nodes.robotMode) {
        nodes.robotMode.textContent = mode.text;
        nodes.robotMode.title = mode.title;
      }
      // LIO 를 쓰지 않는 로봇(AMCL)에는 지도 선택/위치추정 카드를 숨긴다.
      // 지도 적용(/switch_mode) 자체가 없는 로봇에서 빈 카드와 "상태 미수신" 만 보이던 문제를 막는다.
      if (nodes.panel) nodes.panel.hidden = state.initialPoseSource === "amcl";
    }

    function render() {
      if (nodes.select) {
        // HTML 초기 상태(disabled)를 그대로 두면 목록이 있어도 드롭다운이 열리지 않는다.
        const usable = isMapSelectUsable(state);
        nodes.select.disabled = !usable;
        nodes.select.title = usable
          ? "적용할 지도를 선택합니다."
          : "사용할 수 있는 지도 목록이 없습니다.";
      }
      renderMaps();
      renderRobotMode();
      const pose = usablePose();
      if (nodes.seed) {
        if (!pose) {
          nodes.seed.checked = false;
          nodes.seed.disabled = true;
          nodes.seed.title = "현재 로봇 위치(pose)를 확인할 수 없어 초기 위치를 설정할 수 없습니다.";
        } else {
          nodes.seed.disabled = false;
          nodes.seed.title = "현재 로봇 위치를 초기 위치로 함께 보냅니다.";
        }
      }
      const check = gate();
      if (nodes.apply) {
        nodes.apply.disabled = !check.ok || submitting;
        nodes.apply.title = submitting
          ? "요청을 전송하는 중입니다…"
          : check.ok
            ? "선택한 지도를 적용하고 위치추정을 시작합니다."
            : check.reason;
        if (nodes.apply.disabled) nodes.apply.setAttribute("aria-disabled", "true");
        else nodes.apply.removeAttribute("aria-disabled");
      }
      if (nodes.help) {
        if (!check.ok) nodes.help.textContent = check.reason;
        else if (!pose) nodes.help.textContent = "현재 로봇 위치를 확인할 수 없어 초기 위치 설정은 사용할 수 없습니다.";
        else nodes.help.textContent = "";
      }
      renderStatus();
    }

    function selectedMap() {
      if (nodes.select && nodes.select.value) return nodes.select.value;
      return state.selectedMap || "";
    }

    async function submit() {
      if (submitting) return;
      const check = gate();
      if (!check.ok) {
        setResult(check.reason, "text-danger");
        return;
      }
      const pose = usablePose();
      if (nodes.seed && nodes.seed.checked && !pose) {
        setResult("현재 로봇 위치를 확인할 수 없어 초기 위치를 설정할 수 없습니다.", "text-danger");
        return;
      }
      const map = selectedMap();
      if (!map) {
        setResult("적용할 지도를 선택하세요.", "text-danger");
        return;
      }
      const payload = buildSwitchPayload({
        map: map,
        seed: Boolean(nodes.seed && nodes.seed.checked && pose),
        pose: pose,
        mode: DEFAULT_MODE,
      });
      if (!confirmFn(confirmMessage(payload))) return;
      if (!fetchJson) {
        setResult("요청을 보낼 수 없습니다.", "text-danger");
        return;
      }
      submitting = true;
      setResult("지도 적용을 요청하는 중입니다…", "text-body-secondary");
      render();
      try {
        const { ok, status, data } = await fetchJson("/api/localization/switch", getControlToken(), {
          method: "POST",
          body: JSON.stringify(payload),
        });
        const body = data || {};
        if (status === 202 && ok && body.success !== false) {
          if (body.request) state = { ...state, lastSwitch: body.request };
          setResult(
            "접수됨 — 진행 상황은 상태 표시로 확인하세요. (202 접수는 완료가 아닙니다.)",
            "text-success"
          );
        } else {
          setResult(
            "실패(" + status + "): " + (body.error || "요청이 거부되었습니다."),
            "text-danger"
          );
        }
      } catch (err) {
        setResult("실패: " + (err && err.message ? err.message : "네트워크 오류"), "text-danger");
      } finally {
        submitting = false;
        render();
      }
    }

    if (nodes.select) {
      nodes.select.addEventListener("change", () => {
        state = { ...state, selectedMap: nodes.select.value || "" };
        render();
      });
    }
    if (nodes.seed) nodes.seed.addEventListener("change", () => render());
    if (nodes.apply) nodes.apply.addEventListener("click", () => submit());

    render();
    return {
      applySnapshot: (snapshot) => {
        state = applySnapshot(snapshot, state);
        render();
      },
      render: render,
      getState: () => state,
    };
  }

  const api = {
    formatStatus,
    describeControl,
    isMapSelectUsable,
    buildSwitchPayload,
    createState,
    applySnapshot,
    isSwitchActive,
    resolveInitialPoseSource,
    describeRobotMode,
    mount,
  };
  global.RcLocalization = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
