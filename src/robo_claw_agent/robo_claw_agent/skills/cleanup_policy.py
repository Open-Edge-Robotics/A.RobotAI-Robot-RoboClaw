"""순회 정리 작업에 필요한 ROS 비의존 정책 함수."""

from __future__ import annotations

import copy
import math
import random
import re
from typing import Any

_CLEANUP_OBJECT_KEYWORDS = (
    "trash",
    "garbage",
    "waste",
    "litter",
    "wrapper",
    "cigarette butt",
    "쓰레기",
    "포장지",
    "담배꽁초",
)
_CLEANUP_SCENE_KEYWORDS = (
    "쓰레기",
    "포장지",
    "담배꽁초",
    "trash",
    "garbage",
    "waste",
    "litter",
    "wrapper",
    "cigarette butt",
)
_AMBIGUOUS_CLEANUP_KEYWORDS = (
    "cup",
    "bottle",
    "can",
    "bag",
    "clothing",
    "clothes",
    "shoe",
    "book",
    "toy",
    "package",
    "phone",
    "wallet",
    "medicine",
    "battery",
    "chemical",
    "glass",
    "needle",
    "spill",
    "컵",
    "병",
    "캔",
    "가방",
    "옷",
    "신발",
    "책",
    "장난감",
    "택배",
    "휴대폰",
    "지갑",
    "약",
    "배터리",
    "화학",
    "유리",
    "바늘",
    "쏟아",
    "흘린",
)
_NEGATIVE_CLEANUP_CUES = (
    "정리할 대상이 없",
    "정리할 대상이 보이지",
    "정리 대상이 없",
    "정리 대상이 보이지",
    "치울 것이 없",
    "쓰레기가 없",
    "어질러진 물건이 없",
    "no trash",
    "no mess",
    "nothing to clean",
)

# CLOi motion IDs are intentionally pinned to the inspected catalog and exact names.
# The pool represents activity/status gestures, not verified object manipulation.
_CLOID_INDICATOR_MOTIONS = {
    22: "scan",
    128: "task ready pose",
}

_SAFE_DISPOSAL_NAMES = frozenset(
    {
        "trash bin",
        "waste bin",
        "rubbish bin",
        "recycling bin",
        "쓰레기통",
        "휴지통",
        "분리수거함",
    }
)
_CLEANUP_PICK_SKILLS = frozenset(
    {
        "adaptive_pick_object",
        "vla_pick_gripper_object",
        "vla_pick_front_object",
        "pick_front_object",
        "pick_from_right_side_zone",
        "grasp",
    }
)
_ROOM_ALIASES: dict[str, frozenset[str]] = {
    "안방": frozenset({"안방", "침실", "마스터룸", "마스터 침실", "master bedroom"}),
    "주방": frozenset({"주방", "부엌", "키친", "kitchen"}),
    "거실": frozenset({"거실", "리빙룸", "living room"}),
    "현관": frozenset({"현관", "입구", "entryway", "entrance"}),
}


def _normalize_name(value: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").casefold())


def _position_key(place: dict[str, Any]) -> tuple[float, float] | None:
    position = place.get("position")
    if not isinstance(position, dict):
        return None
    try:
        x, y = float(position["x"]), float(position["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    return round(x, 3), round(y, 3)


def _unique_places(places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_positions: set[tuple[float, float]] = set()
    for place in places:
        if not isinstance(place, dict) or not str(place.get("name", "")).strip():
            continue
        position_key = _position_key(place)
        if position_key is None or position_key in seen_positions:
            continue
        seen_positions.add(position_key)
        selected.append(place)
    return selected


def _resolve_cleanup_places(
    scope: str, target_location: str, places: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], str | None]:
    """Resolve a single named room or a bounded one-pass home place list."""
    available = _unique_places(places)
    if scope == "home":
        return (available, None) if available else ([], "no_remembered_places")
    if scope != "room":
        return [], "invalid_scope"
    if not target_location.strip():
        return [], "target_location_required"

    requested = _normalize_name(target_location)
    exact = [place for place in available if _normalize_name(place.get("name")) == requested]
    matches = exact
    if not matches:
        normalized_aliases = {requested}
        for canonical, aliases in _ROOM_ALIASES.items():
            group = {_normalize_name(alias) for alias in aliases | {canonical}}
            if requested in group:
                normalized_aliases.update(group)
        matches = [
            place for place in available if _normalize_name(place.get("name")) in normalized_aliases
        ]
    if len(matches) == 1:
        return matches, None
    if len(matches) > 1:
        return [], "requested_location_ambiguous"
    return [], "requested_location_not_found"


def _has_cleanup_indication(objects: Any, scene: str) -> bool:
    """Return true only when visible labels or positive scene cues indicate cleanup work."""
    scene_lower = str(scene or "").casefold()
    if any(cue in scene_lower for cue in _NEGATIVE_CLEANUP_CUES):
        return False

    if isinstance(objects, (list, tuple)):
        for item in objects:
            label = item.get("name", "") if isinstance(item, dict) else str(item)
            label_lower = str(label).casefold()
            for keyword in _CLEANUP_OBJECT_KEYWORDS:
                if keyword.isascii() and keyword.isalpha():
                    if re.search(rf"\b{re.escape(keyword)}\b", label_lower):
                        return True
                elif keyword in label_lower:
                    return True

    return any(keyword in scene_lower for keyword in _CLEANUP_SCENE_KEYWORDS)


def _has_ambiguous_cleanup_indication(objects: Any, scene: str) -> bool:
    """Flag likely personal, hazardous, or otherwise non-disposable items for clarification."""
    scene_lower = str(scene or "").casefold()
    if any(cue in scene_lower for cue in _NEGATIVE_CLEANUP_CUES):
        return False
    labels = []
    if isinstance(objects, (list, tuple)):
        labels.extend(
            item.get("name", "") if isinstance(item, dict) else str(item) for item in objects
        )
    labels.append(str(scene or ""))
    for label in labels:
        lower = str(label).casefold()
        for keyword in _AMBIGUOUS_CLEANUP_KEYWORDS:
            if keyword.isascii() and keyword.isalpha():
                if re.search(rf"\b{re.escape(keyword)}\b", lower):
                    return True
            elif keyword in lower:
                return True
    return False


def _safe_cleanup_disposal_targets(memory: Any) -> list[dict[str, Any]]:
    """Read only explicitly designated, non-estimated 3D disposal poses from memory."""
    if memory is None or not callable(getattr(memory, "get_all_objects", None)):
        return []
    try:
        objects = memory.get_all_objects()
    except Exception:
        return []

    safe_names = {_normalize_name(name) for name in _SAFE_DISPOSAL_NAMES}
    targets: list[dict[str, Any]] = []
    for obj in objects if isinstance(objects, list) else []:
        if not isinstance(obj, dict):
            continue
        name = str(obj.get("name", "")).strip()
        metadata = obj.get("metadata")
        if (
            _normalize_name(name) not in safe_names
            or not isinstance(metadata, dict)
            or metadata.get("cleanup_disposal_target") is not True
            or bool(metadata.get("estimated"))
        ):
            continue
        pose = metadata.get("cleanup_target_pose") or metadata.get("target_pose")
        if not isinstance(pose, dict) or not str(pose.get("frame_id", "")).strip():
            continue
        position = pose.get("position")
        if not isinstance(position, dict):
            continue
        try:
            xyz = {axis: float(position[axis]) for axis in ("x", "y", "z")}
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in xyz.values()):
            continue
        canonical_pose = copy.deepcopy(pose)
        canonical_pose["position"] = xyz
        targets.append({"name": name, "target_pose": canonical_pose})
    return targets


def _resolve_cleanup_plan_destinations(actions: Any, destinations: list[dict[str, Any]]) -> bool:
    """Replace planner poses with a matching, curated disposal pose from memory."""
    if not isinstance(actions, list) or not actions:
        return False
    destinations_by_name = {
        _normalize_name(str(destination.get("name", ""))): destination
        for destination in destinations
        if isinstance(destination, dict)
    }
    saw_place = False
    for action in actions:
        if not isinstance(action, dict) or action.get("skill") != "place":
            continue
        saw_place = True
        params = action.get("params")
        if not isinstance(params, dict):
            return False
        requested_name = _normalize_name(params.get("target_object", ""))
        destination = destinations_by_name.get(requested_name)
        if destination is None or not isinstance(destination.get("target_pose"), dict):
            return False
        params["target_object"] = destination["name"]
        params["target_pose"] = copy.deepcopy(destination["target_pose"])
    return saw_place


def _cleanup_plan_has_complete_placement(actions: Any) -> bool:
    """Require an explicit placement after each pick; never report a pick-only plan as tidy."""
    if not isinstance(actions, list) or not actions:
        return False
    pending_picks = 0
    saw_pick_or_place = False
    for action in actions:
        if not isinstance(action, dict):
            return False
        skill_name = str(action.get("skill", ""))
        if skill_name in _CLEANUP_PICK_SKILLS:
            pending_picks += 1
            saw_pick_or_place = True
        elif skill_name == "place":
            if pending_picks == 0:
                return False
            pending_picks -= 1
            saw_pick_or_place = True
    return saw_pick_or_place and pending_picks == 0


def _select_cloid_indicator_motion(
    motions: list[dict[str, Any]],
    allowed_motion_ids: list[int],
    *,
    rng: Any | None = None,
    previous_motion_id: int | None = None,
) -> dict[str, Any] | None:
    """Select one reviewed, current, prerequisite-free CLOi activity indicator motion."""
    allowed = {
        motion_id for motion_id in allowed_motion_ids if motion_id in _CLOID_INDICATOR_MOTIONS
    }
    candidates = []
    for motion in motions:
        if not isinstance(motion, dict):
            continue
        motion_id = motion.get("motion_id")
        if (
            motion_id not in allowed
            or motion.get("motion_name") != _CLOID_INDICATOR_MOTIONS[motion_id]
        ):
            continue
        if motion.get("pre_id") not in (None, "", 0, "0"):
            continue
        candidates.append(motion)

    if previous_motion_id is not None and len(candidates) > 1:
        candidates = [item for item in candidates if item.get("motion_id") != previous_motion_id]
    if not candidates:
        return None
    return (rng or random.SystemRandom()).choice(candidates)
