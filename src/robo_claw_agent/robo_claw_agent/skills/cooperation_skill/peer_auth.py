"""동료 메시지 인증과 peer별 스킬 스코프.

ROS 2 ``PeerMessage`` 서비스는 gRPC 계층의 peer 토큰 검증을 우회하는 로컬 경로다.
같은 DDS 도메인의 다른 노드가 임의의 ``peer_name`` 을 신고해 협동 인박스에 메시지를
넣을 수 있으므로, 채널 노드가 검증된 출처를 증명하는 공유 토큰을 요청에 실어 보내고
에이전트가 이를 확인한다.

peer별 스코프는 "연결된 peer 는 allowlist 안의 스킬을 요청할 수 있다"를 peer 단위로
좁히는 제어다. 전역 allowlist 를 넓힐 수는 없고 교집합만 유효하다.

ROS2 의존이 없어 단위 테스트로 검증한다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

#: 토큰 검증이 설정되지 않았을 때의 사유(강제하지 않음).
REASON_NOT_CONFIGURED = "peer_token_not_configured"
REASON_MISSING = "peer_token_missing"
REASON_MISMATCH = "peer_token_mismatch"


def _digest(value: str) -> bytes:
    """길이 차이에 따른 정보 노출을 줄이기 위해 고정 길이로 만든 뒤 비교한다."""
    return hashlib.sha256(value.encode("utf-8")).digest()


def _tokens_by_peer_lower(tokens_by_peer: Mapping[str, str] | None) -> dict[str, str]:
    return {
        str(name).strip().lower(): str(token).strip()
        for name, token in (tokens_by_peer or {}).items()
        if str(name).strip()
    }


def verify_peer_message_token(
    *,
    expected_common: str,
    tokens_by_peer: Mapping[str, str] | None = None,
    peer_name: str,
    supplied: str,
) -> tuple[bool, str]:
    """``PeerMessage`` 인증 토큰을 상수 시간 비교로 검증한다.

    기대 토큰은 peer별 토큰 → 공통 토큰 순으로 찾는다. 둘 다 설정되지 않았으면
    검증을 강제하지 않고 ``peer_token_not_configured`` 를 함께 돌려준다(호출자가
    경고를 남길 수 있게 하기 위함).

    Returns:
        (허용 여부, 사유). 허용이면 사유는 빈 문자열이거나
        ``peer_token_not_configured`` 다.
    """
    peer_key = str(peer_name or "").strip().lower()
    mapped = _tokens_by_peer_lower(tokens_by_peer)
    common = str(expected_common or "").strip()

    if peer_key and peer_key in mapped:
        expected = mapped[peer_key]
    elif mapped and not common:
        # peer별 토큰을 운용하는데 목록에 없는 peer 이고 공통 토큰도 없으면,
        # 그 peer 를 인증할 근거가 없다. 통과시키면 목록에 이름을 넣지 않는 것만으로
        # 인증을 우회하게 되므로 거부한다.
        return False, REASON_MISMATCH
    else:
        expected = common

    if not expected:
        return True, REASON_NOT_CONFIGURED

    candidate = str(supplied or "").strip()
    if not candidate:
        return False, REASON_MISSING

    # peer별 토큰이 설정된 로봇에 공통 토큰을 제출하는 경우도 불일치로 처리한다.
    if not hmac.compare_digest(_digest(expected), _digest(candidate)):
        return False, REASON_MISMATCH
    return True, ""


def parse_peer_skill_scope(raw: str) -> tuple[dict[str, list[str]], str]:
    """``remote_allowed_skills_by_peer_json`` 파라미터를 검증·정규화한다.

    ``{"robot-b": ["navigate_to", "say"]}`` 형태만 허용한다. 값이 목록이 아니면
    전체를 버리고 오류를 돌려준다(부분 적용으로 스코프가 넓어지는 것을 막기 위함).
    """
    text = str(raw or "").strip()
    if not text:
        return {}, ""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}, "remote_allowed_skills_by_peer_json 은 JSON 객체여야 합니다."
    if not isinstance(parsed, dict):
        return {}, "remote_allowed_skills_by_peer_json 은 JSON 객체여야 합니다."

    scope: dict[str, list[str]] = {}
    for peer, skills in parsed.items():
        peer_key = str(peer).strip().lower()
        if not peer_key:
            continue
        if not isinstance(skills, list):
            return {}, (
                f"remote_allowed_skills_by_peer_json 의 '{peer}' 값은 스킬 이름 목록이어야 합니다."
            )
        scope[peer_key] = [str(name).strip() for name in skills if str(name).strip()]
    return scope, ""


def effective_peer_skill_allowlist(
    *,
    global_allowlist: Any,
    peer_scope: Mapping[str, list[str]] | None,
    peer_name: str,
) -> set[str]:
    """이 peer 가 원격 실행을 요청할 수 있는 스킬 집합을 계산한다.

    스코프 항목이 없으면 전역 allowlist 를 그대로 쓰고, 항목이 있으면 전역
    allowlist 와의 교집합을 쓴다(스코프가 권한을 넓힐 수 없다). 항목이 빈 목록이면
    그 peer 는 어떤 스킬도 위임할 수 없다.
    """
    allowed = {str(name) for name in (global_allowlist or ()) if str(name)}
    peer_key = str(peer_name or "").strip().lower()
    scope = {str(k).strip().lower(): v for k, v in (peer_scope or {}).items()}
    if not peer_key or peer_key not in scope:
        return allowed
    return allowed & {str(name) for name in scope[peer_key] if str(name)}
