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


def test_read_env_file_parses_dotenv(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "LAYA_API_KEY=abc123\n"
        "export SYSTEM1_ENDPOINT='http://laya:8000'\n"
        'QUOTED="with space"\n'
        "TRAILING=value  # note\n"
        "NOEQUALS\n",
        encoding="utf-8",
    )

    values = _load_script().read_env_file(env_file)

    assert values["LAYA_API_KEY"] == "abc123"
    assert values["SYSTEM1_ENDPOINT"] == "http://laya:8000"
    assert values["QUOTED"] == "with space"
    assert values["TRAILING"] == "value"
    assert "NOEQUALS" not in values
    assert _load_script().read_env_file(tmp_path / "missing.env") == {}


def test_resolve_setting_precedence():
    resolve = _load_script().resolve_setting
    names = ("SYSTEM1_API_KEY", "LAYA_API_KEY")

    assert resolve("arg", names, {"SYSTEM1_API_KEY": "env"}, {"LAYA_API_KEY": "file"}) == (
        "arg",
        "argument",
    )
    assert resolve("", names, {"LAYA_API_KEY": "env2"}, {"SYSTEM1_API_KEY": "file"}) == (
        "env2",
        "env LAYA_API_KEY",
    )
    assert resolve("", names, {}, {"LAYA_API_KEY": "file"}) == ("file", "env-file LAYA_API_KEY")
    assert resolve("", names, {}, {}) == ("", "")


def test_api_key_from_env_file_is_sent_but_not_printed(tmp_path, monkeypatch, capsys):
    """--env-file 의 LAYA_API_KEY 가 Bearer 헤더로 전달되고, 키 값은 출력되지 않는다."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            seen.append(self.headers.get("Authorization"))
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps(
                {"answers": {"intent": {"choice": "question", "confidence": 0.99}}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for name in ("SYSTEM1_API_KEY", "LAYA_API_KEY", "SYSTEM1_ENDPOINT", "LAYA_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("LAYA_API_KEY=s3cret-key\n", encoding="utf-8")
    try:
        rc = _load_script().main(
            [
                "--router",
                "laya",
                "--endpoint",
                f"http://127.0.0.1:{server.server_port}",
                "--env-file",
                str(env_file),
            ]
        )
    finally:
        server.shutdown()

    assert rc == 0
    assert seen and all(h == "Bearer s3cret-key" for h in seen)
    out = capsys.readouterr().out
    assert "auth: on (env-file LAYA_API_KEY)" in out
    assert "s3cret-key" not in out
