"""AgentNode 초기화 헬퍼.

``__init__`` 에 뭉쳐 있던 메모리/RAG·TF·LLM·스킬·ROS 인터페이스 구성을
단계별 함수로 분리한다. 각 함수는 node 를 받아 부수효과로 node 속성을 채운다.
"""

from __future__ import annotations

import json
import threading

from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.qos import QoSProfile, ReliabilityPolicy

from robo_claw_msgs.action import ExecuteTask
from robo_claw_msgs.msg import AgentStatus
from robo_claw_msgs.srv import (
    CallPeerRobot,
    ExecuteSkill,
    ListPeers,
    PeerMessage,
    QueryState,
    SendMessage,
)

from ..llm_bridge import create_llm_bridge
from ..memory_manager import MemoryManager
from ..tracing import init_tracing
from ..vector_store import DualVectorStore, QdrantVectorStore
from .params import AgentParams


def setup_memory_and_rag(node, p: AgentParams) -> QdrantVectorStore | None:
    """MemoryManager 를 생성하고 RAG 벡터 스토어를 배선한다.

    Qdrant primary 를 준비해 두고(LLM 차원 검증에 재사용), 미러 설정에 따라
    DualVectorStore 또는 단일 스토어로 구성한다. 반환값은 준비된 Qdrant
    primary(없으면 None).
    """
    # 원격(Qdrant) primary 준비. 미러 활성 시 연결 실패해도 RAG를 끄지 않는다.
    qdrant_primary = None
    if node._enable_rag and p.rag_vector_backend == "qdrant":
        try:
            if not p.qdrant_url:
                raise ValueError("qdrant_url 파라미터가 필요합니다.")
            qdrant_primary = QdrantVectorStore.from_url(
                url=p.qdrant_url,
                api_key=p.qdrant_api_key,
                collection_name=p.qdrant_collection,
                timeout_sec=p.qdrant_timeout_sec,
            )
            qdrant_primary.validate_connection()
            node.get_logger().info(f"Qdrant RAG connection ready: collection={p.qdrant_collection}")
        except Exception as e:
            if p.rag_local_mirror:
                node.get_logger().warning(
                    f"Qdrant init/connection failed. Continuing RAG with local mirror: {e}"
                )
            else:
                node.get_logger().error(f"Qdrant init failed, disabling RAG: {e}")
                node._enable_rag = False
                qdrant_primary = None

    # 핵심 컴포넌트 초기화. vector_store=None이면 로컬 저장소가 생성되며,
    # Qdrant 사용 시 이 로컬 저장소를 미러로 재사용한다.
    node._memory = MemoryManager(
        storage_path=p.memory_path,
        backend=p.memory_backend,  # type: ignore
        vector_store=None,
        local_rag_namespace=(
            p.qdrant_collection if p.rag_vector_backend == "qdrant" and p.rag_local_mirror else ""
        ),
        rag_score_threshold=float(p.rag_score_threshold),
        skill_learning_success_sample_rate=float(p.skill_learning_success_sample_rate),
    )

    # RAG 백엔드 배선: Qdrant primary가 있으면 미러 여부에 따라 구성
    if qdrant_primary is not None:
        if p.rag_local_mirror:
            dual = DualVectorStore(qdrant_primary, node._memory._vector_store)
            node._memory._vector_store = dual
            _start_rag_reconcile_thread(node, dual)
        else:
            node._memory._vector_store = qdrant_primary

    return qdrant_primary


def _start_rag_reconcile_thread(node, dual: DualVectorStore) -> None:
    def _run() -> None:
        node.get_logger().info("Starting RAG background reconciliation")
        try:
            result = dual.reconcile()
            node.get_logger().info(
                f"RAG background reconciliation complete: pushed={result['pushed']}, pulled={result['pulled']}"
            )
        except Exception as recon_err:
            node.get_logger().warning(
                f"RAG background reconciliation failed (ignored): {recon_err}"
            )

    thread = threading.Thread(target=_run, name="rag-reconcile", daemon=True)
    thread.start()


def setup_tf(node, p: AgentParams) -> None:
    """위치 좌표를 map 프레임 기준으로 보고하기 위한 TF2 인프라를 준비한다."""
    node._map_frame = p.map_frame
    node._robot_base_frame = p.robot_base_frame
    node._tf_buffer = None
    node._tf_listener = None
    try:
        import tf2_ros

        node._tf_buffer = tf2_ros.Buffer()
        node._tf_listener = tf2_ros.TransformListener(node._tf_buffer, node)
        node.get_logger().info(f"TF listener ready: {node._map_frame} → {node._robot_base_frame}")
    except Exception as tf_error:
        node.get_logger().warning(
            f"TF2 init failed, position will be reported via /odom fallback: {tf_error}"
        )


def setup_llm(node, p: AgentParams, qdrant_primary: QdrantVectorStore | None) -> None:
    """메인 LLM 브릿지와 (RAG 활성 시) 임베더 브릿지를 생성한다."""
    # LangSmith 트레이싱 상태를 시작 시 진단·로깅한다 (선택적, 환경변수 구동).
    init_tracing(node.get_logger().info)
    try:
        node._llm = create_llm_bridge(
            p.llm_provider,
            model=p.llm_model,
            embedding_model=p.llm_embedding,
            endpoint=p.llm_base_url or p.azure_endpoint,
            api_key=p.azure_key or p.openai_key or p.anthropic_key,
            options_json=p.ollama_options_json,
        )

        # provider별 필수 설정 검증
        config_error = node._llm.validate_config()
        if config_error:
            node.get_logger().error(f"LLM config validation failed: {config_error}")
            if p.llm_fail_fast:
                raise RuntimeError(f"LLM 설정 검증 실패: {config_error}")
            node._llm = None
            node._enable_rag = False
            node._memory.set_embedder(None)
            return

        # 시작 시 LLM 서버 연결 헬스체크 (llm_fail_fast인 경우만 실패 시 중단)
        health_error = node._llm.healthcheck(timeout_sec=10.0)
        if health_error:
            node.get_logger().warning(f"LLM healthcheck failed: {health_error}")
            if p.llm_fail_fast:
                raise RuntimeError(f"LLM 헬스체크 실패: {health_error}")
        else:
            node.get_logger().info("LLM healthcheck passed")

        # RAG 전용 임베더 브릿지 생성
        node._embedder_bridge = None
        if node._enable_rag:
            emb_provider = p.llm_embedding_provider
            emb_model = p.llm_embedding
            emb_base_url = p.llm_embedding_base_url
            emb_api_key = p.llm_embedding_api_key

            # 임베더 전용 설정이 누락된 경우 메인 LLM 설정 적용 (Fallback)
            if not emb_provider:
                emb_provider = p.llm_provider
                emb_model = p.llm_embedding
                emb_base_url = p.llm_base_url or p.azure_endpoint
                emb_api_key = p.azure_key or p.openai_key or p.anthropic_key

            if emb_provider.lower() == "anthropic":
                node.get_logger().warning(
                    "The Anthropic provider does not directly support embeddings. "
                    "A separate llm_embedding_provider setting is recommended."
                )

            try:
                node._embedder_bridge = create_llm_bridge(
                    emb_provider,
                    model=emb_model,
                    embedding_model=emb_model,
                    endpoint=emb_base_url,
                    api_key=emb_api_key,
                    options_json=p.ollama_options_json,
                    timeout_sec=p.llm_timeout_sec,
                )

                # RAG 시작 시점에 임베딩/벡터 차원을 검증
                probe_vec = node._embedder_bridge.embed("robo_claw_rag_healthcheck")
                node.get_logger().info(
                    f"RAG embedder configured: provider={emb_provider}, model={emb_model}, dim={len(probe_vec)}"
                )
                store = node._memory._vector_store
                if hasattr(store, "validate_vector_size"):
                    try:
                        store.validate_vector_size(len(probe_vec))
                    except Exception as dim_err:
                        # 미러가 있으면 원격 검증 실패는 치명적이지 않다.
                        if p.rag_local_mirror and qdrant_primary is not None:
                            node.get_logger().warning(
                                "Qdrant vector dimension validation failed (continuing with local mirror): "
                                f"{dim_err}. The current embedder config (provider={emb_provider}, "
                                f"model={emb_model}, dim={len(probe_vec)}) differs from the existing "
                                "collection. To keep the existing collection, set "
                                "RC_LLM_EMBEDDING_PROVIDER/RC_LLM_EMBEDDING_MODEL to match "
                                "the embedder used to create the collection."
                            )
                        else:
                            raise

                node._memory.set_embedder(node._embedder_bridge)
                node.get_logger().info(
                    f"LLM initialized and RAG engine running: {emb_provider} (main LLM: {p.llm_provider})"
                )
            except Exception as rag_error:
                node._memory.set_embedder(None)
                node._enable_rag = False
                node.get_logger().error(f"RAG init failed, disabling RAG: {rag_error}")
                node.get_logger().info(f"LLM init complete (RAG disabled): {p.llm_provider}")
        else:
            node.get_logger().info(f"LLM init complete (RAG disabled): {p.llm_provider}")
    except Exception as e:
        node._enable_rag = False
        node._memory.set_embedder(None)
        node.get_logger().error(f"LLM init failed: {e}")


def load_skills(node, p: AgentParams) -> None:
    """스킬 모듈과 MCP 서버 스킬을 로드한다."""
    for module in p.skill_modules:
        node._skills.load_from_module(module)

    if p.mcp_enabled and p.mcp_servers_json:
        try:
            from ..mcp_adapter import MCPManager

            mcp_configs = json.loads(p.mcp_servers_json)
            if not isinstance(mcp_configs, list):
                raise ValueError("mcp_servers_json는 JSON 배열이어야 합니다.")

            node._mcp_manager = MCPManager()
            mcp_skills = node._mcp_manager.load_servers(mcp_configs)

            # 여러 MCP 서버가 같은 도구명(예: search)을 제공할 수 있다.
            # 기존 로컬 스킬과의 충돌은 보존하고, MCP끼리의 충돌은 서버명을
            # 붙여 양쪽 도구를 모두 LLM에 노출한다.
            local_names = {s["name"] for s in node._skills.list_skills()}
            mcp_name_groups: dict[str, list] = {}
            for skill in mcp_skills:
                mcp_name_groups.setdefault(skill.name, []).append(skill)
            for tool_name, group in mcp_name_groups.items():
                if len(group) > 1:
                    for skill in group:
                        skill.rename(f"{skill._server}__{tool_name}")
                        node.get_logger().warning(
                            f"MCP tool name conflict resolved: {tool_name} -> {skill.name}"
                        )

            loaded_mcp_count = 0
            for skill in mcp_skills:
                if skill.name in local_names or node._skills.has_skill(skill.name):
                    node.get_logger().warning(
                        f"Skipping MCP skill due to name conflict: {skill.name}"
                    )
                    continue
                node._skills.register(skill)
                loaded_mcp_count += 1

            node.get_logger().info(f"Loaded {loaded_mcp_count} MCP skill(s)")
        except Exception as e:
            node.get_logger().error(f"MCP init failed: {e}")

    loaded_skills = [s["name"] for s in node._skills.list_skills()]
    node.get_logger().info(f"Loaded {len(loaded_skills)} skill(s): {', '.join(loaded_skills)}")


def setup_ros_interfaces(node) -> None:
    """퍼블리셔/서비스/액션 서버/타이머 등 ROS 인터페이스를 생성한다."""
    qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
    cb_group = ReentrantCallbackGroup()

    node._status_pub = node.create_publisher(AgentStatus, "~/status", qos)
    node._exec_skill_srv = node.create_service(
        ExecuteSkill,
        "~/execute_skill",
        node._handle_execute_skill,
        callback_group=cb_group,
    )
    node._query_srv = node.create_service(
        QueryState,
        "~/query_state",
        node._handle_query_state,
        callback_group=cb_group,
    )

    # 자율협동 — 동료 로봇이 보낸 대화 메시지를 협동 인박스로 전달하는 서비스
    node._peer_message_srv = node.create_service(
        PeerMessage,
        "~/peer_message",
        node._handle_peer_message,
        callback_group=cb_group,
    )

    # 채널로 메시지를 보내기 위한 클라이언트 (channel_node가 서버 역할)
    node._send_msg_client = node.create_client(
        SendMessage, "/robo_claw_channel_node/send_message", callback_group=cb_group
    )

    # 이름으로 지정한 특정 동료 로봇(gRPC 피어)을 호출하기 위한 클라이언트
    # (client_node/MessengerClientNode가 서버 역할)
    node._call_peer_client = node.create_client(
        CallPeerRobot,
        "/robo_claw_messenger_client_node/call_peer",
        callback_group=cb_group,
    )

    # 연결된 동료 로봇 목록/상태를 조회하기 위한 클라이언트
    node._list_peers_client = node.create_client(
        ListPeers,
        "/robo_claw_messenger_client_node/list_peers",
        callback_group=cb_group,
    )

    # 연결된 모든 동료 로봇에 브로드캐스트하기 위한 클라이언트
    # (client_node의 SendMessage 서비스 — 사람용 채널 노드의 _send_msg_client와는 별개)
    node._broadcast_peers_client = node.create_client(
        SendMessage,
        "/robo_claw_messenger_client_node/send_message",
        callback_group=cb_group,
    )

    node._action_server = ActionServer(
        node,
        ExecuteTask,
        "~/execute_task",
        execute_callback=node._handle_execute_task,
        goal_callback=lambda _: GoalResponse.ACCEPT,
        cancel_callback=lambda _: CancelResponse.ACCEPT,
        callback_group=cb_group,
    )

    node.create_timer(1.0, node._publish_status)

    # 스킬 자가 학습: 주기적 교훈 추출 타이머 (interval>0 일 때만)
    if (
        node._enable_skill_learning
        and node._enable_rag
        and node._skill_learning_reflect_interval_sec > 0.0
    ):
        node.create_timer(
            node._skill_learning_reflect_interval_sec,
            node._on_reflect_timer,
            callback_group=cb_group,
        )
        node.get_logger().info(
            "Enabled periodic skill self-learning lesson extraction "
            f"(interval: {node._skill_learning_reflect_interval_sec}s)"
        )
