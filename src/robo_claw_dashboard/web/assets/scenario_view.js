/* 시나리오 검증 패널 표시 규칙 — 포맷터·리듀서만 모은 순수 함수 모음.
 *
 * app.js 는 이 함수들이 만든 문자열/DOM 값을 넣기만 하고, 서버 응답 필드 해석과
 * 배지 클래스 결정은 여기서 한다. 테스트 케이스 셀은 반드시 escapeHtml 을 거친다.
 */
(function (global) {
  "use strict";

  const utils =
    global.RcRenderUtils ||
    (typeof require === "function" ? require("./render_utils.js") : null);
  if (!utils) throw new Error("render_utils.js 를 먼저 로드해야 합니다");
  const escapeHtml = utils.escapeHtml;

  /** 시나리오 출처 코드 → 화면 라벨. 모르는 코드는 그대로 보여준다. */
  const SOURCE_LABELS = {
    remote: "AI Config 서버",
    cache: "로컬 캐시",
    file: "시나리오 파일",
    default: "내장 기본값",
    custom: "커스텀 JSON",
  };

  /** 실행 상태 코드 → 배너 라벨/배지 클래스. 모르는 상태는 코드를 그대로 쓴다. */
  const RUN_STATE_BADGES = {
    RUNNING: { label: "실행 중", variant: "text-bg-primary" },
    COMPLETED: { label: "완료", variant: "text-bg-success" },
    CANCELLED: { label: "중단됨", variant: "text-bg-warning" },
    FAILED: { label: "실패", variant: "text-bg-danger" },
    IDLE: { label: "대기 중", variant: "text-bg-secondary" },
  };

  /** 스텝 상태 코드 → 배지 클래스. */
  const STEP_STATUS_VARIANTS = {
    PASS: "text-bg-success",
    FAIL: "text-bg-danger",
    WARNING: "text-bg-warning",
    WARN: "text-bg-warning",
    SKIP: "text-bg-dark border",
    RUNNING: "text-bg-primary",
  };

  function sourceLabel(source) {
    return SOURCE_LABELS[source] || source;
  }

  function scenarioTitle(scenario) {
    const value = scenario || {};
    return value.title || value.name || "테스트 시나리오";
  }

  function scenarioDescription(scenario) {
    const value = scenario || {};
    return value.description || "등록된 설명이 없습니다.";
  }

  /** 브라우저(localStorage)에 저장된 서버 설정 문자열을 정규화한다(깨진 값은 빈 설정). */
  function parseScenarioConfig(raw) {
    try {
      const parsed = JSON.parse(raw || "{}");
      return {
        url: String(parsed.url || ""),
        robot: String(parsed.robot || ""),
        environment: String(parsed.environment || ""),
        token: String(parsed.token || ""),
      };
    } catch (_err) {
      return { url: "", robot: "", environment: "", token: "" };
    }
  }

  /** 원격 동기화에 필요한 값이 모두 있는지 확인한다(토큰은 선택). */
  function isScenarioConfigComplete(config) {
    const value = config || {};
    return Boolean(value.url && value.robot && value.environment);
  }

  /** /api/scenarios/sync POST 본문 — 디바이스 토큰은 본문으로만 보낸다. */
  function scenarioSyncBody(config) {
    return {
      config_server_url: config.url,
      robot_name: config.robot,
      environment: config.environment,
      device_token: config.token,
    };
  }

  /** 실행 옵션(주행/매니퓰레이션)에 따라 체크박스 기본 선택 여부를 정한다. */
  function isScenarioRowChecked(testCase, runNavigation, runManipulation) {
    const tc = testCase || {};
    if (tc.enabled === false) return false;
    if (tc.type === "navigation" && !runNavigation) return false;
    if (tc.type === "manipulation" && !runManipulation) return false;
    return true;
  }

  function emptyScenarioRowHtml() {
    return (
      '<td colspan="7" class="text-center text-body-secondary py-3">' +
      "등록된 테스트 케이스가 없습니다.</td>"
    );
  }

  /** 테스트 케이스 한 행의 셀 HTML. 이름/ID/타입/단계는 모두 이스케이프한다. */
  function scenarioRowHtml(testCase, index, runNavigation, runManipulation) {
    const tc = testCase || {};
    const stepNum = tc.step != null ? escapeHtml(tc.step) : String(index + 1);
    const safeName = escapeHtml(tc.name || tc.id);
    const safeId = escapeHtml(tc.id);
    const safeType = escapeHtml(tc.type || "custom");
    const isChecked = isScenarioRowChecked(tc, runNavigation, runManipulation);

    return `
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
  }

  /** 실행 상태 배너의 (라벨, 배지 클래스). */
  function runStateBadge(runState) {
    return RUN_STATE_BADGES[runState] || { label: runState, variant: "text-bg-secondary" };
  }

  /** 상태 응답에서 (총계, PASS/FAIL/WARN/SKIP) 를 뽑아낸다. */
  function summaryCounts(data) {
    const value = data || {};
    const summary = value.summary || {};
    return {
      total: summary.total != null ? summary.total : value.total_steps || 0,
      pass: summary.pass || 0,
      fail: summary.fail || 0,
      warn: summary.warning != null ? summary.warning : summary.warn || 0,
      skip: summary.skip || 0,
    };
  }

  /** 진행률(0~100 정수). progress_percent 가 없으면 단계 수로 계산한다. */
  function progressPercent(data) {
    const value = data || {};
    if (value.progress_percent != null) {
      return Math.min(100, Math.max(0, Math.round(value.progress_percent)));
    }
    if (value.total_steps > 0) {
      const summary = value.summary;
      const finished =
        (summary
          ? summary.pass + summary.fail + (summary.warning || 0) + summary.skip
          : value.current_step) || 0;
      return Math.min(100, Math.max(0, Math.round((finished / value.total_steps) * 100)));
    }
    return 0;
  }

  /** 실행 상태 배너 HTML(라벨 + PASS/FAIL/WARN/SKIP/총계). */
  function statusBannerHtml(data) {
    const value = data || {};
    const runState = String(value.state || value.status || "IDLE").toUpperCase();
    const badge = runStateBadge(runState);
    const counts = summaryCounts(value);
    return `
        <span class="badge ${badge.variant}">${escapeHtml(badge.label)}</span>
        <span class="ms-2">
          PASS: <strong>${counts.pass}</strong> · FAIL: <strong>${counts.fail}</strong> · WARN: <strong>${counts.warn}</strong> · SKIP: <strong>${counts.skip}</strong>
          ${counts.total ? ` (총 ${counts.total}개)` : ""}
        </span>
      `;
  }

  /** 스텝 상태 코드를 배지 클래스로 바꾼다. */
  function stepStatusVariant(status) {
    return STEP_STATUS_VARIANTS[String(status || "").toUpperCase()] || "text-bg-secondary";
  }

  /** 스텝 상태 셀 HTML — 코드는 이스케이프해 넣는다. */
  function stepStatusHtml(status) {
    const code = String(status || "PENDING").toUpperCase();
    return `<span class="badge ${stepStatusVariant(code)}">${escapeHtml(code)}</span>`;
  }

  /** 소요시간 표기(ms). 값이 없으면 "–". */
  function durationText(durationMs) {
    return durationMs != null ? durationMs + " ms" : "–";
  }

  /** 상세 내역 표기. 값이 없으면 "–". */
  function detailText(detail) {
    return detail || "–";
  }

  const api = {
    sourceLabel,
    scenarioTitle,
    scenarioDescription,
    parseScenarioConfig,
    isScenarioConfigComplete,
    scenarioSyncBody,
    isScenarioRowChecked,
    emptyScenarioRowHtml,
    scenarioRowHtml,
    runStateBadge,
    summaryCounts,
    progressPercent,
    statusBannerHtml,
    stepStatusVariant,
    stepStatusHtml,
    durationText,
    detailText,
  };
  global.RcScenarioView = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
