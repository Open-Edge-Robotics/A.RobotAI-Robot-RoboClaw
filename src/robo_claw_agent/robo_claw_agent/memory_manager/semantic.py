import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _normalize_location_name(name: str) -> str:
    """이름을 대소문자·공백에 무관하게 비교하기 위한 정규화 키를 만든다.

    "거실"/"거 실", "Kitchen"/"kitchen"이 동일 위치로 해석되도록 내부 공백을
    모두 제거하고 소문자화한다.
    """
    return re.sub(r"\s+", "", str(name or "")).lower()


# 장소명 뒤에 붙는 로봇 라벨 괄호. "안방 (Former)", "주방 (Cloid 2호)" 처럼 어느 로봇
# 맵에서 기록한 장소인지 표시하려고 붙인 괄호는 물리적 장소 이름의 일부가 아니다.
_ROBOT_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _\-]{0,23}")

# 로봇 자신의 위치를 가리키는 표현. 좌표는 의미가 있지만 장소 이름이 아니므로
# 순회 목록·장소 등록에서 제외한다(비교는 공백 제거 + 소문자 기준).
_SELF_LOCATION_TOKENS = (
    "현재",
    "현위치",
    "지금",
    "여기",
    "여긴",
    "이곳",
    "저기",
    "그곳",
    "거기",
    "이쪽",
    "저쪽",
    "마지막",
    "직전",
    "방금",
    "로봇",
    "내위치",
    "내",
    "나",
    "너",
    "저",
    "제",
    "우리",
    "자신",
    "위치",
    "좌표",
    "장소",
    "자리",
    "지점",
    "포즈",
    "current",
    "now",
    "here",
    "self",
    "robot",
    "last",
    "pose",
    "position",
    "location",
)


def _is_self_location_name(name: str) -> bool:
    """장소 이름이 아니라 로봇 자신의 위치를 가리키는 표현인지 판정한다.

    "현재 위치", "여기", "마지막 위치", "current pose" 처럼 위치 지시어만으로 이루어진
    이름은 기억된 장소가 아니다. 그런 이름을 장소로 등록하면 순회 목록에 방처럼 섬이고,
    동료 로봇은 같은 이름을 자기 맵에서 찾지 못한다. 지시어에 실제 장소명이 붙어
    있으면("거실 소파") 장소로 본다.
    """
    normalized = _normalize_location_name(name)
    if not normalized:
        return True
    residue = normalized
    for token in sorted(_SELF_LOCATION_TOKENS, key=len, reverse=True):
        residue = residue.replace(token, "")
    return not residue


def _canonical_place_name(name: str) -> str:
    """장소명에서 뒤에 붙은 로봇 라벨 괄호를 떼어낸 표준 이름을 만든다.

    "안방 (Former)" → "안방". 괄호 안이 ASCII 로봇 라벨 형태가 아니면(예:
    "회의실 (2층)") 이름을 그대로 둔다. 표준 이름이 비면 원래 이름을 돌려준다.
    """
    text = str(name or "").strip()
    if not text.endswith(")"):
        return text
    open_index = text.rfind("(")
    if open_index <= 0:
        return text
    base = text[:open_index].strip()
    label = text[open_index + 1 : -1].strip()
    if not base or not _ROBOT_LABEL_RE.fullmatch(label):
        return text
    return base


# ── 장소명 추출 (위치 도메인 공용) ────────────────────────────────
# 이 헬퍼들은 원래 skills/system_skill/rag.py 에 있었으나, planner 의 결정론적 위치
# 라우팅도 동일 판정이 필요하다. planner 가 skills 패키지를 import 하면 (a) 의존 방향이
# 거꾸로이고 (b) skills/system_skill/__init__ 이 ROS 메시지(geometry_msgs 등)를 끌어와
# ROS 없는 환경에서 조용히 실패한다. 그래서 ROS 비의존인 여기로 옮겼다.
# rag.py 는 기존 이름 그대로 재노출하므로 호출부/테스트는 영향받지 않는다.

# LLM이 자유 형식으로 저장할 때 키 표기가 흔들린다(location vs location_name 등).
_LOCATION_NAME_KEYS = (
    "target_name",
    "name",
    "object_name",
    "place_name",
    "location_name",
    "location",
    "place",
)

# 텍스트에서 장소명을 뽑는 두 문형(LLM이 이름을 자유 문장으로만 넣는 경우 대비):
#  (A) 이름이 '위치' 앞:  "거실의 위치는 …", "충전대 위치: …", "'living room'의 위치 좌표는 …"
#  (B) 이름이 '위치는' 뒤: "현재 위치는 충전대입니다", "이 위치는 거실이다"
# 다국어/다단어 이름(예: "living room", "회의실 2")을 위해 내부 공백을 허용한다.
_NAME_BEFORE_LOC = re.compile(
    r"^\s*['\"]?([0-9A-Za-z가-힣][0-9A-Za-z가-힣 ]*?)['\"]?\s*(?:의\s*)?위치"
)
_NAME_AFTER_LOC = re.compile(
    r"위치는?\s*['\"]?([0-9A-Za-z가-힣][0-9A-Za-z가-힣 ]*?)['\"]?\s*"
    r"(?:입니다|이다|이에요|에요|예요|이야|임)"
)
# (C) '<이름>(으)로 기억/저장/등록' (예: "3,4 좌표를 실험실로 기억해줘")
_NAME_ACTION_LOC = re.compile(
    r"(?:^|[\s,.\)\]]|[0-9]+(?:\.[0-9]+)?\s*(?:좌표|위치)?\s*)['\"]?([가-힣A-Za-z][0-9A-Za-z가-힣_]{0,19})['\"]?(?:으로|로|을|를)\s*(?:기억|저장|등록|설정)"
)
# (D) '회의실 (-1.5, 2.8)' 처럼 괄호 순서쌍 직전 이름
_NAME_BEFORE_PAREN = re.compile(
    r"^\s*['\"]?([0-9A-Za-z가-힣][0-9A-Za-z가-힣 ]{0,19}?)['\"]?\s*(?:\(위치\)\s*)?(?:\s*위치)?\s*\("
)
# (A) 문형에서 장소명이 아닌 지시/부사어가 이름으로 잡히지 않도록 제외한다.
_NON_NAME_WORDS = frozenset(
    {"현재", "지금", "이", "그", "저", "여기", "우리", "제", "너", "나", "현", "좌표"}
)

# (E) '현재 위치(x: 1.0, y: 2.0)는 안방입니다' / '현재 좌표 (…)는 신발장입니다'
#     실기에서 가장 흔한 발화. 좌표 괄호가 이름 앞을 가로막아 (A)/(B) 문형이 모두
#     빗나갔고, 그 결과 장소가 아닌 일반 지식으로 저장됐다.
_NAME_AFTER_COORD_PAREN = re.compile(
    r"(?:위치|좌표)\s*\([^)]*\)\s*(?:는|은)?\s*['\"]?([0-9A-Za-z가-힣][0-9A-Za-z가-힣 ]*?)['\"]?\s*"
    r"(?:입니다|이다|이에요|에요|예요|이야)"
)
# (F) '안방의 좌표는 x: 1.0, y: 2.0' / '거실 좌표: x=…' — '위치' 대신 '좌표'를 쓰는 문형
_NAME_BEFORE_COORDS = re.compile(
    r"^\s*['\"]?([0-9A-Za-z가-힣][0-9A-Za-z가-힣 ]{0,19}?)['\"]?\s*(?:의\s*)?(?:좌표|위치)\s*"
    r"[:：]?\s*(?:는|은)?\s*(?:x\s*[:=]|\(|-?\d)"
)
# (G) '데스크: x=6.43, y=-3.71' — 이름 토큰 하나 + 콜론 + 좌표
_NAME_BEFORE_COLON_COORDS = re.compile(
    r"^\s*['\"]?([0-9A-Za-z가-힣][0-9A-Za-z가-힣_]{0,19})['\"]?\s*[:：]\s*(?:x\s*[:=]|-?\d+\s*,)"
)
# 장소명이 아닌 운영 로그 어휘. 좌표 문형에서 "이동 성공 좌표" 같은 표현이 장소로
# 승격되는 것을 막는다(_is_self_location_name 이 걸러내지 못하는 경우 보완).
_PLACE_NAME_STOPWORDS = frozenset(
    {"이동", "성공", "실패", "좌표", "위치", "지도", "맵", "경로", "스캔", "관찰", "기록", "결과"}
)
# "이동 성공 좌표: … (키친 (-5.15, -1.18))" 처럼 괄호 안 장소명을 뽑는다.
_PAREN_NAME_RE = re.compile(r"\(([0-9A-Za-z가-힣][0-9A-Za-z가-힣 ]{0,19}?)\s*\(")
# 정방향 위치 근거로 인정하지 않을 순수 운영 로그 타입.
_FWD_SKIP_TYPES = frozenset(
    {
        "skill_episode",
        "skill_lesson",
        "blocked_coordinate",
        "scene_observation",
        "map_visual_analysis",
        "observation",
        "where_am_i",
        "virtual_obstacle",
        "scan_room",
        "explore_result",
        "patrol_result",
        # 마지막 위치 스냅샷. 텍스트가 "마지막 위치: x=…" 형태라 장소명 추출기에
        # "마지막"이 장소로 잡히고, 위치 라우팅이 초기 위치 설정을 장소 이동으로 오인한다.
        "last_pose",
        # VLM/스캔이 기록한 객체 관측은 이동 목적지가 아니다(조작 조회 전용).
        "object_location",
    }
)


def _location_name_from_metadata(metadata: dict[str, Any]) -> str:
    """metadata 의 어떤 이름키 표기로 저장됐든 장소명을 뽑는다."""
    if not isinstance(metadata, dict):
        return ""
    for key in _LOCATION_NAME_KEYS:
        value = str(metadata.get(key, "")).strip()
        if value and not _is_self_location_name(value):
            return value
    return ""


def _location_name_from_text(text: str) -> str:
    """저장 텍스트에서 장소명을 추출한다(이름이 '위치' 앞/뒤 어디에 오든).

    LLM이 이름을 metadata가 아니라 텍스트에만 넣거나(예: '거실의 위치는 x: …'),
    자유 문장으로 저장한 경우(예: '현재 위치는 충전대입니다', '3,4 좌표를 실험실로 기억해줘')의 폴백.
    사이트/언어 무관하게 임의의 장소명을 받는다.
    """
    t = str(text or "")
    # (E) '…위치(x, y)는 <이름>입니다' — 좌표 괄호가 이름 앞에 오는 실기 발화.
    m = _NAME_AFTER_COORD_PAREN.search(t)
    if name := _clean_place_name(m.group(1) if m else ""):
        return name
    # (B) '…위치는 <이름>입니다' 를 먼저 시도(문두의 '현재/이/그' 오검출 방지).
    m = _NAME_AFTER_LOC.search(t)
    if name := _clean_place_name(m.group(1) if m else ""):
        return name
    # (A) '<이름> (의) 위치…' — 단, 지시/부사어는 이름으로 보지 않는다.
    m = _NAME_BEFORE_LOC.search(t)
    if name := _clean_place_name(m.group(1) if m else ""):
        return name
    # (F) '<이름>의 좌표는 x: …' / '<이름> 좌표: x=…'
    m = _NAME_BEFORE_COORDS.search(t)
    if name := _clean_place_name(m.group(1) if m else ""):
        return name
    # (G) '<이름>: x=…, y=…'
    m = _NAME_BEFORE_COLON_COORDS.search(t)
    if name := _clean_place_name(m.group(1) if m else ""):
        return name
    # (C) '<이름>(으)로 기억/저장/등록' (예: "3,4 좌표를 실험실로 기억해줘")
    m = _NAME_ACTION_LOC.search(t)
    if name := _clean_place_name(m.group(1) if m else ""):
        return name
    # (D) '<이름> (x, y)' (예: "회의실 (-1.5, 2.8)")
    m = _NAME_BEFORE_PAREN.search(t)
    if name := _clean_place_name(m.group(1) if m else ""):
        return name
    return ""


def _clean_place_name(name: str) -> str:
    """추출된 후보가 실제 장소명인지 확인한다(지시어·운영 로그 어휘 배제).

    문형 매칭만으로는 "현재 위치", "이동 성공" 같은 표현이 잡힌다. 그런 이름을
    장소로 등록하면 순회 목록이나 이동 목적지로 오용되므로 여기서 한 번 더 거른다.
    """
    candidate = str(name or "").strip().strip("'\"")
    if not candidate or candidate in _NON_NAME_WORDS:
        return ""
    if _is_self_location_name(candidate):
        return ""
    if any(word in _PLACE_NAME_STOPWORDS for word in candidate.split()):
        return ""
    return candidate


def _entry_place_names(entry: dict[str, Any]) -> list[str]:
    """항목이 가리키는 장소명 후보들(표준 이름키 → location_type/category → 텍스트).

    옛 무좌표 항목(예: {"location_type":"Charging Station"} + 텍스트 "…충전대입니다")도
    텍스트에서 뽑은 한글 이름으로 매칭되도록 여러 후보를 모은다.
    """
    names: list[str] = []
    m = entry.get("metadata", {})
    if isinstance(m, dict):
        n = _location_name_from_metadata(m)
        if n:
            names.append(n)
        for key in ("location_type", "category"):
            v = str(m.get(key) or "").strip()
            if v:
                names.append(v)
    t = _location_name_from_text(entry.get("text", ""))
    if t:
        names.append(t)
    return names


def _entry_all_names(entry: dict[str, Any]) -> list[str]:
    """`_entry_place_names` + 괄호 안 이름(navigated_coordinate 근거용)."""
    names = list(_entry_place_names(entry))
    m = _PAREN_NAME_RE.search(str(entry.get("text", "")))
    if m:
        names.append(m.group(1).strip())
    return [n for n in names if n]


def known_place_in_text(text: str, memory: Any) -> str:
    """지시문에 **기억된** 장소명이 들어있으면 그 표준 이름을 돌려준다.

    시맨틱 맵 → 벡터스토어 위치 항목 순으로 본다. 시맨틱 맵만 보면 desync된 순간
    (메모리 디렉터리만 초기화하고 Qdrant는 남긴 경우 등) 호출부의 결정론적 라우팅이
    조용히 발동하지 않아, 결과가 쿼리 문구·임베딩 점수 운에 맡겨진다.
    """
    if memory is None:
        return ""
    norm = _normalize_location_name(text)
    if not norm:
        return ""

    def _match(name: str) -> str:
        n_norm = _normalize_location_name(name)
        return name if len(n_norm) >= 2 and n_norm in norm else ""

    if hasattr(memory, "get_all_objects"):
        try:
            for obj in memory.get_all_objects():
                meta = obj.get("metadata", {})
                if isinstance(meta, dict) and meta.get("alias_of"):
                    continue
                if hit := _match(str(obj.get("name") or "")):
                    return hit
        except Exception as exc:  # noqa: BLE001
            logger.warning("시맨틱 맵 장소명 조회 실패: %s", exc)

    store = getattr(memory, "_vector_store", None)
    if store is None or not hasattr(store, "list_entries"):
        return ""
    try:
        for entry in store.list_entries():
            meta = entry.get("metadata", {})
            if isinstance(meta, dict) and meta.get("type") in _FWD_SKIP_TYPES:
                continue
            for name in _entry_all_names(entry):
                if hit := _match(name):
                    return hit
    except Exception as exc:  # noqa: BLE001
        logger.warning("벡터스토어 장소명 조회 실패 — 위치 라우팅이 약해집니다: %s", exc)


_XY_IN_TEXT = re.compile(
    r"[xyXY]\s*[:=]\s*(-?\d+(?:\.\d+)?)[,\s]+[xyXY]\s*[:=]\s*(-?\d+(?:\.\d+)?)"
)


def _entry_xy(entry: dict[str, Any]) -> tuple[float, float] | None:
    """RAG 항목에서 좌표를 뽑는다. metadata의 x/y 우선, 없으면 텍스트에서 파싱."""
    text = str(entry.get("text", ""))
    if "frame=odom" in text:
        return None
    meta = entry.get("metadata", {})
    if isinstance(meta, dict) and "x" in meta and "y" in meta:
        try:
            return float(meta["x"]), float(meta["y"])
        except (TypeError, ValueError):
            pass
    m = _XY_IN_TEXT.search(text)
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except (TypeError, ValueError):
            pass
    return None


def _best_location_for_targets(
    memory: Any, targets: set[str]
) -> tuple[str, float, float, str, str | None] | None:
    """대상 장소명들의 좌표를 찾는다. 우선순위: 시맨틱맵 > type=location > navigated_coordinate > 기타.

    반환: (표시이름, x, y, source, record_id) 또는 None.
    """
    if not targets:
        return None
    best: tuple[int, str, float, float, str, str | None] | None = None

    if not hasattr(type(memory), "find_location_entries") and hasattr(memory, "get_all_objects"):
        for obj in memory.get_all_objects():
            meta = obj.get("metadata", {})
            if isinstance(meta, dict) and meta.get("alias_of"):
                continue
            name = str(obj.get("name") or "")
            if _normalize_location_name(name) not in targets:
                continue
            pos = obj.get("position") or {}
            try:
                x, y = float(pos["x"]), float(pos["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if best is None or 3 > best[0]:
                best = (3, name, x, y, "시맨틱 맵", None)

    store = getattr(memory, "_vector_store", None)
    if store is not None and hasattr(store, "list_entries"):
        try:
            if hasattr(type(memory), "find_location_entries"):
                entries = list(reversed(memory.find_location_entries(targets)))
            else:
                entries = store.list_entries()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[best_location] 위치 좌표 조회용 RAG 스캔 실패 — 시맨틱 맵 근거만 사용합니다: %s",
                exc,
            )
            entries = []
        for e in entries:
            m = e.get("metadata", {})
            typ = m.get("type") if isinstance(m, dict) else None
            if typ in _FWD_SKIP_TYPES:
                continue
            xy = _entry_xy(e)
            if xy is None:
                continue
            ent_names = _entry_all_names(e)
            disp = next((n for n in ent_names if _normalize_location_name(n) in targets), "")
            if not disp:
                continue
            rank = 2 if typ == "location" else (1 if typ == "navigated_coordinate" else 0)
            src = (
                "저장된 위치"
                if typ == "location"
                else ("이동 기록" if typ == "navigated_coordinate" else "기록")
            )
            if best is None or rank > best[0]:
                best = (rank, disp, xy[0], xy[1], src, e.get("id"))

    if best is None:
        return None
    return (best[1], best[2], best[3], best[4], best[5])


class SemanticMixin:
    """SQL 기반 장소·별칭·좌표 조회를 담당합니다."""

    def add_object_location(
        self,
        name: str,
        x: float,
        y: float,
        metadata: dict[str, Any] | None = None,
        aliases: list[str] | None = None,
        *,
        as_object: bool = False,
    ) -> None:
        """객체의 위치 정보를 SQLite에 저장하거나 갱신합니다.

        좌표(x, y)는 map 프레임 기준이어야 한다. 호출부는 좌표 출처를 명확히 하기 위해
        metadata에 frame_id(예: "map")를 넣는 것을 권장한다(navigate_to가 동일 기준을 가정).
        로봇 자신의 위치를 가리키는 이름("현재 위치" 등)은 장소가 아니므로 등록하지 않는다.

        ``as_object=True`` 는 VLM·스캔이 감지한 **객체** 관측이다. 객체는 이름으로 조회해
        조작(grasp/place)에만 쓰고, 이동 목적지·순회 장소 목록에서는 제외한다
        (`object_location` 타입으로 저장).
        """
        if _is_self_location_name(name):
            logger.info("Semantic map registration skipped (not a place name): %r", name)
            return
        entry_type = "object_location" if as_object else "location"
        stored = self._structured_store.add(
            f"{name}의 위치는 x: {x}, y: {y} 입니다.",
            {
                **(metadata or {}),
                "type": entry_type,
                "location_name": name,
                "x": x,
                "y": y,
                "aliases": aliases or [],
            },
        )
        if not stored:
            raise ValueError("장소를 저장하지 못했습니다.")

    def get_object_location(
        self, name: str, *, include_objects: bool = False
    ) -> dict[str, Any] | None:
        """이름으로 객체 위치 조회.

        대소문자·공백을 정규화한 이름과 별칭을 SQL 인덱스로 조회합니다.
        예를 들어 "거 실"→"거실", "Kitchen"→"kitchen"으로 비교합니다.
        기본값은 장소만 보며, 조작처럼 객체 관측까지 필요한 경우
        ``include_objects=True`` 를 준다(이동 목적지 해석은 기본값 유지).
        """
        return self._structured_store.location(name, include_objects=include_objects)

    def get_nearest_object(
        self, x: float, y: float, radius_m: float | None = None
    ) -> dict[str, Any] | None:
        """좌표에서 가장 가까운 명명된 위치를 조회한다 (좌표→이름 역방향 조회).

        alias 항목은 제외하고 표준 이름만 후보로 본다. radius_m이 주어지면 그 반경
        밖의 위치는 무시한다. 반환 dict에는 최근접 거리를 담은 distance_m를 추가한다.
        """
        return self._structured_store.nearest(x, y, radius_m)

    def get_all_objects(self, *, include_objects: bool = False) -> list[dict[str, Any]]:
        """장소 목록(시맨틱 맵).

        기본값은 장소만 반환한다. 순회(tidy/patrol)와 좌표→이름 역조회가 객체 관측을
        방처럼 순회하지 않도록, 객체가 필요하면 ``include_objects=True`` 를 준다.
        """
        return self._structured_store.locations(include_objects=include_objects)


# ── 위치 metadata 표준화 (저장 경로·이관 도구 공용) ──────────────────
# 원래 skills/system_skill/rag.py 에 있었으나, 레거시 지식 이관(place_migration)도 같은
# 규칙을 써야 두 경로가 어긋나지 않으므로 ROS 비의존 모듈로 옮겼다. rag.py 는 기존
# 이름 그대로 재노출하므로 호출부/테스트는 영향을 받지 않는다.

_XY_IN_TEXT = re.compile(
    r"x\s*[:=]\s*(-?\d+(?:\.\d+)?)\D+?y\s*[:=]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE
)
_PAIR_IN_TEXT = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")
_COMMA_COORD_IN_TEXT = re.compile(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*(?:좌표|위치)?")


def _is_location_metadata(metadata: dict[str, Any]) -> bool:
    """이름 + 좌표(x, y)를 모두 갖춘 위치 지식인지 판정한다."""
    if not isinstance(metadata, dict):
        return False
    if not _location_name_from_metadata(metadata):
        return False
    if "x" not in metadata or "y" not in metadata:
        return False
    try:
        float(metadata["x"])
        float(metadata["y"])
    except (TypeError, ValueError):
        return False
    return True


def _normalize_location_metadata(text: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """위치 저장을 사이트/언어 무관하게 일관 저장하도록 metadata를 표준화한다.

    이름/좌표가 metadata 또는 텍스트 어디에 있든, 확인되면 표준 키
    (`location_name`, `x`, `y`, `type="location"`)를 채운다. 이렇게 하면 시맨틱 맵
    동기화·좌표→이름 역조회·사실 우선 랭킹·검색 필터가 장소명과 무관하게 일관 동작한다.
    좌표를 확인할 수 없으면(일반 지식 등) 원본을 그대로 둔다.
    """
    md = dict(metadata) if isinstance(metadata, dict) else {}

    # metadata 의 이름키는 _location_name_from_metadata 가 걸러내므로, 지시어 이름을
    # 지우려면 원본 키를 직접 본다.
    explicit_name = next(
        (str(md.get(key, "")).strip() for key in _LOCATION_NAME_KEYS if md.get(key)),
        "",
    )
    name = explicit_name or _location_name_from_text(text)
    # "현재 위치"/"여기" 같은 위치 지시어는 장소명이 아니다. 지식 텍스트는 그대로
    # 저장하되 위치 사실로 승격하지 않아 시맨틱 맵에 장소로 등록되지 않게 한다.
    if name and _is_self_location_name(name):
        name = ""
        for key in _LOCATION_NAME_KEYS:
            md.pop(key, None)

    xy: tuple[float, float] | None = None
    if "x" in md and "y" in md:
        try:
            xy = (float(md["x"]), float(md["y"]))
        except (TypeError, ValueError):
            xy = None
    t_str = str(text or "")
    for pattern in (_XY_IN_TEXT, _PAIR_IN_TEXT, _COMMA_COORD_IN_TEXT):
        if xy is not None:
            break
        m = pattern.search(t_str)
        if m:
            try:
                xy = (float(m.group(1)), float(m.group(2)))
            except (TypeError, ValueError):
                xy = None

    if name and xy is not None:
        md.setdefault("location_name", name)
        md["x"] = xy[0]
        md["y"] = xy[1]
        if not md.get("type"):
            md["type"] = "location"
    return md
