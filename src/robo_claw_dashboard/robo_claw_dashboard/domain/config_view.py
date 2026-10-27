"""실행 설정 패널 도메인 — ROS2 노드 파라미터를 마스킹·정규화한다.

주의: rclpy / ROS 메시지 import 금지. 파라미터 값은 어댑터가 이미 Python 값으로 변환해 넘긴다.
시크릿은 값 노출 없이 마스킹하고, 긴 문자열/배열은 화면 표시용으로 줄인다.
"""

from collections.abc import Mapping
from typing import Any

#: 마스킹된 값 표기.
MASK = "***"
#: 화면에 표시할 문자열 최대 길이(초과분은 말줄임표).
MAX_VALUE_CHARS = 160
#: 화면에 표시할 배열 최대 항목 수.
MAX_LIST_ITEMS = 20

#: 이름에 이 조각이 들어가면 시크릿으로 취급한다(부분어 오탐을 피하려고 조각 단위로 본다).
SECRET_HINTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "credential",
)


def is_secret_name(name: str) -> bool:
    """파라미터 이름이 시크릿 성격인지 판단한다."""
    lowered = str(name).lower()
    return any(hint in lowered for hint in SECRET_HINTS)


def normalize_value(value: Any) -> tuple[Any, bool]:
    """표시용으로 값을 정규화한다. (value, truncated) 를 반환한다."""
    if isinstance(value, (list, tuple)):
        items = list(value)[:MAX_LIST_ITEMS]
        normalized = [
            item if isinstance(item, (bool, int, float, str)) else str(item) for item in items
        ]
        return normalized, len(value) > MAX_LIST_ITEMS
    if isinstance(value, str):
        if len(value) > MAX_VALUE_CHARS:
            return value[:MAX_VALUE_CHARS] + "…", True
        return value, False
    if isinstance(value, (bool, int, float)) or value is None:
        return value, False
    return str(value)[:MAX_VALUE_CHARS], len(str(value)) > MAX_VALUE_CHARS


def mask_parameters(parameters: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """파라미터 dict 를 이름순 정렬된 표시 행 목록으로 변환한다."""
    if not parameters:
        return []
    rows: list[dict[str, Any]] = []
    for name in sorted(parameters):
        secret = is_secret_name(name)
        raw = parameters[name]
        if secret:
            # 값이 비어 있으면 "미설정"을 구분할 수 있도록 빈 문자열을 유지한다.
            rows.append(
                {
                    "name": str(name),
                    "value": MASK if raw not in ("", None) else "",
                    "secret": True,
                    "truncated": False,
                }
            )
            continue
        value, truncated = normalize_value(raw)
        rows.append({"name": str(name), "value": value, "secret": False, "truncated": truncated})
    return rows


def _section(node: str, parameters: Mapping[str, Any] | None) -> dict[str, Any]:
    rows = mask_parameters(parameters)
    return {
        "node": str(node),
        "available": parameters is not None,
        "parameters": rows,
    }


def build_config_panel(
    *,
    agent_node: str,
    agent_parameters: Mapping[str, Any] | None,
    dashboard_node: str,
    dashboard_parameters: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """에이전트/대시보드 노드의 실행 파라미터를 한 화면용 구조로 합친다."""
    agent = _section(agent_node, agent_parameters)
    dashboard = _section(dashboard_node, dashboard_parameters)
    return {
        "available": agent["available"] or dashboard["available"],
        "agent": agent,
        "dashboard": dashboard,
        "counts": {
            "agent": len(agent["parameters"]),
            "dashboard": len(dashboard["parameters"]),
        },
    }
