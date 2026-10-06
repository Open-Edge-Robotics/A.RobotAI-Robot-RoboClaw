import logging
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _normalize_location_name(name: str) -> str:
    """이름을 대소문자·공백에 무관하게 비교하기 위한 정규화 키를 만든다.

    "거실"/"거 실", "Kitchen"/"kitchen"이 동일 위치로 해석되도록 내부 공백을
    모두 제거하고 소문자화한다.
    """
    return re.sub(r"\s+", "", str(name or "")).lower()


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
# (A) 문형에서 장소명이 아닌 지시/부사어가 이름으로 잡히지 않도록 제외한다.
_NON_NAME_WORDS = frozenset(
    {"현재", "지금", "이", "그", "저", "여기", "우리", "제", "너", "나", "현"}
)
# "이동 성공 좌표: … (키친 (-5.15, -1.18))" 처럼 괄호 안 장소명을 뽑는다.
_PAREN_NAME_RE = re.compile(r"\(([0-9A-Za-z가-힣][0-9A-Za-z가-힣 ]{0,19}?)\s*\(")
# 정방향 위치 근거로 인정하지 않을 순수 운영 로그 타입.
_FWD_SKIP_TYPES = frozenset({"skill_episode", "skill_lesson", "blocked_coordinate"})


def _location_name_from_metadata(metadata: dict[str, Any]) -> str:
    """metadata 의 어떤 이름키 표기로 저장됐든 장소명을 뽑는다."""
    if not isinstance(metadata, dict):
        return ""
    for key in _LOCATION_NAME_KEYS:
        value = str(metadata.get(key, "")).strip()
        if value:
            return value
    return ""


def _location_name_from_text(text: str) -> str:
    """저장 텍스트에서 장소명을 추출한다(이름이 '위치' 앞/뒤 어디에 오든).

    LLM이 이름을 metadata가 아니라 텍스트에만 넣거나(예: '거실의 위치는 x: …'),
    자유 문장으로 저장한 경우(예: '현재 위치는 충전대입니다')의 폴백.
    사이트/언어 무관하게 임의의 장소명을 받는다.
    """
    t = str(text or "")
    # (B) '…위치는 <이름>입니다' 를 먼저 시도(문두의 '현재/이/그' 오검출 방지).
    m = _NAME_AFTER_LOC.search(t)
    if m:
        return m.group(1).strip()
    # (A) '<이름> (의) 위치…' — 단, 지시/부사어는 이름으로 보지 않는다.
    m = _NAME_BEFORE_LOC.search(t)
    if m:
        name = m.group(1).strip()
        if name and name not in _NON_NAME_WORDS:
            return name
    return ""


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
                if (hit := _match(str(obj.get("name") or ""))):
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
                if (hit := _match(name)):
                    return hit
    except Exception as exc:  # noqa: BLE001
        logger.warning("벡터스토어 장소명 조회 실패 — 위치 라우팅이 약해집니다: %s", exc)
    return ""


class SemanticMixin:
    """시맨틱 매핑 (이름 기반 O(1) 객체 위치 조회) 전담"""

    _semantic_map: dict[str, dict[str, Any]]
    _semantic_path: Path

    # StorageMixin에서 제공될 메서드
    def _save_data(self, path: Path, data: Any) -> None: ...

    def add_object_location(
        self,
        name: str,
        x: float,
        y: float,
        metadata: dict[str, Any] | None = None,
        aliases: list[str] | None = None,
    ) -> None:
        """객체의 위치 정보를 시맨틱 맵에 저장/업데이트 (O(1)).

        좌표(x, y)는 map 프레임 기준이어야 한다. 호출부는 좌표 출처를 명확히 하기 위해
        metadata에 frame_id(예: "map")를 넣는 것을 권장한다(navigate_to가 동일 기준을 가정).
        """
        aliases = aliases or []
        entry = {
            "name": name,
            "position": {"x": x, "y": y},
            "last_seen": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        self._semantic_map[name] = entry

        for alias in aliases:
            normalized = str(alias).strip()
            if not normalized or normalized == name:
                continue

            self._semantic_map[normalized] = {
                "name": normalized,
                "position": {"x": x, "y": y},
                "last_seen": entry["last_seen"],
                "metadata": {
                    **(metadata or {}),
                    "alias_of": name,
                },
            }

        # 저장 시에는 리스트 형태로 변환하여 호환성 유지
        self._save_data(self._semantic_path, list(self._semantic_map.values()))
        logger.info("Semantic map updated: %s at (%.2f, %.2f)", name, x, y)

    def get_object_location(self, name: str) -> dict[str, Any] | None:
        """이름으로 객체 위치 조회.

        정확 일치(O(1))를 먼저 시도하고, 실패하면 대소문자·공백을 무시한
        정규화 매칭으로 폴백한다(예: "거 실"→"거실", "Kitchen"→"kitchen").
        """
        entry = self._semantic_map.get(name)
        if entry is not None:
            return entry

        normalized = _normalize_location_name(name)
        if not normalized:
            return None
        for key, value in self._semantic_map.items():
            if _normalize_location_name(key) == normalized:
                return value
        return None

    def get_nearest_object(
        self, x: float, y: float, radius_m: float | None = None
    ) -> dict[str, Any] | None:
        """좌표에서 가장 가까운 명명된 위치를 조회한다 (좌표→이름 역방향 조회).

        alias 항목은 제외하고 표준 이름만 후보로 본다. radius_m이 주어지면 그 반경
        밖의 위치는 무시한다. 반환 dict에는 최근접 거리를 담은 distance_m를 추가한다.
        """
        try:
            px, py = float(x), float(y)
        except (TypeError, ValueError):
            return None

        best: dict[str, Any] | None = None
        best_dist = float("inf")
        for entry in self._semantic_map.values():
            meta = entry.get("metadata", {})
            if isinstance(meta, dict) and meta.get("alias_of"):
                continue
            pos = entry.get("position") or {}
            try:
                ex, ey = float(pos["x"]), float(pos["y"])
            except (KeyError, TypeError, ValueError):
                continue
            dist = math.hypot(px - ex, py - ey)
            if dist < best_dist:
                best_dist = dist
                best = entry

        if best is None:
            return None
        if radius_m is not None and best_dist > float(radius_m):
            return None
        return {**best, "distance_m": round(best_dist, 3)}

    def get_all_objects(self) -> list[dict[str, Any]]:
        return list(self._semantic_map.values())
