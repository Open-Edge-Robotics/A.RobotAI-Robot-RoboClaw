# RAG 위치 지식 회귀 테스트 (로봇 없이 검증)

`first-use-scenario-test-gemma4_31b.md` 의 실패 케이스(Fail 12/13/15/17/18/20)를
유발한 근본 원인들을 **코드 레벨에서 재현·검증**하는 자동 테스트 모음이다.

개선 상세는 저장소 루트 [`CHANGE_LOG.md`](../../CHANGE_LOG.md)의 부록 "RAG 위치 지식 개선(2026-08-03 ~ 2026-08-14)" 참고.

## 왜 로봇이 필요 없나

이 테스트는 실로봇·시뮬레이터·GPU·임베딩 서버·Qdrant 서버가 **전혀 필요 없다**.

- **임베딩**: 키워드 기반 결정론적 더미 임베더 (네트워크/모델 불필요).
- **벡터 DB**: `QdrantClient(location=":memory:")` 인메모리 인스턴스 + 로컬 JSON 스토어.
- **ROS**: import 하지 않음. ROS 의존 스킬(`status.py` 등)을 피하기 위해 ROS 비의존
  모듈(`rag.py`)만 파일 경로로 직접 로드한다.

즉 순수 파이썬 단위 테스트라서 CI/개발 PC에서 1초 내에 반복 실행된다.

## 실행 방법

### 방법 1) 자동화 스크립트 (권장)

```bash
cd robo-claw
./validation/rag_regression/run_rag_tests.sh
```

특정 순위만:

```bash
./validation/rag_regression/run_rag_tests.sh -k p4      # P4(역방향 조회)만
./validation/rag_regression/run_rag_tests.sh -k "p1 or p3"
```

> 실행 권한이 없으면 `bash validation/rag_regression/run_rag_tests.sh` 로 실행.

### 방법 2) uv 직접

```bash
cd robo-claw
PYTHONPATH="$PWD/src/robo_claw_agent" \
  uv run --project src/robo_claw_agent pytest -p no:cacheprovider \
  validation/rag_regression/test_rag_regression.py -v
```

### 방법 3) uv 없이 (기존 venv 사용)

```bash
cd robo-claw
PYTHONPATH="$PWD/src/robo_claw_agent" python -m pytest \
  validation/rag_regression/test_rag_regression.py -v
```

> `qdrant-client`, `pytest` 가 설치된 파이썬 환경이 필요하다.

### 쓰기 제한 환경(CI/샌드박스)

`~/.cache/uv` 나 프로젝트 디렉터리에 쓸 수 없으면 캐시/venv 위치를 옮긴다:

```bash
export UV_CACHE_DIR="$TMPDIR/uv-cache"
export UV_PROJECT_ENVIRONMENT="$TMPDIR/rc-venv"
./validation/rag_regression/run_rag_tests.sh
```

## 테스트 ↔ 개선 순위 ↔ 실패 케이스 매핑

| 테스트                                            | 순위 | 검증 내용                     | 관련 Fail   |
| ------------------------------------------------- | ---- | ----------------------------- | ----------- |
| `test_p1_query_skills_not_recorded_as_episode`    | P1   | 조회성 스킬은 학습 기록 안 됨 | 12,13,15,20 |
| `test_p1_search_excludes_skill_episode`           | P1   | 검색에서 skill_episode 제외   | 12,13,15,20 |
| `test_p2_name_lookup_normalized`                  | P2   | 이름 조회 대소문자·공백 무관  | 15,20       |
| `test_p2_rag_add_stamps_location_type`            | P2   | 위치에 type=location 태깅     | —           |
| `test_p3_stable_fact_outranks_recent_observation` | P3   | 사실이 최신 관찰보다 우선     | 12,13       |
| `test_p4_reverse_lookup_nearest`                  | P4   | 좌표→최근접 명명 위치         | 17,18       |
| `test_p4_identify_location_skill`                 | P4   | identify_location 스킬        | 17,18       |
| `test_p5_search_formats_location_from_metadata`   | P5   | metadata 기반 응답, 로그 은폐 | 3,7,11      |
| `test_p5_sanitize_strips_skill_log_prefix`        | P5   | 레거시 로그 접두어 제거       | 3,7,11      |

## 실로봇 재검증(선택)

코드 회귀는 위로 충분하지만, 실제 파이프라인(gemma4:31b + qwen3-embedding + 실제
Qdrant)에서의 재현은 `first-use-scenario-test-gemma4_31b.md` 시나리오를 로봇/시뮬에서
다시 수행해 확인한다. 이때 **기존 Qdrant 컬렉션에 남은 레거시 `skill_episode` 오염
항목**은 `rag_reindex`/`rag_delete` 로 정리한 뒤 측정할 것(CHANGES.md 남은 이슈 참고).
