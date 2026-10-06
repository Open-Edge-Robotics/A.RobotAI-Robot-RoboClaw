"""ArUco 마커를 이용한 헤드 카메라 관측 차분 보정.

**이 모듈이 하지 않는 일**: 마커로 로봇 부위의 위치를 "추정"하지 않는다. 그것은
joint encoder + URDF 가 이미 정확히 알고 있는 값이라 중복이고, 마커 검출 오차가
오히려 encoder FK 보다 나쁠 수 있다.

**이 모듈이 하는 일**: 헤드 카메라가 ArUco 로 본 손목 마커의 위치와, URDF 가
말하는 같은 마커의 위치를 비교해 **잔차**를 구한다. 이 잔차는 헤드 카메라
외부파라미터 + head pan/tilt 캘리브레이션 오차이며, 같은 헤드 카메라로 관측한
물체 좌표에도 **똑같이** 실려 있다. 따라서 물체 관측에 잔차를 더하면 그 계통
오차가 상쇄된다(Hello Robot ``stretch_calibration`` 과 같은 원리).

전제: 사용자가 별도로 ``ros2 launch stretch_core stretch_aruco.launch.py`` 를
실행해 마커 TF 가 발행되고 있어야 한다. RealSense 드라이버를 외부에 맡기는 기존
관례(``stretch3_config.yaml`` 주석 참조)와 동일하다. 미실행 시 이 모듈의 모든
함수는 ``None`` 을 반환하고, 호출부는 보정 없이(offset 0) 그대로 동작한다.
"""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .ee_state import _lookup_frame_origin
from .params import _bool_param, _float_param, _node_float_param

logger = logging.getLogger(__name__)

# (ArUco 노드가 발행하는 측정 프레임, URDF 가 발행하는 진짜 프레임) 쌍.
# Hello Robot stretch_core/config/stretch_marker_dict.yaml 의 name/link 필드 기준
# (id 132 = wrist_inside, id 133 = wrist_top, DICT_6X6_250).
# 손목 마커만 쓴다 — 파지에 관계되는 것은 그리퍼 근방의 잔차이고, base/shoulder
# 마커는 운동학 체인의 다른 구간을 측정하기 때문이다.
_DEFAULT_MARKER_FRAME_PAIRS: tuple[tuple[str, str], ...] = (
    ("wrist_inside", "link_aruco_inner_wrist"),
    ("wrist_top", "link_aruco_top_wrist"),
)

_DEFAULT_MARKER_MAX_AGE_SEC = 1.5
_DEFAULT_MAX_RESIDUAL_M = 0.10
_DEFAULT_RESIDUAL_AGREE_M = 0.05
# 래치된 잔차를 신뢰하는 최대 시간. 이보다 오래되면 폐기한다(팔/헤드가 그동안
# 크게 움직였다면 잔차의 전제였던 자세가 더 이상 유효하지 않다).
_DEFAULT_RESIDUAL_TTL_SEC = 60.0


def _marker_frame_pairs(params: dict[str, Any]) -> list[tuple[str, str]]:
    """설정에서 (측정 프레임, URDF 프레임) 쌍 목록을 읽는다.

    실기에서 프레임 이름이 다르면 코드 수정 없이 YAML 한 줄로 고칠 수 있도록
    JSON 문자열(또는 리스트) 파라미터로 오버라이드할 수 있다.
    """
    raw: Any = params.get("aruco_marker_frame_pairs")
    if raw is None:
        raw = params.get("aruco_marker_frame_pairs_json")

    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return list(_DEFAULT_MARKER_FRAME_PAIRS)
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError:
            logger.warning("Failed to parse aruco_marker_frame_pairs_json: %r", stripped)
            return list(_DEFAULT_MARKER_FRAME_PAIRS)

    if not isinstance(raw, (list, tuple)) or not raw:
        return list(_DEFAULT_MARKER_FRAME_PAIRS)

    pairs: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            measured, urdf = str(item[0]).strip(), str(item[1]).strip()
            if measured and urdf:
                pairs.append((measured, urdf))
    return pairs or list(_DEFAULT_MARKER_FRAME_PAIRS)


def _resolve_frame_pairs(skill: BaseSkill, params: dict[str, Any]) -> list[tuple[str, str]]:
    """실행 params 우선, 없으면 노드 파라미터에서 프레임 쌍을 읽는다."""
    if "aruco_marker_frame_pairs" in params or "aruco_marker_frame_pairs_json" in params:
        return _marker_frame_pairs(params)
    configured = skill.get_string_param(params, "aruco_marker_frame_pairs_json", "")
    if configured:
        return _marker_frame_pairs({"aruco_marker_frame_pairs_json": configured})
    return list(_DEFAULT_MARKER_FRAME_PAIRS)


def _marker_tf_residual(
    skill: BaseSkill, params: dict[str, Any]
) -> tuple[tuple[float, float, float], dict[str, Any]] | None:
    """헤드 카메라 계통 오차 잔차 (base_link, m) 를 계산한다.

    각 프레임 쌍에 대해 ``residual = p_urdf - p_measured`` 를 구하고 평균낸다.
    아래 검사 중 하나라도 실패하면 ``None`` 을 반환한다(보정 없이 진행):

    - 마커 TF 없음 / TF 버퍼 없음 (= ArUco 노드 미실행)
    - 마커 TF stamp 가 ``aruco_marker_max_age_sec`` 보다 오래됨 (= 지금 안 보임)
    - ``|residual| > aruco_max_residual_m`` (= 마커 오검출 의심)
    - 쌍이 2개 이상인데 서로 ``aruco_residual_agree_m`` 이상 어긋남 (= 신뢰 불가)

    Returns:
        ((dx, dy, dz), diagnostics) 또는 None.
    """
    node = getattr(skill, "node", None)
    if node is None or getattr(node, "_tf_buffer", None) is None:
        return None

    base_frame = skill.get_string_param(params, "manipulation_base_frame", "base_link")
    max_age_sec = _node_float_param(
        skill, params, "aruco_marker_max_age_sec", _DEFAULT_MARKER_MAX_AGE_SEC
    )
    max_residual_m = _node_float_param(
        skill, params, "aruco_max_residual_m", _DEFAULT_MAX_RESIDUAL_M
    )
    agree_m = _node_float_param(
        skill, params, "aruco_residual_agree_m", _DEFAULT_RESIDUAL_AGREE_M
    )

    residuals: list[tuple[float, float, float]] = []
    used_pairs: list[str] = []
    rejected: list[str] = []

    for measured_frame, urdf_frame in _resolve_frame_pairs(skill, params):
        measured = _lookup_frame_origin(node, base_frame, measured_frame, max_age_sec=max_age_sec)
        if measured is None:
            rejected.append(f"{measured_frame}: TF 없음/오래됨")
            continue
        urdf = _lookup_frame_origin(node, base_frame, urdf_frame)
        if urdf is None:
            rejected.append(f"{urdf_frame}: URDF TF 없음")
            continue

        residual = (
            urdf[0][0] - measured[0][0],
            urdf[0][1] - measured[0][1],
            urdf[0][2] - measured[0][2],
        )
        magnitude = math.sqrt(sum(component * component for component in residual))
        if magnitude > max_residual_m:
            rejected.append(f"{measured_frame}: 잔차 {magnitude:.3f}m > {max_residual_m:.3f}m")
            continue
        residuals.append(residual)
        used_pairs.append(measured_frame)

    if not residuals:
        logger.debug("ArUco residual unavailable: %s", "; ".join(rejected) or "쌍 없음")
        return None

    if len(residuals) > 1:
        for i in range(1, len(residuals)):
            spread = math.dist(residuals[0], residuals[i])
            if spread > agree_m:
                logger.warning(
                    "ArUco residuals disagree (%.3fm > %.3fm) — 보정을 적용하지 않습니다.",
                    spread,
                    agree_m,
                )
                return None

    count = float(len(residuals))
    mean = (
        sum(r[0] for r in residuals) / count,
        sum(r[1] for r in residuals) / count,
        sum(r[2] for r in residuals) / count,
    )
    diagnostics = {
        "pairs_used": used_pairs,
        "pairs_rejected": rejected,
        "magnitude_m": round(math.sqrt(sum(c * c for c in mean)), 4),
    }
    return mean, diagnostics


class MarkerResidualCache:
    """ArUco 잔차 래치.

    팔이 뻗으면 손목 마커가 그리퍼/물체에 가려지거나 헤드 FOV 를 벗어난다. 따라서
    잔차는 마커가 잘 보이는 시점(팔이 접힌 ready pose)에 계산해 보관하고, 서보
    도중에는 마커가 다시 보일 때만 갱신한다. TTL 을 넘긴 값은 폐기한다.
    """

    def __init__(self, *, ttl_sec: float = _DEFAULT_RESIDUAL_TTL_SEC) -> None:
        self._residual: tuple[float, float, float] | None = None
        self._latched_at: float = 0.0
        self._ttl_sec = ttl_sec
        self.diagnostics: dict[str, Any] = {}
        self.update_count = 0

    def refresh(self, skill: BaseSkill, params: dict[str, Any]) -> bool:
        """잔차를 다시 계산해 래치한다. 성공하면 True(기존 값은 유지)."""
        result = _marker_tf_residual(skill, params)
        if result is None:
            return False
        self._residual, self.diagnostics = result
        self._latched_at = time.monotonic()
        self.update_count += 1
        return True

    @property
    def residual(self) -> tuple[float, float, float] | None:
        """래치된 잔차. 없거나 TTL 초과면 None."""
        if self._residual is None:
            return None
        if time.monotonic() - self._latched_at > self._ttl_sec:
            return None
        return self._residual

    def apply(self, xyz: tuple[float, float, float]) -> tuple[float, float, float]:
        """헤드 카메라 관측 좌표에 잔차를 더한다. 잔차가 없으면 원본 그대로."""
        residual = self.residual
        if residual is None:
            return xyz
        return (xyz[0] + residual[0], xyz[1] + residual[1], xyz[2] + residual[2])


def make_residual_cache(
    skill: BaseSkill, params: dict[str, Any], *, refresh: bool = True
) -> MarkerResidualCache:
    """설정을 반영한 캐시를 만들고, ``use_aruco_correction`` 이면 즉시 1회 래치한다."""
    ttl_sec = _float_param(params, "aruco_residual_ttl_sec", _DEFAULT_RESIDUAL_TTL_SEC)
    cache = MarkerResidualCache(ttl_sec=ttl_sec)
    if refresh and _bool_param(params.get("use_aruco_correction"), True):
        cache.refresh(skill, params)
    return cache
