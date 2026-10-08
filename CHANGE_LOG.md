# RoboClaw 변경 이력 (CHANGE_LOG)

ROS 2 기반 로봇 에이전트 런타임 **RoboClaw**의 변경 이력. 최신 항목이 위에 온다.
각 항목은 커밋 히스토리와 실기 검증 리포트(`validation/talk/` 리포트)를 근거로 정리했다.

기간: **2026-02-24 ~ 2026-10-08**

## 2026-10-08 — CLOiD 개체별 소울 프로필 분리 (1호/2호)

- **배경**: 스킬 가이드에 이어 소울(개성)도 개체 상태를 반영해야 했다. 1호는 팔·상체 모션을 쓰는 정상 개체이고, 2호는 팔·상체 구동 오류 상태라 자기 소개와 말투에서 가능/불가능을 담담하게 전달할 필요가 있다.
- **조치**:
  - `src/robo_claw_bringup/config/ROBOT.cloid.1.md`, `ROBOT.cloid.2.md` 추가 — 공통 성격·가치관 위에 개체 상태를 반영한 개성·말투를 정의.
  - `robo_claw.launch.py`: 스킬 가이드/소울 기본값 해석을 `_resolve_config_file_default()` 로 통합. `robot_soul_file` 미지정 시 `ROBOT.<robot_config>.md` → `ROBOT.<robot_config>.1.md` 순서로 찾고 개체 번호 미지정 경고를 출력.
  - `test_cloid_robot_config.py`: 두 소울 문서 존재·차이 검증과 `robot_soul_file` 로 2호 소울을 선택하는 launch 배선 테스트 추가.
  - `docs/ROBOT_CONFIG.md`, `docs/CLOiD_GUIDE.md`, `.env.example`: 개체별 소울 선택 방법 문서화.
  - `scripts/check_prompt_tone.py`: 개체별 소울 파일(`ROBOT.*.md`)도 강제어 점검 대상에 포함.
- **검증**: `test_cloid_robot_config.py` 16건 통과, `robo_claw_bringup` colcon build(symlink-install) 성공, `scripts/check_prompt_tone.py` 합계 10/10 유지, Ruff 검사 통과.

## 2026-10-08 — CLOiD 개체별 스킬 가이드 분리 (1호/2호)

- **배경**: CLOiD 1호는 이동과 CLOi 등록 상체 모션을 모두 사용할 수 있지만, 2호는 팔·waist·neck 구동 오류로 상체를 움직이는 경로를 사용할 수 없다. 단일 `SKILLS.cloid.md` 문서가 두 개체 상태를 구분하지 못해 2호에서 상체 모션·조작·정리 표시 모션이 계획될 위험이 있었다.
- **조치**:
  - `src/robo_claw_bringup/config/SKILLS.cloid.1.md` 추가 — 1호 전용 가이드(이동 + CLOi 등록 상체 모션 사용 가능, 공통 CLOiD 제약 포함).
  - `src/robo_claw_bringup/config/SKILLS.cloid.2.md` 추가 — 2호 전용 가이드(상체 구동 오류, `execute_cloid_motion`·조작 스킬·정리 표시 모션 사용 불가, 주행·인지·조회·정지 전용, `cloid_cleanup_indicator_enabled=false` 안내).
  - `src/robo_claw_bringup/config/SKILLS.cloid.md` 제거.
  - `robo_claw.launch.py`: `SKILLS.<robot_config>.md` 가 없으면 `SKILLS.<robot_config>.1.md` 를 개체 기본값으로 사용하고, 개체 번호 미지정 경고를 출력하도록 개체 fallback 추가.
  - `test_cloid_robot_config.py`: 두 개체 문서 존재·차이 검증과 `skills_guide_file` 로 2호 문서를 선택하는 launch 배선 테스트를 추가.
  - `docs/CLOiD_GUIDE.md`, `.env.example`: 개체별 가이드 선택 방법과 2호 배포 설정을 문서화.
- **검증**: `src/robo_claw_bringup/tests/test_cloid_robot_config.py` 12건 통과, `robo_claw_bringup` colcon build(symlink-install) 성공, `scripts/check_prompt_tone.py` 합계 10/10 유지.

## 2026-10-08 — Discord 분할 전송 폭주 방지 및 get_status 경량화

- **증상**: 실기(CLOiD)에서 "주방에 갔다가 안방으로 와줘" 실행 중 `follow_waypoints` 실패 후 `get_status`를 거쳐 요약 라운드 폴백으로 넘어간 뒤, Discord로 base64 바이트코드 문자열이 끊임없이 분할 전송되는 현상 발생.
- **원인**:
  - `get_status` 스킬이 `include_image=True` 기본값으로 카메라 base64 이미지를 `status_details`에 주입함.
  - 요약 라운드 실패 시 결정론적 폴백 답변(`_compose_result_answer`)으로 풀린 대용량 텍스트가 Discord 분할 전송 루프(`_split_discord_message`)에서 상한 없이 1,900자 단위로 수백 개로 쪼개져 연속 전송됨.
- **조치**:
  - `channels.py`: `DISCORD_MAX_CHUNKS = 10` 상한을 두고 초과 시 생략 안내문(`... (메시지가 너무 길어 일부가 생략되었습니다)`)을 붙여 전송 폭주 차단.
  - `status.py`: `GetStatusSkill`의 `include_image` 기본값을 `False`로 변경하고 `input_schema`에 선언하여 텔레메트리 조회 본연의 목적에 맞게 경량화.
  - `answer.py` / `planner.py`: `MAX_FINAL_ANSWER_CHARS = 4000` 상한 및 `_clip_text_length` 헬퍼를 적용하여 최종 사용자 답변이 비정상적으로 비대해지는 것을 방어.
  - `scripts/system1_skill_catalog.py`로 `skill_catalog.json` 갱신.
- **검증**: `test_discord_channel.py` (5 passed), `test_final_answer_sanitize.py` (24 passed), `test_result_binary_omission.py` (5 passed), `test_new_skills.py::test_get_status_skill` (1 passed), `test_catalog_is_up_to_date` (1 passed), Ruff 및 format 검사 통과.

## 2026-10-08 — 단회 `tidy_home` 정리 작업

- `tidy_home`을 추가해 특정 기억 장소 또는 집 전체의 기억된 시맨틱/RAG 장소를 한 번씩 확인하도록 구성했습니다. 미기억 장소 탐험, 중복 방문, 자동 위임 재시도는 수행하지 않습니다.
- 명확히 폐기물로 확인되는 쓰레기·포장지만 처리 후보로 삼고, 컵·병·개인 소지품·위험물·액체처럼 모호한 항목은 이동/위임하지 않고 사용자 확인을 요청합니다. 로컬 집기/배치는 메모리에 `cleanup_disposal_target: true`와 비추정 3D `target_pose`가 지정된 허용 쓰레기통 장소에만 연결하며, 집기마다 `place`를 요구합니다. 동료 위임은 연결 및 매니퓰레이션 능력을 확인한 한 대에 대한 단발 요청입니다.
- CLOiD는 `manipulation_enabled=false`를 유지합니다. 관찰/위임 시도 후 승인된 `scan`(ID 22) 또는 `task ready pose`(ID 128) 중 하나를 상태 표시로 사용할 수 있습니다. 실제 조작 모션 `wipe_1`(ID 59)은 표시용 allowlist에서 제외하며, 표시 모션으로 정리 완료를 주장하지 않습니다.
- `tidy_home` 목적지/안전 정책, CLOiD allowlist, System 1 스킬 카탈로그·툴 케이스, 런타임 가이드를 갱신했습니다. 정리 workflow/위임 회귀 및 System 1 테스트 37건이 통과했고, ROS 의존 `test_skills_guide.py` 1건은 `rclpy` 부재로 건너뛰었습니다. 변경 파일 Ruff 검사와 카탈로그 일치 검증도 통과했습니다. colcon build는 `/opt/ros/jazzy`의 `ament_package` Python 모듈 부재로 실패해 ROS 통합 검증을 완료하지 못했습니다.

## 2026-10-08 — 자율 순찰 정리 작업의 동료 위임

- `autonomous_act` 기본 patrol 모드가 "집안 정리해줘" 요청에서 기억된 시맨틱 맵/RAG 장소를 순찰·관찰하고 정리 대상을 처리하도록 skill 설명과 시스템 프롬프트를 갱신했습니다.
- 정리 작업은 집기·배치 스킬이 있으면 로컬 에이전트가 직접 판단·실행합니다. 집기 능력이 없으면 현재 연결 상태와 조작 능력이 확인된 피어 한 대에만 단발 요청을 보냅니다. 같은 ROS 네트워크는 `delegate_task`, gRPC는 `call_peer_robot` 경로를 사용합니다.
- 제자리 관찰 모드에서도 검증된 정리 위임만 허용합니다. 적합한 동료가 없으면 관찰 결과를 보고하고, 요청 timeout/실패 후 자동 재요청하지 않습니다. 지속 대화형 `autonomous_cooperate`는 시작하지 않습니다.
- `test_autonomous_cleanup_delegation.py`의 위임 정책 테스트 11건과 변경 Python 파일의 Ruff 검사를 통과했습니다. ROS 2 환경이 없어 workspace build/ROS 통합 검증은 실행하지 못했습니다.

## 2026-10-08 — 폴백 답변·단계 요약의 이미지 base64 유입 차단

- **증상**: 실기에서 "카메라 이미지 보내고 분석해줘"를 실행하자 TaskDecomposer 2단계 LLM 요청이 2,672,002 토큰(한도 922,000)으로
  거부되어(`context_length_exceeded`) 작업이 중단됐다.
- **원인**: `capture_camera_image`(`capture_map`, `get_map_visual`도 같음) 결과의 `image_base64`가 결정론적 폴백 답변
  (`planner._compose_result_answer` → `answer.render_result_data`)에 그대로 풀려 들어갔다. TaskDecomposer는 이 단계 메시지를 길이
  제한 없이 다음 단계 "[이전 단계 요약]"에 넣었다. 같은 폴백 답변이 단일 명령의 최종 답변으로도 쓰여 사용자 메시지에도 섞일 수 있었다.
- **조치**:
  - `answer.flatten_to_text`: 키 이름에 `base64`가 포함된 항목(중첩·대소문자 무관)을 건너뛴다. 기준은 `utils._omit_base64_data`와 같다.
  - `task_planner._clip_step_message`: 이전 단계 요약의 단계 메시지를 `_STEP_RESULT_SUMMARY_CHARS`(800자)로 자른다.
- **검증**: 신규 회귀 테스트 6건(`test_result_binary_omission.py` 5, `test_task_planner.py` 1)은 수정 전 5건 실패, 수정 후 통과했다.
  `robo_claw_agent` 전체(로컬)는 548 passed이며, 실패·수집 오류 목록은 수정 전과 같은 ROS 의존 항목이다.
- **범위 밖**: 같은 로그에서 1단계 LLM이 3단계 스킬(`analyze_scene`)까지 미리 실행했고, 요약 라운드가 답변 대신 스킬 계획을 반환했다.

## 2026-10-08 — System 1 needs_motion 가드 (동작 요청 오응답 차단)

- **배경**: 툴 단위 실서버 평가에서 Laya가 "손 흔드는 모션 해줘", "손목 롤 조인트를 0.5 라디안으로 움직여"를
  `list_cloid_motions`(read)로 직접 실행했다. 동작 요청에 모션 목록만 답하는 오응답이다.
- **조치 (`agent_node/system1_router.py`)**:
  - 후보 스킬이 있을 때 `needs_motion`(noul, "이동하거나 몸·팔·손·머리를 움직이는 동작을 요청했는가?") 질문을 함께 보낸다.
  - `needs_motion` ≥ 0.5(`needs_motion`)이면 읽기 전용 스킬을 직접 실행하지 않고 LLM으로 넘긴다(`route.hint.blocked_by`).
    기억된 장소로의 `navigate_to`는 동작이 정상이므로 막지 않는다.
  - 인사 고정 응답은 별도 기준 `needs_motion_smalltalk`(0.9)를 쓴다. 실측에서 Laya가 평범한 인사("안녕" 0.65, "안녕하세요
    반가워요" 0.84)에도 높은 값을 주고, 제스처 인사("손 흔들어 인사해" 0.93, "고개 숙여 인사해" 0.96)에는 더 높은 값을 줬다.
    0.5를 공통으로 쓰면 "안녕"이 LLM으로 넘어가는 회귀가 생겨 분리했다.
  - 답이 없으면(다른 provider, 후보 스킬 없음) 차단하지 않는다.
- **테스트**: `test_system1_router.py`에 가드 테스트 8건(실측 값 회귀 포함), `tool_cases.jsonl`에 제스처 명령 3건을 추가했다.
  System 1 단위 테스트 81건 통과.
- **실서버 재측정** (`http://192.168.50.212:8000`, readonly):
  - 툴·복합 133건: Laya correct 119 / escalated 14 / **wrong 0**(가드 전 2) / 복합 명령 직접 실행 0. 규칙 라우터 correct 112 /
    direct_ok 5 / wrong 1.
  - 시드 32건: Laya correct 17 / wrong 0(가드 전과 같음). 라이브 테스트 14건 통과. 지연 p50 98ms.
- **남은 과제**: 0.9 기준은 시드 측정 몇 건에서 정한 값으로 여유가 크지 않다(평범한 인사 최대 0.84, 제스처 인사 최소 0.93).
  그림자 기록으로 실제 인사 명령의 분포를 확인해 조정한다. AI Config Server 웹 설정의 임계값 기본값도 새 키를 포함하도록
  맞춰야 한다.

## 2026-10-08 — System 1 툴 단위·복합 명령 테스트

- **조치**:
  - `scripts/system1_skill_catalog.py`: 스킬 클래스를 AST로 정적 분석해 `validation/system1/skill_catalog.json`(스킬 100개,
    공개 95개, 카테고리 14개)을 만든다. `--check`로 소스와의 일치를 검사한다.
  - `validation/system1/tool_cases.jsonl`: 모든 공개 스킬마다 1개 이상의 명령(인자는 `params`)과 인사·질문, 총 110건.
  - `validation/system1/compound_cases.jsonl`: 복합 명령 20건과 기대 스킬 순서.
  - `scripts/system1_tool_eval.py`: robo-claw와 같은 라우터 코드로 경로를 판정한다. 맞는 스킬·인자의 직접 실행은 `direct_ok`로
    구분하고, 복합 명령 직접 실행 수를 센다. 카테고리 → 스킬 2단계 툴 식별과 복합 명령 인식(multi_step, 가드 적중)도 평가한다.
  - `validation/system1/test_laya_tools_live.py`(라이브 3건), `src/robo_claw_agent/tests/test_system1_tool_cases.py`(오프라인
    8건: 카탈로그 최신 여부, 모든 공개 스킬 케이스 존재, 케이스 스킬 유효성, 직접 실행 후보, 규칙 라우터 복합 명령 비실행 등).
  - `run_laya_tests.sh`에 3단계(툴·복합 평가)를 추가하고 `docs/SYSTEM1_LAYA_DOCKER.md` 4.1절을 갱신했다.
- **실서버 결과** (`http://192.168.50.212:8000`, readonly, 130건 = 툴 110 + 복합 20):
  - 라이브 테스트 14건 통과(기존 11 + 툴 3).
  - 라우터: Laya correct 114 / escalated 14 / **wrong 2** / error 0, 복합 명령 직접 실행 0, 지연 p50 94.5ms·p95 116.2ms.
    규칙 라우터 correct 109 / direct_ok 5 / escalated 15 / wrong 1.
  - Laya 오실행 2건: "손목 롤 조인트를 0.5 라디안으로 움직여", "손 흔드는 모션 해줘"를 둘 다 `list_cloid_motions`(read)로 직접
    실행했다. 동작 요청에 목록만 답하는 오응답이다(물리 동작은 없음).
  - 규칙 라우터 오실행 1건: "비전 피드백으로 정면 컵을 집어"를 `adaptive_pick_object`로 직접 실행했다(`vla_pick_front_object` 기대).
  - 툴 식별(103건): 카테고리 48.5%, 스킬 24.3%, 정답 카테고리가 주어졌을 때 스킬 52.4%. 주요 혼동: navigation→autonomous,
    manipulation→autonomous, cooperation→butler, info→communication. 직접 실행 범위를 동작 스킬로 넓힐 수준은 아니다.
  - 복합 명령 20건: Laya가 multi_step으로 분류한 비율 5%. 그러나 robo-claw 복합 명령 가드가 20건 모두 모델 호출 전에 잡아
    LLM으로 보낸다.

## 2026-10-08 — Laya 단독 검증 환경과 테스트 케이스

- **조치**:
  - `scripts/system1_eval.py`: 인증 서버를 지원한다. `--api-key`, `--env-file`(dotenv의 `SYSTEM1_API_KEY`/`LAYA_API_KEY`,
    `SYSTEM1_ENDPOINT`/`LAYA_ENDPOINT`)을 추가했고, endpoint는 `LAYA_ENDPOINT`도 인식한다. 키 값은 출력하지 않고 출처만 표시한다.
  - `validation/system1/test_laya_live.py`: robo-claw 없이 실제 Laya 서버를 검증하는 라이브 테스트 11건(연결, 인증 401,
    choice/noul 스키마, 한국어 → multilingual, robo-claw 질문 세트, 선택지 상한 413, warm 지연 p95, 질문 단위 정확도, 라우터
    오실행률). 엔드포인트가 없으면 전체 skip한다.
  - `validation/system1/laya_cases.jsonl`: 질문 단위 정답 33건(intent 27, skill 13, ambiguous 6 판정).
  - `validation/system1/run_laya_tests.sh`: 라이브 테스트 + 규칙/Laya 비교 평가를 한 번에 실행하고 JSON 리포트를 남긴다.
  - `docs/SYSTEM1_LAYA_DOCKER.md` 4.1절에 사용법을 추가했다.
- **검증**: `test_system1_eval_script.py`에 dotenv 파싱, 키 우선순위, "키는 Bearer로 보내고 출력하지 않음" 테스트 3건을 추가했다
  (System 1 관련 단위 테스트 60건 통과). 가짜 Laya 서버로 라이브 테스트 11건 통과를 확인했다.
- **실서버 결과** (`http://192.168.50.212:8000`, 2026-10-08, robo-claw 질문 세트 readonly):
  - 서버: `device=cuda`, multilingual 체크포인트, 인증 정상(키 없음/틀린 키 401), 라이브 테스트 11건 통과
  - 지연(네트워크 포함, warm): p50 98.6ms, p95 128.2ms → `SYSTEM1_TIMEOUT_MS` 약 200ms 이상 권장
  - 질문 단위 정확도: intent 66.7%(18/27), skill 84.6%(11/13), ambiguous 66.7%(4/6)
  - 라우터(기본 임계값): correct 17 / escalated 15 / wrong 0 / error 0. 규칙 라우터(correct 18)와 비슷하다. 조회 명령의 intent
    confidence가 0.04~0.57로 낮아(`single_skill` 기준 0.85) 대부분 LLM으로 넘어가고, 인사 2건은 0.86~0.87로 기준(0.90)에
    조금 못 미친다. 오실행은 없었다.
  - 주요 오답: 조회 명령을 `question`/`smalltalk`로 분류, `get_status`↔`rag_status` 혼동("상태"), 일부 복합 명령을 `single_skill`로
    분류(robo-claw는 복합 명령 가드로 모델 호출 전에 LLM으로 보냄), "그거 좀 해줘"를 모호하지 않다고 판단.

## 2026-10-08 — Laya Jetson GPU 이미지: torch 인덱스와 베이스 이미지 정리

- **조치 (`docker/laya/Dockerfile`)**:
  - 베이스 이미지에 `python3`/`pip`이 없으면 apt로 설치하는 단계를 추가했다(`l4t-jetpack`, `nvidia/cuda` 베이스 대응).
  - torch 버전을 고정하는 빌드 인자 `LAYA_TORCH_SPEC`(기본 `torch>=2.0`, 예: `torch==2.8.0`)을 추가했다. 루트 `Dockerfile`의
    `INSTALL_LAYA` 경로에도 같은 인자를 전달한다.
  - 헤더 주석에 CPU, x86 GPU, JetPack 6, JetPack 7 빌드 예시를 넣었다.
- **조치 (`docker/laya/install_laya.sh`)**: 인덱스 주석에 Jetson AI Lab 인덱스(`jp6/cu126` 등, `sbsa/cu130`)와 `--index-url` 전용
  사용 이유를 추가했다.
- **문서 (`docs/SYSTEM1_LAYA_DOCKER.md`)**: 2.1절 확인 명령을 정리했다. 2.3절에 L4T → JetPack → CUDA → Python → torch 인덱스 →
  베이스 이미지 대응표를 추가했다(JetPack 6.x: `https://pypi.jetson-ai-lab.io/jp6/cu126`, cp310 / JetPack 7: `.../sbsa/cu130`,
  cp312 / JetPack 6.0: 대응 인덱스 없음). JetPack 6/7별 빌드 명령, GPU 확인 절차, 관련 문제 해결 항목, 참고 링크를 추가했다.
  예전 주소 `pypi.jetson-ai-lab.dev`는 쓸 수 없음을 명시했다.
- **확인**: 인덱스 목록과 torch 휠(`jp6/cu126`: 2.8.0~2.11.0 cp310, `sbsa/cu130`: 2.9.0~2.11.0 cp312)은 2026-10-08에
  인덱스 페이지에서 확인했다.
- **미검증**: 이미지 빌드와 Jetson에서의 GPU 추론은 실행하지 못했다. 베이스 이미지 태그(`l4t-jetpack` r36.4, JetPack 7용
  CUDA 13 이미지)는 NGC 카탈로그에서 호스트에 맞게 골라야 한다.

## 2026-10-07 — 자율 순찰의 기억 장소 우선 및 제자리 관찰

- **순찰 정책**: `autonomous_act`의 순찰 루프에서 frontier 탐험과 새 웨이포인트 생성을 제거했습니다. 시맨틱 맵과 RAG에 기억된 위치 및 기존 저장 지점만 후보로 사용하며, 이동 실패 장소는 60초 동안 재시도하지 않습니다. 기존 `explore` 스킬은 독립 기능으로 유지합니다.
- **목적지 부재 처리**: 후보가 없으면 임의 이동 goal을 보내지 않고 `scan_room`으로 한 번 제자리 관찰한 뒤 장면을 분석·기록합니다. 새 목적지가 나타나면 다음 목적지 부재 구간에서 다시 관찰할 수 있으며, 취소 요청은 스캔 중에도 확인합니다.
- **LLM 행동 제한**: 백그라운드 순찰/탐험 스킬의 중첩 실행을 차단하고, 자율 이동은 기억된 위치를 대상으로 하는 `navigate_to`로 제한합니다. 같은 장면 이벤트와 명시적 no-op의 반복 실행을 억제합니다.
- **호환성 및 문서**: 기존 `autonomous_act` 스키마의 맵/탐험 관련 입력은 하위 호환성을 위해 남기고 deprecated 처리했습니다. 프롬프트와 `docs/SKILLS.md`를 갱신했습니다.
- **검증**: `robo_claw_agent` colcon 빌드, 관련 회귀 테스트 101건, 전체 `task unit-test -- --strict-markers` 987건, 변경 파일 Ruff, 카탈로그/프롬프트/의존성 검사를 통과했습니다. 검증 중 오래된 테스트 기대값 2건을 현재 스킬/프롬프트 정의에 맞췄습니다. `py_validation_bundle`의 직접 pytest 경로는 ROS `launch_testing` hook이 별도 `test_agent_node.py`를 수집해 실패했으며, ROS 환경 runner를 통한 전체 단위 테스트는 통과했습니다. 전체 `task lint`는 변경 범위 밖 파일의 Ruff 진단 13건으로 실패했습니다.

## 2026-10-06 — Laya 서버 Docker 실행 가이드

- **조치**: `docs/SYSTEM1_LAYA_DOCKER.md`를 추가했다. Laya를 별도 컨테이너로 실행하는 절차를 배치별로 정리했다.
  - 같은 호스트(AGX Orin): CPU로 동작 확인 → `l4t-jetpack` 베이스 + Jetson torch로 GPU 전환, `--cpus`/`--cpu-shares`로
    주행·모션 제어 보호, `127.0.0.1` 바인딩
  - 다른 호스트(엣지 서버, 다른 Jetson): `LAYA_HOST=0.0.0.0`, `LAYA_API_KEY`/`SYSTEM1_API_KEY` 인증, 방화벽, 무선 구간
    지연·단절, 평문 HTTP 보안(TLS reverse proxy), 여러 로봇 공유
  - robo-claw 설정값, 연결·속도 확인, AGX Orin 예상 지연(추정), 문제 해결
- **정정**: robo-claw 컨테이너 내장 방식은 베이스 이미지(`ros:humble-ros-base`)에 CUDA/cuDNN이 없어 JetPack 6(AGX Orin)에서 CPU로만
  동작한다. `docs/SYSTEM1_FAST_ROUTER.md` 3장에 이 제약과 새 가이드 링크를 추가했다.
- **남은 과제**: `l4t-jetpack` 베이스에는 pip가 없을 수 있어 `docker/laya/Dockerfile`에 `python3-pip` 설치 단계가 필요할 수
  있다. `scripts/system1_eval.py`는 `SYSTEM1_API_KEY`를 보내지 않아 인증을 켠 서버는 그대로 측정할 수 없다. 둘 다 아직
  수정하지 않았다.

## 2026-10-06 — Laya 서버 배치: 엣지 서버 / Thor 내장(GPU) / CPU (브랜치 `jev-laya`)

- **배경**: robo-claw 이미지에는 Laya가 없어 `SYSTEM1_ENDPOINT=http://localhost:8000`이 `Connection refused`로 실패했다
  (요청은 규칙 라우터로 대체되어 동작에는 문제 없음). 주행·팔/손 모션을 하는 Thor 로봇에서 GPU로 돌리거나, 엣지 서버로
  옮길 수 있도록 같은 설정 체계로 배치를 고를 수 있게 했다.
- **설치 공용화**: `docker/laya/install_laya.sh`를 추가했다. torch를 지정한 인덱스(`LAYA_TORCH_INDEX_URL`, 기본 CPU)에서 먼저
  설치해 CPU/CUDA를 고르고, 이어서 `laya[serve]==0.3.28`을 설치한다. 선택적으로 모델을 미리 받는다(`LAYA_PREFETCH_REPOS`).
- **엣지 서버**: 단독 이미지 `docker/laya/Dockerfile`과 GPU compose 예시 `docker/laya/compose.edge.yaml`을 추가했다.
  `LAYA_API_KEY`(Bearer)와 robo-claw `SYSTEM1_API_KEY`로 인증한다.
- **Thor 내장**:
  - 루트 `Dockerfile`에 빌드 인자 `INSTALL_LAYA`(기본 `false`), `LAYA_TORCH_INDEX_URL`, `LAYA_PREFETCH_REPOS`를 추가했다.
    `numpy<2` 고정을 유지한 채 설치한다. `task docker-build-native`에 같은 인자를 전달한다.
  - `robo_claw_agent/system1_local_server.py`: `SYSTEM1_LOCAL_SERVER=true`일 때 launch가 실행하는 감시 프로세스다.
    `SYSTEM1_LOCAL_DEVICE=auto`는 CUDA가 있으면 GPU, 없으면 CPU로 실행한다. 제어 루프 보호를 위해 nice 10, 스레드 2,
    multilingual 체크포인트 1개, `127.0.0.1` 바인딩을 기본으로 한다. 비정상 종료 시 최대 5회 재시작한다. Laya가 설치되지
    않은 이미지면 경고만 남긴다. CUDA 확인은 짧은 자식 프로세스로 해 감시 프로세스에 torch가 상주하지 않는다.
  - `robo_claw_bringup/launch/_system1_launch.py`: 위 프로세스를 `robo_claw.launch.py`에 추가한다.
  - `System1Config`: 내장 서버면 `SYSTEM1_ENDPOINT`가 비어 있어도 `http://127.0.0.1:<SYSTEM1_LOCAL_PORT>`로 접속한다.
    기동 health check는 모델 로딩을 기다린다(`SYSTEM1_HEALTH_WAIT_SEC`, 내장 서버 기본 120초). 대기 중 실패는 circuit
    breaker에 세지 않는다.
  - `scripts/run_robo_claw_docker.sh`: `--gpu`(`RC_DOCKER_GPU`)로 nvidia 런타임이 있으면 `--runtime nvidia`, 없으면
    `--gpus all`을 붙인다. `--hf-cache`(`RC_HF_CACHE_DIR`)로 모델 캐시를 보존한다.
- **API 호환성**: `laya` 0.3.28 소스(`laya/serve.py`, `laya/agent.py`)로 확인했다. 질문 스키마(choice `criteria` dict, noul
  `instructions`), 응답 키(`choice`/`confidence`/`noul`), Bearer 인증이 robo-claw 클라이언트와 일치한다. `LAYA_DEVICE`는
  선호값이라 GPU를 쓰지 못하면 Laya가 CPU로 실행한다.
- **문서**: `docs/SYSTEM1_FAST_ROUTER.md` 3장을 배치 방식별 절차(엣지/Thor 내장/CPU, 주행·모션 로봇 고려, 모델 캐시·폐쇄망)로
  다시 썼다. 내장 서버 환경변수와 문제 해결 항목을 추가하고, AI Config Server 웹 설정(System 1 섹션)으로 전달되는
  경로를 반영했다. README·`.env.example`·설계 문서도 같은 내용으로 고쳤다.
- **검증**: 신규 `tests/test_system1_local_server.py` 16건과 `test_system1_router.py`의 내장 서버·health 대기 테스트 8건을
  포함해 System 1 관련 62건 통과. `tests/test_system1_launch.py`는 ROS `launch` 패키지가 없는 로컬에서 skip이며, launch
  헬퍼는 stub으로 별도 확인했다. `check_docker_deps.py` 통과, 셸 스크립트 `bash -n` 통과. `robo_claw_agent` 전체(로컬, ROS
  미설치)는 514 passed이며 실패 16건·수집 오류 10건은 변경 전 HEAD와 목록이 같은 ROS 의존 항목이다.
- **미검증**: Docker 이미지 빌드, Thor CUDA torch 설치와 GPU 추론, 실제 모델 응답은 검증하지 못했다. 작업 환경의 네트워크
  정책이 `download.pytorch.org`와 `huggingface.co`를 차단했고 Docker 데몬에도 접근할 수 없었다. `./rclaw launch --docker`의
  GPU 옵션과 AI Config Server의 `SYSTEM1_LOCAL_*` 필드는 별도 작업이 필요하다.

## 2026-10-06 — System 1 Fast Router 사용 가이드 (브랜치 `jev-laya`)

- **조치**: `docs/SYSTEM1_FAST_ROUTER.md`를 추가했다. Laya 서버 실행·확인, 필수/선택 `SYSTEM1_*` 환경변수 표, 단계별 `.env`
  블록(그림자 → readonly → navigation → 롤백), 실행 방식별 환경변수 전달 방법, 적용 전 평가, 로그 확인, 문제 해결을 정리했다.
- **정정**: 2026-10-02 README 절의 "`.env` 또는 AI Config Server의 `.env`에 넣고 `./rclaw run`으로 재기동"은 사실과 달랐다.
  - `./rclaw run`/`sim`은 저장소 `.env`를 launch 인자 구성에만 쓰고 프로세스 환경변수로 넘기지 않는다(`cmd/run.go`의
    `env := os.Environ()`).
  - `./rclaw launch`의 캐시 `.env`는 동기화 때마다 서버가 다시 생성하며, AI Config Server(`handler/config_files.go`)는
    고정 필드만 출력해 `SYSTEM1_*`를 내려주지 않는다.
  - `./rclaw launch --docker`는 캐시 `.env`와 DISPLAY/TZ/ROS_DOMAIN_ID만 컨테이너에 전달한다.
  - `.env`가 그대로 전달되는 경로는 `scripts/run_robo_claw_docker.sh`(및 워크스페이스 `run_robo_claw_docker.sh`)의
    `--env-file`뿐이다. 나머지는 셸 `export`가 필요하다.
  README 절을 요약과 가이드 링크로 줄이고, `.env.example` 주석과 설계 문서의 "설정 주입" 항목을 같은 내용으로 고쳤다.
- **LangSmith 문서 정정**: 같은 이유로 `./rclaw run`은 `.env`의 `LANGSMITH_*`도 전달하지 않는다. `tracing.py` docstring,
  `docs/LANGSMITH_INTEGRATION.md`("`./rclaw run`이 `.env`를 읽어 환경변수로 전달"), `.env.example` 주석을 실행 방식별 전달
  경로(`./rclaw launch` = 서버 프로필, Docker 스크립트 = `.env`, `./rclaw run` = 셸 `export`)로 고쳤다. `./rclaw launch --docker`는
  서버 프로필의 네 항목만 전달해 `LANGSMITH_TRACING_SAMPLING_RATE` 등은 Docker 스크립트의 `.env`로 지정해야 함을 명시했다.
- **남은 과제**: `./rclaw run`의 `.env` 전달과 AI Config Server의 `SYSTEM1_*` 필드는 코드 변경이 필요하다.

## 2026-10-02 — System 1 Fast Router(Laya) 도입 (브랜치 `jev-laya`)

설계: [`docs/SYSTEM1_FAST_ROUTER_DESIGN.md`](docs/SYSTEM1_FAST_ROUTER_DESIGN.md). LLM 호출 전 사전 라우팅을
규칙(RuleRouter)과 Laya System 1 모델 중 **하나만** 쓰도록 배타 선택하고, 성능이 나쁘면 규칙으로 롤백한다.

### Step 1 — 설계 문서 작성

- **배경**: 약한 로컬 LLM의 오라우팅을 정규식으로 교정하는 규칙이 계속 늘어났다. TypeSafe AI Jev(System One
  모델)를 검토했으나 유료 billing이 필요해 보류하고, Apache 2.0 오픈 웨이트 모델 Laya(`convaiinnovations/laya`)를
  자체 호스팅하는 방향으로 정했다.
- **조치**: 기존 규칙을 그룹 A(LLM 전 사전 라우팅: `check_direct_skill`, `_SIMPLE_QUERY_PATTERNS`)와
  그룹 B/B'(LLM 출력 교정·가드: `_force_navigation_if_misrouted`, `_route_location_query`, `_extract_save_place`,
  `nav_safety` 등)로 나눴다. 그룹 A만 Laya와 배타적으로 교체하고 그룹 B/B'는 항상 유지한다.

### Step 2 — 사전 라우팅 규칙을 RuleRouter로 분리 (동작 변화 없음)

- **조치**: `agent_node/fast_router.py`를 추가했다. 라우팅 결과 타입 `RouteDecision`(`direct_skill` /
  `simple_reply` / `llm`)과 `RuleRouter`를 정의하고, `ExecutionMixin._execute_task_inner`에 인라인으로 있던
  그룹 A 규칙(복합 명령 선점 방지, `check_direct_skill`, `_SIMPLE_QUERY_PATTERNS`)을 `RuleRouter`로 옮겼다.
- **실행 경로**: `execution.py`는 `self._router`(없으면 `RuleRouter`)의 결정만 소비한다. 라우팅 결과는
  LangSmith metadata(`route.kind`, `route.source`, `route.fallback` 등)로 기록한다.
- **유지**: 그룹 B/B'(planner의 출력 교정·가드)는 변경하지 않았다.
- **검증**: 신규 `tests/test_fast_router.py` 5건을 포함해 관련 테스트 47 passed / 1 skipped
  (`test_execution_routing.py`는 rclpy가 없는 로컬 환경에서 skip — ROS 2 환경에서 재확인 필요). Ruff 통과.

### Step 5 — Laya 사전 라우터 구현 (배타 선택 + 장애 롤백 + 그림자 기록)

> 설계 문서의 Step 3(PoC), Step 4(fine-tune)는 Laya 서버/GPU가 필요해 구현 코드(Step 5)를 먼저 넣었다.

- **조치**: `agent_node/system1_router.py`를 추가했다.
  - `SystemOneClient`: `POST {SYSTEM1_ENDPOINT}/v1/systemone` 호출(표준 라이브러리 `urllib`, 신규 의존성 없음). Laya와
    Jev가 같은 스키마라 endpoint만 바꾸면 provider를 교체할 수 있다.
  - `SystemOneRouter`: `intent`(choice), `ambiguous`(noul), `skill`(choice), `target_place`(choice)를 한 번에 질의한다.
    확신이 낮거나 모호하면 항상 System 2로 넘긴다. 복합 명령은 모델을 호출하지 않는다.
  - `SelectedRouter` / `build_router`: `SYSTEM1_ROUTER=rule|laya` 중 하나만 판단한다(배타). Laya가 timeout, HTTP 오류,
    circuit open이면 해당 요청만 RuleRouter로 대체하고 `route.fallback=true`를 기록한다.
  - `SYSTEM1_SHADOW=true`: 선택되지 않은 라우터를 백그라운드 스레드로 실행해 로그와 `SYSTEM1_SHADOW_LOG`(JSONL)에만 기록한다.
  - `CircuitBreaker`: 연속 3회 실패 시 30초 동안 Laya 호출을 막고(half-open 재시도), 기동 시 health check를 수행한다.
- **범위**: `SYSTEM1_SCOPE=readonly`(인자 없는 read 스킬) / `navigation`(+ 기억된 장소로 `navigate_to`). Laya는 숫자·자유
  텍스트 인자를 추출할 수 없어 `move_relative` 같은 규칙 지름길은 Laya 모드에서 System 2가 처리한다.
- **로그**: 라우터 구성 결과(`System1 router: router=... shadow=... scope=...`), 설정 오류, health check 실패를 ROS 노드
  로거로 출력한다. ROS 노드에서는 Python 모듈 로거의 INFO가 출력되지 않기 때문이다.
- **설정**: `.env.example`에 `SYSTEM1_*` 항목을 추가했다. `rclaw config-effective`가 `SYSTEM1_API_KEY`를 마스킹하도록 했다.
  기본값 `SYSTEM1_ROUTER=rule`이므로 설정하지 않으면 기존 동작과 같다.
- **검증**: 신규 `tests/test_system1_router.py` 28건(설정 파싱, 배타 선택, 장애 대체, 그림자 기록, circuit breaker, 로컬
  HTTP 서버 왕복) 통과. `robo_claw_agent` 전체 테스트(로컬, ROS 미설치)는 467 passed / 54 skipped이며, 실패 14건과
  수집 오류 10건은 변경 전과 목록이 동일한 ROS 의존 항목이다. Go 툴체인이 없는 환경이라 `effective.go` 변경은 빌드
  확인을 하지 못했다.

### Step 3 — Laya PoC 평가 도구와 시드 데이터셋

- **조치**: `scripts/system1_eval.py`를 추가했다. 같은 케이스 셋으로 RuleRouter와 Laya를 각각(또는 `--router both`로
  함께) 돌린다. ROS 2 없이 실행되며 결과를 correct / escalated(System 2로 넘긴 안전한 놓침) / wrong(오실행)으로
  집계하고 p50/p95 지연을 낸다.
- **데이터**: `validation/system1/router_cases.jsonl`(시드 32건: 인사, 상태/위치/시간 조회, 이동, 복합, 질문, 저장, 모호,
  조작)과 `validation/system1/router_eval_context.json`(평가용 스킬·장소 목록). `expected_navigation`으로
  `SYSTEM1_SCOPE=navigation`의 기대값을 따로 둔다.
- **규칙 기준선**(readonly scope): 32건 중 correct 18 / escalated 14 / wrong 0. 인사 변형("안녕하세요 반가워요")과 조회
  명령("배터리 얼마나 남았어?") 14건이 규칙에 걸리지 않고 LLM으로 넘어간다. 이 14건이 Laya로 줄일 수 있는 대상이다.
- **미실행**: 실제 Laya(`laya-serve`) 측정은 GPU/모델 다운로드가 필요해 이 브랜치에서는 실행하지 않았다. 서버를 띄운 뒤
  `--router both --endpoint ...`로 측정한다.
- **검증**: `tests/test_system1_eval_script.py` 2건(규칙 기준선 오실행 0, Laya 미기동 시 크래시 없이 error 집계) 통과.

### README — System 1 Fast Router 사용법

- **조치**: `README.md`에 "System 1 Fast Router (Laya, 선택)" 절을 추가했다. Laya 서버 실행과 확인, 적용 전 평가
  (`scripts/system1_eval.py`), 단계별 적용(그림자 → readonly → navigation → 롤백)의 `.env` 예시, 설정 항목 표,
  기동/장애/그림자 로그 확인 방법, Laya 모드에서 꺼지는 규칙 지름길을 정리했다.
- **문서 표**: "상세 가이드" 표에 `docs/SYSTEM1_FAST_ROUTER_DESIGN.md`를 추가하고, 주요 특징에 항목을 추가했다.
- **남은 단계**: Step 1(LangSmith 기준선 — 403 해결 필요), Step 4(fine-tune), Step 6~8(운영 중 그림자 → 전환 → 확대)은
  Laya 서버와 운영 데이터가 필요해 이 브랜치에서 수행하지 않았다.

## 2026-10-02 — LangSmith API 키 마스킹 우선순위 보정

- **환경변수 병합**: 원격 `.env` artifact에 유효한 `LANGSMITH_API_KEY`가 있으면 JSON 설정의 값을 덮어쓰지 않습니다. JSON 응답의 마스킹 값(`********`)은 런타임 키로 주입하지 않으며, artifact에 키가 없을 때만 유효한 JSON 키를 fallback으로 사용합니다.
- **회귀 테스트**: artifact 키 보존, 마스킹 값 제외, JSON 키 fallback을 검증합니다.

## 2026-10-02 — LangSmith workspace ID 런타임 설정

- **설정 배포**: 공유 runtime contract v2.3.0에 `LANGSMITH_WORKSPACE_ID`를 등록하고, AI Config Server 프로필 입력란 및 `.env` 생성에 연결했다.
- **로컬 런치**: 서버 API/cache에서 workspace ID를 받아 local launch 환경에 주입하고, `.env.example` 및 LangSmith 통합 가이드를 갱신했다.
- **검증**: contract 호환성 검사와 contract/서버/CLI 설정 회귀 테스트를 추가했다.

## 2026-09-22 — Robo-Claw Maestro FleetControl outbound connector

- **연결**: `MAESTRO_IP`, `ROBOT_PORT`, `ROBOT_ID` 기반 outbound FleetControl client를 `robo_claw_grpc` 실행 경로에 추가했다. `MAESTRO_IP`가 없으면 기존 동작을 유지한다.
- **등록/상태**: session, heartbeat, battery/pose/health telemetry와 `site_id`, `map_id`, `map_version`, `ROBOT_MAP_FRAME_ID`를 registration/telemetry로 전달한다.
- **Capability**: Robot의 `SKILLS.md`를 구조화 manifest로 변환하고 read/motion/dangerous 위험도를 registration 단계에서 Maestro와 교환한다.
- **명령/안전**: Task, Skill, Snapshot, EmergencyStop 명령을 기존 ROS client에 연결하고 `complete`, `stop`, `finish_atomic` disconnect 정책을 지원한다.
- **중복 방지**: SQLite command journal에 idempotency key와 결과를 저장해 process 재시작 후 중복 실행을 차단하고 reconnect 시 미전송 결과를 replay한다.
- **보안**: 개발 LAN용 insecure 연결과 운영용 TLS/mTLS client 인증을 모두 지원한다.
- **검증**: connector 단위/실제 loopback gRPC 통합 테스트 6건 및 Maestro backend 85건 통과. 실제 Robot 읽기 전용 인수 테스트는 다음 단계다.

## 2026-09-22 — LangSmith 상세 실행 그래프 계측

- **조치**: Task root, runtime operation, plan parser, navigation safety, SkillManager, policy/precondition/schema와 skill attempt를 parent-child trace로 계측하고 Robot ID/trace ID correlation을 추가했다.
- **보안**: credential, token, image/base64, binary 및 대용량 payload를 LangSmith 전송 전 복사본에서 redaction한다.
- **비활성 호환성**: `LANGSMITH_TRACING=false`이면 SDK run/client를 만들지 않고 기존 sync/async 함수, 반환 객체와 예외를 그대로 유지한다.
- **검증**: tracing 비활성 반환값·예외 보존과 redaction 테스트를 포함한 관련 테스트 35건 통과, Ruff 검사 통과.

## 2026-09-23 — CLOiD 휴머노이드 로봇 기본 프로필 추가

- **배경**: CLOiD(CLOi-D) 휴머노이드 실기(`CLOI-Robostar-027`, `cloid-nvidia-thor` 모델,
  `ROBOT_TYPE=hmc_v2`)에 RoboClaw 를 붙이려면 `robot_config:=cloid` 로 선택할 로봇 프로필이
  필요했다. 기존 프로필(stretch3/former/butler)은 토픽·센서·제어 경로가 CLOiD 와 달라
  그대로 쓸 수 없었다.
- **실측 조사**: 로봇에 SSH 로 접속해 `cloi status`/`cloi logs` 로 서비스 구성을 확인하고,
  tofu 컨테이너에서 ROS graph 를 직접 조회했다.
  - 토픽/타입: `/cmd_vel`(diff_drive_controller 는 `geometry_msgs/TwistStamped` 구독),
    `/odom`, `/battery_state`, `/chest_imu_broadcaster/imu`, `/lidar_points_bounded`,
    `/rgb_cam_*/image_raw/compressed`, `/camera_front|camera_rear/.../depth/image_rect_raw`,
    `/map`, `/robot_pose`. `/scan` 은 구독자만 있고 발행자가 없다.
  - URDF/TF: `tofu` 컨테이너의 `robot_state_publisher` 파라미터에서 `hmc_v2_hand` URDF 를
    추출해 조인트 이름·limit 을 확인했다. TF 체인은 `map -> lio_odom -> base_link` 이고
    헤드 RGB 카메라의 optical frame 은 TF 트리에 없다.
  - action/service: Nav2 `navigate_to_pose`/`spin` 은 있고, AMCL 관련
    `/amcl_pose`/`/reinitialize_global_localization` 은 없다.
- **조치**:
  - `src/robo_claw_bringup/config/cloid_config.yaml` 추가 — core 는 `imu`
    (`/chest_imu_broadcaster/imu`)와 raw Image 를 발행하는 전면 D435 depth 스트림만
    activity 추적 센서로 등록하고, agent 는 카메라를 compressed 헤드 RGB
    (`/rgb_cam_head_front_left/image_raw/compressed`)로 지정하며 `manipulation_enabled=false` 로
    기동한다.
  - `src/robo_claw_bringup/config/ROBOT_LIMITS.cloid.json` 추가 — 주행 한계와 함께
    URDF limit 을 그대로 옮긴 팔·waist·neck·hand 조인트 raw(rad) 하드 한계를 넣고 조작은
    비활성으로 둔다.
  - `src/robo_claw_bringup/config/SKILLS.cloid.md` 추가 — CLOiD 특유의 제약(조작 백엔드
    미구현, LaserScan 없음, AMCL 없음, compressed 전용 카메라)과 주행·정지 규칙을 런타임
    가이드로 주입한다.
  - `robo_claw.launch.py` 의 `robot_config` 설명을 `cloid` 포함으로 갱신했다.
  - 센서 이름에 `camera` 가 들어가면 launch 의 `_resolve_camera_topic` 이 그 토픽을 agent
    카메라로 선택하므로, raw depth 센서는 link 이름(`front_d435`)으로 등록해 agent 카메라
    override 를 피했다. 이 동작은 테스트로 고정했다.
- **검증**: `task unit-test` **906 passed / 2 deselected**, 신규
  `src/robo_claw_bringup/tests/test_cloid_robot_config.py` 9건 통과,
  `uv run ruff check src/robo_claw_bringup/` 및 `ruff format --check` 통과,
  `python scripts/check_prompt_tone.py` 한도 10/10 유지, `task contract-check` 통과.
- **범위**: `src/robo_claw_bringup/{config,launch,tests}`, `docs/CLOiD_GUIDE.md`,
  `docs/CONFIGURATION_GUIDE.md`, `docs/DOCKER_GUIDE.md`. CLOiD 실기 검증(실제 기동 및 스킬
  동작)은 아직 하지 않았고, 아래 한계는 후속 스킬 개발 과제로 남는다.
  - `/cmd_vel` 이 `TwistStamped` 라 직접 cmd_vel 제어(Twist)가 적용되지 않는다.
  - 2D LaserScan 이 없어 라이다 기반 장애물 스킬과 health 의 lidar 항목이 비어 있다.
  - AMCL 이 없어 `self_localize` 를 쓸 수 없다.
  - 팔/hand 제어 백엔드가 없어 조작 계열 스킬이 차단 상태다.

## 2026-09-17 — gRPC peer 인증 토큰 전파 누락 및 채팅앱 인증 에러 오안내 수정

- **증상**: 서로 다른 로봇에서 `GRPC_PEER_TOKEN`(또는 `GRPC_PEER_TOKENS_JSON`)을 설정하면 수신 로그에
  `[gRPC] Unauthorized message rejected`가 반복되고, 상대 로봇에서는 협동 메시지 회신이
  `협동 메시지 인증 실패`로 처리되어 메시지 전달이 실패로 보고됐다. 또한 채팅앱(robo_claw_talk 등)에서
  일반 사용자 메시지를 보냈을 때도 토큰 미일치 시 `"협동 메시지 인증 실패"`로 응답해, 일반 채팅이 협동로봇
  로직으로 처리되고 있다는 오해를 유발했다.
- **원인**:
  - `client_node._process_received_message`가 보내는 회신(`sender_id=robot`), `broadcast_to_peers`의
    `SendCommand`, envelope가 없는 `call_peer_robot` 메시지는 `metadata[auth_token]`이 없어 서버에서 거부됐다.
  - 서버 `ChatStream`이 `_is_authorized(req, None)`으로 호출해 스트림 호출 metadata
    (`x-robo-claw-peer-token`/`x-robo-claw-peer-id`)를 보지 않았다.
  - `grpc_peer_tokens_json`(피어별 토큰)에서는 `sender_id` 조회만 사용해, `sender_id=robot`인 회신이나
    `sender_id=user`인 채팅앱은 유효한 토큰을 보내도 바인딩 대상을 찾지 못했다.
  - `ChatStream` 및 `SendCommand`에서 인증 실패 회신 문구가 `"협동 메시지 인증 실패"`로 하드코딩되어 있었다.
  - `robo_claw.launch.py`에서 `client_node`로 `grpc_peer_token` 및 `grpc_peer_tokens_json` 파라미터가 누락되어 있었다.
- **조치**:
  - `client_node`에 `_authorized_peer_message()`를 추가해 토큰이 설정된 로봇의 **모든 송신 메시지**에
    `auth_token`을 싣고, `ChatStream`/`SendCommand`/`UploadFile` 호출 metadata로도 토큰과 피어 id를 보낸다.
  - `client_node`에 `grpc_peer_token`, `grpc_peer_tokens_json` ROS 2 파라미터를 선언하고 `robo_claw.launch.py`에서 바인딩했다.
  - 서버는 `ChatStream`에서 호출 context를 전달하고, `_is_authorized`가 메시지 metadata와 호출 metadata를
    모두 확인하며 피어별 토큰 맵은 `x-robo-claw-peer-id`로 보조 조회한다.
  - `_auth_binding`에서 공통 토큰이 없더라도 비-피어 발신자(`user`, `robo_talk` 등)가 등록된 유효 토큰 중 하나를 제시하면 승인하도록 개선했다.
  - 인증 거부 시 메시지 `type`에 따라 분기하여, 협동 메시지(`peer_cooperation`)일 때만 `"협동 메시지 인증 실패"`를 반환하고, 일반 채팅 메시지는 `"gRPC 메시지 인증 실패 (인증 토큰 누락 또는 불일치)"`와 `error_code="AUTH_FAILED"` 메타데이터를 반환한다.
- **검증**: `test_grpc_server.py`에 스트림 토큰 인증, 피어별 토큰 보조 조회, 채팅앱 토큰 승인, 에러 문구 구분 검증 등 24건 통과,
  `test_client_node.py`에 토큰 전파 20건 통과, `test_launch_parameter_value.py` 6건 통과.
  `uv run ruff check src/robo_claw_channel` 통과.
- **범위**: `src/robo_claw_bringup/launch/robo_claw.launch.py`, `src/robo_claw_channel/robo_claw_channel/{client_node,grpc_server}.py`,
  관련 테스트 및 `docs/PEER_COLLABORATION.md`. 상대 로봇 미기동/방화벽/IP 불일치로 인한 접속 불가는 환경 문제로 남는다.

## 2026-09-17 — 정보성 스킬 결과 요약 답변 및 최종 답변 JSON 노출 차단

- **원인 1(답변 누락)**: `run_llm_planning_loop` 이 스킬 체인 성공 직후 마지막 스킬의 `message` 로 종료해, `ros_command`(message 가 `"실행 완료"`)처럼 실제 결과가 `result_data` 에만 있는 스킬은 조회 결과가 사용자에게 전달되지 않았다(`"수신 가능한 토픽 목록 보여줘"` → `"실행 완료"`). `execute_skill_chain` 이 넣는 실행 결과 요약 메시지는 성공 경로에서 소비되지 않는 죽은 코드였다.
- **원인 2(JSON 노출)**: `parse_llm_response` 가 알 수 없는 키만 가진 JSON을 `response` 로 승격해 원문 JSON이 최종 답변이 됐고, `response` 가 객체면 파이썬 repr 로 노출됐다. 또한 `result_msg = plan.reason` 으로 내부 전략 필드가 사용자 답변이 됐다.
- **조치**:
  - `BaseSkill.answer_mode`(`action` 기본 / `informational`) 신설. 결과 데이터를 보여줘야 하는 정보성 스킬 34개(신규 `list_topics` 포함)에 `informational` 을 선언했다.
  - 정보성 체인 성공 시에만 결과 요약 라운드(상한 1회)를 실행해 LLM이 `result_data` 근거로 최종 답변을 합성한다. 물리 동작/백그라운드 스킬은 기존 즉시 종료를 유지해 두 번째 물리 동작 계획 위험을 피하고, 요약 라운드에서 스킬 재호출은 실행하지 않고 결정론적 폴백으로 종료한다.
  - `reason` 을 사용자 답변에서 제거하고 로깅 전용으로 강등했다.
  - `answer.py` 신설: `sanitize_final_answer`(JSON envelope 해제, 구조체 평탄화, think 블록/코드펜스 제거, 계획 envelope 차단)와 `render_result_data`. `ExecuteTask.Result.result_message` 경계(HTTP/gRPC/ROS action/메신저 공통)에 적용했다.
  - 파서 계약 수정: 미지 JSON은 `parse_failed` 로 표시해 형식 교정 재시도 대상에 넣고 `response` 에는 사람이 읽는 텍스트만 담으며, `response` 객체는 평탄화한다.
  - `list_topics` 스킬 신설(`risk_level=read`, `answer_mode=informational`): rclpy 그래프에서 토픽/메시지 타입을 조회해 사람이 읽는 목록으로 반환하고, 숨김 토픽 제외와 빈 그래프 재탐색을 지원한다.
  - 프롬프트: 조회·목록 요청은 결과 데이터를 근거로 자세히 정리하고 JSON을 답변으로 출력하지 않도록 지시를 추가하고, `reason` 이 내부용임을 명시하고, `ros_command` 허용 범위를 실제 계약(읽기 전용 조회만)으로 정정하고 `list_topics` 라우팅을 추가했다.
- **검증**: 신규 테스트 5개 파일(`test_final_answer_sanitize`, `test_final_answer_boundary`, `test_readonly_answer_synthesis`, `test_answer_mode_metadata`, `test_list_topics_skill`)을 추가하고 기존 파싱 테스트 4건을 갱신했다. `task unit-test`, `task lint-skills`, `uv run ruff check`(변경 파일), `python scripts/check_prompt_tone.py`(10/10 유지)를 통과했다.
- **범위**: `execution.py`/`planner.py`/`skill_manager.py`/`utils.py`/`prompts.py`/`skills/system_skill/{ros,__init__}.py`, 스킬 33개 파일에 `answer_mode` 1행 추가, `docs/SKILLS.md`. 저장소에 이미 존재하던 mypy/ruff 부채와 실행 환경의 analyzer stale 캐시는 이 변경에서 건드리지 않았다.

## 2026-09-14 — contract 생성 산출물 포맷터 drift 차단

- **원인**: `scripts/generated_runtime_env.sh`가 자동 포맷터(shfmt)에 의해 2-space로 재작성되면서 `rclaw contract check`가 아티팩트 drift를 보고했다. 생성기(`RenderShellMapping`)와 저장소 shell 표준은 4-space이고, 포맷터는 `.editorconfig`가 없으면 파일에서 들여쓰기를 추정해 `shfmt -i 2`를 적용하므로 매 턴 drift가 재발했다.
- **조치**: 프로젝트 `.pi-lens.json`에 생성 산출물 `ignore` 패턴을 추가했다(`scripts/generated_runtime_env.sh`, `config-schema/generated/**`). pi-lens의 format/autofix 파이프라인 진입이 차단되어 생성 파일이 정본(generator 출력)을 유지한다.
- **검증**: pi-lens `latency.log`의 파이프라인 단계로 확인했다. 제외 전에는 생성 파일 수정 시 `format`/`autofix`/`deferred_format_queued`가 기록되고 `agent_end`에서 `formatter=shfmt, changed=true`가 남았으나, 제외 후에는 `tool_result_received`만 기록되고 파이프라인 단계가 남지 않는다. `./rclaw contract check` 통과, `git diff` 없음.
- **범위**: generator가 소유한 Python/Go 산출물은 각각 ruff-clean·`format.Source` 출력이라 포맷터에 대해 안정적이므로 제외 대상에서 뺐다.

---

## 2026-09-11 — messenger 포트 문서 정정 및 외부 채팅 접속 절차 정리

- **`PEER_COLLABORATION.md` 포트 표기 정정**: §1 `.env` 예시와 `GRPC_TARGET_PEERS_JSON` 예시의 messenger 포트를 50051에서 50052로 통일했다. 50051은 `robo_claw_grpc`(RosGrpc 텔레메트리) 전용 포트이므로 채팅 메신저 포트와 혼동하지 않도록 명시했다.
- **peer token 바인딩 동작 문서화**: `GRPC_PEER_TOKEN` 설정 시 messenger 서버가 `0.0.0.0`으로 바인딩되어 다른 PC/로봇에서도 채팅에 접속 가능하고, 모든 클라이언트가 인증 토큰을 보내야 함을 명시했다(기존 보안 기본값: 토큰 없으면 루프백 전용).

---

## 마일스톤 요약

| 기간            | 주제                                                                                  |
| --------------- | ------------------------------------------------------------------------------------- |
| 2026-02         | 초기 구현 — 에이전트·LLM 브릿지·ROS 메시지·맵/시스템/비전 스킬                        |
| 2026-03 ~ 04    | 스킬·시뮬레이션 고도화, Docker 도입, MCP 어댑터                                       |
| 2026-05         | gRPC 서비스/`robo_claw_grpc` 패키지, 에이전트 모듈 구조 개편, Ollama                  |
| 2026-06         | `robo_claw_cli`(Go) 도입, 스킬 자가학습, 로봇 제어 안정성(v0.2.0)                     |
| 2026-07         | Stretch3 매니퓰레이션, gRPC 피어 협업, Taskfile, 태스크 큐, 복합 명령 분해, LangSmith |
| 2026-08 중하순  | 스킬 입력 스키마 계약, 안전성·자율협동 강화                                           |
| 2026-08-03 ~ 14 | RAG 위치 지식 신뢰성 개선 (→ 부록 상세 노트)                                          |
| 2026-09         | 런타임 설정 contract 체계, `rclaw` CLI 재구조화, `rc.sh` 제거                         |

---

## 2026-09-11 — 스크립트/유틸 디렉터리 통합 및 도구 문서 정리

- **`utility/` 제거 → `scripts/` 단일 통합**: 역할이 겹치던 저장소 루트 운영/개발 보조 스크립트를 한 곳으로 합쳤다. `count_prompt_tokens.py`, `show_ai_config.py`, `reset_memory.sh`, `reset_robot_state.sh`, `reset_robot_state_server_db.sh`를 `scripts/`로 이동하고, 기존 `Taskfile`·Go CLI·문서가 참조하던 `scripts/` 경로는 그대로 두어 파급을 최소화했다. 생성 파일 `scripts/generated_runtime_env.sh`는 contract 소유 대상이라 이동하지 않았다.
- **유틸 readme를 `docs/`로 이관(§12 준수)**: `count_prompt_tokens-readme.md` → `docs/PROMPT_TOKEN_COUNT.md`, `show-ai-config-readme.md` → `docs/AI_CONFIG_VIEWER.md`, `reset_robot_state-readme.md` → `docs/RESET_ROBOT_STATE.md`, `reset_robot_state_server_db-readme.md` → `docs/RESET_ROBOT_STATE_SERVER_DB.md`. UPPER_SNAKE 명명, 한글 제목·~합니다체 적용, README 상세 가이드 표에 등록.
- **참조 동기화**: `docs/`·검증 도구(`validation/talk/robo_talk.py`, `validation/talk/README.md`)·`scripts/reset_robot_state_server_db.sh` 주석의 `utility/` 경로를 `scripts/`로 갱신.
- **`rclaw reset-robot` 커맨드 신설**: 초기화 bash 스크립트를 단일 CLI 입구로 노출(스크립트가 진실 소스 — 로직 이중화 없이 실행만 위임).

- **`rclaw config` 커맨드 신설**: 플랫폼별 기본 설정 폴더(`~/.config/robo_claw_cli/config.yaml`, macOS `~/Library/Application Support/...`, Windows `%AppData%\\...`, `ROBO_CLAW_CLI_CONFIG_DIR` 오버라이드)에 로컬 설정을 저장하고 `init`/`show`/`path`/`raw`/`set`/`edit` 서브커맨드로 생성·확인·변경. 로드 우선순위는 실행 위치 → 실행파일 디렉토리 → 플랫폼 기본 폴더 → `ROBO_CLAW_CLI_*` 환경변수(최우선).
- **CLI 설정 파일 보안 정리**: `robo_claw_cli/config.yaml`(device_token 포함)을 저장소 추적·히스토리에서 제거하고 gitignore 처리, 예시는 `robo_claw_cli/config.example.yaml`로 관리. 실기기 토큰은 환경변수 주입을 권장.
- **런타임 설정 contract 체계 도입**: canonical runtime registry → Go/Dart/launch/shell generated contract 생성, vendored contract lock 검증, 구현 coverage 강제, `task contract-check` 정립. 계약 소스를 공통 저장소(`rcf-config-contract`) Release artifact로 고정(v2.0.1).
- **보안 강화**: 인증 없는 messenger loopback 바인딩, insecure config server HTTP 명시 요구, gRPC `bind_host` 설정 위치 정리.
- **CLI 정비**: `rclaw` 서브커맨드 구조화 + contract 기능 통합, public contract 다운로드, CLI 메시지 한국어 전환.
- **빌드 체계**: Taskfile 기반 빌드/테스트 체계 도입, former0045/former0047 실행 스크립트 분리, legacy docker 스크립트 제거, CLOi 가이드 및 gRPC 포트 설정.
- **`rc.sh` 제거**: 모든 기능이 `task`(빌드/테스트)와 `./rclaw`(기동/런타임 제어)로 이전된 순수 위임 래퍼였으므로 삭제하고 저장소 전체 참조를 정리. `.env` 로딩은 `rclaw run/sim`이 직접 수행.
- 그리퍼 비전 및 로봇 프로필 설정 추가, 원격 스킬 정책/자율 목표 안전성 강화.
- **문서 체계 정비**: `docs/` 파일명 UPPER_SNAKE 통일(http_api→HTTP_API 등 7건, 코드 주석 참조 동기화), behavior-tree 문서를 현재 소스 기준으로 갱신(ProcessPeerMessages·파라미터·blackboard 키), DOCKER 빌드 가이드를 `task docker-*` 태스크 기준으로 재작성, LangSmith/Messenger/README 가이드 최신화. 변경 이록·설계안·AI 세션 노트 문서(rag-improvements, SKILL_AUDIT, STRETCH_AI_PICKUP_COMPARISON, semantic-memory-dedup, changenote, API 치트시트 등)는 삭제하고 상세는 `CHANGE_LOG` 부록으로 일원화. 프롬프트 예시의 미존재 스킬명 수정(capture_image→capture_camera_image).
- **문서 통합·문체 통일·작성 규칙**: AUTONOMOUS_BEHAVIOR → behavior-tree 흡수(고아 문서 제거), ROBOT_SOUL + ROBOT_SAFETY_CONFIG → `ROBOT_CONFIG.md` 통합, Docker 실행 + 빌드 → `DOCKER_GUIDE.md` 통합. CLOi 가이드 한국어 번역, 전체 문서 ~합니다체 통일(문체 혼용 제거). `AGENTS.md`에 문서 작성 규칙(§12: 종류 정책/명명/문체/생성·삭제 절차/링크 검증) 신설.

## 2026-08-20 ~ 09-07 — 스킬 스키마 계약·안전성 강화·자율협동 고도화

- **스킬 입력 스키마 계약 전면 도입** (08-20): 전 스킬 JSON 스키마 선언 → LLM 파라미터 검증·오류 재계획, precondition/물리 부작용 충돌·배타 제어 리소스 검증, 백그라운드 스킬 메타데이터 분류.
- **검증 리포트 투명성**: `robo_talk`이 실행 코드 commit(호스트 체크아웃 vs 이미지)·AI Config(LLM/RAG 파라미터)를 리포트에 강제 출력 — 환경 차이에 의한 오진 방지.
- **MCP**: `streamable_http` 전송 지원, MCPManager 강화.
- **자율협동**: 자율협동 스킬 + Behavior Tree 통합, gRPC 피어 인증, 원격 작업 안전 정책, 피어 메시지 자동 참여, RAG 기반 장소 순회, 다중 로봇 `agent_id` 지원.
- **안전성 리팩토링** (08-24~25): 매니퓰레이션 limits/trajectory 취소·복합 시퀀스 단계별 복구, 내비게이션 goal lifecycle 레지스트리/safety gate·직접 회전 취소, 센서 타임스탬프 freshness, 행동트리 안전 정책/peer 타임아웃 전파, 파일·스크립트 경로 탈출 방지/중앙 비상 정지 래치, 한국어 대상어 → COCO 클래스 정규화, IK 범위 밖 접근 이동.
- **비전**: 오픈어휘 VLM 폴백 (08-28), 객체 인식 Tier1/2 개선 — CLAHE/bbox 검증/temporal 필터 (08-12).
- **09-01~07**: 생성 protobuf 소스 제외 + 이미지 빌드 시 생성, 배터리 체크 유연화, Public/Internal 스킬 분리, 컴팩트 스킬 schema 프롬프트, 스킬 실행 결과 표준화, 전체 스킬 카탈로그 문서화, Stretch 주행 전 팔 stow 검증 강화.

## 2026-08-03 ~ 08-14 — RAG 위치 지식 신뢰성 개선

위치 지식의 자기 오염 제거·좌표↔이름 양방향 조회·사이트 일반화, 검증 자동화 도구, 도커 배포 식별성. **상세는 부록 참조.**

- 저장 표준화(`location_name/x/y/type=location`)와 오염 제거(조회성 스킬 비학습, dedup), `identify_location`(좌표→이름)·`get_location`(이름→좌표) 스킬 신설/통합.
- 위치질의 결정론적 라우팅 강제 + 규칙이 LLM 개체명 추출을 덮어쓰던 문제 정정(규칙은 폴백으로만 동작).
- Qdrant point ID를 UUIDv7로 전환(시간순 정렬), `list_entries` 스캔 비용/무손실 수정, `result_json` 전달 경로 복구.
- 배포 문제 진단: 실기 6건 실패의 진짜 원인은 이미지 낙후가 아니라 **호스트 소스 bind mount + `git pull` 누락**, `robo_talk` 배포 식별성으로 확증.
- 실기 검증: gemma4:31b **17/17**, gemma4:e4b **17/17**(212.8s) — 결정론적 경로로 모델 성능 의존성 제거.

## 2026-08-01 ~ 08-02 — 파지 보조·쿼리 차단

- 헤드 카메라 보조 파지 + LLM 응답 파싱 개선.
- 대화형 쿼리 사전 차단 및 스킬 분류 리팩토링.

## 2026-07-15 ~ 07-30 — 플래닝·gRPC 서비스·관측성·보안

- **태스크 큐 도입** 및 자율 행동 모드 고도화, 전체 코드 품질 개선(린트 경고 정리) (07-15).
- **복합 명령 자동 분해**(Task Decomposition) 도입 (07-23).
- **LangSmith 트레이싱 통합** (07-27).
- **LLM 플래닝/파싱 강화** (07-24~29): 플래닝 개선·스킬 실행 재시도, 응답 형식 가이드/재시도, 스킬 호출 정규화, Ollama think 모드 JSON 포맷 제한, 내비게이션 목적지 보존.
- **gRPC 서비스 확장** (07-29): `ExecuteTask` 서비스, 범용 스킬 실행 서비스, 초기 위치 설정 스킬, `get_datetime` 스킬, 시맨틱 메모리 정리 도구, 컨테이너 타임존.
- **보안**: gRPC 명령 주입 취약점 수정 (07-29), HTTP 요청 리팩토링·인증 에러 처리 (07-22).
- BaseSkill `call_service` 신설(4개 스킬 마이그레이션), goal 알림 템플릿, 제자리 회전 cmd_vel 폴백, 상대 이동 stow 보장, ONNX Runtime 설치 자동화 (07-22).

## 2026-07-01 ~ 07-14 — Stretch3 매니퓰레이션·피어 협업·빌드 전환

- **Stretch3 백엔드** (07-03~10): 내비게이션/속도 제어 통합, 그리퍼 카메라·주행 안전 정책, 파지/배치 정렬 강화, 매니퓰레이션 시퀀스 스킬 세분화.
- **비전·지각**: depth 카메라 3D 포인트 계산, 객체 감지 좌표계 map 프레임 통일 + TF 변환, 헤드 카메라 회전 파라미터/depth3d, 비전 노드 기본 활성화·추론 최적화.
- **동료 로봇 gRPC 피어 협업** 및 자율 순찰 고도화 (07-10).
- **빌드 시스템을 Makefile → Taskfile로 전환** (07-12), 맵 이미지화 이동불가 영역 대비 + VLM 좌표 검증.
- **코드 중복 제거·모듈 분리** (07-01): `_ros_sync`/`stretch_kinematics`/`sensor_health`/`skill_result_msg`/`geo_utils`(agent), `ros_future_utils`/`param_helpers`(channel), `proto_converters`(grpc), `_common_launch_args`(66/60 launch 인자 중앙화) — `node.py` 501→347 라인.

## 2026-06 — CLI 도입·스킬 자가학습·로봇 제어 안정성 (v0.2.0)

- **`robo_claw_cli`(Go) 도입** (06-02~17): 시나리오 기반 실기 테스터, 커스텀 시나리오/리포팅, Go 모듈 + 생성 gRPC proto.
- **스킬 자가학습 3단계** (06-21): 실행 경험 RAG 수집 → 경험 기반 교훈 추출 → 교훈 시스템 프롬프트 자가 주입(+ .env/launch/CLI 연결).
- **회전 안정화·좌표 프레임 통일** (06-23): map 프레임 위치 보고, 4방향 위치 파악, 이동가능 장소 탐색 스킬, annotate 오류 수정.
- **로봇 제어 안정성 개선** (06-24): `ExecuteSkill` 예외 처리/timeout 실제 구현, **ROBOT_LIMITS 하드 강제**(거리/각도/금지구역/조인트), 공통 스킬 정책(`/skill`·`/task`·gRPC·메신저 allow/block), LLM `validate_config`/`healthcheck`/`llm_fail_fast`, `strict_config`, conftest ROS skip, Docker 선택적 장치 마운트.
- **코드 품질**: ruff 규칙 정립(F401 16건 등), pytest 마커(`ros2`/`qdrant`/`mcp`) 도입, `# type: ignore` 86→44개, lazy 로깅 전환.
- 벡터 저장소/내비게이션 `face_direction`·orientation 지원 (06-26), butler config/Docker (06-29), Ollama think 모드 소형 모델 적용 (06-30).

## 2026-05 — gRPC 서비스·에이전트 구조 개편·운영 기반

- **gRPC 로봇 제어/상태 서비스 구현** + `robo_claw_grpc` 패키지 (05-16~18).
- **에이전트 모듈 구조 리팩토링** (05-14): skills/memory/manipulation runtime 패키지화, 가상 장애물 스킬 + Nav2 obstacle layer, 자율 행동 반응형 내비게이션 노드.
- Ollama 엔드포인트·영속 클라이언트 (05-12/19), `LogObservationSkill`.
- **SKILLS.md 시스템 프롬프트 추가**, 운영 한계/안전 가이드 통합, 에이전트 전용 작업 공간 경로 (05-22).
- butler 스킬·설정 (05-21), 통합 에이전트 작업 공간 + 매니퓰레이터 노드, former 설정 (05-28).

## 2026-03 ~ 04 — 스킬 확장·시뮬레이션·Docker 도입

- **Docker 도입** (04-09): `Dockerfile` + `run_robo_claw_docker.sh`, 시뮬레이션/브링업·CycloneDDS/nav2 파라미터 정비.
- 비전 스킬 마킹 개선, 맵/비전 스킬 고도화, 내비게이션 목적지 별칭 처리.
- **MCP 어댑터 + 옵저버빌리티**, 매니퓰레이션 런타임 통합 (04-29), CI·문서·채널/코어 통합 (04-30).
- Go 메신저 클라이언트 제거 — 채널을 Python으로 일원화 (04-16).

## 2026-02 — 초기 구현

- 에이전트, LLM 브릿지, ROS 메시지/서비스/액션, 핵심 시스템 컴포넌트 초기 구현 (02-24).
- 맵/시스템/비전 스킬 + 관련 설정·테스트 코드 (02-24).

---

## 부록 — 상세 노트: RAG 위치 지식 개선 (2026-08-03 ~ 2026-08-14)

주제: RAG 위치 지식 신뢰성 개선(자기 오염 제거·좌표↔이름 양방향·사이트 일반화), 검증 자동화 도구, 도커 배포 식별성.

### 2026-08-03

- **검증 인프라 신설**: `validation/` 폴더 + First-Use 시나리오 테스트케이스 추가.

### 2026-08-04

- **RAG 위치 지식 개선 P1~P5 (1차)**
  - P1: 조회성 스킬(rag_search/rag_add/get_status 등)을 학습 에피소드로 저장 안 함 + 검색에서 `skill_episode` 제외.
  - P2: 위치를 `type=location`으로 구조화 저장 + 이름 조회 정규화(대소문자·공백 무관).
  - P3: 점수 동률 시 안정적 사실(위치)이 최신 관찰/로그에 밀리지 않도록 랭킹.
  - P4: 좌표→이름 역방향 조회 `identify_location` 스킬 신설.
  - P5: 위치 결과를 metadata 좌표로 자연어 요약 + 내부 스킬 로그 접두어 은닉.
- **로봇 없이 도는 회귀 테스트** `validation/rag_regression/`(pytest) + 실행 스크립트.
- **`robo_claw_cli launch --docker`** 에 `--image-tag` / `--pull` / `--use-grpc` 플래그 추가(자동 테스트 gRPC 연동, 옛 이미지 재사용 방지).
- 로봇 실행 스크립트 정리, 개선 문서(`docs/rag-improvements/CHANGES.md`, `CONFERENCE.md`) 작성.

### 2026-08-05

- **`SKILLS.former.md` 갱신**: "…로 이동해" → `navigate_to` 라우팅 지침, "현재 위치 이름" → `identify_location` 우선 지침.

### 2026-08-07

- **실기 결과 반영 수정**
  - `navigate_to`가 남기는 `navigated_coordinate` 로그를 위치 질의 검색에서 제외.
  - 자유형식 저장(키 `location`, 영어 값 등)도 시맨틱 맵에 동기화되도록 키 인식 확장.
  - 좌표→이름 **역방향 조회 폴백**(시맨틱 맵에 없으면 RAG 위치 항목 스캔, 텍스트의 `x=…,y=…`·이름 파싱).
  - **위치 저장 표준화**: 사이트/언어와 무관하게 `location_name/x/y/type=location`으로 정규화.
- **외부 호스트용 gRPC 테스트 클라이언트 `validation/talk/robo_talk.py`** 신설(시나리오 자동 실행 + 대화형). 로봇/설정서버 비의존.
- 자동 테스트 스크립트(`run_scenario_test.sh`), README/가이드 갱신.
- **도커 이미지 버전 배너**: 빌드 날짜·git 커밋·태그를 이미지에 구워 컨테이너 시작 로그에 표시(`/etc/robo_claw_version`). 빌드 스크립트가 자동 주입, 날짜는 로컬 시간 포맷.

### 2026-08-10

- **스킬 라우팅 개선 및 대화형 쿼리 처리** (`update` 브랜치 병합).
- **RAG 점수 프로브 `validation/talk/rag_score_probe.py`**: 에이전트와 동일 임베딩으로 Qdrant 점수 분포를 뽑아 `RAG Score Threshold`를 데이터 기반으로 산출.
- **위치 재저장 중복 제거(dedup)**: 같은 이름 위치를 다시 저장하면 이전 RAG 항목을 삭제(좌표 충돌·중복 누적 방지).
- 추가 오염 차단: `identify_location`을 비학습 스킬로, `skill_lesson`을 사용자 검색에서 제외.
- **메모리 완전 초기화 스크립트 `validation/reset_memory.sh`**: 로컬 미러 + 시맨틱 맵 + Qdrant 컬렉션을 함께 삭제(로컬 미러+reconcile로 삭제 데이터가 되살아나는 문제 대응).
- **RAG 하이퍼파라미터/임베더 검토**: `num_predict` 상향(512→1024+), 임베더 8B→경량 다운사이즈, RAG `Score Threshold≈0.6`·`Top-K=4` 권장(프로브 데이터 기반).
- **도커 버전 배너 날짜 포맷**을 로컬 시간(`date +'%Y-%m-%d %H:%M:%S %Z'`)으로 통일. `changenote.md` 신설.
- **병합 복구 (update ↔ main)**: `7fc5ef5` 병합이 어긋나게 합쳐 발생한 회귀 2건 해결.
  - `rag.py` 충돌 마커·유실(코드 274줄, `IdentifyLocationSkill` export, `import re/math`, `_SKILL_LOG_PREFIX`, P5 요약/sanitize, `location`/`place` 키) **복원**.
  - `types.LLMPlanResult`에 **`parse_failed` 필드 추가** — 파서가 넘기던 인자가 데이터클래스에 없어 **매 LLM 계획 파싱이 `TypeError`로 실패**(원시 `{"reasoning":…}` 노출·스킬 미실행·"저장 기능 없다"·타임아웃)하던 회귀의 근원. 로봇 종류 무관(Former·Stretch3 공통).
  - 검증: `test_llm_plan_parsing` 16개 전부 통과, 유닛 302 passed(ROS 제외), `rag_regression` 16 passed.
- **robo_talk 리포트 로그 강화**: 각 스텝의 응답 전문·`result_json`·지연시간(latency)·판정 사유를 기록하고, 리포트에 "상세 로그" 부록 추가(회귀 진단용).
- **⭐ 런타임 회귀 진짜 원인 규명·근본 수정 (프롬프트 조립 `.format` → `.replace`)**: 프롬프트 조립부 `assemble_static_prompt_base`가 `base.format(skill_list=...)`를 쓰는데, 프롬프트 본문의 리터럴 JSON 예시(`{"skill": ...}`)를 **치환 필드로 오인** → **매 LLM 계획 호출마다 `KeyError: '"skill"'`** → "해석 오류"로 모든 태스크 실패(원시 reasoning 노출·DEADLINE_EXCEEDED). 실기 `docker logs`의 `Failed to parse LLM response: '"skill"'`로 확증.
  - **수정**: `.format()` → `.replace("{skill_list}", ...)` 단순 토큰 치환으로 변경 → 기본 프롬프트든 **커스텀 프롬프트 파일이든** 어떤 중괄호가 있어도 안전(버그 계열 원천 차단). 예시 블록의 escape용 `{{`/`}}`는 원래 `{`/`}`로 환원.
  - (좌표 이동만 직접 라우팅으로 플래너를 우회해 동작하던 것.)
  - 정정 이력: 처음엔 `LLMPlanResult.parse_failed`(유닛 크래시)로, 이어 해당 예시 한 줄 escape로 접근했으나 불충분/부적절. 파일 기반 프롬프트까지 커버하려면 `.replace` 치환이 정답.
- **robo_talk AI Config env 자동 탐색**: `--env`를 추측(기본값 하드코딩)하던 것을 제거하고, 미지정 시 config 서버 `GET /api/v1/configs`에서 **`is_active=true` 설정을 찾아 실제 env를 자동 인식**(예: `0047_w2_2f`). robot+env 명시 시엔 정확 조회. 조회 실패 메시지도 명확화.
- **robo_talk 리포트 가독성/메타데이터 개선**: (1) 스텝별 지연 표기를 `(…ms)` → **`소요 N.Ns`** 로 명확화(요약표 컬럼도 "소요"), (2) 실행 **시작/종료 시각(초 포함) + 총 소요** 출력(콘솔·리포트), (3) 리포트 상단에 **AI Config 요약**(프로파일, LLM 모델·파라미터, 임베딩 모델·base_url, RAG collection/top_k/threshold)을 config 서버(`/api/v1/configs/active`)에서 조회해 출력(`--config-url/--robot/--env`, `--no-config`).
- **좌표 없는 위치 저장 보완 (약한 LLM 대응)**: gemma4:e4b 등 약한 모델이 `rag_add`에 위치 이름만 넣고 좌표를 빠뜨리는 경우(`{"location_name":"충전대"}` 좌표 없음) → `RAGAddSkill`이 **로봇 현재 pose(get_map_pose)로 x/y를 자동 보완**하고 `type=location` 태깅. "현재/그 위치를 X로 기억" 의도상 현재 좌표가 곧 그 장소 좌표. → 좌표 질의·역방향 조회가 모델 성능과 무관하게 동작. (실기 e4b: PASS 13→ 좌표 저장 이슈 4건 해소 기대)

### 2026-08-11

- **위치 저장 유실 근본 수정 (충전대 케이스, 실기 데이터로 규명)**: gemma4:e4b가 "현재 위치를 충전대로 기억"을 `{text:"현재 위치는 충전대입니다", metadata:{location_type:"Charging Station"}}`처럼 **이름키·좌표 없이** 저장 → `_location_name_from_metadata`가 `location_type` 키를 인식 못 해 **pose-보완·시맨틱 동기화가 모두 스킵**되어 충전대만 유실(거실·키친은 LLM이 좌표를 줘서 정상). 실기 시맨틱 맵/Qdrant 덤프로 확증(충전대 부재, 시맨틱 nav도 "충전대 못 찾음"→find_reachable_places 열화 폴백).
  - **수정1** `_location_name_from_text` 강화: 이름이 '위치' **뒤**에 오는 문형("현재 위치는 충전대입니다")도 추출(기존 '위치' 앞 문형 + 지시/부사어 '현재/이/그…' 오검출 제외). 8개 문형 검증.
  - **수정2** pose-보완 발동 조건 확장: 이름 출처를 표준 키 → **텍스트 문장** → 비표준 키(`location_type`/`category`)까지 넓혀, 어떤 저장 형태든 좌표 없으면 현재 pose로 보완 + `location_name`/`type=location` 태깅 → 시맨틱 동기화·양방향 조회 복구.
  - 회귀 테스트 +1(총 18): `test_location_save_nonstandard_key_and_text_name`.
  - 남은 tc4/tc12 실패는 **정방향 좌표질의를 역조회/상태 스킬로 보내는 LLM 라우팅**(약한 모델) — 저장은 정상화되어 rag_search 경로로는 답변 가능.
- **SKILLS 라우팅 지침 추가 (tc4/tc12 라우팅 대응)**
  - config 서버 `database/seeds/SKILLS.former.md`(roboclaw_config_server, 실제 운영 전문) 보완: 기존 가이드에 **누락돼 있던 `identify_location`(좌표→이름 역조회)·`rag_add`(위치 기억) 스킬 항목 추가**, `describe_surroundings`가 "여기가 어디야?"로 안내돼 `identify_location`과 충돌하던 것을 정리(주변 사물 파악 vs 기억된 장소명 판별 구분), `rag_search`를 "이름→좌표 정방향"으로 명확화. **"위치·장소 요청 라우팅" 표**(이동/기억/정방향 좌표질의/역방향 이름질의) 추가 + 행동지침 갱신. (시드는 DB 최초 시드에만 반영 → 활성 config는 아래 스크립트로 적용)
  - **활성 config 적용 스크립트** `validation/talk/apply_skills_guide.py` 신설: UpdateConfig가 전체 레코드 교체(GORM Save)이므로 현재 config를 GET → `skills_content`만 교체 → 통째로 PUT(마스킹 비밀값 `********`는 서버가 복원). `is_active` 자동 선택/`--env`/`--config-id`, `--dry-run`, 백업 저장 지원.
- **RAG 검색 품질 개선 (qwen3-embedding 비대칭 임베딩 + threshold/top_k 정정)**
  - **원인**: 임베더가 쿼리·문서를 동일하게(대칭) 임베딩 → Qwen3-Embedding(instruction-tuned)의 성능을 못 살려 점수가 0.5~~0.72로 압축, 정답/오답 마진이 얇음. 게다가 기본 `rag_score_threshold=0.7`이라 프로브의 실제 정답(0.54~~0.66)이 대거 잘려 검색이 빈손이 됨. (실기 프로브 `robo_claw_former_0047_w2_2f`로 확증; 컬렉션은 1024-dim/Cosine 정상)
  - **② 비대칭 임베딩**: `Embedder.embed_query()` 추가(base 기본=대칭 폴백), `OllamaBridge`가 `qwen3-embedding*` 모델일 때만 쿼리에 `Instruct: …\nQuery: …` 지시문 부착(문서는 원문 → **재인덱싱 불필요**). 검색 경로(`memory_manager/rag.py`)를 `embed_query`로 연결(임베더 더블 대비 getattr 폴백). options_json `embed_query_instruction`로 지시문 override 가능.
  - **① threshold/top_k**: 기본값 `rag_score_threshold 0.7→0.55`, `rag_top_k 2→5` (agent `params.py` + config 모델 gorm default). **활성 config는 API로 갱신 필요**(rag_score_threshold=0.55, rag_top_k=5).
  - **재측정 결과(비대칭 ON)**: 위치질의 정답이 전부 필터 후 1위로 상승, 오답(다른 위치 type=location ≤0.467)과 마진 0.06~0.17로 확대(이전 ~~0.01/역전). 정답 위치 점수 0.586~~0.722(실 "위치" 질의 ≥0.642) → 분리선 **0.55 최적**. qwen3-embedding:0.6b **유지로 충분**(4b 불필요).
  - 검증 도구: `rag_score_probe.py`에 `--query-instruction`/`--no-query-instruction` 추가(에이전트와 동일 지시문으로 재측정·A/B). 회귀 +1(총 19): `test_ollama_embed_query_asymmetric_instruction`.

- **위치질의 결정론적 라우팅(A) + 옛 무좌표 항목 정리(B)** — 실기 report-112047 + Qdrant 스냅샷 분석 반영
  - **분석**: 스냅샷(08:58, 수정 이전)은 거실/키친=location_name+좌표 정상, **충전대=`location_type`만(좌표 없음)** 확인. 현재 실기는 저장 정상(tc13 이름기반 이동 성공)이나 tc4/tc12/tc17 실패는 **e4b가 정방향 "좌표 알려줘"를 identify_location/get_status로 오라우팅하거나 실행 없이 서술만** 하는 문제(SKILLS 텍스트만으론 부족).
  - **A. planner 결정론적 라우팅**(`agent_node/planner.py` `_route_location_query`, 단일 스텝 한정): 정방향("<등록 장소> 위치/좌표…")→`rag_search(query=장소)` 강제, 역방향("여기 어디/현재 위치 이름/좌표로 이름 파악")→`identify_location` 실행 강제. 실제 프롬프트 10종 판정 검증.
  - **A2. `RAGSearchSkill`이 시맨틱 맵 좌표를 확정 반환**: 쿼리에 등록 장소명이 있으면 `get_object_location` 기반 좌표를 RAG 점수/오염과 무관하게 직접 답변(위치의도/장소명일 때만 개입, 아니면 일반 검색 폴백).
  - **B. dedup 강화**(`_dedup_prior_locations`): `type=location`+location_name뿐 아니라 `location_type`/`category`·텍스트에서 뽑은 이름까지 비교해 옛 무좌표 항목(예: `{location_type:"Charging Station"}`)도 제거. 로그 타입은 보존.
  - 회귀 +2(총 21): `test_rag_search_returns_semantic_map_coords`, `test_dedup_removes_stale_location_type_entry`. (planner는 `robo_claw_msgs` 의존으로 유닛 대신 정규식 독립검증)
  - 후속: 실기에서 `reset_memory.sh`로 08:58 잔재 제거 후 재검증 권장.
  - **정방향 라우팅 query 강제 보강**(report-130407 반영): A가 LLM이 이미 rag_search를 고르고 query가 비어있지 않으면 그대로 뒀는데, e4b가 query를 장소명 없이 재작성(예: "위치", 영어)하면 A2 시맨틱 단락도 RAG 검색도 실패("검색 결과가 없습니다"). → 정방향+등록 장소일 때 **query를 등록 장소명으로 항상 치환**하도록 수정. (tc4만 우연히 통과하고 tc3/7/10/11/12가 실패하던 비결정성 제거)

- **robo_talk AI Config 조회 디버깅·인증 지원**: `_get_json`이 실패를 조용히 삼켜 "조회 실패"만 뜨던 것을 개선. (1) 실제 사유(HTTP 상태·본문·네트워크 예외)를 `_LAST_CONFIG_ERROR`에 캡처해 헤더/`stderr`에 노출, (2) **Bearer 토큰 지원**(`--config-token`, 환경변수 `ROBOCLAW_CONFIG_TOKEN`/`ADMIN_TOKEN`/`DEVICE_TOKEN`) — config 서버가 AdminToken/DeviceToken을 설정했으면 API는 토큰 없이는 401. `/configs/active`=device·admin, `/configs`(자동탐색)=admin. (3) 감싸진 응답(`{"configs":[...]}`)·`is_active` 없음도 구분 처리.

- **Stretch3 로봇 설정 신설 (config 서버 시드)**: `roboclaw_config_server`에 Former 기준으로 **Stretch3(모바일 매니퓰레이터)** 프로파일 추가. 시드 프롬프트 파일 4종(`SKILLS/ROBOT/TROUBLESHOOTING.stretch3.md`, `ROBOT_LIMITS.stretch3.json`)을 Stretch3 특성(오른쪽 신축 팔·그리퍼·팬틸트 헤드, 이동 전 자동 stow·모드 전환, 조작 스킬 `adaptive_pick_object`/`pick_front_object`/`vla_*`/`place`/gripper 등)에 맞게 작성(위치·RAG 라우팅 섹션은 로봇 무관이라 공유). `db.go`에 stretch3 config·시나리오 시드 블록 추가(LLM=former 동일 gemma4:e4b, **RAG 활성** + qwen3-embedding:0.6b + Qdrant `robo_claw_stretch3`, top_k 5·threshold 0.55, 매니퓰레이션 시나리오 활성). 문서 갱신: config 서버 `README.md`/`README_EN.md`/`docs/*` 시드 열거에 stretch3 추가, robo-claw `README.md`에 stretch3 기동 안내. (시드는 빈 DB에서만 실행 → 기존 DB는 API/UI로 생성)

- **호스트용 로봇 초기화 스크립트 `utility/reset_robot_state.sh`**: 매 테스트 전 로봇에서 수동으로 하던 `rm -rf ~/.robo_claw/config_cache` + `rm -rf .../agent_workspace/memory`를 **호스트에서 SSH로 한 번에** 실행. `--dry-run`/`--restart`/`--robot`, 안전 가드(위험 경로 거부), 확인 프롬프트. readme: `utility/reset_robot_state-readme.md`. (Qdrant까지 지우는 깊은 초기화는 로봇의 `validation/reset_memory.sh`)

- **저장 오라우팅·정방향 근거 부족·참조 로깅 (실기 문제 1·2·3 대응)**
  - **문제1·2(저장이 검색으로)**: "그 위치를 거실로 저장해"가 rag_add가 아니라 rag_search로 라우팅돼 "검색 결과가 없습니다"로 실패(클라이언트/대화맥락 따라 비결정). → planner `_route_location_query`에 **저장 명령 결정론적 라우팅** 추가: "…<장소>로 기억/저장/등록" → `rag_add(location_name=<장소>)` 강제("그 위치"는 pose 보완이 현재 좌표로 채움 → 맥락 없이도 동작, robo_talk·GUI 동일 결과).
  - **문제3(근거 있는데 못 찾음)**: 키친이 `type=location`은 없고 `navigated_coordinate`("…(키친 (-5.15,-1.18))")만 있는데 rag_search가 이를 제외해 실패. → 정방향 조회(`_semantic_location_answer`)를 **벡터스토어까지 스캔**하도록 확장: 시맨틱맵 → `type=location` → `navigated_coordinate` 순 우선, 괄호 안 장소명(`_PAREN_NAME_RE`)도 인식. 시맨틱맵 desync(문제1의 다른 양상)에도 스토어의 `type=location`을 찾아 응답.
  - **참조 Qdrant 로깅(추가 요구사항)**: rag_search가 참조한 항목(id·score·type·근거·좌표)을 에이전트 로그(`[rag_search] … 참조 N건: id=… score=… type=…`)와 결과 `referenced` 필드에 남김. robo_talk 리포트가 result_json의 `referenced`를 **"참조 Qdrant 항목"** 라인으로 강조 출력.
  - 회귀 +2(총 23): navigated_coordinate 근거 조회, type=location desync 조회. 저장 라우팅 정규식 6종 독립 검증.

- **Qdrant 저장 목록을 시간순으로 (ID를 UUIDv7로)**: Qdrant는 **삽입 순서를 보존하지 않고** scroll/대시보드가 **point ID 순**으로 반환하는데, ID가 랜덤 `uuid4`라 컬렉션이 시간과 무관하게 뒤섞여 보였다(디버깅 난이도↑). → `vector_store/base.py`에 `new_entry_id()`(**UUIDv7**: 상위 48b Unix ms + 12b **단조 카운터** + 62b 랜덤) 추가하고 Qdrant·로컬 미러 양쪽 기본 ID 발급을 교체 → **ID 순서 = 시간 순서**가 되어 대시보드에서도 시간순으로 보인다. `list_entries()`는 두 스토어 모두 `_sort_entries_by_time()`으로 **시간순(오래된 것 → 최신)** 반환(레거시 uuid4 항목이 섞여 있어도 목록은 시간순).
  - 단조 카운터가 필요한 이유: 같은 밀리초에 여러 건 저장(파일 청크 적재 등) 시 랜덤 비트로 순서가 뒤집힘 → RFC 9562 권장 방식으로 `rand_a` 자리를 카운터로 사용. 시계 역행에도 단조성 유지, 스레드 안전(락).
  - 검색 랭킹에는 영향 없음(`_sort_by_score_and_recency` 그대로) — 가독성/디버깅 목적. **기존 데이터의 ID는 그대로**이므로 대시보드를 완전히 시간순으로 보려면 리셋 후 재수집 필요(`reset_memory.sh`).
  - 회귀 +1(총 24): UUIDv7 형식·정렬성 + `list_entries` 시간순. 스트레스: 5000건 동일-ms 생성 정렬 일치·중복 0, 8스레드 동시 생성 중복 0.

- **kitchen/키친**: LLM(gemma4:31b)이 음차어 "키친"을 "kitchen"으로 번역해 저장 → 라벨 언어 불일치(기능은 정상). 교차언어 임베딩 자체는 양호.
- **이름 기반 이동 산발적 오선택**: "거실로 이동해"를 가끔 조회로 처리 — 플래너/LLM 계층 이슈(RAG 아님).
- **권장 RAG 설정**: 비대칭 임베딩 적용 후 재측정 기준 **`Score Threshold = 0.55`, `Top-K = 5`** (코드 기본값도 이 값). 정답 위치 0.586~0.722 vs 오답 위치 ≤0.467 사이의 분리선. (이전 대칭 임베딩 기준 0.6/4 권고는 폐기)

### 2026-08-12

- **report-20260812-110850 실패 5건(tc03/07/10/11/12) 원인 규명 — 코드가 아니라 배포·설정**
  - **데이터 확인**: Qdrant 스냅샷(15 포인트) payload 디코딩 결과 `type=location` 3건이 질의보다 **먼저** 정상 저장돼 있었다. 충전대(3.81, 0.71) 11:09:45 / 거실(0.83, 0.69) 11:11:01 / 키친(-5.18, -1.13) 11:11:46, 모두 `location_name`+x/y 정상. tc03은 저장 12초 뒤 질의였다.
  - **코드 검증**: 위 15건을 그대로 스텁 스토어에 넣고 현재 `RAGSearchSkill.execute()` 를 실패한 5개 프롬프트로 실행 → **시맨틱맵/스토어 조합 4가지 중 3가지에서 정답**, "시맨틱맵 비어있음 + `list_entries()` 예외" 조합에서만 "검색 결과가 없습니다"가 재현됐다. 즉 현재 소스는 이 실패를 내지 않는다.
  - **배포 낙후 확증**: 스냅샷 `mutable_id_tracker.mappings` 에서 15개 point ID 를 추출해 UUID 버전 니블 확인 → **전부 v4**. UUIDv7 커밋(`de321fa`, 10:52)이 테스트(11:08) 빌드에 없었다. → **HEAD 로 이미지 재빌드 후 재측정이 선행 조건.**
  - 진단 방법: `docker logs` 의 `Routing forward location query to rag_search` / `[rag_search] 정방향 위치조회` 라인 유무로 배포 버전을 즉시 판별할 수 있다.

- **활성 config RAG 파라미터 반영 (실제 적용 완료)**: config id=14(`former`/`0047_w2_2f`)가 `rag_score_threshold=0.6`, `rag_top_k=4` 로 남아 있어 코드 기본값(0.55/5)과 불일치했다. threshold 0.6 은 프로브 측정 정답 하단(0.586~0.6)을 잘라내 "데이터는 있는데 검색 결과 없음"을 만든다. → **0.55 / 5 로 갱신 완료**(에이전트 재기동 시 적용).
  - 신규 스크립트 `validation/talk/apply_rag_params.py`: GET → 필드 교체 → 전체 PUT 라운드트립(UpdateConfig 가 GORM Save 로 통째 교체하므로), 백업·`--dry-run`·`--env`/`--config-id` 지원.
  - **`apply_skills_guide.py` 버그 2건 동시 수정** (이 스크립트도 그대로면 동작 불가였다):
    (1) 관리자 API 는 `/api/v1/**admin**/configs/:id` 인데 `/api/v1/configs/:id` 를 호출해 **404 route not found**. (2) config 레코드 PK JSON 키가 gorm.Model 임베드로 **`ID`(대문자)** 인데 `chosen["id"]` 로 읽어 `KeyError`. → `_config_id()` 로 `ID`/`id` 양쪽 수용.
  - 실측 주의: 운영 DB 의 former config 7건이 **모두 `is_active=true`** 다. 자동 선택은 위험하므로 `--env` 명시를 강제한다.
  - 백업 산출물은 `.gitignore` 에 추가(`validation/talk/config-*.bak.json`).

- **`list_entries()` 스캔 비용·무손실 수정 (잠재 재발 원인 제거)**: `QdrantVectorStore.list_entries()` 가 항상 `with_vectors=True` 로 스크롤해 1024-dim 벡터를 전부 끌어왔다. 위치 질의는 **매번** 이 전체 스캔을 타므로 컬렉션이 커지면 `timeout=5s` 를 넘기고, 호출부가 예외를 폴백 처리하며 조용히 순수 벡터검색으로 열화된다 → 이번 리포트와 동일한 증상이 데이터 증가만으로 재발한다. 더 나쁜 것은 벡터가 list 로 안 오면 `continue` 로 **항목을 통째로 버려** 스캔 결과가 빈손이 되던 점.
  - `list_entries(*, with_vectors=False)` 로 프로토콜 변경(base/qdrant/local/dual 일괄). 벡터가 실제로 필요한 `DualVectorStore.reconcile()` 과 `reindex_knowledge()`(실패 시 원본 복구 경로)만 `True`.
  - 단일 벡터가 `{"": [...]}` 형태로 오는 클라이언트/서버 조합도 수용(`_as_vector`).
  - 벡터를 요청했는데 못 받은 항목은 조용히 버리지 않고 `logger.warning` 후 스킵.

- **위치 조회 실패를 숨기던 예외 처리 로깅화** (`skills/system_skill/rag.py`): `_query_target_names`/`_best_location_for_targets`/`_dedup_prior_locations`/`_nearest_named_location_from_rag` 의 `except Exception: pass|return` 4곳이 스토어 스캔 실패를 무음 처리해, "등록된 장소명을 못 알아보고 임베딩 점수에만 의존"하는 상태가 로그에 전혀 남지 않았다. → 전부 `logger.warning` 추가.

- **`referenced`(참조 Qdrant 항목)가 리포트에 도달하도록 result_json 전달 경로 복구**: robo_talk 에 "참조 Qdrant 항목" 출력을 추가했지만 **원리적으로 절대 표시될 수 없었다** — 리포트에 `result_json` 블록이 0개인 것이 그 증거. 원인은 3단 유실:
  (1) `ros_future_utils.send_execute_task()` 가 액션 result 의 `skill_results` 를 버리고 success/message 만 반환, (2) `channel_node.on_msg` 가 `(bool, str)` 만 반환, (3) `grpc_server.SendCommand` 가 `CommandResponse(success, message)` 로 `result_json` 미설정.
  - 신규 `robo_claw_channel/task_result.py`(ROS/gRPC 비의존 순수 모듈): `build_task_result_json()` 은 스킬별 `result_json` 을 **파싱해 객체로** 심어 클라이언트가 중첩 탐색으로 `referenced` 에 닿게 한다. base64 이미지 등으로 16KB 초과 시 본문은 버리고 진단 키(`referenced`/`count`/`message`)만 보존. `unpack_callback_result()` 는 문자열/2-튜플/3-튜플 콜백을 모두 수용(하위 호환).
  - 유닛 +10: `src/robo_claw_channel/tests/test_task_result.py`.

- **설계안 문서 `docs/rag-improvements/LOCATION_STORE_DESIGN.md`**: 위치 지식(이름↔좌표)을 RAG에서 분리하는 PlaceStore(SQLite) 설계. 진실 소스 단일화, 기동 시 RAG `type=location` → PlaceStore 부트스트랩(메모리 디렉터리만 지우는 `reset_robot_state.sh` 가 구조적으로 만드는 desync 차단), 장소명 판정을 LLM 재작성 `query` 가 아니라 원본 instruction 기준으로, 플래너 라우팅의 `len(chain)!=1` 게이트 제거. 수용 기준은 **`score_threshold=0.99` 에서도 위치 질의 정답**(임베딩 무관성 증명) — 현재 코드로는 통과 불가.

- 회귀 +5(총 29): `list_entries` 기본 벡터 미요청 / 벡터 없는 스캔에서 항목 무손실 / `with_vectors=True` 사용 가능한 벡터 / named-vector dict 수용 / 로컬 미러 동일 규약.
- 검증: `rag_regression 29 passed`, 에이전트 유닛 `302 passed`(ROS 의존 4건은 기존과 동일한 환경 실패), 채널 `15 passed`.

#### 2026-08-12 (2차) — "이동은 되는데 위치 조회는 실패" 원인 확정 및 라우팅 강화

- **배포 버전 확정(정정)**: 실기 `/etc/robo_claw_version` = `git_commit=657a83c`, `build_date=12:35:49`. `657a83c`는 `de321fa`의 자손이며 **1차 분석의 수정이 전부 포함된 빌드**다. → 1차에서 "이미지 낙후"로 본 진단은 **오판**이었다(근거로 쓴 UUIDv4는 아래 dual.py 버그 때문). 데이터는 라이브 Qdrant 직접 조회로 확인(13건, `type=location` 3건이 질의보다 먼저 정상 저장).

- **핵심 원인 — 두 경로의 보장 수준 차이**
  - `navigate_to._resolve_target_coordinates`: 시맨틱맵 → RAG(threshold **0.35**, 쿼리=깨끗한 장소명) → **`list_entries()` 전수 스캔 + 문자열 매칭**(`_extract_coords_from_entry`). **임베딩 무관·결정론적** → 이름 기반 이동은 100% 성공(tc13~15).
  - `rag_search`: 결정론적 라우팅이 발동하지 않으면 **LLM이 작성한 query + threshold 0.55** 에만 의존 → 실패(tc03/07/10/11/12).
  - 라이브 13건으로 실증: 시맨틱맵 비움 + 벡터검색 0건이라는 최악 조건에서도 **현재 코드는 3개 프롬프트 전부 정답**. 즉 코드 자체가 아니라 **라우팅 미발동 + LLM query에 장소명 누락**의 조합이 실패 조건이었다. tc04는 통과/tc03은 실패한 이유도 이것(같은 미발동 상태에서 query 문구 운).

- **라우팅 미발동 원인 2건 수정** (`agent_node/planner.py`)
  - **(A) 판정 소스 비대칭**: `_known_place_in_text` 가 **시맨틱 맵만** 봤다. `rag_search` 쪽(`_query_target_names`)은 벡터스토어까지 보는데 플래너는 아니어서, 시맨틱맵 desync 시 라우팅이 **조용히** 미발동했다(`reset_robot_state.sh` 가 메모리 디렉터리만 지우고 Qdrant는 남기는 운영 절차가 이 상태를 만든다). → 시맨틱맵 + 벡터스토어를 함께 보는 공용 헬퍼 `known_place_in_text()` 로 통일.
  - **(B) `len(chain) != 1` 게이트**: 약한 LLM이 `[get_status, rag_search]` 처럼 2스텝을 뱉으면 교정이 **전부** 무력화됐다. → **모든 스텝이 조회 스킬인 계획**(`_is_pure_lookup_chain`)까지 대상 확대. 물리 동작이 섞인 계획("거실 위치로 이동해")은 그대로 둬 사용자 요청을 삼키지 않는다.

- **tc17 수정 — 스킬 없는 계획에서도 위치 질의 확정 처리**: e4b가 툴 호출 대신 툴 호출을 **서술**하는 산문을 냈다(실측: `"…[identify_location] 스킬을 사용하겠습니다 …"`). 이때 `plan.skills` 가 비어 `planner.py` 의 `if not plan.skills:` 분기에서 루프를 나가므로 `_route_location_query` 는 **호출될 기회조차 없었고** 산문이 그대로 최종 답변이 됐다. → 라우팅 로직을 `_location_chain_for()` 로 분리해 **빈 계획 분기에서도** 위치 질의면 결정론적으로 스킬을 강제 실행한다(형식 재시도로 왕복을 늘리지 않음).

- **`dual.py` UUIDv7 누락 수정 (1차 오판의 근원)**: `de321fa` 가 `qdrant.py`/`local.py` 만 바꾸고 `DualVectorStore.add()` 의 `uuid4()` 를 놓쳤다. 미러가 켜진 배포에서는 dual 이 먼저 ID를 발급해 양쪽에 내려보내므로 **UUIDv7 도입 효과가 통째로 무효**였다(실기 컬렉션 point ID 13/13 v4 → 이를 근거로 "이미지 낙후"라 오판). → `new_entry_id()` 사용. 부수 확인: 실기가 미러 ON 으로 동작 중 → 활성 config 의 `rag_local_mirror=false` 가 노드까지 전달되는지 별도 점검 필요.

- **레이어링 정리**: 장소명 추출 헬퍼(`_location_name_from_metadata`/`_location_name_from_text`/`_entry_place_names`/`_entry_all_names`/`_PAREN_NAME_RE`/`_FWD_SKIP_TYPES`)를 `memory_manager/semantic.py` 로 이전. planner 가 `skills.system_skill.rag` 를 import 하면 (a) 의존 방향이 거꾸로이고 (b) `skills/system_skill/__init__` 이 `geometry_msgs` 를 끌어와 ROS 없는 환경에서 조용히 실패한다(테스트가 실제로 이 실패를 잡아냈다). `rag.py` 는 같은 이름으로 재노출해 호출부·기존 테스트 무영향.

- **모델(gemma4:e4b) 영향 검증**: 근본 원인 아님. 근거 — (1) 데이터는 질의 전 정상 저장, (2) 이름 기반 이동은 LLM 무관 경로로 100% 성공, (3) 현재 코드는 벡터검색을 꺼도 정답. 단 **영향은 실재**: `first-use-scenario-test-gemma4_31b-20260807-p1p5.md` 를 보면 **31b는 동일 프롬프트("충전대 위치를 알려줘" 3회, "거실의 위치를 알려줘")를 라우팅 없던 08-07 빌드에서도 통과**했다(검색 경로가 LLM의 query 작성 품질에 직결되므로). e4b는 tc04 PASS/tc12 FAIL 처럼 **동일 프롬프트에서도 결과가 갈린다**. 반면 31b도 컬렉션에 노이즈가 쌓이자 case 24에서 `navigated_coordinate` 로그를 상위 결과로 물어오고 case 25는 "거실 위치가 없음"으로 열화 → **모델 상향은 여유를 늘릴 뿐 정확성을 보장하지 않는다**. tc17(산문 응답)은 명확히 모델 형식 준수 실패이며 위 코드 수정으로 무해화.

- 회귀 +6(총 35): desync 상태 라우팅 발동 / 2스텝 조회 계획 교정 / navigate_to 비침해 / 빈 계획 위치질의 강제 / 저장 라우팅 존중 / dual UUIDv7.
- 검증: `rag_regression 35 passed`, 에이전트 유닛 `302 passed`(ROS 의존 4건은 환경 실패), 채널 `15 passed`, ruff 신규 findings 0.

#### 2026-08-12 (3차) — 실기 6건 실패의 진짜 원인: **호스트 소스 bind mount**

- **원인 확정**: `robo_claw_cli launch --docker` 가 **로봇 호스트의 소스 트리**를 컨테이너 install 공간 **8곳**(agent·channel × python3.10/3.12 × site/dist-packages)에 bind mount 한다. 따라서 **파이썬 코드는 이미지에서 오지 않는다** — 이미지 install 산출물은 마운트에 가려진다. 로봇 호스트 체크아웃이 `git pull` 되지 않아 옛 코드가 계속 실행됐다.
  - 확증: `docker inspect` 마운트 목록 + `grep -rl new_entry_id /ros2_ws/install` 이 **공집합**(호스트 트리에 수정 없음).
  - 이것으로 전 증상이 일관 설명된다 — 재빌드 3회(12:35/14:48/16:21, `--no-cache` 포함) 모두 무효, point ID 계속 UUIDv4, `Routing forward` 로그 부재, `result_json` 미출현, **31b로 바꿔도 동일**(코드가 안 바뀌었으므로).
  - **Dockerfile 은 정상**이다(`COPY src ./src` 전체 복사 + `colcon build`). 빌드가 아니라 배포(런타임 마운트) 문제였다.
  - 진단 이력 정정: 1차 "이미지 낙후" → 2차 "install space staleness" → 3차 **"호스트 소스 마운트 + git pull 누락"**. 배포 검증을 이미지 배너(`/etc/robo_claw_version`)로만 한 것이 오진의 근인. 배너는 이미지를 설명하며 **실행 코드와 무관**하다.
- **파이썬 변경 반영 절차 확립**: 재빌드 아님 → 로봇에서 `git pull` + `docker restart robo_claw_container`. 재빌드는 의존성·`robo_claw_msgs`·비파이썬 구성 변경 시에만.
- **`robo_talk.py` 리포트 최상단 한 줄 추가/개정**: `- 소프트웨어: 실행코드 <commit> (호스트 체크아웃(<branch>), 미커밋 N개) · 이미지 <commit>/<tag> build <date> · 일치 ✅ | ⚠️ 테스트 기준 … 과 다릅니다 → git pull + 재기동 필요`.
  - `docker inspect` 로 install 공간이 bind mount 인지 판정해 **실행 코드의 출처(호스트 체크아웃 vs 이미지)** 를 자동 구분하고, 그 commit 을 테스트 기준(이 저장소 HEAD)과 비교한다. 4가지 배포 형태(마운트/비마운트 × 일치/불일치) 렌더링 검증.
  - 시나리오 **실행 전** 콘솔에도 출력 → 낡은 코드로 5분 돌리고 나서 알게 되는 낭비 차단. ssh 실패 시 사유 한 줄만 남기고 정상 진행(`--no-build-info`, `--robot-ssh`, `--container`, `--robot-repo`).
  - 스크립트는 base64 로 전달해 ssh·docker exec 다중 따옴표 문제를 제거.
- **후속(미해결)**: install 공간이 python3.10/3.12 **두 벌** 존재해 어느 쪽이 임포트되는지 모호하다(현재는 8곳 모두 같은 호스트 디렉터리를 가리켜 무해하지만, 마운트가 빠지면 즉시 위험). 베이스 이미지 파이썬 버전 단일화 권장.

#### 2026-08-14 — 실기 검증 통과(31b 17/17) + 규칙이 LLM 출력을 훼손하던 문제 정정

- **08-11~08-12 수정의 실기 검증 완료.** `report-20260813-094959-gemma4_31b`: **PASS 17 / FAIL 0**. 작동 근거는 응답 형태다 — tc03/07/11/12 가 `'충전대'의 위치는 x: 3.78, y: 0.86 입니다.` 로, **`지식 N건을 검색했습니다` 접두어가 없다** = `_semantic_location_answer` 가 임베딩을 거치지 않고 확정 답변. tc17(산문 응답)도 통과. 부수 검증: robo_talk 헤더의 `일치 ✅`, 리포트에 `result_json` 블록 최초 출현.
  - `report-20260814-111550`(e4b): PASS 14 / FAIL 3 — **기존 6건은 전부 해소**되고 새 실패 3건은 아래 이동 오라우팅 **1건 + 연쇄 2건**이었다(tc16/17 은 `identify_location` 이 정확히 동작했으나 tc15 가 이동을 안 해 기대값과 어긋난 것).

- **⭐ 규칙이 올바른 LLM 개체명 추출을 덮어쓰던 문제 정정 (설계 방향 수정)**
  - **증상**: 저장된 장소명이 `키친` 이 아니라 **`키친으`** (실측 08-13/08-14 리포트: `'키친으'의 위치는 …`). 파급으로 질의 `키친` 과 이름이 불일치해 **키친만 시맨틱 단락이 안 걸리고** 일반 벡터검색으로 열화됐다.
  - **원인**: `_route_location_query` 의 저장 분기가 규칙 추출값과 다르면 **LLM 값을 규칙 값으로 덮어썼다**. `_SAVE_PLACE_RE` 가 탐욕 매칭이라 조사 `으로` 의 `으` 를 이름에 붙였고(받침 있는 이름은 조사가 `으로` — 키친·주방·현관 …), 그 틀린 값이 LLM의 정확한 `키친` 을 밀어냈다. **규칙 배포 전(~08-12)에는 LLM 값이 그대로 쓰여 이름이 전부 정확했다**(Qdrant 덤프로 확인) — 즉 규칙이 회귀를 만들었다.
  - **수정(우선순위 역전)**: LLM이 `rag_add` + `location_name` 을 냈으면 **이름이 규칙과 달라도 그대로 신뢰**한다. 규칙은 **LLM이 이름을 못 준 경우의 폴백**으로만 동작. 정규식은 폴백 품질을 위해 비탐욕으로 정정하되 **덮어쓰기 권한을 제거**했다.
  - **설계 원칙 명문화**: 발화에서 개체명·의도를 뽑는 일은 LLM의 몫이고 규칙의 몫이 아니다. 한국어 조사·어순·다국어·다단어 이름을 규칙으로 따라잡으려 하면 사이트마다 규칙이 늘고, 이번처럼 정답을 훼손한다. 규칙은 (a) 확정된 조회의 결정론적 실행(이름→좌표), (b) 물리 동작의 안전망 — 두 범주에만 둔다.

- **이동 명령 오라우팅 교정** (`_force_navigation_if_misrouted`): 실측(e4b) `"키친으로 이동해"` 를 **`get_status` 단독**으로 계획해 로봇이 아예 움직이지 않았다(`result_json` 으로 확증). `_LOC_INTENT_RE` 는 `위치|좌표|어디…` 를 요구하므로 이 문장은 위치질의 라우팅에 걸리지 않아 교정 경로가 없었다. tc13/14 는 통과했으므로 **모델의 비결정적 라우팅**이며, 이동은 실패 비용이 크므로(이동 미발생) 안전망을 둔다.
  - 조회 스킬만으로 구성된 계획(또는 빈 계획)일 때만 개입하고, 계획에 이미 이동 스킬(`_MOVEMENT_SKILLS`: navigate_to/patrol/rotate/face_direction 등)이 있으면 손대지 않는다 → `"거실로 이동해서 사진 찍어"` 같은 복합 태스크 보존.
  - **위치질의 교정보다 먼저** 실행한다. `"거실 위치로 이동해"` 처럼 두 조건이 모두 걸리는 문장에서 사용자가 원한 이동을 조회로 바꿔치기하지 않도록.
  - 빈 계획 분기(모델이 산문만 답한 경우)에도 동일 적용.

- 회귀 +5(총 40): 조사 `으로` 문형 5종, 이동 오라우팅 4형태 교정, 복합 태스크 보존, 비이동 지시문 무개입, **규칙이 LLM 이름을 덮어쓰지 않음**(이름 불일치·이름 누락 양쪽).
- 검증: `rag_regression 40 passed`, 에이전트 유닛 `302 passed`(ROS 의존 4건은 환경 실패), 채널 `15 passed`, ruff 신규 findings 0.
- **후속(미해결)**: 규칙 빚을 실제로 갚으려면 (1) PlaceStore 로 이름→좌표 조회를 확정화(위 2026-08-12 설계안 항목 참고), (2) 스킬 파라미터 **JSON 스키마 기반 구조화 출력**(Ollama `format`)으로 전환. 둘이 서면 `_SAVE_PLACE_RE`·문형 정규식(`_NAME_BEFORE_LOC`/`_NAME_AFTER_LOC`/`_NON_NAME_WORDS`) 등 **언어 규칙 범주를 삭제**할 수 있다.

#### 2026-08-14 (2차) — `origin/main` 병합 및 `get_location` 통합

- **병합**: `origin/main`(11 커밋) → `using_gemma4_e4b`(21 커밋). 공통 조상 `9382ea1`, 양방향 발산 상태라 fast-forward 불가. 겹친 파일 7개 중 **텍스트 충돌은 `dual.py` 1개**뿐이었고 나머지는 자동 병합. 되돌림 지점으로 `backup/e4b-before-merge-20260814` 브랜치를 남겼다.
  - **`dual.py` 충돌 해결 — 양쪽 개선을 모두 유지**. main: `search()`/`list_entries()` 가 원격이 정상 응답했지만 비었으면 로컬 미러로 폴백(원격 upsert 실패로 로컬에만 남은 항목 조회 보장). 우리: `add()` 의 `uuid4()`→`new_entry_id()`(UUIDv7), `list_entries(*, with_vectors=False)`. 서로 배타적이지 않아 병합 후 둘 다 동작한다(`reconcile()` 만 `with_vectors=True`).

- **`get_location` 통합 (중복 제거 + 퇴행 방지)**: main 이 이름→좌표 전용 스킬 `GetLocationSkill` 을 신설했는데, 우리 `rag_search` 정방향 단락(`_semantic_location_answer`)과 **조회 순서·응답 문구까지 사실상 동일한 별도 구현**이었다. 결정: **둘 다 유지하고 조회 본체를 공용 함수로 통일 + 라우팅 타겟을 교체.**
  - `GetLocationSkill.execute` 가 자체 구현 대신 **`_best_location_for_targets()`** 를 쓴다. 부수 효과로 퇴행 하나가 막혔다 — main 구현은 `_NON_LOCATION_TYPES` 로 `navigated_coordinate` 를 제외해서, 장소가 `type=location` 없이 **이동 기록만** 남은 경우(실측: 키친) 좌표를 못 찾았다. 공용 함수는 이를 최후 순위로 인정한다(시맨틱맵 > `type=location` > `navigated_coordinate` > 기타).
  - `source` 필드는 main 이 정한 안정 식별자(`semantic_map` / `rag`)를 **유지**하고, 공용 함수의 한글 상세 라벨은 `source_detail` 로 분리했다(main 테스트 계약 보존).
  - **라우팅 타겟 교체**: 정방향 위치질의를 `rag_search(query=장소)` → **`get_location(location_name=장소)`** 로 강제한다. `location_name` 이 명시 파라미터라 의도가 분명하고(rag_search 의 `query` 는 자유 문장), 조회 본체를 공유하므로 동작·문구는 동일하다. `rag_search` 는 원래 목적(관찰·교훈·문서의 퍼지 검색)으로 남는다.
  - `_LOC_READ_SKILLS` 에 `get_location` 추가 — LLM이 `get_location` 을 고르고 `location_name` 을 엉뚱하게 넣은 계획도 "순수 조회"로 인식해 파라미터를 교정할 수 있게 한다.
  - 프롬프트 모순은 main 이 이미 정리해 뒀다(`f5f39c0` 이 `"get_location 같은 등록되지 않은 함수"` 문구를 `"장소/좌표 지식 조회는 get_location"` 으로 교체). 추가 조치 불필요.

- **`test_prompt_assembly` 깨짐 수정 (main 의 기존 문제)**: `origin/main` 의 톤 완화 커밋이 `prompts.py` 의 마지막 문구를 `"- JSON 외의 텍스트를 절대 출력하지 마세요."` → `"- 응답은 위 JSON 형식 중 하나로만 출력하세요."` 로 바꾸면서 테스트를 갱신하지 않아, **origin/main 단독으로도 실패하는 상태**였다(문구가 main 의 `prompts.py` 에 0회 존재함을 확인). 병합과 무관.
  - 테스트가 검증하려는 것은 문구가 아니라 **배치**(응답 형식 안내가 교훈·soul 뒤 맨 마지막)이므로, 하드코딩 대신 `prompts._RESPONSE_FORMAT_REMINDER` 상수를 참조하도록 바꿨다 → 앞으로 톤을 다듬어도 깨지지 않는다.

- 회귀 +4(총 44): `get_location` 이 시맨틱맵/`navigated_coordinate`/근거 없음 3케이스 + LLM 파라미터 존중. 기존 라우팅 기대값 3건은 `rag_search` → `get_location` 으로 갱신.
- 검증: `rag_regression 44 passed`, 에이전트 유닛 **307 passed**(병합으로 +5, ROS 의존 4건은 기존 환경 실패), 채널 `15 passed`, ruff 신규 findings 0(I001 2건은 병합 전에도 존재).
- **⭐ 실기 검증 완료 (2026-08-14 12:14, `report-20260814-121456-gemma3_e4b-allpass.md`)**: **gemma4:e4b 로 PASS 17 / FAIL 0**. 헤더 `일치 ✅`(실행코드=이미지=`65c20d6`).
  - tc03/04/07/11/12: 접두어 없는 좌표 답변 → `get_location` 확정 경로 동작(임베딩 미경유).
  - tc10: `'키친'의 위치는 …` — 조사 우선순위 역전 수정 확인(수정 전 `'키친으'`).
  - tc15: `키친 (-5.16, -1.16)지점으로 이동 완료` — 이동 오라우팅 안전망 동작. tc16/17 연쇄 실패도 해소.
  - Qdrant 라이브 확인(`robo_claw_former_0045_w2_2f`): 18 포인트 **전부 UUIDv7**, **scroll 순서 = 시간순**, `type=location` 이름 3건 전부 정확. `dual.py` 의 uuid4 누락 수정으로 **최초 지적된 "시간순 정렬 안 됨"이 해소**됐다.
  - **모델 크기가 원인이 아니었음이 확인됐다.** 08-13 에 31b 로 17/17(449.1s), 08-14 에 e4b 로 17/17(**212.8s**) — 동일 결과에 소요는 절반. 결정론적 경로(라우팅 강제 + 공용 조회 함수)가 모델 성능 의존성을 제거했다.

## 검증 방법

- 로봇 없이: `./validation/rag_regression/run_rag_tests.sh`
- 실로봇 시나리오: `validation/README.md` 참고 (`robo_talk.py` 또는 `robo_claw_cli test --port 50052`)
