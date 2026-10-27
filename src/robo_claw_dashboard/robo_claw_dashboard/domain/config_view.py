"""실행 설정 패널 도메인 — ROS2 노드 파라미터를 마스킹·정규화한다.

주의: rclpy / ROS 메시지 import 금지. 파라미터 값은 어댑터가 이미 Python 값으로 변환해 넘긴다.
시크릿은 값 노출 없이 마스킹하고, 긴 문자열/배열은 화면 표시용으로 줄인다.
"""

from collections.abc import Iterable, Mapping
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


#: 기본값과 같은지 비교할 때 쓰는 부동소수 허용 오차.
_FLOAT_TOLERANCE = 1e-9


def flatten_param_docs(docs: Iterable[Mapping[str, Any] | None]) -> dict[str, Any]:
    """ROS 파라미터 YAML 문서들을 ``{파라미터명: 기본값}`` 으로 평탄화한다.

    문서는 ``{노드명: {"ros__parameters": {...}}}`` 또는 ``{"/**": {...}}`` 형태이며,
    뒤에 오는 문서가 앞의 값을 덮어쓴다(launch 가 나중 파일로 덮어쓰는 순서와 같다).
    """
    flattened: dict[str, Any] = {}
    for document in docs:
        if not isinstance(document, Mapping):
            continue
        for node_block in document.values():
            if not isinstance(node_block, Mapping):
                continue
            params = node_block.get("ros__parameters")
            if isinstance(params, Mapping):
                flattened.update(params)
    return flattened


def parse_contract_defaults(contract: Mapping[str, Any] | None) -> dict[str, Any]:
    """runtime-contract.json 에서 ``{ros 파라미터명: 기본값}`` 을 만든다.

    ROS 파라미터로 노출되는 필드만 대상으로 하며, 기본값이 없는 필드는 None 으로 둔다.
    """
    if not isinstance(contract, Mapping):
        return {}
    fields = contract.get("fields")
    # 생성물은 정규화 단계에 따라 list 또는 canonical 키의 dict 로 나온다. 둘 다 지원한다.
    if isinstance(fields, Mapping):
        field_list: list[Any] = list(fields.values())
    elif isinstance(fields, list):
        field_list = fields
    else:
        return {}
    defaults: dict[str, Any] = {}
    for field in field_list:
        if not isinstance(field, Mapping):
            continue
        parameter = str(field.get("ros_parameter") or "").strip()
        if not parameter:
            continue
        defaults[parameter] = field.get("default")
    return defaults


def values_match(current: Any, default: Any) -> bool:
    """타입 차이(bool/정수/실수)를 흡수해 두 값이 같은지 비교한다."""
    if isinstance(current, bool) or isinstance(default, bool):
        return isinstance(current, bool) and isinstance(default, bool) and current == default
    if isinstance(current, (int, float)) and isinstance(default, (int, float)):
        return abs(float(current) - float(default)) <= _FLOAT_TOLERANCE
    if isinstance(current, (list, tuple)) and isinstance(default, (list, tuple)):
        return len(current) == len(default) and all(
            values_match(left, right) for left, right in zip(current, default, strict=False)
        )
    return current == default


def source_of(name: str, value: Any, defaults: Mapping[str, Any] | None) -> str:
    """값이 기본값과 같은지 판정한다("default" | "explicit" | "unknown").

    기본값을 알 수 없으면 unknown 으로 두어 화면에서 명시 값과 구분하지 않는다.
    """
    if not isinstance(defaults, Mapping) or name not in defaults:
        return "unknown"
    return "default" if values_match(value, defaults[name]) else "explicit"


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


def mask_parameters(
    parameters: Mapping[str, Any] | None,
    defaults: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """파라미터 dict 를 이름순 정렬된 표시 행 목록으로 변환한다.

    ``defaults`` 가 주어지면 각 행에 ``source`` 를 붙여 기본값/명시 값을 구분한다.
    """
    if not parameters:
        return []
    rows: list[dict[str, Any]] = []
    for name in sorted(parameters):
        secret = is_secret_name(name)
        raw = parameters[name]
        source = source_of(str(name), raw, defaults)
        if secret:
            # 값이 비어 있으면 "미설정"을 구분할 수 있도록 빈 문자열을 유지한다.
            rows.append(
                {
                    "name": str(name),
                    "value": MASK if raw not in ("", None) else "",
                    "secret": True,
                    "truncated": False,
                    "source": source,
                }
            )
            continue
        value, truncated = normalize_value(raw)
        rows.append(
            {
                "name": str(name),
                "value": value,
                "secret": False,
                "truncated": truncated,
                "source": source,
            }
        )
    return rows


def _section(
    node: str,
    parameters: Mapping[str, Any] | None,
    defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = mask_parameters(parameters, defaults)
    return {
        "node": str(node),
        "available": parameters is not None,
        "parameters": rows,
        "default_count": sum(1 for row in rows if row["source"] == "default"),
    }


def build_config_panel(
    *,
    agent_node: str,
    agent_parameters: Mapping[str, Any] | None,
    dashboard_node: str,
    dashboard_parameters: Mapping[str, Any] | None,
    agent_defaults: Mapping[str, Any] | None = None,
    dashboard_defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """에이전트/대시보드 노드의 실행 파라미터를 한 화면용 구조로 합친다.

    ``*_defaults`` 를 주면 각 행이 기본값과 같은지(``source``) 판정해 화면에서 흐리게
    구분할 수 있게 한다.
    """
    agent = _section(agent_node, agent_parameters, agent_defaults)
    dashboard = _section(dashboard_node, dashboard_parameters, dashboard_defaults)
    return {
        "available": agent["available"] or dashboard["available"],
        "agent": agent,
        "dashboard": dashboard,
        "counts": {
            "agent": len(agent["parameters"]),
            "dashboard": len(dashboard["parameters"]),
        },
        "default_counts": {
            "agent": agent["default_count"],
            "dashboard": dashboard["default_count"],
        },
    }
