"""Stretch 키네마틱 순수 수학 모듈 (numpy 전용, ROS2 의존 없음).

``stretch_backend.py`` 의 ROS2 액션/서비스 클라이언트 구현과 분리된, FK/수치 IK 및
자세 매핑 상수/함수를 담는다. ROS2 메시지(rclpy, control_msgs, trajectory_msgs) 에
의존하지 않아 단위 테스트가 가벼운 환경에서도 그대로 실행된다.

- ``_STRETCH_CHAIN``: base_link -> link_grasp_center URDF 테이블(SE3 dw3 sg3 추출).
- ``_fk_full``/``_fk_position``: 관절 값 → EE 동차행렬/위치.
- ``_ik_position``: 목표 위치 → (lift, ext, yaw) 수치 IK(Newton + Jacobian 최소제곱).
- ``_WRIST_*``: target RPY → wrist pitch/roll 근사 매핑 상수.
"""

import math
from collections.abc import Mapping, Sequence

import numpy as np

# ── joint 화이트리스트 (권장 이름, 단위: m 또는 rad) ──
# wrist_extension(m, 단일) / gripper_aperture(m) 를 권장한다.
_VALID_JOINTS = frozenset(
    {
        "joint_lift",
        "wrist_extension",
        "joint_wrist_yaw",
        "joint_wrist_pitch",
        "joint_wrist_roll",
        "joint_head_pan",
        "joint_head_tilt",
        "gripper_aperture",
        "translate_mobile_base",
        "rotate_mobile_base",
    }
)

# ── 배타 그룹: 한 goal 에 그룹 내 joint 를 2개 이상 담을 수 없다 ──
# (wrist_extension vs joint_arm 계열, gripper 3종 등. 1단계 권장 이름은
#  각 그룹에 1개씩만 속하지만, 설정/스킬이 alias 를 보낼 때의 안전망.)
_EXCLUSIVE_GROUPS = (
    {
        "wrist_extension",
        "joint_arm",
        "joint_arm_l0",
        "joint_arm_l1",
        "joint_arm_l2",
        "joint_arm_l3",
    },
    {
        "gripper_aperture",
        "joint_gripper_finger_left",
        "joint_gripper_finger_right",
        "stretch_gripper",
    },
    {
        "translate_mobile_base",
        "rotate_mobile_base",
    },
)


def _validate_joint_names(joint_values: Mapping[str, float]) -> str | None:
    """joint 이름이 화이트리스트에 있는지 검증. ROS2 의존 없는 순수 함수."""
    if not joint_values:
        return "joint target 이 비어 있습니다."
    for name in joint_values:
        if name not in _VALID_JOINTS:
            return (
                f"지원하지 않는 joint 이름 '{name}'. "
                f"허용: {sorted(_VALID_JOINTS)}"
            )
    return None


def _check_exclusive(joint_names: list[str]) -> str | None:
    """배타 그룹 규칙 검증. 한 그룹에 2개 이상 속하면 거부. 순수 함수."""
    name_set = set(joint_names)
    for group in _EXCLUSIVE_GROUPS:
        overlap = name_set & group
        if len(overlap) > 1:
            return (
                f"배타 joint 를 동시에 지정할 수 없습니다: {sorted(overlap)}. "
                f"그룹 중 하나만 선택하세요."
            )
    return None


# ── Stretch 키네마틱 (base_link -> link_grasp_center, SE3 dw3 sg3 URDF 추출) ──
# 각 원소: (joint_name, type, xyz, rpy, axis). l0~l3 는 wrist_extension 을 4등분.
# fixed joint 의 axis 는 미사용(더미 0).
_STRETCH_CHAIN: tuple[
    tuple[str, str, tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
    ...,
] = (
    ("joint_mast", "fixed", (-0.067, 0.135, 0.0284), (1.5707963, 0.0, 0.0), (0.0, 0.0, 0.0)),
    ("joint_lift", "prismatic", (-0.0374, 0.1666, 0.0), (-1.5707963, 1.5707963, 0.0), (0.0, 0.0, 1.0)),
    ("joint_arm_l4", "fixed", (-0.2547, 0.0, 0.0), (1.5707963, 0.0, -1.5707963), (0.0, 0.0, 0.0)),
    ("joint_arm_l3", "prismatic", (0.0, 0.0, 0.013), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    ("joint_arm_l2", "prismatic", (0.0, 0.0, 0.013), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    ("joint_arm_l1", "prismatic", (0.0, 0.0, 0.013), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    ("joint_arm_l0", "prismatic", (0.0, 0.0, -0.0137), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    ("joint_wrist_yaw", "revolute", (0.083, -0.0308, 0.0), (-1.5707963, 0.0, 3.1415927), (0.0, 0.0, -1.0)),
    ("joint_wrist_yaw_bottom", "fixed", (0.0, 0.0, 0.0), (-3.1415927, 0.0, -1.5707963), (0.0, 0.0, 0.0)),
    ("joint_wrist_pitch", "revolute", (0.0, -0.0195, -0.0305), (1.5707963, 0.0, 3.1415927), (0.0, 0.0, -1.0)),
    ("joint_wrist_roll", "revolute", (-0.0189, -0.024, 0.0196), (3.1415927, 1.5707963, 0.0), (0.0, 0.0, 1.0)),
    ("joint_gripper_s3_body", "fixed", (0.0, 0.0, 0.021), (0.0, 0.0, -3.1415927), (0.0, 0.0, 0.0)),
    ("joint_grasp_center", "fixed", (0.0, 0.0, 0.23), (-1.5707963, -1.5707963, 0.0), (0.0, 0.0, 0.0)),
)

# position IK (lift, ext, yaw) 가동 범위 (raw 단위).
_IK_LIMITS = (
    (0.0, 1.1),     # joint_lift (m)
    (0.0, 0.52),    # wrist_extension (m)
    (-1.75, 4.0),   # joint_wrist_yaw (rad)
)
_IK_MAX_ITER = 80
_IK_TOL = 1e-3
_IK_FAIL_TOL = 2e-2  # 수렴 실패 판정 임계 (m)

# compute_align_rotation_rad 탐색용 저정밀 도달성 프로브 반복 횟수.
# 무작위 200개 표본에서 _IK_MAX_ITER(80)와 판정(도달 가능/불가) 결과가 100% 일치하면서
# 약 4배 빠름을 확인(각 _ik_position 호출이 ~0.16s로 비싸 그리드 탐색에 그대로 쓰면 느림).
_IK_PROBE_MAX_ITER = 20

# 필요 베이스 회전각 탐색 범위/간격 (compute_align_rotation_rad 기본값).
_ALIGN_SEARCH_RANGE_RAD = math.radians(150.0)
_ALIGN_SEARCH_STEP_RAD = math.radians(5.0)

# 팔 작업축 방위(_arm_reach_bearing_rad) 계산에 쓰는 대표 wrist_extension 값.
_ARM_REACH_REF_EXT = 0.26

# orientation 근사 매핑 상수: target RPY -> wrist joint.
# EE 기본 orientation 이 비표준(mast 회전)이라 부호/스케일은 MuJoCo TF echo 로 보정.
_WRIST_PITCH_SCALE = 1.0
_WRIST_PITCH_OFFSET = 0.0
_WRIST_ROLL_SCALE = 1.0
_WRIST_ROLL_OFFSET = 0.0


def _rpy_to_mat(rpy: Sequence[float]) -> np.ndarray:
    r, p, y = rpy
    cr, sr = math.cos(r), math.sin(r)
    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)
    Rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    Ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    Rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def _axis_angle_mat(axis: Sequence[float], angle: float) -> np.ndarray:
    ax = np.array(axis, dtype=float)
    norm = np.linalg.norm(ax)
    if norm > 0.0:
        ax = ax / norm
    x, y, z = ax
    c, s = math.cos(angle), math.sin(angle)
    C = 1.0 - c
    return np.array(
        [
            [c + x * x * C, x * y * C - z * s, x * z * C + y * s],
            [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
            [z * x * C - y * s, z * y * C + x * s, c + z * z * C],
        ]
    )


def _fk_full(q: Mapping[str, float]) -> np.ndarray:
    """base_link -> link_grasp_center 4x4 동차행렬.

    q 키: joint_lift, wrist_extension, joint_wrist_yaw,
    joint_wrist_pitch, joint_wrist_roll (선택). 누락 시 0.
    """
    T = np.eye(4)
    for name, jtype, xyz, rpy, axis in _STRETCH_CHAIN:
        origin = np.eye(4)
        origin[:3, :3] = _rpy_to_mat(rpy)
        origin[:3, 3] = np.array(xyz, dtype=float)
        Tj = np.eye(4)
        if jtype == "prismatic" and name.startswith("joint_arm_l"):
            # l0~l3 각각 wrist_extension/4 만큼 z 이동 (telescoping 4 누적 = ext).
            Tj[:3, 3] = np.array(axis, dtype=float) * (q.get("wrist_extension", 0.0) / 4.0)
        elif jtype == "prismatic":
            Tj[:3, 3] = np.array(axis, dtype=float) * q.get(name, 0.0)
        elif jtype == "revolute":
            Tj[:3, :3] = _axis_angle_mat(axis, q.get(name, 0.0))
        T = T @ origin @ Tj
    return T


def _fk_position(q: Mapping[str, float]) -> np.ndarray:
    """link_grasp_center 위치(base_link 기준) 반환."""
    return _fk_full(q)[:3, 3]


def _ik_position(
    target_xyz: Sequence[float],
    q0: Sequence[float] | None = None,
    *,
    max_iter: int = _IK_MAX_ITER,
    fail_tol: float = _IK_FAIL_TOL,
) -> tuple[float, float, float] | None:
    """position 수치 IK (Newton + Jacobian 최소제곱).

    ``max_iter``/``fail_tol``은 기본값이 기존 상수와 동일해 하위호환된다.
    ``compute_align_rotation_rad``의 그리드 탐색처럼 다수의 도달성 여부만
    빠르게 확인하면 되는 호출은 ``max_iter=_IK_PROBE_MAX_ITER``로 낮춰
    호출 비용을 약 4배 줄일 수 있다(무작위 200개 표본에서 기본값과 판정 결과 100% 일치 확인됨).

    Returns:
        (joint_lift, wrist_extension, joint_wrist_yaw) 또는 도달 불가/수렴 실패 시 None.
    """
    q = np.array([0.3, 0.2, 0.0], dtype=float) if q0 is None else np.array(q0, dtype=float)
    tgt = np.array(target_xyz, dtype=float)
    eps = 1e-5

    for _ in range(max_iter):
        qmap = {"joint_lift": q[0], "wrist_extension": q[1], "joint_wrist_yaw": q[2]}
        pos = _fk_position(qmap)
        err = tgt - pos
        if np.linalg.norm(err) < _IK_TOL:
            break

        # 수치 Jacobian (3x3, 중앙차분).
        J = np.zeros((3, 3))
        for i in range(3):
            qp = q.copy()
            qp[i] += eps
            p = _fk_position(
                {"joint_lift": qp[0], "wrist_extension": qp[1], "joint_wrist_yaw": qp[2]}
            )
            J[:, i] = (p - pos) / eps

        dq, *_ = np.linalg.lstsq(J, err, rcond=None)
        q = q + np.clip(dq, -0.2, 0.2)
        for i, (lo, hi) in enumerate(_IK_LIMITS):
            q[i] = max(lo, min(hi, q[i]))

    final = _fk_position(
        {"joint_lift": q[0], "wrist_extension": q[1], "joint_wrist_yaw": q[2]}
    )
    if np.linalg.norm(tgt - final) > fail_tol:
        return None
    return float(q[0]), float(q[1]), float(q[2])


# ── 베이스 회전각 자동 계산 (팔이 몸통 오른쪽에만 뻗는 구조 보완) ──
#
# Stretch3 팔은 joint_lift(상하)와 wrist_extension(측면 텔레스코핑)만으로 움직이므로
# 정면/좌우의 임의 물체는 몸통을 회전시켜야 도달 가능한 경우가 많다. 아래 함수들은
# 기존 _ik_position/_fk_position 을 그대로 재사용해 "몸통을 얼마나 회전해야
# 도달 가능한가"를 수치적으로 계산한다.


def _normalize_angle(angle_rad: float) -> float:
    """각도를 (-pi, pi] 범위로 정규화."""
    return (angle_rad + math.pi) % (2.0 * math.pi) - math.pi


def _rotate_xy(
    xyz: Sequence[float], theta_rad: float
) -> tuple[float, float, float]:
    """베이스가 theta_rad 만큼 회전(z축 기준, CCW+)한 뒤의 base_link 좌표계에서
    같은 월드 지점이 어떤 좌표로 보이는지 반환한다(회전 중심 = base_link 원점).

    베이스 자체가 +theta 회전하면, 그 프레임에서 본 고정점의 좌표는 원래
    좌표를 -theta 만큼 회전한 것과 같다(좌표축 회전은 벡터 좌표에 역회전으로 작용).
    """
    x, y, z = xyz
    c, s = math.cos(theta_rad), math.sin(theta_rad)
    return (c * x + s * y, -s * x + c * y, z)


def _arm_reach_bearing_rad(ext: float = _ARM_REACH_REF_EXT) -> float:
    """joint_wrist_yaw=0, 주어진 wrist_extension 에서 EE 의 방위각(atan2(y, x)).

    팔이 '정면'으로 취급하는(오른쪽으로 곧게 뻗었을 때의) 작업축 방위이며,
    베이스 회전 없이 팔이 자연스럽게 도달하는 방향의 기준점으로 쓰인다.
    """
    pos = _fk_position({"wrist_extension": ext, "joint_wrist_yaw": 0.0})
    return math.atan2(pos[1], pos[0])


def compute_align_rotation_rad(
    target_xyz: Sequence[float],
    *,
    search_range_rad: float = _ALIGN_SEARCH_RANGE_RAD,
    search_step_rad: float = _ALIGN_SEARCH_STEP_RAD,
) -> float | None:
    """target_xyz(현재 base_link 기준)를 팔 작업축이 향하도록 만드는 필요 베이스
    회전각(rad, CCW+)을 계산한다.

    1) 이미 회전 없이 도달 가능하면 0.0을 반환한다.
    2) 그렇지 않으면 target 방위와 팔 작업축 방위의 차이(theta0)를 초기 추정치로 삼아,
       theta0 주변을 search_step_rad 간격으로 바깥쪽으로 확장 탐색(0, +step, -step,
       +2*step, -2*step, ...)하며 첫 도달 가능 후보를 채택한다. 탐색 단계에서는
       비용이 큰 정밀 IK 대신 저정밀 프로브(``_ik_position(..., max_iter=_IK_PROBE_MAX_ITER)``)
       를 사용해 그리드 탐색 비용을 낮추고, 채택된 후보는 정밀 IK로 최종 재확인한다.
    3) search_range_rad 안에서 후보를 찾지 못하면 회전으로도 해결 불가로 보고 None.
    """
    if _ik_position(target_xyz) is not None:
        return 0.0

    x, y = target_xyz[0], target_xyz[1]
    theta0 = _normalize_angle(math.atan2(y, x) - _arm_reach_bearing_rad())

    steps = max(1, int(round(search_range_rad / search_step_rad)))
    candidate: float | None = None
    for i in range(steps + 1):
        signs = (1,) if i == 0 else (1, -1)
        for sign in signs:
            theta = _normalize_angle(theta0 + sign * i * search_step_rad)
            rotated = _rotate_xy(target_xyz, theta)
            if _ik_position(rotated, max_iter=_IK_PROBE_MAX_ITER) is not None:
                candidate = theta
                break
        if candidate is not None:
            break

    if candidate is None:
        return None

    # 프로브는 근사이므로 채택 후보를 정밀 IK로 최종 재확인한다.
    if _ik_position(_rotate_xy(target_xyz, candidate)) is None:
        return None
    return candidate


def compute_align_rotation_from_bearing_rad(camera_bearing_rad: float) -> float:
    """카메라 detection의 방위각(예: FindObjectSkill의 bearing_offset_deg를 라디안
    환산한 값, base_link 정면 기준 CCW+ 각도)만으로 필요 베이스 회전각을 계산한다.

    3D pose(깊이) 추정이 실패했거나 아직 없을 때, target 거리 없이 방위값만으로
    회전각을 근사하는 저비용 대안이다. ``compute_align_rotation_rad``와 달리
    IK 도달성 확인 없이 팔 작업축 방위와의 각도 차이만 반환한다.
    """
    return _normalize_angle(camera_bearing_rad - _arm_reach_bearing_rad())


def compute_centering_rotation_rad(target_xyz: Sequence[float]) -> float | None:
    """팔 yaw로 닿을 수 있는지와 무관하게 목표를 팔 작업축 중앙에 맞춘다."""
    x, y = float(target_xyz[0]), float(target_xyz[1])
    if math.hypot(x, y) < 1e-6:
        return None
    return _normalize_angle(math.atan2(y, x) - _arm_reach_bearing_rad())
