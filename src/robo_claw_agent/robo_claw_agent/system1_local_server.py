"""robo-claw 컨테이너 내장 Laya 서버 실행기.

``SYSTEM1_LOCAL_SERVER=true`` 이면 ``robo_claw.launch.py`` 가 이 모듈을 별도 프로세스로
실행한다(``python3 -m robo_claw_agent.system1_local_server``). 이 모듈은 ``laya-serve`` 를
자식 프로세스로 띄우고 감시한다.

주행·팔/손 모션 제어와 같은 호스트에서 돌기 때문에 제어 루프를 방해하지 않도록 한다.

* 낮은 CPU 우선순위(``SYSTEM1_LOCAL_NICE``, 기본 10)
* torch/OpenMP 스레드 상한(``SYSTEM1_LOCAL_THREADS``, 기본 2)
* 체크포인트 하나만 메모리에 유지(multilingual)
* 기본 바인딩은 ``127.0.0.1`` — 같은 호스트의 robo-claw 만 접근

장치는 ``SYSTEM1_LOCAL_DEVICE``(``auto`` / ``cuda`` / ``cpu``)로 정한다. ``auto`` 와 ``cuda`` 는
CUDA 를 쓸 수 없으면 CPU 로 실행한다. 같은 이미지를 GPU 가 있는 Thor 와 GPU 가 없는 장비에서
그대로 쓰기 위해서다.

이 프로세스가 죽거나 Laya 가 설치되지 않아도 에이전트는 규칙 라우터로 계속 동작한다
(``agent_node/system1_router.py`` 의 장애 대체).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

DEVICE_AUTO = "auto"
DEVICE_CUDA = "cuda"
DEVICE_CPU = "cpu"
_VALID_DEVICES = (DEVICE_AUTO, DEVICE_CUDA, DEVICE_CPU)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_THREADS = 2
DEFAULT_NICE = 10
DEFAULT_MAX_RESTARTS = 5


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip()) if value not in (None, "") else default
    except ValueError:
        return default


def local_server_enabled(env: Mapping[str, str]) -> bool:
    return _truthy(env.get("SYSTEM1_LOCAL_SERVER"))


def local_server_port(env: Mapping[str, str]) -> int:
    return _int(env.get("SYSTEM1_LOCAL_PORT"), DEFAULT_PORT)


def local_server_endpoint(env: Mapping[str, str]) -> str:
    """robo-claw 클라이언트가 접속할 내장 서버 주소. 바인딩이 0.0.0.0 이어도 loopback 으로 접속한다."""
    return f"http://127.0.0.1:{local_server_port(env)}"


def cuda_available() -> bool:
    """CUDA 사용 가능 여부. 감시 프로세스에 torch 가 상주하지 않도록 짧은 자식 프로세스로 확인한다."""
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)",
            ],
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


@dataclass
class LocalServerPlan:
    device: str
    env: dict[str, str]
    nice: int
    max_restarts: int
    notes: list[str]


def plan_local_server(
    env: Mapping[str, str], cuda_probe: Callable[[], bool] = cuda_available
) -> LocalServerPlan:
    """환경변수로 laya-serve 실행 환경을 결정한다(부작용 없음)."""
    notes: list[str] = []
    requested = (env.get("SYSTEM1_LOCAL_DEVICE") or DEVICE_AUTO).strip().lower()
    if requested not in _VALID_DEVICES:
        notes.append(f"unknown SYSTEM1_LOCAL_DEVICE={requested!r} — using auto")
        requested = DEVICE_AUTO

    if requested == DEVICE_CPU:
        device = DEVICE_CPU
    elif cuda_probe():
        device = DEVICE_CUDA
    else:
        device = DEVICE_CPU
        if requested == DEVICE_CUDA:
            notes.append(
                "SYSTEM1_LOCAL_DEVICE=cuda but CUDA is not available — falling back to cpu"
            )
        else:
            notes.append("CUDA not available — using cpu")

    threads = max(1, _int(env.get("SYSTEM1_LOCAL_THREADS"), DEFAULT_THREADS))
    child_env = dict(env)
    defaults = {
        "LAYA_HOST": (env.get("SYSTEM1_LOCAL_HOST") or DEFAULT_HOST).strip(),
        "LAYA_PORT": str(local_server_port(env)),
        "LAYA_DEVICE": device,
        "LAYA_THREADS": str(threads),
        "LAYA_PRELOAD": "1",
        "LAYA_MODELS": "multilingual",
        "LAYA_DEFAULT_MODEL": "multilingual",
        "LAYA_MAX_LOADED": "1",
        "OMP_NUM_THREADS": str(threads),
        "MKL_NUM_THREADS": str(threads),
    }
    # 운영자가 LAYA_* 를 직접 지정했으면 그 값을 우선한다. 단 장치와 포트는 위에서 정한 값을 쓴다.
    for key, value in defaults.items():
        if key in ("LAYA_DEVICE", "LAYA_PORT") or not env.get(key):
            child_env[key] = value
    return LocalServerPlan(
        device=device,
        env=child_env,
        nice=_int(env.get("SYSTEM1_LOCAL_NICE"), DEFAULT_NICE),
        max_restarts=max(0, _int(env.get("SYSTEM1_LOCAL_MAX_RESTARTS"), DEFAULT_MAX_RESTARTS)),
        notes=notes,
    )


def _log(msg: str) -> None:
    print(f"[system1-local] {msg}", flush=True)


def _preexec(nice: int) -> Callable[[], None]:
    def _apply() -> None:
        if nice:
            try:
                os.nice(nice)
            except OSError:
                pass

    return _apply


def run(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    if not local_server_enabled(env):
        _log("SYSTEM1_LOCAL_SERVER is not true — nothing to run")
        return 0
    exe = shutil.which("laya-serve", path=env.get("PATH"))
    if exe is None:
        _log(
            "laya-serve not found — build the image with INSTALL_LAYA=true "
            "(agent keeps working with the rule router)"
        )
        return 0

    plan = plan_local_server(env)
    for note in plan.notes:
        _log(note)
    _log(
        f"starting laya-serve: device={plan.device} host={plan.env['LAYA_HOST']} "
        f"port={plan.env['LAYA_PORT']} threads={plan.env['LAYA_THREADS']} nice={plan.nice}"
    )

    child: subprocess.Popen | None = None
    stopping = False

    def _stop(signum, _frame) -> None:
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None:
            child.send_signal(signum)

    previous = {sig: signal.signal(sig, _stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        restarts = 0
        backoff = 5.0
        while True:
            child = subprocess.Popen([exe], env=plan.env, preexec_fn=_preexec(plan.nice))  # noqa: PLW1509
            code = child.wait()
            if stopping:
                return 0
            if restarts >= plan.max_restarts:
                _log(f"laya-serve exited with {code}; restart limit reached — giving up")
                return 0
            restarts += 1
            _log(
                f"laya-serve exited with {code}; restarting in {backoff:.0f}s "
                f"({restarts}/{plan.max_restarts})"
            )
            time.sleep(backoff)
            if stopping:
                return 0
            backoff = min(backoff * 2, 60.0)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
