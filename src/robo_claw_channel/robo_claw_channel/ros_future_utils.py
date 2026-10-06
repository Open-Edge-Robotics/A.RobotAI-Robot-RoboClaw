"""ROS2 future 동기 대기 / ExecuteTask 전송 공통 헬퍼.

``channel_node.py`` 와 ``client_node.py`` 에서 각각 복사되어 있던 future 대기
헬퍼(``_wait_for_future``/``_wait_for_ready``/``_await_future_result``)와
``send_task``(ExecuteTask 액션 전송) 구현을 하나로 묶는다.
"""

import logging
import threading
from typing import Any

from robo_claw_msgs.action import ExecuteTask

logger = logging.getLogger(__name__)


def wait_for_future(future: Any, timeout_sec: float) -> bool:
    """future 완료까지 동기 대기 후 완료 여부 반환(타임아웃 시 False)."""
    event = threading.Event()
    future.add_done_callback(lambda _: event.set())
    return event.wait(timeout=timeout_sec)


def wait_for_ready(wait_fn: Any, timeout_sec: float, error: str) -> str | None:
    """wait_fn(서버/서비스 대기) 성공 시 None, 실패 시 error 문자열 반환."""
    if wait_fn(timeout_sec=timeout_sec):
        return None
    return error


def await_future_result(
    future: Any, timeout_sec: float, timeout_error: str, none_error: str
) -> tuple[bool, Any]:
    """future 결과 대기. (ok, result|error) 튜플 반환.

    타임아웃 시 (False, timeout_error), 결과 None 시 (False, none_error).
    """
    if not wait_for_future(future, timeout_sec=timeout_sec):
        return False, timeout_error

    result = future.result()
    if result is None:
        return False, none_error
    return True, result


def send_execute_task(
    task_client: Any, instruction: str, context_json: str, timeout: float
) -> dict[str, Any]:
    """ExecuteTask 액션 전송 후 결과 반환. goal 거부 시 1회 재시도.

    ``channel_node``/``client_node`` 양쪽의 ``send_task`` 본체 구현이 동일하여
    공통화했다. 호출자는 반환 dict 성공/실패에 따라 메트릭 기록/로그를 처리한다.
    """
    ready_error = wait_for_ready(
        task_client.wait_for_server, timeout, "에이전트 액션 서버 연결 실패"
    )
    if ready_error:
        return {"success": False, "error": ready_error}

    goal = ExecuteTask.Goal()
    goal.instruction = instruction
    goal.context_json = context_json
    goal.timeout_sec = timeout

    # goal이 거부되면 한 번 재시도 (stale action server 응답 방지)
    for attempt in range(2):
        future = task_client.send_goal_async(goal)
        ok, goal_result = await_future_result(
            future,
            timeout_sec=timeout,
            timeout_error="태스크 전송 타임아웃",
            none_error="태스크 goal 응답 없음",
        )
        if not ok:
            return {"success": False, "error": goal_result}

        goal_handle = goal_result
        if goal_handle.accepted:
            break
        if attempt == 0:
            logger.warning("Task goal rejected, retrying once...")
    else:
        return {"success": False, "error": "태스크 거부됨"}

    result_future = goal_handle.get_result_async()
    ok, result = await_future_result(
        result_future,
        timeout_sec=timeout,
        timeout_error="태스크 결과 대기 타임아웃",
        none_error="태스크 결과 없음",
    )
    if not ok:
        try:
            logger.warning("Task result wait timed out — cancelling active goal_handle...")
            goal_handle.cancel_goal_async()
        except Exception as err:
            logger.debug("Failed to send cancel_goal_async: %s", err)
        return {"success": False, "error": result}

    # 스킬별 결과를 함께 돌려준다. 액션 result 에는 원래부터 skill_results 가 실려 오는데
    # 여기서 버려져서, rag_search 의 참조 Qdrant 항목 같은 진단 데이터가 gRPC 응답까지
    # 도달하지 못했다.
    skill_results: list[dict[str, Any]] = []
    for item in getattr(result.result, "skill_results", None) or []:
        skill_results.append(
            {
                "skill_name": str(getattr(item, "skill_name", "") or ""),
                "code": int(getattr(item, "code", 0) or 0),
                "message": str(getattr(item, "message", "") or ""),
                "duration_sec": float(getattr(item, "duration_sec", 0.0) or 0.0),
                "result_json": str(getattr(item, "result_json", "") or ""),
            }
        )

    return {
        "success": result.result.success,
        "message": result.result.result_message,
        "skill_results": skill_results,
    }
