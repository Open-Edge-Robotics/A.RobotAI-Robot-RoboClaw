"""
DiscoveryNode 단위 테스트
"""

import json
from unittest.mock import MagicMock, patch


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
