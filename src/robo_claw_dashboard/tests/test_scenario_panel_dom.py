"""시나리오 패널 DOM 계약 테스트.

`app.js` 는 `el("id")` 로 요소를 찾고, 없으면 조용히 아무 일도 하지 않는다. 새로 추가한
서버 설정 입력/경고 박스의 id 가 HTML 에서 빠지면 버튼이 무반응이 되므로, 시나리오 관련
id 가 `index.html` 에 실제로 존재하는지 고정한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.component

WEB = Path(__file__).resolve().parents[1] / "web"
APP_JS = WEB / "assets" / "app.js"
INDEX_HTML = WEB / "index.html"

_EL_CALL_RE = re.compile(r"el\(\"([^\"]+)\"\)")
_ID_ATTR_RE = re.compile(r"id=\"([^\"]+)\"")


def test_scenario_element_ids_exist_in_html():
    script = APP_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")

    referenced = {name for name in _EL_CALL_RE.findall(script) if name.startswith("scenario")}
    declared = set(_ID_ATTR_RE.findall(html))

    assert referenced, "app.js 에서 시나리오 관련 el() 호출을 찾지 못했습니다"
    missing = sorted(referenced - declared)
    assert missing == [], f"index.html 에 없는 시나리오 id: {missing}"


def test_scenario_sync_posts_config_in_body():
    """디바이스 토큰이 쿼리 문자열로 새지 않도록 동기화는 POST 본문을 써야 한다."""
    script = APP_JS.read_text(encoding="utf-8")

    assert '"/api/scenarios/sync"' in script
    assert 'method = "POST"' in script or 'method: "POST"' in script
    assert "/api/scenarios?refresh=1&" not in script
