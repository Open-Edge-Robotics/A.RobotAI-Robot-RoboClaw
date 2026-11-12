/* 목표 선택 모드 JS 테스트 대역 — DOM/지도/상태/응답 fixture. production 코드 아님. */
"use strict";

const GEOMETRY_ID = "a".repeat(64);
const ROBOT_ID = "robot-a";
const FRAME_ID = "map";

function makeElement(tag) {
  const node = {
    tagName: tag,
    id: "",
    className: "",
    textContent: "",
    title: "",
    hidden: false,
    disabled: false,
    value: "",
    width: 800,
    height: 400,
    clientWidth: 800,
    clientHeight: 400,
    attributes: {},
    dataset: {},
    children: [],
    listeners: {},
    style: {},
    classList: {
      _set: new Set(),
      add(...names) {
        for (const name of names) this._set.add(name);
      },
      remove(...names) {
        for (const name of names) this._set.delete(name);
      },
      toggle(name, force) {
        const on = force === undefined ? !this._set.has(name) : Boolean(force);
        if (on) this._set.add(name);
        else this._set.delete(name);
        return on;
      },
      contains(name) {
        return this._set.has(name);
      },
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    setAttribute(name, value) {
      this.attributes[name] = String(value);
      if (name === "id") this.id = String(value);
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attributes, name)
        ? this.attributes[name]
        : null;
    },
    addEventListener(name, fn) {
      (this.listeners[name] = this.listeners[name] || []).push(fn);
    },
    dispatch(name, event) {
      for (const fn of this.listeners[name] || []) fn(event || {});
    },
    click() {
      this.dispatch("click", { preventDefault() {} });
    },
    focus() {
      this.focused = true;
    },
    setPointerCapture() {},
    getBoundingClientRect() {
      return this.rect || { left: 0, top: 0, width: this.clientWidth, height: this.clientHeight };
    },
    getContext() {
      return fakeContext();
    },
  };
  Object.defineProperty(node, "innerHTML", {
    get() {
      return "";
    },
    set() {
      throw new Error("innerHTML 사용 금지");
    },
  });
  return node;
}

function fakeContext() {
  const calls = [];
  const ctx = {
    calls,
    strokeStyle: "",
    fillStyle: "",
    lineWidth: 1,
    save() {},
    restore() {},
    beginPath() {},
    arc(x, y, radius) {
      calls.push({ op: "arc", x, y, radius, strokeStyle: ctx.strokeStyle });
    },
    moveTo() {},
    lineTo() {},
    stroke() {
      calls.push({ op: "stroke", strokeStyle: ctx.strokeStyle });
    },
    fill() {},
    translate() {},
    rotate() {},
    setTransform() {},
    clearRect() {},
  };
  return ctx;
}

const GOAL_IDS = [
  "pose-mode",
  "map-canvas",
  "goal-mode",
  "goal-clear",
  "goal-send",
  "goal-preview",
  "goal-coords",
  "goal-frame",
  "goal-robot",
  "goal-status",
  "goal-progress",
  "goal-progress-wrap",
  "goal-marker-swatch",
  "goal-marker-pending-swatch",
];

function makeDocument(ids) {
  const registry = {};
  const doc = {
    documentElement: makeElement("html"),
    body: makeElement("body"),
    defaultView: {
      getComputedStyle() {
        return { color: "rgb(120, 200, 140)" };
      },
    },
    createElement(tag) {
      return makeElement(tag);
    },
    getElementById(id) {
      return registry[id] || null;
    },
    listeners: {},
    addEventListener(name, fn) {
      (this.listeners[name] = this.listeners[name] || []).push(fn);
    },
    dispatch(name, event) {
      for (const fn of this.listeners[name] || []) fn(event || {});
    },
  };
  for (const id of ids || GOAL_IDS) {
    const node = makeElement(id === "map-canvas" ? "canvas" : "div");
    node.id = id;
    registry[id] = node;
  }
  return { doc, nodes: registry };
}

function makeMap(extra) {
  const width = 20;
  const height = 10;
  const resolution = 0.5;
  const cells = new Uint8Array(width * height);
  return Object.assign(
    {
      width,
      height,
      resolution,
      origin: { x: 1, y: 2, yaw: 0 },
      frameId: FRAME_ID,
      topic: "/map",
      geometryId: GEOMETRY_ID,
      stampNs: 10,
      cells,
    },
    extra || {}
  );
}

function makeSnapshot(extra) {
  return Object.assign(
    {
      timestamp: 1700000000000,
      state: { agent_id: ROBOT_ID, navigation_frame: FRAME_ID },
      localization: {
        pose: { available: true, x: 2, y: 3, yaw: 0 },
        map: {
          available: true,
          topic: "/map",
          frame_id: FRAME_ID,
          geometry_id: GEOMETRY_ID,
        },
      },
      navigation_request: null,
    },
    extra || {}
  );
}

function makeRecord(extra) {
  return Object.assign(
    {
      request_id: "mg_req_1",
      robot_id: ROBOT_ID,
      status: "queued",
      message: "",
      target: { x: 5, y: 5, frame_id: FRAME_ID },
      geometry_id: GEOMETRY_ID,
      execution_started: false,
      cancel_confirmed: false,
      reason: "",
      progress: null,
      created_at: 1700000000,
      updated_at: 1700000000,
      can_submit: false,
    },
    extra || {}
  );
}

function makeFetch(handler) {
  const calls = [];
  async function fetchJson(url, token, options) {
    const body = options && options.body ? JSON.parse(options.body) : null;
    const call = { url, token, method: options && options.method, body };
    calls.push(call);
    return handler(call, calls.length);
  }
  return { calls, fetchJson };
}

function acceptedFetch() {
  return makeFetch((call) => ({
    ok: true,
    status: 202,
    data: {
      success: true,
      message: "도착했습니다", // task prose 는 도착 판정 근거가 아니다.
      request: makeRecord({
        request_id: call.body.request_id,
        status: "queued",
        target: {
          x: call.body.map_goal.x,
          y: call.body.map_goal.y,
          frame_id: call.body.map_goal.frame_id,
        },
      }),
    },
  }));
}

function mountPicker(goalPicker, options) {
  const opts = options || {};
  const made = makeDocument(opts.ids);
  const fetch = opts.fetch || acceptedFetch();
  const modes = [];
  const draws = [];
  const controller = goalPicker.mount({
    document: made.doc,
    getControlToken: () => "control-token",
    fetchJson: fetch.fetchJson,
    canvas: () => made.nodes["map-canvas"],
    draw: () => draws.push(Date.now()),
    onModeChange: (mode) => modes.push(mode),
    ...(opts.mount || {}),
  });
  return { controller, doc: made.doc, nodes: made.nodes, canvas: made.nodes["map-canvas"], fetch, modes, draws };
}

module.exports = {
  GEOMETRY_ID,
  ROBOT_ID,
  FRAME_ID,
  GOAL_IDS,
  makeElement,
  fakeContext,
  makeDocument,
  makeMap,
  makeSnapshot,
  makeRecord,
  makeFetch,
  acceptedFetch,
  mountPicker,
};
