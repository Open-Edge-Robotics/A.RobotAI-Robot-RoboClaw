"""
파일 및 스크립트 실행 관련 스킬 유닛 테스트
"""

import os
from typing import Any

from robo_claw_agent.skills.file_skill import (
    EditTextFileSkill,
    ReadTextFileSkill,
    RunScriptSkill,
    WriteTextFileSkill,
    _is_safe_path,
)


class MockNode:
    def __init__(self, workspace_dir: str, butler_dir: str = "/tmp/butler_scripts"):
        self._agent_workspace_dir = workspace_dir
        self._butler_script_dir = butler_dir

    def has_parameter(self, name: str) -> bool:
        return name == "butler_script_dir"

    def get_parameter(self, name: str) -> Any:
        class Parameter:
            def __init__(self, val):
                self.val = val
            def get_parameter_value(self):
                class Value:
                    def __init__(self, v):
                        self.string_value = v
                return Value(self.val)
        if name == "butler_script_dir":
            return Parameter(self._butler_script_dir)
        return None


def test_path_safety_check():
    workspace = "/allowed/workspace"
    butler_dir = "/allowed/butler"

    node = MockNode(workspace_dir=workspace, butler_dir=butler_dir)

    # 1. 안전한 경로 검증
    assert _is_safe_path(os.path.join(workspace, "test.txt"), node) is True
    assert _is_safe_path(os.path.join(butler_dir, "run.sh"), node) is True
    assert _is_safe_path("/tmp/anyfile.log", node) is True

    # 2. 위험한 경로 검증 (상위 디렉토리 순회)
    assert _is_safe_path(os.path.join(workspace, "../outside.txt"), node) is False
    assert _is_safe_path("/etc/passwd", node) is False
    assert _is_safe_path("/var/log/syslog", node) is False


def test_write_and_read_text_file(tmp_path):
    workspace = str(tmp_path / "workspace")
    os.makedirs(workspace, exist_ok=True)
    node = MockNode(workspace_dir=workspace)

    writer = WriteTextFileSkill()
    writer.set_node(node)

    reader = ReadTextFileSkill()
    reader.set_node(node)

    file_name = "hello.txt"
    content = "Hello\nWorld\nPython\nROS2"

    # 1. 파일 쓰기
    write_res = writer.execute({"file_path": file_name, "content": content})
    assert write_res["success"] is True
    assert os.path.exists(os.path.join(workspace, file_name))

    # 2. overwrite=False 검증
    write_res_dup = writer.execute({"file_path": file_name, "content": "New Content", "overwrite": False})
    assert write_res_dup["success"] is False

    # 3. 전체 파일 읽기
    read_res = reader.execute({"file_path": file_name})
    assert read_res["success"] is True
    assert read_res["content"] == content
    assert read_res["total_lines"] == 4

    # 4. 특정 구간 읽기 (1-based, 2번째부터 3번째 라인까지)
    read_res_range = reader.execute({"file_path": file_name, "start_line": 2, "end_line": 3})
    assert read_res_range["success"] is True
    assert read_res_range["content"] == "World\nPython\n"


def test_edit_text_file(tmp_path):
    workspace = str(tmp_path / "workspace")
    os.makedirs(workspace, exist_ok=True)
    node = MockNode(workspace_dir=workspace)

    writer = WriteTextFileSkill()
    writer.set_node(node)

    editor = EditTextFileSkill()
    editor.set_node(node)

    file_name = "config.yaml"
    initial_content = "param_a: 10\nparam_b: 20\nparam_a: 30"
    writer.execute({"file_path": file_name, "content": initial_content})

    # 1. 단일 매칭 부분 치환
    edit_res = editor.execute({
        "file_path": file_name,
        "target_content": "param_b: 20",
        "replacement_content": "param_b: 99"
    })
    assert edit_res["success"] is True

    # 2. 다중 매칭 시 안전 에러 반환 검증
    edit_res_dup = editor.execute({
        "file_path": file_name,
        "target_content": "param_a: ",
        "replacement_content": "param_a_new: "
    })
    assert edit_res_dup["success"] is False
    assert "교체 대상" in edit_res_dup["message"] or "allow_multiple" in edit_res_dup["message"]

    # 3. allow_multiple=True 일괄 치환 검증
    edit_res_multi = editor.execute({
        "file_path": file_name,
        "target_content": "param_a: ",
        "replacement_content": "param_a_new: ",
        "allow_multiple": True
    })
    assert edit_res_multi["success"] is True


def test_run_script_python_and_bash(tmp_path):
    workspace = str(tmp_path / "workspace")
    os.makedirs(workspace, exist_ok=True)
    node = MockNode(workspace_dir=workspace)

    writer = WriteTextFileSkill()
    writer.set_node(node)

    runner = RunScriptSkill()
    runner.set_node(node)

    # 1. 파이썬 스크립트 실행 검증
    py_script = "test_script.py"
    py_content = "import sys\nprint('Hello from Python')\nsys.exit(0)"
    writer.execute({"file_path": py_script, "content": py_content})

    py_res = runner.execute({"script_name": py_script})
    assert py_res["success"] is True
    assert "Hello from Python" in py_res["stdout"]
    assert py_res["returncode"] == 0

    # 2. 파이썬 에러 코드 검증
    py_fail_script = "fail_script.py"
    py_fail_content = "import sys\nsys.stderr.write('Error msg\\n')\nsys.exit(42)"
    writer.execute({"file_path": py_fail_script, "content": py_fail_content})

    py_fail_res = runner.execute({"script_name": py_fail_script})
    assert py_fail_res["success"] is False
    assert py_fail_res["returncode"] == 42
    assert "Error msg" in py_fail_res["stderr"]

    # 3. 배시 스크립트 실행 검증
    sh_script = "test_script.sh"
    sh_content = "echo 'Hello from Bash'\nexit 0"
    writer.execute({"file_path": sh_script, "content": sh_content})

    sh_res = runner.execute({"script_name": sh_script})
    assert sh_res["success"] is True
    assert "Hello from Bash" in sh_res["stdout"]
    assert sh_res["returncode"] == 0


def test_unsafe_actions_blocked(tmp_path):
    workspace = str(tmp_path / "workspace")
    os.makedirs(workspace, exist_ok=True)
    node = MockNode(workspace_dir=workspace)

    writer = WriteTextFileSkill()
    writer.set_node(node)

    reader = ReadTextFileSkill()
    reader.set_node(node)

    # /etc/passwd 처럼 허용되지 않은 상위 경로 파일 제어 요청 차단 검증
    res_write = writer.execute({"file_path": "/etc/passwd", "content": "malicious"})
    assert res_write["success"] is False

    res_read = reader.execute({"file_path": "/etc/passwd"})
    assert res_read["success"] is False
