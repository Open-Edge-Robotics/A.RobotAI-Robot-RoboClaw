"""카메라 출처(그리퍼/베이스) 판별 공용 헬퍼.

``detect.py`` 의 ``_is_gripper_camera`` 와 ``core.py`` 의
``_is_gripper_camera_params`` 가 같은 기준을 각자 복제해 갖고 있었다. core.py 가
detect.py 를 역으로 임포트할 수 없어 의도적으로 복제한 것이었으므로, 두 모듈이
함께 참조할 수 있는 ROS 비의존 리프 모듈로 분리한다.
"""

from __future__ import annotations

from typing import Any

_GRIPPER_CAMERA_SOURCES = frozenset(
    {
        "gripper",
        "wrist",
        "hand",
        "end_effector",
        "eoa",
    }
)


def is_gripper_camera(params: dict[str, Any]) -> bool:
    """params 의 camera/camera_source 가 그리퍼(손목) 카메라를 가리키는지 판별한다."""
    camera_source = str(params.get("camera") or params.get("camera_source") or "").strip().lower()
    return camera_source in _GRIPPER_CAMERA_SOURCES
