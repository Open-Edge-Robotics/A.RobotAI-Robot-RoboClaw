# 검색 증강 생성과 Qdrant 및 스킬 자가 학습

이 문서는 로컬·Qdrant RAG 설정과 스킬 자가 학습의 운영 방법을 설명합니다.

## 구조화 데이터와 의미 검색의 역할

장소명·별칭·좌표, 이동 성공/차단 좌표, 스킬 실행 경험은 SQLite에 저장합니다.
이 데이터의 등록과 정확 조회에는 임베딩이나 Qdrant 연결이 필요하지 않습니다.
교훈 본문과 장면 설명 등 비정형 지식은 기존 벡터 저장소에서 의미 검색합니다.
교훈은 모델 호출 전에 검색하며, 실행 전 별도 LLM 검토 단계는 추가하지 않습니다.

| 데이터 | 기준 저장소 | 조회 방식 |
| --- | --- | --- |
| 장소·별칭·좌표 | SQLite | 이름 정규화 후 정확 조회, 좌표 반경·최근접 조회 |
| 이동 성공/차단 좌표 | SQLite | 종류·장소명 기반 조회 |
| 스킬 실행 경험 | SQLite | 스킬명·성공 여부·건수 제한으로 조회 |
| 스킬 교훈·장면 설명 | 벡터 저장소 | 현재 요청과의 의미 유사도 검색 |

구조화 DB는 로컬 지식 파일의 확장자를 `.structured.db`로 바꾼 경로에 생성합니다.
기본 경로는 `memory.kb.structured.db`이며 Qdrant 미러 namespace가 있으면 해당
컬렉션별 경로에 생성하므로 로봇 환경의 장소와 경험이 섞이지 않습니다.
`rag_status`의 `structured_path`와 `structured_count`에서 경로와 건수를 확인합니다.
`get_location`은 SQL 조회 결과에 `source="sql"`을 반환하며, `rag_list`·`rag_delete`는
SQL 데이터와 의미 검색 데이터를 함께 관리합니다.

기존 `.semantic.json`과 벡터 저장소의 구조화 레코드는 자동 이관하지 않습니다.
이관이 필요하면 아래 "레거시 장소 이관" 절차를 사용합니다.

### 장소 기억 저장 규칙

장소를 기억할 때는 `rag_add`에 `metadata`를 함께 전달합니다.

```json
{"type": "location", "location_name": "안방", "x": 6.46, "y": -1.9}
```

좌표는 `get_status`로 확인한 현재 pose를 사용합니다. 다음 동작을 보장합니다.

| 입력 | 저장 결과 |
| --- | --- |
| metadata에 이름과 좌표 | `type=location`으로 SQLite 저장, 시맨틱 맵 동기화 |
| text에만 이름·좌표("현재 위치(x: …)는 안방입니다", "안방의 좌표는 x: …", "데스크: x=…" 등) | 같은 규칙으로 이름·좌표를 추출해 SQLite 저장 |
| 이름만 있고 좌표 없음 | 현재 pose로 좌표를 채우고 `frame_id`를 기록 |
| 이름은 있지만 좌표를 확보하지 못함 | 저장하지 않고 실패 반환(`failure_reason=no_coordinates`) |
| 장소 의도가 아님 | 벡터 지식 저장(`saved_as=knowledge`) |

`metadata.type`이 `location`이 아닌 항목은 `get_location`·`navigate_to`에서 보이지 않습니다.
결과의 `saved_as` 값(`location`/`knowledge`)으로 실제 저장 경로를 확인할 수 있습니다.

장소·경험은 원격 백업이 없는 **로컬 SQLite 파일**에만 저장되므로, 운영 배포는 메모리
디렉터리(`~/.robo_claw`)를 컨테이너 외부에 영속화해야 합니다. `rclaw` docker 모드는
`RC_MEMORY_DIR`(기본 `<project>/agent_workspace/memory`)를 `/ros2_ws/memory`로
마운트합니다. CLOiD처럼 docker compose로 직접 기동하는 배포는 `/root/.robo_claw`를
named volume으로 마운트합니다(볼륨이 없으면 컨테이너 재생성 시 장소가 사라집니다).

### 레거시 장소 이관

SQLite 전환 이전에 JSON 지식 베이스에만 저장된 장소는 이동 해석에 사용되지 않습니다.
다음 스크립트로 한 번에 승격할 수 있습니다(파일 삭제 없음, 이름 키 기준 UPSERT).

```bash
# 1) 대상 확인 (기록 없음)
python3 scripts/repromote_places.py --dry-run

# 2) 반영 (에이전트를 멈추고 실행)
python3 scripts/repromote_places.py

# 로봇 컨테이너
python3 scripts/repromote_places.py --memory-dir /ros2_ws/agent_workspace/memory
```

스크립트는 namespace별 지식 파일을 모두 읽어 ① `type=location`/`navigated_coordinate`이면서
좌표가 있는 항목과 ② metadata 없이 저장된 "장소처럼 보이는 텍스트"를 구조화 저장소로
옮깁니다. 기본 동작은 다음과 같습니다.

- 같은 이름은 **최신 항목**이 이깁니다(파일을 오래된 순으로 합치므로).
- **기존 값은 유지**합니다(`--overwrite` 로 변경). 레거시 좌표가 현재보다 오래된 경우가
  많아 이동 목적지가 과거 좌표로 되돌아가는 것을 막습니다.
- 대상 namespace는 가장 최근에 갱신된 구조화 DB의 namespace이며(`--target-namespace`),
  namespace가 바뀐 로봇(예: `former_0045_konjiam` → `former_arm_konjiam`)의 과거 장소도
  현재 namespace로 옮길 수 있습니다.
- 로봇 라벨(`Former` 등)과 `현재 위치` 같은 지시어 이름은 이관하지 않습니다.
- 다른 장소가 이미 쓰는 이름·별칭과 충돌하면 건너뛰고 `conflicts`로 보고합니다.
장소 갱신은 동일 이름을 덮어쓰고 이전 별칭을 제거하며 다른 장소의 별칭 충돌은 거절합니다.
좌표가 잘못되었거나 유한하지 않으면 기존 값을 유지하고 저장을 거절합니다.

## 1. 로컬 RAG

`src/robo_claw_bringup/config/agent.yaml`

```yaml
robo_claw_agent_node:
  ros__parameters:
    enable_rag: true
    rag_vector_backend: "local"
    rag_top_k: 2
    rag_score_threshold: 0.7
```

저장된 지식 전체를 시작 시 조회하여 시스템 프롬프트에 넣지 않습니다. 현재 명령에 대한
RAG 검색 결과는 `rag_top_k`와 `rag_score_threshold`에 따라 선별하여 제공합니다.
학습된 스킬 교훈은 별도 경로로 반영합니다.

### 검색 문맥 전달

검색된 지식과 관련 스킬 교훈은 현재 사용자 원문 뒤의 `<retrieved_context>` 블록에
함께 전달합니다. 검색 결과를 별도의 system 메시지로 추가하지 않으며, 고정 시스템
규칙에서 해당 블록을 정책보다 우선하지 않는 참고 데이터로 취급하도록 지시합니다.
현재 태스크의 도구 호출 왕복에서는 블록을 유지하지만 대화 이력에는 사용자 원문만
보존하므로, 다음 사용자 요청에는 이전 검색 블록을 다시 전달하지 않습니다.

### 검색 결과 JSON 로그

검색 진단을 위해 `chat_history/rag_search_*.json`에 검색어, 필터, 적용 임계값,
반환 항목의 본문·점수·메타데이터와 소요 시간을 저장합니다. `search_kind`는 일반 지식
검색에서 `knowledge`, 스킬 교훈 검색에서 `skill_lessons`입니다. `status`는 정상 검색의
`success`, 오류의 `error`, 임베더 미설정·빈 저장소의 `skipped`를 구분합니다.
정상 검색에서 결과가 없으면 `result_count`는 0이며, 오류는 `error`, 생략 사유는
`reason`에 기록합니다. JSON 저장 실패는 검색 결과에 영향을 주지 않습니다.

### 검색 쿼리 재작성

RAG 검색 전에는 최근 대화 내역과 현재 명령을 LLM에 전달하여 검색 쿼리를 한 번 재작성합니다.
일반 지식 검색과 관련 스킬 교훈 검색은 이 쿼리를 공유하며, 모델의 계획 수립에는 사용자
원문을 유지합니다.
대화 범위는 기존 `multiturn_n` 설정을 따르며, 현재 명령이 이력의 마지막 사용자 메시지에
이미 있으면 중복 추가하지 않습니다. 이전 대화는 "거기", "그것" 같은 표현의 대상을
구체화하는 데 사용합니다. 대상과 질문이 명확하면 사용자 입력 원문을 유지하도록 지시합니다.
재작성은 확인 가능한 지시 대상만 치환하며, 질문을 핵심 명사로 축약하거나 수치·조건을
생략하거나 정답을 추가하지 않도록 합니다.

Ollama에서는 이 호출에만 `think=False`를 지정하여 검색어 생성 중 thinking을 끕니다.
일반 계획 수립과 도구 호출은 기존 thinking 설정을 유지하며, 다른 provider에는 Ollama의
`think` 필드를 전달하지 않습니다. 구형 Ollama 클라이언트가 `think` 인자를 지원하지 않으면
경고를 남기고 해당 인자를 생략합니다.

재작성에는 기존 `llm_timeout_sec` 제한을 적용합니다. 응답이 비거나 timeout·오류가 발생하면
현재 명령 원문으로 검색합니다. 재작성 단계는 도구를 호출하거나 로봇 명령을 실행하지 않습니다.

## 2. Qdrant RAG

```yaml
robo_claw_agent_node:
  ros__parameters:
    enable_rag: true
    rag_vector_backend: "qdrant"
    qdrant_url: "http://127.0.0.1:6333"
    qdrant_api_key: ""
    qdrant_collection: "robo_claw_knowledge"
    qdrant_timeout_sec: 5.0
    rag_top_k: 2
    rag_score_threshold: 0.7
```

### Qdrant 클라이언트 버전 관리

Qdrant 서버 1.19.x에 맞춰 Python 클라이언트는 1.19.1을 사용합니다.
`pyproject.toml`은 `>=1.19.1,<1.20` 범위를 선언하고, `uv.lock`과
`Dockerfile`은 1.19.1로 고정합니다. 서버 버전과 클라이언트 버전의 patch 번호는
같을 필요가 없습니다.

클라이언트 버전을 변경하면 세 파일을 함께 갱신하고 `task deps-check`와
벡터 저장소 테스트로 개발 환경과 배포 이미지의 일치를 확인합니다.
`uv sync --locked --inexact --no-dev`로 실행 환경을 동기화한 뒤 에이전트를
재시작합니다. Docker 실행 환경은 변경된 Dockerfile로 이미지를 다시 빌드합니다.
호환성 경고를 숨기는 `check_compatibility=False` 대신 버전을 맞춥니다.

## 3. 관련 파라미터

| 파라미터              | 설명                   |
| --------------------- | ---------------------- |
| `enable_rag`          | RAG 기능 활성화 여부   |
| `rag_vector_backend`  | `local` 또는 `qdrant`  |
| `rag_top_k`           | 검색 시 상위 반환 개수 |
| `rag_score_threshold` | 유사도 임계값          |
| `qdrant_url`          | Qdrant 서버 URL        |
| `qdrant_api_key`      | Qdrant API 키          |
| `qdrant_collection`   | 사용할 컬렉션 이름     |
| `qdrant_timeout_sec`  | Qdrant 요청 타임아웃   |

`rag_vector_backend`가 `qdrant`이고 `rag_local_mirror`가 `true`이면 로컬 mirror도
`qdrant_collection`별로 별도 파일에 저장됩니다. 따라서 collection을 변경해도 이전
collection의 로컬 데이터와 섞이지 않습니다. 기존 collection 구분 전의
`*.kb.json` 파일은 자동으로 새 collection에 복사하지 않으므로, 필요한 데이터는
운영자가 확인한 뒤 명시적으로 이전해야 합니다.

운영 중에는 `rag_status` 스킬로 현재 인덱스 상태와 최근 메트릭을 확인할 수 있고, 임베딩 모델 변경이나 차원 불일치 복구 후에는 `rag_reindex` 스킬로 저장된 지식을 다시 색인할 수 있습니다.

수동 운영 도구도 추가되었습니다.

- `rag_add`: 텍스트 1건을 즉시 RAG에 저장
- `rag_add_file`: 로컬 텍스트 파일을 청크 단위로 분할해 일괄 저장
- `rag_delete`: `record_id`, `source_path`, `text_exact` 기준으로 지식 삭제

## 4. 스킬 자가 학습 (경험 → 교훈 → 프롬프트 자가 개선)

에이전트가 스킬 실행 경험을 RAG에 쌓고, 그 경험에서 교훈을 스스로 도출해 **스킬 관련 시스템 프롬프트를 점진적으로 개선**하는 학습 루프입니다. 스킬 코드를 바꾸는 것이 아니라, 같은 실수를 반복하지 않도록 **스킬 사용 지침(교훈)** 을 갱신합니다.

```yaml
robo_claw_agent_node:
  ros__parameters:
    enable_rag: true
    enable_skill_learning: true # 스킬 경험 수집 활성화 (기본 false)
    skill_learning_success_sample_rate: 0.1 # 성공 경험 샘플링 비율 (실패는 항상 수집)
    skill_learning_reflect_interval_sec: 1800.0 # 교훈 자동 추출 간격(초), 0=비활성
```

`.env`로도 동일하게 켤 수 있습니다: `RC_ENABLE_SKILL_LEARNING`, `RC_SKILL_LEARNING_SUCCESS_SAMPLE_RATE`, `RC_SKILL_LEARNING_REFLECT_INTERVAL_SEC`.

### 동작 흐름 (3단계)

```text
스킬 실행
   │ ① 수집  (실행 직후, 자동)
   ▼
[skill_episode]  실패=항상 저장 / 성공=샘플링 저장
   │ ② 추출  (스킬별 경험 ≥ min_episodes 누적 시)
   ▼  LLM이 실패 사례 위주로 분석 → 교훈 1~3줄 압축
[skill_lesson]   스킬당 1건 upsert (기존 교훈 덮어씀)
   │ ③ 주입  (교훈 갱신 직후 다음 추론부터)
   ▼
현재 요청의 <retrieved_context> 블록 (다음 사용자 턴에는 보존하지 않음)
```

1. **수집** — mock을 제외한 학습 대상 스킬의 실행 결과를 SQLite에 `type="skill_episode"`로 저장합니다. 실패는 항상, 성공은 노이즈/토큰 절감을 위해 `skill_learning_success_sample_rate` 확률로만 표본 수집합니다.
2. **추출(reflection)** — 한 스킬에 경험이 `min_episodes`(기본 4건) 이상 쌓이면 LLM이 그 경험들(실패 사례 우선)을 분석해 행동 지침을 뽑아 `type="skill_lesson"`로 저장합니다. 스킬당 1건만 유지(upsert)하므로 최신 교훈으로 계속 갱신됩니다.
3. **주입** — RAG 활성 시 다음 사용자 요청의 검색 단계에서 관련 교훈을 검색하여 현재 사용자 메시지의 `<retrieved_context>` 블록에 전달합니다. RAG 비활성 시에는 기존 교훈 캐시 폴백을 사용합니다.

### 스킬(교훈) 업데이트 타이밍

| 단계          | 시점                   | 트리거                                                                                                                     |
| ------------- | ---------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| 경험 수집     | 매 스킬 실행 직후      | 태스크 처리 중 자동                                                                                                        |
| 교훈 추출     | 주기적 / 온디맨드      | `skill_learning_reflect_interval_sec` 주기 백그라운드(자동) **또는** `reflect_skills` 스킬 호출(수동, 특정 스킬 지정 가능) |
| 프롬프트 반영 | 교훈 갱신 직후 첫 추론 | 캐시 무효화 후 자동                                                                                                        |

> 실행 경험의 SQL 저장은 임베더 없이 가능합니다. 교훈의 벡터 저장·검색에는 임베더와 RAG 설정이 필요합니다. 자동 추출은 별도 백그라운드 스레드에서 실행합니다.

채널 노드의 `GET /health` 엔드포인트는 에이전트 연결 readiness와 최근 RPC 메트릭을 함께 반환합니다.

## 5. 탐험 관찰 로깅 (`log_observation`)

자율 탐험 중 로봇의 현재 위치·감지 객체·장면 설명을 RAG 지식 베이스에 자동 저장합니다.
`autonomous_act` BT 루프(자세한 내용은 [behavior-tree/README.md](behavior-tree/README.md))에 내장되어 있으며, 수동으로도 호출할 수 있습니다.

저장 텍스트 형식:

```text
[위치 x=1.2 y=3.4] 감지 객체: cup, chair, bottle. 장면: 주방 테이블 주변에 ...
```

`enable_rag: true` 환경에서는 `LLMDecideAction`이 과거 관찰을 자동 검색하여 LLM 판단에 반영합니다.
