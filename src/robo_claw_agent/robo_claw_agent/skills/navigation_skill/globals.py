import threading
from typing import Any

from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup

# Nav2 action 클라이언트 캐시(노드별 1회 생성).
NAV_CLIENT: ActionClient | None = None
SPIN_CLIENT: ActionClient | None = None
WP_CLIENT: ActionClient | None = None
CLIENT_NODE: Any = None
CLIENT_GROUP: ReentrantCallbackGroup | None = None

PATROL_ACTIVE = threading.Event()

# 직접 cmd_vel 회전은 Nav2 goal registry를 사용하지 않으므로 별도 취소 경로가 필요하다.
CMD_VEL_ROTATION_LOCK = threading.Lock()
CMD_VEL_ROTATION_CANCEL = threading.Event()
CMD_VEL_ROTATION_PUBLISHER: Any = None
