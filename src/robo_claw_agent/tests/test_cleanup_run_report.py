"""``scripts/cleanup_run_report.py`` 집계 규칙 회귀 테스트."""

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "cleanup_run_report.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("cleanup_run_report", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write(path, records):
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )
    return str(path)


def _run(run_id, status, success, phase_ms=None, elapsed_ms=1000.0):
    return {
        "event": "cleanup_run",
        "run_id": run_id,
        "results": [{"place": "안방", "status": status, "partial_observation": False}],
        "outcomes": {status: 1},
        "success": success,
        "phase_ms": phase_ms or {"navigate": 1000.0},
        "elapsed_ms": elapsed_ms,
    }


def _handoff(run_id, peer, blocked=False):
    return {
        "event": "cleanup_handoff",
        "run_id": run_id,
        "peer": peer,
        "blocked": blocked,
        "candidates": [],
    }


def test_summary_computes_table_2_metrics(tmp_path):
    script = _load_script()
    path = _write(
        tmp_path / "runs.jsonl",
        [
            _handoff("r1", "cloid2"),
            _run("r1", "delegated_demo", True, {"navigate": 1000.0, "delegate": 3000.0}, 8000.0),
            _handoff("r2", "cloid1"),
            _run("r2", "delegated_demo", True, {"navigate": 3000.0, "delegate": 1000.0}, 12000.0),
            _handoff("r3", "cloid2", blocked=True),
            _run("r3", "unhandled", False, {"navigate": 500.0}, 2000.0),
        ],
    )

    summary = script.summarize(script.load_records(path), expect_peer="cloid2")

    assert summary["repeat_count"] == 3
    assert summary["success_rate"] == pytest.approx(200 / 3)
    # 위임 준비 2건 중 1건만 기대 동료(cloid2)가 선택됐다.
    assert summary["allocation_accuracy"] == pytest.approx(50.0)
    # 준비된 2건(r1, r2) 모두 실제 위임 성공 → 100%
    assert summary["delegation_success_rate"] == pytest.approx(100.0)
    assert summary["handoff_prepared"] == 2
    assert summary["handoff_blocked"] == 1
    assert summary["mean_elapsed_ms"] == pytest.approx(22000 / 3)
    assert summary["mean_phase_ms"]["navigate"] == pytest.approx(1500.0)
    assert summary["mean_phase_ms"]["delegate"] == pytest.approx(2000.0)
    assert summary["outcomes"] == {"delegated_demo": 2, "unhandled": 1}


def test_allocation_accuracy_is_absent_without_expected_peer(tmp_path):
    script = _load_script()
    path = _write(tmp_path / "runs.jsonl", [_handoff("r1", "cloid2"), _run("r1", "handled", True)])

    summary = script.summarize(script.load_records(path))

    assert summary["allocation_accuracy"] is None


def test_markdown_renders_one_table_2_row(tmp_path, capsys):
    script = _load_script()
    path = _write(
        tmp_path / "runs.jsonl",
        [_handoff("r1", "cloid2"), _run("r1", "delegated_demo", True, elapsed_ms=65000.0)],
    )

    assert script.main([path, "--expect-peer", "cloid2", "--markdown"]) == 0

    out = capsys.readouterr().out
    assert "| 정상 협업 | 1 | 100.0% | 100.0% | 100.0% | 65.0s | 해당 없음 |" in out


def test_missing_path_returns_error(capsys, tmp_path):
    script = _load_script()

    assert script.main([str(tmp_path / "nope.jsonl")]) == 2
    assert "찾을 수 없습니다" in capsys.readouterr().err


def test_delegation_success_uses_delegate_records_over_run_status(tmp_path):
    """장소 status 는 delegated_demo 지만 실제 위임 레코드가 실패면 위임 성공률은 0% 다."""
    script = _load_script()
    path = _write(
        tmp_path / "runs.jsonl",
        [
            _handoff("r1", "cloid2"),
            {
                "event": "cleanup_delegate",
                "run_id": "r1",
                "peer": "cloid2",
                "success": False,
                "message": "정리 시연 모션 실행이 거부되거나 실패했습니다.",
                "duration_ms": 3000.0,
            },
            _run("r1", "delegated_demo", True),
        ],
    )

    summary = script.summarize(script.load_records(path))

    assert summary["delegation_source"] == "cleanup_delegate"
    assert summary["delegation_attempts"] == 1
    assert summary["delegation_success_rate"] == pytest.approx(0.0)


def test_delegation_falls_back_to_run_status_without_delegate_records(tmp_path):
    script = _load_script()
    path = _write(
        tmp_path / "runs.jsonl", [_handoff("r1", "cloid2"), _run("r1", "delegated_demo", True)]
    )

    summary = script.summarize(script.load_records(path))

    assert summary["delegation_source"] == "run_status"
    assert summary["delegation_attempts"] == 0
    assert summary["delegation_success_rate"] == pytest.approx(100.0)


def test_delegate_phase_prefers_delegate_record_duration(tmp_path):
    """실행 레코드의 delegate 구간 값이 어긋나도 위임 레코드 소요 시간을 쓴다."""
    script = _load_script()
    path = _write(
        tmp_path / "runs.jsonl",
        [
            _handoff("r1", "cloid2"),
            {
                "event": "cleanup_delegate",
                "run_id": "r1",
                "peer": "cloid2",
                "success": True,
                "duration_ms": 90597.5,
            },
            _run("r1", "delegated_demo", True, {"navigate": 1000.0, "delegate": 90.6}, 10000.0),
        ],
    )

    summary = script.summarize(script.load_records(path))

    assert summary["mean_phase_ms"]["delegate"] == pytest.approx(90597.5)
