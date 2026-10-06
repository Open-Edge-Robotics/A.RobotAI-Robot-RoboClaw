"""카메라 무관 detection 폴링 헬퍼.

원래 ``gripper_vision._best_gripper_detection`` 이었으나, 이미 ``detections_topic``
으로 파라미터화되어 있어 그리퍼 카메라 전용일 이유가 없었다. 헤드 카메라 관측
(``head_vision``)이 같은 로직을 그대로 쓰기 위해 이 모듈로 분리했다. 로직은
이동 전과 동일하다.
"""

from __future__ import annotations

import time
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills.perception_skill.core import target_class_variants


def _best_detection(
    skill: BaseSkill,
    target_object: str,
    *,
    detections_topic: str,
    min_score: float,
    timeout_sec: float,
) -> tuple[Any | None, str, float, str]:
    """detections_topic에서 target_object와 매칭되는 최고 점수 detection을 고른다.

    Returns:
        (detection, class_name, score, error_message). 실패 시 detection 이 None 이고
        error_message 에 사유(상위 후보 요약 포함)가 담긴다.
    """
    try:
        from vision_msgs.msg import Detection2DArray
    except ImportError:
        return None, "", 0.0, "vision_msgs 패키지가 설치되지 않았습니다."

    from robo_claw_agent.skills.perception_skill.core import _resolve_class_name

    target_variants = target_class_variants(target_object)
    best_det = None
    best_class = ""
    best_score = -1.0
    candidates: list[tuple[str, float]] = []
    deadline = time.monotonic() + max(0.1, timeout_sec)
    received_message = False
    while time.monotonic() < deadline and best_det is None:
        remaining = max(0.1, deadline - time.monotonic())
        msg = skill.wait_for_message(
            Detection2DArray, detections_topic, timeout_sec=min(1.0, remaining)
        )
        if msg is None:
            continue
        received_message = True
        for det in msg.detections:
            for result in det.results:
                class_name = _resolve_class_name(result.hypothesis.class_id)
                score = float(result.hypothesis.score)
                candidates.append((class_name, score))
                if any(target in class_name.lower() for target in target_variants) and score >= min_score and score > best_score:
                    best_det = det
                    best_class = class_name
                    best_score = score

    if not received_message:
        return None, "", 0.0, f"Detection2DArray 수신 실패 ({detections_topic})"

    if best_det is None:
        top = sorted(candidates, key=lambda item: item[1], reverse=True)[:5]
        if top:
            summary = ", ".join(f"{name}:{score:.2f}" for name, score in top)
            return (
                None,
                "",
                0.0,
                (f"'{target_object}' 미탐지 (min_score={min_score}). 상위 후보: {summary}"),
            )
        return None, "", 0.0, f"'{target_object}' 미탐지 (min_score={min_score}, 후보 없음)"
    return best_det, best_class, best_score, ""
