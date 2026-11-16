"""시나리오 원격 조회·캐시 — AI Config 서버 fetch 와 조회 우선순위.

파일 → 원격 서버 → 로컬 캐시 → 내장 기본 순서로 시나리오를 찾고, 실패 사유를
운영자가 조치할 수 있는 문구로 만들어 돌려준다. 상태 보관은 호출자(서비스)가 한다.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..domain.scenario_models import Scenario, get_default_scenario, parse_scenario_json
from .scenario_config import ScenarioConfig, missing_config_warning

logger = logging.getLogger(__name__)

#: HTTP 페처 시그니처 — (url, headers, timeout) → (status_code, body)
HttpFetcher = Callable[[str, dict[str, str], float], "tuple[int, bytes]"]

#: 원격 시나리오 조회 타임아웃(초)
REMOTE_TIMEOUT_SEC = 10.0

#: 캐시 디렉터리 안에 저장하는 시나리오 파일 이름
CACHE_FILE_NAME = "scenario.json"


def default_http_fetcher(
    url: str,
    headers: dict[str, str],
    timeout_sec: float = 10.0,
) -> tuple[int, bytes]:
    """urllib.request 기반 표준 HTTP GET 요청 헬퍼."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def remote_status_warning(status_code: int) -> str:
    """원격 응답 상태 코드를 운영자가 바로 조치할 수 있는 문장으로 바꿔준다."""
    if status_code in (401, 403):
        return (
            f"AI Config 서버 인증에 실패했습니다 (HTTP {status_code}). "
            "디바이스 토큰을 확인해 다시 입력하세요."
        )
    if status_code == 404:
        return (
            "해당 로봇/환경에 활성화된 테스트 시나리오가 없습니다 (HTTP 404). "
            "AI Config Server 대시보드에서 시나리오를 활성화하세요."
        )
    return f"원격 시나리오 조회 실패 (HTTP {status_code})"


@dataclass(frozen=True)
class ScenarioLoad:
    """시나리오 조회 결과.

    ``warning`` 은 이번 응답에 실어 보낼 문구이고, ``stored_warning`` 은 다음 조회까지
    서비스가 보관할 문구다. 두 값이 다른 경우는 내장 기본값으로 대체했을 때뿐이다
    (설정 누락 안내는 응답 문구로만 쓰고 보관하지 않는다).
    """

    scenario: Scenario
    warning: str
    stored_warning: str


def _remote_url(cfg: ScenarioConfig) -> str:
    """활성 시나리오 조회 URL 을 만든다(설정이 비면 빈 문자열)."""
    if not (cfg.config_server_url and cfg.robot_name and cfg.environment):
        return ""
    base_url = cfg.config_server_url.rstrip("/")
    return (
        f"{base_url}/api/v1/scenarios/active"
        f"?robot_name={cfg.robot_name}&environment={cfg.environment}"
    )


def _auth_headers(cfg: ScenarioConfig) -> dict[str, str]:
    headers: dict[str, str] = {}
    if cfg.device_token:
        headers["Authorization"] = f"Bearer {cfg.device_token}"
    return headers


def _store_cache(cfg: ScenarioConfig, body: bytes) -> None:
    """성공적으로 받은 시나리오를 로컬 캐시에 남긴다(실패해도 조회는 계속)."""
    if not cfg.cache_dir:
        return
    try:
        os.makedirs(cfg.cache_dir, exist_ok=True)
        with open(os.path.join(cfg.cache_dir, CACHE_FILE_NAME), "wb") as f:
            f.write(body)
    except Exception as cache_err:
        logger.debug("시나리오 캐시 저장 실패: %s", cache_err)


def load_scenario(
    cfg: ScenarioConfig,
    *,
    fetcher: HttpFetcher,
    previous_warning: str = "",
) -> ScenarioLoad:
    """시나리오를 파일 → 원격 → 캐시 → 내장 기본 순서로 찾는다.

    ``previous_warning`` 은 직전 조회에서 보관하던 실패 사유다. 캐시·기본값으로
    대체할 때 그 사유를 그대로 이어서 노출하기 위해 받는다.
    """
    warning = previous_warning

    # a. config.scenario_file 지정 시 파일 우선
    if cfg.scenario_file and os.path.isfile(cfg.scenario_file):
        try:
            raw_content = Path(cfg.scenario_file).read_bytes()
            from_file, err = parse_scenario_json(raw_content, source="file")
            if from_file is not None:
                return ScenarioLoad(scenario=from_file, warning="", stored_warning="")
            warning = f"시나리오 파일 파싱 오류: {err}"
        except Exception as exc:
            warning = f"시나리오 파일 읽기 실패: {exc}"

    # b. 원격 설정 서버 조회
    url = _remote_url(cfg)
    if url:
        try:
            status_code, body_bytes = fetcher(url, _auth_headers(cfg), REMOTE_TIMEOUT_SEC)
            if status_code == 200:
                from_remote, err = parse_scenario_json(body_bytes, source="remote")
                if from_remote is not None:
                    _store_cache(cfg, body_bytes)
                    return ScenarioLoad(scenario=from_remote, warning="", stored_warning="")
                warning = f"원격 시나리오 파싱 실패: {err}"
            else:
                warning = remote_status_warning(status_code)
        except Exception as exc:
            warning = f"원격 시나리오 서버 연결 실패: {exc}"

    # c. 로컬 캐시 파일
    if cfg.cache_dir:
        cache_file = os.path.join(cfg.cache_dir, CACHE_FILE_NAME)
        if os.path.isfile(cache_file):
            try:
                cached_bytes = Path(cache_file).read_bytes()
                from_cache, err = parse_scenario_json(cached_bytes, source="cache")
                if from_cache is not None:
                    return ScenarioLoad(
                        scenario=from_cache, warning=warning, stored_warning=warning
                    )
                if not warning:
                    warning = f"캐시 시나리오 파싱 실패: {err}"
            except Exception as exc:
                if not warning:
                    warning = f"캐시 시나리오 읽기 실패: {exc}"

    # d. 기본 내장 시나리오 fallback
    fallback = get_default_scenario()
    fallback.source = "default"
    return ScenarioLoad(
        scenario=fallback,
        warning=warning or missing_config_warning(cfg),
        stored_warning=warning,
    )
