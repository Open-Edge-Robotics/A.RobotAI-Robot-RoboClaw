import importlib.util
import math
from pathlib import Path

_ORIENTATION_MODULE_PATH = (
    Path(__file__).parents[1]
    / "robo_claw_agent"
    / "skills"
    / "navigation_skill"
    / "orientation.py"
)
_ORIENTATION_SPEC = importlib.util.spec_from_file_location(
    "test_navigation_orientation", _ORIENTATION_MODULE_PATH
)
assert _ORIENTATION_SPEC and _ORIENTATION_SPEC.loader
_ORIENTATION_MODULE = importlib.util.module_from_spec(_ORIENTATION_SPEC)
_ORIENTATION_SPEC.loader.exec_module(_ORIENTATION_MODULE)

direction_to_yaw_rad = _ORIENTATION_MODULE.direction_to_yaw_rad
shortest_angular_delta_rad = _ORIENTATION_MODULE.shortest_angular_delta_rad
yaw_to_point_rad = _ORIENTATION_MODULE.yaw_to_point_rad
yaw_targets_from_offsets_rad = _ORIENTATION_MODULE.yaw_targets_from_offsets_rad


def test_direction_to_yaw_rad_supports_cardinal_names():
    assert math.isclose(direction_to_yaw_rad("east"), 0.0)
    assert math.isclose(direction_to_yaw_rad("북쪽"), math.pi / 2.0)
    assert math.isclose(direction_to_yaw_rad("west"), math.pi)
    assert math.isclose(direction_to_yaw_rad("남쪽"), -math.pi / 2.0)


def test_shortest_angular_delta_wraps_to_minimal_rotation():
    current = math.radians(170.0)
    target = math.radians(-170.0)

    assert math.isclose(shortest_angular_delta_rad(current, target), math.radians(20.0))


def test_yaw_to_point_rad_returns_bearing():
    assert math.isclose(yaw_to_point_rad(0.0, 0.0, 0.0, 1.0), math.pi / 2.0)
    assert yaw_to_point_rad(0.0, 0.0, 0.01, 0.01) is None


def test_yaw_targets_from_offsets_rad_uses_start_yaw_as_absolute_anchor():
    targets = yaw_targets_from_offsets_rad(
        math.radians(30.0),
        [0.0, -math.pi / 2.0, math.pi, math.pi / 2.0],
    )

    assert [round(math.degrees(target), 1) for target in targets] == [
        30.0,
        -60.0,
        -150.0,
        120.0,
    ]
