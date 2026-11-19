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

  const api = { resultView, pendingView, isUnconfirmed };
  global.RcChatView = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
