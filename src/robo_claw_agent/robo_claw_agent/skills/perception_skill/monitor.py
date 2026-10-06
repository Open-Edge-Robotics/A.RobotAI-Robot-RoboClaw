import logging
import threading
import time
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from . import globals
from .core import _resolve_class_name

if globals._VISION_MSGS_AVAILABLE:
    from vision_msgs.msg import Detection2DArray

logger = logging.getLogger(__name__)


class MonitorDetectionSkill(BaseSkill):
    """백그라운드에서 특정 객체를 감시하고 감지 시 사용자에게 알립니다."""

    name = "monitor_detection"
    terminal_behavior = "background"
    input_schema = {"type": "object", "properties": {
        "target_object": {"type": "string"}, "min_score": {"type": "number", "default": 0.5},
        "alert_interval_sec": {"type": "number", "default": 30.0}, "camera": {"type": "string"}
    }, "required": ["target_object"], "additionalProperties": False}
    description = (
        "백그라운드에서 ONNX 객체 인식을 폴링하여 target_object가 감지되면 "
        "즉시 사용자에게 알림을 전송합니다. "
        "파라미터: target_object(필수), min_score(0.5), alert_interval_sec(30). "
        "중단: stop_monitor 스킬을 사용하세요. "
        "use_vision:=true 로 시스템이 기동된 경우에만 동작합니다."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if not globals._VISION_MSGS_AVAILABLE:
            return {
                "success": False,
                "message": "vision_msgs 미설치. ros-humble-vision-msgs 설치 필요.",
            }

        target = self.get_string_param(params, "target_object", "").strip().lower()
        if not target:
            return {"success": False, "message": "target_object 파라미터가 필요합니다."}

        if globals._MONITOR_ACTIVE.is_set():
            return {
                "success": False,
                "message": "이미 모니터링 중입니다. stop_monitor로 먼저 중단하세요.",
            }

        min_score = float(params.get("min_score", 0.5))
        alert_interval = float(params.get("alert_interval_sec", 30.0))
        detections_topic = self.get_string_param(
            params, "detections_topic", "/object_detector_node/detections"
        )

        globals._MONITOR_ACTIVE.set()
        last_alert_time = 0.0

        def _monitor_loop() -> None:
            nonlocal last_alert_time
            logger.info(
                "[monitor_detection] Starting: target=%s, min_score=%.2f", target, min_score
            )

            while globals._MONITOR_ACTIVE.is_set():
                msg = self.wait_for_message(
                    Detection2DArray, detections_topic, timeout_sec=1.5
                )
                if not msg:
                    continue

                for det in msg.detections:
                    for r in det.results:
                        score = r.hypothesis.score
                        if score < min_score:
                            continue
                        class_name = _resolve_class_name(r.hypothesis.class_id)
                        if target not in class_name.lower():
                            continue

                        now = time.monotonic()
                        if now - last_alert_time >= alert_interval:
                            last_alert_time = now
                            alert = f"감지 알림: '{class_name}'이(가) 카메라에 포착되었습니다! (신뢰도: {score:.0%})"
                            logger.info("[monitor_detection] %s", alert)
                            self.send_user_message(alert)

            globals._MONITOR_ACTIVE.clear()
            logger.info("[monitor_detection] Stopped")

        thread = threading.Thread(target=_monitor_loop, daemon=True)
        thread.start()

        return {
            "success": True,
            "message": f"'{target}' 모니터링을 시작했습니다. 감지 시 알림을 전송합니다.",
            "target_object": target,
            "min_score": min_score,
            "alert_interval_sec": alert_interval,
        }


class StopMonitorSkill(BaseSkill):
    """백그라운 객체 모니터링을 중단합니다."""

    name = "stop_monitor"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    description = "monitor_detection으로 시작한 백그라운드 객체 감시를 중단합니다."

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if not globals._MONITOR_ACTIVE.is_set():
            return {"success": False, "message": "현재 진행 중인 모니터링이 없습니다."}
        globals._MONITOR_ACTIVE.clear()
        return {"success": True, "message": "객체 모니터링을 중단했습니다."}
