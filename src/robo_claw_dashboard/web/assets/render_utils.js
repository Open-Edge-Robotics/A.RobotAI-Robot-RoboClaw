/* 화면 표시용 공용 포맷터 — DOM 의존 없는 순수 함수. app.js 가 로드해 쓴다. */
(function (global) {
  "use strict";

  /** HTML 로 삽입할 텍스트의 메타문자를 이스케이프한다(null/undefined 는 빈 문자열). */
  function escapeHtml(str) {
    if (str == null) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  /** 실행 설정 셀 표기 — 배열/불리언/null 을 사람이 읽는 문자열로 만든다. */
  function formatConfigValue(value) {
    if (value == null) return "–";
    if (Array.isArray(value)) return value.join(", ");
    if (typeof value === "boolean") return value ? "true" : "false";
    return String(value);
  }

  const api = { escapeHtml, formatConfigValue };
  global.RcRenderUtils = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
