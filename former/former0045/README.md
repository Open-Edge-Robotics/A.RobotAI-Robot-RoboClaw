# former0045 전용 실행 스크립트 가이드

이 폴더의 스크립트들은 former-0045 로봇 환경(SLAM, Navigation2, RoboClaw Agent) 구동을 위한 Docker 런처입니다.

## 파일 구성

| 스크립트 | 설명 | 비고 |
| --- | --- | --- |
| [`run_former_env.sh`](run_former_env.sh) | Former ROS 2 환경 통합 컨트롤러 | `nav2`, `slam`, `nav2-rviz`, `slam-rviz`, `bash` 지원 |
| [`former_nav2_run.sh`](former_nav2_run.sh) | Navigation2 백그라운드 구동 래퍼 | `nav2 --daemon` 자동 적용 |
| [`former_nav2_rviz.sh`](former_nav2_rviz.sh) | Nav2 RViz 시각화 모니터링 | 호스트 X11 자동 연결 |
| [`former_slam_run.sh`](former_slam_run.sh) | SLAM 지도 작성 백그라운드 구동 래퍼 | `slam --daemon` 자동 적용 |
| [`former_slam_rviz.sh`](former_slam_rviz.sh) | SLAM RViz 시각화 모니터링 | SLAM 전용 RViz 뷰 적용 |
| [`run_former_docker.sh`](run_former_docker.sh) | Former0045용 RoboClaw 에이전트 구동 런처 | 카메라 토픽: `/former0045/camera/color/image_raw` |
| [`run_nav_stable.sh`](run_nav_stable.sh) | 실기용 Navigation2 기동 (권장) | `use_composition:=False`, cmd_vel 리맵, 스캔 재스탬프 핫픽스 적용 |
| [`restart_nav.sh`](restart_nav.sh) | nav2 컨테이너만 지우고 `run_nav_stable.sh` 로 재기동 | 베이스/센서 컨테이너는 유지 |

## 부팅 시 자동 시작 (nav2, RoboClaw 에이전트)

former0045 도 former0047 과 같이 부팅 시 `former0045_nav` 컨테이너와 RoboClaw 에이전트
컨테이너(`robo_claw_container`)를 자동 기동합니다. unit 과 설치 스크립트는 두 로봇이
공유하는 [`../autostart/`](../autostart/install_autostart.sh)에 있습니다.

| 대상 | unit | 실행 | 재기동 | 로그 |
| --- | --- | --- | --- | --- |
| nav2 | `former_nav_bringup.service` | `run_nav_stable.sh` (`RC_FOREGROUND=1`) | `Restart=on-failure`, 15초 | `journalctl -u former_nav_bringup` |
| 에이전트 | `roboclaw_bringup.service` | `run_roboclaw.sh` (pty + `rclaw launch former kon45 --docker`) | `Restart=on-failure`, 20초 | `journalctl -u roboclaw_bringup` |

```bash
cd /home/former/konjiam/former/autostart

# 설치 (unit 등록 + 즉시 기동, root 권한 필요). 대상: nav, claw, all
sudo ./install_autostart.sh former0045 all

# 상태와 최근 로그
sudo ./install_autostart.sh former0045 status
journalctl -u former_nav_bringup -f
journalctl -u roboclaw_bringup -f

# 자동 시작만 정지 / 해제
sudo ./install_autostart.sh former0045 stop
sudo ./install_autostart.sh former0045 disable
```

- 기본 맵은 `map_konjiam_L_107-1.yaml` 이며, 설치 전에 `RC_MAP_FILE=<맵 파일>` 로 바꿀 수 있습니다.
- nav 를 자동 시작해도 이동 명령을 스스로 내리지는 않습니다. 부팅 직후부터
  `/base_controller/cmd_vel_unstamped` 로 주행 명령을 받을 수 있는 상태가 되므로,
  실기 시험 중에는 nav unit 을 `stop` 으로 내려 두는 편이 안전합니다.

## 경로 설정 및 자동 감지 안내

- **설정 파일 (`.env`)**: `former0045/.env` -> `former/.env` -> 저장소 루트 `.env` 순서로 자동 탐색 및 로드합니다.
- **파라미터 파일 (`params/`)**: 로컬 `former0045/params`가 있으면 우선 마운트하며, 없으면 공유 `former/params` 디렉토리를 자동으로 마운트합니다.
- **맵 파일 (`maps/`)**: 호스트 실기기 디렉토리(`/home/former/workspace/nav2/maps`), 로컬 `maps/`, 공유 `former/maps/` 순서로 탐색하여 자동으로 `/ws/maps`에 마운트합니다.
- **SLAM 설정 (`config/`)**: 로컬 `former0045/config` 또는 공유 `former/config`를 자동으로 `/ws/config`에 마운트합니다.
- **CycloneDDS 설정**: `/home/former/cyclonedds_conf` 디렉토리가 실제로 존재할 때만 자동 마운트합니다.

## 사용법

```bash
# 1. Navigation2 실행 (데몬 모드)
./former/former0045/former_nav2_run.sh
# 특정 맵 지정 실행
./former/former0045/former_nav2_run.sh -m w2_5f.yaml
# 좁은 통로 최적화 파라미터 적용 시
./former/former0045/former_nav2_run.sh params_file:=/ws/params/nav2_params_narrow.yaml

# 2. Nav2 RViz 모니터링
./former/former0045/former_nav2_rviz.sh

# 3. SLAM 지도 작성 실행 (데몬 모드)
./former/former0045/former_slam_run.sh
# 넓은 영역(30m x 30m) 최적화 SLAM 설정 적용 시
./former/former0045/former_slam_run.sh slam_params_file:=/ws/config/mapper_params_wide.yaml

# 4. SLAM RViz 모니터링
./former/former0045/former_slam_rviz.sh

# 5. RoboClaw 에이전트 실행
./former/former0045/run_former_docker.sh
```
