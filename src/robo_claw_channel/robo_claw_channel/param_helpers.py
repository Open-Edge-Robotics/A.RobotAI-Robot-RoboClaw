"""ROS2 노드 파라미터 타입별 접근 헬퍼.

``node.get_parameter(name).get_parameter_value().<type>_value`` 연쇄를 짧은
호출로 대체해 채널 노드(channel_node/client_node)의 파라미터 읽기 보일러플레이트를 통합한다.

파라미터는 이미 선언되어 있다고 가정한다(미선언 시 rclpy 가 예외를 발생시킨다).
"""

from typing import Any


def param_string(node: Any, name: str) -> str:
    """문자열 파라미터 읽기."""
    return node.get_parameter(name).get_parameter_value().string_value


def param_int(node: Any, name: str) -> int:
    """정수 파라미터 읽기."""
    return node.get_parameter(name).get_parameter_value().integer_value


def param_float(node: Any, name: str) -> float:
    """실수(double) 파라미터 읽기."""
    return node.get_parameter(name).get_parameter_value().double_value


def param_bool(node: Any, name: str) -> bool:
    """bool 파라미터 읽기."""
    return node.get_parameter(name).get_parameter_value().bool_value


def param_string_array(node: Any, name: str) -> list[str]:
    """문자열 배열 파라미터 읽기."""
    return list(node.get_parameter(name).get_parameter_value().string_array_value)
