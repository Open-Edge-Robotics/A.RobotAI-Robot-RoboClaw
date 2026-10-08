import json
import logging
import os
import re
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
    if not isinstance(meta, dict):
        return False
    return (
        meta.get("kind") in {"object", "blocked"}
        or meta.get("type") in {"blocked_coordinate", "virtual_obstacle"}
        or meta.get("source") in {"vlm", "scan_room", "mark_virtual_obstacle"}
    )


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
            meta = {}
        if (
            meta.get("type") in {"blocked_coordinate", "virtual_obstacle"}
            or meta.get("kind") in {"blocked", "object"}
            or meta.get("source") in {"vlm", "scan_room", "mark_virtual_obstacle"}
        ):
            continue

        # 1. 좌표 파싱: 메타데이터 우선 -> 순서쌍 (x, y) -> x:, y: 형식
        x: float | None = None
        y: float | None = None
        if "x" in meta and "y" in meta:
            try:
                x, y = float(meta["x"]), float(meta["y"])
            except (TypeError, ValueError):
                x, y = None, None

        if x is None or y is None:
            pair_match = re.search(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)", text)
            if pair_match:
                try:
                    x, y = float(pair_match.group(1)), float(pair_match.group(2))
                except (TypeError, ValueError):
                    pass

        if x is None or y is None:
            x_match = re.search(r"x\s*[:=]\s*(-?\d+(?:\.\d+)?)", text, re.IGNORECASE)
            y_match = re.search(r"y\s*[:=]\s*(-?\d+(?:\.\d+)?)", text, re.IGNORECASE)
            if x_match and y_match:
                try:
                    x, y = float(x_match.group(1)), float(y_match.group(1))
                except (TypeError, ValueError):
                    pass

        if x is None or y is None:
            continue

        # 2. 장소명 파싱: 메타데이터 우선 -> 텍스트 내 괄호 및 키워드 파싱
        name = ""
        for key in (
            "target_name",
            "name",
            "object_name",
            "place_name",
            "location_name",
            "location_type",
            "category",
        ):
            value = str(meta.get(key) or "").strip()
            if value:
                name = value
                break

        if not name and text:
            # "이동 성공 좌표: x=1.50, y=2.00 (주방)" 처럼 끝에 괄호로 명시된 장소명
            paren_match = re.search(r"\(([^)]+)\)\s*$", text)
            if paren_match:
                candidate_name = paren_match.group(1).strip()
                if candidate_name and not re.search(r"^\s*-?\d+", candidate_name):
                    name = candidate_name

            if not name:
                try:
                    from robo_claw_agent.memory_manager.semantic import _entry_all_names

                    names = _entry_all_names(entry)
                    if names:
                        name = names[0]
                except Exception:
                    pass

        if not name:
            name = f"RAG위치{len(candidates) + 1}"

        dedupe_key = (name, round(x, 3), round(y, 3))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append(
            {"name": name, "position": {"x": x, "y": y}, "text": text, "metadata": meta}
        )
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
    if names & {
        "grasp",
        "place",
        "move_joints",
        "move_pose",
        "arm_pose",
        "open_gripper",
        "close_gripper",
        "adaptive_pick_object",
        "vla_pick_gripper_object",
        "vla_pick_front_object",
        "pick_front_object",
        "pick_from_right_side_zone",
        "estimate_gripper_object_pose",
        "servo_gripper_to_object",
        "observe_gripper_target",
    }:
        caps.append("manipulation")
    if names & {
        "navigate_to",
        "move_relative",
        "rotate",
        "face_direction",
        "follow_waypoints",
        "patrol",
        "explore",
        "approach_object",
    }:
        caps.append("navigation")
    if names & {
        "analyze_scene",
        "get_detections",
        "find_object",
        "scan_room",
        "describe_surroundings",
        "detect_object",
    }:
        caps.append("perception")
    if "say" in names or "listen" in names:
        caps.append("hri")
    return caps


def _has_manipulation_capability(node: Any) -> bool:
    return "manipulation" in _self_capabilities(node)


def _has_cleanup_capability(node: Any) -> bool:
    """정리 대상을 집고 옮길 수 있는 등록 스킬이 있는지 판단한다."""
    skills = getattr(node, "_skills", None)
    if not skills or not hasattr(skills, "list_skills"):
        return False
    try:
        names = {str(skill.get("name", "")) for skill in skills.list_skills()}
    except Exception:
        return False
    pick_skills = {
        "adaptive_pick_object",
        "vla_pick_gripper_object",
        "vla_pick_front_object",
        "pick_front_object",
        "pick_from_right_side_zone",
    }
    return bool(names & pick_skills) or {"grasp", "place"}.issubset(names)


def _peer_has_capability(info: dict[str, Any], capability: str) -> bool:
    caps = info.get("capabilities") or []
    if isinstance(caps, list) and capability in caps:
        return True
    role = str(info.get("role", "")).lower()
    if capability == "manipulation" and any(
        kw in role
        for kw in (
            "매니퓰레이션",
            "manipulation",
            "팔",
            "arm",
            "그리퍼",
            "gripper",
            "파지",
            "grasp",
            "manipulator",
        )
    ):
        return True
    return False


def _find_peer_with_capability(node: Any, capability: str) -> str | None:
    for peer_id, info in _discover_peer_agents(node).items():
        if _peer_has_capability(info, capability):
            return peer_id
    return None


def _connected_grpc_peer_names(node: Any) -> set[str]:
    """ListPeers 서비스에서 현재 연결된 gRPC 피어 이름을 가져온다."""
    if node is None or not hasattr(node, "_list_peers_client"):
        return set()
    try:
        from robo_claw_agent.skills.cooperate_skill import ListPeerRobotsSkill

        skill = ListPeerRobotsSkill()
        skill.set_node(node)
        result = skill.execute({})
        if not result.get("success"):
            return set()
        return {
            str(peer.get("peer_name", "")).strip()
            for peer in result.get("peers", [])
            if peer.get("connected") and peer.get("peer_name")
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("[BT] Could not query connected gRPC peers: %s", exc)
        return set()


def _is_connected_ros_peer(node: Any, peer_id: str) -> bool:
    """ROS 그래프에서 해당 동료의 agent 노드가 실제로 발견되는지 확인한다."""
    try:
        named_nodes = node.get_node_names_and_namespaces()
        if any(
            str(name) == "robo_claw_agent_node" and str(namespace).strip("/") == peer_id.strip("/")
            for name, namespace in named_nodes
        ):
            return True
    except Exception:
        pass
    try:
        node_names = node.get_node_names()
    except Exception:
        return False
    expected = f"{peer_id}_robo_claw_agent_node"
    return any(
        str(name) == expected
        or str(name).endswith(f"/{expected}")
        or (
            str(name).endswith("/robo_claw_agent_node")
            and f"/{peer_id}/" in f"{str(name).rstrip('/')}/"
        )
        for name in node_names
    )


def _query_grpc_peer_capability(node: Any, peer_name: str, capability: str) -> bool:
    """설정 메타데이터가 없는 연결된 gRPC 피어의 실제 스킬 목록을 확인한다."""
    try:
        from robo_claw_agent.skills.cooperate_skill import QueryPeerCapabilitiesSkill

        skill = QueryPeerCapabilitiesSkill()
        skill.set_node(node)
        result = skill.execute({"peer_name": peer_name, "timeout_sec": 10.0})
        if not result.get("success"):
            return False
        skill_names = set(result.get("capabilities", []))
        if capability == "manipulation":
            pick_skills = {
                "adaptive_pick_object",
                "vla_pick_gripper_object",
                "vla_pick_front_object",
                "pick_front_object",
                "pick_from_right_side_zone",
            }
            return bool(skill_names & pick_skills) or {"grasp", "place"}.issubset(skill_names)
        return capability in skill_names
    except Exception as exc:  # noqa: BLE001
        logger.debug("[BT] Could not query capabilities for %s: %s", peer_name, exc)
        return False


def _find_connected_peer_with_capability(node: Any, capability: str) -> dict[str, str] | None:
    """실제로 연결된 피어 중 선언된 능력을 가진 동료와 전송 경로를 반환한다.

    ROS 그래프에서 발견된 피어는 delegate_task, 현재 gRPC 연결이 확인된 피어는
    call_peer_robot으로 보낸다. 설정만 되어 있고 연결되지 않은 피어는 선택하지 않는다.
    """
    peers = _discover_peer_agents(node)
    grpc_names = _connected_grpc_peer_names(node)
    grpc_names_normalized = {name.casefold() for name in grpc_names}

    for peer_id, info in peers.items():
        if _is_connected_ros_peer(node, peer_id) and _peer_has_capability(info, capability):
            return {"peer_name": peer_id, "transport": "ros2"}
        if peer_id.casefold() in grpc_names_normalized:
            if _peer_has_capability(info, capability) or _query_grpc_peer_capability(
                node, peer_id, capability
            ):
                return {"peer_name": peer_id, "transport": "grpc"}

    # launch 파라미터로만 등록된 원격 피어는 환경 설정 목록에 메타데이터가 없을 수 있다.
    # 현재 연결이 확인된 피어만 대상으로 실제 스킬 목록을 조회한다.
    for peer_name in sorted(grpc_names):
        if peer_name.casefold() not in {
            name.casefold() for name in peers
        } and _query_grpc_peer_capability(node, peer_name, capability):
            return {"peer_name": peer_name, "transport": "grpc"}
    return None
