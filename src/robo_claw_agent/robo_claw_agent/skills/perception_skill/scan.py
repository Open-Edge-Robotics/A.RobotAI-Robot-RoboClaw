import logging
import math
import threading
import time
from typing import Any

from sensor_msgs.msg import LaserScan

from robo_claw_agent.skill_manager import BaseSkill

from . import globals
from .core import (
    _depth_point_for_pixel,
    _distance_at_bearing,
    _fetch_depth_frame,
    _resolve_class_name,
    _target_pose_metadata,
)

if globals._VISION_MSGS_AVAILABLE:
    from vision_msgs.msg import Detection2DArray

logger = logging.getLogger(__name__)


class ScanRoomSkill(BaseSkill):
    """제자리 360° 회전으로 방 안의 모든 객체를 시맨틱 맵에 등록합니다."""

    name = "scan_room"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {"max_turn_deg": {"type": "number"}, "step_deg": {"type": "number"}},
        "additionalProperties": True,
    }
    description = (
        "로봇이 제자리에서 360° 회전하며 ONNX 객체 인식 결과를 수집하고 "
        "발견된 모든 물체를 시맨틱 맵에 등록합니다. "
        "파라미터: step_deg(45), min_score(0.4), settle_sec(1.5). "
        "use_vision:=true 로 시스템이 기동된 경우에만 동작합니다."
    )

    def __init__(self, cancel_event: threading.Event | None = None) -> None:
        super().__init__()
        self._cancel_event = cancel_event

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if not globals._VISION_MSGS_AVAILABLE:
            return {
                "success": False,
                "message": "vision_msgs 미설치. ros-humble-vision-msgs 설치 필요.",
            }
        if self.node is None:
            return {"success": False, "message": "ROS 노드에 접근할 수 없습니다."}

        step_deg = float(params.get("step_deg", 45.0))
        min_score = float(params.get("min_score", 0.4))
        settle_sec = float(params.get("settle_sec", 1.5))
        detections_topic = self.get_string_param(
            params, "detections_topic", "/object_detector_node/detections"
        )
        lidar_topic = self.get_string_param(params, "lidar_topic", "/scan")
        h_fov_rad = math.radians(float(params.get("h_fov_deg", 60.0)))
        image_width = float(params.get("image_width", 640))

        try:
            from robo_claw_agent.skills.navigation_skill import (  # type: ignore
                _get_nav2_dependency_error,
                _send_spin_goal,
                _wait_for_goal_result,
            )
            from robo_claw_agent.skills.navigation_skill.core import (  # type: ignore
                _spin_time_allowance_sec,
            )
        except ImportError as exc:
            return {"success": False, "message": f"navigation_skill import 실패: {exc}"}

        nav2_err = _get_nav2_dependency_error()
        if nav2_err:
            return {"success": False, "message": nav2_err}

        steps = max(1, int(360.0 / step_deg))
        step_rad = math.radians(step_deg)
        found: dict[str, Any] = {}  # class_name → best info
        rotation_failures: list[dict[str, Any]] = []

        logger.info("[scan_room] Starting: %d steps x %.1f deg", steps, step_deg)
        self.send_user_message(f"방 스캔을 시작합니다 ({steps}방향 탐색).")

        # 각 스텝 회전 시간 한도를 각도에 비례해 산출한다.
        step_allowance = _spin_time_allowance_sec(step_rad)
        cancelled = False

        for step in range(steps):
            if self._cancel_event is not None and not self._cancel_event.is_set():
                cancelled = True
                break

            ok, msg, goal_handle = _send_spin_goal(
                self.node, step_rad, time_allowance_sec=step_allowance
            )
            if ok and goal_handle:
                rotation_ok, rotation_msg = _wait_for_goal_result(
                    goal_handle,
                    timeout_sec=step_allowance + 15.0,
                    label=f"scan_room 회전 {step + 1}/{steps}",
                )
                if not rotation_ok:
                    rotation_failures.append({"step": step + 1, "message": rotation_msg})
            else:
                logger.warning("[scan_room] step %d rotation failed: %s", step + 1, msg)
                rotation_failures.append({"step": step + 1, "message": msg})

            if self._cancel_event is not None and not self._cancel_event.is_set():
                cancelled = True
                break

            time.sleep(settle_sec)
            if self._cancel_event is not None and not self._cancel_event.is_set():
                cancelled = True
                break

            det_msg = self.wait_for_message(
                Detection2DArray,
                detections_topic,
                timeout_sec=2.0,
                max_age_sec=2.0,
            )
            if self._cancel_event is not None and not self._cancel_event.is_set():
                cancelled = True
                break
            if not det_msg:
                logger.debug("[scan_room] step %d: no detection results", step + 1)
                continue

            pose = self.get_map_pose()
            scan = self.wait_for_message(LaserScan, lidar_topic, timeout_sec=1.0, max_age_sec=1.0)
            depth_frame = _fetch_depth_frame(self, params)

            for det in det_msg.detections:
                for r in det.results:
                    score = r.hypothesis.score
                    if score < min_score:
                        continue
                    class_name = _resolve_class_name(r.hypothesis.class_id)
                    cx = det.bbox.center.position.x
                    cy = det.bbox.center.position.y
                    bearing = (cx / image_width - 0.5) * h_fov_rad

                    depth_point_map, _depth_distance_m = _depth_point_for_pixel(
                        self, depth_frame, params, cx, cy
                    )

                    distance_m = _distance_at_bearing(scan, bearing) if scan else None
                    world_x = world_y = None
                    position_source = ""
                    if pose and distance_m is not None:
                        wb = pose["yaw"] + bearing
                        world_x = pose["x"] + distance_m * math.cos(wb)
                        world_y = pose["y"] + distance_m * math.sin(wb)
                        position_source = "lidar_bearing"

                    # depth+TF 추정치가 있으면 더 정밀하므로 대표 좌표로 우선 사용한다
                    # (find_object와 동일한 정책 — perception_skill/detect.py 참고).
                    if depth_point_map is not None:
                        world_x, world_y = depth_point_map[0], depth_point_map[1]
                        position_source = "depth_tf"

                    prev = found.get(class_name, {})
                    if score > prev.get("score", -1.0):
                        found[class_name] = {
                            "score": round(score, 4),
                            "distance_m": round(distance_m, 3) if distance_m is not None else None,
                            "world_x": round(world_x, 3) if world_x is not None else None,
                            "world_y": round(world_y, 3) if world_y is not None else None,
                            "depth_available": depth_point_map is not None,
                            "step": step + 1,
                        }
                        if world_x is not None and self.node and hasattr(self.node, "_memory"):
                            try:
                                metadata: dict[str, Any] = {
                                    "score": round(score, 4),
                                    "source": "scan_room",
                                    "kind": "object",
                                    "position_source": position_source,
                                    "frame_id": pose["frame"],
                                }
                                if depth_point_map is not None:
                                    metadata["target_pose"] = _target_pose_metadata(
                                        self, depth_point_map
                                    )
                                self.node._memory.add_object_location(
                                    class_name,
                                    world_x,
                                    world_y,
                                    metadata=metadata,
                                )
                            except Exception as exc:
                                logger.warning(
                                    "[scan_room] Map registration failed (%s): %s", class_name, exc
                                )

        success = not rotation_failures and not cancelled
        if cancelled:
            result_msg = f"스캔 취소됨: {len(found)}개 객체를 확인했습니다."
        else:
            result_msg = f"스캔 {'완료' if success else '부분 실패'}: {len(found)}개 객체 발견 — {', '.join(found.keys()) or '없음'}"
        self.send_user_message(result_msg)
        logger.info("[scan_room] %s", result_msg)

        # RAG 저장 (발견 객체 + 위치)
        if (
            found
            and self.node
            and hasattr(self.node, "_memory")
            and getattr(self.node, "_enable_rag", False)
        ):
            try:
                rag_pose = self.get_map_pose()
                loc_str = ""
                if rag_pose:
                    loc_str = (
                        f" [위치 x={round(rag_pose['x'], 2)} y={round(rag_pose['y'], 2)} "
                        f"frame={rag_pose['frame']}]"
                    )
                obj_details = ", ".join(
                    f"{name}(거리={info.get('distance_m')}m)" if info.get("distance_m") else name
                    for name, info in found.items()
                )
                rag_text = f"{loc_str} 방 스캔 결과: {obj_details}"
                self.node._memory.add_knowledge(  # type: ignore
                    rag_text,
                    {
                        "type": "scan_room",
                        "detected_objects": list(found.keys()),
                        "source": "scan_room",
                    },
                )
                logger.info("[scan_room] RAG save complete")
            except Exception as _rag_e:
                logger.debug("[scan_room] RAG save failed: %s", _rag_e)

        return {
            "success": success,
            "message": result_msg,
            "found_objects": found,
            "total_steps": steps,
            "rotation_failures": rotation_failures,
            "partial_success": bool(found) and not success,
        }


class DescribeSurroundingsSkill(BaseSkill):
    """4방향 거리 + ONNX 인식 + VLM 분석을 조합한 상황 종합 보고서를 생성합니다."""

    name = "describe_surroundings"
    answer_mode = "informational"
    side_effects = ("base_motion",)
    exclusive_resources = ("base_control",)
    input_schema = {
        "type": "object",
        "properties": {
            "use_vlm": {"type": "boolean", "default": True},
            "min_score": {"type": "number", "default": 0.4},
            "capture_4way": {"type": "boolean", "default": False},
            "observation_prompt": {
                "type": "string",
                "maxLength": 1200,
                "description": "4방향 이미지별 분석에 사용할 관찰 지침입니다.",
            },
            "return_to_start": {
                "type": "boolean",
                "default": False,
                "description": "4방향 관찰 후 시작 방향으로 복귀합니다.",
            },
        },
        "additionalProperties": True,
    }
    description = (
        "라이다로 4방향 거리를 측정하고, ONNX로 현재 시야의 객체를 인식하며, "
        "VLM으로 장면을 분석하여 통합 상황 보고서를 반환합니다. "
        "현재 위치(map 좌표)도 함께 보고합니다. "
        "capture_4way:=true 로 호출하면 제자리에서 4방향(정면/우/후/좌)을 90°씩 회전·촬영하며 "
        "각 방향을 VLM으로 분석하고, 종합하여 '여기가 어디인지' 위치를 판단합니다. "
        "이 모드는 로봇 베이스를 회전하며, observation_prompt로 이미지별 관찰 지침을 지정하고 "
        "return_to_start:=true로 시작 방향 복귀를 요청할 수 있습니다. 회전 실패 방향의 이미지는 분석에 사용하지 않습니다. "
        '("로봇이 어디 있어?", "사방 둘러보고 위치 알려줘" 류 명령에 사용) '
        "파라미터: use_vlm(true), min_score(0.4), capture_4way(false), "
        "observation_prompt(선택), return_to_start(false)."
    )

    def __init__(self, active_event: threading.Event | None = None) -> None:
        super().__init__()
        # 이벤트가 set이면 바깥 자율 작업이 활성 상태이며, clear이면 중단 요청입니다.
        self._active_event = active_event

    def _is_active(self) -> bool:
        return self._active_event is None or self._active_event.is_set()

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        lidar_topic = self.get_string_param(params, "lidar_topic", "/scan")
        detections_topic = self.get_string_param(
            params, "detections_topic", "/object_detector_node/detections"
        )
        camera_topic = self.get_string_param(params, "camera_topic", "")
        del camera_topic  # get_opencv_image가 params에서 직접 읽으므로 별도 전달 불필요
        min_score = float(params.get("min_score", 0.4))
        use_vlm = str(params.get("use_vlm", "true")).lower() not in ("false", "0", "no")
        capture_4way = str(params.get("capture_4way", "false")).lower() in (
            "true",
            "1",
            "yes",
        )

        report: dict[str, Any] = {"success": True}

        scan = self.wait_for_message(LaserScan, lidar_topic, timeout_sec=2.0, max_age_sec=1.0)
        surroundings: dict[str, str] = {}
        if scan:
            for direction, angle in [
                ("front", 0.0),
                ("left", math.pi / 2),
                ("right", -math.pi / 2),
                ("back", math.pi),
            ]:
                d = _distance_at_bearing(scan, angle)
                if d is None:
                    surroundings[direction] = "측정 불가 (개방)"
                else:
                    label = "장애물" if d < 1.0 else ("가까움" if d < 2.5 else "개방")
                    surroundings[direction] = f"{d:.1f}m — {label}"
        report["surroundings"] = surroundings

        detected: list[str] = []
        if globals._VISION_MSGS_AVAILABLE:
            det_msg = self.wait_for_message(
                Detection2DArray,
                detections_topic,
                timeout_sec=3.0,
                max_age_sec=2.0,
            )
            if det_msg:
                seen: dict[str, float] = {}
                for det in det_msg.detections:
                    for r in det.results:
                        score = r.hypothesis.score
                        if score < min_score:
                            continue
                        cn = _resolve_class_name(r.hypothesis.class_id)
                        if score > seen.get(cn, -1.0):
                            seen[cn] = score
                detected = [f"{cn}({s:.2f})" for cn, s in sorted(seen.items(), key=lambda x: -x[1])]
        report["detected_objects"] = detected

        # 현재 위치를 map 프레임 기준으로 보고
        report["pose"] = self.get_map_pose()

        # 4방향 회전 촬영 모드
        if capture_4way:
            return self._capture_4way(params, report)

        scene_description = ""
        if use_vlm and self.node and hasattr(self.node, "_llm") and self.node._llm:
            try:
                from robo_claw_agent.skills.vision_skill import AnalyzeSceneSkill

                scene_skill = AnalyzeSceneSkill()
                scene_skill.set_node(self.node)
                scene_result = scene_skill.execute(params)
                scene_description = scene_result.get(
                    "description",
                    scene_result.get("scene_description", scene_result.get("message", "")),
                )
            except Exception as exc:
                logger.warning("[describe_surroundings] VLM analysis failed: %s", exc)
        report["scene_description"] = scene_description

        objects_str = ", ".join(detected) if detected else "없음"
        report["message"] = f"상황 분석 완료 — 객체: {objects_str}"
        return report

    def _rotate_to_target_yaw(self, target_yaw: float, label: str) -> tuple[bool, str, float]:
        """목표 yaw로 제자리 회전. Spin 실패 시 NavigateToPose 폴백을 시도한다.

        face_direction 스킬과 동일한 복구 전략을 사용한다. ``/spin`` 액션이 이 로봇에서
        잦은 abort/타임아웃을 보이므로, 실패 시 현재 위치 그대로 target_yaw만 지정한
        NavigateToPose 목표로 재시도한다.

        Returns:
            (success, message, delta_deg). delta_deg는 회전이 필요 없었으면 0.0.
        """
        from robo_claw_agent.skills.limits import check_rotation
        from robo_claw_agent.skills.navigation_skill.core import (  # type: ignore
            _rotate_via_cmd_vel,
            _rotate_via_navigation,
            _send_spin_goal,
            _spin_time_allowance_sec,
            _wait_for_goal_result,
        )
        from robo_claw_agent.skills.navigation_skill.orientation import (  # type: ignore
            shortest_angular_delta_rad,
        )

        pose = self.get_map_pose()
        if not pose:
            return False, "현재 로봇 방향을 가져올 수 없습니다.", 0.0

        current_yaw = float(pose["yaw"])
        angle_rad = shortest_angular_delta_rad(current_yaw, float(target_yaw))
        angle_deg = math.degrees(angle_rad)
        if abs(angle_deg) < 1.0:
            return True, "이미 목표 방향을 바라보고 있습니다.", 0.0

        robot_limits = getattr(self.node, "_robot_limits_dict", None)
        limit_error = check_rotation(angle_deg, robot_limits)
        if limit_error:
            return False, limit_error, round(angle_deg, 1)

        # 1순위: 네비게이션 방식 회전 (NavigateToPose - 가장 안정적이고 확실함)
        nav_ok, nav_msg = _rotate_via_navigation(
            self, float(target_yaw), timeout_sec=20.0, label=label
        )
        if nav_ok:
            return True, nav_msg, round(angle_deg, 1)

        logger.warning(
            "[describe_surroundings] %s 내비게이션 회전 불발(%s), cmd_vel 제자리 회전 폴백 시도",
            label,
            nav_msg,
        )

        # 2순위: direct cmd_vel P-control 회전 폴백
        cmd_ok, cmd_msg = _rotate_via_cmd_vel(self, float(target_yaw), timeout_sec=8.0)
        if cmd_ok:
            return True, cmd_msg, round(angle_deg, 1)

        # 3순위: Nav2 Spin action 폴백
        allowance = _spin_time_allowance_sec(angle_rad)
        accepted, message, goal_handle = _send_spin_goal(
            self.node, angle_rad, time_allowance_sec=allowance
        )
        success = False
        result_message = ""
        if accepted:
            success, result_message = _wait_for_goal_result(
                goal_handle, timeout_sec=allowance + 15.0, label=label
            )
        else:
            result_message = message

        return success, result_message, round(angle_deg, 1)

    def _capture_4way(self, params: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
        """제자리에서 4방향을 회전·촬영하며 VLM 분석 후 위치를 종합 판단한다."""
        import base64
        import time

        import cv2
        import numpy as np

        if self.node is None or not getattr(self.node, "_llm", None):
            report["success"] = False
            report["message"] = "4방향 분석에는 LLM(VLM)이 필요합니다."
            return report

        try:
            from robo_claw_agent.skills.navigation_skill import (  # type: ignore
                _get_nav2_dependency_error,
            )
            from robo_claw_agent.skills.navigation_skill.orientation import (  # type: ignore
                yaw_targets_from_offsets_rad,
            )
        except ImportError as exc:
            report["success"] = False
            report["message"] = f"navigation_skill import 실패: {exc}"
            return report

        nav2_err = _get_nav2_dependency_error()
        if nav2_err:
            report["success"] = False
            report["message"] = nav2_err
            return report

        # 회전 후 안정화 대기 시간. 흔들림이 있으면 늘리세요.
        settle_sec = float(params.get("settle_sec", 1.0))
        # 원위치 복귀 회전 생략 옵션. 위치 파악 목적이면 복귀 불필요.
        return_to_start = str(params.get("return_to_start", "false")).lower() in (
            "true",
            "1",
            "yes",
        )

        self.send_user_message("🧭 사방을 둘러보며 주변을 파악합니다...")

        start_pose = self.get_map_pose()
        if not start_pose:
            report["success"] = False
            report["message"] = "현재 로봇 방향을 가져올 수 없습니다. TF 또는 /odom을 확인하세요."
            return report

        start_yaw = float(start_pose["yaw"])

        # 시작 yaw 기준 정면/우/후/좌 절대 목표 yaw. 각 단계마다 현재 yaw를 다시 읽어 보정한다.
        target_yaws = yaw_targets_from_offsets_rad(
            start_yaw,
            [0.0, -math.pi / 2.0, math.pi, math.pi / 2.0],
        )
        directions = [
            ("정면", "Front", target_yaws[0]),
            ("우측", "Right", target_yaws[1]),
            ("후방", "Back", target_yaws[2]),
            ("좌측", "Left", target_yaws[3]),
        ]
        per_direction: dict[str, str] = {}
        per_direction_meta: dict[str, dict[str, Any]] = {}
        images: list[Any] = []  # (영문라벨, cv_img)
        ts = int(time.time())
        t_start = time.monotonic()

        cancelled = False
        observation_prompt = str(params.get("observation_prompt", "")).strip()[:1200]
        for ko_label, en_label, target_yaw in directions:
            if not self._is_active():
                cancelled = True
                break

            t_step = time.monotonic()
            rot_ok, rot_msg, delta_deg = self._rotate_to_target_yaw(
                target_yaw, f"방향 전환({ko_label})"
            )
            step_meta: dict[str, Any] = {
                "delta_deg": delta_deg,
                "rotate_ok": rot_ok,
                "rotate_msg": rot_msg,
                "image_received": False,
                "analysis_ok": False,
            }
            if not rot_ok:
                per_direction[ko_label] = f"방향 회전 실패: {rot_msg}"
                per_direction_meta[ko_label] = step_meta
                logger.warning(
                    "[describe_surroundings] %s rotation incomplete; skipping image: %s",
                    ko_label,
                    rot_msg,
                )
                continue

            if abs(delta_deg) >= 1.0:
                settle_deadline = time.monotonic() + max(0.0, settle_sec)
                while time.monotonic() < settle_deadline:
                    if not self._is_active():
                        cancelled = True
                        break
                    time.sleep(min(0.1, settle_deadline - time.monotonic()))
            if cancelled or not self._is_active():
                cancelled = True
                per_direction_meta[ko_label] = step_meta
                break

            cv_img, _topic, _comp = self.get_opencv_image(params, timeout_sec=10.0)
            if cv_img is None:
                per_direction[ko_label] = "이미지 수신 실패"
                per_direction_meta[ko_label] = step_meta
                logger.warning(
                    "[describe_surroundings] %s image reception failed (step %.1fs)",
                    ko_label,
                    time.monotonic() - t_step,
                )
                continue
            step_meta["image_received"] = True

            try:
                cv2.imwrite(f"/tmp/surroundings_{en_label}_{ts}.png", cv_img)
                _, buf = cv2.imencode(".png", cv_img)
                img_b64 = base64.b64encode(buf).decode("utf-8")
            except Exception as exc:
                per_direction[ko_label] = f"이미지 처리 오류: {exc}"
                per_direction_meta[ko_label] = step_meta
                continue

            try:
                base_prompt = observation_prompt or (
                    "보이는 공간/사물/구조를 한국어로 2~3문장으로 간결히 설명해줘."
                )
                vlm_prompt = f"이것은 로봇 {ko_label} 방향의 카메라 이미지야. {base_prompt}"
                description = str(self.node._llm.analyze_image(vlm_prompt, img_b64) or "").strip()
                per_direction[ko_label] = description or "VLM 분석 결과가 비어 있습니다."
                step_meta["analysis_ok"] = bool(description)
            except Exception as exc:
                per_direction[ko_label] = f"VLM 분석 실패: {exc}"
            images.append((en_label, cv_img.copy()))
            step_meta["step_sec"] = round(time.monotonic() - t_step, 2)
            per_direction_meta[ko_label] = step_meta
            logger.info(
                "[describe_surroundings] %s complete (%.1fs)",
                ko_label,
                time.monotonic() - t_step,
            )

        # 원위치 복귀는 중단 요청이 없는 경우에만 시도합니다.
        return_to_start_result: dict[str, Any] | None = None
        if return_to_start:
            if cancelled or not self._is_active():
                cancelled = True
                return_to_start_result = {
                    "success": False,
                    "message": "중단 요청으로 시작 방향 복귀를 생략했습니다.",
                }
            else:
                t_ret = time.monotonic()
                rot_ok, rot_msg, _delta = self._rotate_to_target_yaw(start_yaw, "원위치 복귀")
                return_to_start_result = {"success": rot_ok, "message": rot_msg}
                if not rot_ok:
                    logger.warning(
                        "[describe_surroundings] Return-to-start incomplete: %s", rot_msg
                    )
                logger.info(
                    "[describe_surroundings] Return-to-start finished (%.1fs)",
                    time.monotonic() - t_ret,
                )

        logger.info(
            "[describe_surroundings] 4-way capture complete (total %.1fs, images %d/%d)",
            time.monotonic() - t_start,
            len(images),
            len(directions),
        )

        # 4방향 이미지를 2×2 몽타주로 합성
        montage_path = None
        if images:
            try:
                cell_w, cell_h = 320, 240
                cells = []
                for en_label, img in images:
                    resized = cv2.resize(img, (cell_w, cell_h))
                    cv2.putText(
                        resized,
                        en_label,
                        (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        1.0,
                        (0, 255, 0),
                        2,
                    )
                    cells.append(resized)
                while len(cells) < 4:
                    cells.append(np.zeros((cell_h, cell_w, 3), dtype=np.uint8))
                grid = np.vstack([np.hstack(cells[:2]), np.hstack(cells[2:4])])
                montage_path = f"/tmp/surroundings_{ts}.png"
                cv2.imwrite(montage_path, grid)
            except Exception as exc:
                logger.warning("[describe_surroundings] Montage generation failed: %s", exc)

        # 4방향 설명 + 위치 좌표를 종합해 위치 판단
        pose = report.get("pose")
        pose_str = ""
        if pose:
            pose_str = (
                f"현재 map 좌표: x={pose['x']}, y={pose['y']}, "
                f"heading={pose['heading_deg']}도 (frame={pose['frame']}). "
            )
        successful_dirs = [
            label
            for label, _en_label, _target_yaw in directions
            if per_direction_meta.get(label, {}).get("rotate_ok") is True
            and per_direction_meta.get(label, {}).get("image_received") is True
            and per_direction_meta.get(label, {}).get("analysis_ok") is True
            and bool(per_direction.get(label, "").strip())
        ]
        failed_dirs = [
            label for label, _en_label, _target_yaw in directions if label not in successful_dirs
        ]
        dir_text = "\n".join(f"- {label} 방향: {per_direction[label]}" for label in successful_dirs)
        return_failed = bool(return_to_start_result and not return_to_start_result["success"])
        partial = len(successful_dirs) < len(directions) or cancelled
        summary = ""
        if successful_dirs:
            try:
                missing_text = (
                    f"\n관찰에 실패한 방향: {', '.join(failed_dirs)}. 해당 방향의 상태를 추측하지 마세요."
                    if failed_dirs
                    else ""
                )
                messages = [
                    {
                        "role": "user",
                        "content": (
                            "다음은 로봇이 제자리에서 4방향을 촬영해 분석한 결과야.\n"
                            f"{pose_str}\n\n{dir_text}{missing_text}\n\n"
                            "관찰에 성공한 방향의 정보만으로 장소와 주변 상황을 한국어로 3~4문장 요약해줘. "
                            "관찰되지 않은 방향에 대해서는 추측하지 마세요."
                        ),
                    }
                ]
                summary = str(self.node._llm.chat(messages) or "").strip()
            except Exception as exc:
                logger.warning("[describe_surroundings] Comprehensive analysis failed: %s", exc)
            if not summary:
                summary = "방향별 관찰은 수집했으나 종합 요약 생성에 실패했습니다."
            if return_failed:
                summary += " 시작 방향으로 복귀하지 못해 현재 로봇 방향을 확인해야 합니다."
        else:
            summary = (
                "다방향 관찰에서 사용할 수 있는 이미지 분석 결과가 없습니다. "
                "회전, 카메라 토픽 또는 VLM 연결을 확인하세요."
            )
            logger.error("[describe_surroundings] No successful directions — skipping summary")

        if return_to_start_result is not None:
            report["return_to_start"] = return_to_start_result
        report["per_direction"] = per_direction
        report["per_direction_meta"] = per_direction_meta
        report["successful_directions"] = successful_dirs
        report["failed_directions"] = failed_dirs
        report["cancelled"] = cancelled
        report["partial"] = partial
        report["success"] = bool(successful_dirs)
        report["scene_description"] = summary
        report["message"] = summary
        if montage_path:
            report["file_path"] = montage_path

        # RAG 저장
        if (
            report["success"]
            and self.node
            and hasattr(self.node, "_memory")
            and getattr(self.node, "_enable_rag", False)
        ):
            try:
                loc = f" [위치 x={pose['x']} y={pose['y']}]" if pose else ""
                self.node._memory.add_knowledge(  # type: ignore
                    f"{loc} 사방 관찰 위치 판단: {summary[:500]}",
                    {"type": "where_am_i", "source": "describe_surroundings"},
                )
            except Exception as exc:
                logger.debug("[describe_surroundings] RAG save failed: %s", exc)

        return report
