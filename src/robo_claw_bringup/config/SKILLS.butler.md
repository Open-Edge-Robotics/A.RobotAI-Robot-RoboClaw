# 버틀러 로봇 전용 스킬 가이드 (SKILLS.md)

이 가이드는 **버틀러(Butler)** 로봇에만 해당하는 규칙입니다. Butler는 모바일 베이스 + **Piper 7-DOF
매니퓰레이터** + gripper를 갖춘 로봇입니다. 일반적인 주행·관측·지도 스킬 선택은 시스템 프롬프트의
`[도구 선택 전략]`을 따르고, **팔 조작은 스크립트 기반**으로 처리합니다.

---

## 로봇 특성 & 하드웨어

- 전면 RealSense(`/front_cam/...`)·라이다(`/aslam/lidar/scan`)·IMU 탑재.
- 위치 추정: AMCL/Lidar(`22_butler_v01_localization.sh`). 주행은 `/cmd_vel`, 오도메트리는 `/odom`.
- **주행 전 stow/mode 전환 단계 없음**: Stretch3 같은 주행 전 안전 자세·모드 전환 규칙은 없습니다. 팔 안전 자세가 확보되어 있으면 바로 주행 스킬(`navigate_to`, `move_relative` 등)을 사용합니다.
- **팔 조작은 스크립트 기반**: 드라이버(`50_piper_node.sh`)와 MoveIt(`51_start_moveit.sh`)가 먼저 기동돼야 관절 제어·모션 계획이 가능합니다.

---

## 정리/치우기 명령 처리 (핵심 규칙)

1. **"정리해줘", "정리", "쓰레기 주워줘", "바닥 치워줘", "바닥 정리해줘", "주워줘"** 등 모든 정리·수거·치우기 요청은
   **오직 `run_butler_script(script_name="56_organizer_req.sh")`만 단독 호출**하세요.
2. **다른 스크립트는 사전 구동/체이닝 금지**: 표의 다른 스크립트(`50`~`55`, `57`)는 시스템 구성 참고용입니다.
   정리 요청 처리 시 이를 선행 조건으로 실행하지 마세요. 곧바로 `56_organizer_req.sh`만 실행합니다.

---

## 버틀러 전용 스크립트 목록

| 스크립트 | 용도 | 실행 시점 |
| :--- | :--- | :--- |
| `01_start_system.sh` | 시스템 전체 기동 | 최초 가동/부팅 완료 시 |
| `02_stop_system.sh` | 시스템 안전 종료 | 작업 후 전원 차단/대기 시 |
| `03_restart_system.sh` | 시스템 전체 재시작 | 프로세스 정지 등 재시작 필요 시 |
| `04_attach_tmux.sh` | tmux 세션 접속 | 백그라운드 로그·실행 화면 모니터링 |
| `90_chk_version.sh` | 시스템·펌웨어 버전 확인 | 형상/펌웨어 무결성 점검 |
| `21_butler_v01_robot.sh` | 모바일 베이스 드라이버 기동 | 주행 기동 시 |
| `22_butler_v01_localization.sh` | 위치 추정(AMCL/Lidar) 기동 | 맵 기반 주행 전 |
| `31_rv_mapbuilding.sh` | 맵 빌딩(SLAM/PGO) 활성화 | 새 지역 매핑 시 |
| `24_mapsave.sh` | 지도 저장 및 경로 이동 | SLAM 완료 후 지도 저장·정리 |
| `25_mileage_chk.sh` | 주행 거리 누적 체크 | 누적 이동거리 측정 시 |
| `26_mileage_reset.sh` | 주행 거리 리셋 | 새 실험/교대 전 초기화 |
| `56_organizer_req.sh` | **정리/수거/치우기 실행** | 정리·치우기 요청 시 (유일 호출) |

## 참고용 (에이전트 직접 실행 금지)

`50_piper_node.sh`, `51_start_moveit.sh`, `52_recognizer_start.sh`, `53_recognizer_req.sh`,
`54_organizer_start.sh`, `55_organizer_stop.sh`, `57_test_piper_node.sh`

---

### 요약
- 일반 주행·관측·지도·인식 스킬은 시스템 프롬프트 기준.
- Butler 고유 기억할 점: **팔 조작은 스크립트 기반**, **정리 요청은 `56_organizer_req.sh`만 단독 실행**,
  **주행 전 stow 불필요**, **Piper 드라이버·MoveIt은 직접 실행 금지**.
