#!/usr/bin/env python3
"""memory.semantic.json 일괄 정리(batch compaction) 도구.

물리적으로 같은 장소가 서로 다른 이름/별칭으로 여러 레코드에 흩어진 것을
공간 클러스터링(반경 eps 내 Leader clustering)으로 하나의 정규(canonical)
레코드 + 별칭 목록으로 접는다. 순수 표준 라이브러리만 사용한다.

알고리즘: single-pass Leader clustering (Hartigan, 1975).
  - 각 항목을 순서대로 보며, 기존 리더 중 거리 < eps 인 것이 있으면 그 클러스터에
    편입, 없으면 새 리더 생성. O(N * K) (K=리더 수).
  - 정규명 선택: 자동생성 일반명("Place 1" 등)보다 사람이 붙인 이름을 우선,
    그다음 최빈 이름, 그다음 첫 등장 이름.

사용:
    python3 compact_semantic_memory.py INPUT.semantic.json [-o OUT] [--eps 0.30] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime

# 영문 자동 자리표시자 이름(예: "Place 1", "Point 3", "waypoint2").
_EN_PLACEHOLDER_RE = re.compile(
    r"^(place|point|location|loc|node|waypoint)\s*\d*$", re.IGNORECASE
)
_PURE_NUM_RE = re.compile(r"^\d+$")
_HANGUL_RE = re.compile(r"[가-힣]")


def name_rank(name: str) -> tuple[int, int]:
    """정규명 선택용 점수(클수록 좋음): (서술성 등급, 길이).

    순수 숫자("1") < 영문 자리표시자("Place 1") < 서술적 이름(한글/실명).
    """
    n = str(name).strip()
    if _PURE_NUM_RE.match(n):
        tier = 0
    elif _EN_PLACEHOLDER_RE.match(n):
        tier = 1
    else:
        tier = 2  # 한글 자동명(이동가능지점1) 및 사람이 붙인 실명 포함
    return (tier, len(n))


def load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return list(data.values())
    return data if isinstance(data, list) else []


def coord(entry: dict):
    pos = entry.get("position", {}) or {}
    try:
        return float(pos["x"]), float(pos["y"])
    except (KeyError, TypeError, ValueError):
        return None


def leader_cluster(entries: list[dict], eps: float) -> list[list[dict]]:
    """반경 eps 내 Leader clustering. 리더 좌표는 클러스터 평균으로 갱신."""
    clusters: list[list[dict]] = []
    centers: list[tuple[float, float]] = []
    for e in entries:
        c = coord(e)
        if c is None:
            # 좌표 없는 항목은 단독 클러스터로 보존
            clusters.append([e])
            centers.append((math.inf, math.inf))
            continue
        best_i, best_d = -1, eps
        for i, (cx, cy) in enumerate(centers):
            d = math.hypot(cx - c[0], cy - c[1])
            if d <= best_d:
                best_d, best_i = d, i
        if best_i < 0:
            clusters.append([e])
            centers.append(c)
        else:
            clusters[best_i].append(e)
            n = len(clusters[best_i])
            cx, cy = centers[best_i]
            centers[best_i] = ((cx * (n - 1) + c[0]) / n, (cy * (n - 1) + c[1]) / n)
    return clusters


def choose_canonical(names: list[str], alias_targets: list[str]) -> str:
    """정규명 선택.

    우선순위:
      (1) alias_of 로 명시된 대상이 있으면 그 최빈 대상(가장 신뢰도 높은 힌트).
      (2) 없으면 서술성 등급(name_rank) → 최빈 → 첫 등장 순.
    """
    names = [n for n in names if str(n).strip()]
    if not names:
        return "unnamed"
    targets = [t for t in alias_targets if str(t).strip()]
    if targets:
        return Counter(targets).most_common(1)[0][0]
    freq = Counter(names)
    best = max(names, key=lambda n: (name_rank(n), freq[n], -names.index(n)))
    return best


def merge_cluster(cluster: list[dict]) -> dict:
    names = [str(e.get("name", "")).strip() for e in cluster if e.get("name")]
    # 기존 aliases도 후보 이름에 포함
    for e in cluster:
        names.extend(str(a).strip() for a in e.get("aliases", []) if str(a).strip())
    alias_targets = [
        str((e.get("metadata") or {}).get("alias_of", "")).strip()
        for e in cluster
        if (e.get("metadata") or {}).get("alias_of")
    ]
    canonical = choose_canonical(names, alias_targets)

    xs, ys = [], []
    for e in cluster:
        c = coord(e)
        if c:
            xs.append(c[0])
            ys.append(c[1])
    position = (
        {"x": sum(xs) / len(xs), "y": sum(ys) / len(ys)}
        if xs
        else (cluster[0].get("position") or {"x": 0.0, "y": 0.0})
    )

    aliases: list[str] = []
    seen = {canonical.casefold()}
    for n in names:
        if n.casefold() not in seen:
            aliases.append(n)
            seen.add(n.casefold())

    sources = sorted(
        {
            str((e.get("metadata") or {}).get("source", "")).strip()
            for e in cluster
            if (e.get("metadata") or {}).get("source")
        }
    )
    last_seen = max(
        (str(e.get("last_seen", "")) for e in cluster if e.get("last_seen")),
        default=datetime.now().isoformat(),
    )
    # 대표 메타데이터: 정규명을 가진 항목 우선, alias_of 제거
    base_meta: dict = {}
    for e in cluster:
        if str(e.get("name", "")).strip() == canonical:
            base_meta = dict(e.get("metadata") or {})
            break
    if not base_meta and cluster:
        base_meta = dict(cluster[0].get("metadata") or {})
    base_meta.pop("alias_of", None)

    return {
        "name": canonical,
        "position": position,
        "last_seen": last_seen,
        "obs_count": len(cluster),
        "aliases": aliases,
        "sources": sources,
        "metadata": base_meta,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Compact memory.semantic.json duplicates.")
    ap.add_argument("input", help="입력 .semantic.json 경로")
    ap.add_argument("-o", "--output", default=None, help="출력 경로(기본: 입력 파일 덮어쓰기)")
    ap.add_argument("--eps", type=float, default=0.30, help="병합 반경(m, 기본 0.30)")
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 리포트만 출력")
    args = ap.parse_args()

    entries = load(args.input)
    clusters = leader_cluster(entries, args.eps)
    compacted = [merge_cluster(c) for c in clusters]

    print("=" * 60)
    print(f"입력 항목 수 : {len(entries)}")
    print(f"정규 place 수: {len(compacted)}  (eps={args.eps} m)")
    print("=" * 60)
    for c in compacted:
        p = c["position"]
        print(
            f"• {c['name']}  ({p['x']:.3f}, {p['y']:.3f})  "
            f"aliases={len(c['aliases'])} sources={c['sources']}"
        )
        if c["aliases"]:
            print(f"    ↳ {', '.join(c['aliases'])}")

    if args.dry_run:
        print("\n[dry-run] 파일을 쓰지 않았습니다.")
        return 0

    out = args.output or args.input
    with open(out, "w", encoding="utf-8") as f:
        json.dump(compacted, f, ensure_ascii=False, indent=2)
    print(f"\n저장 완료: {out}  ({len(entries)} -> {len(compacted)} 항목)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
