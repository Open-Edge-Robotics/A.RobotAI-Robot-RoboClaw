"""로그 패널 JS 회귀 테스트 — logs.js 순수 함수를 node 로 실행해 검증한다.

화면 표시/파일 저장에 쓰는 포맷 규칙(레벨 표기, 시각, 파일명, 쿼리 생성)이
서버 응답 형식과 어긋나지 않도록 고정한다. node 가 없으면 건너뛴다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.component

ASSETS = Path(__file__).resolve().parents[1] / "web" / "assets"
LOGS_JS = ASSETS / "logs.js"

NODE_SCRIPT = """
const logs = require(process.argv[1]);
const entry = {
  time: "2023-11-14T22:13:20.125Z",
  stamp_sec: 1700000000.125,
  level: "warn",
  node: "/robo_claw_agent_node",
  message: "patrol failed",
};
console.log(
  JSON.stringify({
    clock: logs.formatClock(1700000000125),
    line: logs.formatLine(entry),
    warnClass: logs.levelClass("warn"),
    unknownClass: logs.levelClass("verbose"),
    fileName: logs.exportFileName(new Date(2026, 9, 23, 12, 5, 9)),
    query: logs.buildLogQuery({ level: "warn", query: " patrol x ", limit: 200 }),
    emptyQuery: logs.buildLogQuery({ level: "", query: "", limit: null }),
  })
);
"""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 실행 파일이 없어 JS 로그 패널 테스트를 건너뜁니다")
    return node


@pytest.fixture(scope="module")
def data() -> dict:
    result = subprocess.run(
        [_node(), "-e", NODE_SCRIPT, "--", str(LOGS_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_clock_has_milliseconds(data):
    assert len(data["clock"].split(":")) == 3
    assert "." in data["clock"]
    assert len(data["clock"].split(".")[-1]) == 3


def test_line_includes_level_node_message(data):
    line = data["line"]
    assert "WARN" in line.upper()
    assert "/robo_claw_agent_node" in line
    assert "patrol failed" in line


def test_level_class_maps_known_levels_only(data):
    assert data["warnClass"] == "rc-log-warn"
    assert data["unknownClass"] == "rc-log-info"


def test_export_file_name_is_sortable(data):
    assert data["fileName"] == "robo_claw_log_20261023_120509.log"


def test_query_skips_empty_filters(data):
    assert data["query"] == "?level=warn&q=patrol%20x&limit=200"
    assert data["emptyQuery"] == ""
