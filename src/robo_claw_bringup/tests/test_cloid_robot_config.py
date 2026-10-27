"""CLOiD 로봇 기본 프로필(cloid_config.yaml / ROBOT_LIMITS.cloid.json / SKILLS.cloid.<개체>.md) 검증.

CLOiD 는 ROS 2 토픽 이름과 제어 경로가 기존 stretch3/former/butler 프로필과 다르다.
프로필 파일은 로봇마다 손으로 관리되므로, 오탈자나 launch 배선 누락이 실제 로봇에서
조용히 "센서 없음/스킬 없음"으로 나타난다. 이 테스트는 다음을 고정한다.

- 프로필 파일이 존재하고 파싱된다.
- ``sensor_names`` 와 ``sensor_topics`` 가 일치한다.
- ``ROBOT_LIMITS.cloid.json`` 의 조작 하드 한계가 유효하다(조작은 기본 비활성).
- ``robot_config:=cloid`` 로 launch 를 평가하면 agent 노드가 cloid 전용
  skills guide / limits 파일을 받고, launch 가 cloid 의 카메라/IMU 토픽을 덮어쓰지
  않으며, butler 전용 스킬 모듈이 주입되지 않는다.
  (조작 비활성은 `cloid_config.yaml` 값으로 검증한다.)
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from launch import LaunchContext  # pyright: ignore[reportMissingImports]
from launch.actions import DeclareLaunchArgument  # pyright: ignore[reportMissingImports]
from launch.utilities import perform_substitutions  # pyright: ignore[reportMissingImports]
from launch_ros.actions import Node  # pyright: ignore[reportMissingImports]
from launch_ros.utilities import evaluate_parameters  # pyright: ignore[reportMissingImports]

pytestmark = pytest.mark.unit

_PKG_DIR = Path(__file__).resolve().parents[1]
_CONFIG_DIR = _PKG_DIR / "config"
_LAUNCH_FILE = _PKG_DIR / "launch" / "robo_claw.launch.py"

_CLOID_CONFIG = _CONFIG_DIR / "cloid_config.yaml"
_CLOID_LIMITS = _CONFIG_DIR / "ROBOT_LIMITS.cloid.json"
# CLOiD 는 개체 역할이 달라 스킬 가이드와 소울 프로필을 1호/2호로 분리한다.
_CLOID_SKILLS_UNIT_1 = _CONFIG_DIR / "SKILLS.cloid.1.md"
_CLOID_SKILLS_UNIT_2 = _CONFIG_DIR / "SKILLS.cloid.2.md"
_CLOID_SOUL_UNIT_1 = _CONFIG_DIR / "ROBOT.cloid.1.md"
_CLOID_SOUL_UNIT_2 = _CONFIG_DIR / "ROBOT.cloid.2.md"


def _load_launch_module():
    spec = importlib.util.spec_from_file_location("probe_robo_claw_launch_cloid", _LAUNCH_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_context(launch_description, overrides: dict[str, str]) -> LaunchContext:
    context = LaunchContext()
    for entity in launch_description.entities:
        if isinstance(entity, DeclareLaunchArgument):
            default = entity.default_value
            value = perform_substitutions(context, default) if default is not None else ""
            context.launch_configurations[entity.name] = overrides.get(entity.name, value)
    for key, value in overrides.items():
        context.launch_configurations[key] = value
    return context


def _find_node(entities, executable: str) -> Node:
    for entity in entities:
        if isinstance(entity, Node) and entity.node_executable == executable:
            return entity
    raise AssertionError(f"노드를 찾을 수 없습니다: {executable}")


def _evaluated_params(context: LaunchContext, node: Node) -> dict:
    merged: dict = {}
    for param in evaluate_parameters(context, node._Node__parameters):  # noqa: SLF001
        if isinstance(param, dict):
            merged.update(param)
    return merged


def _raw_param_paths(context: LaunchContext, node: Node) -> list[str]:
    """노드 파라미터 목록에 전달된 YAML 파일 경로만 추린다.

    launch_ros 는 경로 문자열을 ``ParameterFile`` 로 정규화하므로 ``evaluate`` 로
    실제 경로를 얻는다.
    """
    paths: list[str] = []
    for param in node._Node__parameters:  # noqa: SLF001
        if isinstance(param, str):
            paths.append(param)
        elif hasattr(param, "evaluate"):
            paths.append(str(param.evaluate(context)))
    return paths


def _run_launch_cloid() -> tuple[LaunchContext, list]:
    module = _load_launch_module()
    launch_description = module.generate_launch_description()
    context = _build_context(launch_description, {"robot_config": "cloid"})
    return context, module._launch_setup(context)  # noqa: SLF001


# ── 프로필 파일 자체 검증 ──


@pytest.mark.parametrize(
    "path",
    [
        _CLOID_CONFIG,
        _CLOID_LIMITS,
        _CLOID_SKILLS_UNIT_1,
        _CLOID_SKILLS_UNIT_2,
        _CLOID_SOUL_UNIT_1,
        _CLOID_SOUL_UNIT_2,
    ],
)
def test_cloid_profile_files_exist(path: Path) -> None:
    assert path.is_file(), f"CLOiD 프로필 파일이 없습니다: {path}"
    assert path.read_text(encoding="utf-8").strip(), f"CLOiD 프로필 파일이 비어 있습니다: {path}"


def test_cloid_unit_skill_guides_differ_by_deployment_role() -> None:
    """1호와 2호는 모두 상체 모션을 쓸 수 있고, 1호 전용 정리 시연만 구분돼야 한다."""
    unit_1 = _CLOID_SKILLS_UNIT_1.read_text(encoding="utf-8")
    unit_2 = _CLOID_SKILLS_UNIT_2.read_text(encoding="utf-8")

    # 1호: 주행과 상체(팔·waist·neck) 모션을 모두 사용할 수 있다.
    assert "1호" in unit_1
    assert "execute_cloid_motion" in unit_1
    assert "사용할 수 있으며" in unit_1 or "모두 사용 가능" in unit_1

    # 2호: 상체 모션 경로를 사용할 수 있고, "구동 오류" 같은 불가 서술은 남기지 않는다.
    assert "2호" in unit_2
    assert "execute_cloid_motion" in unit_2
    assert "모두 사용 가능" in unit_2
    assert "구동 오류" not in unit_2

    # 정리 시연(`cleanup_demo`)도 2호에서 사용하며, allowlist가 필수다.
    assert "cleanup_demo" in unit_2
    assert "allowlist" in unit_2

    # 두 문서는 서로 다른 개체 전용이므로 동일 문서를 공유하지 않는다.
    assert unit_1 != unit_2


def test_cloid_unit_soul_profiles_differ_by_deployment_role() -> None:
    """소울 프로필은 1호/2호 개성을 구분하되, 2호 구동 오류 서술은 남기지 않아야 한다."""
    unit_1 = _CLOID_SOUL_UNIT_1.read_text(encoding="utf-8")
    unit_2 = _CLOID_SOUL_UNIT_2.read_text(encoding="utf-8")

    # 소울은 기능 규칙을 반복하지 않고 개성만 정의한다.
    for soul in (unit_1, unit_2):
        assert "나는 누구인가 (Identity)" in soul
        assert "커뮤니케이션 스타일 (Communication Style)" in soul
        assert "SKILLS.md" in soul

    # 1호: 상체 모션까지 온전히 쓰는 개체로 자신을 소개한다.
    assert "CLOiD 휴머노이드 1호" in unit_1
    assert "정상적으로 쓸 수 있는 상태" in unit_1

    # 2호: 등록 모션까지 쓰는 개체로 자신을 소개하고, 수리 전 오류 서술을 남기지 않는다.
    assert "CLOiD 휴머노이드 2호" in unit_2
    assert "등록 모션" in unit_2
    assert "구동 오류" not in unit_2
    assert "실행하지 않은 동작을 완료했다고 말하지 않는다" in unit_2

    assert unit_1 != unit_2


def test_cloid_config_sensor_mapping_is_consistent() -> None:
    config = yaml.safe_load(_CLOID_CONFIG.read_text(encoding="utf-8"))
    core = config["robo_claw_core_node"]["ros__parameters"]

    sensor_names = set(core["sensor_names"])
    sensor_topics = core["sensor_topics"]
    assert sensor_names == set(sensor_topics), "sensor_names 와 sensor_topics 키가 다릅니다."
    for name in sensor_names:
        assert str(sensor_topics[name]).startswith("/"), f"{name} 토픽은 절대 경로여야 합니다."

    # CLOiD 는 2D LaserScan 을 발행하지 않으므로 lidar 를 등록하면 안 된다.
    assert "lidar" not in sensor_names

    assert core["cmd_vel_topic"] == "/cmd_vel"
    assert core["odom_topic"] == "/odom"


def test_cloid_agent_topics_and_manipulation_gating() -> None:
    config = yaml.safe_load(_CLOID_CONFIG.read_text(encoding="utf-8"))
    agent = config["robo_claw_agent_node"]["ros__parameters"]

    # CLOiD 의 RGB 카메라는 CompressedImage 만 발행한다.
    assert "compressed" in agent["camera_topic"]
    assert agent["imu_topic"] == "/chest_imu_broadcaster/imu"
    assert agent["manipulation_enabled"] is False
    assert agent["cloid_cleanup_indicator_enabled"] is False


def test_cloid_limits_is_valid_and_manipulation_disabled() -> None:
    limits = json.loads(_CLOID_LIMITS.read_text(encoding="utf-8"))

    nav = limits["navigation"]
    assert float(nav["max_relative_distance_m"]) > 0.0
    assert float(nav["max_rotation_deg"]) > 0.0

    manip = limits["manipulation"]
    assert manip["enabled"] is False

    joint_limits = manip["joint_limits"]
    for joint, rng in joint_limits.items():
        assert isinstance(rng, list) and len(rng) == 2, f"{joint} 범위 형식 오류: {rng}"
        low, high = float(rng[0]), float(rng[1])
        assert low < high, f"{joint} 하한이 상한 이상입니다: {rng}"

    # URDF(hmc_v2_hand) 기준 대표 조인트가 누락되지 않았는지 고정한다.
    for joint in ("waist_joint_1", "neck_joint_1", "left_arm_joint_1", "right_arm_joint_7"):
        assert joint in joint_limits, f"CLOiD 조인트 한계 누락: {joint}"


# ── launch 배선 검증 ──


def test_launch_binds_cloid_profiles_to_agent_node() -> None:
    context, entities = _run_launch_cloid()
    agent = _evaluated_params(context, _find_node(entities, "agent_node"))

    assert str(agent["skills_guide_file"]).endswith("SKILLS.cloid.1.md")
    assert str(agent["robot_soul_file"]).endswith("ROBOT.cloid.1.md")
    assert str(agent["robot_limits_file"]).endswith("ROBOT_LIMITS.cloid.json")

    # cloid_config.yaml 의 카메라/IMU 토픽을 launch 가 덮어쓰지 않아야 한다.
    # (덮어쓰면 센서 이름에 camera 가 들어간 항목이 agent 카메라를 raw depth 로 바꿔버린다.)
    assert "camera_topic" not in agent
    assert "imu_topic" not in agent

    # 로봇 전용 모션 스킬은 CLOi 프로필에만 주입하며 Butler 스크립트 스킬은 제외한다.
    assert "robo_claw_agent.skills.cloid_motion_skill" in agent["skill_modules"]
    # 포장용 최소 부피(boxing) 자세는 전체 CLOi 개체 전용이므로 cloid 프로필이면 항상 등록한다.
    assert "robo_claw_agent.skills.cloid_packing_pose_skill" in agent["skill_modules"]
    assert "robo_claw_agent.skills.cleanup_demo_skill" not in agent["skill_modules"]
    assert "robo_claw_agent.skills.butler_skill" not in agent["skill_modules"]


def test_packing_pose_skill_is_registered_for_every_cloid_unit() -> None:
    """packing pose 스킬은 cloid1/cloid2 구분 없이 cloid 프로필이면 등록된다."""
    module = _load_launch_module()
    launch_description = module.generate_launch_description()

    for agent_id in ("cloid01", "cloi02", "cloid"):
        context = _build_context(
            launch_description,
            {"robot_config": "cloid", "agent_id": agent_id},
        )
        entities = module._launch_setup(context)  # noqa: SLF001
        agent = _evaluated_params(context, _find_node(entities, "agent_node"))
        assert "robo_claw_agent.skills.cloid_packing_pose_skill" in agent["skill_modules"]


def test_packing_pose_skill_is_not_registered_for_non_cloid_profiles() -> None:
    module = _load_launch_module()
    launch_description = module.generate_launch_description()

    for robot_config in ("sim", "former", "butler"):
        context = _build_context(launch_description, {"robot_config": robot_config})
        entities = module._launch_setup(context)  # noqa: SLF001
        agent = _evaluated_params(context, _find_node(entities, "agent_node"))
        assert "robo_claw_agent.skills.cloid_packing_pose_skill" not in agent["skill_modules"]


def test_cleanup_demo_defaults_to_scan_and_wipe_motion_for_cloid1() -> None:
    module = _load_launch_module()
    launch_description = module.generate_launch_description()
    context = _build_context(
        launch_description,
        {
            "robot_config": "cloid",
            "agent_id": "cloid01",
            # 실기 AI Config Server 가이드 파일은 generic SKILLS.md 이름을 사용합니다.
            "skills_guide_file": "/cache/cloid_cloi01/SKILLS.md",
        },
    )

    assert context.launch_configurations["cloid_cleanup_demo_enabled"] == "true"
    entities = module._launch_setup(context)  # noqa: SLF001
    agent = _evaluated_params(context, _find_node(entities, "agent_node"))

    assert "robo_claw_agent.skills.cleanup_demo_skill" in agent["skill_modules"]
    assert agent["cloid_cleanup_demo_motion_ids_json"] == "[22,60]"


def test_cleanup_demo_registers_for_cloid2_with_explicit_allowlist() -> None:
    """2호도 검토된 allowlist가 있으면 정리 시연 스킬을 등록해야 한다."""
    module = _load_launch_module()
    launch_description = module.generate_launch_description()
    context = _build_context(
        launch_description,
        {
            "robot_config": "cloid",
            "agent_id": "cloid02",
            "skills_guide_file": "/cache/cloid_cloi02/SKILLS.md",
            "cloid_cleanup_demo_enabled": "true",
            "cloid_cleanup_demo_motion_ids_json": "[22]",
        },
    )
    entities = module._launch_setup(context)  # noqa: SLF001
    agent = _evaluated_params(context, _find_node(entities, "agent_node"))

    assert "robo_claw_agent.skills.cleanup_demo_skill" in agent["skill_modules"]
    assert agent["cloid_cleanup_demo_motion_ids_json"] == "[22]"


def test_cleanup_demo_is_not_registered_for_cloid2_without_allowlist() -> None:
    """2호 기본값은 빈 allowlist이므로 검토된 모션 ID 없이는 등록되지 않는다."""
    module = _load_launch_module()
    launch_description = module.generate_launch_description()
    context = _build_context(
        launch_description,
        {
            "robot_config": "cloid",
            "agent_id": "cloid02",
            "skills_guide_file": str(_CLOID_SKILLS_UNIT_2),
            "cloid_cleanup_demo_enabled": "true",
        },
    )
    entities = module._launch_setup(context)  # noqa: SLF001
    agent = _evaluated_params(context, _find_node(entities, "agent_node"))

    assert "robo_claw_agent.skills.cleanup_demo_skill" not in agent["skill_modules"]
    assert "cloid_cleanup_demo_motion_ids_json" not in agent


def test_cleanup_demo_is_not_registered_for_non_cloid_profile() -> None:
    module = _load_launch_module()
    launch_description = module.generate_launch_description()
    context = _build_context(
        launch_description,
        {
            "robot_config": "former",
            "cloid_cleanup_demo_enabled": "true",
            "cloid_cleanup_demo_motion_ids_json": "[22]",
        },
    )
    entities = module._launch_setup(context)  # noqa: SLF001
    agent = _evaluated_params(context, _find_node(entities, "agent_node"))

    assert "robo_claw_agent.skills.cleanup_demo_skill" not in agent["skill_modules"]


def test_cleanup_demo_accepts_cloid1_allowlist_with_motion_60() -> None:
    module = _load_launch_module()
    launch_description = module.generate_launch_description()
    context = _build_context(
        launch_description,
        {
            "robot_config": "cloid",
            "agent_id": "cloid01",
            "cloid_cleanup_demo_enabled": "true",
            "cloid_cleanup_demo_motion_ids_json": "[22,60]",
        },
    )
    entities = module._launch_setup(context)  # noqa: SLF001
    agent = _evaluated_params(context, _find_node(entities, "agent_node"))

    assert "robo_claw_agent.skills.cleanup_demo_skill" in agent["skill_modules"]
    assert agent["cloid_cleanup_demo_motion_ids_json"] == "[22,60]"


@pytest.mark.parametrize("motion_ids", ["not-json", "[]"])
def test_cleanup_demo_rejects_empty_or_malformed_motion_allowlist(motion_ids: str) -> None:
    module = _load_launch_module()
    launch_description = module.generate_launch_description()
    context = _build_context(
        launch_description,
        {
            "robot_config": "cloid",
            "agent_id": "cloid01",
            "cloid_cleanup_demo_enabled": "true",
            "cloid_cleanup_demo_motion_ids_json": motion_ids,
        },
    )
    entities = module._launch_setup(context)  # noqa: SLF001
    agent = _evaluated_params(context, _find_node(entities, "agent_node"))

    assert "robo_claw_agent.skills.cleanup_demo_skill" not in agent["skill_modules"]
    assert "cloid_cleanup_demo_motion_ids_json" not in agent


def test_launch_explicit_skills_guide_selects_unit_2() -> None:
    """2호 개체는 skills_guide_file 로 개체 전용 가이드를 명시해야 한다."""
    module = _load_launch_module()
    launch_description = module.generate_launch_description()
    context = _build_context(
        launch_description,
        {
            "robot_config": "cloid",
            "skills_guide_file": str(_CLOID_SKILLS_UNIT_2),
        },
    )
    entities = module._launch_setup(context)  # noqa: SLF001
    agent = _evaluated_params(context, _find_node(entities, "agent_node"))

    assert str(agent["skills_guide_file"]).endswith("SKILLS.cloid.2.md")


def test_launch_explicit_soul_file_selects_unit_2() -> None:
    """2호 개체는 robot_soul_file 로 개체 전용 소울 프로필을 명시해야 한다."""
    module = _load_launch_module()
    launch_description = module.generate_launch_description()
    context = _build_context(
        launch_description,
        {
            "robot_config": "cloid",
            "robot_soul_file": str(_CLOID_SOUL_UNIT_2),
        },
    )
    entities = module._launch_setup(context)  # noqa: SLF001
    agent = _evaluated_params(context, _find_node(entities, "agent_node"))

    assert str(agent["robot_soul_file"]).endswith("ROBOT.cloid.2.md")


def test_launch_explicit_topic_override_wins() -> None:
    module = _load_launch_module()
    launch_description = module.generate_launch_description()
    context = _build_context(
        launch_description, {"robot_config": "cloid", "imu_topic": "/custom/imu"}
    )
    entities = module._launch_setup(context)  # noqa: SLF001
    agent = _evaluated_params(context, _find_node(entities, "agent_node"))

    assert agent["imu_topic"] == "/custom/imu"


def test_launch_passes_cloid_yaml_to_core_and_agent_nodes() -> None:
    context, entities = _run_launch_cloid()

    core_paths = _raw_param_paths(context, _find_node(entities, "core_node"))
    assert any(str(p).endswith("cloid_config.yaml") for p in core_paths)

    agent_paths = _raw_param_paths(context, _find_node(entities, "agent_node"))
    assert any(str(p).endswith("agent.yaml") for p in agent_paths)
    assert any(str(p).endswith("cloid_config.yaml") for p in agent_paths)


def test_cloid_expression_skill_is_registered_only_when_enabled() -> None:
    """상황별 표현 모션 스킬은 cloid + cloid_expression_enabled=true 일 때만 등록된다."""
    module = _load_launch_module()
    launch_description = module.generate_launch_description()

    def _modules(overrides: dict[str, str]) -> list[str]:
        context = _build_context(launch_description, overrides)
        entities = module._launch_setup(context)  # noqa: SLF001
        agent = _evaluated_params(context, _find_node(entities, "agent_node"))
        return agent["skill_modules"]

    assert "robo_claw_agent.skills.cloid_express_skill" not in _modules(
        {"robot_config": "cloid"}
    )
    assert "robo_claw_agent.skills.cloid_express_skill" in _modules(
        {"robot_config": "cloid", "cloid_expression_enabled": "true"}
    )
    assert "robo_claw_agent.skills.cloid_express_skill" not in _modules(
        {"robot_config": "sim", "cloid_expression_enabled": "true"}
    )


def test_cloid_navigation_pose_skill_is_registered_only_when_enabled() -> None:
    """주행 전 boxing 자세 prelude 는 cloid + cloid_navigation_pose_enabled=true 일 때만 등록된다."""
    module = _load_launch_module()
    launch_description = module.generate_launch_description()

    def _modules(overrides: dict[str, str]) -> list[str]:
        context = _build_context(launch_description, overrides)
        entities = module._launch_setup(context)  # noqa: SLF001
        agent = _evaluated_params(context, _find_node(entities, "agent_node"))
        return agent["skill_modules"]

    # 기본값은 꺼짐이므로 스킬 모듈도 등록되지 않는다(게이트가 동작하지 않는다).
    assert "robo_claw_agent.skills.cloid_navigation_pose_skill" not in _modules(
        {"robot_config": "cloid"}
    )
    # 개체 구분 없이 cloid 프로필이면 스위치로 켤 수 있다(개체 설정으로 끈다).
    for agent_id in ("cloid01", "cloi02"):
        assert "robo_claw_agent.skills.cloid_navigation_pose_skill" in _modules(
            {"robot_config": "cloid", "cloid_navigation_pose_enabled": "true", "agent_id": agent_id}
        )
    assert "robo_claw_agent.skills.cloid_navigation_pose_skill" not in _modules(
        {"robot_config": "sim", "cloid_navigation_pose_enabled": "true"}
    )
    assert "robo_claw_agent.skills.cloid_navigation_pose_skill" not in _modules(
        {"robot_config": "former", "cloid_navigation_pose_enabled": "true"}
    )
