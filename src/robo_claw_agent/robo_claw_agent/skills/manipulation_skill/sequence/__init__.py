"""Stretch3 집기/배치 합성 스킬 모음.

이 패키지는 원래 하나의 큰 ``sequence.py`` 모듈이었으나, 재사용성과 가독성을
높이기 위해 관심사별로 아래와 같이 분리되었다:

- ``params``: 파라미터 파싱/그리퍼 preset 관련 순수 헬퍼
- ``joint_state``: joint_states 토픽 조회/합성
- ``detection_common``: 카메라 무관 detection 폴링 헬퍼
- ``gripper_vision``: 그리퍼 카메라 탐지 및 RGB-D 관측
- ``head_vision``: 헤드 카메라 탐지 및 base_link 3D 관측
- ``ee_state``: TF/FK 기반 grasp center 위치 및 오차 분해
- ``aruco_calib``: ArUco 마커 차분으로 헤드 카메라 계통 오차 보정
- ``head_assist``: 헤드 카메라 파지 보조(관측 유도 + 좌우 정렬)
- ``grasp_verification``: gripper_aperture 기반 파지 성공 검증/재시도
- ``rotation``: 베이스 회전을 통한 도달성 확보/방위 계산
- ``align_skill``: 오른쪽 팔 작업축 정렬 스킬
- ``gripper_target_skills``: 그리퍼 카메라 관측/시각 서보 스킬
- ``right_side_pick_skills``: 오른쪽 작업 구역 캘리브레이션 기반 집기 스킬
- ``vla_pick_skills``: 그리퍼 카메라 폐루프 기반 VLA 집기 스킬
- ``search_skill``: 헤드 pan/tilt 스윕 + 몸통 회전 스윕 기반 능동 물체 탐색 스킬
- ``adaptive_pick_skill``: 위 스킬들을 조합하는 최상위 집기 스킬
- ``grasp_place_skills``: 좌표 기반 범용 파지/배치 스킬

공개 스킬 클래스는 이 ``__init__``에서 재노출되므로, 외부에서는 기존과 동일하게
``from robo_claw_agent.skills.manipulation_skill.sequence import GraspSkill`` 등으로
사용할 수 있다.
"""

from __future__ import annotations

from .adaptive_pick_skill import AdaptivePickObjectSkill
from .align_skill import AlignRightArmToFrontSkill
from .aruco_calib import MarkerResidualCache, _marker_tf_residual
from .ee_state import _grasp_center_base_xyz
from .fixed_pattern_grasp import FixedPatternGraspSkill
from .grasp_place_skills import GraspSkill, PlaceSkill
from .gripper_target_skills import ObserveGripperTargetSkill, ServoGripperToObjectSkill
from .gripper_vision import _fallback_cup_center_from_gripper_image, _observe_gripper_target
from .head_assist import HeadAssist, lateral_align
from .head_vision import ObserveHeadTargetSkill, _observe_head_target
from .joint_state import _joint_positions
from .recovery import (
    build_sequence_failure,
    cleanup_attempt_state,
    recover_arm_to_stow,
    recover_head_to_pose,
)
from .right_side_pick_skills import (
    PickFromRightSideZoneSkill,
    PickFrontObjectSkill,
    PrepareRightSidePickSkill,
)
from .search_skill import SearchObjectSkill
from .surface_classify import ClassifyObjectSurfaceSkill
from .vla_pick_skills import VLABasedPickFrontObjectSkill, VLABasedPickGripperObjectSkill

__all__ = [
    "AdaptivePickObjectSkill",
    "AlignRightArmToFrontSkill",
    "ClassifyObjectSurfaceSkill",
    "FixedPatternGraspSkill",
    "GraspSkill",
    "HeadAssist",
    "MarkerResidualCache",
    "ObserveGripperTargetSkill",
    "ObserveHeadTargetSkill",
    "PickFromRightSideZoneSkill",
    "PickFrontObjectSkill",
    "PlaceSkill",
    "PrepareRightSidePickSkill",
    "SearchObjectSkill",
    "ServoGripperToObjectSkill",
    "VLABasedPickFrontObjectSkill",
    "VLABasedPickGripperObjectSkill",
    "build_sequence_failure",
    "cleanup_attempt_state",
    "lateral_align",
    "recover_arm_to_stow",
    "recover_head_to_pose",
    # 아래는 스킬은 아니지만, 단위 테스트에서 직접 호출/monkeypatch하기 위해
    # 하위 호환 목적으로 재노출하는 내부 헬퍼다.
    "_fallback_cup_center_from_gripper_image",
    "_grasp_center_base_xyz",
    "_joint_positions",
    "_marker_tf_residual",
    "_observe_gripper_target",
    "_observe_head_target",
    "lateral_align",
]
