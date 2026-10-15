"""detection_common._best_detection 폴링 동작 테스트.

wait_for_message 는 타임아웃뿐 아니라 "구독을 만들 수 없음"처럼 즉시 None 을
돌려줄 수 있다. 이때 폴링 루프가 sleep 없이 continue 하면 deadline 동안 조밀
루프가 되어 CPU 를 소진하고 에러 로그를 폭주시킨다(실측: 3초 동안 140만 회).
"""

import time

import pytest
from robo_claw_agent.skills.manipulation_skill.sequence import detection_common

pytestmark = pytest.mark.unit


class _NeverMessageSkill:
    """wait_for_message 가 항상 즉시 None 을 돌려주는 페이크."""

    def __init__(self) -> None:
        self.calls = 0

    def wait_for_message(self, msg_type, topic, timeout_sec=5.0):
        self.calls += 1
        return None


def test_best_detection_does_not_busy_loop_when_subscription_unavailable():
    pytest.importorskip("vision_msgs.msg")
    skill = _NeverMessageSkill()

    start = time.monotonic()
    det, class_name, score, error = detection_common._best_detection(
        skill,
        "cup",
        detections_topic="/object_detector_node/detections",
        min_score=0.25,
        timeout_sec=0.3,
    )
    elapsed = time.monotonic() - start

    assert det is None
    assert class_name == ""
    assert score == 0.0
    assert "수신 실패" in error
    # 조밀 루프라면 수만 회를 호출한다. 0.3s / 0.05s ≈ 6회 수준이어야 한다.
    assert skill.calls <= 20, f"busy loop detected: {skill.calls} calls"
    assert elapsed < 2.0
