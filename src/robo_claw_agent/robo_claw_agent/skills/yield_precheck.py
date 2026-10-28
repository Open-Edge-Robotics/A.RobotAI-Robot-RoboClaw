"""자리 양보 선확인 — 이동을 시작하기 전에 동료에게 목표 자리를 먼저 묻는다.

선확인 규칙은 원래 ``navigate_to`` 실행 안에만 있었고, 그래서 장소 이름이 있는
사용자 이동 요청에서만 동작했다. 동료가 위임한 이동(``autonomous_cooperate``)이나
자율협동 장소 순회처럼 좌표로 이동하는 경로는 선확인을 건너뛰었다. 이 모듈은 규칙을
한 곳에 모아 모든 이동 경로가 같은 판단을 쓰게 한다.

설계 원칙(``docs/PEER_COLLABORATION.md``):

- 좌표는 로봇 사이에 넘기지 않는다. 동료에게 묻는 값은 **장소 이름**뿐이다.
- 장소 이름을 모르면 추측하지 않고 묻지 않는다. 좌표만 있으면 시멘틱 맵에서
  가까운 장소를 되짚어 이름을 얻는다(로컬 계산이며 네트워크로 좌표를 보내지 않는다).
- 선확인은 이동을 막지 않는다(fail-open). 거절·시간 초과여도 이동은 그대로 진행한다.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

# 선확인 대상: 베이스가 움직이는 스킬. ``agent_node.nav_safety.BASE_MOTION_SKILLS`` 와
# 같은 목록이며, 이 모듈이 ROS 노드 패키지(rclpy) 없이 import 되어야 해서 값을 따로
# 둔다. 두 목록이 어긋나면 ``test_yield_precheck`` 가 실패한다.
NAVIGATION_SKILLS: frozenset[str] = frozenset(
    {
        "navigate_to",
        "move_relative",
        "approach_object",
        "rotate",
        "face_direction",
        "follow_waypoints",
        "patrol",
        "explore",
        "reactive_navigate",
        "condition_reactive",
    }
)

# LLM/동료가 쓰는 이동 스킬 별칭. agent_node.utils 의 정규화와 같은 값을 쓴다.
_SKILL_ALIASES: dict[str, str] = {"navigate": "navigate_to"}

# 이동 파라미터에서 장소 이름을 담는 키 (우선순위 순).
_PLACE_PARAM_KEYS: tuple[str, ...] = (
    "target_name",
    "destination",
    "destination_name",
    "location",
    "place",
    "target",
    "target_location",
)

PRECHECK_MODES: frozenset[str] = frozenset({"ask", "off"})
DEFAULT_MODE = "ask"

# 좌표로 이동할 때 시멘틱 맵에서 같은 장소로 볼 거리(m). Nav2 도착 판정(0.25m)보다
# 넉넉하고 방 사이 거리보다 좁게 잡는다.
DEFAULT_PLACE_MATCH_RADIUS_M = 1.0
# 자리 양보 서비스 응답을 기다리는 상한(초). 수신측 이동 상한(40초)보다 길어야 한다.
YIELD_REQUEST_TIMEOUT_SEC = 70.0


def yield_precheck_mode(node: Any) -> str:
    """노드 파라미터 ``yield_precheck_mode`` 를 읽는다(``ask``/``off``)."""
    if node is None:
        return "off"
    try:
        if node.has_parameter("yield_precheck_mode"):
            mode = (
                str(
                    node.get_parameter("yield_precheck_mode").get_parameter_value().string_value
                    or ""
                )
                .strip()
                .lower()
            )
            if mode in PRECHECK_MODES:
                return mode
    except Exception:  # noqa: BLE001
        return "off"
    return DEFAULT_MODE


def should_precheck_yield(node: Any, place_name: str) -> bool:
    """지금 동료에게 자리를 물을지 판단한다. 장소 이름이 없으면 묻지 않는다."""
    if yield_precheck_mode(node) == "off":
        return False
    return bool(str(place_name or "").strip())


def place_name_from_move_params(params: Mapping[str, Any] | None) -> str:
    """이동 파라미터에 담긴 장소 이름을 꺼낸다(별칭 포함). 없으면 빈 문자열."""
    if not isinstance(params, Mapping):
        return ""
    for key in _PLACE_PARAM_KEYS:
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def place_name_for_coordinates(
    node: Any,
    x: Any,
    y: Any,
    *,
    radius_m: float = DEFAULT_PLACE_MATCH_RADIUS_M,
) -> str:
    """좌표를 자기 시멘틱 맵의 장소 이름으로 되짚는다. 모르면 빈 문자열.

    좌표를 동료에게 넘기지 않는다는 원칙은 그대로다. 여기서 얻은 이름만 요청에 쓴다.
    """
    if node is None:
        return ""
    try:
        px, py = float(x), float(y)
    except (TypeError, ValueError):
        return ""
    if not (math.isfinite(px) and math.isfinite(py)):
        return ""
    memory = getattr(node, "_memory", None)
    if memory is None:
        return ""
    try:
        from robo_claw_agent.skills.autonomous_skill.helpers import (
            _remembered_place_candidates,
        )

        places = _remembered_place_candidates(memory)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Yield] 장소 역해석을 건너뜁니다: %s", exc)
        return ""

    best_name = ""
    best_distance = float(radius_m)
    for place in places:
        position = place.get("position") or {}
        try:
            cx, cy = float(position["x"]), float(position["y"])
        except (KeyError, TypeError, ValueError):
            continue
        distance = math.hypot(cx - px, cy - py)
        if distance <= best_distance:
            best_distance = distance
            best_name = str(place.get("name") or "").strip()
    return best_name


def request_peer_yield(
    node: Any,
    *,
    place: str,
    timeout_sec: float = YIELD_REQUEST_TIMEOUT_SEC,
) -> bool:
    """동료에게 자리를 요청한다. 실패해도 이동은 계속 시도한다(fail-open)."""
    place = str(place or "").strip()
    if not place or not should_precheck_yield(node, place):
        return False
    skills = getattr(node, "_skills", None)
    if skills is None or not hasattr(skills, "has_skill"):
        return False
    try:
        if not skills.has_skill("request_yield_slot"):
            return False
    except Exception:  # noqa: BLE001
        return False
    try:
        result = skills.execute(
            "request_yield_slot",
            {"target_place": place},
            timeout_sec=timeout_sec,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Yield] 자리 요청 실패(%s): %s", place, exc)
        return False
    logger.info(
        "[Yield] 자리 요청(%s): success=%s message=%s",
        place,
        bool(getattr(result, "success", False)),
        getattr(result, "message", ""),
    )
    return True


def precheck_yield_for_move(
    node: Any,
    skill_name: str,
    params: Mapping[str, Any] | None,
    *,
    place: str = "",
) -> str:
    """이동 스킬 실행 전에 자리를 선확인한다. 사용한 장소 이름을 반환한다(없으면 빈 문자열).

    ``navigate_to`` 는 장소 이름이 있으면 스스로 선확인하므로 여기서 중복 요청하지
    않는다. 반대로 좌표만 있으면 ``navigate_to`` 는 묻지 않으므로, 호출측(동료 위임,
    장소 순회)이 이 함수로 장소를 되짚어 확인한다.
    """
    if yield_precheck_mode(node) == "off":
        return ""
    normalized = _SKILL_ALIASES.get(str(skill_name or "").strip(), str(skill_name or "").strip())
    if normalized not in NAVIGATION_SKILLS:
        return ""

    param_place = place_name_from_move_params(params)
    if not str(place or "").strip() and normalized == "navigate_to" and param_place:
        # navigate_to 가 같은 이름으로 직접 확인한다.
        return ""

    resolved = str(place or "").strip() or param_place
    if not resolved and isinstance(params, Mapping):
        resolved = place_name_for_coordinates(node, params.get("x"), params.get("y"))
    if not should_precheck_yield(node, resolved):
        return ""
    request_peer_yield(node, place=resolved)
    return resolved
