import threading
from typing import Any

from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup

NAV_CLIENT: ActionClient | None = None
SPIN_CLIENT: ActionClient | None = None
WP_CLIENT: ActionClient | None = None
CLIENT_NODE: Any = None
CLIENT_GROUP: ReentrantCallbackGroup | None = None

ACTIVE_GOAL_LOCK = threading.Lock()
ACTIVE_GOAL_HANDLE: Any = None
ACTIVE_GOAL_KIND: str = ""

PATROL_ACTIVE = threading.Event()

# 직접 cmd_vel 회전은 Nav2 goal registry를 사용하지 않으므로 별도 취소 경로가 필요하다.
CMD_VEL_ROTATION_LOCK = threading.Lock()
CMD_VEL_ROTATION_CANCEL = threading.Event()
CMD_VEL_ROTATION_PUBLISHER: Any = None
