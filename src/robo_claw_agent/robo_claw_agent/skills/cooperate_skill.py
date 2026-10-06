"""
멀티 에이전트 협업 스킬 — 타 에이전트와 통신 및 태스크 요청
"""

import logging
import threading
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)

# call_peer_robot/query_peer_status에서 timeout_sec 미지정 시, 로컬 ROS2 future 대기 상한.
# 실제 gRPC 데드라인은 MessengerClientNode의 request_timeout_sec 파라미터(서버 측 설정)를 그대로
# 신뢰하도록 req.timeout_sec=0을 보내고, 여기서는 그 서버 설정이 병목이 되지 않도록 충분히 크게 잡는다.
_DEFAULT_PEER_CALL_LOCAL_WAIT_SEC = 300.0
# timeout_sec를 명시한 경우, 로컬 대기가 서버 측 gRPC 데드라인과 정확히 같은 값으로 경합하지 않도록
# 더해주는 여유 시간.
_SERVICE_CALL_MARGIN_SEC = 5.0


def _local_wait_sec(timeout_sec: float) -> float:
    """peer 호출용 로컬 ROS2 future 대기 시간을 계산한다."""
    return (
        timeout_sec + _SERVICE_CALL_MARGIN_SEC if timeout_sec else _DEFAULT_PEER_CALL_LOCAL_WAIT_SEC
    )


def _wait_for_future(future: Any, timeout_sec: float, message: str) -> tuple[bool, Any]:
    done = threading.Event()
    future.add_done_callback(lambda _: done.set())
    if not done.wait(timeout=timeout_sec):
        return False, message
    return True, future.result()


def _call_service(
    client: Any,
    request: Any,
    call_timeout_sec: float,
    not_found_message: str,
    timeout_message: str,
) -> tuple[bool, Any]:
    """ROS2 서비스 클라이언트로 요청을 보내고 응답(또는 에러 메시지)을 기다린다."""
    if not client.wait_for_service(timeout_sec=5.0):
        return False, not_found_message
    future = client.call_async(request)
    return _wait_for_future(future, call_timeout_sec, timeout_message)


class DelegateTaskSkill(BaseSkill):
    """특정 에이전트에게 작업을 위임하는 스킬"""

    name = "delegate_task"
    input_schema = {
        "type": "object",
        "properties": {"agent_id": {"type": "string"}, "instruction": {"type": "string"}},
        "required": ["agent_id", "instruction"],
        "additionalProperties": False,
    }
    description = (
        "다른 RoboClaw 에이전트에게 자연어 작업을 위임하고 결과를 기다립니다. "
        "`agent_id`와 `instruction`이 필요합니다."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        target_agent = params.get("agent_id", "")
        instruction = params.get("instruction", "")

        if not target_agent or not instruction:
            return {
                "success": False,
                "message": "agent_id와 instruction 파라미터가 필요합니다.",
            }

        logger.info("Delegating task to agent [%s]: %s", target_agent, instruction)

        from rclpy.action import ActionClient  # type: ignore[import]

        from robo_claw_msgs.action import ExecuteTask

        action_name = f"{target_agent}/execute_task"
        if not action_name.startswith("/"):
            action_name = f"/{action_name}"

        client = ActionClient(self.node, ExecuteTask, action_name)
        try:
            if not client.wait_for_server(timeout_sec=5.0):
                return {
                    "success": False,
                    "message": f"에이전트 [{target_agent}] 서버를 찾을 수 없습니다.",
                }

            goal_msg = ExecuteTask.Goal()
            goal_msg.instruction = instruction

            send_goal_future = client.send_goal_async(goal_msg)
            ok, goal_or_message = _wait_for_future(
                send_goal_future,
                timeout_sec=10.0,
                message="에이전트 요청 시간 초과",
            )
            if not ok:
                return {"success": False, "message": str(goal_or_message)}

            goal_handle = goal_or_message
            if not goal_handle:
                return {"success": False, "message": "에이전트 요청 실패"}

            if not goal_handle.accepted:
                return {"success": False, "message": "에이전트가 작업을 거절했습니다."}

            get_result_future = goal_handle.get_result_async()
            ok, result_or_message = _wait_for_future(
                get_result_future,
                timeout_sec=60.0,
                message="에이전트 작업 실행 중 시간 초과",
            )
            if not ok:
                return {
                    "success": False,
                    "message": str(result_or_message),
                    "agent_id": target_agent,
                    "remote_state": "unknown",
                    "side_effect_state": "unknown",
                    "timeout": True,
                }

            result = result_or_message.result

            return {
                "success": result.success,
                "message": f"에이전트 [{target_agent}] 응답: {result.result_message}",
                "agent_id": target_agent,
                "response": result.result_message,
            }
        finally:
            client.destroy()


class CallPeerRobotSkill(BaseSkill):
    """gRPC로 연결된 동료 로봇(다른 ROS 네트워크의 RoboClaw)을 이름으로 지정해 호출/메시지 전달하는 스킬"""

    name = "call_peer_robot"
    input_schema = {
        "type": "object",
        "properties": {
            "peer_name": {"type": "string"},
            "instruction": {"type": "string"},
            "file_path": {"type": "string"},
            "wait_for_result": {"type": "boolean", "default": True},
            "timeout_sec": {"type": "number"},
        },
        "required": ["peer_name"],
        "anyOf": [{"required": ["instruction"]}, {"required": ["file_path"]}],
        "additionalProperties": False,
    }
    description = (
        "target_peers_json으로 연결된 특정 동료 로봇(peer_name)에게 자연어 지시나 메시지를 "
        "전달합니다. delegate_task와 달리 같은 ROS 네트워크가 아니어도(gRPC로 원격 연결된 로봇) "
        "동작합니다. 파라미터: peer_name(필수), instruction(지시/메시지, file_path만 보낼 경우 생략 가능), "
        "file_path(선택, 함께 보낼 파일), wait_for_result(선택, 기본 true - false면 응답을 "
        "기다리지 않고 즉시 반환), timeout_sec(선택)"
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        peer_name = params.get("peer_name", "")
        instruction = params.get("instruction", "")
        file_path = params.get("file_path", "")
        wait_for_result = params.get("wait_for_result", True)
        timeout_sec = float(params.get("timeout_sec", 0.0) or 0.0)

        if not peer_name or (not instruction and not file_path):
            return {
                "success": False,
                "message": "peer_name과 instruction(또는 file_path)이 필요합니다.",
            }

        if not self.node or not hasattr(self.node, "_call_peer_client"):
            return {
                "success": False,
                "message": "에이전트 노드에 피어 호출 클라이언트가 설정되지 않았습니다.",
            }

        logger.info(
            "Calling peer robot [%s]: instruction=%s, file=%s, wait_for_result=%s",
            peer_name,
            instruction,
            file_path,
            wait_for_result,
        )

        from robo_claw_msgs.srv import CallPeerRobot

        req = CallPeerRobot.Request()
        req.peer_name = peer_name
        req.instruction = instruction
        req.file_path = file_path
        req.wait_for_result = bool(wait_for_result)
        req.timeout_sec = timeout_sec

        ok, resp = _call_service(
            self.node._call_peer_client,
            req,
            call_timeout_sec=_local_wait_sec(timeout_sec),
            not_found_message="채널 노드의 CallPeerRobot 서비스를 찾을 수 없습니다.",
            timeout_message="동료 로봇 호출 시간 초과",
        )
        if not ok:
            return {"success": False, "message": str(resp)}

        return {
            "success": bool(resp.success),
            "message": resp.result_message or resp.error_message or "응답 없음",
            "peer_name": peer_name,
            "response": resp.result_message,
        }


class ListPeerRobotsSkill(BaseSkill):
    """target_peers_json으로 설정된 동료 로봇 목록과 연결 상태를 조회하는 스킬"""

    name = "list_peer_robots"
    answer_mode = "informational"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    risk_level = "read"
    description = (
        "target_peers_json으로 연결이 설정된 동료 로봇들의 이름(peer_name)과 현재 gRPC 연결 상태를 "
        "조회합니다. call_peer_robot이나 query_peer_status를 쓰기 전 정확한 peer_name을 모르면 "
        "먼저 이 스킬로 확인하세요. 파라미터가 필요 없습니다."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.node or not hasattr(self.node, "_list_peers_client"):
            return {
                "success": False,
                "message": "에이전트 노드에 피어 목록 조회 클라이언트가 설정되지 않았습니다.",
            }

        from robo_claw_msgs.srv import ListPeers

        req = ListPeers.Request()

        ok, resp = _call_service(
            self.node._list_peers_client,
            req,
            call_timeout_sec=10.0,
            not_found_message="채널 노드의 ListPeers 서비스를 찾을 수 없습니다.",
            timeout_message="피어 목록 조회 시간 초과",
        )
        if not ok:
            return {"success": False, "message": str(resp)}

        if not resp.success:
            return {
                "success": False,
                "message": resp.error_message or "피어 목록 조회 실패",
            }

        peers = [
            {
                "peer_name": p.peer_name,
                "host": p.host,
                "port": p.port,
                "connected": p.connected,
            }
            for p in resp.peers
        ]
        connected_count = sum(1 for p in peers if p["connected"])
        return {
            "success": True,
            "message": f"동료 로봇 {len(peers)}대 중 {connected_count}대 연결됨",
            "peers": peers,
        }


class BroadcastToPeersSkill(BaseSkill):
    """연결된 모든 동료 로봇에게 동시에 메시지/지시를 전달하는 스킬"""

    name = "broadcast_to_peers"
    input_schema = {
        "type": "object",
        "properties": {"message": {"type": "string"}, "file_path": {"type": "string"}},
        "anyOf": [{"required": ["message"]}, {"required": ["file_path"]}],
        "additionalProperties": False,
    }
    description = (
        "target_peers_json으로 연결된 모든 동료 로봇에게 동일한 메시지나 지시를 동시에 전달합니다. "
        "특정 로봇 한 곳만 지정하려면 call_peer_robot을 사용하세요. "
        "파라미터: message(필수), file_path(선택, 함께 보낼 파일)"
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        message = params.get("message", "")
        file_path = params.get("file_path", "")

        if not message and not file_path:
            return {
                "success": False,
                "message": "message 또는 file_path가 필요합니다.",
            }

        if not self.node or not hasattr(self.node, "_broadcast_peers_client"):
            return {
                "success": False,
                "message": "에이전트 노드에 피어 브로드캐스트 클라이언트가 설정되지 않았습니다.",
            }

        logger.info("Broadcasting to all peer robots: message=%s, file=%s", message, file_path)

        from robo_claw_msgs.srv import SendMessage

        req = SendMessage.Request()
        req.message = message
        req.file_path = file_path

        ok, resp = _call_service(
            self.node._broadcast_peers_client,
            req,
            call_timeout_sec=15.0,
            not_found_message="채널 노드의 SendMessage(피어 브로드캐스트) 서비스를 찾을 수 없습니다.",
            timeout_message="동료 로봇 브로드캐스트 시간 초과",
        )
        if not ok:
            return {"success": False, "message": str(resp)}

        return {
            "success": bool(resp.success),
            "message": "동료 로봇 전체 브로드캐스트 완료"
            if resp.success
            else (resp.error_message or "브로드캐스트 실패"),
        }


class QueryPeerStatusSkill(BaseSkill):
    """특정 동료 로봇의 배터리/위치 등 상태를 물어보는 스킬"""

    name = "query_peer_status"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {"peer_name": {"type": "string"}, "timeout_sec": {"type": "number"}},
        "required": ["peer_name"],
        "additionalProperties": False,
    }
    risk_level = "read"
    description = (
        "특정 동료 로봇(peer_name)에게 현재 배터리 잔량, 위치, 수행 중인 작업 여부를 물어보고 "
        "자연어 응답을 받습니다. call_peer_robot을 상태 조회용으로 미리 정형화한 스킬입니다. "
        "파라미터: peer_name(필수), timeout_sec(선택)"
    )

    _STATUS_INSTRUCTION = (
        "너의 현재 상태(배터리 잔량, 위치, 수행 중인 작업 유무)를 간결한 한국어 문장으로 알려줘. "
        "가능하다면 사용 가능한 스킬 이름도 함께 알려줘."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        peer_name = params.get("peer_name", "")
        timeout_sec = float(params.get("timeout_sec", 0.0) or 0.0)

        if not peer_name:
            return {"success": False, "message": "peer_name이 필요합니다."}

        if not self.node or not hasattr(self.node, "_call_peer_client"):
            return {
                "success": False,
                "message": "에이전트 노드에 피어 호출 클라이언트가 설정되지 않았습니다.",
            }

        logger.info("Querying status of peer robot [%s]", peer_name)

        from robo_claw_msgs.srv import CallPeerRobot

        req = CallPeerRobot.Request()
        req.peer_name = peer_name
        req.instruction = self._STATUS_INSTRUCTION
        req.file_path = ""
        req.wait_for_result = True
        req.timeout_sec = timeout_sec

        ok, resp = _call_service(
            self.node._call_peer_client,
            req,
            call_timeout_sec=_local_wait_sec(timeout_sec),
            not_found_message="채널 노드의 CallPeerRobot 서비스를 찾을 수 없습니다.",
            timeout_message="동료 로봇 상태 조회 시간 초과",
        )
        if not ok:
            return {"success": False, "message": str(resp)}

        return {
            "success": bool(resp.success),
            "message": resp.result_message or resp.error_message or "응답 없음",
            "peer_name": peer_name,
            "status_summary": resp.result_message,
        }


class QueryPeerCapabilitiesSkill(BaseSkill):
    """특정 동료 로봇의 사용 가능한 스킬(능력) 목록을 조회하는 스킬"""

    name = "query_peer_capabilities"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {"peer_name": {"type": "string"}, "timeout_sec": {"type": "number"}},
        "required": ["peer_name"],
        "additionalProperties": False,
    }
    risk_level = "read"
    description = (
        "특정 동료 로봇(peer_name)에게 사용 가능한 스킬 목록을 요청해 JSON 배열로 받습니다. "
        "coordinate_peer_task로 협업 분해를 하기 전 동료의 능력을 확인하거나, "
        "GRPC_TARGET_PEERS_JSON에 capabilities가 설정되지 않은 동료의 능력을 런타임에 파악할 때 사용합니다. "
        "파라미터: peer_name(필수), timeout_sec(선택)"
    )

    _CAPABILITIES_INSTRUCTION = (
        "너가 현재 사용할 수 있는 스킬 이름 목록을 JSON 배열 형식으로만 알려줘. "
        '예: ["navigate_to", "grasp", "place", "analyze_scene"]'
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        peer_name = params.get("peer_name", "")
        timeout_sec = float(params.get("timeout_sec", 0.0) or 0.0)

        if not peer_name:
            return {"success": False, "message": "peer_name이 필요합니다."}

        if not self.node or not hasattr(self.node, "_call_peer_client"):
            return {
                "success": False,
                "message": "에이전트 노드에 피어 호출 클라이언트가 설정되지 않았습니다.",
            }

        logger.info("Querying capabilities of peer robot [%s]", peer_name)

        from robo_claw_msgs.srv import CallPeerRobot

        req = CallPeerRobot.Request()
        req.peer_name = peer_name
        req.instruction = self._CAPABILITIES_INSTRUCTION
        req.file_path = ""
        req.wait_for_result = True
        req.timeout_sec = timeout_sec

        ok, resp = _call_service(
            self.node._call_peer_client,
            req,
            call_timeout_sec=_local_wait_sec(timeout_sec),
            not_found_message="채널 노드의 CallPeerRobot 서비스를 찾을 수 없습니다.",
            timeout_message="동료 로봇 능력 조회 시간 초과",
        )
        if not ok:
            return {"success": False, "message": str(resp)}

        if not resp.success:
            return {
                "success": False,
                "message": resp.error_message or resp.result_message or "동료 로봇 능력 조회 실패",
                "peer_name": peer_name,
                "capabilities": [],
            }

        # 동료가 JSON 배열로 응답했다면 파싱, 아니면 빈 리스트
        import json as _json
        import re as _re

        raw = resp.result_message or ""
        capabilities: list[str] = []
        try:
            clean = _re.sub(r"```(?:json)?\s*\n?", "", raw).strip()
            # 응답에서 JSON 배열 추출 시도
            match = _re.search(r"\[.*?\]", clean, _re.DOTALL)
            if match:
                capabilities = _json.loads(match.group(0))
            elif clean.startswith("["):
                capabilities = _json.loads(clean)
        except Exception:
            capabilities = []

        return {
            "success": True,
            "message": resp.result_message or "조회 완료",
            "peer_name": peer_name,
            "capabilities": capabilities,
        }


class CoordinatePeerTaskSkill(BaseSkill):
    """복합 명령을 단계별 서브태스크로 분해해 동료 로봇에게 순차 위임하는 스킬.

    예: '거실로 이동해 컵을 들고 주방에 놔줘' →
      1. 동료에게 '거실로 이동해' 전달
      2. 동료에게 '컵을 들어줘' 전달
      3. 동료에게 '주방으로 가줘' 전달
      4. 동료에게 '싱크대에 놔줘' 전달

    지시 로봇(자신)이 LLM으로 분해하고, 동료는 단순 명령만 처리한다.
    """

    name = "coordinate_peer_task"
    input_schema = {
        "type": "object",
        "properties": {
            "peer_name": {"type": "string"},
            "instruction": {"type": "string"},
            "timeout_sec": {"type": "number"},
            "max_retries": {"type": "integer", "default": 1},
        },
        "required": ["peer_name", "instruction"],
        "additionalProperties": False,
    }
    description = (
        "복합 명령(instruction)을 단계별 서브태스크로 분해해 동료 로봇에게 순차 전달합니다. "
        "지시 로봇(자신)이 LLM으로 명령을 분해하고, 각 단계를 동료에게 단순 명령으로 전달합니다. "
        "동료의 SLM으로도 단순 명령은 처리 가능하도록 설계되었습니다. "
        "파라미터: peer_name(필수), instruction(복합 명령, 필수), "
        "timeout_sec(선택, 각 단계 대기 시간), max_retries(선택, 단계 실패 시 재시도, 기본 1)"
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        peer_name = params.get("peer_name", "")
        instruction = params.get("instruction", "")
        timeout_sec = float(params.get("timeout_sec", 0.0) or 0.0)
        max_retries = int(params.get("max_retries", 1))

        if not peer_name:
            return {"success": False, "message": "peer_name이 필요합니다."}
        if not instruction:
            return {"success": False, "message": "instruction(복합 명령)이 필요합니다."}
        if not self.node or not getattr(self.node, "_llm", None):
            return {"success": False, "message": "LLM을 사용할 수 없습니다."}

        logger.info("Coordinating peer task: peer=%s, instruction=%s", peer_name, instruction)

        # 1. 복합 명령을 단계별 서브태스크로 분해
        steps = self._decompose_instruction(peer_name, instruction)
        if not steps:
            return {
                "success": False,
                "message": "명령 분해에 실패했습니다.",
            }

        logger.info("Decomposed into %d steps: %s", len(steps), steps)

        # 2. 각 단계를 동료에게 순차 전달
        results: list[dict[str, Any]] = []
        for i, step in enumerate(steps):
            step_instruction = step.get("instruction", "")
            if not step_instruction:
                continue

            logger.info(
                "CoordinatePeerTask: step %d/%d → %s: %s",
                i + 1,
                len(steps),
                peer_name,
                step_instruction,
            )
            self.send_user_message(
                f"[협업] {peer_name}에게 단계 {i + 1}/{len(steps)} 전달: {step_instruction}"
            )

            success = False
            attempt = 0
            last_msg = ""
            while attempt <= max_retries and not success:
                attempt += 1
                result = self._send_to_peer(peer_name, step_instruction, timeout_sec)
                success = result.get("success", False)
                last_msg = result.get("message", "")
                if success:
                    break
                if attempt <= max_retries:
                    logger.warning(
                        "Step %d attempt %d failed: %s — retrying",
                        i + 1,
                        attempt,
                        last_msg,
                    )

            results.append(
                {
                    "step": i + 1,
                    "instruction": step_instruction,
                    "success": success,
                    "response": last_msg,
                }
            )

            if not success:
                logger.error("Step %d failed after %d attempts — aborting", i + 1, attempt)
                self.send_user_message(f"[협업] 단계 {i + 1} 실패: {last_msg}. 중단합니다.")
                return {
                    "success": False,
                    "message": f"단계 {i + 1} 실패로 중단: {last_msg}",
                    "peer_name": peer_name,
                    "steps_completed": i,
                    "steps_total": len(steps),
                    "results": results,
                }

        self.send_user_message(f"[협업] {peer_name}에게 모든 단계({len(steps)}) 전달 완료.")
        return {
            "success": True,
            "message": f"{peer_name}에게 {len(steps)}단계 작업을 순차 전달 완료.",
            "peer_name": peer_name,
            "steps_completed": len(steps),
            "steps_total": len(steps),
            "results": results,
        }

    def _decompose_instruction(self, peer_name: str, instruction: str) -> list[dict[str, str]]:
        """LLM으로 복합 명령을 단계별 서브태스크로 분해한다."""
        import json as _json
        import re as _re

        from robo_claw_agent.agent_node.utils import _extract_first_json_object

        system_prompt = f"""당신은 로봇 협업 코디네이터입니다.
주어진 복합 명령을 동료 로봇이 한 번에 하나씩 수행할 수 있는 단순 명령으로 분해하세요.

동료 로봇: {peer_name} (SLM 기반으로 단순한 한 가지 동작 명령만 처리 가능)

[분해 규칙]
1. 복합 명령을 순차적으로 실행 가능한 단순 명령으로 쪼개세요.
2. 각 단계는 동료가 한 번에 처리할 수 있는 단위여야 합니다.
   - 예: "거실로 이동해 컵을 들고 주방에 놔줘" →
     ["거실로 이동해줘", "컵을 들어줘", "주방으로 가줘", "싱크대에 놔줘"]
3. 각 단계는 이전 단계의 완료를 전제로 합니다.
4. 최대 8단계까지만 분해하세요.
5. 명령이 이미 단순하면 1단계로 반환하세요.

[응답 형식 — 반드시 JSON만 출력]
{{"steps": [
  {{"instruction": "<단순 명령 1>"}},
  {{"instruction": "<단순 명령 2>"}},
  ...
]}}

JSON 외의 텍스트를 출력하지 마세요."""

        messages = [
            {
                "role": "user",
                "content": f"복합 명령: {instruction}\n\n이 명령을 동료 로봇 {peer_name}이 순차 실행할 수 있는 단순 명령으로 분해해주세요.",
            }
        ]

        try:
            response = self.node._llm.chat(messages, system_prompt=system_prompt)
            clean = _re.sub(r"```(?:json)?\s*\n?", "", response).strip()
            json_str = _extract_first_json_object(clean)
            if "{" not in json_str:
                logger.error("CoordinatePeerTask: no JSON in LLM response: %s", response)
                return []
            decision = _json.loads(json_str)
            steps = decision.get("steps", [])
            # 각 step이 dict이고 instruction 키가 있는지 검증
            validated: list[dict[str, str]] = []
            for s in steps:
                if isinstance(s, dict) and s.get("instruction"):
                    validated.append({"instruction": str(s["instruction"])})
                elif isinstance(s, str):
                    validated.append({"instruction": s})
            return validated
        except Exception as exc:
            logger.error("CoordinatePeerTask: LLM decomposition failed: %s", exc)
            return []

    def _send_to_peer(self, peer_name: str, instruction: str, timeout_sec: float) -> dict[str, Any]:
        """동료 로봇에게 단일 명령을 전달한다.

        같은 ROS 네트워크면 delegate_task(ExecuteTask action), gRPC 원격이면
        call_peer_robot(CallPeerRobot service)를 사용한다.
        """
        node = self.node
        if node is None:
            return {"success": False, "message": "노드에 접근할 수 없습니다."}

        # 같은 ROS 네트워크에 있는지 확인 (DDS 노드 탐색)
        same_network = False
        try:
            node_names = node.get_node_names()
            target_node_name = f"{peer_name}_robo_claw_agent_node"
            for n in node_names:
                # 노드 이름이 정확히 target_node_name이거나, 네임스페이스 경로로 일치하는지 검증
                if (
                    n == target_node_name
                    or n.endswith(f"/{target_node_name}")
                    or (n == "robo_claw_agent_node" and f"/{peer_name}" in node.get_namespace())
                ):
                    same_network = True
                    break
        except Exception:
            pass

        if same_network:
            # delegate_task 경로 (ExecuteTask action)
            from rclpy.action import ActionClient  # type: ignore[import]

            from robo_claw_msgs.action import ExecuteTask

            action_name = f"/{peer_name}/execute_task"
            client = ActionClient(node, ExecuteTask, action_name)
            try:
                if not client.wait_for_server(timeout_sec=5.0):
                    # action server가 없으면 call_peer_robot으로 폴백
                    same_network = False
                else:
                    goal_msg = ExecuteTask.Goal()
                    goal_msg.instruction = instruction
                    send_goal_future = client.send_goal_async(goal_msg)
                    ok, goal_or_message = _wait_for_future(
                        send_goal_future,
                        timeout_sec=10.0,
                        message="동료 요청 시간 초과",
                    )
                    if not ok:
                        return {"success": False, "message": str(goal_or_message)}
                    goal_handle = goal_or_message
                    if not goal_handle or not goal_handle.accepted:
                        return {"success": False, "message": "동료가 작업을 거절했습니다."}
                    get_result_future = goal_handle.get_result_async()
                    ok, result_or_message = _wait_for_future(
                        get_result_future,
                        timeout_sec=timeout_sec if timeout_sec > 0 else 60.0,
                        message="동료 작업 실행 중 시간 초과",
                    )
                    if not ok:
                        return {"success": False, "message": str(result_or_message)}
                    result = result_or_message.result
                    return {
                        "success": result.success,
                        "message": result.result_message,
                    }
            finally:
                client.destroy()

        # call_peer_robot 경로 (gRPC 원격)
        if not hasattr(node, "_call_peer_client"):
            return {
                "success": False,
                "message": "피어 호출 클라이언트가 설정되지 않았습니다.",
            }

        from robo_claw_msgs.srv import CallPeerRobot

        req = CallPeerRobot.Request()
        req.peer_name = peer_name
        req.instruction = instruction
        req.file_path = ""
        req.wait_for_result = True
        req.timeout_sec = timeout_sec

        ok, resp = _call_service(
            node._call_peer_client,
            req,
            call_timeout_sec=_local_wait_sec(timeout_sec),
            not_found_message="CallPeerRobot 서비스를 찾을 수 없습니다.",
            timeout_message="동료 로봇 호출 시간 초과",
        )
        if not ok:
            return {"success": False, "message": str(resp)}

        return {
            "success": bool(resp.success),
            "message": resp.result_message or resp.error_message or "응답 없음",
        }
