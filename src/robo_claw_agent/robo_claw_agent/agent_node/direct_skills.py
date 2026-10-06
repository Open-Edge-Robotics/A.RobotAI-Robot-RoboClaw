import logging
import re
import threading
from typing import Any

from ..skill_manager import SkillResult
from ..types import AgentState
from .nav_safety import ensure_stretch_navigation_safety

logger = logging.getLogger(__name__)


def direct_cup_pick_skill(node: Any, instruction: str) -> dict[str, Any] | None:
    text = str(instruction or "").strip().lower()
    if not text:
        return None
    has_cup = any(token in text for token in ("컵", "cup", "잔", "물컵"))
    has_pick = any(
        token in text
        for token in (
            "집어",
            "잡아",
            "들어",
            "집기",
            "pick",
            "grasp",
            "grab",
        )
    )
    if not (has_cup and has_pick):
        return None
    if re.search(r"(왜|분석|확인|보여|설명|어떻게|로그|상태|준비자세|준비 자세|prepare|ready\s*pose)", text):
        return None
    return {
        "skill": "adaptive_pick_object",
        "params": {"target_object": "cup"},
        "background": True,
    }


def direct_map_capture_skill(node: Any, instruction: str) -> dict[str, Any] | None:
    text = str(instruction or "").strip().lower()
    if not text:
        return None
    has_map = any(token in text for token in ("맵", "지도", "map"))
    has_action = any(token in text for token in ("보내", "캡처", "사진", "이미지", "show", "get", "capture"))
    has_complex = any(token in text for token in ("분석", "좌표", "이동", "찾아", "마킹", "표시", "가라", "갈 수", "analyze", "annotate", "find"))
    if has_map and has_action and not has_complex:
        return {
            "skill": "capture_map",
            "params": {},
            "background": False,
        }
    return None


def direct_camera_capture_skill(node: Any, instruction: str) -> dict[str, Any] | None:
    text = str(instruction or "").strip().lower()
    if not text:
        return None
    has_cam = any(token in text for token in ("카메라", "전방", "앞쪽", "camera", "view"))
    has_action = any(token in text for token in ("보내", "캡처", "캡쳐", "사진", "이미지", "show", "get", "capture"))
    compact_text = re.sub(r"\s+", "", text)
    # 정보 조회나 복수 카메라 요청을 기본 카메라 한 장 캡처로 축약하지 않는다.
    # 특히 '카메라 이미지 관련 토픽'은 촬영이 아니라 ROS 그래프 조회 의도다.
    has_topic_query = any(
        token in compact_text for token in ("토픽", "topic", "목록", "정리", "나열", "종류")
    )
    has_multi_camera_query = any(
        token in compact_text
        for token in ("각카메라", "모든카메라", "전체카메라", "여러카메라", "여러대", "전부")
    )
    if has_topic_query or has_multi_camera_query:
        return None
    # 맵 캡처(direct_map_capture_skill)와 동일하게 이동/좌표가 섞인 요청은 제외한다.
    # 이 목록에 이동 계열이 빠져 있어 "주방으로 이동해서 카메라 사진 보내줘"가
    # 이동 없이 촬영만 하고 끝나는 오라우팅이 발생했다.
    has_complex = any(
        token in text
        for token in (
            "분석", "찾아", "인식", "감지", "좌표",
            "이동", "가서", "가라", "갈 수", "간 뒤", "간 다음",
            "analyze", "detect", "describe", "find",
        )
    )
    if has_cam and has_action and not has_complex:
        return {
            "skill": "capture_camera_image",
            "params": {},
            "background": False,
        }
    return None


_DIRECTION_GROUP = r"앞으로|앞|전진|뒤로|뒤|후진|왼쪽|좌측|오른쪽|우측"
_DISTANCE_UNIT = r"m|미터|meter|meters"
_MOVE_VERB = r"이동|가줘|가라|가세요|가자|가|전진|후진|움직|주행|move|go"

# "2미터 앞에 있는 컵 집어줘" 처럼 거리·방향이 목적어를 수식하는 문장은
# 상대 이동이 아니라 조작 요청이므로 다이렉트 라우팅에서 제외한다.
_MANIPULATION_PATTERN = re.compile(
    r"집어|집기|집을|잡아|잡을|잡기|들어|들고|들어올|놓아|놓고|내려놔|가져|파지|"
    r"pick|grasp|grab|place|put"
)

# 절대 위치/장소 지정으로 보이는 표현. '방'은 부분문자열('가방', '방향', '난방')
# 오탐을 피하기 위해 장소 조사가 붙은 형태만 인식한다.
_PLACE_HINT_PATTERN = re.compile(r"좌표|장소|위치|룸|room|방(?:으로|에|까지|안|쪽)")


def direct_relative_move_skill(node: Any, instruction: str) -> dict[str, Any] | None:
    text = str(instruction or "").strip().lower()
    if not text:
        return None
    if _PLACE_HINT_PATTERN.search(text) or _MANIPULATION_PATTERN.search(text):
        return None

    # 1) "앞으로 2미터", "왼쪽 방향으로 1미터" — 방향이 거리를 선행하는 형태.
    match = re.search(
        rf"({_DIRECTION_GROUP})\s*(?:방향)?\s*(?:으로)?\s*(\d+(?:\.\d+)?)\s*(?:{_DISTANCE_UNIT})",
        text,
    )
    if match:
        direction = match.group(1)
        distance = float(match.group(2))
    else:
        # 2) 거리가 방향을 선행하는 형태.
        # 2a) "3m 전진" — 방향어 자체가 이동 동사라 추가 동사가 필요 없다.
        match = re.search(
            rf"(\d+(?:\.\d+)?)\s*(?:{_DISTANCE_UNIT})\s*(전진|후진)", text
        )
        if not match:
            # 2b) "2미터 앞으로 가줘" — 이동 동사를 필수로 요구해
            #     "2미터 앞에 있는 ~" 같은 수식 표현을 배제한다.
            match = re.search(
                rf"(\d+(?:\.\d+)?)\s*(?:{_DISTANCE_UNIT})\s*({_DIRECTION_GROUP})"
                rf"(?:방향)?(?:으로)?\s*(?:{_MOVE_VERB})",
                text,
            )
        if not match:
            return None
        distance = float(match.group(1))
        direction = match.group(2)

    params: dict[str, float] = {"forward": 0.0}
    if direction in {"앞", "앞으로", "전진"}:
        params["forward"] = distance
    elif direction in {"뒤", "뒤로", "후진"}:
        params["forward"] = -distance
    elif direction in {"왼쪽", "좌측"}:
        params["lateral"] = distance
    elif direction in {"오른쪽", "우측"}:
        params["lateral"] = -distance
    else:
        return None

    return {
        "skill": "move_relative",
        "params": params,
        "background": False,
    }


def check_direct_skill(node: Any, instruction: str) -> dict[str, Any] | None:
    """LLM 추론 지연이나 환각 없이 100% 직관적으로 처리해야 하는 명확한 지시사항 라우팅"""
    # 조작(집기) 판정을 이동보다 먼저 수행한다. "2미터 앞의 컵 집어" 처럼 거리
    # 표현이 섞인 조작 요청이 상대 이동으로 오라우팅되지 않도록 하는 이중 방어.
    res = direct_cup_pick_skill(node, instruction)
    if res is not None:
        return res
    res = direct_relative_move_skill(node, instruction)
    if res is not None:
        return res
    res = direct_map_capture_skill(node, instruction)
    if res is not None:
        return res
    return direct_camera_capture_skill(node, instruction)


def start_background_direct_skill(
    node: Any, instruction: str, skill_name: str, params: dict[str, Any]
) -> SkillResult:
    message = f"[{skill_name}] 컵 집기 작업을 백그라운드에서 시작했습니다. 완료되면 다시 알려드릴게요."

    def _work() -> None:
        node.get_logger().info(
            f"Starting background direct skill: {skill_name} (params={params})"
        )
        node._set_state(AgentState.EXECUTING, skill_name, f"{skill_name} 실행 중")
        try:
            # LLM 계획 경로와 동일한 Stretch3 안전 게이트를 직접 라우팅에도 적용한다.
            chain = ensure_stretch_navigation_safety(
                [{"skill": skill_name, "params": params}],
                node._skills,
                node.get_logger(),
            )
            res = None
            for item in chain:
                res = node._skills.execute(
                    item["skill"], item.get("params", {}), timeout_sec=0.0
                )
                if not res.success:
                    break
            if res is None:
                raise RuntimeError("실행할 스킬이 없습니다.")
            node._memory.add_event(
                "background_task_completed",
                {
                    "instruction": instruction,
                    "skill": skill_name,
                    "success": res.success,
                    "message": res.message,
                },
            )
            result_text = (
                f"[{skill_name}] 컵 집기 작업 완료: {res.message}"
                if res.success
                else f"[{skill_name}] 컵 집기 작업 실패: {res.message}"
            )
            node.get_logger().info(result_text)
            node._start_channel_send(result_text)
        except Exception as exc:
            error_text = f"[{skill_name}] 컵 집기 작업 중 내부 오류: {exc}"
            node.get_logger().error(error_text)
            node._memory.add_event(
                "background_task_failed",
                {"instruction": instruction, "skill": skill_name, "error": str(exc)},
            )
            node._start_channel_send(error_text)
        finally:
            node._set_state(AgentState.IDLE)

    threading.Thread(target=_work, daemon=True, name=f"direct-{skill_name}").start()
    node._memory.add_event(
        "background_task_started",
        {"instruction": instruction, "skill": skill_name, "params": params},
    )
    return SkillResult(
        skill_name,
        True,
        message,
        0.0,
        {"background": True, "skill": skill_name, "params": params},
    )
