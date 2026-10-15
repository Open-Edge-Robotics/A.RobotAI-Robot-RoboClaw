"""스킬 기본 클래스 — 모든 스킬이 상속하는 ``BaseSkill`` 과 실행 결과 타입.

``SkillManager`` 구현은 ``skills/manager.py`` 에 있다. 기존 import 경로
(``robo_claw_agent.skill_manager``)는 재-export shim 으로 유지한다.
"""

import logging
import threading
import weakref
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from ..geo_utils import yaw_from_quaternion
from ..tracing import trace_process_inputs, trace_process_outputs, traceable

if TYPE_CHECKING:
    from robo_claw_agent.agent_node import AgentNode


logger = logging.getLogger(__name__)

T = TypeVar("T")


@traceable(
    run_type="chain",
    name="skill_preconditions",
    process_inputs=trace_process_inputs,
    process_outputs=trace_process_outputs,
)
def _trace_skill_preconditions(
    skill_name: str, skill: "BaseSkill", params: dict[str, Any]
) -> tuple[bool, str]:
    del skill_name
    return skill.check_preconditions(params)


@traceable(
    run_type="chain",
    name="skill_schema_validation",
    process_inputs=trace_process_inputs,
    process_outputs=trace_process_outputs,
)
def _trace_skill_schema(
    skill_name: str, skill: "BaseSkill", params: dict[str, Any]
) -> tuple[bool, str]:
    del skill_name
    return skill.validate_input_schema(params)


class _SubscriptionSlot:
    """재사용 가능한 ROS 구독 슬롯.

    동일한 (msg_type, topic, QoS) 조합의 구독을 한 번만 생성하고, destroy 대신
    핸들러 등록/해제로 수명을 관리한다. 임시 구독을 매 호출마다 destroy하면
    MultiThreadedExecutor가 이미 작업 큐에 넣은 take 핸들러가 destroy된 handle을
    사용하면서 ``InvalidHandle: cannot use Destroyable`` 예외를 던져
    ``executor.spin()``이 종료되고 노드 전체가 죽는다.

    슬롯은 노드 수명 동안 유지되며, 마지막으로 수신한 메시지를 보관해
    TRANSIENT_LOCAL(latched) 토픽을 재구독 없이 새 대기자에게 재생할 수 있게 한다.
    """

    def __init__(self) -> None:
        self.subscription: Any = None
        self.last_message: Any = None
        self._lock = threading.Lock()
        self._handlers: list[Callable[[Any], None]] = []

    def add_handler(self, handler: Callable[[Any], None]) -> None:
        with self._lock:
            if handler not in self._handlers:
                self._handlers.append(handler)

    def remove_handler(self, handler: Callable[[Any], None]) -> None:
        with self._lock:
            try:
                self._handlers.remove(handler)
            except ValueError:
                pass

    def __call__(self, msg: Any) -> None:
        with self._lock:
            handlers = tuple(self._handlers)
        self.last_message = msg
        for handler in handlers:
            try:
                handler(msg)
            except Exception:  # noqa: BLE001
                logger.exception("Subscription slot handler failed")


# 노드별 구독 슬롯 캐시. 노드 속성으로 보관해 노드 수명과 함께 사라진다.
_SUBSCRIPTION_SLOTS_ATTR = "_robo_claw_subscription_slots"
# 속성 설정이 불가능한 테스트 더블용 id 기반 fallback.
_SUBSCRIPTION_SLOTS_FALLBACK: dict[int, dict[Any, _SubscriptionSlot]] = {}


def _subscription_slot_cache(node: Any) -> dict[Any, _SubscriptionSlot]:
    """노드별 구독 슬롯 캐시를 반환한다. 노드 수명 동안 유지된다."""
    cache = getattr(node, _SUBSCRIPTION_SLOTS_ATTR, None)
    if isinstance(cache, dict):
        return cache
    cache = {}
    try:
        setattr(node, _SUBSCRIPTION_SLOTS_ATTR, cache)
    except Exception:  # noqa: BLE001
        # __slots__ 등으로 속성 설정이 막힌 노드는 id 기반으로 공유한다.
        key = id(node)
        cache = _SUBSCRIPTION_SLOTS_FALLBACK.setdefault(key, {})
        try:
            weakref.finalize(node, _SUBSCRIPTION_SLOTS_FALLBACK.pop, key, None)
        except TypeError:
            pass
    return cache


class BaseSkill(ABC):
    """모든 스킬이 상속해야 하는 기본 스킬 클래스

    컨벤션 (새 스킬을 작성하거나 기존 스킬 파일을 수정할 때 적용, 기존 코드
    일괄 변경은 하지 않음):
    - execute()의 결과 dict는 리터럴 대신 self.success_result()/self.fail_result()로 생성한다.
    - self.node가 필요한 스킬은 진입부에 `if (err := self.require_node()): return err`를 쓴다.
    - ROS 서비스를 호출할 때는 각자 client/future 대기 로직을 재구현하지 말고
      self.call_service()를 쓴다.
    """

    # 서브클래스에서 반드시 오버라이드
    name: str = ""
    description: str = ""
    # False로 설정하면 스킬 로드에서 제외됨
    enabled: bool = True
    # 특정 manipulation_backend 에서만 등록하도록 제한 (예: "stretch").
    # None 이면 모든 백엔드에서 등록한다. stretch 전용 서비스(/stow_the_robot 등)를
    # 호출하는 스킬이 팔 없는 로봇(Former 등)에 등록되는 것을 막기 위한 게이트.
    requires_manipulation_backend: str | None = None
    risk_level: str = "action"
    allow_with_others: bool = True
    # 내부 시퀀스에서만 사용하는 서브루틴은 LLM 프롬프트에서 숨긴다.
    # 스킬 자체는 런타임에 계속 등록되어 상위 스킬이 호출할 수 있다.
    is_internal: bool = False
    # LLM/플래너가 스킬 계약을 이해할 수 있도록 하는 선택적 메타데이터.
    # 기존 스킬은 점진적으로 schema를 추가할 수 있으므로 기본값은 None이다.
    input_schema: dict[str, Any] | None = None
    terminal_behavior: str = "terminal"  # terminal | background | loop | intermediate
    # 성공 후 사용자 답변을 만드는 방식.
    # - "action": 실행 결과 메시지를 그대로 보고한다(기본값). 물리 동작을 수반하는
    #   스킬은 추가 LLM 라운드가 두 번째 물리 동작을 계획할 위험이 있으므로 기본을
    #   action 으로 두어 기존 동작을 보존한다.
    # - "informational": 조회·목록·분석처럼 결과 데이터를 사용자에게 정리해 답해야
    #   하는 스킬. 플래너가 성공 직후 결과를 LLM에 넘겨 최종 답변을 합성한다.
    #   예: ros_command(토픽/노드 목록), get_status, rag_list, get_detections.
    answer_mode: str = "action"
    side_effects: tuple[str, ...] = ()
    # 동일 자원을 배타적으로 점유하는 스킬들의 체이닝을 방지한다.
    # 예: base_motion과 base_rotation은 서로 다른 effect지만 같은 base_control을
    # 사용하므로 하나의 계획에서 동시에 예약하지 않는다.
    exclusive_resources: tuple[str, ...] = ()
    # 실행 전/후 조건을 문서화하고, 향후 상태 검증 훅으로 확장한다.
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()
    # 로컬 결정론적 재시도 설정 (기본값 0: 재시도 없음)
    max_retries: int = 0
    retry_delay_sec: float = 0.5

    def __init__(self) -> None:
        self.node: AgentNode | None = None

    def recover(self, params: dict[str, Any], error_msg: str) -> None:
        """스킬 최종 실패 시 안전 복구 및 상태 정리 훅 (필요 시 오버라이드)"""
        return None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.name:
            raise TypeError(f"{cls.__name__}: 'name' 클래스 변수를 정의해야 합니다.")

    def set_node(self, node: "AgentNode") -> None:
        """ROS2 노드 인스턴스 설정"""
        self.node = node

    def get_message_slot(
        self,
        msg_type: type[T],
        topic: str,
        qos: Any,
        handler: Callable[[T], None] | None = None,
    ) -> "_SubscriptionSlot":
        """(msg_type, topic, QoS) 조합의 재사용 구독 슬롯을 반환한다.

        슬롯은 노드 수명 동안 destroy하지 않는다. 임시 구독을 destroy하면
        MultiThreadedExecutor가 이미 제출한 take 핸들러가 InvalidHandle로
        실패해 노드가 종료될 수 있기 때문이다.

        handler가 주어지면 구독 생성 이전에 등록한다. 일부 테스트 더블이나
        TRANSIENT_LOCAL late-joiner 전달처럼 create_subscription 시점에
        동기적으로 콜백이 호출되는 경우에도 메시지를 놓치지 않게 한다.
        """
        if self.node is None:
            raise RuntimeError("Node is not configured. get_message_slot unavailable.")
        cache = _subscription_slot_cache(self.node)
        key = (
            msg_type,
            topic,
            getattr(qos, "reliability", None),
            getattr(qos, "durability", None),
        )
        slot = cache.get(key)
        if slot is None:
            slot = _SubscriptionSlot()
            if handler is not None:
                slot.add_handler(handler)
            try:
                slot.subscription = self.node.create_subscription(msg_type, topic, slot, qos)
            except Exception:
                if handler is not None:
                    slot.remove_handler(handler)
                raise
            cache[key] = slot
        elif handler is not None:
            slot.add_handler(handler)
        return slot

    def send_user_message(self, message: str, file_path: str = "") -> bool:
        """사용자 메신저(채널)로 즉시 메시지 전송 (비동기 스킬 알림용)"""
        if not self.node or not hasattr(self.node, "_send_msg_client"):
            logger.warning("Message send failed: node or client is not configured.")
            return False

        try:
            from robo_claw_msgs.srv import SendMessage
        except (ImportError, AttributeError) as exc:
            # Notifications must not abort the actual skill execution when
            # generated ROS interfaces are unavailable in a unit-test or
            # partially installed runtime.
            logger.warning("Message notification unavailable: %s", exc)
            return False

        req = SendMessage.Request()
        req.message = str(message or "")
        req.file_path = str(file_path or "")

        client = self.node._send_msg_client
        if not client.service_is_ready():
            logger.warning("Message send failed: SendMessage service not ready")
            return False

        try:
            future = client.call_async(req)
            future.add_done_callback(
                lambda f: (
                    logger.warning("Message send failed: %s", f.exception())
                    if f.exception()
                    else None
                )
            )
            return True
        except Exception as e:
            logger.error("Error during message send: %s", e)
            return False

    @abstractmethod
    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        스킬 실행.

        Args:
            params: JSON 파싱된 파라미터 딕셔너리

        Returns:
            결과 딕셔너리 {"success": bool, "message": str, ...}
        """

    def validate_params(self, params: dict[str, Any]) -> bool:
        """파라미터 유효성 검사 (서브클래스에서 오버라이드 가능)"""
        return True

    def check_preconditions(self, params: dict[str, Any]) -> tuple[bool, str]:
        """실행 직전 로봇/환경 조건을 점검하는 훅.

        기본 구현은 호환성을 위해 통과한다. 센서·ROS 상태를 알아야 하는
        스킬은 이 메서드를 오버라이드해 (False, 이유)를 반환할 수 있다.
        ``preconditions``는 LLM에 노출되는 선언적 설명이고, 실제 판정은
        이 훅에서 수행한다.
        """
        del params
        return True, ""

    def validate_input_schema(self, params: dict[str, Any]) -> tuple[bool, str]:
        """선언된 input_schema를 실행 전에 검증한다.

        외부 jsonschema 패키지에 의존하지 않는 작은 JSON Schema 검증기다.
        현재 스킬 계약에서 사용하는 object/array/primitive, required, enum,
        oneOf, additionalProperties를 지원한다. 세부 도메인 규칙은 기존
        validate_params()가 계속 담당한다.
        """
        schema = getattr(self, "input_schema", None)
        if not isinstance(schema, dict):
            return True, ""

        # LLM이 선택적 인자에 대해 명시적으로 null/None을 전달한 경우,
        # 해당 필드의 schema에 'null'이 허용되어 있지 않다면 params에서 제거하여
        # 미지정 인자로 일관되게 취급한다.
        properties = schema.get("properties", {})
        if isinstance(properties, dict) and isinstance(params, dict):
            cleaned_params: dict[str, Any] = {}
            for k, v in params.items():
                if v is None:
                    prop_spec = properties.get(k)
                    if isinstance(prop_spec, dict):
                        prop_type = prop_spec.get("type")
                        allowed = prop_type if isinstance(prop_type, list) else [prop_type]
                        if "null" in allowed:
                            cleaned_params[k] = v
                        continue
                cleaned_params[k] = v
            params = cleaned_params

        def check(value: Any, spec: dict[str, Any], path: str) -> str | None:
            types = spec.get("type")
            if types is not None:
                allowed = types if isinstance(types, list) else [types]

                def matches(kind: str) -> bool:
                    return {
                        "object": isinstance(value, dict),
                        "array": isinstance(value, list),
                        "string": isinstance(value, str),
                        "boolean": isinstance(value, bool),
                        "integer": isinstance(value, int) and not isinstance(value, bool),
                        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
                        "null": value is None,
                    }.get(kind, True)

                if not any(matches(kind) for kind in allowed):
                    return f"{path}의 타입이 올바르지 않습니다 (필요: {allowed})"
            if "enum" in spec and value not in spec["enum"]:
                return f"{path}의 값이 허용 목록에 없습니다: {value!r}"
            if "oneOf" in spec:
                valid = sum(check(value, option, path) is None for option in spec["oneOf"])
                if valid != 1:
                    return f"{path}가 oneOf 조건을 만족하지 않습니다."
            if isinstance(value, dict):
                required = spec.get("required", [])
                missing = [key for key in required if key not in value]
                if missing:
                    return f"필수 파라미터가 없습니다: {', '.join(missing)}"
                properties = spec.get("properties", {})
                additional_properties = spec.get("additionalProperties")
                if isinstance(additional_properties, bool) and not additional_properties:
                    unknown = [key for key in value if key not in properties]
                    if unknown:
                        return f"허용되지 않은 파라미터입니다: {', '.join(unknown)}"
                for key, child in properties.items():
                    if key in value and isinstance(child, dict):
                        error = check(value[key], child, f"{path}.{key}")
                        if error:
                            return error
            if isinstance(value, list) and isinstance(spec.get("items"), dict):
                for index, item in enumerate(value):
                    error = check(item, spec["items"], f"{path}[{index}]")
                    if error:
                        return error
            return None

        error = check(params, schema, "params")
        return (error is None, error or "")

    def success_result(
        self,
        message: str,
        *,
        status: str = "completed",
        recoverable: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """표준 성공 결과를 생성한다.

        ``success``와 ``message``는 기존 API와 호환되며, 추가 필드는 플래너가
        백그라운드 시작과 실제 완료, 복구 가능 여부를 구분할 수 있게 한다.
        """
        res: dict[str, Any] = {
            "success": True,
            "message": message,
            "status": status,
            "failure_reason": "",
            "recoverable": recoverable,
        }
        if kwargs:
            res.update(kwargs)
        return res

    def fail_result(
        self,
        message: str,
        *,
        failure_reason: str = "execution_failed",
        recoverable: bool = False,
        status: str = "failed",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """표준 실패 결과를 생성한다."""
        res: dict[str, Any] = {
            "success": False,
            "message": message,
            "status": status,
            "failure_reason": failure_reason,
            "recoverable": recoverable,
        }
        if kwargs:
            res.update(kwargs)
        return res

    def require_node(self) -> dict[str, Any] | None:
        """self.node가 없으면 표준 실패 결과를, 있으면 None을 반환한다.

        사용: `if (err := self.require_node()): return err`
        """
        if self.node is None:
            return self.fail_result("ROS 노드에 접근할 수 없습니다.")
        return None

    def get_float_param(self, params: dict[str, Any], key: str, fallback: float = 0.0) -> float:
        """파라미터에서 float 값을 안전하게 추출한다."""
        val = params.get(key)
        if val is None:
            return fallback
        try:
            return float(val)
        except (ValueError, TypeError):
            return fallback

    def get_int_param(self, params: dict[str, Any], key: str, fallback: int = 0) -> int:
        """파라미터에서 int 값을 안전하게 추출한다."""
        val = params.get(key)
        if val is None:
            return fallback
        try:
            return int(val)
        except (ValueError, TypeError):
            return fallback

    def get_bool_param(self, params: dict[str, Any], key: str, fallback: bool = False) -> bool:
        """파라미터에서 bool 값을 안전하게 추출한다."""
        val = params.get(key)
        if val is None:
            return fallback
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes", "y")
        return bool(val)

    def cancel(self) -> bool | None:
        """스킬 실행 취소 요청 (협조적 중단).

        SkillManager 타임아웃 시 호출된다. ROS action 기반 스킬은
        이 메서드를 오버라이해 진행 중인 goal을 cancel 해야 한다.
        반환값이 True이면 취소 요청이 확인된 것이며, None/False는 확인되지 않은
        상태를 뜻한다. 기본 구현은 no-op이다.
        """
        return None

    def set_timeout_hint(self, timeout_sec: float) -> None:
        """SkillManager.execute가 실행 직전에 유효 타임아웃(초)을 전달한다.

        기본 구현은 no-op이며, 내부적으로 자체 RPC 타임아웃을 갖는 스킬
        (예: MCPSkill)만 오버라이드하면 된다. timeout_sec <= 0이면 무제한을 의미한다.
        """
        return None

    def wait_for_message(
        self,
        msg_type: type[T],
        topic: str,
        timeout_sec: float = 5.0,
        use_transient_local: bool = False,
        allow_best_effort: bool = True,
        max_age_sec: float | None = None,
    ) -> T | None:
        """특정 토픽에서 메시지 하나를 수신할 때까지 대기 (One-shot).

        max_age_sec가 지정된 경우, 메시지 header.stamp를 확인하여
        현재 시각 대비 max_age_sec를 초과하거나 미래 시점(>2초)인 메시지는 무시하고
        최신 메시지를 계속 대기한다.

        단, use_transient_local=True(latched 토픽)인 경우에는 age 검사를
        적용하지 않는다. latched 토픽은 마지막 샘플을 그대로 전달하므로
        수신 시점의 age가 max_age_sec보다 큰 것이 정상이며, 이를 stale로
        판단하면 정적 맵(/map)처럼 1회만 발행되는 토픽을 영구히 놓치게 된다.
        """
        if self.node is None:
            logger.error("Node is not configured. wait_for_message unavailable.")
            return None

        import threading

        event = threading.Event()
        received_msg: list[T] = []

        # latched(TRANSIENT_LOCAL) 샘플은 발행 시점에 고정된 stamp를 유지하므로
        # age 기반 freshness 검사를 적용하지 않는다.
        age_limit: float | None = max_age_sec if not use_transient_local else None

        def _cb(msg: T) -> None:
            if age_limit is not None and self.node is not None:
                header = getattr(msg, "header", None)
                stamp = getattr(header, "stamp", None) if header is not None else None
                if stamp is not None:
                    msg_sec = getattr(stamp, "sec", 0) + getattr(stamp, "nanosec", 0) * 1e-9
                    if msg_sec > 0:
                        clock = getattr(self.node, "get_clock", lambda: None)()
                        if clock is not None:
                            now = clock.now()
                            now_sec = getattr(now, "nanoseconds", 0) * 1e-9
                            if now_sec > 0:
                                age = now_sec - msg_sec
                                if age > age_limit:
                                    logger.debug(
                                        "wait_for_message: dropped stale message on %s (age=%.2fs > max=%.2fs)",
                                        topic,
                                        age,
                                        age_limit,
                                    )
                                    return
                                if age < -2.0:
                                    logger.warning(
                                        "wait_for_message: dropped future timestamp message on %s (age=%.2fs)",
                                        topic,
                                        age,
                                    )
                                    return
            received_msg.append(msg)
            event.set()

        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

        # 기본적으로는 RELIABLE로 시도하되, 센서 호환성을 위해 설정을 조정 가능하게 함
        # 이미지 데이터의 경우 큐가 너무 작으면 유실될 수 있으므로 기본값을 5로 상향
        qos_profiles: list[QoSProfile] = []

        if use_transient_local:
            # /map은 map_server/slam_toolbox 설정에 따라 TRANSIENT_LOCAL 또는
            # VOLATILE로 발행될 수 있다. 구독자의 durability가 publisher보다
            # 강하면 DDS QoS 호환성이 깨져 callback 자체가 오지 않으므로,
            # 두 durability와 reliability 조합을 동시에 열어 어느 publisher든
            # 수신할 수 있게 한다. 이벤트가 먼저 설정되면 나머지는 만들지 않는다.
            for reliability in (
                ReliabilityPolicy.RELIABLE,
                ReliabilityPolicy.BEST_EFFORT,
            ):
                for durability in (
                    DurabilityPolicy.TRANSIENT_LOCAL,
                    DurabilityPolicy.VOLATILE,
                ):
                    qos = QoSProfile(depth=5)
                    qos.reliability = reliability
                    qos.durability = durability
                    qos_profiles.append(qos)
        else:
            qos = QoSProfile(depth=5)
            # 퍼블리셔가 RELIABLE이면 구독자도 RELIABLE이어야 함 (단, BEST_EFFORT 허용 시 변경)
            qos.reliability = (
                ReliabilityPolicy.BEST_EFFORT if allow_best_effort else ReliabilityPolicy.RELIABLE
            )
            qos.durability = DurabilityPolicy.VOLATILE
            qos_profiles.append(qos)

        registered: list[tuple[_SubscriptionSlot, Callable[[T], None]]] = []
        for qos in qos_profiles:
            try:
                slot = self.get_message_slot(msg_type, topic, qos, handler=_cb)
            except Exception as exc:
                # 일부 RMW 구현은 지원하지 않는 QoS 조합에서 예외를 낼 수
                # 있으므로 다른 후보 QoS를 계속 시도한다.
                logger.debug(
                    "wait_for_message: failed to create subscription on %s "
                    "(reliability=%s, durability=%s): %s",
                    topic,
                    getattr(qos, "reliability", None),
                    getattr(qos, "durability", None),
                    exc,
                )
                continue
            registered.append((slot, _cb))
            # latched 토픽은 이미 매칭된 구독에 DDS가 재전송하지 않으므로,
            # 캐시된 마지막 샘플을 새 대기자에게 재생한다.
            if use_transient_local and not event.is_set() and slot.last_message is not None:
                _cb(slot.last_message)
            if event.is_set():
                break

        if not registered:
            logger.error("wait_for_message: no subscription could be created on %s", topic)
            return None

        try:
            # 이벤트 대기 (타임아웃 적용)
            event.wait(timeout=timeout_sec)
        finally:
            # 구독은 destroy하지 않는다. 위 _SubscriptionSlot 주석 참고.
            for slot, handler in registered:
                slot.remove_handler(handler)

        return received_msg[0] if received_msg else None

    def validate_message_frame(
        self, message: Any, expected_frame: str | None, label: str
    ) -> tuple[bool, str]:
        """Validate an optional ROS message frame without breaking legacy drivers.

        A present frame must match the requested frame. Messages without a frame
        remain compatible with older test doubles and drivers, while emitting a
        warning so the missing contract is observable.
        """
        if not expected_frame:
            return True, ""
        header = getattr(message, "header", None)
        frame_id = str(getattr(header, "frame_id", "") or "").strip()
        if not frame_id:
            logger.warning("%s has no frame_id; accepting for compatibility", label)
            return True, ""
        if frame_id != expected_frame:
            return False, f"{label} frame_id 불일치: {frame_id} (필요: {expected_frame})"
        return True, ""

    def call_service(
        self,
        srv_type: type[Any],
        service_name: str | None = None,
        request: Any | None = None,
        *,
        client: Any | None = None,
        timeout_sec: float = 5.0,
    ) -> tuple[bool, Any]:
        """ROS2 서비스를 동기적으로 호출한다 (wait_for_service + call_async + future 대기).

        service_name만 주면 임시 클라이언트를 생성해 호출 후 정리한다(저빈도 Trigger/Empty
        호출용). 이미 존재하는 영속 클라이언트를 재사용하려면 client=로 전달한다(예: 채널
        노드가 미리 만들어 둔 SendMessage 클라이언트). request가 None이면 srv_type.Request()를
        기본 생성한다.

        Returns:
            (True, response) 성공. (False, None) 노드 미설정/서비스 미준비/타임아웃/예외.
            구체적인 실패 사유는 logger.error로 기록된다.
        """
        if self.node is None:
            logger.error("Node is not configured. call_service unavailable.")
            return False, None
        # mypy가 가변 속성 `self.node`를 이후 좁혀주지 못하므로 지역 변수로 고정한다.
        node = self.node

        owns_client = client is None
        if owns_client:
            if service_name is None:
                raise ValueError("call_service: service_name or client must be provided")
            active_client: Any = node.create_client(srv_type, service_name)
        else:
            active_client = client

        try:
            if not active_client.wait_for_service(timeout_sec=timeout_sec):
                logger.error(
                    "Service not ready: %s",
                    service_name or getattr(active_client, "srv_name", "?"),
                )
                return False, None

            req = request if request is not None else srv_type.Request()

            import threading

            event = threading.Event()
            future = active_client.call_async(req)
            future.add_done_callback(lambda _: event.set())

            if not event.wait(timeout=timeout_sec):
                logger.error(
                    "Service call timed out: %s", service_name or getattr(client, "srv_name", "?")
                )
                return False, None

            try:
                return True, future.result()
            except Exception as e:  # noqa: BLE001
                logger.error("Service call raised an exception: %s", e)
                return False, None
        finally:
            if owns_client:
                try:
                    node.destroy_client(active_client)
                except Exception as e:  # noqa: BLE001
                    logger.debug("Failed to clean up service client: %s", e)

    def get_string_param(self, params: dict[str, Any], name: str, fallback: str = "") -> str:
        """실행 파라미터 우선, 없으면 노드 기본 파라미터를 사용한다."""
        value = params.get(name)
        if isinstance(value, str) and value:
            return value

        if self.node is not None and self.node.has_parameter(name):
            node_value = self.node.get_parameter(name).get_parameter_value().string_value
            if node_value:
                return node_value

        return fallback

    def get_map_pose(self) -> dict[str, Any] | None:
        """로봇의 현재 위치를 map 프레임 기준으로 조회한다.

        TF2 `map → base_link` 변환을 우선 사용하고, 실패하면 `/odom`으로 폴백한다.
        반환 dict의 `frame` 키로 좌표 출처("map" 또는 "odom")를 구분한다.
        """
        if self.node is None:
            return None

        import math

        # 1순위: TF2 map 프레임 변환
        tf_buffer = getattr(self.node, "_tf_buffer", None)
        if tf_buffer is not None:
            map_frame = getattr(self.node, "_map_frame", "map")
            base_frame = getattr(self.node, "_robot_base_frame", "base_link")
            try:
                import rclpy.time

                tf = tf_buffer.lookup_transform(map_frame, base_frame, rclpy.time.Time())
                t = tf.transform.translation
                q = tf.transform.rotation
                yaw = yaw_from_quaternion(q)
                return {
                    "x": round(t.x, 3),
                    "y": round(t.y, 3),
                    "yaw": round(yaw, 4),
                    "heading_deg": round(math.degrees(yaw), 1),
                    "frame": "map",
                }
            except Exception as e:
                logger.warning("Failed to query map frame TF, falling back to /odom: %s", e)

        # 폴백: /odom (오도메트리 프레임, 드리프트 가능)
        from nav_msgs.msg import Odometry

        odom = self.wait_for_message(Odometry, "/odom", timeout_sec=1.0)
        if odom is None:
            return None
        p = odom.pose.pose.position
        q = odom.pose.pose.orientation
        yaw = yaw_from_quaternion(q)
        return {
            "x": round(p.x, 3),
            "y": round(p.y, 3),
            "yaw": round(yaw, 4),
            "heading_deg": round(math.degrees(yaw), 1),
            "frame": "odom",
        }

    def get_opencv_image(
        self,
        params: dict[str, Any],
        camera_topic_param_name: str = "camera_topic",
        default_compressed_topic: str = "/oakd/rgb/preview/image_raw/compressed",
        default_raw_topic: str = "/oakd/rgb/preview/image_raw",
        timeout_sec: float = 10.0,
        max_age_sec: float | None = 2.0,
    ) -> tuple[Any | None, str, bool]:
        """주어진 파라미터에서 카메라 토픽명을 읽어와 OpenCV 이미지를 획득합니다.

        Returns:
            Tuple[cv_img, used_topic, is_compressed]
        """
        import cv2
        import numpy as np
        from cv_bridge import CvBridge
        from sensor_msgs.msg import CompressedImage, Image

        camera_source = (
            str(params.get("camera") or params.get("camera_source") or "").strip().lower()
        )
        if camera_source in {"gripper", "wrist", "hand", "end_effector", "eoa"}:
            explicit_topic = str(params.get(camera_topic_param_name) or "").strip()
            if not explicit_topic:
                camera_topic_param_name = "gripper_camera_topic"

        camera_topic = self.get_string_param(params, camera_topic_param_name, "")
        img_msg = None
        is_compressed = False

        # 1. 명시적으로 압축 토픽이 요청된 경우
        if camera_topic and "compressed" in camera_topic:
            logger.info("Starting image receive (compressed image): topic=%s", camera_topic)
            img_msg = self.wait_for_message(
                CompressedImage,
                camera_topic,
                timeout_sec=timeout_sec,
                max_age_sec=max_age_sec,
            )
            is_compressed = True
        # 2. 명시적으로 Raw 토픽이 요청된 경우
        elif camera_topic:
            logger.info("Starting image receive (raw image): topic=%s", camera_topic)
            img_msg = self.wait_for_message(
                Image,
                camera_topic,
                timeout_sec=timeout_sec,
                max_age_sec=max_age_sec,
            )
            is_compressed = False
        # 3. 토픽명이 지정되지 않은 경우 (디폴트: Compressed -> Raw Fallback)
        else:
            logger.info(
                "Starting image receive (trying default compressed image): %s",
                default_compressed_topic,
            )
            img_msg = self.wait_for_message(
                CompressedImage,
                default_compressed_topic,
                timeout_sec=3.0,
                max_age_sec=max_age_sec,
            )
            if img_msg:
                is_compressed = True
                camera_topic = default_compressed_topic
            else:
                logger.info(
                    "Failed to receive compressed image. Trying raw image: %s", default_raw_topic
                )
                img_msg = self.wait_for_message(
                    Image,
                    default_raw_topic,
                    timeout_sec=timeout_sec,
                    max_age_sec=max_age_sec,
                )
                is_compressed = False
                camera_topic = default_raw_topic

        if not img_msg:
            return None, camera_topic, is_compressed

        # A mocked wait_for_message may return a raw Image even when the
        # compressed fallback was requested. Prefer the message shape over the
        # requested topic so tests and mixed camera bridges do not attempt to
        # JPEG-decode a raw Image message.
        if is_compressed and hasattr(img_msg, "encoding"):
            is_compressed = False

        try:
            if is_compressed:
                np_arr = np.frombuffer(img_msg.data, np.uint8)
                cv_img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                if cv_img is None:
                    raise ValueError("CompressedImage 디코딩 실패")
            else:
                bridge = CvBridge()
                cv_img = bridge.imgmsg_to_cv2(img_msg, desired_encoding="bgr8")
            return cv_img, camera_topic, is_compressed
        except Exception as e:
            logger.error("Image conversion failed: %s", e)
            return None, camera_topic, is_compressed


class _TimeoutSentinel:
    """스킬 실행 타임아웃을 나타내는 내부 센티널."""

    __slots__ = ("cancel_confirmed",)

    def __init__(self, *, cancel_confirmed: bool = False) -> None:
        self.cancel_confirmed = cancel_confirmed


class SkillResult:
    """스킬 실행 결과 래퍼"""

    def __init__(
        self,
        skill_name: str,
        success: bool,
        message: str,
        duration_sec: float,
        result_data: dict[str, Any] | None = None,
    ) -> None:
        self.skill_name = skill_name
        self.success = success
        self.message = message
        self.duration_sec = duration_sec
        self.result_data = result_data or {}

    @property
    def status(self) -> str:
        """Return the normalized execution status carried by the result."""
        status = self.result_data.get("status")
        if isinstance(status, str) and status:
            return status
        return "completed" if self.success else "failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "success": self.success,
            "message": self.message,
            "status": self.status,
            "duration_sec": self.duration_sec,
            "result_data": self.result_data,
        }


def _standard_failure_data(reason: str, **kwargs: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "success": False,
        "status": "failed",
        "failure_reason": reason,
        "recoverable": False,
    }
    data.update(kwargs)
    return data
