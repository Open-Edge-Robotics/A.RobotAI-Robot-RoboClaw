import logging
import os
import subprocess
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)


def _get_node_butler_script_dir(skill: BaseSkill) -> str:
    """Butler 스크립트 경로는 파라미터 재정의가 아닌 노드 설정값만 신뢰한다."""
    node = getattr(skill, "node", None)
    if node is not None:
        try:
            if node.has_parameter("butler_script_dir"):
                val = node.get_parameter("butler_script_dir").get_parameter_value().string_value
                if val:
                    return val
        except Exception:
            pass
    return "/ros2_ws/butler_scripts"


class ListButlerScriptsSkill(BaseSkill):
    """Butler 로봇 스크립트 목록 조회 스킬"""

    name = "list_butler_scripts"
    answer_mode = "informational"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    description = "실행 가능한 Butler 로봇 전용 스크립트 목록을 조회합니다."

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        script_dir = _get_node_butler_script_dir(self)

        if not os.path.exists(script_dir):
            return {
                "success": False,
                "message": f"스크립트 디렉토리를 찾을 수 없습니다: {script_dir}",
            }

        try:
            files = [
                f
                for f in os.listdir(script_dir)
                if f.endswith(".sh") and os.path.isfile(os.path.join(script_dir, f))
            ]
            return {
                "success": True,
                "message": f"{len(files)}개의 스크립트를 찾았습니다.",
                "script_dir": script_dir,
                "scripts": sorted(files),
            }
        except Exception as e:
            return {"success": False, "message": f"목록 조회 오류: {e}"}


class RunButlerScriptSkill(BaseSkill):
    """Butler 로봇 스크립트 실행 스킬"""

    name = "run_butler_script"
    input_schema = {
        "type": "object",
        "properties": {"script_name": {"type": "string"}},
        "required": ["script_name"],
        "additionalProperties": False,
    }
    description = (
        "지정된 Butler 로봇 쉘 스크립트를 실행합니다. "
        "script_name 파라미터가 필요하며, 보안을 위해 지정된 폴더 내부의 파일만 실행 가능합니다."
    )
    risk_level = "dangerous"
    allow_with_others = False

    def validate_params(self, params: dict[str, Any]) -> bool:
        script_name = str(params.get("script_name", "")).strip()
        if not script_name:
            return False
        # 경로 조작 방지 및 확장자 검사
        if ".." in script_name or script_name.startswith("/") or not script_name.endswith(".sh"):
            return False
        return True

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        script_dir = _get_node_butler_script_dir(self)
        script_name = str(params.get("script_name", "")).strip()
        timeout_sec = float(params.get("timeout_sec", 60.0))

        if not self.validate_params(params):
            return {
                "success": False,
                "message": f"유효하지 않은 스크립트 이름입니다 (.sh 확장자 필요): {script_name}",
            }

        full_path = os.path.join(script_dir, script_name)
        real_script_path = os.path.realpath(full_path)
        real_script_dir = os.path.realpath(script_dir)

        if not real_script_path.startswith(real_script_dir):
            return {
                "success": False,
                "message": f"허용되지 않은 스크립트 경로입니다: {script_name}",
            }

        if not os.path.exists(real_script_path) or not os.path.isfile(real_script_path):
            return {
                "success": False,
                "message": f"스크립트 파일을 찾을 수 없습니다: {full_path}",
            }

        logger.info("Executing butler script: %s", full_path)

        try:
            result = subprocess.run(
                ["/bin/bash", real_script_path],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                cwd=real_script_dir,
            )

            stdout_str = result.stdout.strip()
            stderr_str = result.stderr.strip()

            success = result.returncode == 0
            return {
                "success": success,
                "message": "실행 완료" if success else "실행 실패",
                "script_name": script_name,
                "stdout": stdout_str[:2000],
                "stderr": stderr_str[:2000],
                "returncode": result.returncode,
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "message": f"실행 시간 초과 ({timeout_sec}초)",
                "script_name": script_name,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"실행 중 오류: {e}",
                "script_name": script_name,
            }
