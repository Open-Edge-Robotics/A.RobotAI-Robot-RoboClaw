# former0047 전용 실행 스크립트 가이드

이 폴더의 스크립트들은 former-0047 로봇 환경(Jetson, NVIDIA GPU, SLAM, Navigation2, RoboClaw Agent)에 특화된 Docker 런처입니다.

## 이 로봇의 특수 사항 및 자동 보정

1. **Jetson (aarch64) + NVIDIA GPU**
   - `nvidia-smi` 명령이 없어도 Tegra 및 Docker NVIDIA 런타임을 감지하여 `--runtime nvidia` 및 NVIDIA 디바이스 플래그를 자동으로 추가합니다.
   - OpenGL 하드웨어 가속을 활성화하여 RViz2 구동 시의 SIGBUS(Bus error) 크래시를 방지합니다.

2. **SSH X11 포워딩 환경**
   - SSH 세션(`DISPLAY=localhost:10.0` 등)에서 rviz 실행 시, GLX 하드웨어 가속이 가능한 로컬 X 서버 `:0`으로 자동 전환하고 `xhost` 접근 권한을 자동으로 설정합니다.
   - nav2/slam 등 백그라운드 구동은 기존 DISPLAY를 그대로 유지합니다.

3. **Docker 이미지 fontconfig 손상 자동 복구**
   - `former_docker:1.0` 이미지의 `fonts-urw-base35` 손상으로 남아 있는 0바이트 빈 `urw-*.conf` 파일들을 컨테이너 시작 시 자동 삭제하여 Fontconfig 에러를 제거합니다.

4. **자체 완결형(Self-contained) 경로 지원**
   - 상위 디렉토리 스크립트에 의존하지 않고 단독으로 실행 가능합니다.
   - 로컬 또는 상위 디렉토리의 `params/`, `config/`, `maps/` 및 `.env`를 자동으로 탐색하여 마운트합니다.

## 파일 구성

| 스크립트 | 설명 | 비고 |
| --- | --- | --- |
| [`run_former_env.sh`](run_former_env.sh) | Former0047 ROS 2 환경 통합 컨트롤러 | Jetson/OpenGL/Fontconfig 자동 보정 내장 |
| [`former_nav2_run.sh`](former_nav2_run.sh) | Navigation2 백그라운드 구동 래퍼 | `nav2 --daemon` 자동 적용 |
| [`former_nav2_rviz.sh`](former_nav2_rviz.sh) | Nav2 RViz 시각화 모니터링 | SSH 시 `:0` 로컬 디스플레이 자동 전환 |
| [`former_slam_run.sh`](former_slam_run.sh) | SLAM 지도 작성 백그라운드 구동 래퍼 | `slam --daemon` 자동 적용 |
| [`former_slam_rviz.sh`](former_slam_rviz.sh) | SLAM RViz 시각화 모니터링 | SSH 시 `:0` 로컬 디스플레이 자동 전환 |
| [`run_former_docker.sh`](run_former_docker.sh) | Former0047용 RoboClaw 에이전트 구동 런처 | 카메라 토픽: `/former0047/camera/color/image_raw` |
| [`run_nav_stable.sh`](run_nav_stable.sh) | 실기용 Navigation2 기동 (권장) | `use_composition:=False`, cmd_vel 리맵, 스캔 재스탬프 핫픽스 적용 |
| [`restart_nav.sh`](restart_nav.sh) | nav2 컨테이너만 지우고 `run_nav_stable.sh` 로 재기동 | 베이스/센서 컨테이너는 유지 |
| [`install_nav_autostart.sh`](install_nav_autostart.sh) | nav2 부팅 자동 시작(systemd) 설치/해제 | `former_nav_bringup.service` 를 `/etc/systemd/system` 에 등록 |
| [`former_nav_bringup.service.in`](former_nav_bringup.service.in) | 자동 시작 unit 템플릿 | 설치 스크립트가 경로/맵을 치환해 사용 |

## 사용법

```bash
# 1. Navigation2 실행 (데몬 모드)
./former/former0047/former_nav2_run.sh
# 특정 맵 지정 실행
./former/former0047/former_nav2_run.sh -m w2_5f.yaml
# 좁은 통로 최적화 파라미터 적용 시
./former/former0047/former_nav2_run.sh params_file:=/ws/params/nav2_params_narrow.yaml

# 2. Nav2 RViz 모니터링
./former/former0047/former_nav2_rviz.sh

# 3. SLAM 지도 작성 실행 (데몬 모드)
./former/former0047/former_slam_run.sh
# 넓은 영역(30m x 30m) 최적화 SLAM 설정 적용 시
./former/former0047/former_slam_run.sh slam_params_file:=/ws/config/mapper_params_wide.yaml

# 4. SLAM RViz 모니터링
./former/former0047/former_slam_rviz.sh

# 5. RoboClaw 에이전트 실행
./former/former0047/run_former_docker.sh
```

## 부팅 시 자동 시작 (nav2)

`former_bringup` / `realsense_bringup` 과 같은 방식으로, 부팅 시 `former0047_nav`
컨테이너를 자동 기동합니다. systemd unit 은 `run_nav_stable.sh` 를 전경 모드
(`RC_FOREGROUND=1`)로 실행하므로 launch 프로세스가 죽으면 `Restart=on-failure`
(15초 간격)로 다시 뜨고, 로그는 `journalctl -u former_nav_bringup` 으로 남습니다.

```bash
# 설치 (unit 등록 + 즉시 기동, root 권한 필요)
cd /home/former/konjiam/former/former0047
sudo ./install_nav_autostart.sh

# 상태와 최근 로그
sudo ./install_nav_autostart.sh status
journalctl -u former_nav_bringup -f

# 자동 시작만 정지 / 해제
sudo ./install_nav_autostart.sh stop
sudo ./install_nav_autostart.sh disable
```

- 기본 맵은 `map_konjiam_L_107-1.yaml` 이며, 설치 전에 `RC_MAP_FILE=<맵 파일>` 로 바꿀 수 있습니다.
- 초기 위치는 `../initpose.md` 를 읽어 매 기동마다 반영합니다(수동 실행과 동일).
- 설치(install) 시점에는 이미 떠 있는 nav 컨테이너가 정리되고 다시 기동됩니다.
- 자동 시작을 해도 이동 명령을 스스로 내리지는 않습니다. 다만 부팅 직후부터
  `/base_controller/cmd_vel_unstamped` 로 주행 명령을 받을 수 있는 상태가 되므로,
  실기 시험 중에는 `stop` 으로 내려 두는 편이 안전합니다.
