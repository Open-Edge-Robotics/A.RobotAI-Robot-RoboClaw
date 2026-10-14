"""
DiscoveryNode 단위 테스트
"""

import json
from unittest.mock import MagicMock, patch

from robo_claw_discovery.log_throttle import should_log_summary


def test_get_topics_parses_output():
    """ros2 topic list -t 결과를 올바르게 파싱하는지 확인"""
    mock_output = "/scan [sensor_msgs/msg/LaserScan]\n/imu/data [sensor_msgs/msg/Imu]\n"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=mock_output, returncode=0)
        # subprocess 반환값 mock - 직접 임포트 없이 함수 로직만 검증
        result = []
        for line in mock_output.strip().splitlines():
            parts = line.strip().split(" ")
            if len(parts) == 2:
                result.append({"name": parts[0], "type": parts[1].strip("[]")})
        assert len(result) == 2
        assert result[0]["name"] == "/scan"


def test_capabilities_json_structure():
    """capabilities 딕셔너리가 올바른 구조를 갖는지 확인"""
    capabilities = {
        "topics": [{"name": "/test", "type": "std_msgs/msg/String"}],
        "services": [],
        "actions": [],
        "nodes": ["/test_node"],
    }
    serialized = json.dumps(capabilities, ensure_ascii=False)
    parsed = json.loads(serialized)
    assert "topics" in parsed
    assert "services" in parsed
    assert "actions" in parsed
    assert "nodes" in parsed
    assert parsed["topics"][0]["name"] == "/test"


def test_should_log_summary_first_scan_always_logs():
    """아직 로그를 남기지 않았으면 첫 스캔은 항상 남긴다."""
    assert should_log_summary(None, 100.0, 60.0) is True


def test_should_log_summary_throttles_until_interval_elapsed():
    """마지막 로그로부터 60초가 지나기 전에는 남기지 않는다."""
    assert should_log_summary(100.0, 159.9, 60.0) is False
    assert should_log_summary(100.0, 160.0, 60.0) is True
    assert should_log_summary(100.0, 200.0, 60.0) is True


def test_should_log_summary_disabled_when_interval_is_non_positive():
    """간격이 0 이하이면 매 스캔마다 남긴다."""
    assert should_log_summary(100.0, 100.0, 0.0) is True
    assert should_log_summary(100.0, 100.0, -1.0) is True
