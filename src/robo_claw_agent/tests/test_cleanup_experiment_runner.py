"""``validation/cleanup-experiment/run_cleanup_experiment.py`` 회귀 테스트.

로봇·gRPC 없이 조건 정의 해석, 판정 규칙, run log 상관, 집계, 리포트 생성을 검증한다.
마지막 테스트 2건은 run log 를 스스로 기록하는 가짜 sender 로 전체 실행 경로를 돌린다.
"""

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "validation" / "cleanup-experiment" / "run_cleanup_experiment.py"
_CONDITIONS = _ROOT / "validation" / "cleanup-experiment" / "conditions"


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_cleanup_experiment", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _append(path, record):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _result_json(run_id, skill_name="tidy_home"):
    return json.dumps(
        {
            "skill_results": [
                {
                    "skill_name": skill_name,
                    "code": 0,
                    "message": "started",
                    "duration_sec": 0.4,
                    "result_data": {"status": "started", "run_id": run_id},
                }
            ]
        },
        ensure_ascii=False,
    )


def test_load_conditions_covers_three_conditions():
    runner = _load_runner()

    conditions = runner.load_conditions(_CONDITIONS)

    assert set(conditions) == {"normal", "constrained", "recovery", "cycle4"}
    assert [phase["key"] for phase in conditions["recovery"]["phases"]] == [
        "fail_injected",
        "recovered",
    ]
    # 표 2 행으로 쓰는 단계는 복구 단계뿐이다.
    assert [phase["row"] for phase in conditions["recovery"]["phases"]] == [False, True]
    assert conditions["normal"]["phases"][0]["repeat"] == 10


def test_load_conditions_cycle_condition_orders_four_places():
    runner = _load_runner()

    condition = runner.load_conditions(_CONDITIONS)["cycle4"]

    assert condition["cycle"] is True
    assert condition["cycles"] == 10
    assert [phase["prompt"] for phase in condition["phases"]] == [
        "안방 정리해줘",
        "거실 정리해줘",
        "부엌 정리해줘",
        "신발 정리해줘",
    ]
    assert all(phase["row"] for phase in condition["phases"])
    schedule = runner.build_schedule(condition, condition["phases"])
    assert len(schedule) == 40
    assert [phase["key"] for _cycle, phase in schedule[:5]] == [
        "bedroom",
        "living_room",
        "kitchen",
        "shoe_rack",
        "bedroom",
    ]


def test_build_schedule_falls_back_to_per_phase_repeat():
    runner = _load_runner()
    condition = {"condition": "normal", "phases": []}
    phases = [
        {"key": "fail_injected", "repeat": 3},
        {"key": "recovered", "repeat": 2},
    ]

    assert runner.build_schedule(condition, phases) == [
        (None, phases[0]),
        (None, phases[0]),
        (None, phases[0]),
        (None, phases[1]),
        (None, phases[1]),
    ]
    assert len(runner.build_schedule(condition, phases, repeat_override=1)) == 2


def test_build_place_rows_groups_attempts_by_place():
    runner = _load_runner()
    condition = {
        "phases": [
            {"key": "bedroom", "place": "안방", "row": True, "expect": {"peer": "cloid2"}},
            {"key": "shoe_rack", "place": "신발장", "row": True, "expect": {}},
        ]
    }
    attempts = [
        {
            "phase_key": "bedroom",
            "ok": True,
            "run_success": True,
            "peer": "cloid2",
            "delegate_successes": [True],
            "run_elapsed_ms": 60000.0,
            "phase_ms": {},
            "run_present": True,
            "replan_attempts": 0,
            "replan_tracked": False,
        },
        {
            "phase_key": "shoe_rack",
            "ok": False,
            "run_success": False,
            "peer": "",
            "delegate_successes": [],
            "run_elapsed_ms": 40000.0,
            "phase_ms": {},
            "run_present": True,
            "replan_attempts": 0,
            "replan_tracked": False,
        },
    ]

    rows = runner.build_place_rows(attempts, condition, expect_peer="cloid2")

    assert [label for label, _row in rows] == ["안방", "신발장"]
    assert rows[0][1]["verdict_pass"] == 1
    assert rows[0][1]["delegation_success_rate"] == pytest.approx(100.0)
    # 위임 기록이 없는 장소는 위임 성공률을 계산하지 않는다.
    assert rows[1][1]["delegation_success_rate"] is None


def test_render_report_adds_place_table_for_cycle_condition():
    runner = _load_runner()
    condition = {
        "condition": "cycle4",
        "label": "4장소 순환 협업",
        "phases": [
            {"key": "bedroom", "place": "안방", "row": True, "expect": {}},
            {"key": "living_room", "place": "거실", "row": True, "expect": {}},
        ],
    }
    attempts = [
        {
            "phase_key": "bedroom",
            "ok": True,
            "reasons": [],
            "message": "정리",
            "latency_ms": 1000.0,
            "run_id": "r1",
            "statuses": ["delegated_demo"],
            "peer": "cloid2",
            "run_present": True,
            "run_success": True,
            "run_elapsed_ms": 60000.0,
            "phase_ms": {},
            "delegate_successes": [True],
            "replan_attempts": 0,
            "replan_tracked": False,
        },
        {
            "phase_key": "living_room",
            "ok": True,
            "reasons": [],
            "message": "정리",
            "latency_ms": 1000.0,
            "run_id": "r2",
            "statuses": ["delegated_demo"],
            "peer": "cloid2",
            "run_present": True,
            "run_success": True,
            "run_elapsed_ms": 80000.0,
            "phase_ms": {},
            "delegate_successes": [True],
            "replan_attempts": 0,
            "replan_tracked": False,
        },
    ]
    row = runner.build_condition_row(attempts, expect_peer="cloid2")
    place_rows = runner.build_place_rows(attempts, condition, expect_peer="cloid2")

    report = runner.render_report(condition, attempts, row, place_rows)

    assert "## 장소별 표" in report
    assert "| 안방 | 1 |" in report
    assert "| 거실 | 1 |" in report


def test_extract_run_id_from_nested_result_json():
    runner = _load_runner()

    assert runner.extract_run_id(_result_json("former0047-20261026T165827-abc123")) == (
        "former0047-20261026T165827-abc123"
    )


@pytest.mark.parametrize("payload", ["", "not json", '{"skill_results": []}', '{"x": 1}'])
def test_extract_run_id_returns_empty_for_unrelated_payload(payload):
    runner = _load_runner()

    assert runner.extract_run_id(payload) == ""


def test_count_replan_events():
    runner = _load_runner()

    text = (
        "[ERROR] Skill 'tidy_home' failed: x. Collecting telemetry and attempting replanning...\n"
        "[ERROR] Skill 'tidy_home' failed: y. Collecting telemetry and attempting replanning...\n"
    )
    assert runner.count_replan_events(text) == {"attempts": 2, "exhausted": 0}
    assert runner.count_replan_events(
        "[ERROR] Exceeded max replanning rounds. Result so far: z"
    ) == {"attempts": 0, "exhausted": 1}
    assert runner.count_replan_events("no replan here") == {"attempts": 0, "exhausted": 0}


def test_collect_run_evidence_waits_for_run_record(tmp_path):
    runner = _load_runner()
    path = tmp_path / "runs.jsonl"
    _append(path, {"event": "cleanup_handoff", "run_id": "r1", "peer": "cloid2", "blocked": False})
    _append(path, {"event": "cleanup_run", "run_id": "r1", "success": True, "results": []})
    _append(path, {"event": "cleanup_run", "run_id": "other", "success": False, "results": []})

    evidence = runner.collect_run_evidence(str(path), "r1", timeout_sec=0.5, poll_sec=0.01)

    assert evidence["run"]["run_id"] == "r1"
    assert [record["peer"] for record in evidence["handoffs"]] == ["cloid2"]


def test_collect_run_evidence_ignores_records_before_start_index(tmp_path):
    """같은 run log 재실행 시 이전 회차 레코드가 이번 판정에 섞이지 않아야 한다."""
    runner = _load_runner()
    path = tmp_path / "runs.jsonl"
    stale = {"event": "cleanup_run", "run_id": "r1", "success": True, "results": []}
    _append(path, stale)
    fresh = {"event": "cleanup_run", "run_id": "r1", "success": False, "results": []}
    _append(path, fresh)

    evidence = runner.collect_run_evidence(
        str(path), "r1", timeout_sec=0.2, poll_sec=0.01, start_index=1
    )

    assert evidence["run"]["success"] is False


def test_collect_run_evidence_returns_empty_on_timeout_without_run_id(tmp_path):
    runner = _load_runner()

    assert runner.collect_run_evidence(str(tmp_path / "missing.jsonl"), "", 0.1) == {
        "run": None,
        "handoffs": [],
        "delegates": [],
    }


def _evidence(status="delegated_demo", success=True, peer="cloid2", delegate=True):
    return {
        "run": {"event": "cleanup_run", "success": success, "results": [{"status": status}]},
        "handoffs": [{"event": "cleanup_handoff", "peer": peer, "blocked": False}],
        "delegates": [{"event": "cleanup_delegate", "success": delegate}],
    }


def test_evaluate_attempt_accepts_expected_normal_run():
    runner = _load_runner()
    expect = {
        "run_present": True,
        "run_success": True,
        "place_status": ["delegated_demo"],
        "peer": "cloid2",
        "delegate_success": True,
        "expected_keywords": ["정리"],
        "fail_keywords": ["정리할 수 없"],
    }

    reasons = runner.evaluate_attempt(expect, {"message": "정리 작업을 시작했습니다"}, _evidence())

    assert reasons == []


def test_evaluate_attempt_reports_status_peer_and_keyword_mismatch():
    runner = _load_runner()
    expect = {
        "run_present": True,
        "run_success": True,
        "place_status": ["delegated_demo"],
        "peer": "cloid2",
        "fail_keywords": ["정리할 수 없"],
    }

    reasons = runner.evaluate_attempt(
        expect,
        {"message": "Former는 정리할 수 없습니다"},
        _evidence(status="unhandled", success=False, peer=""),
    )

    joined = " | ".join(reasons)
    assert "금지 키워드 포함" in joined
    assert "실행 성공 판정 불일치" in joined
    assert "장소 status 불일치" in joined
    assert "위임 동료 불일치" in joined


def test_evaluate_attempt_rejects_unexpected_handoff_in_constrained_condition():
    runner = _load_runner()

    reasons = runner.evaluate_attempt(
        {"peer_absent": True}, {"message": "정리"}, _evidence(peer="cloid2")
    )

    assert any("차단되지 않았습니다" in reason for reason in reasons)


def test_build_condition_row_aggregates_metrics():
    runner = _load_runner()
    attempts = [
        {
            "ok": True,
            "run_success": True,
            "peer": "cloid2",
            "delegate_successes": [True],
            "run_elapsed_ms": 60000.0,
            "phase_ms": {"navigate": 1000.0},
            "run_present": True,
            "replan_attempts": 0,
            "replan_tracked": True,
        },
        {
            "ok": False,
            "run_success": False,
            "peer": "cloid1",
            "delegate_successes": [False],
            "run_elapsed_ms": 40000.0,
            "phase_ms": {"navigate": 3000.0},
            "run_present": True,
            "replan_attempts": 2,
            "replan_tracked": True,
        },
    ]

    row = runner.build_condition_row(attempts, expect_peer="cloid2")

    assert row["repeat"] == 2
    assert row["run_record_count"] == 2
    assert row["success_rate"] == pytest.approx(50.0)
    assert row["task_success_count"] == 1
    assert row["verdict_pass"] == 1
    assert row["verdict_pass_rate"] == pytest.approx(50.0)
    assert row["allocation_accuracy"] == pytest.approx(50.0)
    assert row["delegation_success_rate"] == pytest.approx(50.0)
    assert row["mean_elapsed_ms"] == pytest.approx(50000.0)
    assert row["mean_phase_ms"]["navigate"] == pytest.approx(2000.0)
    assert row["replan_attempts"] == 1
    assert row["replan_success_rate"] == pytest.approx(0.0)


def test_render_report_contains_table_2_row():
    runner = _load_runner()
    condition = {
        "condition": "normal",
        "label": "정상 협업",
        "target": "1.2.3.4:50052",
        "measured_at": "2026-10-26 17:00:00",
        "phases": [{"key": "normal", "row": True, "expect": {"peer": "cloid2"}}],
    }
    attempts = [
        {
            "phase_key": "normal",
            "ok": True,
            "reasons": [],
            "message": "정리 작업을 시작했습니다",
            "latency_ms": 1500.0,
            "run_id": "r1",
            "statuses": ["delegated_demo"],
            "peer": "cloid2",
            "run_present": True,
            "run_success": True,
            "run_elapsed_ms": 65000.0,
            "phase_ms": {"navigate": 1000.0},
            "delegate_successes": [True],
            "replan_attempts": 0,
            "replan_tracked": False,
        }
    ]
    row = runner.build_condition_row(attempts, expect_peer="cloid2")

    report = runner.render_report(condition, attempts, row)

    assert "| 정상 협업 | 1 | 100.0% | 100.0% | 100.0% | 65.0s | n/a |" in report
    assert "기대 동작 준수: 1/1회" in report
    assert "| 1 | normal |" in report
    assert "PASS" in report


def _fake_sender_writing_log(run_log, *, status, success, peer="cloid2", delegate=True):
    """에이전트 역할을 대신해 run log 를 쓰고 gRPC 응답을 돌려주는 sender."""
    counter = {"n": 0}
    prompts: list[str] = []

    def _send(prompt, timeout_sec):
        counter["n"] += 1
        prompts.append(str(prompt))
        run_id = f"former0047-test-{counter['n']:06d}"
        place = str(prompt).replace(" 정리해줘", "") or "안방"
        _append(
            run_log,
            {
                "event": "cleanup_handoff",
                "run_id": run_id,
                "place": place,
                "peer": peer,
                "blocked": not peer,
            },
        )
        if peer:
            _append(
                run_log,
                {"event": "cleanup_delegate", "run_id": run_id, "peer": peer, "success": delegate},
            )
        _append(
            run_log,
            {
                "event": "cleanup_run",
                "run_id": run_id,
                "target_location": place,
                "success": success,
                "results": [{"place": place, "status": status}],
                "elapsed_ms": 65000.0,
                "phase_ms": {"navigate": 18000.0},
            },
        )
        return {
            "success": True,
            "message": "1개 기억 장소를 대상으로 한 번의 정리 작업을 시작했습니다.",
            "result_json": _result_json(run_id),
            "latency_ms": 1200.0,
        }

    _send.close = lambda: None
    _send.prompts = prompts
    return _send


def test_main_runs_experiment_end_to_end(tmp_path, monkeypatch):
    runner = _load_runner()
    run_log = tmp_path / "runs.jsonl"
    output = tmp_path / "report.md"
    monkeypatch.setattr(
        runner,
        "send_via_robotalk",
        lambda *_args, **_kwargs: _fake_sender_writing_log(
            run_log, status="delegated_demo", success=True, delegate=True
        ),
    )

    code = runner.main(
        [
            "--condition",
            "normal",
            "--host",
            "127.0.0.1",
            "--run-log",
            str(run_log),
            "--expect-peer",
            "cloid2",
            "--repeat",
            "1",
            "--settle-sec",
            "0",
            "--yes",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    report = output.read_text(encoding="utf-8")
    assert "정리 태스크 실험 결과 — 정상 협업" in report
    assert "| 정상 협업 | 1 | 100.0% | 100.0% | 100.0% | 65.0s | n/a |" in report


def test_main_runs_cycle_condition_in_place_order(tmp_path, monkeypatch):
    runner = _load_runner()
    run_log = tmp_path / "runs.jsonl"
    output = tmp_path / "report.md"
    sender = _fake_sender_writing_log(run_log, status="delegated_demo", success=True)
    monkeypatch.setattr(runner, "send_via_robotalk", lambda *_args, **_kwargs: sender)

    code = runner.main(
        [
            "--condition",
            "cycle4",
            "--host",
            "127.0.0.1",
            "--run-log",
            str(run_log),
            "--expect-peer",
            "cloid2",
            "--repeat",
            "2",
            "--settle-sec",
            "0",
            "--yes",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    assert sender.prompts == [
        "안방 정리해줘",
        "거실 정리해줘",
        "부엌 정리해줘",
        "신발 정리해줘",
        "안방 정리해줘",
        "거실 정리해줘",
        "부엌 정리해줘",
        "신발 정리해줘",
    ]
    report = output.read_text(encoding="utf-8")
    assert "정리 태스크 실험 결과 — 4장소 순환 협업" in report
    assert "| 4장소 순환 협업 | 8 | 100.0% | 100.0% | 100.0% | 65.0s | n/a |" in report
    assert "## 장소별 표" in report
    assert "| 안방 | 2 | 100.0% |" in report
    assert "| 신발장 | 2 | 100.0% |" in report


def test_main_returns_failure_when_verdict_fails(tmp_path, monkeypatch):
    runner = _load_runner()
    run_log = tmp_path / "runs.jsonl"
    monkeypatch.setattr(
        runner,
        "send_via_robotalk",
        lambda *_args, **_kwargs: _fake_sender_writing_log(
            run_log, status="unhandled", success=False, peer="", delegate=False
        ),
    )

    code = runner.main(
        [
            "--condition",
            "normal",
            "--host",
            "127.0.0.1",
            "--run-log",
            str(run_log),
            "--repeat",
            "1",
            "--settle-sec",
            "0",
            "--yes",
        ]
    )

    assert code == 1


def test_apply_peer_override_replaces_expected_peer_in_row_phases():
    """배포마다 동료 등록명이 다르므로 --expect-peer 로 덮어쓸 수 있어야 한다."""
    runner = _load_runner()
    condition = {
        "condition": "normal",
        "phases": [
            {"key": "fail_injected", "row": False, "expect": {"run_success": False}},
            {"key": "recovered", "row": True, "expect": {"peer": "cloid2"}},
        ],
    }

    runner.apply_peer_override(condition, "Cloid 2호")

    assert condition["phases"][1]["expect"]["peer"] == "Cloid 2호"
    assert "peer" not in condition["phases"][0]["expect"]


def test_apply_peer_override_is_noop_without_peer():
    runner = _load_runner()
    condition = {"phases": [{"key": "main", "row": True, "expect": {"peer": "cloid2"}}]}

    runner.apply_peer_override(condition, "")

    assert condition["phases"][0]["expect"]["peer"] == "cloid2"


def test_send_via_robotalk_passes_chat_token(monkeypatch):
    """에이전트에 GRPC_PEER_TOKEN이 설정된 경우 채팅 인증 토큰을 전달해야 한다."""
    import sys
    import types

    runner = _load_runner()
    captured = {}

    class _FakeTalk:
        def __init__(self, host, port, sender_id="robo_talk", token=""):
            captured.update(host=host, port=port, sender_id=sender_id, token=token)

        def wait_ready(self, _timeout_sec):
            return True

        def send(self, prompt, timeout_sec):
            return True, "ok", "{}"

        def close(self):
            pass

    fake_module = types.ModuleType("robo_talk")
    fake_module.RoboTalk = _FakeTalk
    monkeypatch.setitem(sys.modules, "robo_talk", fake_module)

    send = runner.send_via_robotalk("127.0.0.1", 50052, "tester", "secret-token")

    assert captured == {
        "host": "127.0.0.1",
        "port": 50052,
        "sender_id": "tester",
        "token": "secret-token",
    }
    assert send("안방 정리해줘", 5.0)["success"] is True
    send.close()
