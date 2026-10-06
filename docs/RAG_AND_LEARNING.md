# RAG / Qdrant 설정 및 스킬 자가 학습

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
시스템 프롬프트 [학습된 스킬 교훈] 섹션 (정적 프롬프트는 불변, 추가만)
```

1. **수집** — 모든 스킬 실행 직후 결과를 `type="skill_episode"`로 저장합니다. 실패는 항상, 성공은 노이즈/토큰 절감을 위해 `skill_learning_success_sample_rate` 확률로만 표본 수집합니다.
2. **추출(reflection)** — 한 스킬에 경험이 `min_episodes`(기본 4건) 이상 쌓이면 LLM이 그 경험들(실패 사례 우선)을 분석해 행동 지침을 뽑아 `type="skill_lesson"`로 저장합니다. 스킬당 1건만 유지(upsert)하므로 최신 교훈으로 계속 갱신됩니다.
3. **주입** — 교훈이 갱신되면 프롬프트 캐시를 무효화하고, **다음 LLM 추론(다음 태스크 또는 다음 계획 라운드)부터** 시스템 프롬프트의 `[학습된 스킬 교훈]` 섹션에 최신 교훈이 실립니다.

### 스킬(교훈) 업데이트 타이밍

| 단계          | 시점                   | 트리거                                                                                                                     |
| ------------- | ---------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| 경험 수집     | 매 스킬 실행 직후      | 태스크 처리 중 자동                                                                                                        |
| 교훈 추출     | 주기적 / 온디맨드      | `skill_learning_reflect_interval_sec` 주기 백그라운드(자동) **또는** `reflect_skills` 스킬 호출(수동, 특정 스킬 지정 가능) |
| 프롬프트 반영 | 교훈 갱신 직후 첫 추론 | 캐시 무효화 후 자동                                                                                                        |

> `enable_rag`이 꺼져 있으면 동작하지 않습니다. 자동 추출은 별도 백그라운드 스레드에서 실행되어 실시간 제어 루프를 막지 않습니다.

채널 노드의 `GET /health` 엔드포인트는 에이전트 연결 readiness와 최근 RPC 메트릭을 함께 반환합니다.

## 5. 탐험 관찰 로깅 (`log_observation`)

자율 탐험 중 로봇의 현재 위치·감지 객체·장면 설명을 RAG 지식 베이스에 자동 저장합니다.
`autonomous_act` BT 루프(자세한 내용은 [behavior-tree/README.md](behavior-tree/README.md))에 내장되어 있으며, 수동으로도 호출할 수 있습니다.

저장 텍스트 형식:

```text
[위치 x=1.2 y=3.4] 감지 객체: cup, chair, bottle. 장면: 주방 테이블 주변에 ...
```

`enable_rag: true` 환경에서는 `LLMDecideAction`이 과거 관찰을 자동 검색하여 LLM 판단에 반영합니다.
