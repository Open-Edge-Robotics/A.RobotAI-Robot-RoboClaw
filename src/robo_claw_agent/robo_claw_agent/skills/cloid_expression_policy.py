"""CLOiD 상황별 표현 모션 선택 정책 (ROS-free).

CLOiD 등록 모션 카탈로그에서 사용자 상호작용 상황에 맞는 모션을 **상수 allowlist**로
고른다. 자동 재생은 태스크당 1회를 전제로 하며, 베이스 주행이 있었던 태스크에서는
선택하지 않는다(주행 중 상체 모션 금지).

allowlist 는 실측 카탈로그(137개)에서 `pre_id` 가 없고 위험도가 낮은 표현 모션만
검토해 고정한 값이다. 카탈로그 `intent_id` 는 사람이 읽는 분류일 뿐이므로 id 를
기준으로 선택한다.
"""

from __future__ import annotations

import random
from typing import Any

SITUATION_GREETING = "greeting"
SITUATION_TALKING = "talking"
SITUATION_SCANNING = "scanning"
SITUATION_FAIL = "fail"

# 상황 → 검토된 모션 ID(모두 pre_id 없는 모션).
SITUATION_MOTION_IDS: dict[str, tuple[int, ...]] = {
    # 손흔들기/인사: wave1~4, greet1, gentleman_greet, greet4/5
    SITUATION_GREETING: (14, 15, 16, 17, 18, 19, 21, 69, 70),
    # 대화: talk1~13
    SITUATION_TALKING: (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13),
    # 탐색: scan (WH 마스크, 좌우/좌/우)
    SITUATION_SCANNING: (22, 23, 24, 77, 78, 79, 80, 81),
    # 실패: fail
    SITUATION_FAIL: (27, 94),
}

SITUATIONS: tuple[str, ...] = tuple(SITUATION_MOTION_IDS)

# 인사 상황으로 볼 사용자 표현(소문자 비교).
GREETING_KEYWORDS: tuple[str, ...] = ("안녕", "반가", "하이", "헬로", "hello", "hi ", "인사")
# 탐색/관찰 상황으로 볼 표현.
SCAN_KEYWORDS: tuple[str, ...] = ("탐색", "찾아", "스캔", "둘러", "살펴", "scan", "explore")
# 베이스 주행을 수반하는 요청으로 볼 표현. 해당 태스크에서는 표현 모션을 재생하지 않는다.
BASE_MOTION_KEYWORDS: tuple[str, ...] = (
    "이동",
    "가줘",
    "가라",
    "가서",
    "주행",
    "전진",
    "후진",
    "navigate",
    "move",
)


def looks_like_base_motion(instruction: str) -> bool:
    """사용자 명령이 베이스 주행을 수반하는지 보수적으로 판정한다."""
    text = str(instruction or "").strip().lower()
    if not text:
        return False
    return any(keyword in text for keyword in BASE_MOTION_KEYWORDS)


def classify_situation(
    instruction: str,
    *,
    task_success: bool = True,
    used_base_motion: bool = False,
) -> str | None:
    """태스크 결과/명령에서 표현 상황을 정한다. 재생하지 않을 때는 None."""
    if used_base_motion or looks_like_base_motion(instruction):
        return None
    text = str(instruction or "").strip().lower()
    if any(keyword in text for keyword in GREETING_KEYWORDS):
        return SITUATION_GREETING
    if any(keyword in text for keyword in SCAN_KEYWORDS):
        return SITUATION_SCANNING
    if not task_success:
        return SITUATION_FAIL
    return SITUATION_TALKING


def select_expression_motion(
    motions: list[dict[str, Any]],
    situation: str,
    *,
    rng: Any | None = None,
    previous_motion_id: int | None = None,
) -> dict[str, Any] | None:
    """상황 allowlist 안에서 `pre_id` 없는 모션 하나를 고른다(반복 회피)."""
    allowed = set(SITUATION_MOTION_IDS.get(situation, ()))
    if not allowed:
        return None

    candidates: list[dict[str, Any]] = []
    for motion in motions:
        if not isinstance(motion, dict):
            continue
        motion_id = motion.get("motion_id")
        if motion_id not in allowed:
            continue
        if motion.get("pre_id") not in (None, "", 0, "0"):
            continue
        if not str(motion.get("motion_name", "")).strip():
            continue
        candidates.append(motion)

    if previous_motion_id is not None and len(candidates) > 1:
        alternatives = [
            motion for motion in candidates if motion.get("motion_id") != previous_motion_id
        ]
        if alternatives:
            candidates = alternatives

    if not candidates:
        return None
    return (rng or random.SystemRandom()).choice(candidates)
