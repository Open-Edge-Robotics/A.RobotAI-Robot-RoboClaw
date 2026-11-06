"""레거시 지식 베이스 항목을 SQLite 장소 저장소로 승격·이관한다.

장소·좌표·이동 기록은 구조화 저장소(SQLite)로 분리됐고, 이동 요청의 좌표 해석은
SQLite만 조회한다(`find_location_entries`). 그 이전에 JSON 지식 베이스에 쌓인
장소 항목과, 전환 직후 `rag_add`가 metadata 없이 저장한 "장소처럼 보이는 텍스트"는
조회 경로에서 보이지 않아 이동이 실패했다.

이 모듈은 그런 항목을 골라 구조화 저장소로 옮긴다. 판정 규칙은 실행 시점의 저장
경로와 같다(`_location_name_from_text` + `_normalize_location_metadata`).
"""

from typing import Any

from .semantic import (
    _is_self_location_name,
    _location_name_from_metadata,
    _location_name_from_text,
    _normalize_location_metadata,
    _normalize_location_name,
)

# 좌표가 함께 있어야 장소/이동 근거로 쓸 수 있는 타입.
_NAMED_TYPES = frozenset({"location", "navigated_coordinate"})
_MAX_NAME_LEN = 40

# 이름 어절 단위로 배제할 단어. `Former 현재 위치` 처럼 로봇 라벨·지시어가 붙은
# 항목이 장소로 등록되는 것을 막는다(전체 이름 비교로는 걸러지지 않음).
_NAME_REJECT_WORDS = frozenset({"현재", "위치", "좌표", "로봇", "robot", "self", "last", "pose"})


def _is_rejected_name(name: str, excluded_keys: set[str]) -> bool:
    """장소로 보지 않을 이름(로봇 라벨·지시어 포함)인지 판정한다."""
    if not name or _is_self_location_name(name):
        return True
    words = [w for w in name.replace("(", " ").replace(")", " ").split() if w]
    for word in words:
        key = _normalize_location_name(word)
        if key in excluded_keys or key in _NAME_REJECT_WORDS:
            return True
    return False


def _coords(metadata: dict[str, Any]) -> tuple[float, float] | None:
    try:
        x, y = float(metadata["x"]), float(metadata["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if x != x or y != y:  # NaN
        return None
    return x, y


def _legacy_name(metadata: dict[str, Any], text: str) -> str:
    """레거시 항목의 장소명을 뽑는다(metadata 이름키 → target_name → 텍스트)."""
    name = _location_name_from_metadata(metadata)
    if name:
        return name
    target = str(metadata.get("target_name") or "").strip()
    if target and not _is_self_location_name(target):
        return target
    return _location_name_from_text(text)


def iter_place_candidates(
    entries: list[dict[str, Any]], *, exclude_names: Any = ()
) -> list[dict[str, Any]]:
    """지식 베이스 항목 목록에서 장소로 승격·이관할 후보를 만든다.

    - 이미 `type=location`/`navigated_coordinate`이고 좌표가 있는 항목은 그대로 이관한다.
    - 타입이 없거나 다른 항목이라도 텍스트에서 장소명과 좌표를 모두 얻으면 `location`으로
      승격한다(전환 직후 metadata 없이 저장된 실기 데이터).
    - 이름이나 좌표를 얻지 못한 항목은 건너뛴다. 이름이 없으면 이동 목적지로 쓸 수 없다.
    - `exclude_names`(로봇 라벨 등)에 해당하는 이름은 제외한다.
    같은 이름이 여러 번 나오면 나중 항목(최신)이 이긴다.
    """
    excluded = {_normalize_location_name(name) for name in exclude_names}
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or "")
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        metadata = dict(metadata)
        xy = _coords(metadata)
        name = _legacy_name(metadata, text)
        legacy_type = str(metadata.get("type") or "")

        if legacy_type in _NAMED_TYPES and xy is not None and name:
            candidate_type = legacy_type
        elif name and len(name) <= _MAX_NAME_LEN:
            # 좌표가 metadata 에 없으면 텍스트에서 찾는다(실행 경로와 동일한 정규화 규칙).
            normalized = _normalize_location_metadata(text, metadata)
            if normalized.get("type") != "location":
                continue
            metadata = normalized
            xy = _coords(metadata)
            name = str(metadata["location_name"])
            candidate_type = "location"
            metadata.setdefault("source", "repromote")
        else:
            continue

        if xy is None:
            continue
        metadata.update(
            type=candidate_type,
            location_name=name,
            x=xy[0],
            y=xy[1],
        )
        metadata.setdefault("aliases", [])
        key = _normalize_location_name(name)
        if _is_rejected_name(name, excluded):
            continue
        if key not in best:
            order.append(key)
        best[key] = {
            "text": text or f"{name}의 위치는 x: {xy[0]}, y: {xy[1]} 입니다.",
            "metadata": metadata,
        }
    return [best[key] for key in order]


def repromote_places(
    store: Any,
    entries: list[dict[str, Any]],
    *,
    dry_run: bool = False,
    overwrite: bool = True,
    exclude_names: Any = (),
) -> dict[str, int]:
    """후보 항목을 구조화 저장소에 기록하고 결과 통계를 돌려준다.

    ``overwrite=False`` 면 이미 저장된 이름은 건너뛰고 ``kept``로 센다. 레거시 데이터가
    현재 값보다 오래된 경우가 많으므로, 실기 이관에서는 기본적으로 이 모드를 쓴다.
    """
    candidates = iter_place_candidates(entries, exclude_names=exclude_names)
    stats = {"planned": len(candidates), "inserted": 0, "kept": 0, "conflicts": 0, "invalid": 0}
    if dry_run:
        return stats
    for candidate in candidates:
        name = str(candidate["metadata"]["location_name"])
        if not overwrite and store.location(name) is not None:
            stats["kept"] += 1
            continue
        try:
            stored = store.add(candidate["text"], candidate["metadata"])
        except ValueError:
            stats["conflicts"] += 1
            continue
        except Exception:  # noqa: BLE001 - 한 건 실패가 전체 이관을 막지 않는다
            stats["invalid"] += 1
            continue
        stats["inserted"] += 1 if stored else 0
    return stats
