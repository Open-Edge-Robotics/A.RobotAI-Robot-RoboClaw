"""테스트 환경 공통 설정.

ROS2 의존 모듈(rclpy, nav_msgs, sensor_msgs, cv_bridge 등)이 설치되지 않은
로컬 개발 환경에서는 해당 테스트를 자동으로 skip한다.
CI에서는 ROS2 환경이 구성되므로 모든 테스트가 실행된다.
"""

import importlib
import importlib.util
import os
import sys
import types

import pytest

# ROS2 핵심 의존 모듈 목록
_ROS2_MODULES = [
    "rclpy",
    "nav_msgs",
    "sensor_msgs",
    "cv_bridge",
    "action_msgs",
    "geometry_msgs",
    "std_msgs",
    "nav2_msgs",
    "builtin_interfaces",
]


def _is_module_available(name: str) -> bool:
    """모듈 import 가능 여부를 반환한다."""
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


def _check_ros2_available() -> bool:
    """ROS2 핵심 모듈이 모두 설치되어 있는지 확인한다."""
    return all(_is_module_available(m) for m in _ROS2_MODULES)


ROS2_AVAILABLE = _check_ros2_available()


def _install_ros2_stubs() -> None:
    """ROS2 가 설치되지 않은 환경에서 robo_claw_agent 패키지 import 가 가능하도록
    최소한의 stub 모듈을 sys.modules 에 등록한다.

    이 stub 은 robo_claw_agent.agent_node (node.py 가 rclpy 를 import 함) 등의
    패키지 __init__ import 를 우회하기 위한 것으로, 실제 ROS2 기능을 mock 하지는 않는다.
    robo_claw_agent.agent_node.utils (rclpy 의존성 없음) 는 실제 모듈을 로드한다.

    주의: importorskip("rclpy") 를 사용하는 기존 테스트는 여전히 skip 되도록,
    rclpy 자체는 stub 으로 등록하지 않는다. 대신 robo_claw_agent.agent_node 패키지만
    빈 모듈로 등록해 lazy import 가 가능하게 한다.
    """
    if ROS2_AVAILABLE:
        return

    # robo_claw_agent.agent_node 패키지를 빈 모듈로 등록해 __init__.py import 를 우회.
    # __path__ 는 실제 디렉터리로 지정한다. 이렇게 하면 __init__.py(node.py -> rclpy)를
    # 실행하지 않으면서도, rclpy 의존이 없는 하위 모듈(planner, nav_safety, task_planner,
    # utils ...)은 일반적인 import 문으로 로드된다. 모듈마다 spec 을 손으로 만들 필요가
    # 없고, 하위 모듈 간 상대 임포트(planner -> .nav_safety, .utils)도 그대로 동작한다.
    if "robo_claw_agent.agent_node" not in sys.modules:
        pkg = types.ModuleType("robo_claw_agent.agent_node")
        pkg.__path__ = [
            os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "robo_claw_agent", "agent_node")
            )
        ]
        sys.modules["robo_claw_agent.agent_node"] = pkg

    # robo_claw_agent.agent_node.utils 는 rclpy 의존성이 없으므로 실제 파일에서 로드.
    if "robo_claw_agent.agent_node.utils" not in sys.modules:
        utils_path = os.path.join(
            os.path.dirname(__file__), "..", "robo_claw_agent", "agent_node", "utils.py"
        )
        if os.path.exists(utils_path):
            spec = importlib.util.spec_from_file_location(
                "robo_claw_agent.agent_node.utils", utils_path
            )
            utils_mod = importlib.util.module_from_spec(spec)
            sys.modules["robo_claw_agent.agent_node.utils"] = utils_mod
            try:
                spec.loader.exec_module(utils_mod)
            except Exception:
                pass

    # robo_claw_agent.agent_node.task_planner 도 rclpy 의존성이 없으므로 동일하게 로드.
    if "robo_claw_agent.agent_node.task_planner" not in sys.modules:
        task_planner_path = os.path.join(
            os.path.dirname(__file__), "..", "robo_claw_agent", "agent_node", "task_planner.py"
        )
        if os.path.exists(task_planner_path):
            spec = importlib.util.spec_from_file_location(
                "robo_claw_agent.agent_node.task_planner", task_planner_path
            )
            task_planner_mod = importlib.util.module_from_spec(spec)
            sys.modules["robo_claw_agent.agent_node.task_planner"] = task_planner_mod
            try:
                spec.loader.exec_module(task_planner_mod)
            except Exception:
                pass


# 모듈 로드 시 stub 설치
_install_ros2_stubs()

# pytest 마커 등록
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "ros2: ROS2 환경(rclpy, nav_msgs 등)이 필요한 테스트. "
        "ROS2 미설치 시 자동 skip.",
    )
    config.addinivalue_line(
        "markers",
        "qdrant: qdrant-client 패키지가 필요한 테스트.",
    )
    config.addinivalue_line(
        "markers",
        "mcp: mcp 패키지가 필요한 테스트.",
    )


def pytest_collection_modifyitems(config, items):
    """ROS2 의존 테스트를 자동으로 감지하고 미설치 환경에서 skip한다."""
    if ROS2_AVAILABLE:
        return

    skip_ros2 = pytest.mark.skip(reason="ROS2 환경이 필요합니다 (rclpy 등 미설치)")
    skip_qdrant = pytest.mark.skip(reason="qdrant_client 미설치")
    skip_mcp = pytest.mark.skip(reason="mcp 패키지 미설치")

    for item in items:
        # @pytest.mark.ros2 마커가 있거나 ROS2 모듈 import가 필요한 테스트
        if "ros2" in item.keywords:
            item.add_marker(skip_ros2)

        # qdrant 마커
        if "qdrant" in item.keywords and not _is_module_available("qdrant_client"):
            item.add_marker(skip_qdrant)

        # mcp 마커
        if "mcp" in item.keywords and not _is_module_available("mcp"):
            item.add_marker(skip_mcp)
