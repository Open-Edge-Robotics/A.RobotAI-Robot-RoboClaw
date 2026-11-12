"""목표 선택 모드 프런트엔드 계약 테스트.

- ``goal_picker.js`` 순수 함수/리듀서/컨트롤러를 node 로 실행해 canonical 메시지,
  map_goal 본문, 자유 셀 검증, 모드 배타성, 202/409/unknown 처리, 키보드 동작을 검증한다.
- ``index.html`` 이 요구하는 element id/스크립트 순서/접근성 속성을 고정한다.
- 목표 선택 모드가 skill/Nav2/초기 위치 endpoint 를 직접 호출하지 않는지 확인한다.

node 가 없으면 JS 실행 테스트만 건너뛴다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.component

WEB = Path(__file__).resolve().parents[1] / "web"
ASSETS = WEB / "assets"
INDEX_HTML = WEB / "index.html"
GOAL_JS = ASSETS / "goal_picker.js"
GEOMETRY_JS = ASSETS / "map_geometry.js"
APP_JS = ASSETS / "app.js"
STYLE_CSS = ASSETS / "style.css"
JS_TESTS = Path(__file__).resolve().parent / "js"
HARNESS = JS_TESTS / "goal_picker.js"
FIXTURES = JS_TESTS / "goal_picker_fixtures.js"

GEOMETRY_ID = "a" * 64
EXPECTED_MESSAGE = (
    "지도에서 선택한 목표로 이동해 주세요. 로봇: robot-a. 기준 프레임: map. "
    "목표 좌표: x=3.250000 m, y=-1.500000 m. 필요한 주행 안전 절차를 먼저 적용하고, "
    "목표 좌표와 프레임을 변경하지 마세요."
)
_EL_CALL_RE = re.compile(r"el\(\"([^\"]+)\"\)")
_ID_ATTR_RE = re.compile(r"id=\"([^\"]+)\"")


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 실행 파일이 없어 목표 선택 JS 테스트를 건너뜁니다")
    return node


@pytest.fixture(scope="module")
def harness() -> dict:
    result = subprocess.run(
        [
            _node(),
            str(HARNESS),
            str(GOAL_JS),
            str(FIXTURES),
            str(GEOMETRY_JS),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


# ---- 정적 계약 -------------------------------------------------------------


def test_goal_picker_script_loads_after_geometry_and_before_app():
    html = INDEX_HTML.read_text(encoding="utf-8")
    geometry = html.index("/assets/map_geometry.js")
    goal = html.index("/assets/goal_picker.js")
    app = html.index("/assets/app.js")
    assert geometry < goal < app


def test_goal_element_ids_exist_in_html():
    script = GOAL_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")
    referenced = {name for name in _EL_CALL_RE.findall(script) if name.startswith("goal")}
    referenced.add("map-canvas")
    referenced.add("pose-mode")
    declared = set(_ID_ATTR_RE.findall(html))
    missing = sorted(referenced - declared)
    assert missing == [], f"index.html 에 없는 목표 선택 id: {missing}"


def test_goal_picker_uses_status_snapshot_only_and_no_extra_fetch():
    script = GOAL_JS.read_text(encoding="utf-8")
    app = APP_JS.read_text(encoding="utf-8")
    assert "setInterval(" not in script
    assert "setTimeout(" not in script
    assert "navigation_request" in script
    assert "state.mapGoal.applySnapshot" in app
    assert "state.mapGoal.applyMap" in app
    # 일반 채팅 동작은 그대로 둔다.
    assert "JSON.stringify({ message })" in app


def test_goal_picker_never_calls_skill_nav2_or_initial_pose_endpoints():
    script = GOAL_JS.read_text(encoding="utf-8")
    lowered = script.lower()
    for forbidden in (
        "executeskill",
        "execute_skill",
        "execute_task",
        "navigate_to",
        "nav2",
        "/api/initial_pose",
        "/api/execute",
        "/api/navigate",
        "innerhtml",
        "document.write",
    ):
        assert forbidden not in lowered, f"목표 선택 모드가 {forbidden} 를 사용합니다"
    assert '"/api/chat"' in script
    # control 토큰만 쓴다. 읽기 전용 토큰으로 제어 요청을 대체하지 않는다.
    assert "getControlToken" in script
    assert "getReadToken" not in script


def test_initial_pose_mode_is_delegated_to_goal_picker_when_mounted():
    app = APP_JS.read_text(encoding="utf-8")
    script = GOAL_JS.read_text(encoding="utf-8")
    assert "setupGoalPicker" in app
    # 목표 선택 컨트롤러가 있으면 app.js 의 기존 초기 위치 토글은 양보한다.
    assert "if (state.mapGoal) return;" in app
    assert 'addEventListener("click", toggleInitialPose)' in script


def test_app_propagates_auth_status_and_releases_drag_pointer_capture():
    app = APP_JS.read_text(encoding="utf-8")
    assert "err.status = resp.status" in app
    assert "releasePointerCapture" in app
    assert "releaseDragCapture" in app
    assert "if (!state.poseMode) releaseDragCapture" in app
    assert 'typeof data.geometry_id === "string"' in app


def test_goal_picker_uses_only_control_token_without_readonly_fallback():
    script = GOAL_JS.read_text(encoding="utf-8")
    assert "getControlToken" in script
    assert "getReadToken" not in script
    assert "readonly" not in script.lower()
    assert "conflictWait" in script
    assert "retiredIds" in script


def test_map_markers_subtract_origin_yaw_and_initial_pose_path_is_unchanged():
    app = APP_JS.read_text(encoding="utf-8")
    script = GOAL_JS.read_text(encoding="utf-8")
    # 지도 이미지는 grid-local 축이므로 마커/화살표 방향에서 origin.yaw 를 뺀다.
    assert "RcMapGeometry.headingToCanvas" in app
    # 초기 위치 설정의 기존 드래그/확인/endpoint 경로는 그대로 둔다.
    assert "sendInitialPose" in app
    assert '"/api/initial_pose"' in app
    assert "state.poseMode = !state.poseMode" in app
    # geometry ID 는 서버가 주는 값을 복사만 하고 브라우저에서 계산하지 않는다.
    assert "crypto" not in script.lower()


def test_goal_controls_are_themed_accessible_and_touch_sized():
    css = STYLE_CSS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "--rc-goal-marker" in css
    assert "--rc-goal-marker-pending" in css
    assert ".rc-goal-send" in css
    assert "min-height: 48px" in css or "min-height:48px" in css
    assert "prefers-reduced-motion" in css
    assert "rc-goal" in css
    assert 'aria-readonly="true"' in html
    assert 'id="goal-preview"' in html
    assert 'tabindex="0"' in html
    canvas = re.search(r"<canvas[^>]*>", html)
    assert canvas and "aria-label" in canvas.group(0)


def test_goal_picker_stays_under_module_size_limit():
    lines = GOAL_JS.read_text(encoding="utf-8").count("\n") + 1
    # Project guidance treats 400–500 lines as a module-boundary warning.
    assert lines < 500, f"goal_picker.js 가 단일 컴포넌트 범위를 넘었습니다: {lines} lines"


# ---- JS 실행 계약 ----------------------------------------------------------


def test_goal_picker_js_checks_pass(harness: dict):
    failures = [check for check in harness["checks"] if not check["ok"]]
    assert failures == [], "\n".join(
        f"{item['name']}: {item.get('error', '')}" for item in failures
    )
    assert len(harness["checks"]) >= 36


def test_canonical_message_matches_backend_contract(harness: dict):
    assert harness["observed"]["canonicalMessage"] == EXPECTED_MESSAGE


def test_chat_body_matches_frontend_contract(harness: dict):
    body = harness["observed"]["chatBody"]
    assert set(body) == {"message", "request_id", "map_goal"}
    assert body["message"] == EXPECTED_MESSAGE
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", body["request_id"])
    assert body["map_goal"] == {
        "x": 3.25,
        "y": -1.5,
        "frame_id": "map",
        "robot_id": "robot-a",
        "geometry_id": GEOMETRY_ID,
    }


def test_send_body_uses_canonical_coordinates_and_generated_request_id(harness: dict):
    body = harness["observed"]["firstRequestBody"]
    assert set(body) == {"message", "request_id", "map_goal"}
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", body["request_id"])
    assert body["message"].startswith("지도에서 선택한 목표로 이동해 주세요.")
    assert "x=5.000000 m, y=5.000000 m" in body["message"]
    assert body["map_goal"]["x"] == 5
    assert body["map_goal"]["y"] == 5
    assert body["map_goal"]["frame_id"] == "map"
    assert body["map_goal"]["robot_id"] == "robot-a"
    assert body["map_goal"]["geometry_id"] == GEOMETRY_ID


def test_uncertain_network_response_holds_unknown_without_resend(harness: dict):
    """네트워크/5xx 는 접수 여부가 불명확하므로 unknown 으로 보류하고 재전송하지 않는다."""
    assert "요청 접수 여부를 확인할 수 없습니다" in harness["observed"]["networkHold"]
    assert "취소" in harness["observed"]["networkRecovered"]


def test_409_without_request_recovers_from_foreign_status(harness: dict):
    """old 409 처럼 request 가 없어도 다음 status 의 외부 요청을 추적해 회복한다."""
    assert "409" in harness["observed"]["conflictWait"]
    ids = harness["observed"]["conflictRecovered"]
    assert len(ids) == 2
    assert ids[0] != ids[1], "409 거절 후 이전 request_id 를 재사용함"
    assert harness["observed"]["conflictNullRecovery"] == 1
    assert harness["observed"]["wrongTargetRejected"] is True
    assert harness["observed"]["latestResultRestored"] is True


def test_no_auth_local_mode_allows_empty_control_token(harness: dict):
    assert harness["observed"]["noAuthToken"] == ""
