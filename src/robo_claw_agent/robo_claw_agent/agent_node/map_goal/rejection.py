"""Non-actuating validation outcome for map requests rejected before dispatch."""

import time
from typing import Any

from robo_claw_agent.skills.base import SkillResult

from .contract import parse_context_json
from .tracker import get_map_goal_tracker

VALIDATION_RESULT_NAME = "map_goal_validation"


def rejection_result(node: Any, goal_handle: Any, message: str) -> SkillResult | None:
    """Report proven non-execution without closing an earlier request's lease.

    This is a validation report, not an executed navigation skill. A malformed
    context cannot be correlated safely; an existing request ID must retain
    its original state rather than being closed by a rejected duplicate.
    """
    request = getattr(goal_handle, "request", None)
    try:
        context = parse_context_json(getattr(request, "context_json", ""))
    except (TypeError, ValueError):
        return None
    if context is None or get_map_goal_tracker(node).get(context.request_id) is not None:
        return None
    record = {
        "request_id": context.request_id,
        "robot_id": context.robot_id,
        "status": "failed",
        "message": str(message),
        "target": {"x": context.x, "y": context.y, "frame_id": context.frame_id},
        "geometry_id": context.geometry_id,
        "execution_started": False,
        "cancel_confirmed": False,
        "reason": "navigation_not_executed",
        "progress": 0,
        "created_at": context.created_at,
        "updated_at": time.time(),
        "can_submit": True,
    }
    return SkillResult(
        VALIDATION_RESULT_NAME,
        False,
        str(message),
        0.0,
        {"map_goal_rejection": record, "executed": False},
    )
