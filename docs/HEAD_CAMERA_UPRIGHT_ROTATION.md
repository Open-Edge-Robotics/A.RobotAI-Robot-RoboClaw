# 헤드 카메라(D435i) 세로 마운트 회전 보정

Stretch3 헤드 카메라가 물리적으로 세로(90도 회전)로 장착되어 있어, `object_detector_node`가
구독하는 `/camera/color/image_raw`가 90도 회전된 상태로 들어온다. YOLO(ONNX) 모델은 회전
불변이 아니므로 이 상태에서 `find_object` / `get_detections`의 인식률이 크게 떨어진다.

## 원인 요약

- `robo_claw_vision/src/object_detector_node.cpp`가 raw 이미지를 보정 없이 그대로 추론에 입력함.
- `robo_claw_bringup/config/stretch3_config.yaml`의 `camera_topic`이 raw 토픽(`/camera/color/image_raw`)을
  그대로 가리킴.
- `stretch_urdf`의 d435i 마운트 `rpy`가 `0 0 0`이라 TF에도 실제 90도 회전이 반영되어 있지 않음.
- 결과적으로 rqt_image_view 등에서 보이는 90도 회전 이미지가 그대로 탐지 파이프라인에 들어감.

## 해결: `stretch_core`의 `upright_rotater` 활성화

`stretch_ros2/stretch_core/launch/stretch_realsense.launch.py`에 이미 이 문제를 위한 노드가
정의되어 있다 (`image_rotate` 패키지의 `upright_rotater`, `target_x: -1.5708` rad = -90도 고정 회전).
단, 현재 `generate_launch_description()`의 리턴 리스트에서 **주석 처리되어 있어 실제로는 켜지지 않는다.**

### 로봇에서 적용할 단계

1. **패키지 설치 확인** (로봇 온보드에서)
   ```bash
   sudo apt install ros-humble-image-rotate   # 배포판에 맞는 rosdistro로 대체
   ```

2. **`stretch_realsense.launch.py`의 주석 해제**
   `stretch_ros2/stretch_core/launch/stretch_realsense.launch.py`의
   `generate_launch_description()` 반환부에서 `# upright_rotater,` 줄의 주석을 제거:
   ```python
   return LaunchDescription(declare_configurable_parameters(configurable_parameters) + [
        d435i_high_launch,
        d435i_low_launch,
        # d435i_configure,
        # d435i_frustum_visualizer,
        upright_rotater,   # 주석 해제
        ])
   ```
   재빌드(`colcon build --packages-select stretch_core`) 후 반영.

3. **`publish_upright_img:=true`로 런치** (이미 기본값 true이지만 명시 권장)
   ```bash
   ros2 launch stretch_core stretch_realsense.launch.py publish_upright_img:=true
   ```
   → `/camera/color/upright_image_raw` 토픽에 -90도 보정된 이미지가 발행되는지 확인:
   ```bash
   ros2 topic hz /camera/color/upright_image_raw
   ```

4. **robo_claw 쪽 카메라 토픽을 회전된 스트림으로 변경**
   `robo_claw_bringup/config/stretch3_config.yaml`:
   ```yaml
   camera_topic: "/camera/color/upright_image_raw"   # 기존 "/camera/color/image_raw"에서 변경
   ```
   `object_detector_node`가 이 파라미터로 구독하므로, 위 변경만으로 YOLO 입력이 upright 이미지로 바뀐다.

## 남은 주의사항 (depth 파이프라인)

`upright_rotater`는 **color 이미지만** 회전시키고, `depth_topic`
(`/camera/aligned_depth_to_color/image_raw`)과 `camera_info_topic`
(`/camera/aligned_depth_to_color/camera_info`)은 회전되지 않은 원본 그대로 유지됩니다.

`perception_skill/depth3d.py`의 `_compute_depth_point_base` → `deproject_pixel`은 color 이미지에서
얻은 bbox 중심 `(cx, cy)`를 그대로 depth 이미지/`CameraInfo`에 대입해 3D 역투영을 수행하므로,
**color만 회전시키고 depth/CameraInfo를 그대로 두면 좌표계 불일치로 depth 기반 위치 추정이 깨진다.**

즉 위 1~4단계는 "탐지율"은 즉시 개선하지만, `world_x`/`world_y`(`position_source: depth_tf`)
정확도는 별도 조치 전까지 신뢰할 수 없습니다. 옵션은 아래와 같습니다.

- (A) depth/CameraInfo도 동일하게 -90도 회전시키는 노드를 추가하고, `depth_topic`/`camera_info_topic`도
  회전된 버전으로 교체한다 (color와 동일한 패턴).
- (B) `object_detector_node`에서만 회전된 이미지를 쓰고, bbox 좌표를 depth 샘플링 직전에 원본
  (회전 전) 픽셀 좌표계로 역변환해서 `depth3d.py`에 넘긴다.
- (C) `stretch_urdf`의 d435i 마운트 `rpy`를 실제 물리 회전값으로 수정해 TF를 바로잡는다. 이건
  depth 좌표계 자체는 안 바꾸지만, `depth_point_map`을 map 프레임으로 변환하는 단계의 오차를
  줄이는 데 필요하다.

우선 탐지율 문제부터 확인하고, `world_x`/`world_y` 값이 실제와 어긋나는지 로봇에서 재검증한 뒤
필요하면 (A)/(B)/(C) 중 하나를 진행하는 것을 권장합니다.

## (B) 구현됨: `head_image_rotate_deg` 파라미터

옵션 (B)의 소비측 역회전이 구현되어 있습니다. `object_detector_node`는 그대로 upright 영상을
추론에 쓰고(회전 후 bbox = upright 좌표), depth 파이프라인 쪽에서 depth 샘플링/역투영 직전에
upright 픽셀을 raw 픽셀로 되돌린다.

- 구현: `perception_skill/depth3d.py:unrotate_pixel` + `core.py:_depth_point_for_pixel`
  (헤드 카메라일 때만, `head_image_rotate_deg != 0` 일 때만 적용).
- 파라미터: `head_image_rotate_deg`(도) — `stretch3_config.yaml`의
  `robo_claw_agent_node` 섹션 또는 search/find 호출 params로 전달. 기본 `0.0` = 보정 없음.
- **기하 모델(중요)**: `upright_rotater`(image_rotate)는 raw 640×480을 **640×640 정사각
  캔버스에 회전+패딩**한 upright 영상을 만든다(치수 단순 교환 480×640이 아님). 따라서
  `unrotate_pixel`은 OpenCV y-down 중심회전 규약(양수 각 = 시각적 CCW)의 일반 중심회전
  역변환을 쓴다: `p_r = raw_center + R(−a)·(p_u − up_center)`. upright 캔버스 폭/높이는
  기본 90° 휴리스틱으로 `max(raw_w,raw_h)` 정사각(640×640)을 추정하며, 필요 시
  `upright_image_width`/`upright_image_height` params로 명시 override 가능.
- 활성화: `upright_rotater(target_x=-1.5708=-90°)`를 썼다면 예상값은 `-90.0`(검증 결과
  `90.0`일 수도). **반드시 로봇에서 알려진 위치의 컵 픽셀로 `depth_distance_m`가 실제 거리와
  맞는지 확인한 뒤 설정**. 방향이 틀리면 depth가 더 나빠지므로 기본값은 0.0으로 둔다.
- 검증 절차(방향 확정): 컵을 로봇 정면 알려진 거리 D(자)에 두고, `head_image_rotate_deg`를
  `-90.0`과 `90.0` 각각으로 `find_object` 호출 → `depth_distance_m`가 D에 가까운 쪽이 올바른
  방향. 둘 다 D와 멀면 컵이 도달 거리 밖(집기 전 navigate 필요)이거나 매핑·camera_info 문제.
- 단위 테스트: `tests/test_depth3d.py`의 `test_unrotate_pixel_*`(swap/정사각패딩/중심불변
  /clamp/heuristic 케이스 포함).
