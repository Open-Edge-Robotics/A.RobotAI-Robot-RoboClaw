# DDS 도메인 공유로 인한 ROS 그래프 혼선 (미해결)

이 문서는 former0045·former0047·NERO 팔 PC가 같은 `ROS_DOMAIN_ID` 를 쓰면서 생기는 그래프 혼선을
정리한 미해결 이슈 노트입니다. 2026-10-29 실기 점검에서 확인한 사실과, 고칠 때 손대야 하는 지점,
적용 후 검증 항목까지만 적었습니다. 아직 조치는 적용하지 않았습니다.

## 1. 현재 상태

| 구성 | 주소 | DDS 링크 | RMW | ROS 도메인 |
| --- | --- | --- | --- | --- |
| former0045 (베이스·nav·에이전트) | 192.168.50.142 (LAN) / 192.168.10.10 (LiDAR) / 192.168.127.200 (NERO 직결) | `cyclonedds.xml` 이 eth0 에 바인딩 | `rmw_cyclonedds_cpp` | 4 |
| former0047 (베이스·nav·에이전트) | 192.168.50.165 (LAN) / 192.168.10.10 (LiDAR) | `cyclonedds.xml` 이 eth0 에 바인딩 | `rmw_cyclonedds_cpp` | 4 |
| NERO 팔 PC (wrist 카메라) | 192.168.127.10 (0045 직결) / 192.168.50.202 (LAN) | 자체 설정 | `rmw_cyclonedds_cpp` | 4 (ROS 2 jazzy) |

두 로봇은 같은 `192.168.50.0/24` LAN 에 있고 도메인이 같아서, 서로의 노드와 토픽이 같은 그래프에
나타납니다. DDS 인터페이스를 바꾸는 것으로는 해결되지 않습니다. 같은 도메인에 참가자(participant)가
있으면 SPDP/CIMP 로 서로 보이는 것이 DDS 동작이기 때문입니다.

## 2. 확인된 증상 (2026-10-29 실측)

| 증상 | 관찰한 내용 |
| --- | --- |
| 상대 로봇 토픽 노출 | former0047 의 `former_robot` 컨테이너에서 `ros2 topic list` 에 `/former0045/camera/color/image_raw`, `/former0045/camera/depth/color/points` 등이 보였습니다 |
| 노드 이름 중복 | former0047 의 `ros2 node list` 가 `nodes in the graph that share an exact name` 경고와 함께 `/robo_claw_grpc_node` 를 5개 나열했습니다 |
| 다른 배포판 노드 혼재 | former0045 의 eth1 링크 너머 NERO 팔 PC(jazzy, 도메인 4)가 같은 도메인에 있었고, 이 상태에서 humble 컨테이너의 rclpy 그래프 질의가 `empty node name returned by the RMW layer` 로 실패해 controller spawner 가 전부 죽었습니다(`docs/NAVI_ISSUE.md` 3.9) |
| 진단 도구 오염 | 어느 로봇에서 실행했는지와 무관하게 `ros2 node list`·`ros2 topic list` 에 상대 로봇 항목이 섞여, 장애 원인 판별에 시간이 걸렸습니다 |

## 3. 위험

| 위험 | 내용 |
| --- | --- |
| 제어 명령 오배송 | 서비스·액션은 이름으로 상대를 고릅니다. `/robo_claw_grpc_node` 처럼 이름이 겹치면 `stop`·`navigate_to` 요청이 다른 로봇으로 갈 수 있습니다. 두 로봇을 동시에 켠 상태에서 실제로 확인된 위험입니다 |
| 센서·TF 혼선 | `/scan`, `/tf`, `/map`, `/amcl_pose` 가 같은 그래프에 있으므로 잘못 구독하면 다른 로봇의 스캔·지도로 위치를 추정할 수 있습니다 |
| 실험 결과 오염 | 두 로봇을 함께 쓰는 실험(4장소 순환 등)에서 상대 로봇 토픽이 섞이면 결과 해석이 어긋납니다 |
| 기동 실패 | 같은 도메인에 다른 배포판(jazzy) 노드가 들어오면 rclpy 기반 도구가 죽습니다(`docs/NAVI_ISSUE.md` 3.9) |

## 4. 선택지

| 방안 | 내용 | 장점 | 비용·한계 |
| --- | --- | --- | --- |
| A. 로봇별 도메인 분리 (권장) | former0045=45, former0047=47 처럼 로봇마다 `ROS_DOMAIN_ID` 를 다르게 | 그래프가 완전히 분리되고 부수 효과가 없습니다 | 단위 장치·외부 도구(rosbridge, 원격 RViz, 대시보드, gRPC 클라이언트)도 같은 도메인을 써야 합니다. 적용 지점이 넓습니다 |
| B. 인터페이스·유니캐스트 피어 제한 | `cyclonedds.xml` 의 `<Peers>` 를 명시하고 멀티캐스트를 끄기 | 설정 변경이 작습니다 | 같은 도메인이면 참가자 발견 자체는 계속 되므로 근본 해결이 아닙니다 |
| C. NERO 카메라 전용 도메인 | NERO 팔 스택을 별도 도메인(예: 104)으로 옮기고 0045 에 `domain_bridge` 를 두어 카메라 토픽만 중계 | 팔 스택과 로봇 도메인을 동시에 분리합니다 | NERO PC 작업이 필요하고 중계 컨테이너를 새로 운영해야 합니다 |
| D. DDS 파티션 | CycloneDDS `<Partitions>` | 설정만으로 분리 | ROS 2 는 토픽 파티션을 쓰지 않으므로 그대로는 동작하지 않습니다 |

A 를 먼저 적용하고, NERO 팔 카메라를 former0045 로 받을 필요가 생기면 C 를 함께 설계하는 순서를
권합니다.

## 5. 변경 대상 (조사 결과)

| 위치 | 현재 | 비고 |
| --- | --- | --- |
| `former/autostart/former_bringup.service.in` | `Environment=ROS_DOMAIN_ID=4` | 베이스 컨테이너 |
| `former/autostart/former_nav_bringup.service.in` | `Environment=ROS_DOMAIN_ID=4` | nav 컨테이너 |
| `former/autostart/former_bringup_watchdog.sh`, `wait_for_base.sh` | 기본값 4 (`ROS_DOMAIN_ID` 없을 때) | unit 환경변수를 따르도록 이미 되어 있습니다 |
| `former/former0045/run_former_env.sh`, `former/former0047/run_former_env.sh` | 기본값 4, `--domain-id` 옵션 | 수동 기동 경로 |
| `robo_claw_cli/launcher/docker.go`, `local.go` | `EnvMap` → 호스트 환경변수 → `activeConfig.RosDomainId` 순으로 결정 | 에이전트 컨테이너 주입 경로 |
| `config-schema/runtime-config.yaml`, `config-schema/generated/CONFIG_REFERENCE.md`, `robo_claw_cli/contract/runtime_config_generated.go` | `ros.domain_id` (기본 0) | contract 소유 필드라 저장소에서 직접 고치지 않습니다 |
| rosbridge, 원격 RViz sidecar, 대시보드, gRPC 클라이언트 | 도메인 4 전제 | 로봇별 도메인을 알도록 배포 설정을 같이 바꿔야 합니다 |
| `docs/CONFIGURATION_GUIDE.md` | `ROS_DOMAIN_ID` 관련 서술 | 문서도 함께 갱신 대상입니다 |

## 6. 적용 절차 (초안)

1. 도메인 번호를 표로 확정합니다(예: former0045=45, former0047=47, NERO 팔=104).
2. unit 템플릿의 `ROS_DOMAIN_ID` 를 `__DOMAIN_ID__` 로 바꾸고 `install_autostart.sh` 에 로봇별
   매핑을 추가합니다.
3. `run_former_env.sh` 의 기본값과 `--domain-id` 사용처를 같은 값으로 맞춥니다.
4. 에이전트 실행 환경(`kon45`/`kon47` env)의 `ROS_DOMAIN_ID` 를 갱신합니다. contract 필드
   (`ros.domain_id`)를 쓰는 경로라면 공통 저장소에서 값을 올린 뒤 `rclaw contract update` 로
   반영합니다.
5. rosbridge, 원격 RViz, 대시보드, gRPC 클라이언트의 접속 도메인을 로봇별로 지정합니다.
6. 아래 체크리스트로 검증한 뒤 재부팅 1회를 더 확인합니다.

## 7. 검증 체크리스트

| 항목 | 기대 결과 |
| --- | --- |
| 그래프 분리 | 각 로봇에서 `ros2 node list`·`ros2 topic list` 에 상대 로봇 항목이 없습니다 |
| 노드 이름 중복 | `nodes in the graph that share an exact name` 경고가 사라집니다 |
| 베이스 | `ros2 control list_controllers` 에서 `base_controller` 가 `active` 입니다 |
| 위치추정 | `/base_controller/odom` publisher 1, `odom→base_link`·`map→base_link` TF 가 정상이고 `/amcl_pose` 가 발행됩니다 |
| nav 로그 | `Message Filter dropping`·`process has died` 0건입니다 |
| 에이전트 | `robo_claw_discovery_node` 가 의도한 peer 만 보고, gRPC·대시보드가 해당 로봇에 붙습니다 |
| 재부팅 | 두 로봇 재부팅 후 base·nav·agent unit 이 `active`, `NRestarts 0` 입니다 |

## 8. 참고

- `docs/NAVI_ISSUE.md` 3.9절 — 같은 도메인에 다른 배포판 노드가 섞였을 때 베이스가 통째로 비는 사례
- `former/FORMER_GUIDE.md` 10.2절 — 베이스 준비와 DDS 설정, 인터페이스를 바꿀 때의 주의사항
- `former/autostart/cyclonedds.xml` — 로봇 공통 DDS 설정(eth0)
