"""시뮬레이션 런치 구조 계약 테스트.

배경: ``turtlebot4_gz.launch.py`` 가 선언한 인자는 ``namespace``, ``rviz``,
``world``, ``model``, pose 뿐이다. 여기에 ``nav2``, ``slam``, ``localization``,
``map``, ``params_file`` 을 넘기면 launch 는 미선언 인자를 조용히 무시하므로
"시뮬레이터는 뜨는데 Nav2 는 없다" 상태가 된다. 실제로 이 문제 때문에
jazzy 환경에서 ``--nav2`` 가 무동작이었다.

이 테스트는 (1) 소스 구조를 AST 로 검증해 ROS 설치 없이도 빠른 게이트에서 돌고,
(2) ROS/turtlebot4 패키지가 있는 환경에서는 실제로 include 되는 런치 파일을 확인한다.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_LAUNCH_FILE = Path(__file__).resolve().parents[1] / "launch" / "robo_claw_sim.launch.py"

# turtlebot4_gz.launch.py 가 실제로 선언하는 인자만 허용한다.
_GZ_LAUNCH_ALLOWED_ARGS = {"model", "world", "rviz", "namespace", "x", "y", "z", "yaw"}


def _parse() -> ast.Module:
    return ast.parse(_LAUNCH_FILE.read_text(encoding="utf-8"))


def _str_constants(node: ast.AST) -> list[str]:
    return [
        n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _included_launch_files(tree: ast.Module) -> dict[str, set[str]]:
    """IncludeLaunchDescription 호출별로 (포함되는 런치 파일명 -> launch_arguments 키) 매핑."""
    result: dict[str, set[str]] = {}
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        func = call.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "IncludeLaunchDescription" or not call.args:
            continue
        candidates = [s for s in _str_constants(call.args[0]) if s.endswith(".launch.py")]
        if not candidates:
            continue
        keys: set[str] = set()
        for keyword in call.keywords:
            if keyword.arg != "launch_arguments":
                continue
            if isinstance(keyword.value, ast.Dict):
                keys = {k.value for k in keyword.value.keys if isinstance(k, ast.Constant)}
        result[candidates[0]] = keys
    return result


def test_gz_include_passes_only_declared_arguments() -> None:
    included = _included_launch_files(_parse())
    assert "turtlebot4_gz.launch.py" in included, "Gazebo(Harmonic) 분기가 사라졌습니다"
    unknown = included["turtlebot4_gz.launch.py"] - _GZ_LAUNCH_ALLOWED_ARGS
    assert not unknown, (
        "turtlebot4_gz.launch.py 에 선언되지 않은 인자를 넘기고 있습니다(조용히 무시됨): "
        f"{sorted(unknown)}"
    )


@pytest.mark.parametrize(
    "launch_file",
    [
        "turtlebot4_gz.launch.py",
        "localization.launch.py",
        "slam.launch.py",
        "nav2.launch.py",
    ],
)
def test_gz_branch_includes_navigation_stack(launch_file: str) -> None:
    included = _included_launch_files(_parse())
    assert launch_file in included, (
        f"jazzy 분기가 {launch_file} 를 include 하지 않습니다. "
        "이 경우 --nav2/--slam 이 무동작이 됩니다."
    )


def _load_launch_module() -> Any:
    spec = importlib.util.spec_from_file_location("probe_robo_claw_sim_launch", _LAUNCH_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_context(launch_description: Any, overrides: dict[str, str]) -> Any:
    launch = pytest.importorskip("launch")  # ROS 미설치 환경에서는 skip
    from launch.actions import DeclareLaunchArgument
    from launch.utilities import perform_substitutions

    context = launch.LaunchContext()
    for entity in launch_description.entities:
        if isinstance(entity, DeclareLaunchArgument):
            default = entity.default_value
            value = perform_substitutions(context, default) if default is not None else ""
            context.launch_configurations[entity.name] = overrides.get(entity.name, value)
    for key, value in overrides.items():
        context.launch_configurations[key] = value
    return context


def _include_targets(entities: list[Any]) -> list[str]:
    targets = []
    for entity in entities:
        location = getattr(getattr(entity, "launch_description_source", None), "location", None)
        if location is not None:
            targets.append(str(location))
    return targets


@pytest.mark.ros2
def test_gz_launch_setup_includes_navigation_when_nav2_enabled() -> None:
    """실제 LaunchContext 로 jazzy 분기를 평가해 내비게이션 include 를 확인한다."""
    module = _load_launch_module()
    description = module.generate_launch_description()
    context = _build_context(
        description,
        {"sim_engine": "gz", "use_nav2": "true", "use_slam": "false", "world": "warehouse"},
    )

    try:
        entities = module.launch_setup(context)
    except Exception as exc:  # turtlebot4 패키지 미설치 환경
        pytest.skip(f"turtlebot4 시뮬레이션 패키지가 없어 평가할 수 없습니다: {exc}")

    targets = " ".join(_include_targets(entities))
    assert "turtlebot4_gz.launch.py" in targets
    assert "localization.launch.py" in targets
    assert "nav2.launch.py" in targets
    assert "slam.launch.py" not in targets


@pytest.mark.ros2
def test_stub_engine_starts_robo_claw_sim_node() -> None:
    """sim_engine:=stub 이면 Gazebo 대신 robo_claw_sim 노드가 떠야 한다."""
    from launch_ros.actions import Node

    module = _load_launch_module()
    description = module.generate_launch_description()
    context = _build_context(description, {"sim_engine": "stub"})

    entities = module.launch_setup(context)
    sim_nodes = [
        entity
        for entity in entities
        if isinstance(entity, Node) and entity.node_package == "robo_claw_sim"
    ]

    assert sim_nodes, "stub 엔진에서 robo_claw_sim 노드가 포함되지 않았습니다"
    assert sim_nodes[0].node_executable == "sim_node"


def test_launch_file_drops_gazebo_classic_assets() -> None:
    """Humble/Gazebo Classic 지원이 남아 있으면 안 된다.

    ``sim_engine:=classic`` 분기가 다시 살아나면 jazzy 호스트에서 ``gazebo_ros``
    부재로 런치가 즉시 예외로 종료되고, ``stub`` 엔진에서도 classic 자산을 찾다가
    같은 실패가 난다. Gazebo 자산은 Harmonic(``ros_gz_sim``)만 참조한다.
    """
    src = _LAUNCH_FILE.read_text(encoding="utf-8")

    for legacy in ("turtlebot3_gazebo", "gazebo_ros", "turtlebot3_world"):
        assert legacy not in src, f"Gazebo Classic(Humble) 전용 자산 참조가 남아 있습니다: {legacy}"


def test_sim_engine_default_is_gz() -> None:
    """런치 기본 엔진은 Gazebo Harmonic 이어야 한다.

    기본값이 classic 이면 ``sim_engine`` 을 넘기지 않는 진입점에서 gazebo_ros 부재로
    런치가 즉시 실패한다.
    """
    for node in ast.walk(_parse()):
        if not isinstance(node, ast.Call):
            continue
        func = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if func != "DeclareLaunchArgument" or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == "sim_engine"):
            continue
        defaults = [
            keyword.value.value
            for keyword in node.keywords
            if keyword.arg == "default_value" and isinstance(keyword.value, ast.Constant)
        ]
        assert defaults == ["gz"], f"sim_engine 기본값은 gz 여야 합니다: {defaults}"
        return

    pytest.fail("sim_engine 인자 선언을 찾지 못했습니다")


@pytest.mark.ros2
def test_removed_classic_engine_is_rejected() -> None:
    """제거된 ``sim_engine:=classic`` 은 조용히 진행하지 않고 즉시 실패해야 한다."""
    module = _load_launch_module()
    description = module.generate_launch_description()
    context = _build_context(description, {"sim_engine": "classic"})

    with pytest.raises(RuntimeError, match="classic"):
        module.launch_setup(context)


@pytest.mark.ros2
def test_stub_engine_setup_does_not_require_gazebo() -> None:
    """stub 엔진으로 launch_setup 을 평가해도 예외가 나지 않아야 한다."""
    module = _load_launch_module()
    description = module.generate_launch_description()
    context = _build_context(description, {"sim_engine": "stub", "use_nav2": "false"})

    entities = module.launch_setup(context)

    targets = " ".join(_include_targets(entities)).lower()
    assert "gazebo" not in targets, f"stub 엔진이 Gazebo 런치를 포함했습니다: {targets}"


_LAUNCH_DIR = _LAUNCH_FILE.parent
_COMMON_ARGS_FILE = _LAUNCH_DIR / "_common_launch_args.py"


def _declared_argument_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if func == "DeclareLaunchArgument" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names.add(first.value)
    return names


def _include_argument_references(path: Path) -> set[str]:
    """IncludeLaunchDescription 의 launch_arguments 에서 참조하는 LaunchConfiguration 이름."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        func = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if func != "IncludeLaunchDescription":
            continue
        for keyword in node.keywords:
            if keyword.arg != "launch_arguments":
                continue
            for inner in ast.walk(keyword.value):
                if not isinstance(inner, ast.Call):
                    continue
                inner_func = getattr(inner.func, "id", None) or getattr(inner.func, "attr", None)
                if inner_func == "LaunchConfiguration" and inner.args:
                    first = inner.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        names.add(first.value)
    return names


@pytest.mark.parametrize("launch_file", ["robo_claw.launch.py", "robo_claw_sim.launch.py"])
def test_include_arguments_are_declared_by_the_including_file(launch_file: str) -> None:
    """include 에 넘기는 ``LaunchConfiguration`` 은 그 파일에서 선언되어야 한다.

    ``launch_arguments`` 의 치환은 include 를 실행하는 **부모** 컨텍스트에서 평가되므로,
    부모 launch 파일이 선언하지 않은 인자를 넘기면
    ``launch configuration '<name>' does not exist`` 로 런치 전체가 즉시 종료된다.
    """
    target = _LAUNCH_DIR / launch_file
    declared = _declared_argument_names(target) | _declared_argument_names(_COMMON_ARGS_FILE)

    missing = sorted(_include_argument_references(target) - declared)

    assert not missing, (
        f"{launch_file} 가 선언하지 않은 인자를 include 에 넘기고 있습니다: {missing}. "
        "부모 launch 파일에 DeclareLaunchArgument 를 추가해야 합니다."
    )


def _performed_launch_arguments(entities: list[Any], context: Any) -> dict[str, str]:
    from launch.utilities import perform_substitutions

    def _resolve(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            return perform_substitutions(context, list(value))
        return perform_substitutions(context, [value])

    resolved: dict[str, str] = {}
    for entity in entities:
        for key, value in getattr(entity, "launch_arguments", []) or []:
            resolved[_resolve(key)] = _resolve(value)
    return resolved


@pytest.mark.ros2
def test_stub_engine_uses_wall_clock_time() -> None:
    """stub 엔진은 /clock 을 발행하지 않으므로 하위 스택에 실시간을 넘겨야 한다.

    ``use_sim_time:=true`` 로 두면 시뮬레이션 시계가 없어 하위 노드 타이머가 멈추고,
    30초 뒤 heartbeat watchdog 이 에이전트 노드를 종료한다.
    """
    module = _load_launch_module()
    description = module.generate_launch_description()
    context = _build_context(description, {"sim_engine": "stub", "use_nav2": "false"})

    entities = module.launch_setup(context)
    arguments = _performed_launch_arguments(entities, context)

    assert arguments.get("use_sim_time") == "false", (
        "stub 엔진에서 하위 스택에 use_sim_time:=false 를 넘기지 않았습니다: "
        f"{arguments.get('use_sim_time')!r}"
    )
