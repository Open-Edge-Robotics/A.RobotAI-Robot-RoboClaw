"""플래너의 규칙 기반 라우팅 헬퍼.

약한 LLM의 오라우팅을 결정론적 규칙으로 교정한다. ROS/LLM 호출 없이 문자열과
스킬 체인만 판정하는 순수 함수들이다.
"""

import re
from typing import Any

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
