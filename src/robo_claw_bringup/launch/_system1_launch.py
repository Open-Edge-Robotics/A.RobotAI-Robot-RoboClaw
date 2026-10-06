"""System 1(Laya) 내장 서버 launch 헬퍼.

``SYSTEM1_LOCAL_SERVER=true`` 이면 같은 컨테이너에서 ``laya-serve`` 를 띄우는 프로세스를
launch 에 추가한다. 실제 실행·감시는 ``robo_claw_agent.system1_local_server`` 가 담당한다
(장치 선택 cuda/cpu, nice, 스레드 상한, 재시작).

SYSTEM1_* 는 launch 인자가 아니라 환경변수이므로 ``os.environ`` 을 그대로 읽는다.
에이전트 노드도 같은 환경변수를 읽어 ``SYSTEM1_ENDPOINT`` 가 비어 있으면 내장 서버 주소로 접속한다.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping

from launch.actions import ExecuteProcess


def system1_local_server_enabled(env: Mapping[str, str]) -> bool:
    return (env.get("SYSTEM1_LOCAL_SERVER") or "").strip().lower() in ("1", "true", "yes", "on")


def system1_local_server_actions(env: Mapping[str, str]) -> list:
    if not system1_local_server_enabled(env):
        return []
    return [
        ExecuteProcess(
            cmd=[sys.executable, "-m", "robo_claw_agent.system1_local_server"],
            name="system1_server",
            output="screen",
        )
    ]
