"""시나리오/스냅샷 표시 모듈 JS 실행 테스트 — 순수 함수를 node 로 실행해 검증한다.

`scenario_view.js` / `snapshot_view.js` / `render_utils.js` 의 포맷터·리듀서를 실제로
호출해 (1) 테스트 케이스 행 템플릿이 이름·ID·타입·단계를 모두 이스케이프하는지,
(2) 실행 상태 배너/스윕 진행률/배터리 표기가 서버 응답 형태에 맞는지 고정한다.

node 가 없으면 건너뛴다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ._js_node import skip_or_fail_missing_node

pytestmark = pytest.mark.component

WEB = Path(__file__).resolve().parents[1] / "web"
ASSETS = WEB / "assets"
INDEX_HTML = WEB / "index.html"
APP_JS = ASSETS / "app.js"
RENDER_UTILS_JS = ASSETS / "render_utils.js"
SCENARIO_VIEW_JS = ASSETS / "scenario_view.js"
SNAPSHOT_VIEW_JS = ASSETS / "snapshot_view.js"
HARNESS = Path(__file__).resolve().parent / "js" / "scenario_view.js"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        skip_or_fail_missing_node("node 실행 파일이 없어 시나리오 표시 JS 테스트를 건너뜁니다")
    return node


@pytest.fixture(scope="module")
def view() -> dict:
    result = subprocess.run(
        [
            _node(),
            str(HARNESS),
            str(RENDER_UTILS_JS),
            str(SCENARIO_VIEW_JS),
            str(SNAPSHOT_VIEW_JS),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


# ---- 테스트 케이스 행 템플릿 (escapeHtml 적용) ------------------------------


class TestScenarioRowEscaping:
    def test_hostile_fields_are_escaped(self, view):
        html = view["rowHostile"]

        assert "<script>" not in html
        assert "<img" not in html
        assert "&lt;script&gt;" in html
        assert "&lt;img" in html

    def test_quotes_in_attributes_cannot_break_out(self, view):
        html = view["rowHostile"]

        # data-id/type/id 속성에 원본 큰따옴표가 남으면 속성이 끊긴다.
        assert 'data-id="tc_1&quot;&gt;&lt;img src=x onerror=alert(1)&gt;"' in html
        assert 'data-type="ping&quot; onmouseover=&quot;alert(1)"' in html
        assert 'onmouseover="' not in html
        assert 'id="tc-status-tc_1&quot;' in html

    def test_step_is_escaped_and_rows_are_marked(self, view):
        assert 'data-id="a" data-type="ping" checked' in view["rowEnabledFalse"]
        assert 'class="form-check-input tc-check"' in view["rowHostile"]

    def test_disabled_and_gated_rows_are_unchecked(self, view):
        assert "checked" not in view["rowDisabled"]
        assert "checked" not in view["rowNavigationGated"]
        assert "checked" not in view["rowManipulationGated"]
        assert 'data-id="d" data-type="navigation" checked' in view["rowNavigationAllowed"]

    def test_missing_step_falls_back_to_row_number(self, view):
        assert ">5</td>" in view["rowNoStep"]

    def test_missing_name_and_type_fall_back_to_defaults(self, view):
        assert ">g</span>" in view["rowNoNameType"]
        assert ">custom</span>" in view["rowNoNameType"]

    def test_empty_table_row_spans_all_columns(self, view):
        assert 'colspan="7"' in view["emptyRow"]
        assert "등록된 테스트 케이스가 없습니다" in view["emptyRow"]


# ---- 실행 상태 배너/진행률 --------------------------------------------------


class TestScenarioStatusRendering:
    def test_banner_maps_run_state_to_korean_label(self, view):
        assert "실행 중" in view["bannerRunning"]
        assert "text-bg-primary" in view["bannerRunning"]
        assert "완료" in view["bannerCompletedNoSummary"]
        assert "중단됨" in view["bannerCancelledWarnOnly"]

    def test_banner_counts_summary_values(self, view):
        banner = view["bannerRunning"]

        assert "PASS: <strong>1</strong>" in banner
        assert "FAIL: <strong>2</strong>" in banner
        assert "WARN: <strong>1</strong>" in banner
        assert "SKIP: <strong>1</strong>" in banner
        assert "(총 5개)" in banner

    def test_banner_uses_total_steps_when_summary_missing(self, view):
        assert "(총 3개)" in view["bannerCompletedNoSummary"]

    def test_banner_omits_total_when_unknown(self, view):
        assert "(총" not in view["bannerIdleZero"]
        assert "대기 중" in view["bannerIdleZero"]

    def test_unknown_state_shows_code_with_neutral_badge(self, view):
        assert "PAUSED" in view["bannerUnknownState"]
        assert "text-bg-secondary" in view["bannerUnknownState"]
        assert view["badge"]["unknown"] == {
            "label": "MIGRATING",
            "variant": "text-bg-secondary",
        }

    def test_summary_counts_prefers_summary_over_total_steps(self, view):
        assert view["counts"] == {"total": 4, "pass": 2, "fail": 1, "warn": 1, "skip": 0}
        assert view["countsNoSummary"]["total"] == 7
        assert view["countsNoSummary"]["pass"] == 0

    def test_progress_prefers_explicit_percent_and_clamps(self, view):
        progress = view["progress"]

        assert progress["explicit"] == 43
        assert progress["clampedHigh"] == 100
        assert progress["clampedLow"] == 0

    def test_progress_computes_from_finished_steps(self, view):
        assert view["progress"]["computed"] == 50
        assert view["progress"]["computedCurrentStep"] == 25
        assert view["progress"]["empty"] == 0

    def test_step_status_badges(self, view):
        status = view["stepStatus"]

        assert status["pass"] == '<span class="badge text-bg-success">PASS</span>'
        assert status["warn"] == '<span class="badge text-bg-warning">WARN</span>'
        assert status["skip"] == '<span class="badge text-bg-dark border">SKIP</span>'
        assert status["missing"] == '<span class="badge text-bg-secondary">PENDING</span>'

    def test_unknown_step_status_is_escaped(self, view):
        assert view["stepStatus"]["unknown"] == (
            '<span class="badge text-bg-secondary">&lt;IMG SRC=X&gt;</span>'
        )

    def test_duration_and_detail_text(self, view):
        assert view["duration"] == {"value": "1533 ms", "zero": "0 ms", "missing": "–"}
        assert view["detail"] == {"value": "상세", "empty": "–", "missing": "–"}


# ---- 출처/설정 입력 해석 ----------------------------------------------------


class TestScenarioSourceAndConfig:
    def test_source_labels(self, view):
        assert view["source"]["remote"] == "AI Config 서버"
        assert view["source"]["cache"] == "로컬 캐시"
        assert view["source"]["file"] == "시나리오 파일"
        assert view["source"]["fallbackBuiltin"] == "기본 내장"

    def test_title_and_description_defaults(self, view):
        assert view["title"]["explicit"] == "T"
        assert view["title"]["name"] == "N"
        assert view["title"]["missing"] == "테스트 시나리오"
        assert view["description"]["missing"] == "등록된 설명이 없습니다."

    def test_stored_config_is_normalized(self, view):
        assert view["config"]["parsed"] == {
            "url": " http://10.0.0.5:30180 ",
            "robot": "former",
            "environment": "kon47",
            "token": "t",
        }
        assert view["config"]["extraKeys"] == {
            "url": "u",
            "robot": "",
            "environment": "",
            "token": "",
        }

    def test_broken_stored_config_falls_back_to_empty(self, view):
        empty = {"url": "", "robot": "", "environment": "", "token": ""}
        assert view["config"]["broken"] == empty
        assert view["config"]["missing"] == empty

    def test_completeness_requires_url_robot_environment(self, view):
        assert view["complete"]["full"] is True
        assert view["complete"]["noToken"] is True
        assert view["complete"]["noEnv"] is False

    def test_sync_body_field_names_match_http_contract(self, view):
        assert view["syncBody"] == {
            "config_server_url": "http://10.0.0.5:30180",
            "robot_name": "former",
            "environment": "kon47",
            "device_token": "dev",
        }


# ---- 상태 스냅샷 포맷터 -----------------------------------------------------


class TestSnapshotFormatting:
    def test_state_badge(self, view):
        snapshot = view["snapshot"]

        assert snapshot["stateText"] == {"idle": "IDLE", "unknown": "–"}
        assert snapshot["stateClass"]["error"] == "badge rounded-pill text-bg-danger"
        assert snapshot["stateClass"]["unknown"] == "badge rounded-pill text-bg-secondary"

    def test_battery_text_accepts_ratio_percent_and_string(self, view):
        battery = view["snapshot"]["battery"]

        assert battery["ratio"] == "82%"
        assert battery["percent"] == "82%"
        assert battery["text"] == "85%"
        assert battery["unknownText"] == "UNKNOWN"
        assert battery["missing"] == "–"

    def test_battery_fill_is_clamped(self, view):
        battery = view["snapshot"]["battery"]

        assert battery["fillHigh"] == 100
        assert battery["fillLow"] == 0
        assert battery["fillMissing"] == 0

    def test_voltage_and_charging_text(self, view):
        assert view["snapshot"]["voltage"] == {"number": "24.50 V", "text": "24V"}
        assert view["snapshot"]["charging"] == {
            "charging": "충전 중",
            "discharging": "방전",
            "missing": "–",
        }

    def test_sensor_and_peer_chips(self, view):
        assert view["snapshot"]["sensors"] == [
            {"label": "camera", "ok": True, "text": "정상"},
            {"label": "map", "ok": False, "text": "미수신"},
        ]
        assert view["snapshot"]["peers"] == [
            {"label": "cloid01 (10.0.0.7:9090)", "ok": True, "text": "연결됨"},
            {"label": "cloid02", "ok": False, "text": "끊김"},
        ]
        assert view["snapshot"]["emptySensors"] == []

    def test_render_utils_formatting(self, view):
        utils = view["renderUtils"]

        assert utils["escape"] == "&lt;a href=&quot;x&quot;&gt;&amp;&#039;&lt;/a&gt;"
        assert utils["escapeNull"] == ""
        assert utils["configArray"] == "a, 1, true"
        assert utils["configBool"] == "false"
        assert utils["configNull"] == "–"
        assert utils["configNumber"] == "3.5"


class TestStatusBannerEscaping:
    def test_status_banner_escapes_unknown_state_label(self, view):
        """서버가 보낸 알 수 없는 state 문자열을 HTML 로 그대로 넣지 않는다."""
        banner = view["bannerHostileState"]

        lowered = banner.lower()
        # 태그로 해석되지 않고 텍스트로 이스케이프되어야 한다.
        assert "<img" not in lowered
        assert "&lt;img" in lowered
        # 배지 클래스 속성은 고정값 그대로여야 한다(주입으로 깨지지 않음).
        assert '<span class="badge text-bg-secondary">' in banner


# ---- 정적 배선 계약 ---------------------------------------------------------


class TestAppWiring:
    def test_display_modules_load_before_app(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        app = html.index("/assets/app.js")

        assert html.index("/assets/render_utils.js") < app
        assert html.index("/assets/snapshot_view.js") < app
        assert html.index("/assets/scenario_view.js") < app

    def test_app_delegates_formatting_to_display_modules(self):
        app = APP_JS.read_text(encoding="utf-8")

        for call in (
            "RcScenarioView.scenarioRowHtml(",
            "RcScenarioView.emptyScenarioRowHtml(",
            "RcScenarioView.statusBannerHtml(",
            "RcScenarioView.stepStatusHtml(",
            "RcScenarioView.progressPercent(",
            "RcRenderUtils.formatConfigValue(",
            "RcSnapshotView.batteryText(",
            "RcSnapshotView.sensorChips(",
        ):
            assert call in app, f"app.js 가 {call} 를 사용하지 않습니다"

    def test_row_template_has_a_single_source_of_truth(self):
        app = APP_JS.read_text(encoding="utf-8")
        scenario_view = SCENARIO_VIEW_JS.read_text(encoding="utf-8")

        # 체크박스 셀 마크업은 scenario_view.js 에만 있어야 한다(선택자 사용은 허용).
        assert 'class="form-check-input tc-check"' not in app
        assert 'class="form-check-input tc-check"' in scenario_view

    def test_escape_helper_has_a_single_definition(self):
        app = APP_JS.read_text(encoding="utf-8")

        assert "function escapeHtml" not in app
        assert "function escapeHtml" in RENDER_UTILS_JS.read_text(encoding="utf-8")
