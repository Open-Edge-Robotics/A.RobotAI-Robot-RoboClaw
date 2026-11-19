/* 채팅 결과 표시 규칙 — /api/chat 응답을 화면 문구로 바꾸는 순수 함수.
 *
 * 결과 대기 상한을 넘겨 돌아온 응답(`unknown: true`)은 실패가 아니다. goal 은
 * 취소되지 않으므로 에이전트가 계속 실행 중일 수 있고, 최종 결과는 상태/현재 스킬/
 * 로그로 확인해야 한다. 이 구분을 화면 문구에서 유지한다.
 */
(function (global) {
  "use strict";

  const BASE_CLASS = "small mt-2";
  const PENDING_TEXT = "전송 중…";
  const UNCONFIRMED_PREFIX = "결과 미확인";
  const UNCONFIRMED_DEFAULT_REASON = "태스크 결과를 확인하지 못했습니다";
  const UNCONFIRMED_HINT =
    "에이전트가 계속 실행 중일 수 있으니 상단 에이전트 상태·현재 스킬과 로그에서 확인하세요.";

  function classOf(kind) {
    return BASE_CLASS + " text-" + kind;
  }

  /** 결과 대기 시간이 지나 돌아온 응답인지 판정한다(성공 응답은 미확인으로 보지 않는다). */
  function isUnconfirmed(data) {
    return Boolean(data) && data.unknown === true && !data.success;
  }

  function textOf(value) {
    return String(value == null ? "" : value).trim();
  }

  /** /api/chat 응답 → { className, text }. 전송 결과 영역에 그대로 쓴다. */
  function resultView(data) {
    const payload = data || {};
    if (isUnconfirmed(payload)) {
      const reason = textOf(payload.message) || textOf(payload.error) || UNCONFIRMED_DEFAULT_REASON;
      return {
        className: classOf("warning"),
        text: UNCONFIRMED_PREFIX + ": " + reason + " — " + UNCONFIRMED_HINT,
      };
    }
    if (payload.success) {
      return { className: classOf("success"), text: "성공: " + (textOf(payload.message) || "완료") };
    }
    const reason = textOf(payload.message) || textOf(payload.error) || "완료";
    return { className: classOf("danger"), text: "실패: " + reason };
  }

  /** 전송 직후 표시(아직 응답 없음). */
  function pendingView() {
    return { className: BASE_CLASS, text: PENDING_TEXT };
  }

  /** 입력이 비어 있을 때의 안내(전송하지 않는다). */
  function emptyInputView() {
    return { className: classOf("warning"), text: "메시지를 입력하세요." };
  }

  /** HTTP 실패 응답 → { className, text }. 상태 코드를 함께 남겨 원인 파악을 돕는다. */
  function failureView(status, payload) {
    const body = payload || {};
    const reason = textOf(body.error) || textOf(body.message) || "요청 실패";
    return { className: classOf("danger"), text: "실패(" + String(status) + "): " + reason };
  }

  /** 예외(네트워크/토큰 오류) → { className, text }. */
  function errorView(err) {
    const reason = err && err.message ? err.message : String(err);
    return { className: classOf("danger"), text: "실패: " + reason };
  }

  /** 채팅 폼 전송 배선 — 전송 중복 차단, pending/결과 표시, 실패 시 입력 보존.
   *
   * `window.RcChatView` 를 쓰는 쪽(app.js)이 폼·입력·결과 노드와 fetch 구현을 넘긴다.
   * 전송이 진행 중일 때 다시 제출해도 HTTP 요청은 한 번만 나간다(에이전트 중복 실행 방지).
   *
   * @param {object} options form/input/result/sendButton/fetchJson/getControlToken/url
   * @returns {{ submit: function(): Promise<void>, isInFlight: function(): boolean }|null}
   */
  function bindSubmit(options) {
    const opts = options || {};
    const form = opts.form;
    const input = opts.input;
    if (!form || typeof form.addEventListener !== "function" || !input) return null;
    const result = opts.result || null;
    const sendButton = opts.sendButton || null;
    const fetchJson = opts.fetchJson;
    const getControlToken = opts.getControlToken || function () {
      return "";
    };
    const url = opts.url || "/api/chat";

    let inFlight = false;

    function render(view) {
      if (!result) return;
      result.className = view.className;
      result.textContent = view.text;
    }

    function setBusy(busy) {
      if (sendButton) sendButton.disabled = busy;
    }

    async function submit() {
      if (typeof fetchJson !== "function") return;
      const message = String(input.value == null ? "" : input.value).trim();
      if (!message) {
        render(emptyInputView());
        return;
      }
      if (inFlight) return; // 전송 중 재제출은 무시한다(중복 실행 방지)
      inFlight = true;
      setBusy(true);
      render(pendingView());
      try {
        const response = (await fetchJson(url, getControlToken(), {
          method: "POST",
          body: JSON.stringify({ message: message }),
        })) || {};
        if (!response.ok) {
          render(failureView(response.status, response.data));
          return;
        }
        render(resultView(response.data));
        // 접수된 메시지는 입력창에서 비운다. 실패/미확인이면 재전송할 수 있게 남긴다.
        input.value = "";
      } catch (err) {
        render(errorView(err));
      } finally {
        inFlight = false;
        setBusy(false);
      }
    }

    form.addEventListener("submit", function (event) {
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      submit();
    });

    return {
      submit: submit,
      isInFlight: function () {
        return inFlight;
      },
    };
  }

  const api = {
    resultView,
    pendingView,
    emptyInputView,
    failureView,
    errorView,
    isUnconfirmed,
    bindSubmit,
  };
  global.RcChatView = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
