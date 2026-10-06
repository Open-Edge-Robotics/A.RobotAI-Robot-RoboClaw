"""robo_claw_grpc 메인 노드 — ROS2 파라미터 관리"""

from typing import Any

from rclpy.action import ActionClient
from rclpy.node import Node

from robo_claw_msgs.action import ExecuteTask
from robo_claw_msgs.srv import ExecuteSkill


class RoboClawGrpcNode(Node):
    """robo_claw_grpc 기본 노드 — 토픽명 파라미터 관리"""

    def __init__(self):
        super().__init__("robo_claw_grpc_node")

        try:
            self.declare_parameter("camera_topic", "/camera/image_raw")
            self.declare_parameter("camera_compressed_topic", "/camera/image_raw/compressed")
            self.declare_parameter("battery_topic", "/battery_state")
            self.declare_parameter("pose_topic", "/amcl_pose")
            self.declare_parameter("navigate_to_topic", "/robo_claw_grpc/navigate_to")
            self.declare_parameter("map_topic", "/map")

            # robo_claw_agent_node의 execute_skill 서비스 클라이언트 생성
            self.exec_skill_client = self.create_client(
                ExecuteSkill, "/robo_claw_agent_node/execute_skill"
            )

            # robo_claw_agent_node의 execute_task 액션 클라이언트 생성
            # 채널(Telegram/Slack/HTTP/peer) 수신과 동일하게 자연어 명령을
            # LLM 에이전트로 전달하기 위해 사용합니다.
            self.exec_task_client = ActionClient(
                self, ExecuteTask, "/robo_claw_agent_node/execute_task"
            )

            self.get_logger().info("robo_claw_grpc main node started")

        except Exception as e:
            self.get_logger().error(f"Node initialization error: {e}")
            raise e

    async def call_execute_skill(self, skill_name: str, params: dict, timeout_sec: float = 30.0) -> Any:
        """robo_claw_agent의 execute_skill 서비스를 비동기 호출합니다."""
        import asyncio
        import json

        if not self.exec_skill_client.service_is_ready():
            self.get_logger().error("ExecuteSkill service server is not ready.")
            return None

        req = ExecuteSkill.Request()
        req.skill_name = skill_name
        req.params_json = json.dumps(params, ensure_ascii=False)
        req.timeout_sec = timeout_sec

        future = self.exec_skill_client.call_async(req)

        while not future.done():
            await asyncio.sleep(0.05)

        return future.result()

    async def call_execute_task(
        self, instruction: str, context_json: str = "", timeout_sec: float = 300.0
    ) -> Any:
        """robo_claw_agent의 execute_task 액션을 비동기 호출합니다.

        채널 노드의 send_task()와 동일한 경로(ExecuteTask 액션)를 사용해
        자연어 명령을 LLM 에이전트로 전달합니다.
        """
        import asyncio

        # wait_for_server()는 동기 블로킹이라 async 핸들러에서 직접 호출하면
        # gRPC 이벤트 루프 전체가 멈춘다. 논블로킹 폴링으로 대기한다.
        deadline = asyncio.get_running_loop().time() + 5.0
        while not self.exec_task_client.server_is_ready():
            if asyncio.get_running_loop().time() >= deadline:
                self.get_logger().error("ExecuteTask action server is not ready.")
                return None
            await asyncio.sleep(0.05)

        goal = ExecuteTask.Goal()
        goal.instruction = instruction
        goal.context_json = context_json
        goal.timeout_sec = timeout_sec

        # goal 전송 (거부 시 1회 재시도 — channel_node의 send_execute_task와 동일 정책)
        goal_handle = None
        for attempt in range(2):
            future = self.exec_task_client.send_goal_async(goal)
            while not future.done():
                await asyncio.sleep(0.05)
            goal_handle = future.result()
            if goal_handle is None:
                return None
            if goal_handle.accepted:
                break
            if attempt == 0:
                self.get_logger().warning("ExecuteTask goal rejected, retrying once...")
        else:
            return None

        result_future = goal_handle.get_result_async()
        while not result_future.done():
            await asyncio.sleep(0.05)

        return result_future.result()
