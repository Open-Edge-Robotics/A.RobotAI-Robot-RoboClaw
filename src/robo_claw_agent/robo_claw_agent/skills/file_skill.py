"""
파일 처리 스킬 — 메신저 등을 통해 전달받은 로컬 파일 관리 및 이미지 분석
"""

import base64
import logging
import os
import subprocess
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .path_utils import _is_safe_path, _resolve_and_validate_path  # noqa: F401

logger = logging.getLogger(__name__)


class AnalyzeStoredFileSkill(BaseSkill):
    """로컬에 저장된 이미지 파일을 분석하는 스킬"""

    name = "analyze_stored_file"
    answer_mode = "informational"
    description = (
        "로컬 경로에 저장된 이미지 파일(JPG, PNG 등)을 읽어 VLM(Vision-Language Model)으로 분석합니다. "
        "메신저로부터 전달받은 사진의 내용을 파악할 때 사용합니다."
    )
    input_schema = {
        "type": "object",
        "properties": {"file_path": {"type": "string"}, "prompt": {"type": "string"}},
        "required": ["file_path"],
        "additionalProperties": False,
    }

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        file_path = str(params.get("file_path") or "").strip()
        prompt = params.get("prompt", "이 이미지에 무엇이 있는지 자세히 설명해줘.")

        if not file_path:
            return {"success": False, "message": "file_path 파라미터가 유효하지 않습니다."}

        abs_path, is_safe = _resolve_and_validate_path(file_path, self.node)
        if not is_safe:
            return {
                "success": False,
                "message": f"허용되지 않은 파일 경로이거나 접근 권한이 없습니다: {file_path}",
            }

        if not os.path.exists(abs_path):
            return {
                "success": False,
                "message": f"파일을 찾을 수 없습니다: {file_path}",
            }

        logger.info("Starting local image analysis: %s", abs_path)

        try:
            file_size = os.path.getsize(abs_path)
            if file_size > 20 * 1024 * 1024:  # 20MB limit
                return {
                    "success": False,
                    "message": f"이미지 파일 크기가 너무 큽니다 ({file_size / (1024 * 1024):.1f}MB > 20MB 제한)",
                }
            with open(abs_path, "rb") as f:
                img_data = f.read()
                img_base64 = base64.b64encode(img_data).decode("utf-8")
        except Exception as e:
            logger.error("Failed to read image: %s", e)
            return {"success": False, "message": f"이미지 읽기 오류: {e}"}

        analysis_text = "분석 실패"
        if self.node and hasattr(self.node, "_llm") and self.node._llm:
            try:
                analysis_text = self.node._llm.analyze_image(prompt, img_base64)
            except Exception as e:
                logger.error("Error during VLM analysis: %s", e)
                return {"success": False, "message": f"VLM 분석 오류: {e}"}
        else:
            return {
                "success": False,
                "message": "에이전트에 LLM이 설정되지 않았습니다.",
            }

        return {
            "success": True,
            "message": "이미지 분석 완료",
            "analysis": analysis_text,
        }


class ListFilesSkill(BaseSkill):
    """특정 디렉토리의 파일 목록을 조회하는 스킬"""

    name = "list_files"
    answer_mode = "informational"
    description = "에이전트의 작업 디렉토리 또는 특정 경로에 있는 파일 목록을 조회합니다."
    input_schema = {
        "type": "object",
        "properties": {"dir_path": {"type": "string", "default": "/tmp/robo_claw_files"}},
        "additionalProperties": False,
    }

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        dir_path = str(params.get("dir_path", "/tmp/robo_claw_files")).strip()

        abs_path, is_safe = _resolve_and_validate_path(dir_path, self.node)
        if not is_safe:
            return {
                "success": False,
                "message": f"허용되지 않은 디렉토리 경로이거나 접근 권한이 없습니다: {dir_path}",
            }

        if not os.path.exists(abs_path):
            try:
                os.makedirs(abs_path, exist_ok=True)
            except Exception as e:
                return {"success": False, "message": f"디렉토리 생성 실패: {e}"}

        try:
            files = os.listdir(abs_path)
            file_infos = []
            for f in files:
                full_path = os.path.join(abs_path, f)
                stat = os.stat(full_path)
                file_infos.append(
                    {
                        "name": f,
                        "size_bytes": stat.st_size,
                        "created_at": stat.st_ctime,
                        "is_file": os.path.isfile(full_path),
                    }
                )

            return {
                "success": True,
                "message": f"{len(file_infos)}개의 파일을 찾았습니다.",
                "dir_path": abs_path,
                "files": file_infos,
            }
        except Exception as e:
            logger.error("Failed to list files: %s", e)
            return {"success": False, "message": f"파일 목록 조회 오류: {e}"}


class DeleteFileSkill(BaseSkill):
    """로컬 파일을 삭제하는 스킬"""

    name = "delete_file"
    description = "분석이 완료되었거나 더 이상 필요 없는 로컬 파일을 삭제합니다."
    input_schema = {
        "type": "object",
        "properties": {"file_path": {"type": "string"}},
        "required": ["file_path"],
        "additionalProperties": False,
    }

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        file_path = str(params.get("file_path", "")).strip()

        if not file_path:
            return {"success": False, "message": "file_path 파라미터가 유효하지 않습니다."}

        abs_path, is_safe = _resolve_and_validate_path(file_path, self.node)
        if not is_safe:
            return {
                "success": False,
                "message": f"허용되지 않은 파일 경로이거나 삭제 권한이 없습니다: {file_path}",
            }

        if not os.path.exists(abs_path):
            return {
                "success": False,
                "message": f"파일을 찾을 수 없거나 이미 삭제되었습니다: {file_path}",
            }

        try:
            os.remove(abs_path)
            logger.info("File deleted: %s", abs_path)
            return {
                "success": True,
                "message": f"파일을 성공적으로 삭제했습니다: {file_path}",
            }
        except Exception as e:
            logger.error("Failed to delete file: %s", e)
            return {"success": False, "message": f"파일 삭제 오류: {e}"}


class ReadTextFileSkill(BaseSkill):
    """로컬 텍스트 파일의 내용을 읽는 스킬"""

    name = "read_text_file"
    answer_mode = "informational"
    description = (
        "로컬 텍스트 파일의 내용을 읽어옵니다. "
        "상대 경로는 에이전트 작업 공간 기준이며, 특정 라인 범위만 지정하여 읽을 수도 있습니다."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
            "encoding": {"type": "string", "default": "utf-8"},
        },
        "required": ["file_path"],
        "additionalProperties": False,
    }

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        file_path = params.get("file_path", "").strip()
        start_line = params.get("start_line")
        end_line = params.get("end_line")
        encoding = params.get("encoding", "utf-8")

        if not file_path:
            return {
                "success": False,
                "message": "file_path 파라미터가 유효하지 않습니다.",
            }

        abs_path, is_safe = _resolve_and_validate_path(file_path, self.node)
        if not is_safe:
            return {
                "success": False,
                "message": f"허용되지 않은 파일 경로이거나 접근 권한이 없습니다: {file_path}",
            }

        if not os.path.exists(abs_path):
            return {"success": False, "message": f"파일을 찾을 수 없습니다: {abs_path}"}

        if not os.path.isfile(abs_path):
            return {
                "success": False,
                "message": f"디렉토리는 읽을 수 없습니다: {abs_path}",
            }

        try:
            with open(abs_path, encoding=encoding, errors="replace") as f:
                lines = f.readlines()

            total_lines = len(lines)

            if start_line is not None or end_line is not None:
                start = int(start_line) - 1 if start_line is not None else 0
                end = int(end_line) if end_line is not None else total_lines

                start = max(0, min(start, total_lines))
                end = max(start, min(end, total_lines))

                content = "".join(lines[start:end])
                message = f"파일 {abs_path}의 {start + 1}번째부터 {end}번째 라인까지 읽었습니다."
            else:
                content = "".join(lines)
                message = f"파일 {abs_path}의 전체 내용을 읽었습니다."

            return {
                "success": True,
                "message": message,
                "content": content,
                "total_lines": total_lines,
            }
        except Exception as e:
            logger.error("File read error: %s", e)
            return {"success": False, "message": f"파일 읽기 중 오류 발생: {e}"}


class WriteTextFileSkill(BaseSkill):
    """로컬 텍스트 파일을 새로 작성하거나 덮어쓰는 스킬"""

    name = "write_text_file"
    description = (
        "텍스트 파일에 내용을 작성하여 저장합니다. 필요한 상위 폴더는 자동으로 생성됩니다."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "content": {"type": "string"},
            "overwrite": {"type": "boolean", "default": True},
            "encoding": {"type": "string", "default": "utf-8"},
        },
        "required": ["file_path", "content"],
        "additionalProperties": False,
    }

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        file_path = params.get("file_path", "").strip()
        content = params.get("content", "")
        overwrite = params.get("overwrite", True)
        encoding = params.get("encoding", "utf-8")

        if not file_path:
            return {
                "success": False,
                "message": "file_path 파라미터가 유효하지 않습니다.",
            }

        abs_path, is_safe = _resolve_and_validate_path(file_path, self.node)
        if not is_safe:
            return {
                "success": False,
                "message": f"허용되지 않은 파일 경로이거나 접근 권한이 없습니다: {file_path}",
            }

        if not overwrite and os.path.exists(abs_path):
            return {
                "success": False,
                "message": f"파일이 이미 존재하며 덮어쓰기가 비활성화되어 있습니다: {abs_path}",
            }

        try:
            dir_path = os.path.dirname(abs_path)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path, exist_ok=True)

            with open(abs_path, "w", encoding=encoding) as f:
                f.write(content)

            return {
                "success": True,
                "message": f"파일 작성을 성공적으로 완료했습니다: {abs_path}",
            }
        except Exception as e:
            logger.error("File write error: %s", e)
            return {"success": False, "message": f"파일 쓰기 중 오류 발생: {e}"}


class EditTextFileSkill(BaseSkill):
    """로컬 텍스트 파일의 특정 영역을 편집(치환)하는 스킬"""

    name = "edit_text_file"
    description = (
        "파일의 특정 텍스트 블록(target_content)을 찾아 새로운 내용(replacement_content)으로 교체합니다. "
        "정확성을 높이기 위해 매칭 대상이 단 1개인 상태로 편집하는 것을 권장합니다."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "target_content": {"type": "string"},
            "replacement_content": {"type": "string"},
            "allow_multiple": {"type": "boolean", "default": False},
        },
        "required": ["file_path", "target_content", "replacement_content"],
        "additionalProperties": False,
    }

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        file_path = params.get("file_path", "").strip()
        target_content = params.get("target_content")
        replacement_content = params.get("replacement_content")
        allow_multiple = params.get("allow_multiple", False)
        encoding = params.get("encoding", "utf-8")

        if not file_path:
            return {
                "success": False,
                "message": "file_path 파라미터가 유효하지 않습니다.",
            }
        if target_content is None or replacement_content is None:
            return {
                "success": False,
                "message": "target_content와 replacement_content 파라미터가 필요합니다.",
            }

        abs_path, is_safe = _resolve_and_validate_path(file_path, self.node)
        if not is_safe:
            return {
                "success": False,
                "message": f"허용되지 않은 파일 경로이거나 접근 권한이 없습니다: {file_path}",
            }

        if not os.path.exists(abs_path):
            return {
                "success": False,
                "message": f"편집할 파일을 찾을 수 없습니다: {abs_path}",
            }

        try:
            with open(abs_path, encoding=encoding, errors="replace") as f:
                content = f.read()

            occurrences = content.count(target_content)
            if occurrences == 0:
                return {
                    "success": False,
                    "message": "파일 내용 중 교체 대상(target_content)을 찾을 수 없습니다. 정확히 일치하는지 확인해 주세요.",
                }

            if occurrences > 1 and not allow_multiple:
                return {
                    "success": False,
                    "message": (
                        f"교체 대상(target_content)이 파일 내에 {occurrences}번 존재합니다. "
                        "안전을 위해 하나의 대상만 편집하거나, allow_multiple 옵션을 True로 설정하여 일괄 치환해 주세요."
                    ),
                }

            new_content = content.replace(target_content, replacement_content)

            with open(abs_path, "w", encoding=encoding) as f:
                f.write(new_content)

            return {
                "success": True,
                "message": f"파일 편집을 성공적으로 완료했습니다. (치환 횟수: {occurrences}회)",
            }
        except Exception as e:
            logger.error("File edit error: %s", e)
            return {"success": False, "message": f"파일 편집 중 오류 발생: {e}"}


class RunScriptSkill(BaseSkill):
    """에이전트 작업 공간 내에 있는 파이썬 또는 쉘 스크립트를 실행하는 스킬"""

    name = "run_script"
    description = (
        "에이전트 전용 작업 공간 또는 허용된 경로 내의 스크립트(.py, .sh)를 안전하게 실행합니다. "
        "script_name과 인수 목록(args)을 전달할 수 있습니다."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "script_name": {"type": "string"},
            "args": {"type": "array", "items": {"type": "string"}},
            "timeout_sec": {"type": "number"},
        },
        "required": ["script_name"],
        "additionalProperties": False,
    }

    def validate_params(self, params: dict[str, Any]) -> bool:
        script_name = params.get("script_name", "").strip()
        if not script_name:
            return False
        if ".." in script_name:
            return False
        return True

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        script_name = params.get("script_name", "").strip()
        args = params.get("args", [])
        timeout_sec = float(params.get("timeout_sec", 60.0))

        if not isinstance(args, list):
            return {"success": False, "message": "args는 리스트 형식이어야 합니다."}

        workspace = "/ros2_ws/agent_workspace"
        if (
            self.node
            and hasattr(self.node, "_agent_workspace_dir")
            and self.node._agent_workspace_dir
        ):
            workspace = self.node._agent_workspace_dir

        full_path = os.path.join(workspace, script_name)
        abs_path = os.path.abspath(full_path)

        if not _is_safe_path(abs_path, self.node):
            return {
                "success": False,
                "message": f"허용되지 않은 경로의 스크립트 실행 시도입니다: {script_name}",
            }

        if not os.path.exists(abs_path):
            return {
                "success": False,
                "message": f"스크립트 파일을 찾을 수 없습니다: {abs_path}",
            }

        _, ext = os.path.splitext(abs_path)
        ext = ext.lower()

        if ext == ".py":
            cmd = ["python3", abs_path]
        elif ext == ".sh":
            cmd = ["/bin/bash", abs_path]
        else:
            return {
                "success": False,
                "message": f"지원되지 않는 스크립트 확장자입니다. (.py 및 .sh 파일만 실행 가능): {ext}",
            }

        cmd.extend([str(arg) for arg in args])

        logger.info("Starting agent script execution: %s (args=%s)", abs_path, args)

        try:
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                cwd=workspace,
            )

            success = result.returncode == 0
            return {
                "success": success,
                "message": "스크립트 실행 완료"
                if success
                else "스크립트 실행 실패 (Returncode != 0)",
                "stdout": result.stdout.strip()[:4000],
                "stderr": result.stderr.strip()[:4000],
                "returncode": result.returncode,
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "message": f"스크립트 실행이 제한 시간({timeout_sec}초)을 초과하여 종료되었습니다.",
            }
        except Exception as e:
            logger.error("Script execution error: %s", e)
            return {"success": False, "message": f"스크립트 실행 중 오류 발생: {e}"}
