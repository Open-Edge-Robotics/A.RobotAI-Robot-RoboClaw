"""ROS2 future 동기 대기 / 서비스 호출 공통 헬퍼 (이벤트 콜백 기반).

``MultiThreadedExecutor`` 가 이미 spin 중인 스킬·노드 코드에서는 ``rclpy.spin_once``
기반 대기가 차단되므로, future 완료 콜백만 이벤트로 기다린다. spin 기반 대기 헬퍼는
``manipulation_runtime/_ros_sync.py`` 를 참고한다.

과거에는 ``skills/cooperate_skill.py``, ``skills/cooperation_skill/cooperate_autonomous.py``,
``skills/navigation_skill/core.py``, ``skills/cloid_packing_pose_skill.py`` 가 같은 대기
루프를 각자 복사해 갖고 있었고, 그 결과 로그·타임아웃 처리 방식이 조금씩 달라졌다.
이 모듈을 단일 구현으로 사용한다.
"""

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# peer 호출류 스킬에서 timeout_sec 미지정 시 로컬 ROS2 future 대기 상한.
# 실제 gRPC 데드라인은 채널 노드의 서버 측 설정을 신뢰하고(req.timeout_sec=0),
# 여기서는 그 설정이 병목이 되지 않도록 충분히 크게 잡는다.
DEFAULT_PEER_CALL_LOCAL_WAIT_SEC = 300.0
# timeout_sec 를 명시한 경우, 로컬 대기가 서버 측 gRPC 데드라인과 정확히 같은 값으로
# 경합하지 않도록 더해주는 여유 시간.
SERVICE_CALL_MARGIN_SEC = 5.0
# 서비스가 준비될 때까지 기다리는 기본 상한.
SERVICE_READY_TIMEOUT_SEC = 5.0


def local_wait_sec(
    timeout_sec: float,
    *,
    default_wait_sec: float = DEFAULT_PEER_CALL_LOCAL_WAIT_SEC,
    margin_sec: float = SERVICE_CALL_MARGIN_SEC,
) -> float:
    """peer 호출용 로컬 ROS2 future 대기 시간을 계산한다."""
    return timeout_sec + margin_sec if timeout_sec else default_wait_sec


def wait_for_future(
    future: Any,
    timeout_sec: float,
    *,
    timeout_value: Any = None,
    label: str | None = None,
    return_timeout_value_on_error: bool = False,
) -> tuple[bool, Any]:
    """future 완료까지 이벤트로 동기 대기한다.

    Args:
        future: 완료를 기다릴 ROS2 future.
        timeout_sec: 최대 대기 시간(초). 초과 시 ``timeout_value`` 를 반환한다.
        timeout_value: 타임아웃(그리고 선택적으로 결과 예외) 시 함께 반환할 값.
        label: 로그 접두. None이면 로그를 남기지 않는다.
        return_timeout_value_on_error: ``future.result()`` 가 예외를 던질 때 예외를
            전파하지 않고 ``(False, timeout_value)`` 를 반환한다.

    Returns:
        (True, result) 완료. (False, timeout_value) 타임아웃 또는 흡수된 예외.
    """
    done = threading.Event()
    future.add_done_callback(lambda _: done.set())
    if not done.wait(timeout=timeout_sec):
        if label:
            logger.error("%s timed out (timeout=%.1fs)", label, timeout_sec)
        return False, timeout_value

    try:
        return True, future.result()
    except Exception as exc:  # noqa: BLE001
        if not return_timeout_value_on_error:
            raise
        if label:
            logger.error("Error obtaining %s result: %s", label, exc)
        return False, timeout_value


def call_service(
    client: Any,
    request: Any,
    *,
    call_timeout_sec: float,
    not_found_message: str,
    timeout_message: str,
    ready_timeout_sec: float = SERVICE_READY_TIMEOUT_SEC,
) -> tuple[bool, Any]:
    """ROS2 서비스 클라이언트로 요청을 보내고 응답(또는 에러 메시지)을 기다린다."""
    if not client.wait_for_service(timeout_sec=ready_timeout_sec):
        return False, not_found_message
    future = client.call_async(request)
    return wait_for_future(future, call_timeout_sec, timeout_value=timeout_message)
