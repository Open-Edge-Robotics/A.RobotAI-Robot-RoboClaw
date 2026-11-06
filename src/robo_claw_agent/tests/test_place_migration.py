"""레거시 KB 항목 → SQLite 장소 이관 테스트.

배경: 장소·좌표가 SQLite로 분리되기 전 데이터는 JSON 지식 베이스에만 남았고,
조회 경로(`find_location_entries`)가 SQLite만 보므로 이동 요청에서 보이지 않았다.
또 SQLite 전환 직후 `rag_add`가 metadata 없이 저장한 "장소처럼 보이는 텍스트"도
같은 이유로 유실됐다. 이 이관 도구가 두 경우를 모두 살린다.
"""

import pytest
from robo_claw_agent.memory_manager.place_migration import (
    iter_place_candidates,
    repromote_places,
)
from robo_claw_agent.memory_manager.structured import StructuredMemoryStore

pytestmark = pytest.mark.unit

# 실기 former/cloid 로그에서 확인된 실제 저장 텍스트
REAL_ENTRIES = [
    {"text": "현재 위치(x: 6.37, y: -1.99)는 안방입니다.", "metadata": {}},
    {"text": "안방의 좌표는 x: 6.46, y: -1.9 입니다.", "metadata": {}},
    {"text": "데스크: x=6.43, y=-3.71", "metadata": {}},
    {"text": "현재 좌표(x=-0.71, y=-0.01)는 신발장입니다.", "metadata": {}},
    {"text": "사용자는 매주 화요일에 분리수거를 요청한다.", "metadata": {}},
    {"text": "현재 위치: x=1.0, y=2.0", "metadata": {}},
]


def _store(tmp_path):
    return StructuredMemoryStore(tmp_path / "places.structured.db")


def test_iter_place_candidates_promotes_metadata_less_place_texts():
    candidates = {c["metadata"]["location_name"]: c for c in iter_place_candidates(REAL_ENTRIES)}

    assert set(candidates) == {"안방", "데스크", "신발장"}
    assert candidates["데스크"]["metadata"]["type"] == "location"
    assert candidates["데스크"]["metadata"]["x"] == 6.43
    assert candidates["데스크"]["metadata"]["y"] == -3.71
    assert candidates["데스크"]["metadata"]["source"] == "repromote"
    # 같은 이름이 여러 번 나오면 마지막 항목만 남긴다.
    assert candidates["안방"]["metadata"]["x"] == 6.46


def test_iter_place_candidates_keeps_legacy_typed_records():
    entries = [
        {
            "text": "거실의 위치는 x: 1.94, y: 0.06 입니다.",
            "metadata": {
                "type": "location",
                "location_name": "거실",
                "x": 1.94,
                "y": 0.06,
                "source": "rag_add",
            },
        },
        {
            "text": "이동 성공 좌표: x=4.00, y=1.00",
            "metadata": {"type": "navigated_coordinate", "target_name": "주방", "x": 4.0, "y": 1.0},
        },
        {
            "text": "이동 성공 좌표: x=9.00, y=9.00",
            "metadata": {"type": "navigated_coordinate", "x": 9.0, "y": 9.0},
        },
    ]

    candidates = {
        (c["metadata"]["type"], c["metadata"].get("location_name"))
        for c in iter_place_candidates(entries)
    }

    assert ("location", "거실") in candidates
    assert ("navigated_coordinate", "주방") in candidates
    # 이름이 없는 좌표 기록은 장소로 만들지 않는다.
    assert not any(name is None for _kind, name in candidates)


def test_repromote_places_writes_to_structured_store(tmp_path):
    store = _store(tmp_path)

    stats = repromote_places(store, REAL_ENTRIES)

    assert stats["inserted"] == 3
    assert store.location("안방")["position"] == {"x": 6.46, "y": -1.9}
    assert store.location("데스크")["position"] == {"x": 6.43, "y": -3.71}
    assert store.location("신발장")["position"] == {"x": -0.71, "y": -0.01}


def test_repromote_places_dry_run_does_not_write(tmp_path):
    store = _store(tmp_path)

    stats = repromote_places(store, REAL_ENTRIES, dry_run=True)

    assert stats["inserted"] == 0
    assert stats["planned"] == 3
    assert store.count() == 0


def test_repromote_places_reports_conflicts_without_stopping(tmp_path):
    store = _store(tmp_path)
    store.add("기존 장소", {"type": "location", "location_name": "데스크", "x": 0.0, "y": 0.0})
    entries = [
        {"text": "창고: x=1.0, y=2.0", "metadata": {"location_name": "창고"}},
        # 다른 장소(데스크)의 별칭과 충돌 → 건너뛰고 계속 진행
        {
            "text": "보관실: x=3.0, y=4.0",
            "metadata": {"location_name": "보관실", "aliases": ["데스크"]},
        },
    ]

    stats = repromote_places(store, entries)

    assert stats["inserted"] == 1
    assert stats["conflicts"] == 1
    assert store.location("창고") is not None


def test_repromote_places_keeps_existing_names_when_overwrite_disabled(tmp_path):
    """레거시 값이 현재 값보다 오래된 경우가 많으므로 기존 이름은 유지한다."""
    store = _store(tmp_path)
    store.add("현재 안방", {"type": "location", "location_name": "안방", "x": 6.46, "y": -1.9})
    entries = [{"text": "안방의 좌표는 x: 6.45, y: -2.1 입니다.", "metadata": {}}]

    stats = repromote_places(store, entries, overwrite=False)

    assert stats["kept"] == 1
    assert stats["inserted"] == 0
    assert store.location("안방")["position"] == {"x": 6.46, "y": -1.9}


def test_repromote_places_excludes_robot_labels(tmp_path):
    """`Former`, `Former 현재 위치` 같은 로봇 라벨·지시어는 장소가 아니다."""
    store = _store(tmp_path)
    entries = [
        {
            "text": "Former 위치는 x: 6.27, y: -2.8 입니다.",
            "metadata": {"type": "location", "location_name": "Former", "x": 6.27, "y": -2.8},
        },
        {
            "text": "Former 현재 위치: x: 3.79, y: 1.83",
            "metadata": {
                "type": "location",
                "location_name": "Former 현재 위치",
                "x": 3.79,
                "y": 1.83,
            },
        },
        {"text": "거실: x=2.93, y=-0.38", "metadata": {}},
    ]

    stats = repromote_places(store, entries, exclude_names={"former"})

    assert stats["inserted"] == 1
    assert store.location("Former") is None
    assert store.location("Former 현재 위치") is None
    assert store.location("거실") is not None


def test_newest_entry_wins_for_same_name(tmp_path):
    store = _store(tmp_path)
    entries = [
        {"text": "안방: x=6.45, y=-2.1", "metadata": {}, "timestamp": "2026-10-29T17:26:01"},
        {"text": "안방: x=6.46, y=-1.9", "metadata": {}, "timestamp": "2026-11-06T15:57:22"},
    ]

    repromote_places(store, entries)

    assert store.location("안방")["position"] == {"x": 6.46, "y": -1.9}
