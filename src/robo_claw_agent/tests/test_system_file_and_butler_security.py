import os
import tempfile
from types import SimpleNamespace

import pytest
from robo_claw_agent.skills.butler_skill import RunButlerScriptSkill
from robo_claw_agent.skills.file_skill import (
    AnalyzeStoredFileSkill,
    DeleteFileSkill,
    ListFilesSkill,
)
from robo_claw_agent.skills.hri_skill import SendMessageSkill
from robo_claw_agent.skills.path_utils import (
    _is_safe_path,
)
from robo_claw_agent.skills.system_skill.emergency import (
    EmergencyStopSkill,
    ResetEmergencyStopSkill,
)


@pytest.mark.unit
def test_path_utils_symlink_escape_blocked():
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Create a symlink pointing to /etc
        link_path = os.path.join(tmp_dir, "escape_link")
        try:
            os.symlink("/etc", link_path)
        except OSError:
            pytest.skip("Symlink creation not permitted in this environment")

        node = SimpleNamespace(_agent_workspace_dir=tmp_dir)
        # Inside the directory tmp_dir, but targets /etc
        target_file = os.path.join(link_path, "passwd")
        assert not _is_safe_path(target_file, node)


@pytest.mark.unit
def test_analyze_stored_file_rejects_unsafe_path():
    skill = AnalyzeStoredFileSkill()
    node = SimpleNamespace(_agent_workspace_dir="/tmp/safe_ws")
    skill.set_node(node)

    res = skill.execute({"file_path": "/etc/shadow"})
    assert res["success"] is False
    assert "권한" in res["message"] or "허용되지" in res["message"] or "찾을 수 없습니다" in res["message"]


@pytest.mark.unit
def test_delete_file_rejects_unsafe_path():
    skill = DeleteFileSkill()
    node = SimpleNamespace(_agent_workspace_dir="/tmp/safe_ws")
    skill.set_node(node)

    res = skill.execute({"file_path": "/etc/passwd"})
    assert res["success"] is False
    assert "권한" in res["message"] or "허용되지" in res["message"] or "찾을 수 없" in res["message"]


@pytest.mark.unit
def test_list_files_rejects_unsafe_path():
    skill = ListFilesSkill()
    node = SimpleNamespace(_agent_workspace_dir="/tmp/safe_ws")
    skill.set_node(node)

    res = skill.execute({"dir_path": "/etc"})
    assert res["success"] is False
    assert "권한" in res["message"] or "허용되지" in res["message"] or "디렉토리" in res["message"]


@pytest.mark.unit
def test_hri_send_message_does_not_silently_fallback():
    skill = SendMessageSkill()
    node = SimpleNamespace(_send_msg_client=object())
    skill.set_node(node)

    # User explicitly requested a missing file
    res = skill.execute({"message": "report", "file_path": "/tmp/non_existent_file_12345.png"})
    assert res["success"] is False
    assert "존재하지" in res["message"] or "찾을 수" in res["message"] or "오류" in res["message"] or "허용되지" in res["message"]


@pytest.mark.unit
def test_butler_script_uses_node_parameter_not_param_override():
    skill = RunButlerScriptSkill()
    node = SimpleNamespace(
        has_parameter=lambda name: True,
        get_parameter=lambda name: SimpleNamespace(
            get_parameter_value=lambda: SimpleNamespace(string_value="/ros2_ws/butler_scripts")
        ),
    )
    skill.set_node(node)

    # Attempt to override script_dir to /bin
    res = skill.execute({"script_name": "test.sh", "butler_script_dir": "/bin"})
    assert res["success"] is False
    # Should look in /ros2_ws/butler_scripts, NOT /bin
    assert "/bin/test.sh" not in res["message"]


@pytest.mark.unit
def test_emergency_stop_latches_and_reset_unlatches(monkeypatch):
    stop_skill = EmergencyStopSkill()
    reset_skill = ResetEmergencyStopSkill()

    node = SimpleNamespace(
        create_publisher=lambda msg_type, topic, qos: SimpleNamespace(publish=lambda msg: None),
        _emergency_stop_latched=False,
    )
    stop_skill.set_node(node)
    reset_skill.set_node(node)

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill._cancel_active_goal",
        lambda *args, **kwargs: (True, "cancelled"),
    )

    # Trigger emergency stop
    res = stop_skill.execute({})
    assert res["success"] is True
    assert res["status"] == "completed"
    assert res["failure_reason"] == ""
    assert res["recoverable"] is False
    assert getattr(node, "_emergency_stop_latched", False) is True

    # Reset emergency stop
    res_reset = reset_skill.execute({"confirm": True})
    assert res_reset["success"] is True
    assert res_reset["status"] == "completed"
    assert getattr(node, "_emergency_stop_latched", True) is False
