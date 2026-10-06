"""Dockerfile pip 의존성 정합성 검사(scripts/check_docker_deps.py) 검증."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_docker_deps.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_docker_deps", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()

_DOCKERFILE = """\
FROM ros:humble-ros-base
RUN pip3 install \\
    "mcp==1.27.0" \\
    "PyYAML>=6.0.0" \\
    "cmake>=3.24,<4"
"""

_PYPROJECT = """\
[project]
name = "demo"
dependencies = [
    "mcp>=1.27,<2",
    "PyYAML>=6.0.0",
]
"""

_UV_LOCK = """\
version = 1

[[package]]
name = "mcp"
version = "1.27.0"

[[package]]
name = "PyYAML"
version = "6.0.2"
"""


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """검사 대상 3개 파일을 가진 임시 저장소 루트."""
    (tmp_path / "Dockerfile").write_text(_DOCKERFILE, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    (tmp_path / "uv.lock").write_text(_UV_LOCK, encoding="utf-8")
    monkeypatch.setattr(checker, "LOCK_PINNED", {"mcp": "테스트"})
    monkeypatch.setattr(checker, "DOCKER_ONLY", {"cmake": "테스트"})
    monkeypatch.setattr(checker, "DOCKER_OVERRIDE_ALLOWED", {})
    return tmp_path


def test_parse_requirement_splits_name_and_specifier():
    assert checker.parse_requirement("mcp==1.27.0") == ("mcp", "==1.27.0")
    assert checker.parse_requirement("PyYAML >= 6.0.0") == ("PyYAML", ">=6.0.0")
    assert checker.parse_requirement("setuptools") == ("setuptools", "")


def test_normalize_uses_pep503_name(repo):
    assert checker.normalize("PyYAML") == "pyyaml"
    assert checker.normalize("pyTelegramBotAPI") == "pytelegrambotapi"
    assert checker.normalize("python_dotenv") == "python-dotenv"


def test_consistent_repository_passes(repo):
    assert checker.check(repo) == []


def test_shared_package_specifier_mismatch_is_reported(repo):
    (repo / "Dockerfile").write_text(
        _DOCKERFILE.replace('"PyYAML>=6.0.0"', '"PyYAML>=5.0.0"'), encoding="utf-8"
    )

    problems = checker.check(repo)

    assert any("pyyaml" in problem and "버전 범위가 다릅니다" in problem for problem in problems)


def test_lock_pinned_package_must_match_uv_lock(repo):
    (repo / "Dockerfile").write_text(
        _DOCKERFILE.replace('"mcp==1.27.0"', '"mcp>=1.0.0"'), encoding="utf-8"
    )

    problems = checker.check(repo)

    assert any("mcp" in problem and "==1.27.0" in problem for problem in problems)


def test_lock_pinned_package_requires_pyproject_declaration(repo):
    (repo / "pyproject.toml").write_text(
        _PYPROJECT.replace('    "mcp>=1.27,<2",\n', ""), encoding="utf-8"
    )

    problems = checker.check(repo)

    assert any("lock 고정 대상이지만 pyproject.toml에 없습니다" in problem for problem in problems)


def test_lock_pinned_package_missing_from_uv_lock_is_reported(repo):
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    problems = checker.check(repo)

    assert any("uv.lock에 없어 정확히 고정할 수 없습니다" in problem for problem in problems)


def test_dockerfile_only_package_must_be_registered(repo):
    (repo / "Dockerfile").write_text(
        _DOCKERFILE.replace('"cmake>=3.24,<4"', '"cmake>=3.24,<4" \\\n    "gTTS>=2.3.0"'),
        encoding="utf-8",
    )

    problems = checker.check(repo)

    assert any("gtts" in problem and "pyproject.toml에 없습니다" in problem for problem in problems)


def test_docker_only_entry_must_exist_in_dockerfile(repo):
    checker.DOCKER_ONLY["pygame"] = "테스트"

    problems = checker.check(repo)

    assert any("pygame" in problem and "설치하지 않습니다" in problem for problem in problems)


def test_override_package_skips_specifier_comparison(repo):
    (repo / "pyproject.toml").write_text(
        _PYPROJECT.replace('    "PyYAML>=6.0.0",\n', '    "PyYAML>=6.0.0",\n    "setuptools",\n'),
        encoding="utf-8",
    )
    (repo / "Dockerfile").write_text(
        _DOCKERFILE.replace('"cmake>=3.24,<4"', '"cmake>=3.24,<4" \\\n    "setuptools==65.7.0"'),
        encoding="utf-8",
    )
    checker.DOCKER_OVERRIDE_ALLOWED["setuptools"] = "테스트"

    assert checker.check(repo) == []


def test_docker_only_package_declared_in_pyproject_is_reported(repo):
    (repo / "pyproject.toml").write_text(
        _PYPROJECT.replace('    "PyYAML>=6.0.0",\n', '    "PyYAML>=6.0.0",\n    "cmake",\n'),
        encoding="utf-8",
    )

    problems = checker.check(repo)

    assert any(
        "cmake" in problem and "docker_only에 둘 수 없습니다" in problem for problem in problems
    )


def test_missing_pip_install_block_is_reported(repo):
    (repo / "Dockerfile").write_text("FROM ros:humble-ros-base\n", encoding="utf-8")

    problems = checker.check(repo)

    assert any("pip install 요구사항을 찾지 못했습니다" in problem for problem in problems)


def test_main_returns_nonzero_on_problem(repo, capsys):
    (repo / "Dockerfile").write_text(
        _DOCKERFILE.replace('"mcp==1.27.0"', '"mcp>=1.0.0"'), encoding="utf-8"
    )

    assert checker.main([str(repo)]) == 1
    assert "Dockerfile 의존성 정합성 오류" in capsys.readouterr().out


def test_main_returns_zero_on_success(repo, capsys):
    assert checker.main([str(repo)]) == 0
    assert "정합성 OK" in capsys.readouterr().out


def test_repository_dependencies_are_consistent():
    """실제 저장소의 Dockerfile/pyproject/uv.lock이 정합성을 유지해야 한다."""
    assert checker.check(REPO_ROOT) == []


def test_repository_pins_mcp_to_locked_version():
    """회귀 방지: 이미지가 uv.lock과 다른 mcp 버전을 설치하면 안 된다."""
    locked = checker.locked_versions(REPO_ROOT / "uv.lock")
    dockerfile = checker.dockerfile_requirements(REPO_ROOT / "Dockerfile")
    project = checker.pyproject_requirements(REPO_ROOT / "pyproject.toml")

    assert dockerfile["mcp"] == f"=={locked['mcp']}"
    assert project["mcp"] == ">=1.27,<2"
