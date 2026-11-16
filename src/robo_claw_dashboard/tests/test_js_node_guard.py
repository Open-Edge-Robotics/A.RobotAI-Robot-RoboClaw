"""node 부재 처리 가드(`RC_REQUIRE_JS_TESTS`) 회귀 테스트.

런타임 Docker 이미지에는 node 가 없어 JS 실행 테스트가 조용히 skip 된다. 게이트
(`task verify`/`task unit-test`)에서는 이 침묵을 실패로 바꿔, 검증되지 않은 채
"통과" 로 보고되는 것을 막는다.
"""

from __future__ import annotations

import pytest

from ._js_node import REQUIRE_ENV, skip_or_fail_missing_node

pytestmark = pytest.mark.unit


def test_skips_when_gate_mode_is_off(monkeypatch):
    monkeypatch.delenv(REQUIRE_ENV, raising=False)

    with pytest.raises(pytest.skip.Exception):
        skip_or_fail_missing_node("JS 테스트를 건너뜁니다")


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", " TRUE "])
def test_fails_when_gate_mode_is_on(monkeypatch, value):
    monkeypatch.setenv(REQUIRE_ENV, value)

    with pytest.raises(pytest.fail.Exception) as excinfo:
        skip_or_fail_missing_node("JS 테스트를 건너뜁니다")

    message = str(excinfo.value)
    assert "node" in message
    assert REQUIRE_ENV in message


def test_unknown_value_keeps_skipping(monkeypatch):
    monkeypatch.setenv(REQUIRE_ENV, "maybe")

    with pytest.raises(pytest.skip.Exception):
        skip_or_fail_missing_node("JS 테스트를 건너뜁니다")
