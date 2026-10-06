"""맵 기반 이동 가능 지점 발굴 알고리즘 (순수 계산).

OccupancyGrid와 현재 pose를 입력받아 로봇이 돌아다닐 만한 대표 지점을
도출한다. ROS/메모리 부수효과 없이 좌표 리스트만 반환하므로 단독 테스트가
가능하다.
"""


import numpy as np


def find_reachable_places(
    map_msg,
    pose: dict | None,
    *,
    min_openness_m: float = 0.3,
    min_separation_m: float = 1.5,
    max_places: int = 5,
) -> list[tuple[float, float, float]]:
    """도달 가능한 대표 지점 목록을 반환한다.

    반환값: ``[(world_x, world_y, openness_m), ...]`` (최대 ``max_places`` 개).
    이동 가능 공간이 없거나 후보가 없으면 빈 리스트를 반환한다.

    - ``min_openness_m``: 후보로 인정할 최소 개방도(벽까지 거리, m).
    - ``min_separation_m``: 지점 간 최소 간격(m).
    """
    import math
    from collections import deque

    import cv2

    info = map_msg.info
    res = info.resolution
    origin_x = info.origin.position.x
    origin_y = info.origin.position.y
    width, height = info.width, info.height
    data = np.array(map_msg.data, dtype=np.int16).reshape((height, width))

    # 다운샘플 처리 (대형 맵 대응)
    factor = 1
    if width * height > 1_000_000:
        factor = int(math.ceil(math.sqrt((width * height) / 1_000_000)))
        data = data[::factor, ::factor]
        height, width = data.shape
    res_eff = res * factor

    free = data == 0
    if not free.any():
        return []

    free_u8 = free.astype(np.uint8)
    openness_m = cv2.distanceTransform(free_u8, cv2.DIST_L2, 5) * res_eff

    # BFS로 현재 위치에서 도달 가능한 마스크 생성
    reachable = free.copy()
    if pose and pose.get("frame") == "map":
        col = int((pose["x"] - origin_x) / res_eff)
        row = int((pose["y"] - origin_y) / res_eff)
        seed = None
        if 0 <= row < height and 0 <= col < width and free[row, col]:
            seed = (row, col)
        else:
            # 주변 검색
            radius = 12
            found = False
            for rad in range(1, radius + 1):
                if found:
                    break
                r0, r1 = max(0, row - rad), min(height - 1, row + rad)
                c0, c1 = max(0, col - rad), min(width - 1, col + rad)
                sub = free[r0 : r1 + 1, c0 : c1 + 1]
                hits = np.argwhere(sub)
                if len(hits) > 0:
                    hr, hc = hits[0]
                    seed = (r0 + int(hr), c0 + int(hc))
                    found = True

        if seed:
            visited = np.zeros_like(free, dtype=bool)
            sr, sc = seed
            visited[sr, sc] = True
            q = deque([(sr, sc)])
            while q:
                r, c = q.popleft()
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nr, nc = r + dr, c + dc
                    if (
                        0 <= nr < height
                        and 0 <= nc < width
                        and not visited[nr, nc]
                        and free[nr, nc]
                    ):
                        visited[nr, nc] = True
                        q.append((nr, nc))
            reachable = visited

    # 후보지 필터링: 개방도 높은 순으로 최소 간격을 두고 선택
    cand_mask = reachable & (openness_m >= min_openness_m)
    rows, cols = np.where(cand_mask)
    if len(rows) == 0:
        return []

    scores = openness_m[rows, cols]
    order = np.argsort(-scores)[:8000]
    selected: list[tuple[float, float, float]] = []
    sep_sq = min_separation_m * min_separation_m
    for idx in order:
        r, c = int(rows[idx]), int(cols[idx])
        wx = origin_x + (c + 0.5) * res_eff
        wy = origin_y + (r + 0.5) * res_eff
        if all((wx - sx) ** 2 + (wy - sy) ** 2 >= sep_sq for sx, sy, _ in selected):
            selected.append((wx, wy, float(scores[idx])))
            if len(selected) >= max_places:
                break
    return selected
