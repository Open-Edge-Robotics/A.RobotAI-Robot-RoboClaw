"""ROS 파라미터 기본값 로더 — launch 가 쓰는 파라미터 파일에서 기본값을 읽는다.

`rclaw launch`가 넘긴 값은 노드 파라미터를 덮어쓰고, 넘기지 않은 값은 파라미터 파일
(예: `agent.yaml`, `former_config.yaml`)의 값이 그대로 쓰인다. 대시보드는 이 파일들을
읽어 "작성하지 않은 값(=기본값)"을 구분해 흐리게 표시한다.
"""

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from ..domain.config_view import flatten_param_docs, parse_contract_defaults

logger = logging.getLogger(__name__)


def default_param_paths(config_dir: str | Path, names: Iterable[str]) -> list[str]:
    """설정 디렉터리에서 실제로 존재하는 파라미터 파일 경로만 추린다."""
    base = Path(config_dir)
    paths: list[str] = []
    for name in names:
        candidate = name if str(name).startswith("/") else str(base / str(name))
        path = Path(candidate)
        if path.is_file():
            paths.append(str(path))
    return paths


def load_param_defaults(paths: Iterable[str]) -> dict[str, Any]:
    """파라미터 YAML 파일들을 읽어 ``{파라미터명: 기본값}`` 으로 합친다.

    읽기 실패나 형식 오류는 조용히 건너뛴다(대시보드는 기본값 없이도 동작해야 한다).
    """
    documents: list[Any] = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as handle:
                documents.append(yaml.safe_load(handle))
        except (OSError, yaml.YAMLError) as exc:
            logger.debug("파라미터 기본값 파일을 건너뜁니다: %s (%s)", path, exc)
    return flatten_param_docs(documents)


def load_contract_defaults(path: str | Path) -> dict[str, Any]:
    """runtime-contract.json 을 읽어 ROS 파라미터 기본값 맵을 만든다(없으면 빈 맵)."""
    try:
        with open(path, encoding="utf-8") as handle:
            contract = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.debug("계약 파일을 건너뜁니다: %s (%s)", path, exc)
        return {}
    return parse_contract_defaults(contract)
