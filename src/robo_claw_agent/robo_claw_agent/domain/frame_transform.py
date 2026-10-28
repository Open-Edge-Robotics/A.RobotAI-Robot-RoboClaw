"""로봇 간 맵 좌표계 정합 — 공유 장소 이름으로 similarity 변환을 추정한다 (ROS 의존 없음).

두 로봇이 각자 SLAM 맵을 쓰면 ``map`` 프레임 이름은 같아도 원점·회전·축척이
다르다. 그래서 동료의 좌표를 그대로 비교하면 안 된다. 다만 두 로봇의 시멘틱
맵에 **같은 이름의 장소**가 있으면, 그 대응점들로 좌표계 사이 변환을 추정할 수
있다.

설계 원칙:

- **fail-closed.** 대응점이 부족하거나, 축척이 물리적으로 말이 안 되거나,
  잔차가 크면 변환을 만들지 않는다. 변환이 없으면 동료 좌표는 쓰지 않는다
  (현재 동작과 같다).
- **축척 검증이 오대응을 잡는다.** 서로 다른 방을 같은 이름으로 저장한 경우
  축척이 1에서 크게 벗어나므로 거부된다.
- 변환은 **동료 프레임 → 내 프레임** 방향으로 만든다. 쓰는 쪽이 헷갈리지 않게
  방향을 하나로 고정한다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from robo_claw_agent.domain.yield_slot import Pose2D

# 대응점 최소 개수. 2점이면 similarity(회전·축척·평행이동)가 유일하게 결정된다.
MIN_CORRESPONDENCES = 2
# 같은 물리 세계를 보는 로봇이므로 축척은 1 근처여야 한다.
MIN_SCALE = 0.9
MAX_SCALE = 1.1
# 허용 잔차(m). 넘으면 대응이 틀렸다고 보고 변환을 버린다.
MAX_RMS_RESIDUAL_M = 0.5
# 대응점이 서로 너무 가까우면 회전 추정이 불안정하다.
MIN_SPREAD_M = 0.5

IDENTITY_REASON = "identity"


@dataclass(frozen=True)
class Transform2D:
    """동료 프레임 좌표를 내 프레임으로 옮기는 similarity 변환."""

    scale: float = 1.0
    rotation_rad: float = 0.0
    tx: float = 0.0
    ty: float = 0.0

    def is_identity(self) -> bool:
        return (
            math.isclose(self.scale, 1.0, abs_tol=1e-9)
            and math.isclose(self.rotation_rad, 0.0, abs_tol=1e-9)
            and math.isclose(self.tx, 0.0, abs_tol=1e-9)
            and math.isclose(self.ty, 0.0, abs_tol=1e-9)
        )

    def apply(self, pose: Pose2D) -> Pose2D:
        """동료 프레임의 자세를 내 프레임으로 변환한다."""
        cos_r = math.cos(self.rotation_rad)
        sin_r = math.sin(self.rotation_rad)
        x = self.scale * (cos_r * pose.x - sin_r * pose.y) + self.tx
        y = self.scale * (sin_r * pose.x + cos_r * pose.y) + self.ty
        return Pose2D(x, y, pose.yaw + self.rotation_rad)

    def as_dict(self) -> dict[str, float]:
        return {
            "scale": round(self.scale, 6),
            "rotation_rad": round(self.rotation_rad, 6),
            "tx": round(self.tx, 4),
            "ty": round(self.ty, 4),
        }


@dataclass(frozen=True)
class TransformFit:
    """정합 결과. ``transform`` 이 None 이면 쓰지 않는다."""

    transform: Transform2D | None
    reason: str = ""
    correspondences: int = 0
    rms_residual_m: float | None = None
    scale: float | None = None

    @property
    def ok(self) -> bool:
        return self.transform is not None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "reason": self.reason,
            "correspondences": self.correspondences,
        }
        if self.transform is not None:
            payload["transform"] = self.transform.as_dict()
        if self.rms_residual_m is not None:
            payload["rms_residual_m"] = round(self.rms_residual_m, 4)
        if self.scale is not None:
            payload["scale"] = round(self.scale, 6)
        return payload


def _centroid(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    n = len(points)
    return (
        sum(p[0] for p in points) / n,
        sum(p[1] for p in points) / n,
    )


def _spread(points: Sequence[tuple[float, float]], centroid: tuple[float, float]) -> float:
    """중심에서 가장 먼 점까지의 거리. 대응점이 한 곳에 몰렸는지 판정한다."""
    return max(
        (math.dist(p, centroid) for p in points),
        default=0.0,
    )


def fit_similarity_transform(
    source: Sequence[tuple[float, float]],
    target: Sequence[tuple[float, float]],
) -> TransformFit:
    """``source`` 프레임의 점들을 ``target`` 프레임에 맞추는 similarity 변환을 추정한다.

    ``source`` 는 동료 좌표, ``target`` 은 내 좌표 순서다. 결과 변환은
    동료 프레임 → 내 프레임이다.
    """
    if len(source) != len(target):
        return TransformFit(None, "length_mismatch", correspondences=0)
    n = len(source)
    if n < MIN_CORRESPONDENCES:
        return TransformFit(None, "insufficient_correspondences", correspondences=n)
    for point in (*source, *target):
        if not (math.isfinite(point[0]) and math.isfinite(point[1])):
            return TransformFit(None, "non_finite_correspondence", correspondences=n)

    src_c = _centroid(source)
    dst_c = _centroid(target)
    if _spread(source, src_c) < MIN_SPREAD_M or _spread(target, dst_c) < MIN_SPREAD_M:
        return TransformFit(None, "degenerate_spread", correspondences=n)

    sum_aa = 0.0
    sum_a_dot_b = 0.0
    sum_cross = 0.0
    for (ax, ay), (bx, by) in zip(source, target, strict=False):
        ax_c = ax - src_c[0]
        ay_c = ay - src_c[1]
        bx_c = bx - dst_c[0]
        by_c = by - dst_c[1]
        sum_aa += ax_c * ax_c + ay_c * ay_c
        sum_a_dot_b += ax_c * bx_c + ay_c * by_c
        sum_cross += ax_c * by_c - ay_c * bx_c

    if sum_aa <= 0.0:
        return TransformFit(None, "degenerate_spread", correspondences=n)

    cos_scaled = sum_a_dot_b / sum_aa
    sin_scaled = sum_cross / sum_aa
    scale = math.hypot(cos_scaled, sin_scaled)
    if not math.isfinite(scale) or scale < MIN_SCALE or scale > MAX_SCALE:
        return TransformFit(None, "implausible_scale", correspondences=n, scale=scale)
    rotation = math.atan2(sin_scaled, cos_scaled)

    cos_r = math.cos(rotation)
    sin_r = math.sin(rotation)
    tx = dst_c[0] - scale * (cos_r * src_c[0] - sin_r * src_c[1])
    ty = dst_c[1] - scale * (sin_r * src_c[0] + cos_r * src_c[1])
    transform = Transform2D(scale=scale, rotation_rad=rotation, tx=tx, ty=ty)

    squared_error = 0.0
    for (ax, ay), (bx, by) in zip(source, target, strict=False):
        mapped = transform.apply(Pose2D(ax, ay, 0.0))
        squared_error += (mapped.x - bx) ** 2 + (mapped.y - by) ** 2
    rms = math.sqrt(squared_error / n)
    if rms > MAX_RMS_RESIDUAL_M:
        return TransformFit(
            None,
            "residual_too_large",
            correspondences=n,
            rms_residual_m=rms,
            scale=scale,
        )
    return TransformFit(
        transform,
        IDENTITY_REASON if transform.is_identity() else "fitted",
        correspondences=n,
        rms_residual_m=rms,
        scale=scale,
    )


def _normalize(name: str) -> str:
    return "".join(str(name or "").split()).lower()


def shared_place_pairs(
    peer_places: Mapping[str, tuple[float, float]],
    own_places: Mapping[str, tuple[float, float]],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]], list[str]]:
    """두 시멘틱 맵의 공통 장소를 (동료 좌표, 내 좌표, 이름) 순서로 뽑는다."""
    own_index = {_normalize(name): (name, xy) for name, xy in own_places.items()}
    peer_source: list[tuple[float, float]] = []
    own_target: list[tuple[float, float]] = []
    names: list[str] = []
    for peer_name, peer_xy in sorted(peer_places.items()):
        key = _normalize(peer_name)
        match = own_index.get(key)
        if match is None:
            continue
        peer_name_found, own_xy = match
        peer_source.append((float(peer_xy[0]), float(peer_xy[1])))
        own_target.append((float(own_xy[0]), float(own_xy[1])))
        names.append(peer_name)
    return peer_source, own_target, names


def fit_frame_transform(
    peer_places: Mapping[str, tuple[float, float]],
    own_places: Mapping[str, tuple[float, float]],
) -> TransformFit:
    """공통 장소 이름으로 동료 프레임 → 내 프레임 변환을 추정한다.

    공통 장소가 2개 미만이면 변환을 만들지 않는다. 좌표계가 같다고 **추측하지
    않는다** — 같은 이름의 장소가 실제로 같은 지점일 때만 변환이 성립한다.
    """
    source, target, _names = shared_place_pairs(peer_places, own_places)
    return fit_similarity_transform(source, target)
