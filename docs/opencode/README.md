# RoboClaw 프로젝트 분석 문서

이 문서는 `docs/docs.readme.md` 가이드에 맞춰 현재 `robo-claw` 저장소를 정적 분석한 결과를 정리한 문서다.

관련 문서:

- `docs/opencode/architecture.md`: 시스템/배포 아키텍처 정리
- `docs/opencode/flowchart.md`: 주요 실행 플로우차트
- `docs/opencode/functional-architecture.md`: Functional Architecture 정리

## 1. 프로젝트 한 줄 요약

RoboClaw는 ROS 2 위에서 동작하는 로봇용 AI 에이전트 런타임으로, 자연어 명령을 받아 LLM이 스킬 체인을 계획하고, ROS 액션/서비스/토픽 및 외부 메신저 채널을 통해 실제 로봇 또는 시뮬레이터를 제어하는 구조다.

## 2. 현재 저장소 기준 핵심 구성

| 패키지 | 구현 언어 | 현재 역할 |
| --- | --- | --- |
| `robo_claw_msgs` | ROS IDL + Proto | 액션, 서비스, 메시지, gRPC 인터페이스 명세서 정의 |
| `robo_claw_core` | C++ | 하드웨어 인터페이스, 센서 등록, 50Hz 제어 루프 |
| `robo_claw_agent` | Python | LLM 브리지, 메모리, 스킬 로딩, 실행 오케스트레이션 |
| `robo_claw_channel` | Python | HTTP, gRPC, Discord, Slack, Telegram 채널 통합 |
| `robo_claw_discovery` | Python | ROS 그래프와 다른 에이전트 탐색 |
| `robo_claw_bringup` | Python | 실제 로봇/시뮬레이션 런치와 설정 조합 |

## 3. 진입점과 실행 방식

프로젝트의 실제 운영 진입점은 `rc.sh`다.

- `./rc.sh setup-deps`: `uv` 기반 가상환경 및 Python 의존성 구성
- `./rc.sh proto`: `messenger.proto`를 Python gRPC 코드로 생성
- `./rc.sh build`: `colcon build --symlink-install` 수행
- `./rc.sh run`: 실제 로봇용 `robo_claw.launch.py` 실행
- `./rc.sh sim`: 시뮬레이션용 `robo_claw_sim.launch.py` 실행
- `./rc.sh test`: Python 테스트 실행

운영 흐름은 크게 두 갈래다.

1. 실제 로봇 런치: `robo_claw_core` + `robo_claw_agent` + `robo_claw_discovery` + 선택적 `robo_claw_channel`
2. 시뮬레이션 런치: Gazebo/Nav2 계층 + `robo_claw_agent` + `robo_claw_discovery` + 선택적 `robo_claw_channel`

## 4. 핵심 동작 방식

### 4.1 자연어 명령 처리

사용자 명령은 다음 경로 중 하나로 들어온다.

- HTTP `POST /task`
- gRPC `SendCommand` 또는 `ChatStream`
- Discord/Slack/Telegram 메시지
- ROS 액션 직접 호출 `/<agent>/execute_task`

그 다음의 공통 흐름은 아래와 같다.

1. `robo_claw_channel`이 명령을 `ExecuteTask` 액션으로 `robo_claw_agent`에 전달한다.
2. `robo_claw_agent`는 최근 대화 히스토리, 선택적 RAG 지식, 로봇 상태 요약을 수집한다.
3. 시스템 프롬프트에 현재 로드된 스킬 목록을 동적으로 삽입한다.
4. LLM은 반드시 JSON 형태로 다음 중 하나를 반환한다.
   - 단일 스킬 실행
   - 다중 스킬 체이닝
   - 최종 자연어 응답
5. `SkillManager`가 이름 기반으로 스킬을 실행한다.
6. 실패 시 에이전트는 실패 내용을 다시 LLM에 넣어 재플래닝을 최대 5라운드까지 시도한다.
7. 성공한 결과에 `file_path`가 있으면 채널 노드를 통해 사용자에게 파일을 자동 전송한다.
8. 모든 작업이 끝나면 결과를 액션 응답으로 반환하고, RAG가 켜져 있으면 성공 경험을 지식으로 저장한다.

### 4.2 하드웨어/시뮬레이션 제어

`robo_claw_core`는 별도 C++ 노드로 동작한다.

- `/joint_states`를 구독해 내부 조인트 상태를 갱신한다.
- `cmd_vel` 퍼블리셔를 통해 베이스 제어 명령을 내보낸다.
- `HardwareInterface::write()`가 50Hz 루프에서 명령을 발행한다.
- 비상 정지 시 속도를 0으로 만들고 에러 콜백을 통해 상태를 올린다.

즉, 에이전트가 직접 하드웨어를 건드리기보다, 스킬과 ROS 인터페이스를 통해 제어 계층을 건드리는 구조다.

## 5. 패키지별 상세 분석

### 5.1 `robo_claw_msgs`

이 패키지는 시스템 전체의 인터페이스 명세서 집합이다.

- `ExecuteTask.action`: 자연어 명령 기반 상위 태스크 실행 API
- `ExecuteSkill.srv`: 특정 스킬 직접 실행 API
- `QueryState.srv`: 에이전트/코어 상태 조회 API
- `SendMessage.srv`: 채널 노드를 통한 메시지/파일 전송 API
- `AgentStatus.msg`: 에이전트 상태 코드와 메시지
- `SkillResult.msg`: 개별 스킬 실행 결과
- `proto/messenger.proto`: 내부망용 gRPC 메신저 인터페이스 명세서

특징은 ROS 인터페이스와 gRPC 인터페이스가 같이 존재한다는 점이다. 이 덕분에 ROS 네이티브 제어와 외부 시스템 연동을 동시에 지원한다.

### 5.2 `robo_claw_core`

실시간 제어와 하드웨어 추상화 레이어다.

- `core_node.cpp`: 코어 노드 생성, 센서/하드웨어 초기화, 50Hz 루프 관리
- `hardware_interface.*`: 조인트 상태, 속도 제한, 비상 정지, `cmd_vel` 발행 담당
- `sensor_manager.*`: 등록된 센서 목록 및 상태 JSON 생성 담당

현재 구현상 특징:

- 실제 하드웨어 드라이버 전체를 직접 구현하기보다는 ROS 토픽 브리지 방식에 가깝다.
- `joint_names`, `sensor_names`, `sensor_topics.*`, `cmd_vel_topic` 같은 YAML 파라미터 중심으로 구성된다.
- Stretch 3용 기본 설정은 `robo_claw_bringup/config/stretch3_config.yaml`에 있다.

### 5.3 `robo_claw_agent`

프로젝트의 지능 계층 핵심이다.

- `agent_node.py`: 전체 태스크 오케스트레이션
- `llm_bridge.py`: Ollama, OpenAI, Azure OpenAI, Anthropic 공통 래퍼
- `skill_manager.py`: 플러그인형 스킬 로더 및 실행기
- `memory_manager.py`: 단기/장기 메모리, RAG 지식, 시맨틱 맵 관리
- `skills/*.py`: 도메인별 실행 가능한 도구 모음

중요한 설계 포인트:

- 프롬프트에 실제 로드된 스킬 목록을 삽입한다.
- LLM 출력 형식을 JSON으로 강하게 제한한다.
- 스킬 결과를 다시 LLM 컨텍스트에 투입해 멀티 스텝 실행을 이어간다.
- `file_path` 반환 스킬은 자동 전송 경로를 갖는다.

### 5.4 `robo_claw_channel`

외부 인터페이스 집약 계층이다.

- HTTP 서버는 표준 라이브러리 `HTTPServer`로 직접 구현되어 있다.
- gRPC 서버는 `messenger.proto`를 기반으로 동작한다.
- Telegram, Slack, Discord를 모두 동일 추상 인터페이스 `BaseMessengerChannel`로 묶는다.
- 에이전트가 사용자에게 역방향 메시지를 보내기 위해 `SendMessage` 서비스 서버도 제공한다.

즉, 이 노드는 단순 입력 게이트웨이가 아니라, 사용자 응답과 결과물 파일 배포까지 담당하는 I/O 허브다.

### 5.5 `robo_claw_discovery`

ROS CLI를 사용해 토픽, 서비스, 액션, 노드 목록을 주기적으로 수집하고 JSON으로 퍼블리시한다.

현재 역할:

- ROS 그래프 메타데이터 수집
- `robo_claw_agent_node` 패턴을 가진 노드를 다른 에이전트로 식별
- `~/robot_capabilities` 토픽에 JSON 발행

현재 저장소 기준으로는 "탐색 결과를 생성하는 쪽" 구현은 있으나, 에이전트가 이를 적극 구독해 의사결정에 반영하는 코드 연결은 아직 보이지 않는다.

### 5.6 `robo_claw_bringup`

배포 조립 계층이다.

- 실제 로봇용 런치와 시뮬레이션용 런치를 분리한다.
- 로봇 설정 파일에서 카메라 토픽을 자동 추론한다.
- ROS 배포판에 따라 Jazzy는 Gazebo Harmonic/TurtleBot4, Humble은 Gazebo Classic/TurtleBot3 기반으로 분기한다.

이 부분은 "로봇 종류"와 "시뮬레이션 엔진 차이"를 런치 파일 레벨에서 흡수하는 역할을 한다.

## 6. 스킬 체계 분석

현재 기본 로드 스킬 모듈은 `agent.yaml` 기준 9개다.

- `navigation_skill`
- `manipulation_skill`
- `perception_skill`
- `system_skill`
- `vision_skill`
- `map_skill`
- `hri_skill`
- `cooperate_skill`
- `file_skill`

대표 스킬 분류는 아래와 같다.

| 카테고리 | 주요 스킬 | 현재 상태 |
| --- | --- | --- |
| Navigation | `navigate_to`, `rotate`, `stop` | Nav2 액션 직접 호출까지 구현 |
| Perception | `detect_object`, `get_distance` | 거리 측정은 실연동, 객체 탐지는 최소 구현 |
| Vision | `analyze_scene`, `annotate_image` | VLM 기반 장면 분석 및 이미지 마킹 |
| Map | `analyze_map`, `get_map_visual`, `annotate_map` | 맵 이미지 생성, VLM 분석, 좌표 마킹 |
| System | `get_status`, `ros_command`, `emergency_stop` | 상태 요약, 제한된 ROS CLI, 안전 정지 |
| HRI | `say`, `listen`, `send_message` | 음성/메신저 인터페이스, 일부 스텁 포함 |
| Cooperation | `delegate_task` | 다른 에이전트로 태스크 위임 |
| File | `analyze_stored_file`, `list_files`, `delete_file` | 저장 파일 분석/관리 |
| Manipulation | `grasp`, `place`, `arm_pose` | 인터페이스 중심, 실제 조작은 TODO 수준 |

## 7. 독특한 아이디어와 기술 포인트

### 7.1 스킬 결과의 자동 파일 전송

이 프로젝트는 `file_path`를 반환하는 스킬을 단순 결과값으로 끝내지 않는다.

- `analyze_scene`
- `annotate_image`
- `get_map_visual`
- `annotate_map`

위 같은 스킬은 결과에 `file_path`를 포함하고, 에이전트가 이를 감지해 자동으로 채널 노드로 전송한다. 즉 "분석 -> 파일 생성 -> 사용자 전달"이 하나의 런타임 규약으로 묶여 있다.

### 7.2 맵/비전 결과를 시맨틱 메모리로 환원

`MemoryManager`는 단순 대화 히스토리만 저장하지 않는다.

- RAG용 지식 벡터 저장
- 객체/장소 이름 기반 시맨틱 좌표 저장
- 별칭까지 같이 저장해 `1번`, `Place 1`, `Location 1` 같은 이름으로 재참조

이 설계 덕분에 한 번 인식한 장소를 이후 자연어 명령에서 재사용할 수 있다.

### 7.3 JSON 강제형 LLM 오케스트레이션

에이전트는 자유 대화형 응답보다 기계가 다시 해석하기 쉬운 JSON 계획을 우선한다. 이 접근은 로봇 제어에서 특히 중요하다.

- 스킬 이름이 명시됨
- 파라미터 구조가 분리됨
- 실패 시 재플래닝 루프에 그대로 재투입 가능함

### 7.4 폐쇄망 고려가 반영된 이중 인터페이스

외부 메신저뿐 아니라 gRPC 메신저를 따로 둔 점이 특징이다. 인터넷이 없는 환경에서도 내부망 기반 제어를 유지하려는 설계 의도가 분명하다.

## 8. 변경점 리스트

코드 주석, Phase 표기, 현재 구현 차이를 기준으로 추적한 주요 변경 방향은 아래와 같다.

1. LLM 브리지에 멀티모달 이미지 분석 기능이 추가됐다.
2. 에이전트에 장기 기억과 RAG 기반 경험 재활용 구조가 통합됐다.
3. 시맨틱 맵 기능이 들어가면서 장소/객체 이름으로 목적지를 찾는 방식이 추가됐다.
4. 맵 이미지 분석과 이미지 마킹 등 VLM 기반 시각 도구가 강화됐다.
5. `file_path` 기반 자동 결과 전송 규약이 추가됐다.
6. `ros_command`에 금지 키워드 기반 보안 필터가 들어갔다.
7. `emergency_stop` 스킬이 추가되며 안전 가드레일이 강화됐다.
8. Discovery 노드가 다른 RoboClaw 에이전트를 식별하도록 확장됐다.
9. 런치 레벨에서 Jazzy/Humble 이중 시뮬레이션 경로가 정리됐다.

## 9. 인터페이스 명세서 요약

### 9.1 `ExecuteTask.action`

상위 태스크 실행 API다.

- 입력: 자연어 명령, 추가 컨텍스트 JSON, 타임아웃
- 출력: 성공 여부, 최종 메시지, 스킬별 결과 목록
- 피드백: 현재 단계, 진행률, 에이전트 상태

### 9.2 `ExecuteSkill.srv`

특정 스킬을 직접 호출하는 API다. 테스트나 외부 자동화에 적합하다.

### 9.3 `QueryState.srv`

에이전트 또는 코어 노드 상태를 JSON 문자열로 돌려주는 상태 조회 API다.

### 9.4 `SendMessage.srv`

에이전트가 채널 노드에 "사용자에게 이 텍스트/파일을 보내라"고 요청하는 역방향 전달 API다.

### 9.5 `messenger.proto`

`src/robo_claw_msgs/proto/messenger.proto`는 내부 메신저 인터페이스 명세서다.

- `ChatStream`: 양방향 스트리밍 대화
- `SendCommand`: 단발성 명령 요청/응답
- `UploadFile`: 파일 업로드
- `DownloadFile`: 파일 다운로드

즉, ROS 밖의 외부 클라이언트가 RoboClaw를 제어하는 별도 표준 채널 역할을 한다.

## 10. 현재 상태에서 보이는 강점

1. ROS 2 네이티브 구조와 LLM 오케스트레이션이 분리되어 있다.
2. 실제 로봇과 시뮬레이션 런치 구성이 모두 준비돼 있다.
3. 외부 채널, 내부망 채널, ROS API가 모두 존재한다.
4. 지도/이미지 기반 시각 결과를 사용자에게 자동 전달하는 UX가 강하다.
5. 메모리가 단순 로그가 아니라 RAG와 시맨틱 네비게이션으로 이어진다.

## 11. 현재 상태에서 확인된 구현 갭

정적 분석 기준으로 아래는 문서화해 둘 만한 현재 상태다.

1. `robo_claw_discovery`는 capability를 퍼블리시하지만, 이를 `robo_claw_agent`가 직접 소비하는 연결은 아직 확인되지 않았다.
2. `manipulation_skill`, `listen`, `say` 일부 경로는 실제 하드웨어/엔진 연동보다 스텁 또는 TODO 성격이 강하다.
3. `SensorManager`는 센서를 등록하지만 `active` 상태를 실제 토픽 구독으로 갱신하지는 않는다.
4. `AnalyzeSceneSkill`의 시맨틱 좌표 저장은 현재 로봇 위치를 스텁 값으로 사용한다.
5. 테스트 코드 일부는 현재 `MemoryManager` 구현과 어긋나 보인다. 예를 들어 `get_recent`, `clear_all`, SQLite 관련 메서드를 기대하지만 현재 구현에서 바로 보이지 않는다.

이 항목들은 프로젝트의 방향성 자체를 해치지는 않지만, "현재 완성된 부분"과 "앞으로 연결할 부분"을 구분하는 데 중요하다.

## 12. 결론

현재 RoboClaw는 "LLM이 자연어를 스킬 계획으로 변환하고, ROS 2와 다중 채널을 통해 실제 로봇/시뮬레이터를 제어하는 에이전트 런타임"으로 정의할 수 있다.

핵심 가치는 아래 세 가지다.

1. 자연어 기반 로봇 오케스트레이션
2. 시각/맵 결과물을 자동 전달하는 사용자 인터페이스
3. 경험 기억과 시맨틱 위치 기억을 결합한 누적형 에이전트 구조

반대로 현재 저장소는 일부 기능이 완전 제품 수준이라기보다, 강한 방향성이 잡힌 통합 프로토타입 + 확장 기반에 가깝다. 특히 조작, 디스커버리 활용, 메모리 테스트 정합성은 다음 정리 포인트로 보인다.
