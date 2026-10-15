"""ROS 비의존(순수) 모듈 경계 회귀 테스트 (AGENTS.md §3).

도메인 규칙과 순수 헬퍼는 ROS2(rclpy)나 ROS 메시지에 의존하지 않아야 한다. 아래
목록의 모듈이 ROS 의존을 갖게 되면 실패한다. 새 코드를 순수 계층으로 옮기면 목록에
추가한다.

소스를 import 하지 않고 AST 로만 검사하므로 ROS 환경 없이도 실행된다.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PKG_ROOT = Path(__file__).resolve().parents[1] / "robo_claw_agent"

_ROS_TOP_LEVEL = {
    "rclpy",
    "rcl_interfaces",
    "rospy",
    "sensor_msgs",
    "geometry_msgs",
    "nav_msgs",
    "std_msgs",
    "std_srvs",
    "vision_msgs",
    "action_msgs",
    "builtin_interfaces",
    "control_msgs",
    "trajectory_msgs",
    "tf2_ros",
    "tf2_geometry_msgs",
    "nav2_simple_commander",
    "lifecycle_msgs",
    "unique_identifier_msgs",
}

# ROS 비의존(순수) 계층으로 유지해야 하는 모듈(패키지 루트 기준 상대 경로).
_PURE_MODULES = [
    "geo_utils.py",
    "ros_future.py",
    "answer.py",
    "types.py",
    "observability.py",
    "tracing.py",
    "skills/_param_utils.py",
    "skills/_battery.py",
    "skills/path_utils.py",
    "skills/limits.py",
    "skills/cleanup/policy.py",
    "skills/cleanup/protocol.py",
    "vector_store/local.py",
    "vector_store/base.py",
    "agent_node/task_planner.py",
    "agent_node/nav_safety.py",
]


def _ros_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] in _ROS_TOP_LEVEL]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] in _ROS_TOP_LEVEL:
                found.append(module)
    return sorted(set(found))


@pytest.mark.parametrize("relative", _PURE_MODULES)
def test_pure_module_does_not_import_ros(relative):
    path = _PKG_ROOT / relative
    assert path.exists(), f"순수 모듈 목록에 존재하지 않는 파일: {relative}"
    assert _ros_imports(path) == [], f"{relative} 가 ROS 에 의존한다"


def test_pure_module_list_has_no_stale_entries():
    missing = [relative for relative in _PURE_MODULES if not (_PKG_ROOT / relative).exists()]
    assert missing == [], f"순수 모듈 목록의 stale 항목: {missing}"
