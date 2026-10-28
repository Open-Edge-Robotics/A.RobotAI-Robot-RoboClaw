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


def _run(run_id, status, success, phase_ms=None, elapsed_ms=1000.0, place="안방"):
    return {
        "event": "cleanup_run",
        "run_id": run_id,
        "target_location": place,
        "places": [place],
        "results": [{"place": place, "status": status, "partial_observation": False}],
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


def test_delegation_rate_is_not_applicable_when_no_handoff_prepared(tmp_path):
    """기능 제약처럼 위임 시도가 없으면 위임 성공률은 0%가 아니라 n/a 다."""
    script = _load_script()
    path = _write(
        tmp_path / "runs.jsonl",
        [_handoff("r1", "", blocked=True), _run("r1", "unhandled", False)],
    )

    summary = script.summarize(script.load_records(path), expect_peer="cloid2")

    assert summary["delegation_success_rate"] is None
    assert summary["handoff_prepared"] == 0
    assert summary["handoff_blocked"] == 1


def _run_at(run_id, place, status="delegated_demo", success=True):
    record = _run(run_id, status, success)
    record["target_location"] = place
    record["places"] = [place]
    record["results"] = [{"place": place, "status": status, "partial_observation": False}]
    return record


def test_split_by_place_writes_one_file_per_place(tmp_path):
    """4장소 순환 로그를 장소별 파일로 나누고, 같은 위임/실행 기록을 함께 옮긴다."""
    script = _load_script()
    path = _write(
        tmp_path / "runs.jsonl",
        [
            _handoff("r1", "cloid2"),
            _run_at("r1", "안방"),
            _handoff("r2", "cloid2"),
            _run_at("r2", "거실"),
            _run_at("r3", "안방"),
        ],
    )
    out = tmp_path / "by_place"

    written = script.split_by_place(script.load_records(path), str(out))

    assert sorted(written) == ["거실", "안방"]
    bedroom = script.load_records(written["안방"])
    assert [record["event"] for record in bedroom] == [
        "cleanup_handoff",
        "cleanup_run",
        "cleanup_run",
    ]
    assert len(script.load_records(written["거실"])) == 2
    assert script.summarize(bedroom)["repeat_count"] == 2


def test_split_by_place_falls_back_to_record_place_without_run(tmp_path):
    """실행 기록 없이 남은 위임 결정도 버리지 않고 장소 파일로 분류한다."""
    script = _load_script()
    path = _write(
        tmp_path / "runs.jsonl",
        [
            {
                "event": "cleanup_handoff",
                "run_id": "r9",
                "place": "부엌",
                "peer": "",
                "blocked": True,
            }
        ],
    )

    written = script.split_by_place(script.load_records(path), str(tmp_path / "by_place"))

    assert list(written) == ["부엌"]
    assert len(script.load_records(written["부엌"])) == 1


def test_render_place_table_lists_each_place():
    script = _load_script()
    path_records = [
        _run_at("r1", "안방"),
        _run_at("r2", "신발장", status="needs_clarification", success=False),
    ]
    rows = [(record["target_location"], script.summarize([record])) for record in path_records]

    table = script.render_place_table(rows)

    assert "| 장소 | 반복 횟수 |" in table
    assert "| 안방 | 1 | 100.0% |" in table
    assert "| 신발장 | 1 | 0.0% |" in table


def _delegate(run_id, success, message="", duration_ms=1000.0):
    return {
        "event": "cleanup_delegate",
        "run_id": run_id,
        "peer": "cloid2",
        "success": success,
        "message": message,
        "duration_ms": duration_ms,
    }


def test_navigation_timeout_is_excluded_from_repeat_count(tmp_path):
    """동료가 요청을 받고 주행 타임아웃으로 실패한 회차는 횟수에서 빼고 예외로 남긴다."""
    script = _load_script()
    path = _write(
        tmp_path / "runs.jsonl",
        [
            _handoff("ok", "cloid2"),
            _delegate("ok", True, "정리 시연 모션 실행을 요청했습니다."),
            _run("ok", "delegated_demo", True),
            _handoff("nav", "cloid2"),
            _delegate(
                "nav",
                False,
                "실패: '거실'에 도착하지 못해 정리 시연을 중단했습니다. "
                "visit_place → 거실 결과를 받지 못했습니다 (타임아웃).",
            ),
            _run("nav", "execution_failed", False),
        ],
    )

    summary = script.summarize(script.load_records(path))

    assert summary["repeat_count"] == 2
    assert summary["success_rate"] == pytest.approx(50.0)
    assert summary["exception_count"] == 1
    assert summary["effective_repeat_count"] == 1
    assert summary["effective_success_rate"] == pytest.approx(100.0)
    assert summary["effective_delegation_success_rate"] == pytest.approx(100.0)
    assert summary["exceptions"][0]["run_id"] == "nav"
    assert summary["exceptions"][0]["category"] == "navigation"
    assert summary["counted_failure_count"] == 0


def test_network_outage_is_excluded_from_repeat_count(tmp_path):
    script = _load_script()
    path = _write(
        tmp_path / "runs.jsonl",
        [
            _handoff("r1", "cloid2"),
            _delegate("r1", False, "실패: 동료 연결 실패 (No route to host)"),
            _run("r1", "execution_failed", False),
        ],
    )

    summary = script.summarize(script.load_records(path))

    assert summary["exceptions"][0]["category"] == "network"
    assert summary["effective_repeat_count"] == 0
    assert summary["counted_failure_count"] == 0


def test_communication_failure_stays_in_denominator(tmp_path):
    """전달 자체가 실패한 회차(gRPC 연결 오류)는 예외가 아니라 실패로 집계한다."""
    script = _load_script()
    path = _write(
        tmp_path / "runs.jsonl",
        [
            _handoff("r1", "cloid2"),
            _delegate("r1", False, "실패: gRPC connection error. Stream removed (Socket closed)"),
            _run("r1", "execution_failed", False),
        ],
    )

    summary = script.summarize(script.load_records(path))

    assert summary["exception_count"] == 0
    assert summary["effective_repeat_count"] == 1
    assert summary["effective_success_rate"] == pytest.approx(0.0)
    assert summary["counted_failure_count"] == 1
    assert summary["failures"][0]["category"] == "communication"


def test_failure_without_delegate_record_is_not_an_exception(tmp_path):
    """위임 요청이 나가기 전에 끝난 회차는 통신 실패도 환경 예외도 아니라 실패로 센다."""
    script = _load_script()
    path = _write(
        tmp_path / "runs.jsonl",
        [_handoff("r1", "cloid2"), _run("r1", "execution_failed", False)],
    )

    summary = script.summarize(script.load_records(path))

    assert summary["exception_count"] == 0
    assert summary["effective_success_rate"] == pytest.approx(0.0)
    assert summary["failures"][0]["category"] == "other"


def test_exclude_exceptions_flag_renders_effective_count_and_exception_log(tmp_path, capsys):
    script = _load_script()
    path = _write(
        tmp_path / "runs.jsonl",
        [
            _handoff("ok", "cloid2"),
            _delegate("ok", True, "정리 시연 모션 실행을 요청했습니다."),
            _run("ok", "delegated_demo", True),
            _handoff("nav", "cloid2"),
            _delegate(
                "nav",
                False,
                "실패: '거실'에 도착하지 못해 정리 시연을 중단했습니다. visit_place 타임아웃.",
            ),
            _run("nav", "execution_failed", False, place="거실"),
        ],
    )

    assert script.main([path, "--expect-peer", "cloid2", "--markdown"]) == 0
    plain = capsys.readouterr().out
    assert "| 정상 협업 | 2 | 50.0% |" in plain
    assert "예외 기록" not in plain

    assert script.main([path, "--expect-peer", "cloid2", "--markdown", "--exclude-exceptions"]) == 0
    adjusted = capsys.readouterr().out
    assert "| 정상 협업 | 1 | 100.0% | 100.0% | 100.0% |" in adjusted
    assert "원래 반복 2회 중 예외 1회 제외 → 유효 반복 1회" in adjusted
    assert "[네비게이션 타임아웃] 거실 `nav`" in adjusted
    assert "실패로 집계한 회차: 0건" in adjusted


def test_run_status_unreachable_is_navigation_exception(tmp_path):
    """위임 레코드가 없어도 장소 status 가 unreachable 이면 주행 예외로 뺀다."""
    script = _load_script()
    path = _write(
        tmp_path / "runs.jsonl",
        [
            _handoff("ok", "cloid2"),
            _delegate("ok", True, "정리 시연 모션 실행을 요청했습니다."),
            _run("ok", "delegated_demo", True),
            _run("nav", "unreachable", False, place="신발장"),
        ],
    )

    summary = script.summarize(script.load_records(path))

    assert summary["exception_count"] == 1
    assert summary["exceptions"][0]["category"] == "navigation"
    assert summary["effective_repeat_count"] == 1
    assert summary["effective_success_rate"] == pytest.approx(100.0)
    assert summary["counted_failure_count"] == 0
