import json
import logging
import sys
import sysconfig
import threading
from typing import Any

# ROS 2 환경에서 사용자 설치 패키지를 인식하지 못하는 경우를 위해 경로 명시적 추가.
# Python 버전에 무관하게 sysconfig 기반으로 사용자 site-packages 경로를 구한다.
_USER_SITE = sysconfig.get_path("purelib")
if _USER_SITE and _USER_SITE not in sys.path:
    sys.path.insert(0, _USER_SITE)

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from robo_claw_msgs.msg import AgentStatus

from ..llm_bridge import BaseLLMBridge
from ..observability import OperationTracker
from ..skill_manager import SkillManager, SkillResult
from ..skills.limits import load_limits_dict
from ..types import AgentState
from .execution import ExecutionMixin
from .initialization import (
    load_skills,
    setup_llm,
    setup_memory_and_rag,
    setup_ros_interfaces,
    setup_tf,
)
from .params import declare_agent_parameters, read_agent_parameters
from .planner import LLMPlanner
from .prompts import (
    assemble_static_prompt_base,
    assemble_system_prompt,
    load_robot_limits,
    load_robot_soul,
    load_script_skills_guide,
    load_skills_guide,
    load_system_prompt,
    load_troubleshooting_guide,
)
from .sensor_health import (
    check_sensors_cached as _check_sensors_cached_impl,
)
from .sensor_health import (
    get_robot_health_state as _get_robot_health_state_impl,
)
from .skill_result_msg import build_failure_skill_result_msg, build_skill_result_msg
from .system1_router import System1Config, build_router
from .task_planner import TaskDecomposer
from .task_queue import bypasses_queue

logger = logging.getLogger(__name__)


class AgentNode(Node, ExecutionMixin):
    """
    RoboClaw AI 에이전트 노드
    """

    def __init__(self) -> None:
        super().__init__("robo_claw_agent_node")

        declare_agent_parameters(self)
        p = read_agent_parameters(self)

        self.get_logger().info(f"Python path (sys.path): {sys.path[:3]} ...")
        self.get_logger().info(f"Parameters loaded: skill_modules={p.skill_modules}")
        self.get_logger().info(f"Camera topic parameter: {p.camera_topic or '(not set)'}")
        if p.gripper_camera_topic:
            self.get_logger().info(
                f"Gripper camera topic parameter: {p.gripper_camera_topic}"
            )

        self._agent_id = p.agent_id
        self._state = AgentState.IDLE
        self._current_skill = ""
        # 센서 토픽 — 자가진단(_check_sensors_cached)이 하드코딩 대신 이 값을 사용
        self._lidar_topic = p.lidar_topic or "/scan"
        self._imu_topic = p.imu_topic or "/imu"
        self._camera_topic = p.camera_topic
        self._gripper_camera_topic = p.gripper_camera_topic
        self._gripper_depth_topic = p.gripper_depth_topic
        self._gripper_camera_info_topic = p.gripper_camera_info_topic
        self._gripper_pointcloud_topic = p.gripper_pointcloud_topic
        self._system_prompt = load_system_prompt(p.prompt_file)
        self._compact_skill_prompt = p.compact_skill_prompt
        self._robot_soul = load_robot_soul(p.soul_file)
        self._skills_guide = load_skills_guide(p.skills_guide_file)
        self._script_skills_guide = load_script_skills_guide(p.butler_script_dir)
        self._agent_workspace_dir = p.agent_workspace_dir
        self._robot_limits = load_robot_limits(p.robot_limits_file)
        self._robot_limits_dict = load_limits_dict(p.robot_limits_file)
        self._troubleshooting_guide = load_troubleshooting_guide(p.troubleshooting_guide_file)

        # strict_config 모드: 필수 설정 파일이 지정되었으나 로드 실패한 경우 기동 중단
        if p.strict_config:
            self._validate_required_configs(p)
        self._runtime_metrics = OperationTracker()
        self._enable_rag = p.enable_rag
        self._rag_top_k = max(1, int(p.rag_top_k or 2))
        self._llm_timeout_sec = float(p.llm_timeout_sec or 120.0)
        self._multiturn_n = p.multiturn_n
        self._enable_skill_learning = p.enable_skill_learning
        self._skill_learning_reflect_interval_sec = p.skill_learning_reflect_interval_sec
        self._reflecting = False
        # 학습된 스킬 교훈 프롬프트 캐시 (dirty 시 재로드)
        self._skill_lessons_ctx = ""
        self._skill_lessons_dirty = True
        # 정적 시스템 프롬프트 캐시 (스킬/가이드 영역 1회 조립)
        self._system_prompt_base_cache = None
        # 센서 점검 캐시 (매 태스크마다 동기 대기 방지)
        self._sensor_cache: dict[str, bool] | None = None
        self._sensor_cache_ts: float = 0.0
        self._sensor_cache_ttl_sec: float = 2.0

        # 메모리/RAG → 스킬매니저 → LLM/TF 순서로 구성 (원본 순서 유지)
        qdrant_primary = setup_memory_and_rag(self, p)
        self._skills = SkillManager(self)  # type: ignore
        self._apply_skill_policy(p)
        self._llm: BaseLLMBridge | None = None
        self._mcp_manager = None
        setup_tf(self, p)
        setup_llm(self, p, qdrant_primary)
        self._planner = LLMPlanner(self)
        self._task_decomposer = TaskDecomposer(self)
        # 사전 라우팅(System 1): SYSTEM1_ROUTER=rule|laya 배타 선택
        self._router = build_router(System1Config.from_env(), log=self.get_logger())
        self._enable_task_decomposition = p.enable_task_decomposition
        self._task_decomposition_max_steps = p.task_decomposition_max_steps
        self._task_step_max_retries = p.task_step_max_retries
        self._task_decomposition_wait_margin_cap_sec = (
            p.task_decomposition_wait_margin_cap_sec
        )

        # 시작 시 벡터 DB 지식 목록 로드
        self._startup_knowledge_ctx = self._load_startup_knowledge()

        load_skills(self, p)
        setup_ros_interfaces(self)

        # 태스크 큐 초기화 — 순차 실행 보장 (max_size=0 이면 큐 비활성화)
        _qmax = getattr(p, "task_queue_max_size", 8)
        if _qmax > 0:
            self._init_task_queue(max_size=_qmax)
        else:
            self._task_queue = None
            self.get_logger().info("TaskQueue disabled (task_queue_max_size=0) — parallel execution")

        if self._llm is None:
            self.get_logger().warning(
                f"AgentNode starting in restricted mode [id={p.agent_id}] — "
                "LLM was not initialized. Natural language reasoning and LLM-dependent skills are unavailable. "
                "Check the LLM configuration (llm_provider/endpoint/api_key)."
            )
        else:
            self.get_logger().info(f"AgentNode ready [id={p.agent_id}]")

    def _apply_skill_policy(self, p) -> None:
        """AgentParams에서 스킬 허용/차단 정책을 파싱해 SkillManager에 주입한다."""
        import json as _json

        def _parse(raw: str) -> list:
            if not raw:
                return []
            try:
                data = _json.loads(raw)
                if isinstance(data, list):
                    return [str(item) for item in data if isinstance(item, str)]
            except _json.JSONDecodeError:
                self.get_logger().warning(f"Failed to parse skill policy JSON: {raw}")
            return []

        allowed = _parse(p.skill_allowed_json)
        blocked = _parse(p.skill_blocked_json)
        self._skills.set_skill_policy(allowed=allowed, blocked=blocked)

    def _validate_required_configs(self, p) -> None:
        """strict_config 모드에서 필수 설정 파일 로드 성공 여부를 검증한다.

        파일 경로가 지정되었으나 내용이 비어 있다면 기동을 중단한다.
        """

        checks = [
            ("robot_limits_file", p.robot_limits_file, self._robot_limits),
            ("robot_soul_file", p.soul_file, self._robot_soul),
            ("skills_guide_file", p.skills_guide_file, self._skills_guide),
            (
                "troubleshooting_guide_file",
                p.troubleshooting_guide_file,
                self._troubleshooting_guide,
            ),
        ]
        for param_name, file_path, content in checks:
            if file_path and not content:
                raise RuntimeError(
                    f"strict_config: 필수 설정 파일 로드 실패 "
                    f"({param_name}={file_path}). 파일이 존재하는지 확인하세요."
                )

    def _on_reflect_timer(self) -> None:
        """주기적으로 스킬 경험을 분석해 교훈을 갱신한다 (백그라운드 스레드)."""
        if not self._enable_skill_learning or self._llm is None:
            return
        if self._reflecting:
            return
        self._reflecting = True

        def _work() -> None:
            try:
                summary = self._memory.reflect_all_skills(self._llm)
                reflected = summary.get("reflected", [])
                if reflected:
                    self.mark_skill_lessons_dirty()
                    self.get_logger().info(f"Skill lessons auto-updated: {summary.get('message', '')}")
            except Exception as e:  # noqa: BLE001
                self.get_logger().warning(f"Automatic skill lesson extraction failed: {e}")
            finally:
                self._reflecting = False

        threading.Thread(target=_work, daemon=True).start()

    def _load_startup_knowledge(self) -> str:
        """시작 시 벡터 DB에 저장된 지식 목록을 읽어 시스템 프롬프트 컨텍스트를 구성한다."""
        if not self._enable_rag:
            return ""
        try:
            entries = self._memory._vector_store.list_entries()
            if not entries:
                self.get_logger().info("No knowledge stored in vector DB")
                return ""
            lines = []
            for e in entries:
                text = str(e.get("text", "")).strip()
                preview = text[:120].replace("\n", " ")
                if len(text) > 120:
                    preview += "..."
                meta = e.get("metadata", {})
                tag = meta.get("type", "") or meta.get("source_name", "")
                lines.append(f"- [{tag}] {preview}" if tag else f"- {preview}")
            ctx = "[시작 시 로드된 지식 목록 ({count}건)]\n{items}".format(
                count=len(lines), items="\n".join(lines)
            )
            self.get_logger().info(f"Loaded {len(lines)} knowledge item(s) at startup")
            return ctx
        except Exception as e:
            self.get_logger().warning(f"Failed to load startup knowledge: {e}")
            return ""

    def mark_skill_lessons_dirty(self) -> None:
        """교훈 갱신 후 프롬프트 캐시를 무효화한다."""
        self._skill_lessons_dirty = True

    def _skill_lessons_section(self) -> str:
        """캐시된 교훈 섹션을 반환하되, dirty면 재로드한다.

        RAG 활성 시에는 상황 인지형 인출(_handle_execute_task에서 명령 기반
        render_relevant_skill_lessons)이 교훈을 담당하므로, 전량 주입은 RAG
        비활성 폴백 경로에서만 수행한다.
        """
        if not self._enable_skill_learning:
            return ""
        if self._enable_rag:
            return ""
        if self._skill_lessons_dirty:
            try:
                self._skill_lessons_ctx = self._memory.render_skill_lessons()
            except Exception as e:
                self.get_logger().warning(f"Failed to load skill lessons: {e}")
                self._skill_lessons_ctx = ""
            self._skill_lessons_dirty = False
        return self._skill_lessons_ctx

    def _static_prompt_base(self) -> str:
        """정적 프롬프트 영역 조립(캐시 포함). 구현은 prompts.assemble_static_prompt_base."""
        return assemble_static_prompt_base(self)

    def _build_system_prompt(self) -> str:
        """최종 시스템 프롬프트 조립. 구현은 prompts.assemble_system_prompt."""
        return assemble_system_prompt(self)

    def _set_state(self, state: AgentState, skill: str = "", msg: str = "") -> None:
        self._state = state
        self._current_skill = skill
        self._publish_status(msg)

    def _publish_status(self, message: str = "") -> None:
        status = AgentStatus()
        status.header.stamp = self.get_clock().now().to_msg()
        status.state = int(self._state)
        status.current_skill = self._current_skill
        status.message = message
        status.agent_id = self._agent_id
        self._status_pub.publish(status)

    def _check_sensors_cached(self) -> dict[str, bool] | None:
        """핵심 센서(lidar/imu/camera) 토픽 수신 여부 점검.

        구현은 ``sensor_health.check_sensors_cached`` 로 위임. 캐시 상태는
        ``self._sensor_cache`` / ``self._sensor_cache_ts`` 에 보관된다.
        """
        return _check_sensors_cached_impl(self)

    def _get_robot_health_state(self, robot_summary: dict = None) -> dict:
        """로봇 배터리·측위·센서 상태 취합. 구현은 sensor_health.get_robot_health_state."""
        return _get_robot_health_state_impl(self, robot_summary)

    def _handle_execute_skill(self, request, response):
        skill_name = request.skill_name

        # params_json 파싱: 잘못된 JSON이면 예외 대신 실패 응답 반환
        try:
            params = json.loads(request.params_json) if request.params_json else {}
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Failed to parse ExecuteSkill params_json [%s]: %s", skill_name, e)
            response.result = build_failure_skill_result_msg(
                self.get_clock(), skill_name, f"params_json 파싱 실패: {e}"
            )
            return response

        if not isinstance(params, dict):
            logger.warning("ExecuteSkill params_json is not an object [%s]: %r", skill_name, params)
            response.result = build_failure_skill_result_msg(
                self.get_clock(), skill_name, "params_json은 JSON 객체여야 합니다."
            )
            return response

        timeout_sec = request.timeout_sec or 30.0

        # 큐가 없거나, 읽기 전용/정지 계열 스킬이면 즉시 실행
        if self._task_queue is None or bypasses_queue(skill_name):
            return self._execute_skill_direct(skill_name, params, timeout_sec, response)

        # 큐를 통한 순차 실행
        return self._execute_skill_via_queue(skill_name, params, timeout_sec, response)

    def _execute_skill_direct(self, skill_name, params, timeout_sec, response):
        """즉시 스킬 실행 (큐 우회)."""
        self._set_state(AgentState.EXECUTING, skill_name)
        try:
            result = self._skills.execute(skill_name, params, timeout_sec=timeout_sec)
        except Exception as e:  # noqa: BLE001
            logger.exception("Exception while handling ExecuteSkill [%s]: %s", skill_name, e)
            result = SkillResult(skill_name, False, f"ExecuteSkill 내부 오류: {e}", 0.0)
        finally:
            self._set_state(AgentState.IDLE)

        response.result = build_skill_result_msg(self.get_clock(), result)
        return response

    def _execute_skill_via_queue(self, skill_name, params, timeout_sec, response):
        """태스크 큐를 통해 순차 실행 (service callback 내에서 블로킹 대기)."""
        done_event = threading.Event()
        result_box: dict[str, Any] = {}

        def _run() -> None:
            self._set_state(AgentState.EXECUTING, skill_name)
            try:
                result_box["result"] = self._skills.execute(
                    skill_name, params, timeout_sec=timeout_sec
                )
            except Exception as e:  # noqa: BLE001
                logger.exception("Exception in queued ExecuteSkill [%s]: %s", skill_name, e)
                result_box["result"] = SkillResult(
                    skill_name, False, f"ExecuteSkill 내부 오류: {e}", 0.0
                )
            finally:
                self._set_state(AgentState.IDLE)
                done_event.set()

        task_id = self._submit_task_to_queue(
            instruction=f"[skill] {skill_name}",
            execute_fn=_run,
        )
        if task_id is None:
            response.result = build_failure_skill_result_msg(
                self.get_clock(), skill_name,
                "처리 대기열이 가득 찼습니다. 잠시 후 다시 시도해주세요.",
            )
            return response

        done_event.wait(timeout=timeout_sec + 60.0)

        result = result_box.get(
            "result",
            SkillResult(skill_name, False, "실행 시간 초과", 0.0),
        )
        response.result = build_skill_result_msg(self.get_clock(), result)
        return response

    def _handle_query_state(self, request, response):
        state_data = {
            "agent_id": self._agent_id,
            "state": self._state.name,
            "skills": self._skills.list_skills(),
            "memory": self._memory.summary(),
            "skill_runtime": self._skills.runtime_summary(),
            "runtime_metrics": self._runtime_metrics.snapshot(),
        }
        if self._task_queue is not None:
            state_data["task_queue"] = self._task_queue.metrics.__dict__
        response.success, response.state_json = (
            True,
            json.dumps(state_data, ensure_ascii=False),
        )
        return response

    def _handle_peer_message(self, request, response):
        """자율협동 — 동료 로봇이 보낸 대화 메시지를 협동 인박스로 전달한다."""
        peer_name = request.peer_name
        message = request.message

        if not peer_name or not message:
            response.success = False
            response.error_message = "peer_name과 message가 필요합니다."
            return response

        try:
            from ..skills.cooperation_skill.inbox import get_peer_message_inbox

            ok = get_peer_message_inbox().push(peer_name, message)
            if not ok:
                response.success = False
                response.error_message = "협동 인박스가 가득 차 메시지를 수신하지 못했습니다."
                return response
            response.success = True
            response.result_message = "메시지를 협동 인박스에 전달했습니다."
            self._ensure_autonomous_cooperate_for_peer_message(message)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f"PeerMessage handling error: {exc}")
            response.success = False
            response.error_message = f"협동 메시지 처리 오류: {exc}"
        return response

    def _ensure_autonomous_cooperate_for_peer_message(self, message: str) -> None:
        """협동 메시지를 처음 받는 로봇도 목표를 이어받아 자동 참여시킨다."""
        try:
            from ..skills.cooperation_skill.cooperate_autonomous import cooperation_start_params
            from ..skills.cooperation_skill.globals import _COOPERATE_ACTIVE

            if _COOPERATE_ACTIVE.is_set() or not self._skills.has_skill("autonomous_cooperate"):
                return
            params = cooperation_start_params(message)
            result = self._skills.execute("autonomous_cooperate", params, timeout_sec=5.0)
            if result.success:
                self.get_logger().info(
                    f"Auto-joined autonomous cooperation from peer message "
                    f"(goal={params.get('goal', '')})"
                )
            else:
                self.get_logger().warning(
                    f"Failed to auto-join autonomous cooperation: {result.message}"
                )
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warning(f"Auto-join cooperation failed: {exc}")

    def destroy_node(self):
        """ROS 노드 종료 전에 자율협동 백그라운드 루프를 정리한다."""
        try:
            from ..skills.cooperation_skill.globals import (
                _COOPERATE_ACTIVE,
                _COOPERATE_THREAD,
                _COOPERATE_WAKE,
            )

            _COOPERATE_ACTIVE.clear()
            _COOPERATE_WAKE.set()
            if _COOPERATE_THREAD is not None:
                _COOPERATE_THREAD.join(timeout=2.0)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warning(f"자율협동 루프 종료 정리 실패: {exc}")
        super().destroy_node()


def main() -> None:
    rclpy.init()
    node = AgentNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        if node._task_queue is not None:
            node._task_queue.stop()
        node._memory.flush()
        if node._mcp_manager:
            node._mcp_manager.shutdown()
        node.destroy_node()
        # executor.shutdown()이 context를 이미 닫았을 수 있으므로 중복 shutdown 방지
        try:
            rclpy.shutdown()
        except Exception:
            pass
