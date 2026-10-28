# NAVI_ISSUE — Former 실기 Nav2 멈춤/크래시 이슈 정리

이 문서는 Former 실기(former0045 / former0047)에서 발생한 Nav2 멈춤 문제의 원인과 해결을 다른 에이전트·엔지니어가 바로 참고할 수 있도록 정리한 이슈 노트입니다. 기능 가이드는 `former/FORMER_GUIDE.md` 를 참고합니다.

## 0. 요약 (먼저 읽기)

**상태: 해결됨 (2026-10-23).** 두 로봇 모두 적용 완료했고, former0047 에서 기동 후 약 15분간 골 11건을 받는 동안 크래시가 없었습니다.

| 항목 | 내용 |
| --- | --- |
| 증상 | 골을 찍어도 안 움직이거나 주행 중 멈추고, 로그에 `Message Filter dropping` / `Extrapolation` / `Controller patience exceeded` 가 폭주 |
| 직접 원인 | `nav2_amcl` 프로세스가 SIGSEGV(exit -11)로 죽어 `map→odom` TF 가 사라짐. 위 로그 폭주는 전부 이 결과 |
| 근본 원인 | `params` 의 `recovery_alpha_fast/slow` 가 `0.1 / 0.001`(0 아님). AMCL 이 map 포인터를 NULL 로 저장한 채 무작위 파티클을 주입하다 NULL 역참조 (3.5절) |
| 해결 | `former/params/nav2_params.yaml`, `nav2_params_narrow.yaml` 의 `recovery_alpha_fast/slow` 를 `0.0` 으로 설정 후 `./restart_nav.sh` |
| 부수 문제(별개) | 계획 실패(3.1), 주행 명령 미전달(3.2), 스캔 스탬프 불일치(3.3). 각각 다른 증상이며 `run_nav_stable.sh` 가 함께 처리 |
| 좁은 문 통과 실패 (2026-10-26) | costmap 이 실측 기하보다 넓게 지정됨(global `robot_radius` 0.26 vs 실측 half 0.202, inflation 0.35/csf 3.0 = 계단 코스트장). 실패의 주류는 제어가 아니라 **계획**(0047 로그 52분: 골 42, 도달 12, 계획 실패 19). 3.8절 |
| 트레이드오프 | 위치를 크게 잃었을 때의 자동 복구(kidnap recovery)가 꺼지므로 RViz 에서 initial pose 를 다시 지정해야 함 |

**크래시가 다시 나면 이 순서로 봅니다.**

1. `docker logs former00XX_nav 2>&1 | grep "process has died"` 로 죽은 노드를 확인합니다. `amcl` 이 아니면 이 문서의 원인이 아닙니다.
2. `ros2 param get /amcl recovery_alpha_fast` 가 `0.0` 인지 확인합니다. 아니라면 `former_nav2_run.sh` 등 핫픽스 없는 경로로 떴거나 params 가 되돌아간 것입니다(5절).
3. `0.0` 인데도 죽으면 새로운 원인입니다. 6절 절차로 core 를 받아 백트레이스부터 확인합니다. 로그만으로 추측하지 않습니다(이번 건에서 추측이 두 번 빗나갔습니다, 3.6절).

**관련 파일**

| 경로 | 역할 |
| --- | --- |
| `former/params/nav2_params.yaml`, `nav2_params_narrow.yaml` | Nav2 파라미터. `recovery_alpha`, costmap, `scan_restamped` 토픽 |
| `former/former00XX/run_nav_stable.sh` | 핫픽스를 적용한 nav 기동(0045/0047 동일) |
| `former/former00XX/restart_nav.sh` | nav 컨테이너만 지우고 재기동 |
| `former/scripts/check_nav_params.py` | 파라미터 계약 검사(recovery_alpha, footprint, inflation 절벽, 해상도) |
| `former/scripts/nav_map_tools.py` | 지도 잡음 진단·정리, 병목 폭과 임계값별 계획 가능 면적 계산 |
| `former/maps/map_konjiam_L_107-1_clean.pgm/.yaml` | 잡음 점 제거·벽 구멍 폐쇄 지도(원본 유지, 3.8절) |
| `former/FORMER_GUIDE.md` 9절 | 같은 내용의 운영 가이드 |
| 로봇 `~/konjiam/former/former0047/debug/` | 크래시 core, 백트레이스, 로그(저장소에는 없음) |

## 1. 증상

- RViz 에서 `Nav2 Goal` 을 찍어도 로봇이 이동하지 않거나, 이동 중 갑자기 멈춥니다.
- 멈춘 뒤 로그에 `Message Filter dropping`, `Extrapolation Error looking up target frame`, `Controller patience exceeded` 가 폭주합니다.

## 2. 환경

| 항목 | 값 |
| --- | --- |
| 로봇 | former0047(Jetson, aarch64), former0045(aarch64) |
| 이미지 | `former_docker:1.0` (벤더 제공, 소스/Dockerfile 없음). dpkg DB(`/var/lib/dpkg/status`)가 비어 있고 ROS apt 키가 만료(`EXPKEYSIG F42ED6FBAB17C654`)되어 `apt-get update`/`install` 이 실패합니다. |
| ROS | Humble, `nav2_amcl` 1.1.12, `ROS_DOMAIN_ID=4` |
| RMW | nav 컨테이너는 `rmw_cyclonedds_cpp`(백트레이스로 확인, `/root/cyclonedds_conf` 마운트). `realsense_docker`, `remote-rviz-sidecar` 도 CycloneDDS |
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
- `tf2_ros::MessageFilter` 가 스캔을 드롭합니다.
- 조치: `scan_restamp` 노드가 `/scan` 을 ROS 시간으로 다시 찍어 `/scan_restamped` 로 발행하고 AMCL·costmap 이 이를 사용합니다.
- 이 조치 이후에도 AMCL 크래시는 계속되었으므로 크래시의 원인은 아닙니다(3.5절).

### 3.4 AMCL 사망이 모든 후속 오류의 원인

AMCL 이 죽으면 `map → odom` 변환이 사라져 다음이 연쇄 발생합니다.

- costmap: 스캔을 map 으로 변환 불가 → `Message Filter dropping`
- bt_navigator: goal 을 map 으로 변환 불가 → `Extrapolation ... into the past` (요청 시각은 골을 찍은 시각)
- controller: `Controller patience exceeded` 반복
- lifecycle_manager: 4초 뒤 `CRITICAL FAILURE: SERVER amcl IS DOWN` 으로 localization 스택을 내립니다.

즉 로그 폭주는 원인이 아니라 결과입니다. 노드를 개별 프로세스로 띄우면(4절) 크래시 노드가 로그에 명시됩니다.

### 3.5 AMCL SIGSEGV 근본 원인 — `recovery_alpha` + NULL map 포인터 (확정)

core dump 백트레이스(2026-10-23, former0047)로 확정했습니다.

```
Program terminated with signal SIGSEGV, Segmentation fault.   si_addr = 0x10
#0  nav2_amcl::AmclNode::uniformPoseGenerator(void*)   libamcl_core.so
#1  pf_update_resample()                               libpf_lib.so
#2  nav2_amcl::AmclNode::laserReceived(...)            libamcl_core.so
#5  tf2_ros::MessageFilter<LaserScan>::transformReadyCallback(...)
```

| 관찰 | 값 |
| --- | --- |
| 크래시 명령 | `ldr d1, [x19, #16]`, `x19 = 0x0` → `map_t::scale`(오프셋 16) 읽기 |
| `pf_t::random_pose_fn` | `uniformPoseGenerator` |
| `pf_t::random_pose_data` | `0x0` (NULL) |
| `alpha_slow` / `alpha_fast` | `0.001` / `0.1` |
| `w_slow` / `w_fast` | `0.00892` / `0.00884` (`w_fast < w_slow`) |

동작 원리:

1. AMCL 은 configure 단계에서 파티클 필터를 만들며 `map_` 을 `random_pose_data` 로 저장합니다. 이 시점에는 맵을 받기 전이라 NULL 이 저장되고, 이후 맵을 받아도 갱신되지 않습니다.
2. `recovery_alpha_fast/slow` 가 0 이 아니면 스캔 매칭 점수가 떨어져 `w_fast < w_slow` 가 되는 순간부터 리샘플링마다 `w_diff = 1 - w_fast/w_slow` 확률로 무작위 파티클을 주입합니다. 이때 NULL map 으로 `uniformPoseGenerator` 를 호출해 죽습니다.
3. 매칭 점수는 주행, 수동으로 밀기, 동적 장애물만으로도 떨어지므로 크래시 시점이 무작위로 보입니다. 실제로 대기 중 19분 만에, 또는 골 수신 1초 뒤에 크래시했습니다.

Nav2 기본값은 `recovery_alpha_fast: 0.0`, `recovery_alpha_slow: 0.0` 입니다. 벤더 `nav2_params.yaml` 이 ROS1 amcl 튜토리얼 값(0.1 / 0.001)을 사용해 이 경로가 열려 있었습니다.

조치: 두 값을 `0.0` 으로 설정합니다(4절 5번). 무작위 파티클 주입이 꺼지므로 위치를 크게 잃은 경우(kidnap) 자동 복구는 되지 않습니다. 그때는 RViz 에서 initial pose 를 다시 지정합니다.

### 3.6 기각된 가설

| 가설 | 판정 근거 |
| --- | --- |
| RViz `Particle Cloud` 구독이 방아쇠 | 구독자가 없는 상태에서도 크래시했습니다. RViz 를 켜는 시점과 골을 보내는 시점이 겹쳐 상관관계처럼 보였습니다. |
| USB 재열거 | 키보드 허브(`1-2.1`) 분리 시 같은 허브의 베이스 FTDI(`1-2.3`)는 재열거되지 않았고, 크래시는 5.5분 뒤였습니다. `/dev/ttyUSB*` 의 mtime 은 I/O 만으로 갱신되므로 재열거 근거로 쓸 수 없습니다. 커널 로그(`/var/log/kern.log`, `adm` 그룹으로 읽기 가능)로 판정합니다. |

### 3.7 진단 이력 (같은 길을 다시 가지 않기 위한 기록)

| 단계 | 한 일 | 결과 |
| --- | --- | --- |
| 1 | 로그의 `Message Filter dropping` / `Extrapolation` 을 원인으로 추적 | 전부 AMCL 사망의 결과였음(3.4) |
| 2 | 스캔 스탬프 불일치(3.3)를 AMCL 크래시 원인으로 보고 `scan_restamp` 적용 | 드롭은 사라졌지만 AMCL 크래시는 계속됨 |
| 3 | RViz `Particle Cloud` 구독을 방아쇠로 추정(크래시 직전 로그에 항상 있었음) | 구독자가 없는 부팅 직후 크래시로 기각. RViz 를 켜는 사람이 곧 골을 보내서 같이 나타났을 뿐이었음 |
| 4 | 키보드 USB 분리를 방아쇠로 추정 | `kern.log` 대조로 기각(3.6) |
| 5 | core dump + gdb 백트레이스 확보 (6절) | `uniformPoseGenerator` NULL map 역참조 확정 → `recovery_alpha` 가 경로를 여는 조건임을 코드와 파라미터로 확인 |
| 6 | `recovery_alpha` 0.0 적용 | 골 11건 주행 무크래시 |

교훈: 크래시 직전 로그의 공통 문구는 원인이 아니라 같은 시점에 일어나는 다른 사건일 수 있습니다. SIGSEGV 는 추측하지 말고 core 로 확인합니다.

### 3.8 좁은 문 통과 실패 — costmap 과다 지정 (2026-10-26)

증상: 여유가 있는 문(0.7~0.9 m)에서 멈추거나 회피 기동을 반복합니다. 크래시가 아니라 **계획 실패**가 주류입니다(0047 로그 52분: 골 42건, 도달 12건, `GridBased: failed to create plan` 19건, `No valid trajectories` 2건, `spin failed` 5건, `Spin: Collision Ahead` 4건, `clear entirely the global_costmap` 20회).

실측 기하: `base_link` collision mesh **0.404 x 0.404 m**, `wheel_separation` 0.305 m → half-width 0.202 m, 모서리 0.286 m. 선언값은 local footprint ±0.21, global `robot_radius` 0.26 이었습니다.

원인 4가지:

1. **global `robot_radius` 0.26** 이 실측 half-width 보다 29% 커서, 0.70 m 문에서 전역 경로 여유가 ±9 cm, 0.60 m 문은 ±4 cm 로 줄어듭니다.
2. **inflation 이 계단**입니다. nav2 비용식 `252*exp(-csf*(d-inscribed))` 에 `csf 3.0`, `inflation_radius 0.35` 를 쓰면 경계에서 cost 167 이 0 으로 끊깁니다. 0.70 m 문은 내부 전체가 고비용이고 0.73 m 문의 저비용 밴드는 3 cm 입니다.
3. **지도 잡음**: occupied 2,968 셀 중 고립 점 216개(7%), 벽 두께 5~10 cm, 1셀 구멍 다수. 고립 점 하나가 inflation 만큼 부풀면 문이 닫힙니다.
4. **접촉 판정 비대칭**: `FollowPath.critics` 에 `ObstacleFootprint` 가 없어 실제 충돌 판정은 중심점 하나입니다. 코스트맵은 넓게 부풀렸는데 접촉은 관대합니다.

적용값(두 로봇 공통, `former/params/nav2_params.yaml`):

| 항목 | 이전 | 현재 |
| --- | --- | --- |
| local footprint | ±0.21 | ±0.205 |
| global 형상 | `robot_radius: 0.26` | local 과 같은 footprint |
| `inflation_radius` (global/local) | 0.35 | 0.30 |
| `cost_scaling_factor` | 3.0 | 10.0 |
| global `resolution` | 0.025 | 0.05 |
| `BaseObstacle.scale` | 0.02 | 0.10 |
| `angular_dist_threshold` | 0.485 | 0.60 |

지도는 `former/scripts/nav_map_tools.py clean` 으로 정리한 `map_konjiam_L_107-1_clean.pgm` 을 준비했습니다(occupied 2,968→2,697, 고립 점 216→25, clearance>0.205 계획 가능 면적 79,317→82,598, 최대 연결 영역 77,445→82,592). 시작 위치 `(-0.731, 4.521)` 에서 본 경로별 최소 통과 폭은 `(6.59,-2.28)` 0.70→**0.80 m**, `(9.55,0.45)` 0.70→**0.73 m**, `(-1.90,3.50)` 0.20→**0.60 m**, `(-4.90,3.00)` 0.36→**0.45 m** 로 넓어졌습니다. 원본 지도는 그대로 두고 `RC_MAP_FILE` 로 선택합니다.

검증과 주의사항은 `former/FORMER_GUIDE.md` 9.6~9.7절을 참고합니다. 요약하면:

- `python3 former/scripts/check_nav_params.py` 가 PASS 여야 합니다(이전 파라미터에서는 FAIL).
- 회복 기동(`spin`/`backup`)은 footprint 전체를 검사하므로 1.0 m 보다 좁은 통로에서 실패하는 것이 정상입니다.
- 주행 검증 전에는 실기를 움직이지 않습니다. 검증 지표는 문 통과 성공률, `failed to create plan` 0건, `spin failed` 0건입니다.

## 4. 현재 적용된 핫픽스 (이미지 재빌드 없이)

`~/konjiam/former/former00XX/run_nav_stable.sh` 가 아래를 자동 적용합니다.

1. `use_composition:=False` — 노드별 개별 프로세스(크래시 국소화 + 범인 노드 식별)
2. `navigation_launch.py` 패치 바인드마운트 — `cmd_vel_smoothed → /base_controller/cmd_vel_unstamped`
3. `bringup_launch.py` 패치 바인드마운트 + `/opt/rc/scan_restamp.py` — 스캔 재스탬프 노드 실행
4. `~/konjiam/former/params/nav2_params.yaml` — `track_unknown_space: false`, `inflation_radius: 0.35`, `scan_topic: scan_restamped`, costmap 관측 토픽 `/scan_restamped`
5. 같은 파일의 amcl — `recovery_alpha_fast: 0.0`, `recovery_alpha_slow: 0.0` (3.5절). 백업은 `nav2_params.yaml.bak_20261023-recovery_alpha` 입니다. former0045, former0047 모두 적용했습니다.

검증된 정상 상태 지표(재기동 직후 1~2분):

| 지표 | 정상 기준 |
| --- | --- |
| `/scan_restamped` | 15 Hz |
| `Message Filter dropping` | 0 건 |
| `process has died` | 0 건 |
| `ros2 param get /amcl recovery_alpha_fast` | `0.0` |
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
docker exec former00XX_nav bash -lc 'source /opt/ros/humble/setup.bash; ros2 param get /amcl recovery_alpha_fast'
docker exec former00XX_nav bash -lc 'source /opt/ros/humble/setup.bash; ros2 topic hz /base_controller/odom | head -2'
```

`component_container_isolated-1` 이 보이면 예전 방식(`./former_nav2_run.sh`)으로 뜬 것이므로 위 핫픽스가 모두 미적용 상태입니다.

## 6. 크래시 백트레이스 확보 절차

벤더 이미지에서는 gdb 를 설치할 수 없고, 호스트 apport 는 컨테이너 크래시를 버립니다(`/var/log/apport.log` 에 `crashed in a container without apport support`). 다음 절차로 core 와 백트레이스를 얻습니다.

1. 디버그 이미지를 만듭니다(원본 `former_docker:1.0` 은 변경하지 않습니다). 이 이미지는 libc/libstdc++ 가 갱신되므로 분석 전용으로만 씁니다.

   ```bash
   docker run --name gdb_build --entrypoint bash former_docker:1.0 -c '
     export DEBIAN_FRONTEND=noninteractive
     rm -f /etc/apt/sources.list.d/ros2-latest.list; rm -rf /var/lib/apt/lists/*
     apt-get update -qq && apt-get install -y -qq --no-install-recommends \
       -o Dpkg::Options::=--force-confold -o Dpkg::Options::=--force-confdef gdb'
   docker commit gdb_build former_docker:1.0-gdb && docker rm gdb_build
   ```

2. `core_pattern` 을 파일 경로로 바꿉니다. 컨테이너 core 크기 제한은 이미 `unlimited` 입니다. sudo 대신 privileged 컨테이너로 씁니다. 원래 값은 재부팅 시 복원되며, 수동 복원은 저장해 둔 값을 같은 방법으로 다시 씁니다.

   ```bash
   cat /proc/sys/kernel/core_pattern > core_pattern.orig
   docker run --rm --privileged --entrypoint sh former_docker:1.0 \
     -c 'echo /tmp/core.%e.%p.%t > /proc/sys/kernel/core_pattern'
   ```

3. 크래시 후 core 를 꺼내 분석합니다. sysroot 는 nav 컨테이너의 살아 있는 프로세스 rootfs(`/proc/<pid>/root`)를 써서 원본 라이브러리로 심볼을 맞춥니다.

   ```bash
   docker cp former00XX_nav:/tmp/core.amcl.<pid>.<t> ./debug/
   docker run --rm --pid=container:former00XX_nav --cap-add SYS_PTRACE -v $PWD/debug:/dbg \
     --entrypoint bash former_docker:1.0-gdb -c '
       P=$(pgrep -f planner_server | head -1)
       gdb -batch -ex "set sysroot /proc/$P/root" \
         -ex "file /proc/$P/root/opt/ros/humble/lib/nav2_amcl/amcl" \
         -ex "core-file /dbg/core.amcl.<pid>.<t>" -ex bt -ex "thread apply all bt 25"'
   ```

former0047 의 분석 산출물(core, 백트레이스, 크래시 로그, `core_pattern.orig`)은 `~/konjiam/former/former0047/debug/` 에 있습니다. 분석 후 `core_pattern` 은 apport 값으로 복원했습니다.

## 7. 남은 가설과 다음 실험

| # | 가설 | 검증 방법 | 상태 |
| --- | --- | --- | --- |
| A | AMCL 이 `/particle_cloud` 발행 시 크래시 | — | **기각** (3.6절) |
| B | odom 주기 저하(40 Hz)가 controller patience 초과 유발 | `former_robot` 재기동 후 80 Hz 회복 여부와 주행 확인. 2026-10-23 부팅 직후에도 40 Hz 였으므로 정상 기준값부터 재확인 필요 | 미확인 |
| C | USB 재열거가 TF 타이밍을 깨뜨림 | `kern.log` 의 `USB disconnect` 와 크래시 시각 대조 | **기각** (3.6절) |
| D | nav2_amcl 1.1.12 자체 버그 | core dump 백트레이스 | **확정** (3.5절), `recovery_alpha` 0.0 으로 회피 |
| E | AMCL 대신 slam_toolbox localization 사용 | 동일 구역 1회 매핑 후 `.posegraph/.data` 생성 | 불필요 시 보류 |
| F | `recovery_alpha` 0.0 적용 후 주행 시 AMCL 생존 | 주행 중 `process has died` 0 건 유지 | **확인** — former0047 에서 기동 후 21:10 까지 `Received a goal` 11건 동안 `process has died` 0, `Message Filter dropping` 0, core 0 (2026-10-23). former0045 는 재기동 후 파라미터 로드와 노드 active 만 확인했고 주행 검증은 아직입니다. 장시간 운용은 계속 관찰합니다. |
| G | 좁은 문 실패의 주원인은 costmap 과다 지정 | 정적 분석(기하·inflation 계산·지도 병목·로그 집계) | **적용 완료**(3.8절). 0.70~0.73 m 문 왕복 주행으로 확인 필요 |
| H | 계획 실패 16건은 live costmap 마킹 때문 | 실패 시점 `/global_costmap/costmap` 덤프를 정적 지도와 비교 | 미확인 — 골 `(6.59,-2.28)` 은 정적 지도에서 clearance 0.585 m, 주 연결 영역 안에 있어 정적 요인만으로 설명되지 않습니다 |

## 8. 참고: 로그 해석 요령

- `Message Filter dropping ... timestamp ... earlier than all the data in the transform cache`: TF 체인이 끊겼거나(AMCL 사망) 스캔 스탬프가 어긋났다는 뜻입니다. 스캔 스탬프 문제라면 `scan_restamp` 적용 여부와 `/scan_restamped` Hz 를 먼저 확인합니다.
- `Extrapolation ... Requested time <골 시각>`: 골을 찍은 뒤 오래 지나 TF 버퍼(기본 10초)에 해당 시각 데이터가 없어서 발생합니다. 골을 다시 찍으면 사라지므로, 근본 원인은 그 사이에 골 처리가 실패한 것입니다.
- `Controller patience exceeded`: 로봇이 실제로 움직이지 못했다는 뜻입니다. `/base_controller/cmd_vel_unstamped` 에 `velocity_smoother` 가 publisher 로 있는지, 값이 실제로 흐르는지 확인합니다.
- `[amcl-N]: process has died ... exit code -11` 이고 직전에 다른 원인 로그가 없으면 3.5절의 `recovery_alpha` 설정부터 확인합니다.
