"""컨테이너 내장 Laya 서버 실행기(``system1_local_server``) 테스트.

같은 이미지를 GPU 가 있는 Thor 와 GPU 가 없는 장비에서 쓰므로, CUDA 가 없으면 CPU 로
실행해야 한다. 제어 루프와 CPU 를 나눠 쓰므로 스레드 상한과 nice 가 항상 적용돼야 한다.
"""

import os
import signal
import stat

import pytest

from robo_claw_agent import system1_local_server as sls

pytestmark = pytest.mark.unit


def _plan(env, cuda=False):
    return sls.plan_local_server(env, cuda_probe=lambda: cuda)


class TestEnabledAndEndpoint:
    def test_disabled_by_default(self):
        assert sls.local_server_enabled({}) is False

    def test_enabled_values(self):
        for value in ("true", "1", "yes", "on", "TRUE"):
            assert sls.local_server_enabled({"SYSTEM1_LOCAL_SERVER": value})

    def test_endpoint_uses_loopback_and_port(self):
        assert sls.local_server_endpoint({}) == "http://127.0.0.1:8000"
        assert sls.local_server_endpoint({"SYSTEM1_LOCAL_PORT": "8100"}) == "http://127.0.0.1:8100"
        assert sls.local_server_endpoint({"SYSTEM1_LOCAL_PORT": "bad"}) == "http://127.0.0.1:8000"


class TestDeviceSelection:
    def test_auto_uses_cuda_when_available(self):
        plan = _plan({}, cuda=True)
        assert plan.device == "cuda"
        assert plan.env["LAYA_DEVICE"] == "cuda"
        assert plan.notes == []

    def test_auto_falls_back_to_cpu(self):
        plan = _plan({}, cuda=False)
        assert plan.device == "cpu"
        assert any("CUDA not available" in n for n in plan.notes)

    def test_explicit_cuda_without_gpu_falls_back_to_cpu(self):
        plan = _plan({"SYSTEM1_LOCAL_DEVICE": "cuda"}, cuda=False)
        assert plan.device == "cpu"
        assert any("falling back to cpu" in n for n in plan.notes)

    def test_explicit_cpu_never_probes_cuda(self):
        def _probe():
            raise AssertionError("must not probe CUDA when cpu is requested")

        plan = sls.plan_local_server({"SYSTEM1_LOCAL_DEVICE": "cpu"}, cuda_probe=_probe)
        assert plan.device == "cpu"

    def test_invalid_device_uses_auto(self):
        plan = _plan({"SYSTEM1_LOCAL_DEVICE": "tpu"}, cuda=True)
        assert plan.device == "cuda"
        assert any("unknown SYSTEM1_LOCAL_DEVICE" in n for n in plan.notes)


class TestServerEnv:
    def test_defaults_protect_control_loop(self):
        plan = _plan({})
        env = plan.env
        assert env["LAYA_HOST"] == "127.0.0.1"
        assert env["LAYA_PORT"] == "8000"
        assert env["LAYA_THREADS"] == "2"
        assert env["OMP_NUM_THREADS"] == "2"
        assert env["MKL_NUM_THREADS"] == "2"
        assert env["LAYA_MODELS"] == "multilingual"
        assert env["LAYA_DEFAULT_MODEL"] == "multilingual"
        assert env["LAYA_MAX_LOADED"] == "1"
        assert env["LAYA_PRELOAD"] == "1"
        assert plan.nice == 10
        assert plan.max_restarts == 5

    def test_settings_from_system1_env(self):
        plan = _plan(
            {
                "SYSTEM1_LOCAL_PORT": "8100",
                "SYSTEM1_LOCAL_HOST": "0.0.0.0",
                "SYSTEM1_LOCAL_THREADS": "4",
                "SYSTEM1_LOCAL_NICE": "15",
                "SYSTEM1_LOCAL_MAX_RESTARTS": "0",
            }
        )
        assert plan.env["LAYA_PORT"] == "8100"
        assert plan.env["LAYA_HOST"] == "0.0.0.0"
        assert plan.env["LAYA_THREADS"] == "4"
        assert plan.nice == 15
        assert plan.max_restarts == 0

    def test_threads_at_least_one(self):
        assert _plan({"SYSTEM1_LOCAL_THREADS": "0"}).env["LAYA_THREADS"] == "1"

    def test_explicit_laya_env_is_kept_except_device_and_port(self):
        plan = _plan(
            {
                "LAYA_MODELS": "english,multilingual",
                "LAYA_DEVICE": "mps",
                "LAYA_PORT": "9999",
                "SYSTEM1_LOCAL_PORT": "8100",
            },
            cuda=False,
        )
        assert plan.env["LAYA_MODELS"] == "english,multilingual"
        assert plan.env["LAYA_DEVICE"] == "cpu"
        assert plan.env["LAYA_PORT"] == "8100"

    def test_input_env_is_not_mutated(self):
        env = {"SYSTEM1_LOCAL_SERVER": "true"}
        _plan(env)
        assert env == {"SYSTEM1_LOCAL_SERVER": "true"}


def _fake_laya_serve(tmp_path, exit_code: int):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    script = bin_dir / "laya-serve"
    script.write_text(
        f'#!/bin/sh\necho "$LAYA_DEVICE $LAYA_PORT $LAYA_THREADS" >> "{log}"\nexit {exit_code}\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return bin_dir, log


class TestRun:
    def test_disabled_returns_without_running(self, capsys):
        assert sls.run({}) == 0
        assert "nothing to run" in capsys.readouterr().out

    def test_missing_laya_serve_does_not_fail_launch(self, tmp_path, capsys):
        env = {"SYSTEM1_LOCAL_SERVER": "true", "PATH": str(tmp_path)}
        assert sls.run(env) == 0
        assert "INSTALL_LAYA=true" in capsys.readouterr().out

    def test_restarts_until_limit(self, tmp_path, monkeypatch, capsys):
        bin_dir, log = _fake_laya_serve(tmp_path, exit_code=3)
        monkeypatch.setattr(sls, "cuda_available", lambda: False)
        monkeypatch.setattr(sls.time, "sleep", lambda _s: None)
        env = {
            "SYSTEM1_LOCAL_SERVER": "true",
            "SYSTEM1_LOCAL_MAX_RESTARTS": "2",
            "SYSTEM1_LOCAL_NICE": "0",
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        }

        before = signal.getsignal(signal.SIGINT)
        assert sls.run(env) == 0
        assert signal.getsignal(signal.SIGINT) is before

        calls = log.read_text().splitlines()
        assert calls == ["cpu 8000 2"] * 3
        out = capsys.readouterr().out
        assert "restart limit reached" in out
