import logging
import math
import re
from pathlib import Path
from typing import Any

# 장소명 추출 헬퍼는 planner 의 결정론적 라우팅과 공유해야 하므로 ROS 비의존인
# memory_manager.semantic 에 있다. 기존 이름 그대로 재노출한다(호출부/테스트 호환).
from robo_claw_agent.memory_manager.semantic import (  # noqa: F401
    _FWD_SKIP_TYPES,
    _NAME_AFTER_LOC,
    _NAME_BEFORE_LOC,
    _NON_NAME_WORDS,
    _PAREN_NAME_RE,
    _entry_all_names,
    _entry_place_names,
    _location_name_from_metadata,
    _location_name_from_text,
    _normalize_location_name,
)
from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)

# 내부 스킬 실행 로그 접두어 (예: "[스킬 rag_add 성공] ") — 사용자 노출 방지용
_SKILL_LOG_PREFIX = re.compile(r"^\s*\[스킬\s+\S+\s+(?:성공|실패)\]\s*")


def _sync_location_to_semantic_map(memory: Any, metadata: dict[str, Any]) -> bool:
    if not hasattr(memory, "add_object_location"):
        return False

    name = _location_name_from_metadata(metadata)
    if not name or "x" not in metadata or "y" not in metadata:
        return False

    try:
        x = float(metadata["x"])
        y = float(metadata["y"])
    except (TypeError, ValueError):
        return False

    aliases = metadata.get("aliases", [])
    if not isinstance(aliases, list):
        aliases = []

    memory.add_object_location(
        name,
        x,
        y,
        metadata={**metadata, "source": metadata.get("source", "rag_add")},
        aliases=[str(alias) for alias in aliases],
    )
    return True


def _sanitize_knowledge_text(text: str) -> str:
    """내부 스킬 로그 접두어([스킬 X 성공] 등)를 제거해 사용자 노출을 막는다.

    (레거시 데이터 방어책: 정책상 스킬 로그는 더 이상 검색에 노출되지 않지만,
    필터 이전에 적재된 항목이 남아 있을 수 있다.)
    """
    cleaned = _SKILL_LOG_PREFIX.sub("", str(text or "")).strip()
    return cleaned or str(text or "").strip()


def _summarize_top_result(result: dict[str, Any]) -> str:
    """검색 상위 결과를 사용자용 자연어로 요약한다.

    위치 지식(이름+좌표)은 metadata에서 좌표를 뽑아 깔끔히 답하고, 그 외에는
    저장 텍스트를 쓰되 내부 스킬 로그 포맷은 정리한다.
    """
    meta = result.get("metadata", {})
    if isinstance(meta, dict):
        name = _location_name_from_metadata(meta)
        if name and "x" in meta and "y" in meta:
            try:
                return f"'{name}'의 위치는 x: {float(meta['x']):.2f}, y: {float(meta['y']):.2f} 입니다."
            except (TypeError, ValueError):
                pass
    return _sanitize_knowledge_text(str(result.get("text", "")))


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


# 역방향 조회(좌표→이름) 폴백에서 위치로 취급하지 않을 운영 로그 타입
_NON_LOCATION_TYPES = frozenset(
    {"skill_episode", "observation", "navigated_coordinate", "blocked_coordinate"}
)
_XY_IN_TEXT = re.compile(
    r"x\s*[:=]\s*(-?\d+(?:\.\d+)?)\D+?y\s*[:=]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE
)


def _normalize_location_metadata(text: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """위치 저장을 사이트/언어 무관하게 일관 저장하도록 metadata를 표준화한다.

    이름/좌표가 metadata 또는 텍스트 어디에 있든, 확인되면 표준 키
    (`location_name`, `x`, `y`, `type="location"`)를 채운다. 이렇게 하면 시맨틱 맵
    동기화·좌표→이름 역조회·사실 우선 랭킹·검색 필터가 장소명과 무관하게 일관 동작한다.
    좌표를 확인할 수 없으면(일반 지식 등) 원본을 그대로 둔다.
    """
    md = dict(metadata) if isinstance(metadata, dict) else {}

    name = _location_name_from_metadata(md) or _location_name_from_text(text)

    xy: tuple[float, float] | None = None
    if "x" in md and "y" in md:
        try:
            xy = (float(md["x"]), float(md["y"]))
        except (TypeError, ValueError):
            xy = None
    if xy is None:
        m = _XY_IN_TEXT.search(str(text or ""))
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


# dedup에서 삭제하면 안 되는 운영 로그 타입(위치 사실이 아님).
_DEDUP_KEEP_TYPES = frozenset(
    {
        "skill_episode",
        "skill_lesson",
        "navigated_coordinate",
        "blocked_coordinate",
        "observation",
        "where_am_i",
    }
)


def _dedup_prior_locations(memory: Any, metadata: dict[str, Any]) -> int:
    """같은 장소를 가리키는 이전 위치 RAG 항목을 삭제한다.

    동일 장소를 다시 저장할 때 RAG 스토어에 중복/좌표 충돌 항목이 쌓이는 것을 막는다
    (시맨틱 맵은 이름으로 덮어쓰지만 RAG는 append이므로). 표준 이름키뿐 아니라
    location_type/category·텍스트에서 뽑은 이름까지 비교해, 좌표 없이 저장됐던 옛
    항목도 정리한다(로그 타입은 제외). 이름 비교는 대소문자·공백 무관 정규화. 반환: 삭제 수.
    """
    if not isinstance(metadata, dict) or metadata.get("type") != "location":
        return 0
    name = _location_name_from_metadata(metadata)
    if not name:
        return 0
    store = getattr(memory, "_vector_store", None)
    if store is None or not hasattr(store, "list_entries") or not hasattr(store, "delete_entries"):
        return 0

    target = _normalize_location_name(name)
    ids: list[str] = []
    try:
        for e in store.list_entries():
            m = e.get("metadata", {})
            # 스킬/좌표 로그류는 위치 사실이 아니므로 삭제 대상에서 제외.
            if isinstance(m, dict) and m.get("type") in _DEDUP_KEEP_TYPES:
                continue
            # 같은 장소를 가리키면(이름키·location_type·텍스트 어디로든) 제거 대상.
            if any(_normalize_location_name(c) == target for c in _entry_place_names(e)):
                eid = e.get("id")
                if eid:
                    ids.append(str(eid))
    except Exception as exc:  # noqa: BLE001
        # 스캔이 실패하면 중복 위치가 남는다(치명적이진 않지만 좌표 충돌의 원인).
        logger.warning("[rag_add] dedup 스캔 실패 — 이전 위치 항목을 정리하지 못했습니다: %s", exc)
        return 0
    if not ids:
        return 0
    try:
        return int(store.delete_entries(ids=ids))
    except Exception:  # noqa: BLE001
        return 0


def _entry_xy(entry: dict[str, Any]) -> tuple[float, float] | None:
    """RAG 항목에서 좌표를 뽑는다. metadata의 x/y 우선, 없으면 텍스트에서 파싱."""
    meta = entry.get("metadata", {})
    if isinstance(meta, dict) and "x" in meta and "y" in meta:
        try:
            return float(meta["x"]), float(meta["y"])
        except (TypeError, ValueError):
            pass
    m = _XY_IN_TEXT.search(str(entry.get("text", "")))
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except (TypeError, ValueError):
            pass
    return None


def _nearest_named_location_from_rag(
    memory: Any, x: float, y: float, radius_m: float | None
) -> dict[str, Any] | None:
    """시맨틱 맵에 없을 때, RAG 저장 항목에서 이름+좌표를 가진 위치를 스캔해 최근접을 찾는다.

    LLM이 저장 metadata에 x/y를 빠뜨려 시맨틱 맵 동기화가 누락된 경우의 폴백.
    이름은 metadata(_location_name_from_metadata)에서, 좌표는 metadata 또는 텍스트에서 얻는다.
    """
    store = getattr(memory, "_vector_store", None)
    if store is None or not hasattr(store, "list_entries"):
        return None
    try:
        entries = store.list_entries()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[identify_location] RAG 스캔 실패 — 좌표→이름 폴백을 쓸 수 없습니다: %s", exc
        )
        return None

    best: dict[str, Any] | None = None
    best_d = float(radius_m) if radius_m is not None else float("inf")
    for e in entries:
        meta = e.get("metadata", {})
        if isinstance(meta, dict) and meta.get("type") in _NON_LOCATION_TYPES:
            continue
        name = _location_name_from_metadata(meta) if isinstance(meta, dict) else ""
        if not name:
            # 이름이 metadata에 없으면 텍스트에서 추출 (예: '거실의 위치는 x: …')
            name = _location_name_from_text(e.get("text", ""))
        if not name:
            continue
        xy = _entry_xy(e)
        if xy is None:
            continue
        d = math.hypot(x - xy[0], y - xy[1])
        if d <= best_d:
            best_d = d
            best = {"name": name, "position": {"x": xy[0], "y": xy[1]}, "distance_m": round(d, 3)}
    return best


class RAGStatusSkill(BaseSkill):
    """RAG 상태 조회 스킬"""

    name = "rag_status"
    answer_mode = "informational"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    risk_level = "read"
    description = "현재 RAG 인덱스 상태, 벡터 저장소 종류, 문서 수, 최근 오류와 검색/저장 메트릭 요약을 조회합니다."

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if self.node is None or not hasattr(self.node, "_memory"):
            return {"success": False, "message": "메모리 시스템에 접근할 수 없습니다."}
        status = self.node._memory.rag_status()
        store = status.get("vector_store", {})
        count = store.get("count", 0)
        backend = store.get("backend", "unknown")
        return {
            "success": True,
            "message": f"RAG 상태 조회 완료 ({backend}, {count}건)",
            "rag_status": status,
        }


class RAGReindexSkill(BaseSkill):
    """RAG 재색인 스킬"""

    name = "rag_reindex"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    risk_level = "action"
    allow_with_others = False
    description = (
        "현재 저장된 RAG 지식을 다시 임베딩하여 인덱스를 재구성합니다. "
        "임베딩 모델 변경, Qdrant 차원 불일치 복구, 검색 품질 점검 후 재빌드할 때 사용합니다."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if self.node is None or not hasattr(self.node, "_memory"):
            return {"success": False, "message": "메모리 시스템에 접근할 수 없습니다."}
        return self.node._memory.reindex_knowledge()


class RAGAddSkill(BaseSkill):
    """수동 RAG 텍스트 추가 스킬"""

    name = "rag_add"
    risk_level = "action"
    allow_with_others = False
    description = (
        "RAG 지식 베이스에 텍스트 1건을 수동 등록합니다. "
        "파라미터: text(str, 필수), metadata(dict, 선택)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "저장할 지식 또는 관찰 내용"},
            "metadata": {
                "type": "object",
                "description": "선택 메타데이터. 위치는 location_name/x/y 사용",
            },
        },
        "required": ["text"],
        "additionalProperties": False,
    }

    def validate_params(self, params: dict[str, Any]) -> bool:
        return bool(str(params.get("text", "")).strip())

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None or not hasattr(self.node, "_memory"):
            return {"success": False, "message": "메모리 시스템에 접근할 수 없습니다."}
        text = str(params.get("text", "")).strip()
        metadata = params.get("metadata", {})
        if not isinstance(metadata, dict):
            return {"success": False, "message": "metadata는 JSON 객체여야 합니다."}
        # 위치 저장 표준화: 이름/좌표가 metadata든 텍스트든, 한글이든 영어든 어디서 오든
        # location_name/x/y/type=location 으로 정규화한다. 사이트·장소명과 무관하게
        # 시맨틱 맵 동기화·좌표→이름 역조회·랭킹·필터가 일관 동작하도록 하는 기반.
        metadata = _normalize_location_metadata(text, metadata)
        # 좌표 없는 위치 저장 보완: 약한 LLM(예: gemma4:e4b)이 위치 이름만 넣고 좌표를
        # 빠뜨리는 경우가 많다. "(현재) 그 위치를 X로 기억/저장" 의도이므로, 좌표가 전혀
        # 없으면 로봇의 현재 pose를 그 장소의 좌표로 채운다(모델 성능과 무관하게 저장 보장).
        # 이름 출처를 넓게 인식: 표준 키 → 텍스트 문장 → 비표준 키(location_type/category).
        # 약한 LLM은 "현재 위치를 충전대로 기억"을 {location_type:"Charging Station"} 처럼
        # 이름키·좌표 없이 저장하기도 한다 → 그래도 위치로 보고 pose로 좌표를 보완한다.
        loc_name = (
            _location_name_from_metadata(metadata)
            or _location_name_from_text(text)
            or str(metadata.get("location_type") or metadata.get("category") or "").strip()
        )
        if loc_name and ("x" not in metadata or "y" not in metadata):
            try:
                pose = self.get_map_pose()
            except Exception:  # noqa: BLE001
                pose = None
            if pose and pose.get("x") is not None and pose.get("y") is not None:
                metadata = {
                    **metadata,
                    "x": round(float(pose["x"]), 2),
                    "y": round(float(pose["y"]), 2),
                    "location_name": metadata.get("location_name") or loc_name,
                    "type": metadata.get("type") or "location",
                }
                logger.info(
                    "[rag_add] location '%s' saved without coords — filled from current pose (%.2f, %.2f)",
                    loc_name,
                    metadata["x"],
                    metadata["y"],
                )
        # 같은 이름 위치 재저장 시 이전 RAG 항목 제거(중복/좌표 충돌 방지).
        # 시맨틱 맵은 add_object_location이 이름으로 덮어쓰므로 별도 처리 불필요.
        deduped = _dedup_prior_locations(self.node._memory, metadata)
        stored = self.node._memory.add_knowledge(text, metadata)
        semantic_updated = False
        if stored:
            semantic_updated = _sync_location_to_semantic_map(self.node._memory, metadata)
        return {
            "success": stored,
            "message": "지식 1건을 저장했습니다." if stored else "지식 저장에 실패했습니다.",
            "text": text,
            "semantic_updated": semantic_updated,
            "deduped": deduped,
        }


class RAGAddFileSkill(BaseSkill):
    """파일 기반 RAG 적재 스킬"""

    name = "rag_add_file"
    risk_level = "action"
    allow_with_others = False
    description = (
        "로컬 텍스트 파일을 읽어 청크로 분할한 뒤 RAG 지식 베이스에 일괄 적재합니다. "
        "파라미터: file_path(str, 필수), chunk_size(int, 선택), chunk_overlap(int, 선택), metadata(dict, 선택)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "chunk_size": {"type": "integer", "default": 1000},
            "chunk_overlap": {"type": "integer", "default": 100},
            "encoding": {"type": "string", "default": "utf-8"},
            "metadata": {"type": "object"},
        },
        "required": ["file_path"],
        "additionalProperties": False,
    }

    def validate_params(self, params: dict[str, Any]) -> bool:
        return bool(str(params.get("file_path", "")).strip())

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None or not hasattr(self.node, "_memory"):
            return {"success": False, "message": "메모리 시스템에 접근할 수 없습니다."}
        file_path = str(params.get("file_path", "")).strip()
        metadata = params.get("metadata", {})
        if not isinstance(metadata, dict):
            return {"success": False, "message": "metadata는 JSON 객체여야 합니다."}
        result = self.node._memory.add_knowledge_file(
            file_path,
            metadata=metadata,
            chunk_size=int(params.get("chunk_size", 1000)),
            chunk_overlap=int(params.get("chunk_overlap", 100)),
            encoding=str(params.get("encoding", "utf-8")),
        )
        if result.get("success"):
            result["file_name"] = Path(file_path).name
        return result


class RAGDeleteSkill(BaseSkill):
    """RAG 지식 삭제 스킬"""

    name = "rag_delete"
    risk_level = "action"
    allow_with_others = False
    description = (
        "RAG 지식 항목을 삭제합니다. "
        "record_id, source_path, text_exact 중 하나로 삭제 대상을 지정합니다."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "record_id": {"type": "string"},
            "source_path": {"type": "string"},
            "text_exact": {"type": "string"},
        },
        "oneOf": [
            {"required": ["record_id"]},
            {"required": ["source_path"]},
            {"required": ["text_exact"]},
        ],
        "additionalProperties": False,
    }

    def validate_params(self, params: dict[str, Any]) -> bool:
        return any(
            str(params.get(key, "")).strip() for key in ("record_id", "source_path", "text_exact")
        )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None or not hasattr(self.node, "_memory"):
            return {"success": False, "message": "메모리 시스템에 접근할 수 없습니다."}
        return self.node._memory.delete_knowledge(
            record_id=str(params.get("record_id", "")).strip(),
            source_path=str(params.get("source_path", "")).strip(),
            text_exact=str(params.get("text_exact", "")).strip(),
        )


def _query_target_names(memory: Any, query: str) -> set[str]:
    """쿼리가 가리키는 장소명(정규화) 집합. 텍스트 패턴 + 기억된 장소명 중 쿼리에 포함된 것."""
    q_norm = _normalize_location_name(query)
    targets: set[str] = set()
    tn = _location_name_from_text(query)
    if tn:
        targets.add(_normalize_location_name(tn))
    known: set[str] = set()
    if hasattr(memory, "get_all_objects"):
        for obj in memory.get_all_objects():
            meta = obj.get("metadata", {})
            if isinstance(meta, dict) and meta.get("alias_of"):
                continue
            if obj.get("name"):
                known.add(str(obj["name"]))
    store = getattr(memory, "_vector_store", None)
    if store is not None and hasattr(store, "list_entries"):
        try:
            for e in store.list_entries():
                m = e.get("metadata", {})
                if isinstance(m, dict) and m.get("type") in _FWD_SKIP_TYPES:
                    continue
                for nm in _entry_all_names(e):
                    known.add(nm)
        except Exception as exc:  # noqa: BLE001
            # 여기서 조용히 넘어가면 등록된 장소명을 못 알아보고 임베딩 점수에만 의존하게 된다
            # ("데이터는 있는데 못 찾음"의 주 경로). 반드시 로그로 드러낸다.
            logger.warning(
                "[rag_search] 장소명 수집용 RAG 스캔 실패 — 시맨틱 맵만으로 판정합니다: %s", exc
            )
    for nm in known:
        n = _normalize_location_name(nm)
        if len(n) >= 2 and n in q_norm:
            targets.add(n)
    return {t for t in targets if len(t) >= 2}


def _best_location_for_targets(
    memory: Any, targets: set[str]
) -> tuple[str, float, float, str, str | None] | None:
    """대상 장소명들의 좌표를 찾는다. 우선순위: 시맨틱맵 > type=location > navigated_coordinate > 기타.

    반환: (표시이름, x, y, source, qdrant_id) 또는 None.
    """
    if not targets:
        return None
    best: tuple[int, str, float, float, str, str | None] | None = None

    if hasattr(memory, "get_all_objects"):
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
            entries = store.list_entries()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[rag_search] 위치 좌표 조회용 RAG 스캔 실패 — 시맨틱 맵 근거만 사용합니다: %s",
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


class RAGSearchSkill(BaseSkill):
    """RAG 지식 베이스 검색 스킬"""

    name = "rag_search"
    answer_mode = "informational"
    risk_level = "read"
    description = (
        "RAG 지식 베이스에서 자연어 쿼리로 유사 지식 항목을 검색합니다. "
        "파라미터: query(str, 필수), top_k(int, 선택, 기본 3), score_threshold(float, 선택)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "검색할 자연어 질문/키워드"},
            "top_k": {"type": "integer", "default": 3, "minimum": 1},
            "score_threshold": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def validate_params(self, params: dict[str, Any]) -> bool:
        return bool(str(params.get("query", "")).strip())

    def _semantic_location_answer(self, query: str) -> dict[str, Any] | None:
        """정방향 위치질의를 확정 답변한다(RAG 점수/오염과 무관).

        쿼리가 가리키는 장소의 좌표를 **시맨틱 맵 → type=location → navigated_coordinate**
        순으로 찾아 반환한다. 형식적 위치 저장이 없어도 이동 기록(navigated_coordinate)의
        근거가 있으면 그것으로 답한다. 위치 의도(위치/좌표/어디 등)가 있거나 쿼리가 곧
        장소명일 때만 개입해 일반 검색을 가로채지 않는다. 근거가 없으면 None(일반 RAG 폴백).
        """
        mem = getattr(self.node, "_memory", None)
        if mem is None:
            return None
        q_norm = _normalize_location_name(query)
        if not q_norm:
            return None
        targets = _query_target_names(mem, query)
        if not targets:
            return None
        has_intent = bool(
            re.search(r"위치|좌표|자리|어디|어딨|where|location|coordinate", query, re.IGNORECASE)
        )
        if not (has_intent or q_norm in targets):
            return None
        found = _best_location_for_targets(mem, targets)
        if not found:
            return None
        name, x, y, source, entry_id = found
        msg = f"'{name}'의 위치는 x: {x:.2f}, y: {y:.2f} 입니다."
        logger.info(
            "[rag_search] 정방향 위치조회 '%s' → (%.2f, %.2f) [근거:%s] qdrant_id=%s",
            name,
            x,
            y,
            source,
            entry_id,
        )
        return {
            "success": True,
            "message": msg,
            "results": [
                {
                    "text": msg,
                    "metadata": {
                        "location_name": name,
                        "x": round(x, 2),
                        "y": round(y, 2),
                        "type": "location",
                    },
                    "score": 1.0,
                    "id": entry_id,
                    "source": source,
                }
            ],
            "count": 1,
            "referenced": [
                {
                    "id": entry_id,
                    "source": source,
                    "location_name": name,
                    "x": round(x, 2),
                    "y": round(y, 2),
                }
            ],
        }

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None or not hasattr(self.node, "_memory"):
            return {"success": False, "message": "메모리 시스템에 접근할 수 없습니다."}
        # 정방향 위치질의 우선 처리: 등록된 장소면 시맨틱 맵 좌표를 확정 답변.
        query_early = str(params.get("query", "")).strip()
        loc = self._semantic_location_answer(query_early)
        if loc is not None:
            return loc
        status = self.node._memory.rag_status()
        if not status.get("enabled"):
            return {"success": False, "message": "RAG가 비활성화 상태입니다."}

        query = str(params.get("query", "")).strip()
        top_k = int(params.get("top_k", 3))
        score_threshold = params.get("score_threshold", None)
        if score_threshold is not None:
            score_threshold = float(score_threshold)

        # 운영/메타 항목은 사용자 지식 검색 결과에서 제외한다(사실 항목을 밀어내는 것 방지).
        # - skill_episode: 스킬 실행 기록 (예: 이전 rag_search/identify_location 기록)
        # - skill_lesson: 자가학습 교훈. 프롬프트 주입용 메타지식이지 위치/사실 답변이 아님
        # - navigated_coordinate / blocked_coordinate: navigate_to가 매 이동마다 남기는
        #   좌표 로그. "이동 성공 좌표: …(충전대 …)" 형태라 큐레이팅된 type=location 사실을
        #   밀어낸다.
        results = self.node._memory.search_knowledge(
            query,
            top_k=top_k,
            score_threshold=score_threshold,
            filter_metadata={
                "type_exclude": [
                    "skill_episode",
                    "skill_lesson",
                    "navigated_coordinate",
                    "blocked_coordinate",
                ]
            },
        )
        if not results:
            logger.info("[rag_search] query=%r → 검색 결과 없음 (참조 Qdrant 항목 0건)", query)
            return {
                "success": True,
                "message": "검색 결과가 없습니다.",
                "results": [],
                "count": 0,
                "referenced": [],
            }
        # 어떤 Qdrant 항목을 참조했는지 로그로 남긴다(추가 요구사항: 참조 데이터 identify).
        referenced = [
            {
                "id": r.get("id"),
                "score": round(r.get("score", 0.0), 4),
                "type": (r.get("metadata", {}) or {}).get("type"),
                "text": _sanitize_knowledge_text(r.get("text", ""))[:80],
            }
            for r in results
        ]
        logger.info(
            "[rag_search] query=%r → 참조 %d건: %s",
            query,
            len(referenced),
            "; ".join(
                f"id={x['id']} score={x['score']} type={x['type']} '{x['text']}'"
                for x in referenced
            ),
        )
        summary = _summarize_top_result(results[0])
        message = f"지식 {len(results)}건을 검색했습니다."
        if summary:
            message = f"{message} 상위 결과: {summary[:300]}"
        return {
            "success": True,
            "message": message,
            "results": [
                {
                    "text": _sanitize_knowledge_text(r.get("text", "")),
                    "score": round(r.get("score", 0.0), 4),
                    "metadata": r.get("metadata", {}),
                    "timestamp": r.get("timestamp", ""),
                    "id": r.get("id"),
                }
                for r in results
            ],
            "count": len(results),
            "referenced": referenced,
        }


class RAGListSkill(BaseSkill):
    """RAG 지식 베이스 목록 조회 스킬"""

    name = "rag_list"
    answer_mode = "informational"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    risk_level = "read"
    description = (
        "RAG 지식 베이스에 저장된 항목 목록을 조회합니다. "
        "저장된 텍스트·메타데이터·타임스탬프를 전체 확인할 때 사용합니다. "
        "파라미터 없음."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if self.node is None or not hasattr(self.node, "_memory"):
            return {"success": False, "message": "메모리 시스템에 접근할 수 없습니다."}
        try:
            entries = self.node._memory._vector_store.list_entries()
        except Exception as e:
            return {"success": False, "message": f"목록 조회 실패: {e}", "count": 0}
        return {
            "success": True,
            "message": f"지식 {len(entries)}건이 등록되어 있습니다.",
            "entries": [
                {
                    "id": e.get("id", ""),
                    "text": e.get("text", "")[:200],  # 너무 긴 경우 잘라서 반환
                    "metadata": e.get("metadata", {}),
                    "timestamp": e.get("timestamp", ""),
                }
                for e in entries
            ],
            "count": len(entries),
        }


class LogObservationSkill(BaseSkill):
    """현재 위치·감지 객체·장면을 RAG 지식 베이스에 관찰 기록으로 저장합니다."""

    name = "log_observation"
    input_schema = {
        "type": "object",
        "properties": {"observation": {"type": "string"}, "metadata": {"type": "object"}},
        "additionalProperties": True,
    }
    risk_level = "action"
    allow_with_others = False
    description = (
        "현재 로봇의 위치(map 기준), ONNX 감지 객체, 장면 설명을 RAG 지식 베이스에 저장합니다. "
        "파라미터: scene_description(str, 선택 — 없으면 감지 객체 목록만 기록), "
        "detection_topic(str, 선택, 기본 /object_detector_node/detections)."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None or not hasattr(self.node, "_memory"):
            return {"success": False, "message": "메모리 시스템에 접근할 수 없습니다."}

        memory = self.node._memory
        if not memory.rag_status().get("enabled"):
            return {"success": False, "message": "RAG가 비활성화 상태입니다."}

        pose = self.get_map_pose()
        x = round(pose["x"], 2) if pose else None
        y = round(pose["y"], 2) if pose else None
        pose_frame = pose["frame"] if pose else None

        det_topic = str(params.get("detection_topic", "/object_detector_node/detections"))
        try:
            from vision_msgs.msg import Detection2DArray

            det_arr = self.wait_for_message(Detection2DArray, det_topic, timeout_sec=1.0)
        except ImportError:
            det_arr = None

        objects: list = []
        if det_arr:
            for det in det_arr.detections:
                if det.results:
                    objects.append(det.results[0].hypothesis.class_id)

        loc_str = f"위치 x={x} y={y} ({pose_frame})" if x is not None else "위치 미상"
        obj_str = ", ".join(objects) if objects else "없음"
        scene_desc = str(params.get("scene_description", "")).strip()

        text = f"[{loc_str}] 감지 객체: {obj_str}."
        if scene_desc:
            text += f" 장면: {scene_desc}"

        metadata: dict[str, Any] = {
            "type": "observation",
            "x": x,
            "y": y,
            "frame_id": pose_frame,
            "objects": objects,
        }

        # 중복 방지 (Deduplication): 동일 반경 0.5m 내에 유사도 0.95 이상의 관찰 기록이 존재하면 스킵
        try:
            current_pose = (x, y) if x is not None and y is not None else None
            duplicate_hits = memory.search_knowledge(
                text, top_k=1, score_threshold=0.95, current_pose=current_pose, radius_m=0.5
            )
            if duplicate_hits:
                logger.info(
                    "Similar observation knowledge already exists within the same radius, skipping save. (Similar knowledge: %s...)",
                    duplicate_hits[0]["text"][:60],
                )
                return {
                    "success": True,
                    "message": "유사 관찰 기록이 이미 존재하여 저장을 스킵했습니다.",
                    "text": text,
                    "skipped": True,
                }
        except Exception as search_err:
            logger.debug(
                "Error occurred during duplicate check search (not skipped): %s", search_err
            )

        stored = memory.add_knowledge(text, metadata)
        return {
            "success": stored,
            "message": "관찰 기록 저장 완료." if stored else "저장 실패 (임베더 미설정 또는 오류).",
            "text": text,
        }


class IdentifyLocationSkill(BaseSkill):
    """좌표→이름 역방향 조회 스킬 (현재/지정 좌표가 어떤 기억된 장소인지 판별)."""

    name = "identify_location"
    answer_mode = "informational"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    risk_level = "read"
    description = (
        "로봇의 현재 위치(또는 지정한 x, y 좌표)가 기억된 어떤 장소에 해당하는지 "
        "이름으로 알려줍니다. 저장된 명명 위치(거실·키친·충전대 등) 중 가장 가까운 "
        "곳을 반환합니다. '여기가 어디야', '현재 위치 이름이 뭐야', '이 좌표는 어디야'류 "
        "질문에 사용하세요. 주변 사물을 보고 추정하는 analyze_scene과 달리, 기억된 좌표와 "
        "비교해 정확한 장소명을 판별합니다. "
        "파라미터: x(float, 선택), y(float, 선택 — 미지정 시 현재 위치), "
        "radius_m(float, 선택, 기본 2.0)."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if err := self.require_node():
            return err
        if not hasattr(self.node, "_memory"):
            return {"success": False, "message": "메모리 시스템에 접근할 수 없습니다."}
        memory = self.node._memory

        x = params.get("x")
        y = params.get("y")
        if x is None or y is None:
            pose = self.get_map_pose()
            if not pose:
                return {"success": False, "message": "현재 위치를 파악할 수 없습니다."}
            x, y = pose["x"], pose["y"]
        try:
            x = float(x)
            y = float(y)
        except (TypeError, ValueError):
            return {"success": False, "message": "x, y 좌표가 올바르지 않습니다."}

        try:
            radius_m = float(params.get("radius_m", 2.0))
        except (TypeError, ValueError):
            radius_m = 2.0

        # 1순위: 시맨틱 맵(구조화 위치) 조회
        nearest = None
        if hasattr(memory, "get_nearest_object"):
            nearest = memory.get_nearest_object(x, y, radius_m=radius_m)
        # 2순위(폴백): 시맨틱 맵에 없으면 RAG 위치 항목 스캔.
        # (저장 시 metadata에 x/y가 누락돼 시맨틱 맵 동기화가 안 된 경우 대비)
        if not nearest:
            nearest = _nearest_named_location_from_rag(memory, x, y, radius_m)
        if not nearest:
            return {
                "success": True,
                "message": (
                    f"현재 위치(x: {x:.2f}, y: {y:.2f}) 반경 {radius_m:.1f}m 안에 "
                    "기억된 장소가 없습니다."
                ),
                "location_name": None,
                "position": {"x": round(x, 2), "y": round(y, 2)},
            }

        name = str(nearest.get("name", ""))
        dist = float(nearest.get("distance_m", 0.0))
        return {
            "success": True,
            "message": f"현재 위치는 '{name}'입니다 (거리 {dist:.2f}m).",
            "location_name": name,
            "distance_m": round(dist, 2),
            "position": {"x": round(x, 2), "y": round(y, 2)},
        }


class GetLocationSkill(BaseSkill):
    """이름→좌표 정방향 조회 스킬 (기억된 장소의 좌표를 반환)."""

    name = "get_location"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {"location_name": {"type": "string", "description": "기억된 장소명"}},
        "required": ["location_name"],
        "additionalProperties": False,
    }
    risk_level = "read"
    description = (
        "기억된 장소(거실·키친·충전대 등)의 이름으로 좌표(x, y)를 조회합니다. "
        "'~의 좌표 알려줘', '~가 어디 있어?', '~ 위치가 어디야?'류 질문에 사용하세요. "
        "시맨틱 맵과 RAG 지식 베이스에서 기억된 위치를 찾아 좌표를 반환합니다. "
        "파라미터: location_name(str, 필수)."
    )

    def validate_params(self, params: dict[str, Any]) -> bool:
        return bool(str(params.get("location_name", "")).strip())

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if err := self.require_node():
            return err
        if not hasattr(self.node, "_memory"):
            return {"success": False, "message": "메모리 시스템에 접근할 수 없습니다."}
        memory = self.node._memory

        location_name = str(params.get("location_name", "")).strip()
        if not location_name:
            return {"success": False, "message": "location_name 파라미터가 필요합니다."}

        # 조회는 `_best_location_for_targets` 하나로 통일한다(2026-08-14 병합 정리).
        # 원래 이 스킬은 시맨틱 맵 → RAG 스캔을 자체 구현했는데, `rag_search` 의 정방향
        # 단락(`_semantic_location_answer`)이 같은 일을 하는 별도 구현을 갖고 있었다.
        # 공용 함수로 합치면서 두 가지가 정리된다:
        #   - 중복 구현 제거 (동작·응답 문구가 갈리지 않는다)
        #   - `navigated_coordinate` 를 **최후 근거로 인정**. 자체 구현은 이를
        #     `_NON_LOCATION_TYPES` 로 제외해서, 장소가 `type=location` 없이 이동 기록만
        #     남은 경우(실측: 키친) 좌표를 못 찾았다.
        # 우선순위: 시맨틱 맵 > type=location > navigated_coordinate > 기타.
        found = _best_location_for_targets(memory, {_normalize_location_name(location_name)})
        if found:
            name, x, y, source, entry_id = found
            logger.info(
                "[get_location] '%s' → (%.2f, %.2f) [근거:%s] qdrant_id=%s",
                name,
                x,
                y,
                source,
                entry_id,
            )
            return {
                "success": True,
                "message": f"'{name}'의 위치는 x: {x:.2f}, y: {y:.2f} 입니다.",
                "location_name": name,
                "position": {"x": round(x, 2), "y": round(y, 2)},
                # `source` 는 origin/main 이 정한 안정 식별자를 유지한다(소비자/테스트 계약).
                # 공용 함수의 한글 라벨은 사람이 읽는 상세 근거이므로 별도 필드로 노출한다.
                "source": "semantic_map" if source == "시맨틱 맵" else "rag",
                "source_detail": source,
                "id": entry_id,
            }

        return {
            "success": True,
            "message": f"'{location_name}'의 기억된 좌표를 찾지 못했습니다.",
            "location_name": location_name,
            "position": None,
        }
