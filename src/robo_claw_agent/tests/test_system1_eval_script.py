"""``scripts/system1_eval.py`` 와 시드 데이터셋 회귀 테스트.

RuleRouter 기준선은 오실행(wrong) 0건이어야 한다. 케이스를 추가하다가
규칙과 어긋나는 기대값을 넣으면 여기서 드러난다.
"""

import importlib.util
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "system1_eval.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("system1_eval", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_rule_baseline_has_no_false_act(tmp_path):
    out = tmp_path / "eval.json"

    assert _load_script().main(["--router", "rule", "--json-out", str(out)]) == 0

    summary = json.loads(out.read_text(encoding="utf-8"))["results"][0]["summary"]
    assert summary["router"] == "rule"
    assert summary["total"] >= 30
    assert summary["wrong"] == 0
    assert summary["error"] == 0


def test_laya_unreachable_is_reported_as_error_not_crash(tmp_path):
    out = tmp_path / "eval.json"

    rc = _load_script().main(
        [
            "--router",
            "laya",
            "--endpoint",
            "http://127.0.0.1:9",
            "--timeout-ms",
            "200",
            "--json-out",
            str(out),
        ]
    )

    assert rc == 0
    summary = json.loads(out.read_text(encoding="utf-8"))["results"][0]["summary"]
    assert summary["wrong"] == 0
    assert summary["error"] > 0
