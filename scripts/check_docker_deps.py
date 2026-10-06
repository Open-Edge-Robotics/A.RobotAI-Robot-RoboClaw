"""Dockerfile의 pip 의존성과 pyproject.toml/uv.lock의 정합성을 검증합니다.

이미지 빌드는 uv.lock을 사용하지 않고 Dockerfile에서 pip로 직접 설치하므로, 두 곳의
버전 범위가 어긋나면 개발 환경(.venv)과 배포 이미지의 런타임 API가 달라질 수 있습니다.
실제로 mcp 2.0이 ``ClientSession.list_tools``의 ``cursor`` 파라미터를 제거했을 때
Dockerfile의 ``mcp>=1.0.0``이 최신 2.x를 설치해 MCP 스킬 로딩이 실패했습니다.

검증 규칙:

1. Dockerfile과 pyproject.toml에 모두 있는 패키지는 버전 범위가 같아야 합니다.
   (ROS/colcon 빌드 도구처럼 이미지에서 별도 버전을 쓰는 경우는 override 목록에
   사유와 함께 등록합니다.)
2. lock_pinned 패키지는 Dockerfile에서 uv.lock과 같은 버전으로 정확히 고정해야
   합니다. 이미지와 개발 환경이 같은 버전을 설치하도록 강제하기 위함입니다.
3. Dockerfile에만 있는 패키지는 docker_only 목록에 사유와 함께 등록해야 합니다.

사용법::

    python3 scripts/check_docker_deps.py [저장소 루트]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

# 이미지가 개발 환경(uv.lock)과 정확히 같은 버전을 설치해야 하는 패키지.
# 이미지의 pip 해석 결과가 달라지면 런타임 API가 바뀔 수 있는 패키지를 등록한다.
LOCK_PINNED: dict[str, str] = {
    "mcp": "1.x/2.x에서 list_tools 시그니처와 응답 필드명이 달라짐",
}

# pyproject.toml에도 있지만 이미지에서 다른 버전을 고정하는 빌드 도구.
DOCKER_OVERRIDE_ALLOWED: dict[str, str] = {
    "setuptools": "colcon 빌드 도구: 이미지에서 65.7.0 고정",
}

# pyproject.toml에 없는 이미지 전용 패키지. 사유를 함께 기록한다.
DOCKER_ONLY: dict[str, str] = {
    "cmake": "colcon 빌드 도구",
    "empy": "ROS/colcon 빌드 도구: 이미지에서 3.3.4 고정",
    "gTTS": "TTS 런타임",
    "pygame": "오디오 재생 런타임",
    "python-dotenv": ".env 로딩 런타임",
    "lark": "파서 런타임",
    "lxml": "XML 처리 런타임",
}

_PIP_INSTALL_RE = re.compile(r"^\s*RUN\s+pip3?\s+install\b")
_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9._-]+)\s*(.*)$")


def normalize(name: str) -> str:
    """PEP 503 정규화 이름을 반환합니다."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _normalized_keys(entries: dict[str, str]) -> dict[str, str]:
    """allow 목록의 키를 Dockerfile 요구사항 이름과 같은 규칙으로 맞춥니다."""
    return {normalize(name): reason for name, reason in entries.items()}


def parse_requirement(requirement: str) -> tuple[str, str]:
    """``"mcp==1.27.0"``을 ``("mcp", "==1.27.0")``로 나눕니다."""
    match = _REQUIREMENT_RE.match(requirement.strip())
    if not match:
        raise ValueError(f"해석할 수 없는 요구사항입니다: {requirement!r}")
    return match.group(1), match.group(2).replace(" ", "")


def dockerfile_requirements(path: Path) -> dict[str, str]:
    """Dockerfile의 ``pip install`` 블록에 있는 요구사항을 정규화 이름으로 모읍니다."""
    requirements: dict[str, str] = {}
    collecting = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not collecting and not _PIP_INSTALL_RE.match(line):
            continue
        collecting = True
        for quoted in re.findall(r'"([^"]+)"', line):
            name, specifier = parse_requirement(quoted)
            requirements[normalize(name)] = specifier
        if not line.rstrip().endswith("\\"):
            collecting = False
    return requirements


def pyproject_requirements(path: Path) -> dict[str, str]:
    """pyproject.toml의 ``project.dependencies``를 정규화 이름으로 모읍니다."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    requirements: dict[str, str] = {}
    for dependency in data.get("project", {}).get("dependencies", []):
        name, specifier = parse_requirement(dependency)
        requirements[normalize(name)] = specifier
    return requirements


def locked_versions(path: Path) -> dict[str, str]:
    """uv.lock에 기록된 패키지 버전을 정규화 이름으로 모읍니다."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {normalize(package["name"]): package["version"] for package in data.get("package", [])}


def check(
    root: Path,
    *,
    lock_pinned: dict[str, str] | None = None,
    docker_only: dict[str, str] | None = None,
    overrides: dict[str, str] | None = None,
) -> list[str]:
    """정합성 위반 사항을 문자열 목록으로 반환합니다(빈 목록이면 통과)."""
    lock_pinned = _normalized_keys(LOCK_PINNED if lock_pinned is None else lock_pinned)
    docker_only = _normalized_keys(DOCKER_ONLY if docker_only is None else docker_only)
    overrides = _normalized_keys(DOCKER_OVERRIDE_ALLOWED if overrides is None else overrides)

    dockerfile = dockerfile_requirements(root / "Dockerfile")
    project = pyproject_requirements(root / "pyproject.toml")
    locked = locked_versions(root / "uv.lock")
    problems: list[str] = []

    if not dockerfile:
        problems.append("Dockerfile에서 pip install 요구사항을 찾지 못했습니다.")

    for name, specifier in sorted(dockerfile.items()):
        if name in lock_pinned:
            version = locked.get(name)
            if name not in project:
                problems.append(f"{name}: lock 고정 대상이지만 pyproject.toml에 없습니다.")
            if version is None:
                problems.append(f"{name}: uv.lock에 없어 정확히 고정할 수 없습니다.")
            elif specifier != f"=={version}":
                problems.append(
                    f"{name}: Dockerfile은 =={version}(uv.lock)으로 고정해야 합니다. "
                    f"현재: {specifier or '(범위 없음)'}"
                )
            continue

        if name in overrides:
            continue

        if name in docker_only:
            if name in project:
                problems.append(
                    f"{name}: pyproject.toml에도 있으므로 docker_only에 둘 수 없습니다. "
                    "이미지 전용이 아니면 버전 범위를 일치시키세요."
                )
            continue

        if name not in project:
            problems.append(
                f"{name}: pyproject.toml에 없습니다. 이미지 전용 의존성이면 사유와 함께 "
                "docker_only에 등록하세요."
            )
        elif specifier != project[name]:
            problems.append(
                f"{name}: 버전 범위가 다릅니다. "
                f"Dockerfile={specifier or '(범위 없음)'} "
                f"pyproject={project[name] or '(범위 없음)'}"
            )

    for name in sorted(docker_only):
        if name not in dockerfile:
            problems.append(f"{name}: docker_only에 있지만 Dockerfile에서 설치하지 않습니다.")

    for name in sorted(overrides):
        if name not in dockerfile:
            problems.append(f"{name}: override 목록에 있지만 Dockerfile에서 설치하지 않습니다.")

    return problems


def main(argv: list[str] | None = None) -> int:
    """위반 사항을 출력하고 종료 코드를 반환합니다."""
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]).resolve() if args else Path(__file__).resolve().parents[1]

    problems = check(root)
    if problems:
        print("Dockerfile 의존성 정합성 오류:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("Dockerfile 의존성 정합성 OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
