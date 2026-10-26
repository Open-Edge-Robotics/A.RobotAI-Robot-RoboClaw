#!/usr/bin/env python3
"""former Nav2 파라미터 계약 검사.

실기에서 반복된 두 가지 회귀를 코드로 막는다.

1. `recovery_alpha_fast/slow != 0.0` -> nav2_amcl SIGSEGV (NAVI_ISSUE.md 3.5절)
2. 코스트맵 기하/인플레이션 불일치 -> 좁은 문에서 경로는 그려지는데 통과 실패
   (global `robot_radius` 가 실측 half-width 보다 크거나, local footprint 와 다르거나,
   `cost_scaling_factor` 가 낮아 inflation 이 계단이 되는 조합)

사용:
  python3 former/scripts/check_nav_params.py                    # former/params 전체
  python3 former/scripts/check_nav_params.py path/to/nav2.yaml
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "PyYAML 이 필요합니다: uv run python former/scripts/check_nav_params.py"
    ) from exc

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = REPO_ROOT / "former" / "params"

# 실측 base_link collision mesh 0.404 x 0.404 (former.urdf.xacro, wheel_separation 0.305)
ROBOT_WIDTH = 0.404
ROBOT_HALF = ROBOT_WIDTH / 2.0
FOOTPRINT_HALF_RANGE = (ROBOT_HALF - 0.005, ROBOT_HALF + 0.02)
MAP_RESOLUTION = 0.05
SCAN_TOPIC = "/scan_restamped"


class StrictLoader(yaml.SafeLoader):
    """중복 키를 오류로 처리한다(YAML 은 마지막 값만 남겨 조용히 덮어쓴다)."""


def _no_duplicates(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"중복 키 '{key}'",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)


def load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=StrictLoader)


def parse_footprint(text) -> list[tuple[float, float]]:
    if isinstance(text, str):
        points = yaml.safe_load(text)
    else:
        points = text
    return [(float(x), float(y)) for x, y in points]


def polygon_radii(points: list[tuple[float, float]]) -> tuple[float, float]:
    """footprint 의 inscribed(가장 가까운 변) / circumscribed(가장 먼 꼭짓점) 반경."""
    inscribed = min(math.hypot(x, y) for x, y in points)
    for i, (x1, y1) in enumerate(points):
        x2, y2 = points[(i + 1) % len(points)]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length == 0:
            continue
        # 원점에서 선분까지의 거리
        t = max(0.0, min(1.0, -(x1 * dx + y1 * dy) / (length * length)))
        px, py = x1 + t * dx, y1 + t * dy
        inscribed = min(inscribed, math.hypot(px, py))
    circumscribed = max(math.hypot(x, y) for x, y in points)
    return inscribed, circumscribed


def inflation_cliff(inscribed: float, radius: float, csf: float) -> float:
    """inflation_radius 경계에서의 cost. 0 에서 크게 끊기면 코스트장이 계단이 된다."""
    if radius <= inscribed:
        return 253.0
    return 252.0 * math.exp(-csf * (radius - inscribed)) + 1.0


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    notes: list[str] = []
    try:
        cfg = load(path)
    except yaml.YAMLError as exc:
        return [f"YAML 파싱 실패: {exc}"]

    amcl = cfg.get("amcl", {}).get("ros__parameters", {})
    for key in ("recovery_alpha_fast", "recovery_alpha_slow"):
        value = amcl.get(key)
        if value is None:
            errors.append(
                f"amcl.{key} 가 없습니다(기본값 0.1/0.001 이 적용되어 크래시 경로가 열릴 수 있음)"
            )
        elif float(value) != 0.0:
            errors.append(f"amcl.{key}={value} — 0.0 이어야 합니다(NAVI_ISSUE.md 3.5절)")
    if str(amcl.get("scan_topic", "")).lstrip("/") != SCAN_TOPIC.lstrip("/"):
        errors.append(f"amcl.scan_topic={amcl.get('scan_topic')!r} — {SCAN_TOPIC} 이어야 합니다")

    local_inscribed = local_circumscribed = global_inscribed = None

    for name in ("local_costmap", "global_costmap"):
        block = cfg.get(name, {}).get(name, {}).get("ros__parameters", {})
        if not block:
            errors.append(f"{name} 설정이 없습니다")
            continue

        radius = block.get("robot_radius")
        footprint = block.get("footprint")
        if footprint is None:
            errors.append(f"{name}: footprint 가 없습니다(robot_radius={radius})")
            continue
        if radius is not None:
            errors.append(
                f"{name}: robot_radius({radius}) 와 footprint 가 동시에 설정되어 있습니다"
                " — footprint 만 남기세요"
            )
        points = parse_footprint(footprint)
        inscribed, circumscribed = polygon_radii(points)

        if not (FOOTPRINT_HALF_RANGE[0] <= inscribed <= FOOTPRINT_HALF_RANGE[1]):
            errors.append(
                f"{name}.footprint inscribed={inscribed:.3f} m — 실측 half-width"
                f" {ROBOT_HALF:.3f} m 범위({FOOTPRINT_HALF_RANGE[0]:.3f}~{FOOTPRINT_HALF_RANGE[1]:.3f})"
                " 를 벗어납니다"
            )

        inflation = block.get("inflation_layer", {})
        inflation_radius = float(inflation.get("inflation_radius", 0.0))
        csf = float(inflation.get("cost_scaling_factor", 0.0))
        if not inflation.get("plugin"):
            errors.append(f"{name}: inflation_layer plugin 이 없습니다")
        if inflation_radius <= inscribed:
            errors.append(
                f"{name}.inflation_radius({inflation_radius}) <= inscribed({inscribed:.3f})"
                " — 인플레이션이 동작하지 않습니다"
            )
        if csf <= 0:
            errors.append(f"{name}.cost_scaling_factor={csf} — 양수여야 합니다")
        cliff = inflation_cliff(inscribed, inflation_radius, csf)
        if cliff > 140:
            errors.append(
                f"{name}: inflation 경계 cost={cliff:.0f} 로 절벽이 큽니다"
                f" (cost_scaling_factor {csf} 를 올리거나 inflation_radius 를 줄이세요)"
            )
        else:
            notes.append(f"{name}: inflation 경계 cost={cliff:.0f} (양호)")

        resolution = block.get("resolution")
        if name == "global_costmap" and resolution is not None:
            if abs(float(resolution) - MAP_RESOLUTION) > 1e-6:
                errors.append(
                    f"{name}.resolution={resolution} — 지도 해상도 {MAP_RESOLUTION} 와 일치시켜야"
                    " 셀 수를 4배로 늘리지 않습니다"
                )

        for layer_name, source_name in (("obstacle_layer", "scan"), ("voxel_layer", "scan")):
            layer = block.get(layer_name)
            if not layer:
                continue
            topic = layer.get(source_name, {}).get("topic")
            if str(topic or "").lstrip("/") != SCAN_TOPIC.lstrip("/"):
                errors.append(
                    f"{name}.{layer_name}.{source_name}.topic={topic!r} — {SCAN_TOPIC} 이어야 합니다"
                )

        if name == "global_costmap":
            global_inscribed = inscribed
        else:
            local_inscribed, local_circumscribed = inscribed, circumscribed
    controller = cfg.get("controller_server", {}).get("ros__parameters", {})
    follow = controller.get("FollowPath", {})
    critics = follow.get("critics") or []
    if "BaseObstacle" not in critics:
        notes.append("FollowPath.critics 에 BaseObstacle 이 없습니다(중심점 충돌 검사 없음)")
    if "ObstacleFootprint" not in critics:
        notes.append(
            "FollowPath.critics 에 ObstacleFootprint 이 없습니다 — 코스트맵은 넓게 부풀리는데"
            " footprint 충돌 검사는 하지 않는 비대칭 상태입니다"
        )

    try:
        if (
            global_inscribed is not None
            and local_inscribed is not None
            and global_inscribed != local_inscribed
        ):
            errors.append(
                f"global({global_inscribed:.3f}) 과 local({local_inscribed:.3f}) inscribed 반경이"
                " 다릅니다 — 전역 경로와 국소 제어의 통과 판정이 어긋납니다"
            )
    except TypeError:
        pass

    speed = follow.get("max_vel_x")
    if speed is not None:
        notes.append(f"max_vel_x={speed}")

    print(f"{path.name}")
    if local_inscribed is not None and global_inscribed is not None:
        print(
            f"  local  inscribed={local_inscribed:.3f} circumscribed={local_circumscribed:.3f}"
            f" -> 전역 계획 최소 문 폭 {2 * global_inscribed:.2f} m,"
            f" spin/backup 최소 통로 {2 * (local_inscribed + local_circumscribed):.2f} m"
        )
    for note in notes:
        print(f"  note: {note}")
    for error in errors:
        print(f"  ERROR: {error}")
    return errors


def main(argv: list[str]) -> int:
    paths = [Path(p) for p in argv[1:]]
    if not paths:
        paths = sorted(DEFAULT_DIR.glob("*.yaml"))
    if not paths:
        raise SystemExit(f"검사할 파라미터 파일이 없습니다: {DEFAULT_DIR}")
    failed = 0
    for path in paths:
        if not path.is_file():
            print(f"{path}: 파일이 없습니다")
            failed += 1
            continue
        if check_file(path):
            failed += 1
        print()
    print("결과:", "FAIL" if failed else "PASS", f"({len(paths) - failed}/{len(paths)} 통과)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
