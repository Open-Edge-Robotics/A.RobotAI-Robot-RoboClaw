// 채팅 전송 배선 계약 테스트용 node 하네스 — test_chat_send_js.py 가 실행한다.
// 가짜 폼/입력/결과 노드와 가짜 fetchJson 으로 chat_view.js bindSubmit 을 검증한다.
const TARGET = process.argv[2] || process.argv[1];
const chat = require(TARGET);
const out = {};

function makeNode(extra) {
  const node = Object.assign(
    {
      className: "",
      value: "",
      disabled: false,
      listeners: {},
      history: [],
      addEventListener(name, fn) {
        (this.listeners[name] = this.listeners[name] || []).push(fn);
      },
      dispatch(name, event) {
        for (const fn of this.listeners[name] || []) fn(event || {});
      },
    },
    extra || {}
  );
  let text = "";
  Object.defineProperty(node, "textContent", {
    get() {
      return text;
    },
    set(value) {
      text = value == null ? "" : String(value);
      node.history.push({ className: node.className, text: text });
    },
  });
  return node;
}

function recorder(results, probe) {
  const calls = [];
  const fetchJson = async (url, token, options) => {
    calls.push({ url, token, options, snapshot: probe.snapshot() });
    const step = results[calls.length - 1];
    if (!step) throw new Error("예상 밖 추가 호출");
    if (step.throws) throw step.throws;
    return step.result;
  };
  return { fetchJson, calls };
}

function mount(results) {
  const form = makeNode();
  const input = makeNode();
  const result = makeNode();
  const button = makeNode();
  const probe = {
    snapshot: () => ({ disabled: button.disabled, text: result.textContent, input: input.value }),
  };
  const rec = recorder(results, probe);
  const controller = chat.bindSubmit({
    form,
    input,
    result,
    sendButton: button,
    fetchJson: rec.fetchJson,
    getControlToken: () => "control-token",
    url: "/api/chat",
  });
  return { form, input, result, button, controller, calls: rec.calls };
}

async function scenarioSuccess() {
  const h = mount([{ result: { ok: true, status: 200, data: { success: true, message: "완료" } } }]);
  h.input.value = "  현재 상태 알려줘  ";
  await h.controller.submit();
  out.success = {
    calls: h.calls.length,
    url: h.calls[0].url,
    token: h.calls[0].token,
    method: h.calls[0].options.method,
    body: h.calls[0].options.body,
    duringFetch: h.calls[0].snapshot,
    text: h.result.textContent,
    className: h.result.className,
    inputAfter: h.input.value,
    buttonEnabledAfter: h.button.disabled === false,
    pendingText: h.result.history[0].text,
  };
}

async function scenarioFormSubmit() {
  const h = mount([{ result: { ok: true, status: 200, data: { success: true } } }]);
  h.input.value = "안녕";
  let prevented = false;
  h.form.dispatch("submit", {
    preventDefault() {
      prevented = true;
    },
  });
  // 제출 핸들러는 async 이므로 마이크로태스크를 한 번 비워 결과를 확정한다.
  await Promise.resolve();
  await Promise.resolve();
  out.formSubmit = { prevented, calls: h.calls.length, text: h.result.textContent };
}

async function scenarioEmptyInput() {
  const h = mount([]);
  h.input.value = "   ";
  let prevented = false;
  h.form.dispatch("submit", { preventDefault() { prevented = true; } });
  await Promise.resolve();
  out.emptyInput = { prevented, calls: h.calls.length, text: h.result.textContent, className: h.result.className };
}

async function scenarioDuplicateSubmit() {
  const h = mount([{ result: { ok: true, status: 200, data: { success: true } } }]);
  h.input.value = "한 번만";
  const first = h.controller.submit();
  const second = h.controller.submit(); // 전송 중 재제출 → 무시되어야 한다
  const inFlight = h.controller.isInFlight();
  await Promise.all([first, second]);
  out.duplicate = {
    calls: h.calls.length,
    inFlight,
    inFlightAfter: h.controller.isInFlight(),
    text: h.result.textContent,
  };
}

async function scenarioHttpFailure() {
  const h = mount([
    { result: { ok: false, status: 400, data: { error: "거부됨" } } },
  ]);
  h.input.value = "위험한 명령";
  await h.controller.submit();
  out.httpFailure = {
    text: h.result.textContent,
    className: h.result.className,
    inputAfter: h.input.value,
    buttonEnabledAfter: h.button.disabled === false,
  };
}

async function scenarioUnconfirmed() {
  const h = mount([
    {
      result: {
        ok: true,
        status: 200,
        data: { success: false, unknown: true, message: "태스크 결과 대기 타임아웃" },
      },
    },
  ]);
  h.input.value = "스캔해봐";
  await h.controller.submit();
  out.unconfirmed = { text: h.result.textContent, className: h.result.className, inputAfter: h.input.value };
}

async function scenarioThrown() {
  const h = mount([{ throws: new Error("권한 없음: 토큰을 확인하세요. (401)") }]);
  h.input.value = "안녕";
  await h.controller.submit();
  out.thrown = {
    text: h.result.textContent,
    className: h.result.className,
    inputAfter: h.input.value,
    buttonEnabledAfter: h.button.disabled === false,
  };
}

function scenarioMissingNodes() {
  out.missingForm = chat.bindSubmit({ input: makeNode(), fetchJson: async () => ({ ok: true }) });
  out.missingApi = typeof chat.bindSubmit === "function" && typeof chat.pendingView === "function";
}

(async () => {
  await scenarioSuccess();
  await scenarioFormSubmit();
  await scenarioEmptyInput();
  await scenarioDuplicateSubmit();
  await scenarioHttpFailure();
  await scenarioUnconfirmed();
  await scenarioThrown();
  scenarioMissingNodes();
  console.log(JSON.stringify(out));
})();
