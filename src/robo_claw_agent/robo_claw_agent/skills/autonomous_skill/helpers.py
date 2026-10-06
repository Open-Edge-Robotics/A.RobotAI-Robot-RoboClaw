import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _is_alias_object(obj: dict[str, Any]) -> bool:
    meta = obj.get("metadata", {}) if isinstance(obj, dict) else {}
    return isinstance(meta, dict) and "alias_of" in meta


def _is_map_generated_place(obj: dict[str, Any]) -> bool:
    meta = obj.get("metadata", {}) if isinstance(obj, dict) else {}
    return isinstance(meta, dict) and meta.get("source") == "autonomous_ensure_places"


def _is_object_kind(obj: dict[str, Any]) -> bool:
    meta = obj.get("metadata", {}) if isinstance(obj, dict) else {}
    return isinstance(meta, dict) and meta.get("kind") == "object"


def _has_xy_position(obj: dict[str, Any]) -> bool:
    pos = obj.get("position", {}) if isinstance(obj, dict) else {}
    if not isinstance(pos, dict):
        return False
    try:
        float(pos["x"])
        float(pos["y"])
        return True
    except (KeyError, TypeError, ValueError):
        return False


def _last_seen_epoch(obj: dict[str, Any]) -> float:
    raw = obj.get("last_seen")
    if not raw:
        return 0.0
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(raw)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _format_recency(obj: dict[str, Any]) -> str:
    epoch = _last_seen_epoch(obj)
    if epoch <= 0:
        return ""
    from datetime import datetime

    elapsed_sec = max(0.0, datetime.now().timestamp() - epoch)
    if elapsed_sec < 60:
        return " [방금 방문]"
    if elapsed_sec < 3600:
        return f" [{int(elapsed_sec // 60)}분 전 방문]"
    if elapsed_sec < 86400:
        return f" [{int(elapsed_sec // 3600)}시간 전 방문]"
    return f" [{int(elapsed_sec // 86400)}일 전 방문]"


def _format_place(obj: dict[str, Any]) -> str:
    pos = obj.get("position", {})
    return (
        f"  • {obj.get('name')}: (x={float(pos.get('x')):.2f}, y={float(pos.get('y')):.2f})"
        f"{_format_recency(obj)}"
    )


def _split_semantic_places(
    objects: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    semantic_places = []
    map_generated_places = []
    for obj in objects:
        if _is_alias_object(obj) or not _has_xy_position(obj):
            continue
        if _is_object_kind(obj):
            continue
        if _is_map_generated_place(obj):
            map_generated_places.append(obj)
        else:
            semantic_places.append(obj)
    return semantic_places, map_generated_places


def _extract_rag_location_candidates(memory: Any, limit: int = 5) -> list[dict[str, Any]]:
    entries = []
    try:
        if hasattr(memory, "_vector_store") and hasattr(memory._vector_store, "list_entries"):
            entries = list(memory._vector_store.list_entries())
        elif hasattr(memory, "search_knowledge"):
            entries = list(memory.search_knowledge("장소 위치 좌표 이동 목적지", top_k=limit))
    except Exception as exc:
        logger.debug("[BT] Failed to retrieve RAG location candidates (ignored): %s", exc)
        return []

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float]] = set()
    for entry in reversed(entries):
        meta = entry.get("metadata", {}) if isinstance(entry, dict) else {}
        text = str(entry.get("text", "")) if isinstance(entry, dict) else ""
        if not isinstance(meta, dict):
            continue
        if "x" not in meta or "y" not in meta:
            continue
        if meta.get("type") == "blocked_coordinate" or meta.get("kind") == "blocked":
            continue
        try:
            x = float(meta["x"])
            y = float(meta["y"])
        except (TypeError, ValueError):
            continue
        name = ""
        for key in ("target_name", "name", "object_name", "place_name", "location_name"):
            value = str(meta.get(key) or "").strip()
            if value:
                name = value
                break
        if not name:
            name = f"RAG위치{len(candidates) + 1}"
        dedupe_key = (name, round(x, 3), round(y, 3))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append({"name": name, "position": {"x": x, "y": y}, "text": text, "metadata": meta})
        if len(candidates) >= limit:
            break
    return candidates


def _discover_peer_agents(node: Any) -> dict[str, dict[str, Any]]:
    other_agents: dict[str, dict[str, Any]] = {}

    peers_json = os.environ.get("GRPC_TARGET_PEERS_JSON", "[]")
    try:
        if peers_json and peers_json.strip() and peers_json != "[]":
            peers_list = json.loads(peers_json)
            for p in peers_list:
                name = p.get("name") or p.get("agent_name")
                if name:
                    other_agents[name] = {
                        "description": p.get("description") or "설명 없음",
                        "role": p.get("role") or "",
                        "capabilities": p.get("capabilities") or [],
                    }
    except Exception as e:
        logger.warning("[BT] Failed to parse GRPC_TARGET_PEERS_JSON: %s", e)

    try:
        node_names = node.get_node_names()
        my_name = node.get_name()
        my_ns = node.get_namespace().strip("/")
        my_full_name = f"/{my_ns}/{my_name}" if my_ns else f"/{my_name}"
        for name in node_names:
            if "robo_claw_agent_node" in name and name != my_full_name:
                parts = name.split("/")
                if len(parts) >= 3:
                    agent_id = parts[1]
                else:
                    agent_id = name.strip("/").replace("_node", "")
                if agent_id != my_ns and agent_id not in other_agents:
                    other_agents[agent_id] = {
                        "description": "설명 없음",
                        "role": "",
                        "capabilities": [],
                    }
    except Exception as e:
        logger.warning("[BT] Failed to discover other agent list: %s", e)

    my_ns = node.get_namespace().strip("/")
    other_agents.pop(my_ns, None)
    return other_agents


def _self_capabilities(node: Any) -> list[str]:
    skills = getattr(node, "_skills", None)
    if not skills or not hasattr(skills, "list_skills"):
        return []
    try:
        all_skills = skills.list_skills()
    except Exception:
        return []
    names = {s["name"] for s in all_skills}
    caps: list[str] = []
    if names & {"grasp", "place", "move_joints", "move_pose", "arm_pose", "open_gripper", "close_gripper",
                "adaptive_pick_object", "vla_pick_gripper_object", "vla_pick_front_object",
                "pick_front_object", "pick_from_right_side_zone", "estimate_gripper_object_pose",
                "servo_gripper_to_object", "observe_gripper_target"}:
        caps.append("manipulation")
    if names & {"navigate_to", "move_relative", "rotate", "face_direction", "follow_waypoints",
                "patrol", "explore", "approach_object"}:
        caps.append("navigation")
    if names & {"analyze_scene", "get_detections", "find_object", "scan_room",
                "describe_surroundings", "detect_object"}:
        caps.append("perception")
    if "say" in names or "listen" in names:
        caps.append("hri")
    return caps


def _has_manipulation_capability(node: Any) -> bool:
    return "manipulation" in _self_capabilities(node)


def _find_peer_with_capability(node: Any, capability: str) -> str | None:
    peers = _discover_peer_agents(node)
    for peer_id, info in peers.items():
        caps = info.get("capabilities") or []
        if isinstance(caps, list) and capability in caps:
            return peer_id
        role = str(info.get("role", "")).lower()
        if capability == "manipulation" and any(
            kw in role for kw in ("매니퓰레이션", "manipulation", "팔", "arm", "그리퍼", "gripper", "파지", "grasp")
        ):
            return peer_id
    return None
