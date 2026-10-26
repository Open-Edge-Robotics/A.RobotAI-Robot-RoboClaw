#!/usr/bin/env python3
"""Nav2 지도(PGM) 진단·정리 도구.

former 실기 지도(RTAB-Map grid map -> PGM)는 벽이 5~10 cm 로 얇고, 고립 점과
1셀 구멍이 많다. 고립 점 하나가 inflation_radius 만큼 부풀면 0.7 m 문을 닫아
버리므로, 배포 전에 지도를 정리하고 통로 폭을 측정한다.

사용 예:
  # 통로 병목 폭과 코스트맵 설정별 통과 여부 진단
  python3 former/scripts/nav_map_tools.py analyze former/maps/map_konjiam_L_107-1.pgm

  # 고립 점 제거 + 1셀 벽 구멍 폐쇄
  python3 former/scripts/nav_map_tools.py clean \
      former/maps/map_konjiam_L_107-1.pgm \
      former/maps/map_konjiam_L_107-1_clean.pgm

  # 두 지점 사이를 지나는 경로의 최소 통과 폭(가장 좁은 병목)
  python3 former/scripts/nav_map_tools.py route former/maps/map_konjiam_L_107-1.pgm \
      -0.731,4.521 6.59,-2.28

정리 규칙(둘 다 보수적):
  1. occupied 이웃이 1개 이하인 occupied 셀을 제거한다(= 레이저/RTAB 잡음 점).
     두 셀 이상 이어진 벽이나 물체는 건드리지 않는다.
  2. 상하 또는 좌우가 모두 occupied 인 비점유 셀을 occupied 로 채운다
     (= 벽 선의 1셀 구멍). 2셀 이상 벌어진 통로는 유지한다.
"""

from __future__ import annotations

import sys

import numpy as np
from scipy import ndimage

FREE_VALUE = 254  # ROS 지도 표준 free 값 (occ_prob = 1/255)
UNKNOWN_VALUE = 205


def load_pgm(path: str) -> np.ndarray:
    data = open(path, "rb").read()
    if data[:2] != b"P5":
        raise SystemExit(f"P5(PGM binary) 형식이 아닙니다: {path}")
    idx, vals = 2, []
    while len(vals) < 3:
        while data[idx : idx + 1].isspace():
            idx += 1
        if data[idx : idx + 1] == b"#":
            while data[idx : idx + 1] != b"\n":
                idx += 1
            continue
        start = idx
        while not data[idx : idx + 1].isspace():
            idx += 1
        vals.append(int(data[start:idx]))
    idx += 1
    width, height, _maxval = vals
    img = np.frombuffer(data[idx : idx + width * height], dtype=np.uint8)
    return img.reshape(height, width).copy()


def save_pgm(path: str, img: np.ndarray) -> None:
    height, width = img.shape
    with open(path, "wb") as f:
        f.write(f"P5\n{width} {height}\n255\n".encode())
        f.write(img.astype(np.uint8).tobytes())


def masks(img: np.ndarray):
    # map_server trinary 규칙(negate=0)과 동일하게 판정한다.
    occ_prob = (255.0 - img.astype(np.float32)) / 255.0
    occupied = occ_prob > 0.65  # occupied_thresh
    free = occ_prob < 0.196  # free_thresh
    unknown = ~occupied & ~free
    return occupied, free, unknown


def occupied_neighbour_count(occupied: np.ndarray) -> np.ndarray:
    kernel = np.ones((3, 3), np.uint8)
    return ndimage.convolve(occupied.astype(np.uint8), kernel, mode="constant") - occupied


def disk(radius_cells: int) -> np.ndarray:
    y, x = np.ogrid[-radius_cells : radius_cells + 1, -radius_cells : radius_cells + 1]
    return (x * x + y * y <= radius_cells * radius_cells).astype(np.uint8)


def clean(
    img: np.ndarray, resolution: float = 0.05, min_passage: float = 0.0
) -> tuple[np.ndarray, dict]:
    """잡음 점과 1셀 벽 구멍을 정리하고, 선택적으로 좁은 통로를 막는다."""
    occupied = masks(img)[0]
    stats = {"isolated_removed": 0, "holes_filled": 0}

    # 1) 고립 점 제거를 수렴할 때까지 반복한다(이웃 제거로 새로 고립되는 셀 처리).
    for _ in range(4):
        nb = occupied_neighbour_count(occupied)
        isolated = occupied & (nb <= 1)
        removed = int(isolated.sum())
        if removed == 0:
            break
        stats["isolated_removed"] += removed
        occupied = occupied & ~isolated

    # 2) 상하/좌우가 모두 occupied 인 1셀 구멍을 벽으로 메운다.
    left = np.zeros_like(occupied)
    right = np.zeros_like(occupied)
    up = np.zeros_like(occupied)
    down = np.zeros_like(occupied)
    left[:, 1:] = occupied[:, :-1]
    right[:, :-1] = occupied[:, 1:]
    up[1:, :] = occupied[:-1, :]
    down[:-1, :] = occupied[1:, :]
    holes = ~occupied & ((left & right) | (up & down))
    stats["holes_filled"] = int(holes.sum())
    occupied = occupied | holes

    # 3) 로봇 폭보다 좁은 통로를 막는다. 0.404 m 폭 로봇은 그보다 좁은 틈을
    #    물리적으로 지나갈 수 없는데, 지도에 틈이 남아 있으면 전역 계획이
    #    그리로 경로를 그린 뒤 실행 단계에서 실패한다.
    if min_passage > 0:
        radius_cells = int(np.ceil(min_passage / 2.0 / resolution))
        sealed = ndimage.binary_closing(occupied, structure=disk(radius_cells))
        stats["sealed_cells"] = int((sealed & ~occupied).sum())
        occupied = sealed
    else:
        stats["sealed_cells"] = 0

    out = img.copy()
    out[img <= 89] = FREE_VALUE
    out[occupied] = 0
    stats["unknown_untouched"] = int(masks(out)[2].sum())
    return out, stats


def clearance(obstacle_mask: np.ndarray, resolution: float) -> np.ndarray:
    return ndimage.distance_transform_edt(~obstacle_mask) * resolution


def bottleneck_widths(
    clr: np.ndarray,
    traversable: np.ndarray,
    resolution: float,
    room_half: float = 0.55,
    min_room_cells: int = 150,
):
    """Widest-bottleneck(merge tree) 방식으로 개실 사이 통로 폭을 측정한다.

    clearance >= room_half 인 영역을 개실로 보고, clearance 를 큰 값부터 낮춰가며
    합집합을 만들 때 서로 다른 개실이 처음 연결되는 지점의 clearance*2 를 통로 폭으로
    본다. 결과는 (폭[m], col, row) 리스트를 폭 내림차순으로 돌려준다.
    """
    height, width = clr.shape
    rooms, n_rooms = ndimage.label((clr >= room_half) & traversable, structure=np.ones((3, 3)))
    if n_rooms:
        sizes = ndimage.sum(np.ones_like(rooms), rooms, range(1, n_rooms + 1))
        keep = {i + 1 for i, size in enumerate(sizes) if size >= min_room_cells}
    else:
        keep = set()
    room_of = np.where(np.isin(rooms, list(keep)), rooms, 0) if keep else np.zeros_like(rooms)

    parent: dict[int, int] = {}
    labels: dict[int, set] = {}

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    rows, cols = np.nonzero(traversable)
    order = np.argsort(-clr[rows, cols])
    events: list[tuple[float, int, int]] = []
    for i in order:
        y, x = int(rows[i]), int(cols[i])
        node = y * width + x
        parent[node] = node
        room = int(room_of[y, x])
        labels[node] = {room} if room else set()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            ny, nx = y + dy, x + dx
            if not (0 <= ny < height and 0 <= nx < width) or not traversable[ny, nx]:
                continue
            other = ny * width + nx
            if other not in parent:
                continue
            root_a, root_b = find(node), find(other)
            if root_a == root_b:
                continue
            lab_a, lab_b = labels[root_a], labels[root_b]
            if lab_a and lab_b and not (lab_a & lab_b):
                events.append((2 * float(clr[y, x]), x, y))
            parent[root_b] = root_a
            labels[root_a] = lab_a | lab_b
            node = find(node)
    events.sort(key=lambda e: -e[0])
    return events, len(keep)


def inflation_cost(distance: float, inscribed: float, radius: float, csf: float) -> float:
    """nav2_costmap_2d::InflationLayer 비용식."""
    if distance <= 0:
        return 254.0
    if distance <= inscribed:
        return 253.0
    if distance > radius:
        return 0.0
    return 252.0 * float(np.exp(-csf * (distance - inscribed))) + 1.0


def maximin_width(
    img: np.ndarray, start: tuple[int, int], goal: tuple[int, int], resolution: float
) -> float:
    """두 셀 사이 경로 중 가장 넓은 통로의 폭(m).

    clearance 임계값을 높여가며 연결성을 이분 탐색하는 maximin(가장 넓은 병목) 경로
    계산이다. "이 두 지점 사이를 지나려면 최소 몇 m 문을 통과해야 하는가" 를 준다.
    """
    occupied = masks(img)[0]
    clr = clearance(occupied, resolution)
    traversable = ~occupied
    low, high = 0.0, 2.0
    for _ in range(18):
        mid = (low + high) / 2.0
        labels, _count = ndimage.label(traversable & (clr > mid), structure=np.ones((3, 3)))
        if labels[start] and labels[start] == labels[goal]:
            low = mid
        else:
            high = mid
    return 2 * low


def world_to_index(
    x: float, y: float, height: int, resolution: float, origin: tuple[float, float]
) -> tuple[int, int]:
    row = height - 1 - int(round((y - origin[1]) / resolution))
    col = int(round((x - origin[0]) / resolution))
    return row, col


def cmd_route(path: str, points: list[str], resolution: float, origin: tuple[float, float]) -> int:
    img = load_pgm(path)
    height = img.shape[0]
    coords = [tuple(float(v) for v in p.split(",")) for p in points]
    if len(coords) < 2:
        raise SystemExit("route 는 좌표가 최소 2개 필요합니다: x1,y1 x2,y2 ...")
    base = world_to_index(*coords[0], height, resolution, origin)
    for target in coords[1:]:
        goal = world_to_index(*target, height, resolution, origin)
        width = maximin_width(img, base, goal, resolution)
        print(
            f"  ({coords[0][0]:6.2f},{coords[0][1]:6.2f}) -> "
            f"({target[0]:6.2f},{target[1]:6.2f}) : 최소 통과 폭 {width:.2f} m"
        )
    return 0


def cmd_analyze(path: str, resolution: float, origin: tuple[float, float]) -> int:
    img = load_pgm(path)
    occupied, free, unknown = masks(img)
    traversable = ~occupied
    clr = clearance(occupied, resolution)
    nb = occupied_neighbour_count(occupied)

    total = img.size
    print(f"map: {img.shape[1]} x {img.shape[0]} @ {resolution} m")
    print(
        f"  free={int(free.sum())} ({free.sum() / total:.1%})"
        f"  unknown={int(unknown.sum())} ({unknown.sum() / total:.1%})"
        f"  occupied={int(occupied.sum())}"
    )
    print(
        f"  고립 occupied(이웃<=1)={int((occupied & (nb <= 1)).sum())}"
        f"  얇은 occupied(이웃<=2)={int((occupied & (nb <= 2)).sum())}"
    )

    events, n_rooms = bottleneck_widths(clr, traversable, resolution)
    print(f"\n개실(clearance>={0.55 * 2:.1f} m) {n_rooms}개, 병목 후보 {len(events)}개")
    print("  폭[m]  map(x,y)            반경[m]")
    height = img.shape[0]
    for width, x, y in events[:14]:
        wx = x * resolution + origin[0]
        # 지도 이미지 row 0 은 최대 y 다(map_server 와 동일하게 뒤집어 계산).
        wy = (height - 1 - y) * resolution + origin[1]
        print(f"  {width:5.2f}  ({wx:6.2f},{wy:6.2f})     {width / 2:5.2f}")

    # 코스트맵 임계값별로 계획 가능한 면적과 단절 여부를 본다. 이 값이 지도 정리 전후
    # 비교와 파라미터(inscribed radius) 선택의 기준이다.
    print("\nclearance 임계값별 계획 가능 면적 (largest = 최대 연결 영역)")
    for threshold in (0.205, 0.26, 0.35, 0.42, 0.50):
        keep = traversable & (clr > threshold)
        labels, count = ndimage.label(keep, structure=np.ones((3, 3)))
        if count:
            sizes = ndimage.sum(np.ones_like(labels), labels, range(1, count + 1))
            largest = int(sizes.max())
            big = int((sizes >= 500).sum())
        else:
            largest = big = 0
        print(
            f"  clr > {threshold:.3f} m : cells={int(keep.sum()):6d}  components={count:4d}"
            f"  (>=500 cells: {big})  largest={largest}"
        )
    return 0


def cmd_clean(src: str, dst: str, resolution: float, min_passage: float) -> int:
    img = load_pgm(src)
    occupied_before = masks(img)[0].sum()
    out, stats = clean(img, resolution, min_passage)
    occupied_after = masks(out)[0].sum()
    save_pgm(dst, out)
    print(f"{src} -> {dst}")
    print(f"  occupied {int(occupied_before)} -> {int(occupied_after)}")
    print(
        f"  고립 점 제거 {stats['isolated_removed']}, 벽 구멍 폐쇄 {stats['holes_filled']}"
        f", 좁은 통로 막음 {stats['sealed_cells']} (min_passage={min_passage} m)"
    )
    print(f"  unknown 셀은 그대로 둔다 ({stats['unknown_untouched']})")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    command = argv[1]
    if command == "analyze":
        resolution = 0.05
        origin = (-5.95, -4.95)
        for arg in argv[3:]:
            if arg.startswith("--resolution="):
                resolution = float(arg.split("=", 1)[1])
            elif arg.startswith("--origin="):
                ox, oy = arg.split("=", 1)[1].split(",")
                origin = (float(ox), float(oy))
        return cmd_analyze(argv[2], resolution, origin)
    if command == "clean":
        if len(argv) < 4:
            print(__doc__)
            return 2
        resolution = 0.05
        min_passage = 0.0
        for arg in argv[4:]:
            if arg.startswith("--resolution="):
                resolution = float(arg.split("=", 1)[1])
            elif arg.startswith("--min-passage="):
                min_passage = float(arg.split("=", 1)[1])
        return cmd_clean(argv[2], argv[3], resolution, min_passage)
    if command == "route":
        resolution = 0.05
        origin = (-5.95, -4.95)
        args = []
        for arg in argv[3:]:
            if arg.startswith("--resolution="):
                resolution = float(arg.split("=", 1)[1])
            elif arg.startswith("--origin="):
                ox, oy = arg.split("=", 1)[1].split(",")
                origin = (float(ox), float(oy))
            else:
                args.append(arg)
        return cmd_route(argv[2], args, resolution, origin)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
