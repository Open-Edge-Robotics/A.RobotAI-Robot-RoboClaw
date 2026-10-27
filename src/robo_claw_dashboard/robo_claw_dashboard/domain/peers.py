"""동료 로봇 표시 정규화 — ListPeers 응답을 대시보드 화면용 구조로 바꾼다.

주의: ROS2 / rclpy import 금지. 입력은 어댑터가 뽑아낸 순수 dict 목록이다.
"""

from collections.abc import Iterable, Mapping
from typing import Any


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_peers(items: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """ListPeers 응답 항목을 ``{name, host, port, connected}`` 로 정규화한다.

    메시지 필드는 ``peer_name`` 이지만 이전 구현/대역이 ``name`` 을 쓰는 경우도 받아 준다.
    이름이 비어 있으면 화면에서 "동료 N" 으로 표시해 빈 칩이 생기지 않게 한다.
    """
    peers: list[dict[str, Any]] = []
    for index, item in enumerate(items or []):
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("peer_name") or item.get("name") or "").strip()
        peers.append(
            {
                "name": name or f"동료 {index + 1}",
                "host": str(item.get("host") or ""),
                "port": _as_int(item.get("port")),
                "connected": bool(item.get("connected", False)),
            }
        )
    return peers
