"""AMCL 초기 위치 설정 스킬 — /initialpose 토픽 발행"""

import logging
import math
from typing import Any

from geometry_msgs.msg import PoseWithCovarianceStamped

from robo_claw_agent.agent_node.last_pose_tracker import read_tracked_or_stored_pose
from robo_claw_agent.memory_manager.last_pose import (
    LAST_POSE_DISPLAY_NAME,
    is_last_pose_request,
)
from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)

#: 좌표를 사용자가 직접 지정했을 때의 위치 공분산(AMCL 파티클을 좁게 모은다).
_COVARIANCE_EXPLICIT = 0.05
#: 마지막 위치 복원 시의 공분산. 스냅숏 지연·AMCL 드리프트가 있을 수 있으므로
#: 더 넓게 두어 AMCL 이 라이다로 보정할 여지를 남긴다.
_COVARIANCE_LAST_POSE = 0.25


class SetInitialPoseSkill(BaseSkill):
    """AMCL의 초기 추정 위치를 설정합니다.

    /initialpose 토픽에 PoseWithCovarianceStamped 메시지를 발행하여
    AMCL 파티클 분포를 지정된 위치 주변으로 집중시킵니다.

    좌표 대신 기억된 장소명(`target_name`)을 받을 수 있습니다. "안방을 초기 위치로
    지정해줘"처럼 방 이름으로 요청하면 시맨틱 맵/RAG에서 좌표를 조회해 사용하며,
    해석 경로는 `navigate_to`의 `target_name`과 같습니다. 좌표를 추측하지 않으므로
    기억되지 않은 이름은 실패로 보고합니다.

    `target_name`에 "마지막 위치"류 표현을 주면 RAG에 유지되는 마지막 pose
    (x, y, yaw)를 복원합니다. 네비게이션 재기동으로 AMCL 초기 위치를 잃었을 때
    쓰는 경로입니다.
    """

    name = "set_initial_pose"
    input_schema = {
        "type": "object",
        "properties": {
            "target_name": {
                "type": "string",
                "description": "기억된 장소명(예: '안방', '거실') 또는 '마지막 위치'",
            },
            "x": {"type": "number", "description": "map frame x(m)"},
            "y": {"type": "number", "description": "map frame y(m)"},
            "yaw": {"type": "number", "description": "라디안(선택, 미지정 시 0.0)"},
        },
        "anyOf": [{"required": ["target_name"]}, {"required": ["x", "y"]}],
        "additionalProperties": False,
    }
    description = (
        "로봇의 초기 위치(AMCL)를 지정된 x, y 좌표와 yaw(라디안)로 설정합니다. "
        "좌표 대신 기억된 장소명(target_name) 또는 '마지막 위치'를 전달할 수 있습니다. "
        "'마지막 위치'는 로봇이 마지막으로 파악한 pose(x, y, yaw)를 복원합니다"
        "(네비게이션 재기동 후 위치 복구용). "
        "yaw를 생략하면 0rad(정면)로 설정합니다. "
        "예: {'x': 0.0, 'y': 0.0, 'yaw': 0.0}, {'target_name': '안방'}, "
        "{'target_name': '마지막 위치'}"
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if (err := self.require_node()):
            return err

        target_name = str(params.get("target_name") or "").strip()
        resolved_name = ""
        pose_source = "coordinates"
        recovered_yaw: float | None = None
        x_raw = params.get("x")
        y_raw = params.get("y")

        # 좌표가 없으면 장소명으로 해석한다. 좌표와 이름이 함께 오면 좌표를 우선한다
        # (사용자가 직접 지정한 값을 이름 조회로 덮어쓰지 않기 위함).
        if x_raw is None or y_raw is None:
            if not target_name:
                return self.fail_result(
                    "초기 위치를 지정하려면 좌표 x, y 또는 기억된 장소명(target_name)이 필요합니다."
                )
            resolved = self._resolve_target(target_name)
            if not resolved:
                logger.warning("SetInitialPose unresolved target: %s", target_name)
                return self.fail_result(
                    self._unresolved_message(target_name),
                    failure_reason="unknown_place",
                )
            resolved_name = resolved["name"]
            pose_source = resolved["source"]
            x_raw, y_raw = resolved["x"], resolved["y"]
            recovered_yaw = resolved.get("yaw")
            logger.info(
                "SetInitialPose target resolved (%s): %s -> (%.2f, %.2f)",
                pose_source,
                resolved_name,
                x_raw,
                y_raw,
            )

        try:
            x = float(x_raw)
            y = float(y_raw)
            explicit_yaw = params.get("yaw")
            if explicit_yaw is not None:
                yaw = float(explicit_yaw)
                yaw_defaulted = False
            elif recovered_yaw is not None:
                yaw = float(recovered_yaw)
                yaw_defaulted = False
            else:
                yaw = 0.0
                yaw_defaulted = True
        except (TypeError, ValueError):
            return self.fail_result(f"초기 위치 좌표값이 올바르지 않습니다: x={x_raw!r}, y={y_raw!r}")

        if not all(math.isfinite(v) for v in (x, y, yaw)):
            return self.fail_result(f"초기 위치 좌표값이 올바르지 않습니다: x={x}, y={y}, yaw={yaw}")

        from robo_claw_agent.agent_node.nav_safety import check_navigation_safety_gate

        gate_ok, gate_err = check_navigation_safety_gate(
            self.node, target_x=x, target_y=y, target_yaw=yaw, check_stow=False
        )
        if not gate_ok:
            logger.warning("SetInitialPose safety gate rejection: %s", gate_err)
            return self.fail_result(f"초기 위치 설정 거부: {gate_err}")

        logger.info("SetInitialPose: x=%.2f, y=%.2f, yaw=%.2f", x, y, yaw)

        try:
            init_pose = PoseWithCovarianceStamped()
            init_pose.header.frame_id = "map"
            try:
                init_pose.header.stamp = self.node.get_clock().now().to_msg()
            except Exception:
                init_pose.header.stamp.sec = 0
                init_pose.header.stamp.nanosec = 0
            init_pose.pose.pose.position.x = float(x)
            init_pose.pose.pose.position.y = float(y)
            init_pose.pose.pose.position.z = 0.0
            init_pose.pose.pose.orientation.z = math.sin(float(yaw) / 2.0)
            init_pose.pose.pose.orientation.w = math.cos(float(yaw) / 2.0)

            # 위치 공분산 (AMCL이 파티클을 좁은 영역에 집중하도록 작은 값 설정)
            covariance = (
                _COVARIANCE_LAST_POSE
                if pose_source == "last_pose"
                else _COVARIANCE_EXPLICIT
            )
            init_pose.pose.covariance[0] = covariance   # x
            init_pose.pose.covariance[7] = covariance   # y
            init_pose.pose.covariance[35] = covariance  # yaw

            # /initialpose 퍼블리셔 생성 및 발행
            from rclpy.qos import QoSProfile, ReliabilityPolicy

            qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE)
            pub = self.node.create_publisher(
                PoseWithCovarianceStamped, "/initialpose", qos
            )

            # 퍼블리셔 준비 대기 (최대 2초)
            import time

            t0 = time.monotonic()
            while not pub.wait_for_subscribers(timeout_sec=0.5):
                if time.monotonic() - t0 > 2.0:
                    logger.warning("No subscribers on /initialpose after 2s, publishing anyway")
                    break

            pub.publish(init_pose)
            logger.info("Initial pose published to /initialpose")

            # 발행 후 정리
            self.node.destroy_publisher(pub)

            place_label = f"'{resolved_name}' " if resolved_name else ""
            yaw_note = " (방향 미지정 → 0°)" if yaw_defaulted else ""
            self.send_user_message(
                f"📍 초기 위치를 설정했습니다: {place_label}({x:.2f}, {y:.2f}), "
                f"방향 {math.degrees(yaw):.1f}°"
            )
            return self.success_result(
                f"초기 위치 설정 성공: {place_label}x={x:.2f}, y={y:.2f}, "
                f"yaw={yaw:.2f} ({math.degrees(yaw):.1f}°){yaw_note}",
                x=x,
                y=y,
                yaw=yaw,
                target_name=resolved_name,
                yaw_defaulted=yaw_defaulted,
                pose_source=pose_source,
            )
        except Exception as e:
            logger.exception("SetInitialPose failed: %s", e)
            return self.fail_result(f"초기 위치 설정 실패: {e}")

    def _resolve_target(self, target_name: str) -> dict[str, Any] | None:
        """장소명 또는 '마지막 위치'를 pose 로 해석한다.

        Returns:
            ``{"name", "x", "y", "yaw"(선택), "source"}`` 또는 해석 실패 시 None.
        """
        if is_last_pose_request(target_name):
            return self._resolve_last_pose()
        return self._resolve_place(target_name)

    def _resolve_last_pose(self) -> dict[str, Any] | None:
        """RAG 에 유지되는 마지막 pose 를 복원한다(없으면 None)."""
        pose = read_tracked_or_stored_pose(self.node)
        if pose is None:
            return None
        try:
            x = float(pose["x"])
            y = float(pose["y"])
            yaw = float(pose.get("yaw") or 0.0)
        except (KeyError, TypeError, ValueError):
            return None
        if not all(math.isfinite(v) for v in (x, y, yaw)):
            return None
        if str(pose.get("frame") or "map") != "map":
            # odom 좌표는 map 초기 위치로 쓸 수 없다.
            return None
        return {
            "name": LAST_POSE_DISPLAY_NAME,
            "x": x,
            "y": y,
            "yaw": yaw,
            "source": "last_pose",
        }

    def _unresolved_message(self, target_name: str) -> str:
        if is_last_pose_request(target_name):
            return (
                "아직 기록된 마지막 위치가 없어 초기 위치를 설정할 수 없습니다. "
                "로봇이 위치를 파악한 뒤 다시 시도하거나 x, y 좌표를 직접 지정하세요."
            )
        return (
            f"'{target_name}'의 기억된 좌표를 찾지 못해 초기 위치를 설정할 수 없습니다. "
            "장소를 먼저 등록하거나 x, y 좌표를 직접 지정하세요."
        )

    def _resolve_place(self, target_name: str) -> dict[str, Any] | None:
        """기억된 장소명을 좌표로 해석한다(navigate_to와 같은 경로).

        시맨틱 맵 → RAG 우선순위 스캔 → 자연어 검색 순으로 조회하며, 좌표가 유한하지
        않거나 없으면 None을 돌려준다(추측 좌표로 초기 위치를 잡지 않는다).
        """
        memory = getattr(self.node, "_memory", None)
        if memory is None or not hasattr(memory, "get_object_location"):
            return None

        from .core import _resolve_target_coordinates

        info = _resolve_target_coordinates(memory, target_name)
        if not info:
            return None
        position = info.get("position") or {}
        try:
            x = float(position["x"])
            y = float(position["y"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        return {
            "name": str(info.get("name") or target_name),
            "x": x,
            "y": y,
            "source": "place",
        }
