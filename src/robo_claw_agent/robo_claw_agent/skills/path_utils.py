"""파일 경로 안전성 검증 유틸.

스킬의 파일 접근을 허용된 디렉토리(``/tmp``, agent_workspace,
butler_script_dir) 내부로 제한한다. 여러 파일 관련 스킬에서 공유한다.
"""

import os
from typing import Any


def _is_safe_path(file_path: str, node: Any) -> bool:
    """파일 경로가 안전한(허용된) 디렉토리 내에 있는지 검사합니다.

    symlink를 통한 경로 탈출을 방지하기 위해 realpath를 기준으로 비교합니다.
    """
    if not file_path:
        return False

    real_path = os.path.realpath(file_path)
    allowed_dirs = [os.path.realpath("/tmp")]

    if node and hasattr(node, "_agent_workspace_dir") and node._agent_workspace_dir:
        allowed_dirs.append(os.path.realpath(node._agent_workspace_dir))

    if node:
        try:
            if node.has_parameter("butler_script_dir"):
                butler_dir = (
                    node.get_parameter("butler_script_dir")
                    .get_parameter_value()
                    .string_value
                )
                if butler_dir:
                    allowed_dirs.append(os.path.realpath(butler_dir))
        except Exception:
            pass

    for allowed_dir in allowed_dirs:
        try:
            if os.path.commonpath([allowed_dir, real_path]) == allowed_dir:
                return True
        except ValueError:
            continue

    return False


def _resolve_and_validate_path(file_path: str, node: Any) -> tuple[str, bool]:
    """경로를 절대 경로로 해석하고 안전성 검증을 수행합니다.

    상대 경로인 경우 agent_workspace_dir을 기준으로 해석합니다.
    """
    if not file_path:
        return "", False

    if not os.path.isabs(file_path):
        workspace = "/ros2_ws/agent_workspace"
        if node and hasattr(node, "_agent_workspace_dir") and node._agent_workspace_dir:
            workspace = node._agent_workspace_dir
        file_path = os.path.join(workspace, file_path)

    abs_path = os.path.abspath(file_path)
    return abs_path, _is_safe_path(abs_path, node)
