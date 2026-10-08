# 2026.09.14

merge: origin/main 통합 및 multi-robot 변경사항 정리

로컬 main과 최신 origin/main 사이의 중복 이력을 정리하고 충돌을 해소한다.
multi-robot 브랜치의 기존 작업 이력을 유지하면서 최신 원격 구현을 기준으로 통합한다.

주요 변경사항:
- 웹 대시보드 노드와 HTTP 인터페이스를 추가한다.
- 런타임 설정 스키마, 계약 파일 및 설정 관리 CLI를 통합한다.
- 자율 협동, 피어 통신, 내비게이션 및 로봇 안전 처리를 개선한다.
- 조작, 인식, 지도 및 시스템 스킬 구현과 테스트를 보강한다.
- Docker, 시뮬레이션, launch 및 로봇별 설정을 최신 구조로 정리한다.
- 운영, 설정, 검증 및 스킬 관련 문서를 개편한다.
- 사용하지 않는 레거시 CLI, 스크립트 및 중복 문서를 제거한다.

검증:
- Dashboard 테스트: 65 passed
- SkillManager 테스트: 24 passed
- Python compileall 통과
- 충돌 마커 및 unstaged 변경 없음


# 2026.10.08

아래 동작 방식으로 수정

- Laya에 보내는 질문에 "이동하거나 몸·팔·손·머리를 움직이는 동작을 요청했는가?"(needs_motion)를 추가했습니다.
- 이 확률이 0.5 이상이면 읽기 전용 스킬을 직접 실행하지 않고 LLM으로 넘깁니다. 기억된 장소로의 navigate_to는 원래 이동이 목적이므로 막지 않습니다.
- **인사 고정 응답에는 별도 기준(0.9)**을 둡니다. 처음에 0.5를 공통으로 썼더니 "안녕"까지 LLM으로 넘어갔습니다. 측정해 보니 Laya는 평범한 인사에도 꽤 높은 값을 줍니다.

| 명령                | needs_motion |
|---------------------|--------------|
| 안녕                | 0.65         |
| 안녕하세요 반가워요 | 0.84         |
| 손 흔들어 인사해    | 0.93         |
| 고개 숙여 인사해    | 0.96         |
  0.9를 기준으로 하면 평범한 인사는 고정 응답, 제스처 인사는 LLM(모션 실행)으로 나뉩니다.
- Laya가 이 질문에 답하지 않는 경우(다른 provider 등)는 막지 않습니다.

재측정 결과 (실서버, readonly)

┌───────────────────────────────────┬───────────┬───────────────────────────────┐
│               항목                │  가드 전  │            가드 후            │
├───────────────────────────────────┼───────────┼───────────────────────────────┤
│ 툴·복합 평가: Laya 정답           │ 114 / 130 │ 119 / 133                     │
├───────────────────────────────────┼───────────┼───────────────────────────────┤
│ 툴·복합 평가: Laya 오실행         │ 2         │ 0                             │
├───────────────────────────────────┼───────────┼───────────────────────────────┤
│ 툴·복합 평가: 복합 명령 직접 실행 │ 0         │ 0                             │
├───────────────────────────────────┼───────────┼───────────────────────────────┤
│ 시드 32건: 정답 / 오실행          │ 17 / 0    │ 17 / 0                        │
├───────────────────────────────────┼───────────┼───────────────────────────────┤
│ 라이브 테스트                     │ 14 통과   │ 14 통과                       │
├───────────────────────────────────┼───────────┼───────────────────────────────┤
│ 규칙 라우터 (참고)                │ —         │ 정답 112 + 지름길 5, 오실행 1 │
└───────────────────────────────────┴───────────┴───────────────────────────────┘

케이스가 133건으로 늘어난 것은 제스처 명령 3건을 새로 넣었기 때문입니다.

변경 파일 (OpenEdge 저장소)

- src/robo_claw_agent/robo_claw_agent/agent_node/system1_router.py: needs_motion 질문과 가드, 기준값 2개 추가
- src/robo_claw_agent/tests/test_system1_router.py: 가드 테스트 8건 (실측 값 회귀 포함). System 1 단위 테스트 81건 통과
- validation/system1/tool_cases.jsonl: 제스처 명령 3건 추가 ("손 흔들어 인사해" 등)
- docs/SYSTEM1_FAST_ROUTER.md, .env.example, CHANGE_LOG.md: 기본 임계값과 결과 반영

남은 일

- 0.9 기준은 여유가 크지 않습니다. 측정한 평범한 인사는 최대 0.84, 제스처 인사는 최소 0.93이었습니다. 그림자 기록으로 실제 인사 명령의 값 분포를 보고 조정하는 것을 권장합니다.
- 웹 설정 기본값(동료분 작업): AI Config Server의 "Confidence thresholds JSON" 기본값에 새 키를 넣어야 합니다. 웹에서 이 값을 직접 입력하면 입력한 키만 덮어쓰고 나머지는 코드 기본값이 적용되므로, 지금 상태로도 동작에는 문제가 없습니다.
{"smalltalk":0.9,"single_skill":0.85,"skill":0.8,"target_place":0.8,"ambiguous":0.5,"needs_motion":0.5,"needs_motion_smalltalk":0.9}
- 작업본 미반영: 이번 변경은 OpenEdge 저장소에만 했고, ~/workspace-roboclaw/robo-claw 작업본에는 반영하지 않았습니다.
- 규칙 라우터 오실행 1건 그대로: "비전 피드백으로 정면 컵을 집어"를 adaptive_pick_object로 바로 실행하는 문제는 Laya와 무관한 기존 규칙(direct_cup_pick_skill)의 문제입니다. 이번에는 손대지 않았습니다.


# 2026.10.08 (2)

fix(agent): 폴백 답변·단계 요약에서 이미지 base64 제외 및 단계 메시지 길이 제한

'카메라 이미지 보내고 분석해줘' 실행 중 1단계 폴백 답변에 카메라 이미지의 base64 원본이
그대로 들어가, TaskDecomposer 2단계 LLM 요청이 2,672,002 토큰(한도 922,000)으로 거부되고
작업 전체가 중단됐다.

원인:
- capture_camera_image / capture_map / get_map_visual 결과의 image_base64 가
  planner._compose_result_answer → answer.render_result_data 에서 그대로 텍스트로 풀렸다.
  (_EXCLUDED_RESULT_KEYS 에 base64 키가 없음)
- TaskDecomposer 는 단계 결과 메시지(step_msg)를 길이 제한 없이 다음 단계의
  "[이전 단계 요약]"에 넣었다. ([결과 데이터]는 이미 base64 생략 + 800자 제한)
- 같은 폴백 답변은 단일 명령에서 사용자에게 보내는 최종 답변으로도 쓰여,
  사용자 메시지에도 base64 가 섞일 수 있었다.

주요 변경사항:
- answer.flatten_to_text: 키 이름에 base64 가 포함된 항목(중첩·대소문자 무관)을 건너뛴다.
  render_result_data, render_structured_answer 가 모두 이 경로를 쓴다.
  기준은 utils._omit_base64_data 와 같다.
- task_planner: 이전 단계 요약의 단계 메시지를 _STEP_RESULT_SUMMARY_CHARS(800자)로 자른다
  (_clip_step_message).
- 회귀 테스트 추가
  - tests/test_result_binary_omission.py (5건): 폴백 답변·중첩 키·구조화 답변의 base64 제외,
    image_path 같은 일반 키는 유지
  - tests/test_task_planner.py (1건): 3MB 단계 메시지가 다음 단계 프롬프트에서 잘리는지

검증:
- 신규 테스트 6건: 수정 전 5 failed → 수정 후 통과
- answer/task_planner 관련 테스트 56 passed, 2 skipped
- robo_claw_agent 전체(로컬, ROS 미설치): 548 passed / 54 skipped,
  실패 14건·수집 오류 10건은 수정 전과 목록이 같은 ROS 의존 항목
- Ruff check 통과

변경 파일:
- src/robo_claw_agent/robo_claw_agent/answer.py
- src/robo_claw_agent/robo_claw_agent/agent_node/task_planner.py
- src/robo_claw_agent/tests/test_result_binary_omission.py (신규)
- src/robo_claw_agent/tests/test_task_planner.py
- CHANGE_LOG.md

참고(이번 범위 밖):
- 같은 로그에서 1단계 LLM이 3단계(analyze_scene)까지 미리 실행했고, 요약 라운드가 답변 대신
  스킬 계획을 반환했다(planner 동작). 로봇에 map 프레임이 없어 /odom 으로 대체 중이다.
