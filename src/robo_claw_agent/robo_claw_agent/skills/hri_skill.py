"""
HRI(Human-Robot Interaction) 스킬 — 메신저 메시지 전송
"""

import logging
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)


class SendMessageSkill(BaseSkill):
    """사용자 메신저(Telegram, Slack 등)로 메시지나 파일을 전송하는 스킬"""

    name = "send_message"
    input_schema = {
        "type": "object",
        "properties": {
            "message": {"type": ["string", "null"]},
            "file_path": {"type": ["string", "null"]},
        },
        "anyOf": [{"required": ["message"]}, {"required": ["file_path"]}],
        "additionalProperties": False,
    }
    description = (
        "활성화된 메신저 채널로 텍스트 메시지나 로컬 파일을 전송합니다. "
        "사용자에게 보고를 하거나, 생성된 맵/스냅샷 이미지를 보낼 때 사용합니다. "
        "파라미터: message (텍스트), file_path (선택적 파일 경로)"
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        import os

        from robo_claw_agent.skills.path_utils import _resolve_and_validate_path

        message = str(params.get("message") or "").strip()
        file_path = str(params.get("file_path") or "").strip()

        if file_path:
            abs_path, is_safe = _resolve_and_validate_path(file_path, self.node)
            if not is_safe:
                return {
                    "success": False,
                    "message": f"허용되지 않은 파일 경로이거나 접근 권한이 없습니다: {file_path}",
                }
            if not os.path.exists(abs_path):
                return {
                    "success": False,
                    "message": f"전송할 파일이 존재하지 않습니다: {file_path}",
                }
            file_path = abs_path

        if not message and not file_path:
            return {
                "success": False,
                "message": "전송할 내용(메시지 또는 파일)이 없습니다.",
            }

        if not self.node or not hasattr(self.node, "_send_msg_client"):
            return {
                "success": False,
                "message": "에이전트 노드에 전송 클라이언트가 설정되지 않았습니다.",
            }

        logger.info("Messenger send request: msg=%s, file=%s", message, file_path)

        from robo_claw_msgs.srv import SendMessage

        req = SendMessage.Request()
        req.message = message
        req.file_path = file_path

        ok, resp = self.call_service(
            SendMessage, request=req, client=self.node._send_msg_client, timeout_sec=10.0
        )
        if not ok:
            return {
                "success": False,
                "message": "메시지 전송 서비스 호출 실패 (서비스 준비 안됨 또는 타임아웃)",
            }
        if resp and resp.success:
            return {"success": True, "message": "메신저로 전송 완료"}
        err = resp.error_message if resp else "응답 없음"
        return {"success": False, "message": f"전송 실패: {err}"}
