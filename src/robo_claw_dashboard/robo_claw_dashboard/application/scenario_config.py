"""시나리오 실행기 설정 — 실행 환경 모델과 AI Config 서버 설정 검증.

대시보드 UI/CLI 가 넘긴 서버 URL·로봇·환경·토큰을 검증·정규화하고, 어떤 호스트로
요청을 보내도 되는지(허용 목록·링크로컬 차단)를 판단한다. I/O 는 하지 않는다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

#: 대시보드 UI 에서 입력받는 AI Config 서버 설정 키
USER_CONFIG_KEYS: tuple[str, ...] = (
    "config_server_url",
    "robot_name",
    "environment",
    "device_token",
)

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_CONFIG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

#: 클라우드 메타데이터·링크로컬 주소. 대시보드는 로봇/설정 서버와 통신할 뿐이며,
#: 이 주소로 요청을 보내는 정상 시나리오가 없다. 허용 목록 설정과 무관하게 차단한다.
_BLOCKED_HOSTS = frozenset(
    {
        "169.254.169.254",
        "metadata.google.internal",
        "metadata",
        "fd00:ec2::254",
    }
)
_LINK_LOCAL_PREFIX = "169.254."
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_host_entry(value: str) -> str:
    """호스트 표기를 ``host:port`` 형태로 정규화한다.

    ``http://10.0.0.5``, ``10.0.0.5``, ``https://config.example.com`` 처럼 스킴이
    있든 없든 같은 표기로 비교할 수 있게 만든다. 기본 포트(80/443)는 명시한다.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        parts = urlsplit(raw)
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        port = parts.port or _DEFAULT_PORTS.get(scheme, 0)
    else:
        host, _, port_text = raw.partition(":")
        host = host.strip().lower()
        port = int(port_text) if port_text.isdigit() else 0
    if not host:
        return ""
    return f"{host}:{port}" if port else host


def host_of(url: str) -> str:
    """URL 의 ``host:port`` 를 정규화해 돌려준다(파싱 실패 시 빈 문자열)."""
    return normalize_host_entry(url)


def _is_blocked_host(url: str) -> bool:
    """클라우드 메타데이터·링크로컬 목적지인지 판단한다."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        return False
    return host in _BLOCKED_HOSTS or host.startswith(_LINK_LOCAL_PREFIX)


def parse_user_server_config(
    payload: dict[str, Any],
    *,
    allowed_hosts: frozenset[str] = frozenset(),
) -> tuple[dict[str, str], str]:
    """대시보드가 입력받은 AI Config 서버 설정을 검증·정규화한다.

    (정규화된 값, 오류 메시지) 를 반환한다. 오류가 있으면 값은 빈 딕셔너리다.
    URL 은 http(s)://host[:port] 만 허용해 file:// 같은 스킴 주입을 막는다.

    ``allowed_hosts`` 가 비어 있지 않으면 그 목록에 있는 ``host:port`` 만 허용한다.
    목록이 비어 있을 때는 기존 동작(임의 호스트 허용)을 유지하되, 링크로컬·메타
    데이터 주소는 목록과 무관하게 항상 차단한다.
    """

    def _text(key: str) -> str:
        raw = payload.get(key)
        return "" if raw is None else str(raw).strip()

    url = _text("config_server_url")
    robot = _text("robot_name")
    environment = _text("environment")
    token = _text("device_token")

    if not url:
        return {}, "AI Config 서버 URL을 입력하세요. (예: http://10.0.0.5:30180)"
    if _CONTROL_CHARS_RE.search(url) or not _URL_RE.match(url):
        return {}, "AI Config 서버 URL은 http(s)://host[:port] 형식이어야 합니다."
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return {}, "AI Config 서버 URL은 http(s)://host[:port] 형식이어야 합니다."
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return (
            {},
            "AI Config 서버 URL에는 경로/쿼리를 넣을 수 없습니다. host[:port] 까지만 입력하세요.",
        )

    normalized_host = host_of(url)
    if _is_blocked_host(url):
        return {}, "AI Config 서버 URL로 링크로컬·메타데이터 주소는 허용되지 않습니다."
    if allowed_hosts and normalized_host not in allowed_hosts:
        allowed_text = ", ".join(sorted(allowed_hosts))
        return (
            {},
            "허용되지 않은 AI Config 서버 호스트입니다. "
            f"허용 목록: {allowed_text} (설정: config_server_allowlist)",
        )

    if not robot:
        return {}, "로봇 이름을 입력하세요."
    if not _CONFIG_NAME_RE.match(robot):
        return {}, "로봇 이름은 영문/숫자/._- 만 사용할 수 있습니다."
    if not environment:
        return {}, "환경(environment)을 입력하세요."
    if not _CONFIG_NAME_RE.match(environment):
        return {}, "환경(environment)은 영문/숫자/._- 만 사용할 수 있습니다."
    if _CONTROL_CHARS_RE.search(token) or len(token) > 4096:
        return {}, "디바이스 토큰 형식이 올바르지 않습니다."

    return (
        {
            "config_server_url": f"{parts.scheme.lower()}://{parts.netloc}",
            "robot_name": robot,
            "environment": environment,
            "device_token": token,
        },
        "",
    )


@dataclass
class ScenarioConfig:
    """시나리오 실행기 환경 설정 모델."""

    robot_name: str = ""
    environment: str = ""
    config_server_url: str = ""
    device_token: str = ""
    scenario_file: str = ""
    cache_dir: str = ""
    robot_soul_file: str = ""
    robot_limits_file: str = ""
    agent_workspace_dir: str = ""
    llm_info: str = ""
    #: 대시보드에서 입력한 AI Config 서버 호스트 허용 목록(정규화된 host:port).
    #: 비어 있으면 제한하지 않는다(링크로컬·메타데이터 주소는 항상 차단).
    allowed_config_hosts: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.cache_dir and self.robot_name and self.environment:
            self.cache_dir = os.path.expanduser(
                f"~/.robo_claw/config_cache/{self.robot_name}_{self.environment}"
            )


def missing_config_warning(cfg: ScenarioConfig) -> str:
    """원격 조회를 건너뛴 이유를 사용자에게 설명하는 문구를 만든다."""
    missing: list[str] = []
    if not cfg.config_server_url:
        missing.append("AI Config 서버 URL")
    if not cfg.robot_name:
        missing.append("로봇 이름(robot_name)")
    if not cfg.environment:
        missing.append("환경(environment)")
    if not missing:
        return ""
    return (
        ", ".join(missing) + " 설정이 없어 원격 시나리오를 조회하지 않았습니다. "
        "대시보드의 '서버 설정'에서 입력한 뒤 다시 불러오세요."
    )


def _parse_yaml_lines(content: str, target: dict[str, str]) -> None:
    """PyYAML 부재 시 정규식 기반으로 간단한 YAML 키-값을 추출한다."""
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(content)
        if isinstance(data, dict):
            if not target.get("device_token") and data.get("device_token"):
                target["device_token"] = str(data["device_token"])
            server = data.get("server")
            if isinstance(server, dict):
                if not target.get("host") and server.get("host"):
                    target["host"] = str(server["host"])
                if not target.get("port") and server.get("port"):
                    target["port"] = str(server["port"])
                if not target.get("allow_insecure_http") and "allow_insecure_http" in server:
                    target["allow_insecure_http"] = str(server["allow_insecure_http"])
            return
    except Exception:
        pass

    # Fallback regex parsing
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m_tok = re.match(r"^device_token\s*:\s*[\"']?([^\"'#\s]+)", line)
        if m_tok and not target.get("device_token"):
            target["device_token"] = m_tok.group(1).strip()
        m_host = re.match(r"^\s*host\s*:\s*[\"']?([^\"'#\s]+)", line)
        if m_host and not target.get("host"):
            target["host"] = m_host.group(1).strip()
        m_port = re.match(r"^\s*port\s*:\s*[\"']?([^\"'#\s]+)", line)
        if m_port and not target.get("port"):
            target["port"] = m_port.group(1).strip()
        m_insec = re.match(r"^\s*allow_insecure_http\s*:\s*[\"']?([^\"'#\s]+)", line)
        if m_insec and not target.get("allow_insecure_http"):
            target["allow_insecure_http"] = m_insec.group(1).strip()


def resolve_cli_config_defaults(config_yaml_path: str | None = None) -> dict[str, str]:
    """~/.robo_claw/config.yaml 또는 지정된 경로를 읽어 기본 설정을 추출한다."""
    paths_to_check: list[Path] = []
    if config_yaml_path:
        paths_to_check.append(Path(config_yaml_path).expanduser())
    else:
        paths_to_check.extend(
            [
                Path("~/.robo_claw/config.yaml").expanduser(),
                Path("~/.config/robo_claw_cli/config.yaml").expanduser(),
            ]
        )

    result: dict[str, str] = {
        "host": "",
        "port": "",
        "allow_insecure_http": "",
        "device_token": "",
        "config_server_url": "",
    }

    # 환경 변수 우선 확인
    if os.environ.get("CONFIG_URL"):
        result["config_server_url"] = os.environ["CONFIG_URL"]
    if os.environ.get("DEVICE_TOKEN"):
        result["device_token"] = os.environ["DEVICE_TOKEN"]
    elif os.environ.get("ROBO_CLAW_CLI_DEVICE_TOKEN"):
        result["device_token"] = os.environ["ROBO_CLAW_CLI_DEVICE_TOKEN"]

    for p in paths_to_check:
        if p.is_file():
            try:
                content = p.read_text(encoding="utf-8")
                _parse_yaml_lines(content, result)
                break
            except Exception:
                pass

    if not result["config_server_url"] and result["host"]:
        port_str = result["port"] or "8080"
        insecure = result["allow_insecure_http"].lower() in ("true", "1", "yes")
        proto = "http" if insecure else "https"
        result["config_server_url"] = f"{proto}://{result['host']}:{port_str}"

    return result
