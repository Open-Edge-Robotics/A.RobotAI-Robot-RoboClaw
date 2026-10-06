import json
import logging
import re
import threading
from typing import Any

from ..answer import looks_like_json, render_result_data
from ..tracing import traceable
from ..types import AgentState
from .nav_safety import ensure_stretch_navigation_safety
from .utils import _omit_base64_data

logger = logging.getLogger(__name__)

# 정보성 스킬 성공 후 결과를 사용자에게 정리해 답하는 LLM 라운드 수 상한.
# 상한을 두어 모델이 조회 스킬을 반복 실행하려는 경우에도 지연/토큰이 무한히
# 늘지 않게 하고, 초과 시에는 실행 결과로 만든 결정론적 폴백으로 종료한다.
_MAX_ANSWER_ROUNDS = 1


def _compose_result_answer(skill_results: list[Any]) -> str:
    """실행된 스킬 결과로 결정론적 답변 텍스트를 만든다.

    요약 라운드가 답변을 내지 못했거나 스킬을 다시 계획한 경우의 폴백이다.
    운영 필드는 제외하고 실제 결과 데이터(result_data)를 사람이 읽을 수 있게
    풀어서 사용자에게 최소한의 정보를 보장한다.
    """
    parts: list[str] = []
    for res in skill_results:
        message = str(getattr(res, "message", "") or "").strip()
        rendered = render_result_data(getattr(res, "result_data", None))
        if rendered:
            parts.append(f"{message} ({rendered})" if message else rendered)
        elif message:
            parts.append(message)
    return "\n".join(parts)


# 자동 RAG 검색에서 제외할 운영/메타 항목 타입.
# 명시적 rag_search(skills/system_skill/rag.py)와 동일하게 적용해,
# 스킬 기록·이동 좌표 로그가 저장된 장소 사실을 밀어내지 않도록 한다.
_RAG_EXCLUDED_TYPES = [
    "skill_episode",
    "skill_lesson",
    "navigated_coordinate",
    "blocked_coordinate",
]

# 읽기 전용 종료형 스킬: 물리적 동작 없이 정보만 반환.
# 성공 시 추가 LLM 추론 라운드를 생략해 토큰 비용을 절감한다.
_READONLY_TERMINAL_SKILLS: frozenset[str] = frozenset(
    {
        "say",
        "get_status",
        "get_distance",
        "capture_camera_image",
        "capture_map",
        "get_map_visual",
        "annotate_map",
        "analyze_map",
        "find_reachable_places",
        "describe_surroundings",
        "detect_object",
        "find_object",
        "scan_room",
        "get_detections",
        "analyze_scene",
        "annotate_image",
        "read_text_file",
        "list_files",
        "get_datetime",
        "rag_search",
        "rag_list",
        "rag_status",
        "log_observation",
        "get_location",
    }
)

# 물리적 동작을 수반하는 종료형 스킬: 실행 전 추가 검증 필요.
_ACTION_TERMINAL_SKILLS: frozenset[str] = frozenset(
    {
        "navigate_to",
        "rotate",
        "stop",
        "emergency_stop",
        "arm_pose",
        "move_joints",
        "move_pose",
        "open_gripper",
        "close_gripper",
        "grasp",
        "place",
        # 자체완결형 복합 조작 스킬 (allow_with_others=False) — 터미널 액션으로 일관 처리
        "adaptive_pick_object",
        "vla_pick_gripper_object",
        "vla_pick_front_object",
        "pick_front_object",
        "pick_from_right_side_zone",
        "prepare_right_side_pick",
        "servo_gripper_to_object",
        "stop_autonomous",
        "stop_patrol",
        "stop_monitor",
        "rag_add",
        "rag_delete",
        "rag_reindex",
        "reflect_skills",
        "run_butler_script",
    }
)

_TERMINAL_SKILLS: frozenset[str] = _READONLY_TERMINAL_SKILLS | _ACTION_TERMINAL_SKILLS

# 스킬이 필요없는 대화형 쿼리 패턴 (LLM이 실수로 스킬을 선택한 경우 재시도용).
#
# 모든 패턴은 문장 **전체**가 인사/자기소개/능력 문의일 때만 매치하도록 앵커링한다.
# 부분 매치(re.search)를 허용하면 "저기 앞에 뭐야 확인하고 알려줘" 같은 실행 명령의
# 일부 어절만 보고 대화로 오분류해, 스킬 실행을 5라운드 내내 거부하다 태스크가
# 실패한다. 호격("로봇아", "야")은 접두 그룹으로 흡수한다.
_VOCATIVE = r"(?:로봇아|로봇|야|얘|너)?\s*"
_SENTENCE_TAIL = r"\s*[.!?~]*\s*$"

_CONVERSATIONAL_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"^" + _VOCATIVE + r"(?:안녕[가-힣]*|고마워|고맙[가-힣]*|감사[가-힣]*|반가워|잘가|잘\s*가|"
        r"수고[가-힣]*|들어가|꺼져|잘했어|잘\s*했어|좋아|대단[가-힣]*|응|아니|그래|맞아|"
        r"ㅋ+|ㅎ+|넵|네|ok|okay|yes|no|hello|hi|bye|thanks|thank\s*you)" + _SENTENCE_TAIL,
        re.IGNORECASE,
    ),
    re.compile(
        r"^"
        + _VOCATIVE
        + r"(?:너\s*)?(?:누구[가-힣]*|뭐야|뭐니|뭐하는\s*로봇[가-힣]*|이름이\s*뭐[가-힣]*|"
        r"뭐\s*할\s*수\s*있[가-힣]*|할\s*수\s*있는\s*(?:게|것이)\s*뭐[가-힣]*|"
        r"뭐\s*해[가-힣]*|뭐\s*하고\s*있[가-힣]*|할\s*일\s*있[가-힣]*)" + _SENTENCE_TAIL
    ),
]

# 목적지명 앞에 흔히 붙는 호격/부사 어절. 추출 결과에서 제거한다.
_TARGET_FILLER_WORDS = frozenset(
    {
        "로봇아",
        "로봇",
        "야",
        "얘",
        "너",
        "니가",
        "네가",
        "이제",
        "지금",
        "빨리",
        "당장",
        "우선",
        "먼저",
        "그럼",
        "그러면",
        "일단",
        "좀",
        "잠깐",
        "다시",
    }
)


def _extract_requested_navigation_target(instruction: str) -> str:
    """사용자 이동 명령에서 명시 목적지명을 추출한다.

    re.search 는 최좌측 매칭이라 non-greedy 패턴이어도 문장 앞의 호격/부사가
    목적지에 딸려 들어간다("로봇아 주방으로 가줘" → "로봇아 주방"). 따라서
    매칭 후 어절 단위로 필러를 제거한다.
    """
    text = str(instruction or "").strip()
    if not text:
        return ""
    patterns = (
        r"(?P<target>[가-힣A-Za-z0-9_\- ]{1,40}?)(?:으로|로)\s*(?:이동|가|가줘|가라|가세요)",
        r"(?P<target>[가-힣A-Za-z0-9_\- ]{1,40}?)(?:까지)\s*(?:이동|가|가줘|가라|가세요)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        target = match.group("target").strip(" ,.!?\t\n")
        if not target:
            continue
        # 앞쪽 어절부터 필러를 제거한다. "로봇아 주방" → "주방"
        words = target.split()
        while len(words) > 1 and words[0] in _TARGET_FILLER_WORDS:
            words.pop(0)
        # 필러를 걷어내고도 어절이 많이 남으면 과다 추출로 보고 포기한다.
        # (LLM 값을 덮어써서 없는 장소명을 만드는 것보다 그대로 두는 편이 안전)
        if len(words) > 2:
            return ""
        cleaned = " ".join(words).strip()
        if cleaned:
            return cleaned
    return ""


def _preserve_user_navigation_target(
    chain: list[dict[str, Any]], instruction: str, logger_obj: Any
) -> list[dict[str, Any]]:
    requested_target = _extract_requested_navigation_target(instruction)
    if not requested_target:
        return chain

    normalized_instruction = re.sub(r"\s+", "", str(instruction or ""))
    updated_chain: list[dict[str, Any]] = []
    for item in chain:
        if item.get("skill") != "navigate_to":
            updated_chain.append(item)
            continue
        params = item.get("params", {})
        if not isinstance(params, dict) or "target_name" not in params:
            updated_chain.append(item)
            continue
        planned_target = str(params.get("target_name") or "").strip()
        if not planned_target:
            updated_chain.append(item)
            continue
        normalized_planned = re.sub(r"\s+", "", planned_target)
        if normalized_planned and normalized_planned not in normalized_instruction:
            new_params = dict(params)
            new_params["target_name"] = requested_target
            logger_obj.info(
                "Preserving user navigation target: "
                f"replacing LLM target_name '{planned_target}' with '{requested_target}'"
            )
            updated_chain.append({**item, "params": new_params})
        else:
            updated_chain.append(item)
    return updated_chain


def _is_likely_conversational(instruction: str) -> bool:
    """사용자 명령이 스킬이 필요없는 단순 대화형인지 판단한다.

    판정은 오직 위 앵커링된 인사/자기소개 패턴으로만 한다.
    과거에는 "짧고(<=10자) 작업 키워드가 없으면 대화형"이라는 길이 휴리스틱이
    있었지만, "컵 가져와", "쓰레기 치워", "문 열기"처럼 정상적인 짧은 실행 명령과
    분해기가 생성하는 하위 지시문("지도 캡처", "테이블 청소")까지 대화로 오분류해
    스킬 실행 자체를 막았다. 키워드 목록을 늘리는 방식은 열거가 끝나지 않으므로
    길이 기반 추정은 제거한다 — 진짜 잡담은 실행 경로 진입 전
    ``fast_router._SIMPLE_QUERY_PATTERNS`` 와 시스템 프롬프트가 함께 걸러낸다.
    """
    text = str(instruction or "").strip()
    if not text:
        return True
    for pattern in _CONVERSATIONAL_PATTERNS:
        if pattern.search(text):
            return True
    return False


# 위치질의 결정론적 라우팅용 패턴/상수.
# 약한 LLM(예: gemma4:e4b)이 "<장소> 좌표 알려줘"(정방향)를 identify_location(역조회)·
# get_status로 오라우팅하거나, "여기 어디야"(역방향)를 스킬 실행 없이 서술만 하는 문제를
# 규칙으로 교정한다. 단일 스텝 질의에만 개입(복합 태스크는 LLM 계획을 그대로 둔다).
_LOC_INTENT_RE = re.compile(r"위치|좌표|자리|어디|어딨|where|location|coordinate", re.IGNORECASE)
_REVERSE_LOC_RE = re.compile(
    r"(여기\s*가?\s*어디|"
    r"현재\s*(?:내|로봇)?\s*(?:위치|장소|있는\s*곳|있는|서\s*있는).{0,15}?(?:이름|어디|뭐|무엇)|"
    r"지금\s*(?:있는|서\s*있는).{0,10}?(?:곳|위치|장소).{0,8}?(?:이름|어디|뭐)|"
    r"이\s*좌표.{0,12}?(?:어디|이름)|"
    r"좌표.{0,25}?(?:비교|기반).{0,25}?(?:이름|위치|장소|어디))"
)
# 위치질의로 오라우팅되기 쉬운 read 스킬(정방향에서 get_location 으로 교정 대상).
# get_location 자신도 포함해야 한다 — LLM이 get_location 을 골랐더라도 location_name 을
# 엉뚱하게 넣을 수 있고, 그 계획을 "순수 조회"로 인식해야 파라미터를 교정할 수 있다.
_LOC_READ_SKILLS: frozenset[str] = frozenset(
    {
        "identify_location",
        "get_status",
        "analyze_scene",
        "describe_surroundings",
        "get_location",
        "rag_search",
        "rag_list",
    }
)

# 저장 명령: "…<장소>로/으로 기억/저장/등록" → rag_add 로 강제(약한 LLM이 저장을 검색으로
# 오라우팅해 "검색 결과가 없습니다"로 실패하는 문제 방지). "그 위치"는 rag_add의 pose 보완이
# 현재 좌표로 채우므로 대화 맥락 없이도 동작한다(클라이언트 무관).
_SAVE_INTENT_RE = re.compile(r"기억|저장|등록")
# 이름 그룹은 **비탐욕(lazy)** 이어야 한다. 탐욕이면 "키친으로" 에서 조사 "으로" 의 '으'까지
# 이름으로 먹어 `키친으` 로 저장된다(실측: 2026-08-13/14 리포트의 '키친으'). 받침 있는 이름은
# 조사가 "으로" 이므로(키친→키친으로, 주방→주방으로) 이 계열 이름이 전부 오염된다.
# lazy 로 두면 짧은 이름 + "으로" 가 먼저 매칭돼 "키친" 을 얻는다.
# ('로' 조사를 쓰는 ㄹ받침·모음 종결 이름(거실→거실로)도 동일하게 정상 동작)
_SAVE_PLACE_RE = re.compile(r"([가-힣A-Za-z0-9]{1,20}?)\s*(?:으로|로)\s*(?:기억|저장|등록)")
_SAVE_PLACE_STOPWORDS = frozenset(
    {"현재", "여기", "지금", "이", "그", "저", "위치", "장소", "곳", "여길", "거길"}
)


def _extract_save_place(instruction: str) -> str:
    """ "…<장소>로 기억/저장/등록" 에서 저장할 장소명을 뽑는다(지시/부사어는 제외)."""
    for m in _SAVE_PLACE_RE.finditer(str(instruction or "")):
        cand = m.group(1).strip()
        if cand and cand not in _SAVE_PLACE_STOPWORDS:
            return cand
    return ""


# 이동을 실제로 수행하는 스킬. 계획에 이미 하나라도 있으면 이동 교정을 하지 않는다
# (복합 태스크 "거실로 이동해서 사진 찍어" 의 계획을 망가뜨리지 않기 위함).
_MOVEMENT_SKILLS: frozenset[str] = frozenset(
    {
        "navigate_to",
        "follow_waypoints",
        "move_relative",
        "patrol",
        "explore",
        "autonomous_act",
        "approach_object",
        "rotate",
        "face_direction",
    }
)


def _force_navigation_if_misrouted(
    chain: list[dict[str, Any]], instruction: str, logger_obj: Any
) -> list[dict[str, Any]]:
    """ "<장소>로 이동해" 를 조회 스킬로 오라우팅한 계획을 navigate_to 로 교정한다.

    실측(2026-08-14, gemma4:e4b): "키친으로 이동해" 를 `get_status` 단독으로 계획해
    로봇이 움직이지 않았고, 이후 역방향 조회(tc16/17)가 옛 위치를 보고해 연쇄 실패했다.
    tc13/14(충전대로/거실로)는 통과했으므로 **모델의 비결정적 라우팅**이며, 이동 명령은
    실패 비용이 크므로(이동이 아예 일어나지 않음) 규칙으로 고정한다.

    `_LOC_INTENT_RE` 기반 위치질의 라우팅은 "위치/좌표" 같은 단어를 요구해 여기에 걸리지
    않는다("키친으로 이동해" 에는 그런 단어가 없다). 그래서 별도 교정이 필요하다.
    """
    target = _extract_requested_navigation_target(instruction)
    if not target:
        return chain
    # 이미 이동 스킬이 계획돼 있으면 그대로 둔다.
    if any(str(item.get("skill") or "") in _MOVEMENT_SKILLS for item in chain):
        return chain
    # 조회만 하는 계획(또는 빈 계획)일 때만 개입한다. 그 외 물리 스킬이 섞였으면 손대지 않는다.
    if chain and not all(str(item.get("skill") or "") in _LOC_READ_SKILLS for item in chain):
        return chain
    logger_obj.info(f"Routing misrouted navigation command to navigate_to(target_name='{target}')")
    return [{"skill": "navigate_to", "params": {"target_name": target}}]


def _known_place_in_text(text: str, node: Any) -> str:
    """지시문에 **기억된** 장소명이 들어있으면 그 표준 이름을 돌려준다.

    판정 소스를 시맨틱 맵으로만 한정하면, 시맨틱 맵이 비었거나 desync된 순간
    (예: 메모리 디렉터리만 초기화하고 Qdrant는 남긴 경우) 이 라우팅이 **조용히 발동하지 않고**
    쿼리 문구·임베딩 점수 운에 결과가 맡겨진다. 그래서 `rag_search` 쪽 판정과 동일하게
    시맨틱 맵 + 벡터스토어를 함께 보는 공용 헬퍼를 쓴다.
    """
    from robo_claw_agent.memory_manager.semantic import known_place_in_text

    return known_place_in_text(text, getattr(node, "_memory", None))


# 정방향/역방향 위치질의 교정을 적용해도 안전한 "순수 조회" 체인인지 판정한다.
# navigate_to 처럼 물리 동작이 섞인 계획("거실 위치로 이동해")을 조회로 바꿔치기하면
# 사용자의 실제 요청을 삼키게 되므로, 모든 스텝이 read 조회 스킬일 때만 개입한다.
def _is_pure_lookup_chain(chain: list[dict[str, Any]]) -> bool:
    return bool(chain) and all(str(item.get("skill") or "") in _LOC_READ_SKILLS for item in chain)


def _location_chain_for(
    instruction: str, node: Any, logger_obj: Any
) -> list[dict[str, Any]] | None:
    """위치 관련 지시문을 처리할 결정론적 스킬 체인을 만든다(해당 없으면 None).

    LLM이 스킬을 아예 고르지 못한 경우(소형 모델이 산문으로 답하는 경우)에도 재사용할 수
    있도록 `_route_location_query` 에서 분리했다.
    """
    text = str(instruction or "")

    # 저장 명령: "…<장소>로 기억/저장/등록" → rag_add 강제(검색 오라우팅 방지)
    if _SAVE_INTENT_RE.search(text):
        save_place = _extract_save_place(text)
        if not save_place:
            return None
        return [
            {
                "skill": "rag_add",
                "params": {
                    "text": f"이곳은 {save_place}입니다.",
                    "metadata": {"location_name": save_place},
                },
            }
        ]

    # 정방향: "<기억된 장소> 위치/좌표 …" → get_location(location_name=장소명)
    #
    # 2026-08-14 병합 전에는 rag_search(query=장소) 로 강제했다. origin/main 이 이름→좌표
    # 전용 스킬 `get_location` 을 신설했으므로 그쪽을 타겟으로 쓴다:
    #   - `location_name` 이 명시 파라미터라 의도가 분명하다(rag_search 의 query 는 자유 문장).
    #   - 조회 본체는 `_best_location_for_targets` 로 공유하므로 동작·문구가 동일하다.
    # rag_search 는 원래 목적(관찰·교훈·문서의 퍼지 검색)으로 남는다.
    if _LOC_INTENT_RE.search(text) and not _REVERSE_LOC_RE.search(text):
        place = _known_place_in_text(text, node)
        if place:
            return [{"skill": "get_location", "params": {"location_name": place}}]
        return None

    # 역방향: "여기 어디 / 현재 위치 이름 / 좌표로 이름 파악" → identify_location
    if _REVERSE_LOC_RE.search(text):
        return [{"skill": "identify_location", "params": {}}]
    return None


def _route_location_query(
    chain: list[dict[str, Any]], instruction: str, node: Any, logger_obj: Any
) -> list[dict[str, Any]]:
    """단일 스텝 위치질의를 올바른 스킬로 결정론적으로 교정한다(모델 성능과 무관).

    - 정방향("<등록 장소> 위치/좌표 …"): rag_search(query=장소)로 강제 → 시맨틱 맵 좌표 확정 답변.
    - 역방향("여기 어디/현재 위치 이름/좌표로 이름 파악"): identify_location 실행 강제.

    과거에는 `len(chain) != 1` 일 때 아무것도 하지 않았다. 약한 LLM이 `[get_status, rag_search]`
    처럼 두 스텝을 뱉는 순간 교정이 전부 무력화돼 임베딩 점수 운에 맡겨지므로, 이제는
    **모든 스텝이 조회 스킬인 계획**(`_is_pure_lookup_chain`)까지 교정 대상으로 넓힌다.
    물리 동작이 섞인 계획은 그대로 둔다(사용자 요청을 삼키지 않기 위해).
    """
    forced = _location_chain_for(instruction, node, logger_obj)
    if forced is None:
        return chain

    forced_skill = str(forced[0].get("skill") or "")

    # 저장 라우팅 — **LLM의 개체명 추출을 우선한다.**
    #
    # 과거에는 규칙이 뽑은 이름과 다르면 규칙 값으로 덮어썼다. 그 결과 LLM이 올바르게
    # 추출한 "키친"을 조사 파싱을 틀린 규칙이 "키친으"로 **망가뜨렸다**(실측: 2026-08-13/14
    # 리포트. 규칙 배포 전인 08-12까지는 LLM 값이 그대로 쓰여 이름이 전부 정확했다).
    #
    # 발화에서 개체명을 뽑는 일은 LLM이 하는 게 맞고, 규칙이 할 일이 아니다. 한국어 조사
    # (으로/로)·어순·다국어를 규칙으로 따라잡으려 하면 사이트마다 규칙을 늘려야 한다.
    # 따라서 규칙은 **LLM이 이름을 못 준 경우의 폴백**으로만 쓴다.
    if forced_skill == "rag_add":
        for item in chain:
            if str(item.get("skill") or "") != "rag_add":
                continue
            params = item.get("params")
            if not isinstance(params, dict):
                params = {}
            meta = params.get("metadata")
            if not isinstance(meta, dict):
                meta = {}
            if str(meta.get("location_name") or "").strip():
                # LLM이 rag_add + 장소명을 이미 냈다 → 그대로 신뢰한다.
                return chain
        target = str(forced[0]["params"]["metadata"]["location_name"])
        logger_obj.info(
            f"Save command had no LLM-extracted place name — falling back to "
            f"rule-extracted rag_add(location_name='{target}')"
        )
        return forced

    # 조회 라우팅: 순수 조회 계획일 때만 교정한다(빈 계획은 호출부에서 따로 처리).
    if not _is_pure_lookup_chain(chain):
        return chain

    if forced_skill == "get_location":
        # location_name 을 반드시 기억된 장소명으로 고정한다. 약한 LLM이 get_location 을
        # 고르더라도 이름을 엉뚱하게(예: "위치", 영어, 조사 포함) 넣으면 조회가 실패하므로
        # 강제 치환한다. 이미 같은 이름을 넣었으면 LLM 계획을 그대로 존중한다.
        place = str(forced[0]["params"]["location_name"])
        if len(chain) == 1 and str(chain[0].get("skill") or "") == "get_location":
            cur = str((chain[0].get("params") or {}).get("location_name") or "").strip()
            if cur == place:
                return chain
        logger_obj.info(f"Routing forward location query to get_location(location_name='{place}')")
        return forced

    if forced_skill == "identify_location":
        if len(chain) == 1 and str(chain[0].get("skill") or "") == "identify_location":
            return chain
        logger_obj.info("Routing reverse location query to identify_location")
        return forced

    return chain


class LLMPlanner:
    """LLM을 통한 계획 수립, 컨텍스트 수집 및 스킬 체인 실행 조율 클래스"""

    def __init__(self, node: Any) -> None:
        self.node = node

    def _skill_metadata(self, skill_name: str) -> Any | None:
        """등록된 스킬의 실행 메타데이터를 조회한다.

        과거에는 planner가 별도의 고정 집합을 유지해 새 스킬을 추가할 때
        terminal/action 분류를 놓칠 수 있었다. 고정 집합은 하위 호환용
        fallback으로 남기되, 등록된 스킬의 메타데이터를 우선 사용한다.
        """
        try:
            return self.node._skills.get_skill(skill_name)
        except Exception:
            return None

    def _is_terminal_skill(self, skill_name: str) -> bool:
        skill = self._skill_metadata(skill_name)
        if skill is not None:
            return getattr(skill, "terminal_behavior", "terminal") in {
                "terminal",
                "background",
                "loop",
            }
        return skill_name in _TERMINAL_SKILLS

    def _is_readonly_skill(self, skill_name: str) -> bool:
        skill = self._skill_metadata(skill_name)
        if skill is not None:
            return getattr(skill, "risk_level", "action") == "read"
        return skill_name in _READONLY_TERMINAL_SKILLS

    def _is_action_skill(self, skill_name: str) -> bool:
        skill = self._skill_metadata(skill_name)
        if skill is not None:
            return getattr(skill, "risk_level", "action") != "read"
        return skill_name in _ACTION_TERMINAL_SKILLS

    def _is_informational_skill(self, skill_name: str) -> bool:
        """결과 데이터를 사용자에게 정리해 답해야 하는 정보성 스킬인지 판정한다.

        안전 기본값은 ``"action"`` 이다. 등록되지 않은 스킬(MCP 등)은 물리적
        부작용을 알 수 없으므로 기존 동작(스킬 message 로 즉시 종료)을 유지한다.
        """
        skill = self._skill_metadata(skill_name)
        if skill is not None:
            return getattr(skill, "answer_mode", "action") == "informational"
        return False

    def _chain_needs_answer(self, chain: list[dict[str, Any]]) -> bool:
        """체인 전체가 정보성 스킬일 때만 결과 요약 라운드를 요청한다.

        물리적 부작용이 있는 스킬이 하나라도 섞이면 추가 LLM 라운드가 두 번째
        물리 동작을 계획할 위험이 있으므로 요약하지 않고 즉시 종료한다.
        """
        if not chain:
            return False
        return all(self._is_informational_skill(str(item.get("skill") or "")) for item in chain)

    @traceable(name="collect_task_context")
    async def collect_task_context(
        self, instruction: str, goal_handle: Any
    ) -> tuple[list[dict[str, str]], dict]:
        """RAG 검색, 로봇 상태 수집, 스킬 교훈 인출을 수행해 LLM messages를 구성한다."""
        knowledge_ctx = ""
        if self.node._enable_rag:
            search_query = instruction
            if self.node._llm:
                try:
                    reformulate_prompt = (
                        "다음 사용자 명령에서 RAG 지식 검색을 위한 핵심 키워드 및 환경 요소를 추출하여 검색 쿼리 하나만 반환하세요.\n"
                        "불필요한 지시어나 서술어는 제거하고 핵심 명사나 명사구 위주로 작성하세요. 다른 텍스트는 절대 출력하지 마세요.\n\n"
                        f"명령: {instruction}\n"
                        "쿼리:"
                    )
                    reformulated = await self.node._run_blocking(
                        "query_reformulate",
                        self.node._llm.chat,
                        [{"role": "user", "content": reformulate_prompt}],
                        timeout_sec=getattr(self.node, "_llm_timeout_sec", 60.0),
                    )
                    reformulated = reformulated.strip().strip('"').strip("'")
                    if reformulated:
                        self.node.get_logger().info(
                            f"RAG query refined: '{instruction}' -> '{reformulated}'"
                        )
                        search_query = reformulated
                except Exception as e:
                    self.node.get_logger().warning(
                        f"RAG query refinement failed (using original instruction): {e}"
                    )

            try:
                related_knowledge = await self.node._run_blocking(
                    "rag_search",
                    self.node._memory.search_knowledge,
                    search_query,
                    self.node._rag_top_k,
                    None,
                    {"type_exclude": _RAG_EXCLUDED_TYPES},
                )
                if related_knowledge:
                    # 위치 지식은 metadata의 좌표를 함께 실어 LLM이 정확한 좌표를
                    # 참조할 수 있게 한다. (text만 전달하면 좌표가 누락됨)
                    lines = []
                    for k in related_knowledge:
                        meta = k.get("metadata") or {}
                        text = str(k.get("text", "")).strip()
                        if (
                            isinstance(meta, dict)
                            and meta.get("type") == "location"
                            and "x" in meta
                            and "y" in meta
                        ):
                            name = meta.get("location_name") or meta.get("name") or ""
                            try:
                                lines.append(
                                    f"- '{name}'의 위치는 x: {float(meta['x']):.2f}, "
                                    f"y: {float(meta['y']):.2f} 입니다."
                                )
                                continue
                            except (TypeError, ValueError):
                                pass
                        lines.append(f"- {text}")
                    knowledge_ctx = "[참고 지식 (과거 경험)]\n" + "\n".join(lines)
            except Exception as e:
                self.node.get_logger().warning(
                    f"RAG knowledge search failed (RAG backend/Qdrant issue): {e}"
                )

        # 로봇 상태 수집
        robot_summary: dict = {}
        try:
            status_skill = self.node._skills._skills.get("get_status")
            if hasattr(status_skill, "get_robot_summary"):
                robot_summary = await self.node._run_blocking(
                    "get_robot_summary", status_skill.get_robot_summary
                )
        except Exception as e:
            self.node.get_logger().warning(
                f"Failed to retrieve robot summary from get_status skill: {e}"
            )

        # LLM messages 구성
        messages: list[dict[str, str]] = []
        if self.node._llm:
            history = self.node._memory.get_conversation_history(self.node._multiturn_n)
            messages = history + [{"role": "user", "content": instruction}]

            health_state = self.node._get_robot_health_state(robot_summary)
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": f"[로봇 현재 상태]\n{json.dumps(robot_summary, ensure_ascii=False)}\n\n[로봇 자가진단 상태 (Health State)]\n{json.dumps(health_state, ensure_ascii=False)}",
                },
            )
            if knowledge_ctx:
                messages.insert(0, {"role": "system", "content": knowledge_ctx})

            # 상황 인지형 스킬 교훈 인출
            if getattr(self.node, "_enable_skill_learning", False) and getattr(
                self.node, "_enable_rag", False
            ):
                try:
                    current_pose = None
                    pose = robot_summary.get("pose") if isinstance(robot_summary, dict) else None
                    if isinstance(pose, dict) and "x" in pose and "y" in pose:
                        current_pose = (float(pose["x"]), float(pose["y"]))
                    lessons_ctx = await self.node._run_blocking(
                        "render_relevant_skill_lessons",
                        self.node._memory.render_relevant_skill_lessons,
                        instruction,
                        current_pose=current_pose,
                    )
                    if lessons_ctx:
                        messages.insert(0, {"role": "system", "content": lessons_ctx})
                except Exception as e:
                    self.node.get_logger().warning(f"Failed to retrieve relevant lessons: {e}")

            if goal_handle.request.context_json:
                messages.insert(
                    0,
                    {
                        "role": "system",
                        "content": f"추가 컨텍스트: {goal_handle.request.context_json}",
                    },
                )

        return messages, robot_summary

    @traceable(name="run_llm_planning_loop")
    async def run_llm_planning_loop(
        self,
        goal_handle: Any,
        instruction: str,
        messages: list[dict[str, str]],
        robot_summary: dict,
        timeout: float,
        send_fb: Any,
    ) -> tuple[str, list[Any], bool, list[threading.Event]]:
        """LLM 계획 수립 → 스킬 체인 실행을 최대 max_rounds회 반복한다."""
        max_rounds = 5
        current_round = 0
        # 응답 형식 교정 재시도는 계획 라운드와 별도 예산으로 관리한다.
        # (형식 오류가 반복돼도 실제 작업 수행 라운드가 소진되지 않도록)
        format_retries = 0
        max_format_retries = 2
        # 대화형 오분류 교정도 1회로 제한한다. 무제한 재요구는 판정이 틀렸을 때
        # 계획 라운드를 전부 태우고 "재플래닝 횟수 초과"로 태스크를 실패시킨다.
        conversational_retries = 0
        max_conversational_retries = 1
        # 정보성 스킬 결과를 사용자 답변으로 합성하는 라운드 상태.
        # ``answering`` 이 True인 동안은 스킬을 새로 실행하지 않는다(중복 실행 방지).
        answer_rounds = 0
        answering = False
        recovered_pending = False
        skill_results: list[Any] = []
        pending_send_events: list[threading.Event] = []
        result_msg = ""
        task_success = True

        while current_round < max_rounds:
            current_round += 1
            send_fb(f"계획 수립 중 (라운드 {current_round})", 10 + current_round * 5)
            self.node._set_state(AgentState.THINKING, "", "계획 분석 중")

            try:
                self.node.get_logger().info(f"Starting LLM planning (round {current_round})")
                llm_resp = await self.node._run_blocking(
                    "llm_chat",
                    self.node._llm.chat,
                    messages,
                    system_prompt=self.node._build_system_prompt(),
                    response_format={"type": "json_object"},
                    timeout_sec=getattr(self.node, "_llm_timeout_sec", timeout),
                )
                from .utils import parse_llm_plan

                plan = parse_llm_plan(llm_resp)
                self.node.get_logger().info(
                    f"LLM planning complete: {len(plan.skills)} skills planned, final={plan.final}"
                )
            except Exception as e:
                self.node.get_logger().error(f"Failed to parse LLM response: {e}")
                result_msg = f"해석 오류: {e}"
                task_success = False
                self.node._set_state(AgentState.ERROR, "", f"LLM 에러: {e}")
                break

            if answering and plan.skills:
                # 요약 라운드에서 스킬 재호출은 받지 않는다. 정보성 스킬만 요약
                # 라운드에 들어오므로 물리적 부작용은 없지만, 같은 조회 스킬을
                # 반복 실행하면 지연과 토큰만 늘고 답변은 나오지 않는다.
                self.node.get_logger().warning(
                    "Summary round returned a skill plan instead of a final answer — "
                    "using deterministic fallback from executed results"
                )
                result_msg = _compose_result_answer(skill_results) or "완료"
                break

            if not plan.skills:
                # 위치 질의인데 스킬을 못 골랐으면 결정론적으로 실행한다.
                #
                # 약한 LLM(gemma4:e4b)은 툴 호출 대신 툴 호출을 **서술**하는 산문을 낸다
                # (실측: "…[identify_location] 스킬을 사용하겠습니다 …"). 이때 plan.skills 가
                # 비어 final=True 가 되므로, 아래 `_route_location_query` 교정은 호출될 기회조차
                # 없이 산문이 그대로 최종 답변으로 나갔다. 형식 재시도로 왕복을 늘리기보다
                # 위치 질의는 여기서 바로 확정 처리한다(모델 성능과 무관하게 동작).
                # 이동 명령도 같은 처리를 받는다("키친으로 이동해" 에 산문만 답하는 경우).
                forced_chain = None
                if not answering:
                    # 요약 라운드에서는 결정론적 강제 실행을 하지 않는다. 이 라운드의
                    # 목적은 이미 실행된 결과를 답변으로 만드는 것이므로, 스킬을 새로
                    # 강제하면 답변 없이 스킬만 다시 실행된다.
                    forced_chain = _force_navigation_if_misrouted(
                        [], instruction, self.node.get_logger()
                    ) or _location_chain_for(instruction, self.node, self.node.get_logger())
                if forced_chain:
                    self.node.get_logger().warning(
                        "LLM returned no skill for a location/navigation command "
                        f"(final={plan.final}, parse_failed={plan.parse_failed}) — "
                        f"forcing '{forced_chain[0].get('skill')}' deterministically. "
                        f"Raw response (first 200 chars): {llm_resp[:200]}"
                    )
                    chain_success, recovered_pending = await self.execute_skill_chain(
                        forced_chain,
                        messages,
                        llm_resp,
                        instruction,
                        timeout,
                        skill_results,
                        pending_send_events,
                        recovered_pending,
                        send_fb,
                    )
                    if chain_success and skill_results and skill_results[-1].success:
                        result_msg = skill_results[-1].message or "완료"
                        break
                    # 강제 실행이 실패하면 기존 형식 교정 흐름으로 계속 진행한다.

                # 형식 교정 재시도 대상 판정. 두 가지 경우 모두 포함한다:
                # (1) final=False — LLM이 도구를 호출하려 했지만 형식이 맞지 않아
                #     파싱에 실패한 경우(소형 LLM이 tool_calls로 회귀 등).
                # (2) parse_failed=True — JSON 파싱 자체가 실패해 원문 전체가
                #     response로 강등되어 final=True가 된 경우. 이 경우 겉보기엔
                #     "의도된 최종 답변"처럼 보이지만 실제로는 형식 오류이므로,
                #     사용자에게 산문(혹은  thinking 잔해)을 그대로 넘기기 전에
                #     최소 1회는 올바른 형식으로 재시도할 가치가 있다.
                needs_format_retry = not plan.final or plan.parse_failed
                if needs_format_retry and format_retries < max_format_retries:
                    format_retries += 1
                    self.node.get_logger().warning(
                        f"LLM returned 0 skills (round {current_round}, "
                        f"final={plan.final}, parse_failed={plan.parse_failed}) — "
                        f"requesting format correction and retrying "
                        f"({format_retries}/{max_format_retries})"
                    )
                    # raw 응답의 앞부분을 로깅하여 디버깅 지원
                    self.node.get_logger().info(
                        f"Raw LLM response (first 300 chars): {llm_resp[:300]}"
                    )
                    messages.append({"role": "assistant", "content": llm_resp})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "응답이 올바른 JSON 형식이 아닙니다. "
                                "반드시 다음 형식 중 하나로만 다시 응답해 주세요.\n"
                                '{"skill": "<스킬명>", "params": {...}, "reason": "..."}\n'
                                '또는 {"skills": [{"skill": "<스킬명>", "params": {...}}], "reason": "..."}\n'
                                '또는 {"skill": null, "response": "<답변>"}\n'
                                "tool_calls 형식은 사용하지 마세요. "
                                "[사용 가능한 스킬]에 있는 스킬명만 사용하세요. "
                                "장소/좌표 지식 조회는 get_location, 현재 로봇 좌표 조회는 get_status를 사용하세요."
                            ),
                        }
                    )
                    # 형식 교정 재시도는 계획 라운드 예산을 소모하지 않는다.
                    current_round -= 1
                    continue

                if not plan.final:
                    # 형식 교정 예산을 모두 쓰고도 스킬도 응답도 얻지 못한 상태.
                    # 성공으로 위장해 "완료"를 돌려주면 실패가 은폐되므로 실패 처리한다.
                    self.node.get_logger().error(
                        "LLM failed to produce a valid plan after "
                        f"{max_format_retries} format corrections"
                    )
                    result_msg = plan.response or "LLM 응답 형식 오류로 계획을 수립하지 못했습니다."
                    task_success = False
                    self.node._set_state(AgentState.ERROR, "", "LLM 응답 형식 오류")
                    break

                if plan.parse_failed:
                    # 형식 교정 재시도를 다 썼는데도 JSON을 못 냈다 — 사용자 확정 정책대로
                    # 산문을 답변으로 그대로 사용하되, 재시도가 소진됐다는 사실을 로그로
                    # 남겨 "정상적인 최종 답변"과 구분되도록 한다.
                    self.node.get_logger().warning(
                        f"Format correction retries ({max_format_retries}) exhausted with "
                        "still-unparseable output — using raw text as final answer "
                        f"(first 300 chars): {llm_resp[:300]}"
                    )
                result_msg = plan.response or "완료"
                if answering and (not plan.response or looks_like_json(plan.response)):
                    # 요약 라운드가 사람이 읽을 답변을 내지 못한 경우(형식 재시도 소진으로
                    # 강등된 JSON 원문 등)에는 실행 결과 데이터로 직접 답한다.
                    result_msg = _compose_result_answer(skill_results) or result_msg
                self.node.get_logger().info(f"Final answer determined: {result_msg}")
                break

            # 대화형 쿼리에 LLM이 실수로 스킬을 선택한 경우 1회만 재요구한다.
            # 예산을 다 쓰고도 LLM이 스킬을 고집하면 체인 실행으로 넘어가고,
            # 물리 동작 스킬은 execute_skill_chain 에서 개별 차단한다.
            if _is_likely_conversational(instruction) and (
                conversational_retries < max_conversational_retries
            ):
                conversational_retries += 1
                self.node.get_logger().info(
                    f"Conversational query '{instruction}' got skill plan — requesting re-evaluation"
                )
                messages.append({"role": "assistant", "content": llm_resp})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "사용자의 요청은 스킬이 필요하지 않은 대화형 질문입니다. "
                            '반드시 {"skill": null, "response": "<답변>"} 형식으로만 응답하세요.'
                        ),
                    }
                )
                continue

            raw_chain = [{"skill": item.skill, "params": item.params} for item in plan.skills]
            raw_chain = _preserve_user_navigation_target(
                raw_chain, instruction, self.node.get_logger()
            )
            # 이동 교정을 위치질의 교정보다 **먼저** 한다. "거실 위치로 이동해" 처럼 두 조건이
            # 모두 걸리는 문장에서, 사용자가 원한 이동을 조회로 바꿔치기하면 안 된다.
            nav_corrected = _force_navigation_if_misrouted(
                raw_chain, instruction, self.node.get_logger()
            )
            if nav_corrected is not raw_chain:
                raw_chain = nav_corrected
            else:
                raw_chain = _route_location_query(
                    raw_chain, instruction, self.node, self.node.get_logger()
                )
            raw_chain = ensure_stretch_navigation_safety(
                raw_chain, self.node._skills, self.node.get_logger()
            )
            policy_error = self.node._validate_skill_chain(raw_chain)
            if policy_error:
                self.node.get_logger().warning(f"Plan policy validation failed: {policy_error}")
                messages.append({"role": "assistant", "content": llm_resp})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"계획 검증 실패: {policy_error}. "
                            "복합 조작 스킬(adaptive_pick_object, grasp, place, vla_pick_*, "
                            "pick_*, prepare_right_side_pick, servo_gripper_to_object)은 "
                            "자체완결형이라 다른 스킬과 체이닝할 수 없습니다. 단일 스킬로만 "
                            "계획하거나, 여러 동작이 필요하면 각 동작을 별도 라운드로 순차 "
                            "처리하세요. 장소/좌표 지식 조회는 get_location, 현재 로봇 좌표 "
                            "조회는 get_status를 사용하세요."
                        ),
                    }
                )
                continue

            # 스킬 체인 실행
            chain_success, recovered_pending = await self.execute_skill_chain(
                raw_chain,
                messages,
                llm_resp,
                instruction,
                timeout,
                skill_results,
                pending_send_events,
                recovered_pending,
                send_fb,
            )

            if chain_success:
                # 정보성 스킬만으로 이루어진 체인은 결과 데이터를 사용자에게 정리해
                # 답해야 한다. 예전에는 마지막 스킬의 message(ros_command 는
                # "실행 완료")를 그대로 답변으로 써서 실제 조회 결과가 사라졌다.
                if (
                    not plan.final
                    and not answering
                    and answer_rounds < _MAX_ANSWER_ROUNDS
                    and self._chain_needs_answer(raw_chain)
                ):
                    answer_rounds += 1
                    answering = True
                    self.node.get_logger().info(
                        "Informational skill chain succeeded — requesting a synthesized "
                        f"final answer from executed results "
                        f"({answer_rounds}/{_MAX_ANSWER_ROUNDS})"
                    )
                    # execute_skill_chain 이 이미 messages 에 실행 결과 요약과 최종
                    # 답변 요청을 넣었다. 다음 라운드가 그것을 소비한다.
                    continue

                # 물리 동작/백그라운드 스킬이거나 요약 라운드 예산을 다 쓴 경우:
                # 마지막 스킬의 결과 메시지로 종료한다. reason 은 내부 전략
                # 필드이므로 사용자 답변으로 쓰지 않는다.
                last_result = skill_results[-1] if skill_results else None
                result_msg = (
                    (last_result.message if last_result else "")
                    or _compose_result_answer(skill_results)
                    or "실행 완료"
                )
                break
            continue
        else:
            self.node.get_logger().error(
                f"Exceeded max replanning rounds. Result so far: {result_msg}"
            )
            result_msg = "재플래닝 횟수 초과 (결과 분석 실패)"
            task_success = False

        return result_msg, skill_results, task_success, pending_send_events

    async def execute_skill_chain(
        self,
        chain: list[dict[str, Any]],
        messages: list[dict[str, str]],
        llm_resp: str,
        instruction: str,
        timeout: float,
        skill_results: list[Any],
        pending_send_events: list[threading.Event],
        recovered_pending: bool,
        send_fb: Any,
    ) -> tuple[bool, bool]:
        """스킬 체인을 순차 실행하고 결과를 messages에 반영한다."""
        chain_success = True
        executed_reports = []

        # assistant 메시지는 체인 실행 전 1회만 추가하여 중복 누적 방지
        messages.append({"role": "assistant", "content": llm_resp})

        for item in chain:
            name, params = item.get("skill"), item.get("params", {})
            if not name:
                continue

            # 대화형 쿼리에서 action 스킬이 선택된 경우 안전하게 건너뛴다.
            # (재요구 예산을 소진하고도 LLM이 물리 동작을 고집한 경우의 최종 방어선)
            if self._is_action_skill(name) and _is_likely_conversational(instruction):
                self.node.get_logger().warning(
                    f"Action skill '{name}' blocked for conversational query: '{instruction}' — skipping"
                )
                executed_reports.append(
                    f"- '{name}': 실행하지 않음. 대화형 요청이라 물리 동작 스킬은 차단됩니다."
                )
                continue

            self.node.get_logger().info(f"Attempting skill execution: {name} (params={params})")
            send_fb(f"실행: {name}", 40)
            self.node._set_state(AgentState.EXECUTING, name, f"{name} 실행 중")
            res = await self.node._run_blocking(
                f"skill:{name}",
                self.node._skills.execute,
                name,
                params,
                timeout,
            )

            clean_result_data = _omit_base64_data(res.result_data)

            if res.success and "file_path" in res.result_data:
                file_path = res.result_data["file_path"]
                msg_text = res.result_data.get("message", f"'{name}' 실행 결과 이미지입니다.")
                self.node.get_logger().info(f"Starting image transfer: {file_path}")
                event = self.node._start_channel_send(msg_text, file_path)
                pending_send_events.append(event)

            skill_results.append(res)

            if getattr(self.node, "_enable_skill_learning", False):
                try:
                    await self.node._run_blocking(
                        "record_skill_episode",
                        self.node._memory.record_skill_episode,
                        name,
                        res.success,
                        duration_sec=res.duration_sec,
                        error="" if res.success else res.message,
                        params=params,
                        instruction=instruction,
                        replanned=not res.success,
                        recovered=(res.success and recovered_pending),
                    )
                except Exception as e:
                    self.node.get_logger().warning(f"Failed to record skill experience: {e}")

            if not res.success:
                self.node.get_logger().error(
                    f"Skill '{name}' failed: {res.message}. Collecting telemetry and attempting replanning..."
                )
                # 실패 직후 최신 로봇 상태 수집 (텔레메트리 Context 주입)
                latest_summary: dict = {}
                try:
                    status_skill = self.node._skills._skills.get("get_status")
                    if hasattr(status_skill, "get_robot_summary"):
                        latest_summary = await self.node._run_blocking(
                            "get_robot_summary_on_failure", status_skill.get_robot_summary
                        )
                except Exception as e:
                    self.node.get_logger().warning(
                        f"Failed to retrieve robot summary on skill failure: {e}"
                    )

                result_details = res.result_data if isinstance(res.result_data, dict) else {}
                error_type = str(result_details.get("error_type") or "SkillExecutionFailed")
                fail_context = {
                    "error_type": error_type,
                    "failed_skill": name,
                    "params": params,
                    "error_message": res.message,
                    "robot_state_at_failure": _omit_base64_data(latest_summary),
                }
                if error_type == "ParameterSchemaError":
                    fail_context["schema_error"] = result_details.get("schema_error", "")
                elif error_type == "SkillPreconditionError":
                    fail_context["precondition_error"] = result_details.get(
                        "precondition_error", ""
                    )
                    fail_context["preconditions"] = result_details.get("preconditions", [])

                recovery_instruction = (
                    "이는 실행 전 파라미터 형식 검증 오류입니다. 로봇은 움직이지 않았습니다. "
                    "input_schema에 맞게 필수 파라미터/타입/enum을 수정한 단일 스킬 계획을 다시 작성하세요. "
                    "같은 잘못된 파라미터를 반복하지 마세요."
                    if error_type == "ParameterSchemaError"
                    else "이는 실행 전 환경 조건 검증 오류입니다. 물리 동작은 시작되지 않았습니다. "
                    "preconditions와 최신 로봇 상태를 확인하고, 조건을 만족하도록 복구 스킬을 먼저 실행하거나 "
                    "안전한 대체 계획을 작성하세요."
                    if error_type == "SkillPreconditionError"
                    else "위 로봇 상태와 실패 사유를 고려하여 최적의 대처 및 다음 계획을 수립해 주세요."
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"오류: 스킬 '{name}' 실행 실패.\n"
                            f"상황 분석 데이터:\n{json.dumps(fail_context, ensure_ascii=False, indent=2)}\n"
                            f"{recovery_instruction} "
                            "장소명 좌표가 필요하면 get_location을 사용하고, analyze_error 같은 등록되지 않은 함수는 사용하지 마세요."
                        ),
                    }
                )
                chain_success = False
                recovered_pending = True
                break
            else:
                recovered_pending = False
                self.node.get_logger().info(f"Skill '{name}' executed successfully")
                executed_reports.append(
                    f"- '{name}': 성공. 결과: {json.dumps(clean_result_data, ensure_ascii=False)}"
                )

        if chain_success:
            results_summary = "\n".join(executed_reports)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"스킬 체인 실행 결과:\n{results_summary}\n\n"
                        "요청된 스킬들이 성공적으로 수행되었습니다. "
                        "다음 행동이 필요하면 스킬을 실행하고, 완료됐으면 반드시 JSON 형식으로만 최종 답변하세요: "
                        '{"skill": null, "response": "<최종 답변>"}'
                    ),
                }
            )

        return chain_success, recovered_pending
