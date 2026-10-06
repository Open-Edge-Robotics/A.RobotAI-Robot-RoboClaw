from __future__ import annotations

import logging
import uuid
from typing import Any

import rclpy
from std_srvs.srv import Trigger

from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)

_MODE_SERVICES = {
    "position": "/switch_to_position_mode",
    "navigation": "/switch_to_navigation_mode",
}
_STOW_SERVICE = "/stow_the_robot"

_MODE_ALIASES = {
    "pos": "position",
    "position": "position",
    "position_mode": "position",
    "joint": "position",
    "manipulation": "position",
    "arm": "position",
    "gripper": "position",
    "nav": "navigation",
    "navigation": "navigation",
    "navigation_mode": "navigation",
    "drive": "navigation",
    "base": "navigation",
    "move": "navigation",
}


def _normalize_mode(value: Any) -> str:
    mode = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _MODE_ALIASES.get(mode, "")


def _bool_param(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _call_trigger_service(
    service_name: str,
    timeout_sec: float,
    *,
    action_label: str,
    result_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    node = None
    result_extra = result_extra or {}
    try:
        node = rclpy.create_node(f"robo_claw_stretch_service_{uuid.uuid4().hex[:12]}")
        client = node.create_client(Trigger, service_name)
        if not client.wait_for_service(timeout_sec=timeout_sec):
            return {
                "success": False,
                "message": f"{action_label} 서비스가 준비되지 않았습니다: {service_name}",
                "service": service_name,
                **result_extra,
            }

        future = client.call_async(Trigger.Request())
        rclpy.spin_until_future_complete(node, future, timeout_sec=timeout_sec)
        if not future.done():
            future.cancel()
            return {
                "success": False,
                "message": f"{action_label} 타임아웃({timeout_sec:.1f}s)",
                "service": service_name,
                **result_extra,
            }

        response = future.result()
        if response is None:
            return {
                "success": False,
                "message": f"{action_label} 응답을 받지 못했습니다.",
                "service": service_name,
                **result_extra,
            }

        success = bool(getattr(response, "success", False))
        response_message = str(getattr(response, "message", "") or "")
        message = response_message or (
            f"{action_label} 완료" if success else f"{action_label} 실패"
        )
        return {
            "success": success,
            "message": message,
            "service": service_name,
            **result_extra,
        }
    except Exception as exc:  # noqa: BLE001 - ROS client 생성/호출 실패를 결과로 반환
        logger.exception("%s failed", action_label)
        return {
            "success": False,
            "message": f"{action_label} 오류: {exc}",
            "service": service_name,
            **result_extra,
        }
    finally:
        if node is not None:
            try:
                node.destroy_node()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to clean up Stretch Trigger temporary node: %s", exc)


def _call_stretch_mode_service(mode: str, timeout_sec: float) -> dict[str, Any]:
    return _call_trigger_service(
        _MODE_SERVICES[mode],
        timeout_sec,
        action_label=f"Stretch3 {mode} 모드 전환",
        result_extra={"mode": mode},
    )


class SwitchStretchModeSkill(BaseSkill):
    """Stretch3 driver mode 전환 스킬."""

    name = "switch_stretch_mode"
    input_schema = {"type": "object", "properties": {
        "mode": {"type": "string", "enum": ["position", "navigation"]}
    }, "required": ["mode"], "additionalProperties": False}
    requires_manipulation_backend = "stretch"
    description = (
        "Stretch3 driver 모드를 전환합니다. mode 파라미터는 'position' 또는 "
        "'navigation' 입니다. 팔/그리퍼 제어 전에는 position, 주행 전에는 "
        "navigation 모드를 사용하세요."
    )

    def validate_params(self, params: dict[str, Any]) -> bool:
        return bool(_normalize_mode(params.get("mode")))

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        mode = _normalize_mode(params.get("mode"))
        if not mode:
            return {
                "success": False,
                "message": "mode는 position 또는 navigation 이어야 합니다.",
            }
        timeout_sec = float(params.get("timeout_sec", 5.0) or 5.0)
        return _call_stretch_mode_service(mode, timeout_sec)


class StretchPositionModeSkill(BaseSkill):
    """Stretch3 position mode 전환 단축 스킬."""

    name = "stretch_position_mode"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    requires_manipulation_backend = "stretch"
    description = "Stretch3를 팔/그리퍼 제어용 position 모드로 전환합니다."

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        timeout_sec = float(params.get("timeout_sec", 5.0) or 5.0)
        return _call_stretch_mode_service("position", timeout_sec)


class StretchNavigationModeSkill(BaseSkill):
    """Stretch3 navigation mode 전환 단축 스킬."""

    name = "stretch_navigation_mode"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    requires_manipulation_backend = "stretch"
    description = "Stretch3를 Nav2/베이스 주행용 navigation 모드로 전환합니다."

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        timeout_sec = float(params.get("timeout_sec", 5.0) or 5.0)
        return _call_stretch_mode_service("navigation", timeout_sec)


class StowForNavigationSkill(BaseSkill):
    """Stretch3 팔/그리퍼를 주행 안전 자세로 접고 navigation 모드로 복귀."""

    name = "stow_for_navigation"
    input_schema = {"type": "object", "properties": {
        "stow_timeout_sec": {"type": "number", "default": 30.0},
        "mode_timeout_sec": {"type": "number", "default": 5.0},
        "switch_navigation": {"type": "boolean", "default": True}
    }, "additionalProperties": False}
    requires_manipulation_backend = "stretch"
    description = (
        "Stretch3 팔과 그리퍼를 mobile base footprint 안으로 접는 /stow_the_robot "
        "서비스를 호출한 뒤 navigation 모드로 전환합니다. 네비게이션/주행 전에 "
        "팔이 펴져 장애물에 걸릴 위험이 있을 때 사용하세요."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        stow_timeout_sec = float(params.get("stow_timeout_sec", 30.0) or 30.0)
        mode_timeout_sec = float(params.get("mode_timeout_sec", 5.0) or 5.0)
        switch_navigation = _bool_param(params.get("switch_navigation"), True)

        stow_result = _call_trigger_service(
            _STOW_SERVICE,
            stow_timeout_sec,
            action_label="Stretch3 stow",
            result_extra={"mode": "stowing"},
        )
        if not stow_result.get("success", False):
            return {
                **stow_result,
                "message": f"주행 안전 자세 전환 실패: {stow_result.get('message', '')}",
                "stowed": False,
                "navigation_mode": False,
            }

        if not switch_navigation:
            return {
                "success": True,
                "message": "Stretch3 stow 완료",
                "stowed": True,
                "navigation_mode": False,
                "stow": stow_result,
            }

        nav_result = _call_stretch_mode_service("navigation", mode_timeout_sec)
        if not nav_result.get("success", False):
            return {
                "success": False,
                "message": f"stow는 완료했지만 navigation 모드 전환 실패: {nav_result.get('message', '')}",
                "stowed": True,
                "navigation_mode": False,
                "stow": stow_result,
                "navigation": nav_result,
            }

        return {
            "success": True,
            "message": "Stretch3 팔/그리퍼 stow 완료 및 navigation 모드 전환 완료",
            "stowed": True,
            "navigation_mode": True,
            "stow": stow_result,
            "navigation": nav_result,
        }
