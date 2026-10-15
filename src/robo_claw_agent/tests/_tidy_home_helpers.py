from __future__ import annotations

import random

import pytest
import robo_claw_agent.skills.tidy_home_skill as tidy_home_module
from robo_claw_agent.skills.cleanup_policy import (
    _cleanup_target_category,
    _safe_cleanup_storage_targets,
    _select_cleanup_demo_motion,
)
from robo_claw_agent.skills.tidy_home_skill import (
    TidyHomeSkill,
    _cleanup_plan_has_complete_placement,
    _has_ambiguous_cleanup_indication,
    _has_cleanup_indication,
    _resolve_cleanup_places,
    _resolve_cleanup_plan_destinations,
    _safe_cleanup_disposal_targets,
    _select_cloid_indicator_motion,
)

pytestmark = pytest.mark.unit


def _place(name, x, y, **metadata):
    return {
        "name": name,
        "position": {"x": x, "y": y},
        "metadata": metadata,
    }
def _motion(motion_id, name, *, pre_id=None, intent="", description="", mask=""):
    return {
        "motion_id": motion_id,
        "motion_name": name,
        "pre_id": pre_id,
        "intent_id": intent,
        "description": description,
        "mask": mask,
    }


__all__ = [
    "TidyHomeSkill",
    "_cleanup_plan_has_complete_placement",
    "_cleanup_target_category",
    "_has_ambiguous_cleanup_indication",
    "_has_cleanup_indication",
    "_motion",
    "_place",
    "_resolve_cleanup_places",
    "_resolve_cleanup_plan_destinations",
    "_safe_cleanup_disposal_targets",
    "_safe_cleanup_storage_targets",
    "_select_cleanup_demo_motion",
    "_select_cloid_indicator_motion",
    "annotations",
    "pytest",
    "random",
    "tidy_home_module",
]
