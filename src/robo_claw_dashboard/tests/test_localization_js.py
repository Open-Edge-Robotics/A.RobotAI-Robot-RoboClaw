"""CLOiD 지도 선택/위치추정 패널 JS 회귀 테스트.

``localization.js`` 의 순수 함수를 node 로 실행해 상태 문구, 제어 안내, switch 본문,
스냅샷 병합 규칙을 고정한다. ``index.html``/``app.js``/``style.css`` 의 정적 배선도
함께 검증한다(node 가 없으면 건너뛴다).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ._js_node import skip_or_fail_missing_node

pytestmark = pytest.mark.component

WEB = Path(__file__).resolve().parents[1] / "web"
ASSETS = WEB / "assets"
LOCALIZATION_JS = ASSETS / "localization.js"
APP_JS = ASSETS / "app.js"
STYLE_CSS = ASSETS / "style.css"
INDEX_HTML = WEB / "index.html"

REQUIRED_IDS = [
    "map-select",
    "map-seed",
    "map-apply",
    "localization-status",
    "localization-result",
    "localization-panel",
    "robot-mode",
]

NODE_SCRIPT = """
const loc = require(process.argv[1]);
const ready = loc.formatStatus({
  available: true, label: "위치추정 준비 완료", active: false, failed: false,
});
const active = loc.formatStatus({
  available: true, label: "위치추정", active: true, failed: false,
});
const failed = loc.formatStatus({
  available: true, label: "위치추정 실패", active: false, failed: true,
});
const unavailable = loc.formatStatus({ available: false, label: "상태 미수신" });

const controlOk = loc.describeControl({
  control: { available: true, reason: "", busy: false },
  maps: [{ name: "m1", path: "/m1" }],
  maps_available: true,
});
const controlBlocked = loc.describeControl({
  control: { available: false, reason: "lio_fr_system_msgs 미설치", busy: false },
  maps: [],
  maps_available: false,
});
const controlBusy = loc.describeControl({
  control: { available: true, reason: "", busy: true },
  maps: [{ name: "m1", path: "/m1" }],
  maps_available: true,
});

const noSeed = loc.buildSwitchPayload({
  map: "konjiam_L_107", seed: false,
  pose: { x: 1.2, y: -0.4, yaw: 1.57 }, mode: "localization",
});
const withSeed = loc.buildSwitchPayload({
  map: "konjiam_L_107", seed: true,
  pose: { x: 1.2, y: -0.4, yaw: 1.57 }, mode: "localization",
});
const mapping = loc.buildSwitchPayload({
  map: "ignored", seed: false, pose: null, mode: "mapping",
});

const selectUsable = loc.isMapSelectUsable({
  maps: [{ name: "m1", path: "/m1" }, { name: "m2", path: "/m2" }],
});
const selectEmpty = loc.isMapSelectUsable({ maps: [] });
const selectMissing = loc.isMapSelectUsable(null);

const kept = loc.applySnapshot(
  {
    available: true,
    status: { available: true, label: "준비" },
    maps: [{ name: "m1", path: "/m1" }, { name: "m2", path: "/m2" }],
    current_map: "m2",
    control: { available: true },
    last_switch: null,
  },
  { selectedMap: "m2" }
);
const fallback = loc.applySnapshot(
  {
    available: true,
    status: { available: true, label: "준비" },
    maps: [{ name: "m1", path: "/m1" }, { name: "m2", path: "/m2" }],
    current_map: "missing",
    control: { available: true },
    last_switch: null,
  },
  { selectedMap: "gone" }
);

console.log(JSON.stringify({
  ready, active, failed, unavailable,
  controlOk, controlBlocked, controlBusy,
  noSeed, withSeed, mapping, kept, fallback,
  selectUsable, selectEmpty, selectMissing,
  sourceLio: loc.resolveInitialPoseSource({ initial_pose_source: "lio" }),
  sourceAmcl: loc.resolveInitialPoseSource({ initial_pose_source: "AMCL" }),
  sourceUnknown: loc.resolveInitialPoseSource({}),
  sourceStale: loc.applySnapshot(
    { maps: [], control: { available: false } },
    { initialPoseSource: "lio" }
  ).initialPoseSource,
  modeLio: loc.describeRobotMode("lio"),
  modeAmcl: loc.describeRobotMode("amcl"),
  modeUnknown: loc.describeRobotMode(""),
}));
"""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        skip_or_fail_missing_node("node 실행 파일이 없어 JS 위치추정 패널 테스트를 건너뜁니다")
    return node


@pytest.fixture(scope="module")
def observed() -> dict:
    result = subprocess.run(
        [_node(), "-e", NODE_SCRIPT, "--", str(LOCALIZATION_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_format_status_ready(observed):
    assert observed["ready"] == {"text": "위치추정 준비 완료", "tone": "ok"}


def test_format_status_active_marks_progress(observed):
    assert observed["active"]["tone"] == "active"
    assert "진행 중" in observed["active"]["text"]


def test_format_status_failed_uses_error_tone(observed):
    assert observed["failed"] == {"text": "위치추정 실패", "tone": "error"}


def test_format_status_unavailable_shows_label_with_warn_tone(observed):
    assert observed["unavailable"] == {"text": "상태 미수신", "tone": "warn"}


def test_describe_control_ready(observed):
    assert observed["controlOk"] == "지도 적용 가능"


def test_describe_control_unavailable_includes_reason(observed):
    assert observed["controlBlocked"] == "제어 불가: lio_fr_system_msgs 미설치"


def test_describe_control_busy_mentions_progress(observed):
    assert "진행 중" in observed["controlBusy"]


def test_switch_payload_without_seed_omits_pose(observed):
    body = observed["noSeed"]
    assert set(body) == {"mode", "map", "set_initial_pose", "confirm"}
    assert body["mode"] == "localization"
    assert body["map"] == "konjiam_L_107"
    assert body["set_initial_pose"] is False
    assert body["confirm"] is True


def test_switch_payload_with_seed_includes_pose(observed):
    body = observed["withSeed"]
    assert set(body) == {"mode", "map", "set_initial_pose", "x", "y", "yaw", "confirm"}
    assert body["set_initial_pose"] is True
    assert body["x"] == 1.2
    assert body["y"] == -0.4
    assert body["yaw"] == 1.57
    assert body["confirm"] is True


def test_switch_payload_mapping_omits_map_and_pose(observed):
    body = observed["mapping"]
    assert body["mode"] == "mapping"
    assert "map" not in body
    assert body["set_initial_pose"] is False
    assert body["confirm"] is True


def test_apply_snapshot_keeps_selection_when_still_listed(observed):
    assert observed["kept"]["selectedMap"] == "m2"
    assert [item["name"] for item in observed["kept"]["maps"]] == ["m1", "m2"]


def test_apply_snapshot_falls_back_to_first_map(observed):
    assert observed["fallback"]["selectedMap"] == "m1"


def test_map_select_is_usable_only_when_maps_exist(observed):
    # HTML 초기 상태가 disabled 이므로, 목록이 있을 때만 select 를 풀어야 한다.
    assert observed["selectUsable"] is True
    assert observed["selectEmpty"] is False
    assert observed["selectMissing"] is False


def test_initial_pose_source_is_normalized(observed):
    # AMCL 로봇과 CLOiD 로봇의 초기 위치 흐름을 화면에서 나누기 위한 값이다.
    assert observed["sourceLio"] == "lio"
    assert observed["sourceAmcl"] == "amcl"
    assert observed["sourceUnknown"] == ""


def test_initial_pose_source_is_not_dropped_by_empty_snapshot(observed):
    # 서버가 필드를 잠시 안 보내도 카드가 깜박이지 않도록 이전 값을 유지한다.
    assert observed["sourceStale"] == "lio"


def test_robot_mode_badge_describes_pose_semantics(observed):
    assert observed["modeLio"]["text"] == "CLOiD LIO 위치추정"
    assert "switch_mode" in observed["modeLio"]["title"]
    assert observed["modeAmcl"]["text"] == "AMCL 위치추정"
    assert "/initialpose" in observed["modeAmcl"]["title"]
    assert observed["modeUnknown"]["text"] == "위치추정 확인 중"


class TestStaticContract:
    def test_render_toggles_map_select_disabled(self):
        script = LOCALIZATION_JS.read_text(encoding="utf-8")
        assert "nodes.select.disabled = !usable" in script, (
            "지도 목록이 있어도 select 가 disabled 로 남으면 드롭다운이 열리지 않습니다"
        )
        assert "isMapSelectUsable" in script

    def test_required_element_ids_exist_in_html(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        declared = set(re.findall(r'id="([^"]+)"', html))
        missing = [element_id for element_id in REQUIRED_IDS if element_id not in declared]
        assert missing == [], f"index.html 에 없는 위치추정 패널 id: {missing}"

    def test_localization_script_loads_before_app(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert html.index("/assets/localization.js") < html.index("/assets/app.js")

    def test_app_polls_localization_with_readonly_and_ignores_failure(self):
        app = APP_JS.read_text(encoding="utf-8")
        assert 'fetchJson("/api/localization", loadTokens().readonly)' in app
        assert "state.localization.applySnapshot(data)" in app
        assert "RcLocalization.mount" in app
        assert "async function refreshLocalization" in app

    def test_amcl_robot_hides_lio_map_card(self):
        script = LOCALIZATION_JS.read_text(encoding="utf-8")
        assert 'nodes.panel.hidden = state.initialPoseSource === "amcl"' in script, (
            "AMCL 로봇에서 LIO 지도 카드가 그대로 보이면 사용할 수 없는 제어를 노출합니다"
        )

    def test_app_adapts_initial_pose_flow_to_robot_source(self):
        app = APP_JS.read_text(encoding="utf-8")
        assert "function initialPoseSource()" in app
        assert 'initialPoseSource() === "lio"' in app
        assert "위치추정을 다시 시작하므로" in app

    def test_status_regions_are_live(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        for element_id in ("localization-status", "localization-result"):
            pattern = r'id="' + element_id + r'"[^>]*aria-live="polite"'
            assert re.search(pattern, html), f"{element_id} 에 aria-live 가 없습니다"

    def test_theme_tokens_and_touch_sizes_in_css(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        for token in (
            "rc-loc-ok",
            "rc-loc-active",
            "rc-loc-warn",
            "rc-loc-error",
            "min-height: 48px",
            "var(--bs-success)",
            "var(--bs-danger)",
        ):
            assert token in css, f"style.css 에 {token} 이 없습니다"

    def test_localization_module_uses_text_insertion_only(self):
        script = LOCALIZATION_JS.read_text(encoding="utf-8")
        for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write", "outerHTML"):
            assert forbidden not in script, f"localization.js 가 {forbidden} 를 사용합니다"
