"""하위 호환 shim. 구현은 ``skills.cleanup.policy`` 로 이동했다."""

from .cleanup.policy import (  # noqa: F401
    _CLOID_INDICATOR_MOTIONS,
    _cleanup_plan_has_complete_placement,
    _cleanup_target_categories,
    _cleanup_target_category,
    _cleanup_target_labels,
    _has_ambiguous_cleanup_indication,
    _has_cleanup_indication,
    _resolve_cleanup_places,
    _resolve_cleanup_plan_destinations,
    _safe_cleanup_disposal_targets,
    _safe_cleanup_storage_targets,
    _select_cleanup_demo_motion,
    _select_cloid_indicator_motion,
)
