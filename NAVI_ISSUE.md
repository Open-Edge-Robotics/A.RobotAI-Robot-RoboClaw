# NAVI_ISSUE — Former 실기 Nav2 멈춤/크래시 이슈 정리

이 문서는 Former 실기(former0045 / former0047)에서 발생한 Nav2 멈춤 문제를 다른 에이전트·엔지니어가 이어서 진단할 수 있도록 정리한 이슈 노트입니다. 기능 가이드는 `former/FORMER_GUIDE.md` 를 참고합니다.

## 1. 증상

- RViz 에서 `Nav2 Goal` 을 찍어도 로봇이 이동하지 않거나, 이동 중 갑자기 멈춥니다.
- 멈춘 뒤 로그에 `Message Filter dropping`, `Extrapolation Error looking up target frame`, `Controller patience exceeded` 가 폭주합니다.

## 2. 환경

| 항목 | 값 |
| --- | --- |
| 로봇 | former0047(Jetson, aarch64), former0045(aarch64) |
| 이미지 | `former_docker:1.0` (벤더 제공, 소스/Dockerfile 없음, 컨테이너 내 `apt-get update` 실패 → gdb 설치 불가) |
| ROS | Humble, `nav2_amcl` 1.1.12, RMW 기본값(Fast DDS), `ROS_DOMAIN_ID=4` |
| 지도 | RTAB-Map `grid_map` 결과를 PGM 으로 변환해 `map_konjiam_L_107-1` 로 교체 (324x307 @ 0.05 m, origin (-5.95,-4.95), 미지 52%) |
| 기동 | `~/konjiam/former/former00XX/run_nav_stable.sh` (아래 4절 참조) |

## 3. 규명된 원인 (순서대로)

### 3.1 계획 실패 — RTAB-Map 의 미지 영역

- `global_costmap.track_unknown_space: true` 상태에서 미지 52% 가 통행 불가로 처리되어 `GridBased: failed to create plan` 이 반복되었습니다.
- 조치: `track_unknown_space: false`, `inflation_radius 0.45 → 0.35`. 자유 셀이 13,161 → 65,820 로 늘고 `Begin navigating ...` 로그가 나오기 시작했습니다.

### 3.2 주행 명령 미전달 — `/cmd_vel` vs `/base_controller/cmd_vel_unstamped`

- 벤더 `navigation_launch.py` 는 `velocity_smoother` 의 최종 출력을 `/cmd_vel` 로 보내지만, 베이스(`/base_controller`)는 `/base_controller/cmd_vel_unstamped` 만 구독합니다. 원본 launch 의 리맵 `('/cmd_vel', '/base_controller/cmd_vel_unstamped')` 는 **주석 처리**되어 있습니다.
- 조치: `navigation_launch.py` 사본을 바인드마운트해 `cmd_vel_smoothed → /base_controller/cmd_vel_unstamped` 로 리맵. `velocity_smoother` 가 베이스 토픽 publisher 로 나타나는 것을 확인했습니다.

### 3.3 스캔 타임스탬프 불일치

- SICK 스캐너가 자체 시계로 `header.stamp` 를 채워 ROS 시각과 **-0.23초 ~ +0.68초**로 흔들립니다.
- `tf2_ros::MessageFilter` 가 스캔을 드롭하고, 그 직후 `nav2_amcl` 이 SIGSEGV(exit -11)로 죽는 패턴이 반복되었습니다.
- 조치: `scan_restamp` 노드가 `/scan` 을 ROS 시간으로 다시 찍어 `/scan_restamped` 로 발행하고 AMCL·costmap 이 이를 사용합니다.

### 3.4 AMCL 사망이 모든 후속 오류의 원인

AMCL 이 죽으면 `map → odom` 변환이 사라져 다음이 연쇄 발생합니다.

- costmap: 스캔을 map 으로 변환 불가 → `Message Filter dropping`
- bt_navigator: goal 을 map 으로 변환 불가 → `Extrapolation ... into the past` (요청 시각은 골을 찍은 시각)
- controller: `Controller patience exceeded` 반복

즉 로그 폭주는 원인이 아니라 결과입니다. 노드를 개별 프로세스로 띄우면(4절) 크래시 노드가 로그에 명시됩니다.

### 3.5 크래시 방아쇠 후보 ① RViz `Particle Cloud` 구독 (현재 가장 유력)

크래시 직전 로그는 항상 다음과 같습니다.

```
[amcl] createLaserObject
[amcl] [WARN] New subscription discovered on topic '/particle_cloud', requesting incompatible QoS ...
[ERROR] [amcl-3]: process has died [pid 103, exit code -11]
```

검증 실험(former0047):

| 조건 | 결과 |
| --- | --- |
| RViz 켠 상태 | `particle_cloud` QoS 경고 직후 AMCL SIGSEGV (반복) |
| **RViz 컨테이너 중지 상태로 2분 이상** | AMCL 생존, `process has died` 0건, `particle_cloud` 경고 0건, 드롭 0건 |

정황상 AMCL 이 `/particle_cloud` 를 실제로 발행할 때(구독자가 생겨 lazy publish 시작) 크래시하는 것으로 보입니다. 조치로 RViz 설정(`~/workspace/nav2/rviz/nav2_view.rviz`)의 `Particle Cloud` 디스플레이를 `Enabled: false` 로 변경했습니다(백업: `nav2_view.rviz.bak_*`). **RViz 를 켠 상태에서 장시간 생존 여부는 아직 재검증 전입니다.**

### 3.6 크래시 방아쇠 후보 ② USB 재열거 (기각되지는 않음)

- 키보드를 뽑은 직후 크래시한 사례가 있었고, 그때 `/dev/ttyUSB0`,`/dev/ttyUSB2` 타임스탬프가 갱신(재열거)되고 odom 주기가 80 Hz → 40 Hz 로 떨어졌습니다.
- USB 토폴로지상 베이스 시리얼(FTDI)이 키보드와 **같은 허브**에 있습니다.
- 단, 키보드를 건드리지 않은 실행에서도 크래시가 재발했으므로 USB 는 보조 요인으로 봅니다.

## 4. 현재 적용된 핫픽스 (이미지 재빌드 없이)

`~/konjiam/former/former00XX/run_nav_stable.sh` 가 아래를 자동 적용합니다.

1. `use_composition:=False` — 노드별 개별 프로세스(크래시 국소화 + 범인 노드 식별)
2. `navigation_launch.py` 패치 바인드마운트 — `cmd_vel_smoothed → /base_controller/cmd_vel_unstamped`
3. `bringup_launch.py` 패치 바인드마운트 + `/opt/rc/scan_restamp.py` — 스캔 재스탬프 노드 실행
4. `~/konjiam/former/params/nav2_params.yaml` — `track_unknown_space: false`, `inflation_radius: 0.35`, `scan_topic: scan_restamped`, costmap 관측 토픽 `/scan_restamped`

검증된 정상 상태 지표(재기동 직후 1~2분):

| 지표 | 정상 기준 |
| --- | --- |
| `/scan_restamped` | 15 Hz |
| `Message Filter dropping` | 0 건 |
| `process has died` | 0 건 |
| `/base_controller/cmd_vel_unstamped` publisher | teleop, **velocity_smoother**, robo_claw_core, former_auto_docking |
| `map→base_link` TF | 존재, 값이 크게 튀지 않음 |

## 5. 재현/확인 절차

```bash
cd ~/konjiam/former/former00XX
./restart_nav.sh                                    # 올바른 설정으로 재기동

docker ps -a | grep former00XX_nav                  # 컨테이너 생존 확인
docker logs former00XX_nav 2>&1 | grep -E "process has died|Magick" | tail -5
docker logs former00XX_nav 2>&1 | grep -c "Message Filter dropping"
docker logs former00XX_nav 2>&1 | grep -oE "component_container_isolated-1|\[amcl-[0-9]+\]" | sort -u
docker exec former00XX_nav bash -lc 'source /opt/ros/humble/setup.bash; ros2 node list | grep -c "^/amcl$"'
docker exec former00XX_nav bash -lc 'source /opt/ros/humble/setup.bash; ros2 topic hz /base_controller/odom | head -2'
```

`component_container_isolated-1` 이 보이면 예전 방식(`./former_nav2_run.sh`)으로 뜬 것이므로 위 핫픽스가 모두 미적용 상태입니다.

## 6. 남은 가설과 다음 실험

| # | 가설 | 검증 방법 | 상태 |
| --- | --- | --- | --- |
| A | AMCL 이 `/particle_cloud` 발행 시 크래시 | RViz `Particle Cloud` 비활성 상태에서 장시간 주행 | 수정 적용, **재검증 필요** |
| B | odom 주기 저하(40 Hz)가 controller patience 초과 유발 | `former_robot` 재기동 후 80 Hz 회복 여부와 주행 확인 | 미확인 |
| C | USB 재열거가 TF 타이밍을 깨뜨림 | udev symlink 고정 + 베이스 시리얼을 별도 허브로 분리 | 미적용 |
| D | nav2_amcl 1.1.12 자체 버그 | 백트레이스 필요(현 이미지에서 gdb/apt 불가) → 파생 이미지에 gdb 주입 필요 | 미확인 |
| E | AMCL 대신 slam_toolbox localization 사용 | 동일 구역 1회 매핑 후 `.posegraph/.data` 생성 | 미적용 |

### 다른 에이전트에게 묻고 싶은 것

1. `nav2_amcl` 1.1.12 에서 `/particle_cloud` 구독자 출현 시 SIGSEGV 하는 알려진 이슈가 있는지, 있다면 회피 파라미터가 있는지.
2. 벤더 이미지 재빌드 없이 `map_server`/`amcl` 을 독립 프로세스로 분리하고 **자동 재기동(respawn)** 시키는 표준 패턴(lifecycle manager bond 와의 충돌 회피 포함).
3. `former_docker:1.0` 에서 `apt-get update` 가 실패하는 원인(오프라인/저장소 제거)과, gdb 없이 커널 core dump 로 백트레이스 확보하는 방법.
4. RTAB-Map grid map(미지 52%)을 Nav2 실기 운용에 쓸 때 권장 costmap 설정(`track_unknown_space`, `allow_unknown`, inflation) 조합.

## 7. 참고: 로그 해석 요령

- `Message Filter dropping ... timestamp ... earlier than all the data in the transform cache`: TF 체인이 끊겼거나(AMCL 사망) 스캔 스탬프가 어긋났다는 뜻입니다. 스캔 스탬프 문제라면 `scan_restamp` 적용 여부와 `/scan_restamped` Hz 를 먼저 확인합니다.
- `Extrapolation ... Requested time <골 시각>`: 골을 찍은 뒤 오래 지나 TF 버퍼(기본 10초)에 해당 시각 데이터가 없어서 발생합니다. 골을 다시 찍으면 사라지므로, 근본 원인은 그 사이에 골 처리가 실패한 것입니다.
- `Controller patience exceeded`: 로봇이 실제로 움직이지 못했다는 뜻입니다. `/base_controller/cmd_vel_unstamped` 에 `velocity_smoother` 가 publisher 로 있는지, 값이 실제로 흐르는지 확인합니다.
