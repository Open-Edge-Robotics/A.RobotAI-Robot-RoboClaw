"""자리 요청 스킬 — 동료 로봇에게 내 목표 지점을 비켜줄 것을 요청한다.

``navigate_to`` 가 목표 지점에 도달하지 못했을 때, 그 원인이 동료의 점유인지
확인하고 양보를 협상하는 요청자 측 스킬이다. 서비스 이름 해석과 응답 종합은
순수 함수로 분리해 ROS 없이 테스트한다.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from robo_claw_agent.domain.yield_slot import (
    ACCEPTED,
    BUSY,
    DEFAULT_RADIUS_M,
    DENIED,
    NOT_NEEDED,
    Pose2D,
)
from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)

YIELD_SLOT_SERVICE_TYPE = "robo_claw_msgs/srv/YieldSlot"
YIELD_SLOT_SERVICE_SUFFIX = "/yield_slot"
# 서비스 호출 상한(초). 수신측이 실제 이동까지 마치고 응답하므로 넉넉히 잡는다.
YIELD_CALL_TIMEOUT_SEC = 30.0
# 재시도 안내를 받았을 때 기다릴 최대 시간(초). 이보다 길면 포기하고 보고한다.
MAX_RETRY_WAIT_SEC = 10.0
# gRPC 중계 요청 대기 상한(초). 수신측이 실제 이동까지 마치고 응답한다.
YIELD_GRPC_TIMEOUT_SEC = 90.0

_ACCEPTED = ACCEPTED
_BUSY = BUSY
_DENIED = DENIED
_NOT_NEEDED = NOT_NEEDED


@dataclass(frozen=True)
class PeerYieldOutcome:
    """동료 한 대의 양보 응답."""

    peer_name: str
    decision: str
    reason: str = ""
    retry_after_sec: float = 0.0
    yield_spot: tuple[float, float] | None = None
    message: str = ""

    @property
    def accepted(self) -> bool:
        return self.decision == _ACCEPTED


def resolve_yield_slot_services(
    services: Iterable[tuple[str, Sequence[str]]], *, exclude_agent_id: str = ""
) -> dict[str, str]:
    """ROS 서비스 목록에서 ``peer_name -> yield_slot 서비스 이름`` 매핑을 만든다.

    서비스 이름은 ``/<agent_id>/<node_name>/yield_slot`` 형태이므로 첫 경로
    세그먼트를 peer 이름으로 쓴다. 자기 자신은 제외한다.
    """
    resolved: dict[str, str] = {}
    excluded = str(exclude_agent_id or "").strip()
    for name, types in services:
        if YIELD_SLOT_SERVICE_TYPE not in tuple(types):
            continue
        if not str(name).endswith(YIELD_SLOT_SERVICE_SUFFIX):
            continue
        parts = [p for p in str(name).split("/") if p]
        if len(parts) < 2:
            continue
        peer_name = parts[0]
        if peer_name == excluded:
            continue
        # 같은 peer 에 중복 서비스가 있으면 이름이 짧은 쪽(상위 네임스페이스)을 쓴다.
        if peer_name not in resolved or len(name) < len(resolved[peer_name]):
            resolved[peer_name] = str(name)
    return resolved


def summarize_outcomes(outcomes: Sequence[PeerYieldOutcome]) -> dict[str, Any]:
    """동료들의 응답을 요청자 관점의 결과로 종합한다.

    성공은 "한 대 이상이 실제로 비켜섰다"일 때만 참이다. 모두 점유 상태가
    아니었다면(``not_needed``) 내 이동 실패는 동료 때문이 아니므로 그 사실을
    구분해 돌려준다 — 호출자가 다른 원인을 찾아야 한다.
    """
    if not outcomes:
        return {
            "success": False,
            "reason": "no_peer_available",
            "message": "양보를 요청할 수 있는 동료를 찾지 못했습니다.",
            "yielded_peers": [],
            "blocking_peers": [],
            "retry_after_sec": 0.0,
        }

    accepted = [o for o in outcomes if o.accepted]
    if accepted:
        spots = {
            o.peer_name: {"x": round(o.yield_spot[0], 3), "y": round(o.yield_spot[1], 3)}
            for o in accepted
            if o.yield_spot is not None
        }
        return {
            "success": True,
            "reason": "peer_yielded",
            "message": f"동료 {len(accepted)}대가 자리를 비켜섰습니다.",
            "yielded_peers": [o.peer_name for o in accepted],
            "yield_spots": spots,
            "blocking_peers": [],
            "retry_after_sec": 0.0,
        }

    blocking = [o for o in outcomes if o.decision in {_BUSY, _DENIED}]
    if not blocking:
        return {
            "success": False,
            "reason": "no_peer_blocking",
            "message": "동료 중 누구도 요청한 지점을 점유하고 있지 않습니다.",
            "yielded_peers": [],
            "blocking_peers": [],
            "retry_after_sec": 0.0,
        }

    waits = [o.retry_after_sec for o in blocking if o.retry_after_sec > 0.0]
    return {
        "success": False,
        "reason": "peers_busy" if any(o.decision == _BUSY for o in blocking) else "yield_refused",
        "message": "; ".join(
            f"{o.peer_name}: {o.message or o.reason or o.decision}" for o in blocking
        ),
        "yielded_peers": [],
        "blocking_peers": [o.peer_name for o in blocking],
        "retry_after_sec": min(waits) if waits else 0.0,
    }


class RequestYieldSlotSkill(BaseSkill):
    """동료 로봇에게 내가 도달하려는 지점을 비켜줄 것을 요청한다."""

    name = "request_yield_slot"
    side_effects = ("peer_request",)
    description = (
        "`navigate_to`가 목표 지점에 도달하지 못했고 그 자리를 동료 로봇이 점유하고 "
        "의심될 때, 동료에게 자리를 비켜줄 것을 요청합니다. 요청을 받은 동료는 자기 "
        "로컬 정책으로 판정하고, 수락하면 안전한 다른 지점으로 실제 이동한 뒤 응답합니다. "
        "파라미터: target_place(도달하려는 장소 이름) 또는 target_x/target_y(map 좌표) 중 "
        "하나, peer_name(선택, 특정 동료만 지정), radius_m(선택, 점유 판정 반경), "
        "max_attempts(선택, 기본 2). 두 로봇이 같은 맵을 쓰지 않으면 target_place를 쓰세요."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "target_x": {"type": "number"},
            "target_y": {"type": "number"},
            "target_place": {"type": "string"},
            "peer_name": {"type": "string"},
            "radius_m": {"type": "number", "minimum": 0.1, "maximum": 2.0},
            "max_attempts": {"type": "integer", "minimum": 1, "maximum": 3},
        },
        "oneOf": [{"required": ["target_place"]}, {"required": ["target_x", "target_y"]}],
        "additionalProperties": False,
    }

    _DEFAULT_MAX_ATTEMPTS = 2

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if err := self.require_node():
            return err

        place = str(params.get("target_place") or "").strip()
        target: Pose2D | None = None
        if not place:
            try:
                target = Pose2D(float(params["target_x"]), float(params["target_y"]), 0.0)
            except (KeyError, TypeError, ValueError):
                return self.fail_result("target_place 또는 target_x/target_y가 필요합니다.")
            if not target.is_finite():
                return self.fail_result("목표 좌표가 유효하지 않습니다.")

        radius = self._parse_radius(params.get("radius_m"))
        if radius is None:
            return self.fail_result("radius_m은 0.1~2.0 사이 값이어야 합니다.")
        attempts = self._parse_attempts(params.get("max_attempts"))
        if attempts is None:
            return self.fail_result("max_attempts는 1~3 사이 정수여야 합니다.")

        peer_names = self._target_peers(params.get("peer_name"))
        if not peer_names:
            return self.fail_result("양보를 요청할 동료를 찾을 수 없습니다.")

        requester_pose = self._requester_pose()
        # 자기 맵에서 그 장소 반경 안에 있는지 스스로 판정한다. 좌표를 로봇
        # 사이에 넘기지 않으므로 맵을 공유하지 않아도 동시 점유 판정이 된다.
        requester_at_target = self._requester_at_target(place, target, radius)
        for attempt in range(attempts):
            outcomes = self._query_peers(
                peer_names, target, radius, requester_pose, place, requester_at_target
            )
            summary = summarize_outcomes(outcomes)
            if summary["success"] or summary["reason"] != "peers_busy":
                return self._result_from_summary(summary)
            if attempt + 1 >= attempts:
                return self._result_from_summary(summary)
            wait_sec = float(summary.get("retry_after_sec") or 0.0)
            if wait_sec <= 0.0 or wait_sec > MAX_RETRY_WAIT_SEC:
                return self._result_from_summary(summary)
            if not self._sleep(wait_sec):
                return self._result_from_summary(summary)

        return self._result_from_summary(summarize_outcomes(outcomes))

    # --- 내부 -----------------------------------------------------------------

    def _result_from_summary(self, summary: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": bool(summary["success"]),
            "message": summary["message"],
            "reason": summary["reason"],
            "yielded_peers": summary.get("yielded_peers", []),
            "blocking_peers": summary.get("blocking_peers", []),
            "retry_after_sec": summary.get("retry_after_sec", 0.0),
            **({"yield_spots": summary["yield_spots"]} if summary.get("yield_spots") else {}),
        }

    @staticmethod
    def _parse_radius(value: Any) -> float | None:
        if value is None:
            return DEFAULT_RADIUS_M
        try:
            radius = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(radius) or not 0.1 <= radius <= 2.0:
            return None
        return radius

    def _parse_attempts(self, value: Any) -> int | None:
        if value is None:
            return self._DEFAULT_MAX_ATTEMPTS
        if isinstance(value, bool):
            return None
        try:
            attempts = int(value)
        except (TypeError, ValueError):
            return None
        return attempts if 1 <= attempts <= 3 else None

    def _target_peers(self, requested: Any) -> list[str]:
        """요청 대상 peer 이름 목록. 지정이 없으면 발견된 peer 전체."""
        wanted = str(requested or "").strip()
        if wanted:
            return [wanted]
        peers = sorted(self._resolve_services())
        if peers:
            return peers
        # 같은 ROS 도메인이 아니면 서비스가 없다. gRPC로 연결된 피어 이름을 쓴다.
        return self._grpc_peer_names()

    def _grpc_peer_names(self) -> list[str]:
        """메신저 클라이언트가 알고 있는 연결된 피어 이름 목록."""
        client = getattr(self.node, "_list_peers_client", None)
        if client is None:
            return []
        from robo_claw_agent import ros_future
        from robo_claw_msgs.srv import ListPeers

        ok, response = ros_future.call_service(
            client,
            ListPeers.Request(),
            call_timeout_sec=10.0,
            not_found_message="피어 목록 서비스를 찾을 수 없습니다.",
            timeout_message="피어 목록 조회 시간 초과",
        )
        if not ok or not getattr(response, "success", False):
            return []
        return sorted(
            str(peer.peer_name)
            for peer in response.peers
            if bool(getattr(peer, "connected", False))
        )

    def _resolve_services(self) -> dict[str, str]:
        node = self.node
        try:
            services = node.get_service_names_and_types()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Yield] 서비스 목록 조회 실패: %s", exc)
            return {}
        return resolve_yield_slot_services(
            services, exclude_agent_id=str(getattr(node, "_agent_id", ""))
        )

    def _requester_pose(self) -> Pose2D | None:
        try:
            from robo_claw_agent.skills.yield_slot_service import _current_map_pose

            return _current_map_pose(self.node)
        except Exception:  # noqa: BLE001
            return None

    def _requester_at_target(self, place: str, target: Pose2D | None, radius: float) -> bool | None:
        """요청자가 자기 맵 기준으로 목표 반경 안에 있는지. 판정 불가면 None."""
        if not place:
            if target is None:
                return None
            pose = self._requester_pose()
            if pose is None:
                return None
            return math.dist((pose.x, pose.y), (target.x, target.y)) <= radius
        try:
            from robo_claw_agent.skills.yield_slot_service import resolve_place_in_local_map

            local_place = resolve_place_in_local_map(self.node, place)
        except Exception:  # noqa: BLE001
            return None
        if local_place is None or target is not None:
            return None
        pose = self._requester_pose()
        if pose is None:
            return None
        return math.dist((pose.x, pose.y), (local_place.x, local_place.y)) <= radius

    def _sleep(self, wait_sec: float) -> bool:
        """재시도 대기. 양보 요청 스킬은 태스크 큐 워커에서 돌므로 짧게만 기다린다."""
        import time

        time.sleep(wait_sec)
        return True

    def _query_peers(
        self,
        peer_names: Sequence[str],
        target: Pose2D | None,
        radius: float,
        requester_pose: Pose2D | None,
        place: str = "",
        requester_at_target: bool | None = None,
    ) -> list[PeerYieldOutcome]:
        services = self._resolve_services()
        outcomes: list[PeerYieldOutcome] = []
        for peer_name in peer_names:
            service_name = services.get(peer_name)
            if service_name is not None:
                outcomes.append(
                    self._call_peer(
                        service_name,
                        peer_name,
                        target,
                        radius,
                        requester_pose,
                        place,
                        requester_at_target,
                    )
                )
                continue
            # 같은 ROS 도메인이 아니면 서비스가 발견되지 않는다. 이때는 gRPC로
            # 구조화 요청을 중계한다(메신저 클라이언트 노드가 서버 역할).
            outcome = self._call_peer_via_grpc(
                peer_name, target, radius, requester_pose, place, requester_at_target
            )
            if outcome is not None:
                outcomes.append(outcome)
                continue
            outcomes.append(
                PeerYieldOutcome(
                    peer_name=peer_name,
                    decision=_DENIED,
                    reason="service_not_found",
                    message="yield_slot 서비스를 찾지 못했습니다.",
                )
            )
        return outcomes

    def _call_peer_via_grpc(
        self,
        peer_name: str,
        target: Pose2D | None,
        radius: float,
        requester_pose: Pose2D | None,
        place: str = "",
        requester_at_target: bool | None = None,
    ) -> PeerYieldOutcome | None:
        """gRPC 피어 중계로 양보를 요청한다. 클라이언트가 없으면 None."""
        client = getattr(self.node, "_call_peer_yield_client", None)
        if client is None:
            return None

        from robo_claw_agent import ros_future
        from robo_claw_msgs.srv import CallPeerYield

        request = CallPeerYield.Request()
        request.peer_name = peer_name
        request.target_place = place
        request.has_target_coords = target is not None
        if target is not None:
            request.target_x = target.x
            request.target_y = target.y
        if requester_at_target is not None:
            request.has_requester_at_target = True
            request.requester_at_target = bool(requester_at_target)
        request.radius_m = radius
        request.timeout_sec = YIELD_GRPC_TIMEOUT_SEC
        if requester_pose is not None:
            request.requester_x = requester_pose.x
            request.requester_y = requester_pose.y
        else:
            request.requester_x = float("nan")
            request.requester_y = float("nan")

        ok, response = ros_future.call_service(
            client,
            request,
            call_timeout_sec=YIELD_GRPC_TIMEOUT_SEC + 10.0,
            not_found_message="메신저 클라이언트의 call_peer_yield 서비스를 찾을 수 없습니다.",
            timeout_message="자리 양보 gRPC 요청 시간 초과",
            ready_timeout_sec=5.0,
        )
        if not ok:
            return PeerYieldOutcome(
                peer_name=peer_name,
                decision=_DENIED,
                reason="grpc_call_failed",
                message=str(response),
            )

        spot = None
        if bool(getattr(response, "has_yield_spot", False)):
            spot = (float(response.yield_spot_x), float(response.yield_spot_y))
        return PeerYieldOutcome(
            peer_name=peer_name,
            decision=str(response.decision or ""),
            reason=str(response.reason or ""),
            retry_after_sec=float(getattr(response, "retry_after_sec", 0.0) or 0.0),
            yield_spot=spot,
            message=str(
                getattr(response, "message", "") or getattr(response, "error_message", "") or ""
            ),
        )

    def _call_peer(
        self,
        service_name: str,
        peer_name: str,
        target: Pose2D | None,
        radius: float,
        requester_pose: Pose2D | None,
        place: str = "",
        requester_at_target: bool | None = None,
    ) -> PeerYieldOutcome:
        from rclpy.callback_groups import ReentrantCallbackGroup  # type: ignore[import]

        from robo_claw_agent import ros_future
        from robo_claw_msgs.srv import YieldSlot

        client = self.node.create_client(
            YieldSlot, service_name, callback_group=ReentrantCallbackGroup()
        )
        try:
            request = YieldSlot.Request()
            request.requester_id = str(getattr(self.node, "_agent_id", ""))
            request.target_place = place
            request.has_target_coords = target is not None
            if target is not None:
                request.target_x = target.x
                request.target_y = target.y
            if requester_at_target is not None:
                request.has_requester_at_target = True
                request.requester_at_target = bool(requester_at_target)
            request.radius_m = radius
            request.timeout_sec = YIELD_CALL_TIMEOUT_SEC
            if requester_pose is not None:
                request.requester_x = requester_pose.x
                request.requester_y = requester_pose.y
            else:
                # 위치를 모르면 nan — 수신측은 동시 점유 판정을 보수적으로 한다.
                request.requester_x = float("nan")
                request.requester_y = float("nan")

            ok, response = ros_future.call_service(
                client,
                request,
                call_timeout_sec=YIELD_CALL_TIMEOUT_SEC,
                not_found_message=f"{peer_name}의 yield_slot 서비스를 찾을 수 없습니다.",
                timeout_message="양보 요청 응답 시간 초과",
                ready_timeout_sec=5.0,
            )
            if not ok:
                return PeerYieldOutcome(
                    peer_name=peer_name,
                    decision=_DENIED,
                    reason="call_failed",
                    message=str(response),
                )
            spot = None
            if bool(getattr(response, "has_yield_spot", False)):
                spot = (float(response.yield_spot_x), float(response.yield_spot_y))
            return PeerYieldOutcome(
                peer_name=peer_name,
                decision=str(response.decision or ""),
                reason=str(response.reason or ""),
                retry_after_sec=float(getattr(response, "retry_after_sec", 0.0) or 0.0),
                yield_spot=spot,
                message=str(getattr(response, "message", "") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Yield] %s 호출 실패: %s", peer_name, exc)
            return PeerYieldOutcome(
                peer_name=peer_name,
                decision=_DENIED,
                reason="call_failed",
                message=f"양보 요청 실패: {exc}",
            )
        finally:
            try:
                self.node.destroy_client(client)
            except Exception:  # noqa: BLE001
                pass
