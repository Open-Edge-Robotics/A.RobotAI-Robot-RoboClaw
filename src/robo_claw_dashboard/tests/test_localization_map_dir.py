"""지도 디렉터리 스캔 테스트 — 맵 목록 소스(설정/디렉터리)의 안전한 판독."""

import pytest
from robo_claw_dashboard.adapters.param_defaults import discover_map_names

pytestmark = pytest.mark.component


def test_directory_children_become_sorted_map_names(tmp_path):
    for name in ("konjiam_L_107", "FACTORYMAP"):
        (tmp_path / name).mkdir()
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".hidden").mkdir()
    assert discover_map_names(tmp_path) == ["FACTORYMAP", "konjiam_L_107"]


def test_unsafe_directory_names_are_dropped(tmp_path):
    (tmp_path / "ok_map").mkdir()
    (tmp_path / "with space").mkdir()
    assert discover_map_names(tmp_path) == ["ok_map"]


def test_missing_directory_returns_empty_list(tmp_path):
    assert discover_map_names(tmp_path / "missing") == []
    assert discover_map_names("") == []
