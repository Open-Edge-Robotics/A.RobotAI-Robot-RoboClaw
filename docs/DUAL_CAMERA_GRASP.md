# 헤드 카메라 병용 파지 (Dual-Camera Grasp)

Stretch 3에서 `adaptive_pick_object`가 파지 직전 단계에서 반복 실패하던 문제를 해결하기 위해,
그리퍼 카메라 단독 서보에 헤드 카메라 관측을 병용하도록 확장한 구조를 설명합니다.

## 1. 왜 필요했나

### 1.1 근접 시 관측 소실

그리퍼 카메라(D405)는 **손목에 고정**되어 있습니다. `wrist_extension`이 늘어나면 카메라도 같이
전진하므로 대상이 화면 하단으로 밀리고, 근접하면 컵이 프레임을 채워 COCO로 학습된
YOLOv8n이 물체를 인식하지 못합니다. 로봇 자체에 붙은 ArUco 마커와 그리퍼 손가락이
오탐을 유발하는 것도 겹친다.

기존 `ServoGripperToObjectSkill`에는 이 실패를 메우려는 workaround가 세 개 있었고,
전부 "관측이 사라진다"는 전제 위에 있었다:

| 위치                                                     | workaround                                           |
| :------------------------------------------------------- | :--------------------------------------------------- |
| `gripper_target_skills.py` 대상 소실 분기                | `joint_lift`를 ±로 **눈감고 흔들어** 재획득 (컵 5회) |
| `gripper_vision._fallback_cup_center_from_gripper_image` | YOLO 실패 시 HSV 색상으로 컵 중심 추정               |
| `gripper_target_skills.py` depth 소실 분기               | **직전 관측 기준으로 눈감고 집기**                   |

### 1.2 좌우(`err_x`) 오차 보정 수단 부재

기존 서보의 제어량은 `err_y`→`joint_lift`, `depth`→`wrist_extension` 둘뿐이었다.
`err_x`는 계산만 하고 사용하지 않았는데, `ready_to_grasp` 판정에는 들어간다
(`gripper_vision.py`의 `centered`).

→ 물체가 팔 작업축에서 좌우로 벗어나 있으면 **보정 수단이 없어 36스텝을 소진하고 실패**했다.
`require_ready` 게이트에 막혀 gripper close가 아예 실행되지 않는다.
Stretch 3에서 이 자유도는 **베이스 회전**뿐입니다.

### 1.3 헤드 카메라가 답이 되는 이유

`stretch_kinematics._fk_position`으로 확인한 grasp center 위치(base_link):

| `wrist_extension` | grasp_center xyz            |   방위 |
| ----------------: | :-------------------------- | -----: |
|              0.00 | (-0.021, **-0.415**, 0.710) | -92.9° |
|              0.26 | (-0.021, **-0.675**, 0.710) | -91.8° |
|              0.52 | (-0.021, **-0.935**, 0.710) | -91.3° |

팔은 -y(오른쪽)로 뻗고 헤드는 마스트 상단(z≈1.35m)에 있습니다. **헤드→파지점 거리는
0.8~1.0m로 D435i 유효 측정 범위 안**입니다. 헤드 카메라는 손목에 붙어있지 않으므로
팔이 뻗어도 프레이밍이 무너지지 않고, 거리가 있어 YOLO가 물체 전체를 보는 뷰를 유지합니다.

## 2. 구조

```
adaptive_pick_object
 ├ 1) search_object            (헤드 pan/tilt 스윕으로 방위 탐색)
 ├ 2) align_right_arm_to_front (몸통 정렬)
 ├ 3) prepare_right_side_pick  (ready pose, ext≈0.10)
 ├ 3.5) head_pan_tilt(gripper_side_head) ← 신규: 헤드를 파지 구역 감시 자세로
 ├ 3.6) observe_head_target               ← 신규: 헤드 3D 관측 + ArUco 잔차 래치
 ├ 3.7) lateral_align                     ← 신규: 좌우 오차를 베이스 미세회전으로 제거
 │        (팔이 접혀 있는 지금이 회전이 안전한 유일한 시점)
 ├ 4) observe_gripper_target
 └ 5) vla_pick_gripper_object
       └ servo_gripper_to_object  ← 헤드 보조가 통합된 서보 루프
```

### 2.1 서보 루프의 우선순위 변경

```
매 스텝:
  그리퍼 카메라 관측
   ├ 성공 + ready_to_grasp  → close 진행 (기존과 동일)
   ├ 성공 + 보정 가능        → err_y/depth 기반 기존 로직 (기존과 동일)
   ├ 성공 + 보정할 델타 없음  → [신규] 좌우 오차로 판단 → lateral_align 시도
   ├ depth 소실             → [신규] 1순위 헤드로 파지 거리 확인
   │                          2순위(폴백) 직전 관측 기준 눈감고 집기
   └ 관측 실패              → [신규] 1순위 헤드 3D 오차로 유도
                              2순위(폴백) 기존 눈감고 lift 흔들기
```

**눈감고 집기/흔들기가 "첫 번째 수단"에서 "마지막 수단"으로 강등**된 것이 핵심입니다.
헤드 보조가 실패하면 기존 경로가 그대로 동작하므로 기능 후퇴가 없습니다.

### 2.2 헤드 유도 계산

```
obj_base = 헤드 관측 3D (map → base_link, 최신 TF) + ArUco 잔차
ee_base  = TF(base_link ← link_grasp_center), 실패 시 joint_states + FK
err      = obj_base - ee_base

(along, lateral, vertical) = _decompose_base_error(err)
  along    = err · arm_axis      → wrist_extension 델타 (+면 더 뻗어야 함)
  lateral  = err · arm_axis⊥     → 베이스 회전 필요 (팔 관절로는 불가)
  vertical = err.z               → joint_lift 델타 (FK상 z와 1:1)
```

`arm_axis`는 `stretch_kinematics._arm_reach_bearing_rad()`로 URDF 체인에서 유도한
단위벡터(≈ `(-0.03, -1.0)`)다.

### 2.3 좌우 정렬의 안전 제약

`rotation._ROTATE_SAFE_WRIST_EXTENSION_M = 0.15`는 **팔이 뻗은 상태의 베이스 회전을 금지**합니다.
뻗은 팔을 휘두르는 것은 물리적으로 위험하기 때문입니다. 따라서:

- **주 경로**: 서보 시작 **전**, ready pose(ext≈0.10)에서 1회 — 기본 활성
- **서보 중**: `allow_servo_base_rotation` **기본 False**로 옵트인.
  활성 시 안전 길이까지 먼저 접고 → 회전 → 원래 길이로 재신장

## 3. ArUco 차분 보정

### 3.1 무엇이 아닌가

마커로 **로봇 부위의 위치를 추정하지 않는다.** 그것은 joint encoder + URDF가 이미 정확히
알고 있는 값(`link_grasp_center` TF)이라 중복이고, 마커 검출 오차가 오히려 encoder FK보다
나쁠 수 있습니다.

### 3.2 무엇인가

헤드 카메라가 ArUco로 본 손목 마커 위치(`p_measured`)와 URDF가 말하는 같은 마커 위치
(`p_urdf`)를 비교해 **잔차**를 구한다:

```
residual = p_urdf(base_link) - p_measured(base_link)
```

이 잔차는 헤드 카메라 외부파라미터 + head pan/tilt 캘리브레이션 오차이며, **같은 헤드
카메라로 관측한 물체 좌표에도 똑같이 실려 있습니다.** 따라서 물체 관측에 잔차를 더하면 그
계통 오차가 상쇄됩니다. 얻는 것은 절대 위치가 아니라 "그리퍼→물체 상대 벡터"이며,
이것이 서보에 정확히 필요한 값입니다. (Hello Robot `stretch_calibration`과 같은 원리)

### 3.3 실행 방법

**이 저장소는 ArUco 노드를 실행하지 않는다.** RealSense 드라이버를 외부에 맡기는 기존
관례와 동일하게, 별도 터미널에서 실행한다:

```bash
ros2 launch stretch_core stretch_aruco.launch.py
```

미실행 시 `_marker_tf_residual`이 `None`을 반환하고 **offset 0으로 그대로 동작한다**
(헤드 보조 자체는 영향 없음).

### 3.4 방어 로직

잔차는 다음 검사를 모두 통과해야 적용됩니다. 하나라도 실패하면 보정 없이 진행합니다.

| 검사                 | 기본값                          | 이유                               |
| :------------------- | :------------------------------ | :--------------------------------- |
| 마커 TF stamp 신선도 | `aruco_marker_max_age_sec` 1.5s | 지금 실제로 보이는 마커인가        |
| 잔차 크기            | `aruco_max_residual_m` 0.10m    | 마커 오검출 의심                   |
| 쌍 간 일관성         | `aruco_residual_agree_m` 0.05m  | 두 마커가 다른 답을 내면 신뢰 불가 |
| 래치 TTL             | `aruco_residual_ttl_sec` 60s    | 자세가 크게 바뀌면 전제가 무효     |

### 3.5 래칭이 필요한 이유

팔이 뻗으면 손목 마커가 그리퍼·물체에 가려지거나 헤드 FOV를 벗어난다. 따라서 잔차는
**마커가 잘 보이는 시점(팔이 접힌 ready pose)에 계산해 보관**하고, 서보 중에는 마커가
다시 보일 때만 갱신한다(`MarkerResidualCache`).

## 4. 파라미터

| 파라미터                     | 기본값                | 역할                                   |
| :--------------------------- | :-------------------- | :------------------------------------- |
| `use_head_assist`            | `True`                | 헤드 카메라 보조 전체 on/off           |
| `head_assist_pose`           | `"gripper_side_head"` | 서보 중 헤드 자세                      |
| `head_assist_ready`          | `False`               | 헤드가 이미 배치됨(상위 스킬이 내려줌) |
| `use_aruco_correction`       | `True`                | ArUco 차분 보정 적용                   |
| `allow_servo_base_rotation`  | `False`               | 서보 **중** 베이스 회전 허용           |
| `max_base_rotations`         | `1`                   | 서보 중 회전 최대 횟수                 |
| `max_head_guided_steps`      | `6`                   | 헤드 유도 최대 스텝 수                 |
| `head_move_settle_sec`       | `0.5`                 | 헤드 이동 후 대기                      |
| `head_min_score`             | `0.3`                 | 헤드 검출기 최소 confidence            |
| `grasp_confirm_radius_m`     | `0.05`                | 헤드 기준 "파지 가능" 반경             |
| `lateral_deadband_deg`       | `1.5`                 | 이 이내는 회전하지 않음                |
| `max_lateral_correction_deg` | `12.0`                | 초과 시 오검출 의심으로 거부           |
| `dual_view_agree_m`          | `0.15`                | 헤드/그리퍼 관측 불일치 임계           |

전부 스킬 파라미터로 오버라이드 가능하고, ArUco 관련 항목은 `stretch3_config.yaml`에도 있습니다.

## 5. 실기 튜닝 절차

계획 단계에서 확정할 수 없어 실기 확인이 필요한 항목들입니다.

### 5.1 `gripper_side_head` 각도 (우선순위 높음)

FK로 계산한 헤드→grasp center 방위는 약 **-87°**(ext 0.26 기준)인데, 현재 프리셋은
pan **-103°**(-1.8 rad)다. 약 16° 차이로 D435i HFOV(~69°) 안에는 들어오지만 중앙에서 벗어난다.

```bash
# 1. 파지 준비자세로 이동
ros2 service call /robo_claw_agent/execute_skill robo_claw_msgs/srv/ExecuteSkill \
  '{skill_name: "prepare_right_side_pick", params_json: "{}"}'
# 2. 헤드를 후보 자세로 이동시켜 가며 화면 확인
ros2 service call /robo_claw_agent/execute_skill robo_claw_msgs/srv/ExecuteSkill \
  '{skill_name: "head_pan_tilt", params_json: "{\"pan_deg\": -90, \"tilt_deg\": -45}"}'
ros2 run rqt_image_view rqt_image_view /camera/color/image_raw
```

그리퍼와 물체가 **둘 다** 프레임 중앙 근처에 오는 각도를 찾아
`stretch3_config.yaml`의 `manipulation_named_poses_json`에 `gripper_side_head`로 등록한다
(코드 수정 불필요).

**헤드 pan 부호 규약**도 이때 확인한다 — `-1.8 rad`이 실제로 팔이 있는 오른쪽을 보는가.

### 5.2 ArUco TF 프레임 이름

```bash
ros2 launch stretch_core stretch_aruco.launch.py
ros2 run tf2_tools view_frames    # 생성된 frames.pdf에서 마커 프레임 이름 확인
ros2 run tf2_ros tf2_echo base_link wrist_inside
ros2 run tf2_ros tf2_echo base_link link_aruco_inner_wrist
```

두 값의 차이가 잔차다. 프레임 이름이 다르면 `stretch3_config.yaml`의
`aruco_marker_frame_pairs_json`만 고치면 됩니다.

### 5.3 잔차 크기 판정

`observe_head_target` 결과의 `aruco_corrected_base_xyz`와 로그의 `aruco_diagnostics`를 본다.

- 잔차 < 2cm: 정상. 보정이 잘 동작합니다.
- 잔차 2~5cm: 보정으로 흡수 가능하나 캘리브레이션 점검 권장.
- **잔차 > 5cm: `stretch_calibration`을 먼저 다시 돌릴 것.** ArUco 보정은 잔차를 상쇄할 뿐
  근본 캘리브레이션을 대체하지 못합니다.

### 5.4 진단 스킬

```bash
# 헤드가 대상을 보고 있는지, base_link 3D가 맞는지 확인
ros2 service call /robo_claw_agent/execute_skill robo_claw_msgs/srv/ExecuteSkill \
  '{skill_name: "observe_head_target", params_json: "{\"target_object\": \"cup\"}"}'
```

`servo_gripper_to_object` 결과의 `head_assist` 필드에 유도 스텝 수, 베이스 회전 횟수,
단계별 진단이 모두 담긴다.

## 6. 관련 파일

| 파일                                | 역할                                          |
| :---------------------------------- | :-------------------------------------------- |
| `sequence/head_vision.py`           | 헤드 카메라 3D 관측, `ObserveHeadTargetSkill` |
| `sequence/ee_state.py`              | TF/FK 기반 grasp center 위치, 오차 분해       |
| `sequence/aruco_calib.py`           | ArUco 차분 잔차 계산 + 래치                   |
| `sequence/head_assist.py`           | 헤드 보조 유도 + 좌우 정렬                    |
| `sequence/detection_common.py`      | 카메라 무관 detection 폴링 (그리퍼/헤드 공용) |
| `sequence/gripper_target_skills.py` | 서보 루프 (헤드 보조 통합 지점)               |
| `sequence/adaptive_pick_skill.py`   | 3.5~3.7단계 배선                              |

관련 문서: `docs/MANIPULATION.md`, `docs/STRETCH3_TEST_GUIDE.md`,
`docs/HEAD_CAMERA_UPRIGHT_ROTATION.md`
