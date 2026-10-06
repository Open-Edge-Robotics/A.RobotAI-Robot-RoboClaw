"""CLOi motion-map lookup and guarded command tests (ROS-free fakes)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit


class _BaseSkillStub:
    def __init__(self) -> None:
        self.node = None

    def set_node(self, node) -> None:
        self.node = node

    def require_node(self):
        if self.node is None:
            return self.fail_result("missing node", failure_reason="execution_failed")
        return None

    def fail_result(self, message, *, failure_reason="execution_failed", status="failed", **kwargs):
        return {
            "success": False,
            "message": message,
            "status": status,
            "failure_reason": failure_reason,
            **kwargs,
        }

    def success_result(self, message, *, status="completed", **kwargs):
        return {
            "success": True,
            "message": message,
            "status": status,
            **kwargs,
        }


# Import the skill file with a minimal BaseSkill stub so these unit tests don't
# depend on ROS packages or the multi-package colcon Python import path.
_skill_manager_stub = ModuleType("robo_claw_agent.skill_manager")
_skill_manager_stub.BaseSkill = _BaseSkillStub
_previous_skill_manager = sys.modules.get("robo_claw_agent.skill_manager")
sys.modules["robo_claw_agent.skill_manager"] = _skill_manager_stub

_MODULE_PATH = Path(__file__).parents[1] / "robo_claw_agent" / "skills" / "cloid_motion_skill.py"
_SPEC = importlib.util.spec_from_file_location("cloid_motion_skill_under_test", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
try:
    _SPEC.loader.exec_module(_MOD)
finally:
    if _previous_skill_manager is None:
        sys.modules.pop("robo_claw_agent.skill_manager", None)
    else:
        sys.modules["robo_claw_agent.skill_manager"] = _previous_skill_manager
ListCloidMotionsSkill = _MOD.ListCloidMotionsSkill
ExecuteCloidMotionSkill = _MOD.ExecuteCloidMotionSkill


class _FakeString:
    def __init__(self, data: str = "") -> None:
        self.data = data


class _FakeMotionCommand:
    def __init__(self) -> None:
        self.motion_id = 0
        self.command_type = 255


class _FakePublisher:
    def __init__(self, subscription_count: int = 1) -> None:
        self.subscription_count = subscription_count
        self.messages = []

    def get_subscription_count(self) -> int:
        return self.subscription_count

    def publish(self, message: Any) -> None:
        self.messages.append(message)


class _FakeNode:
    pass


def _catalog(*motions: dict[str, Any]) -> str:
    return json.dumps({"motions": list(motions)}, ensure_ascii=False)


def _motion(
    motion_id: int,
    name: str,
    *,
    intent: str = "인사",
    description: str = "인사 동작",
    mask: str = "LRH",
    pre_id: str = "",
) -> dict[str, Any]:
    return {
        "id": motion_id,
        "display_name": name,
        "intent_id": intent,
        "description": description,
        "mask": mask,
        "pre_id": pre_id,
    }


def _set_catalog(skill, raw: str) -> None:
    skill.wait_for_message = lambda *_args, **_kwargs: type("Message", (), {"data": raw})()


def _patch_ros(monkeypatch, publisher: _FakePublisher) -> None:
    monkeypatch.setattr(_MOD, "_load_string_type", lambda: _FakeString)
    monkeypatch.setattr(
        _MOD,
        "_create_motion_publisher",
        lambda _node: (publisher, _FakeMotionCommand),
    )


def test_list_returns_registered_motion_names_and_metadata(monkeypatch):
    monkeypatch.setattr(_MOD, "_load_string_type", lambda: _FakeString)
    skill = ListCloidMotionsSkill()
    skill.set_node(_FakeNode())
    _set_catalog(
        skill,
        _catalog(
            _motion(14, "wave", intent="손흔들기", description="설명 후 인사", mask="WLRH"),
            _motion(19, "greet1", intent="인사"),
        ),
    )

    result = skill.execute({})

    assert skill.name == "list_cloid_motions"
    assert skill.risk_level == "read"
    assert skill.answer_mode == "informational"
    assert result["success"] is True
    assert result["source_topic"] == "/task_manager/motion_map"
    assert result["motion_count"] == 2
    assert result["motions"] == [
        {
            "motion_id": 14,
            "motion_name": "wave",
            "intent_id": "손흔들기",
            "description": "설명 후 인사",
            "mask": "WLRH",
            "pre_id": None,
        },
        {
            "motion_id": 19,
            "motion_name": "greet1",
            "intent_id": "인사",
            "description": "인사 동작",
            "mask": "LRH",
            "pre_id": None,
        },
    ]


def test_motion_map_subscription_requests_latched_delivery(monkeypatch):
    monkeypatch.setattr(_MOD, "_load_string_type", lambda: _FakeString)
    skill = ListCloidMotionsSkill()
    skill.set_node(_FakeNode())
    calls = []
    skill.wait_for_message = lambda *args, **kwargs: (
        calls.append((args, kwargs))
        or type("Message", (), {"data": _catalog(_motion(14, "wave"))})()
    )

    assert skill.execute({})["success"] is True
    assert calls[0][0][1] == "/task_manager/motion_map"
    assert calls[0][1]["use_transient_local"] is True


def test_catalog_rejects_malformed_json(monkeypatch):
    monkeypatch.setattr(_MOD, "_load_string_type", lambda: _FakeString)
    skill = ListCloidMotionsSkill()
    skill.set_node(_FakeNode())
    _set_catalog(skill, "not-json")

    result = skill.execute({})

    assert result["success"] is False
    assert result["failure_reason"] == "invalid_motion_catalog"


def test_catalog_rejects_duplicate_or_invalid_ids(monkeypatch):
    monkeypatch.setattr(_MOD, "_load_string_type", lambda: _FakeString)
    skill = ListCloidMotionsSkill()
    skill.set_node(_FakeNode())
    _set_catalog(skill, _catalog(_motion(14, "wave"), _motion(14, "wave-again")))

    result = skill.execute({})

    assert result["success"] is False
    assert result["failure_reason"] == "invalid_motion_catalog"


def test_catalog_requires_ros_node():
    result = ListCloidMotionsSkill().execute({})

    assert result["success"] is False
    assert result["failure_reason"] == "execution_failed"


def test_catalog_uses_read_only_file_fallback_when_topic_has_no_message(tmp_path, monkeypatch):
    monkeypatch.setattr(_MOD, "_load_string_type", lambda: _FakeString)
    catalog_path = tmp_path / "motion_map.json"
    catalog_path.write_text(_catalog(_motion(14, "wave")), encoding="utf-8")

    class _FileNode:
        def has_parameter(self, name: str) -> bool:
            return name == "cloid_motion_catalog_file"

        def get_parameter(self, _name: str):
            return SimpleNamespace(
                get_parameter_value=lambda: SimpleNamespace(string_value=str(catalog_path))
            )

    skill = ListCloidMotionsSkill()
    skill.set_node(_FileNode())
    skill.wait_for_message = lambda *_args, **_kwargs: None

    result = skill.execute({})

    assert result["success"] is True
    assert result["source_file"] == str(catalog_path)
    assert result["motions"][0]["motion_name"] == "wave"


def test_catalog_rejects_oversized_file_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(_MOD, "_load_string_type", lambda: _FakeString)
    catalog_path = tmp_path / "motion_map.json"
    catalog_path.write_text('{"motions":[]}' + " " * 262_144, encoding="utf-8")

    class _FileNode:
        def has_parameter(self, name: str) -> bool:
            return name == "cloid_motion_catalog_file"

        def get_parameter(self, _name: str):
            return SimpleNamespace(
                get_parameter_value=lambda: SimpleNamespace(string_value=str(catalog_path))
            )

    skill = ListCloidMotionsSkill()
    skill.set_node(_FileNode())
    skill.wait_for_message = lambda *_args, **_kwargs: None

    result = skill.execute({})

    assert result["success"] is False
    assert result["failure_reason"] == "invalid_motion_catalog"


def test_catalog_fails_closed_when_topic_and_file_are_unavailable(monkeypatch):
    monkeypatch.setattr(_MOD, "_load_string_type", lambda: _FakeString)
    skill = ListCloidMotionsSkill()
    skill.set_node(_FakeNode())
    skill.wait_for_message = lambda *_args, **_kwargs: None

    result = skill.execute({})

    assert result["success"] is False
    assert result["failure_reason"] == "motion_catalog_unavailable"


def test_execute_registered_motion_name_publishes_catalog_id_once(monkeypatch):
    publisher = _FakePublisher()
    _patch_ros(monkeypatch, publisher)
    skill = ExecuteCloidMotionSkill()
    skill.set_node(_FakeNode())
    _set_catalog(skill, _catalog(_motion(14, "wave", intent="손흔들기")))

    result = skill.execute({"motion_name": "wave", "confirm": True})

    assert skill.name == "execute_cloid_motion"
    assert skill.risk_level == "dangerous"
    assert result["success"] is True
    assert result["status"] == "sent"
    assert result["motion_id"] == 14
    assert result["motion_name"] == "wave"
    assert result["completion_confirmed"] is False
    assert len(publisher.messages) == 1
    assert publisher.messages[0].motion_id == 14
    assert publisher.messages[0].command_type == 0


def test_execute_resolves_duplicate_name_only_when_id_disambiguates(monkeypatch):
    publisher = _FakePublisher()
    _patch_ros(monkeypatch, publisher)
    skill = ExecuteCloidMotionSkill()
    skill.set_node(_FakeNode())
    _set_catalog(
        skill,
        _catalog(
            _motion(22, "scan", intent="탐색 모션", description="좌우 탐색"),
            _motion(23, "scan", intent="탐색 모션", description="왼쪽 탐색"),
        ),
    )

    ambiguous = skill.execute({"motion_name": "scan", "confirm": True})
    selected = skill.execute({"motion_name": "scan", "motion_id": 23, "confirm": True})

    assert ambiguous["success"] is False
    assert ambiguous["failure_reason"] == "ambiguous_motion_name"
    assert {x["motion_id"] for x in ambiguous["candidates"]} == {22, 23}
    assert selected["success"] is True
    assert selected["motion_id"] == 23
    assert len(publisher.messages) == 1
    assert publisher.messages[0].motion_id == 23


def test_execute_rejects_mismatched_name_and_id(monkeypatch):
    publisher = _FakePublisher()
    _patch_ros(monkeypatch, publisher)
    skill = ExecuteCloidMotionSkill()
    skill.set_node(_FakeNode())
    _set_catalog(skill, _catalog(_motion(14, "wave"), _motion(19, "greet1")))

    result = skill.execute({"motion_name": "wave", "motion_id": 19, "confirm": True})

    assert result["success"] is False
    assert result["failure_reason"] == "motion_selection_mismatch"
    assert publisher.messages == []


def test_execute_rejects_unregistered_name(monkeypatch):
    publisher = _FakePublisher()
    _patch_ros(monkeypatch, publisher)
    skill = ExecuteCloidMotionSkill()
    skill.set_node(_FakeNode())
    _set_catalog(skill, _catalog(_motion(14, "wave")))

    result = skill.execute({"motion_name": "not-registered", "confirm": True})

    assert result["success"] is False
    assert result["failure_reason"] == "motion_not_registered"
    assert publisher.messages == []


def test_execute_blocks_motion_with_unfulfilled_prerequisite(monkeypatch):
    publisher = _FakePublisher()
    _patch_ros(monkeypatch, publisher)
    skill = ExecuteCloidMotionSkill()
    skill.set_node(_FakeNode())
    _set_catalog(skill, _catalog(_motion(20, "greet2", pre_id="19")))

    result = skill.execute({"motion_name": "greet2", "confirm": True})

    assert result["success"] is False
    assert result["failure_reason"] == "motion_prerequisite_required"
    assert result["required_pre_motion_id"] == 19
    assert publisher.messages == []


def test_execute_requires_explicit_confirmation(monkeypatch):
    publisher = _FakePublisher()
    _patch_ros(monkeypatch, publisher)
    skill = ExecuteCloidMotionSkill()
    skill.set_node(_FakeNode())
    _set_catalog(skill, _catalog(_motion(14, "wave")))

    result = skill.execute({"motion_name": "wave", "confirm": False})

    assert result["success"] is False
    assert result["failure_reason"] == "safety_rejected"
    assert publisher.messages == []


def test_execute_blocks_when_motion_player_has_no_subscriber(monkeypatch):
    publisher = _FakePublisher(subscription_count=0)
    _patch_ros(monkeypatch, publisher)
    monkeypatch.setattr(_MOD.time, "sleep", lambda _seconds: None)
    skill = ExecuteCloidMotionSkill()
    skill.set_node(_FakeNode())
    _set_catalog(skill, _catalog(_motion(14, "wave")))

    result = skill.execute({"motion_name": "wave", "confirm": True, "connection_timeout_sec": 0.1})

    assert result["success"] is False
    assert result["failure_reason"] == "motion_player_unavailable"
    assert publisher.messages == []


def test_stop_skill_requires_confirmation_and_publishes_stop_command(monkeypatch):
    publisher = _FakePublisher()
    _patch_ros(monkeypatch, publisher)
    stop_skill_type = getattr(_MOD, "StopCloidMotionSkill", None)
    assert stop_skill_type is not None
    skill = stop_skill_type()
    skill.set_node(_FakeNode())

    rejected = skill.execute({"confirm": False})
    sent = skill.execute({"confirm": True})

    assert rejected["success"] is False
    assert rejected["failure_reason"] == "safety_rejected"
    assert sent["success"] is True
    assert sent["status"] == "sent"
    assert sent["command_type"] == 1
    assert len(publisher.messages) == 1
    assert publisher.messages[0].motion_id == 0
    assert publisher.messages[0].command_type == 1


def test_execute_rejects_bad_timeout_and_bad_motion_id(monkeypatch):
    publisher = _FakePublisher()
    _patch_ros(monkeypatch, publisher)
    skill = ExecuteCloidMotionSkill()
    skill.set_node(_FakeNode())
    _set_catalog(skill, _catalog(_motion(14, "wave")))

    bad_timeout = skill.execute(
        {"motion_name": "wave", "confirm": True, "connection_timeout_sec": 50.0}
    )
    bad_id = skill.execute({"motion_name": "wave", "motion_id": True, "confirm": True})

    assert bad_timeout["success"] is False
    assert bad_timeout["failure_reason"] == "invalid_timeout"
    assert bad_id["success"] is False
    assert bad_id["failure_reason"] == "invalid_motion_id"
    assert publisher.messages == []
