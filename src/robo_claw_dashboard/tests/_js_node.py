"""JS 실행 테스트의 node 부재 처리 — 조용한 skip 을 게이트에서 드러나게 한다.

대시보드 표시 로직 테스트는 `node` 로 실제 JS 를 실행해 검증합니다. 그런데 node 가
없는 환경(예: 런타임 Docker 이미지)에서는 지금까지 조용히 skip 되어, "통과" 로
보이지만 JS 회귀는 전혀 검증되지 않았습니다.

`RC_REQUIRE_JS_TESTS` 가 켜져 있으면 skip 대신 실패시킵니다. `task verify` ·
`task unit-test` 가 이 값을 켜므로, 개발/CI 호스트에 node 가 없으면 침묵 대신
설치 안내와 함께 게이트가 실패합니다. 컨테이너처럼 JS 테스트를 의도적으로 제외하는
환경은 이 값을 켜지 않으면 됩니다(이때는 skip 사유가 요약에 남습니다).
"""

from __future__ import annotations

import os

import pytest

#: 1/true/yes/on 이면 node 부재를 실패로 처리한다.
REQUIRE_ENV = "RC_REQUIRE_JS_TESTS"

_INSTALL_HINT = (
    "node 가 없어 JS 실행 테스트를 건너뛰려 했지만 RC_REQUIRE_JS_TESTS 가 설정되어 있습니다. "
    "node(Node.js 18+)를 설치하거나, JS 테스트를 의도적으로 제외하는 환경이면 "
    "이 환경변수를 해제하세요"
)


def _required() -> bool:
    raw = os.environ.get(REQUIRE_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def skip_or_fail_missing_node(reason: str) -> None:
    """node 부재 시 skip 하거나(기본) 실패한다(게이트 모드).

    Args:
        reason: skip 사유로 남길 문구. 예) "JS 협동 패널 테스트를 건너뜁니다".
    """
    if _required():
        pytest.fail(f"{_INSTALL_HINT} ({reason})", pytrace=False)
    pytest.skip(reason)
