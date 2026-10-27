# 정리 태스크 협업 실험 결과 보고서

이 보고서는 former0047과 Cloid 2호를 대상으로 `'안방 정리해줘'` 협업 시나리오를 3개 조건에서
반복 실행해 수집한 원시 로그를 집계한 결과입니다. 수치는 모두 `cleanup_runs-*.jsonl` 원시
레코드에서 계산했으며, 재현 절차는 RoboClaw 저장소의 `docs/CLEANUP_EXPERIMENT_RUNBOOK.md`를
따릅니다.

- 작성 시각: 2026-10-26 19:03 KST
- 계측: `cleanup_handoff` / `cleanup_delegate` / `cleanup_run` (런타임 설정 컨트랙트 v2.10.0 `RC_CLEANUP_RUN_LOG_PATH`)
- 기대 동료 등록명: `Cloid 2호`

## 1. 실험 설계

| 조건 | 정의 | 회차 | 기대 동작 |
| --- | --- | ---: | --- |
| 정상 협업 | normal | 10 | 전 회차 위임·시연 요청 성공(delegated_demo) |
| 기능 제약 | constrained | 10 | 전 회차 위임 차단·완료 미보고(unhandled, 위임 요청 없음) |
| 실패 및 복구 — 실패 유발 | recovery_fail | 3 | 동료 미연결 시 요청하지 않고 실패 보고(unhandled) |
| 실패 및 복구 — 복구 | recovery_ok | 3 | 동료 복구 후 위임·시연 요청 성공 |

판정 규칙은 다음과 같습니다.

- 전체 성공률: 태스크 실행 성공(`cleanup_run.success`) 비율입니다.
- 기대 동작 준수: 조건이 의도한 결과(성공 또는 차단)를 회차마다 냈는지 센 값입니다.
- 할당 정확도: 위임이 실제로 이루어진 회차 중 기대 동료와 일치한 비율입니다.
- 위임 성공률: `cleanup_delegate.success` 비율입니다. 위임 시도가 없으면 `n/a`입니다.
- 평균 시간: `cleanup_run.elapsed_ms` 평균입니다. 위임 구간은 `cleanup_delegate.duration_ms`를 씁니다.
- 재계획 성공률: `tidy_home`은 단일 백그라운드 스킬이라 정상 협업에서는 재계획이 발생하지 않아 해당 없음입니다.
  기능 제약·실패 유발 조건은 에이전트 로그를 수집하지 않아 미측정으로 둡니다(러너의 `--agent-log`로 계산 가능).

## 2. 결과 요약

| 실험 조건 | 반복 횟수 | 전체 성공률 | 할당 정확도 | 위임 성공률 | 평균 시간 | 재계획 성공률 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 정상 협업 | 10 | 100.0% | 100.0% | 100.0% | 147.0s | 해당 없음 |
| 기능 제약 | 10 | 0.0% | n/a | n/a | 27.0s | 미측정 |
| 실패 및 복구 | 3 | 66.7% | 100.0% | 66.7% | 119.0s | 미측정 |

| 조건 | 기대 동작 준수 | 차단 회차 | 위임 시도 | 소요 중앙값 | 소요 최소~최대 |
| --- | ---: | ---: | ---: | ---: | --- |
| 정상 협업 | 10/10 | 0 | 10 | 149.1s | 127.7s ~ 156.3s |
| 기능 제약 | 10/10 | 10 | 0 | 25.9s | 24.0s ~ 33.3s |
| 실패 및 복구 — 실패 유발 | 3/3 | 3 | 0 | 30.3s | 26.2s ~ 31.1s |
| 실패 및 복구 — 복구 | 2/3 | 0 | 3 | 126.4s | 102.2s ~ 128.4s |

## 3. 정상 협업

시연 권한이 있는 Cloid 2호에게 위임해 시연 모션 실행 요청까지 성공한 조건입니다.

- 회차: 10회 (성공 10회, 기대 동작 준수 10회)
- 장소 status 집계: delegated_demo=10
- 구간별 평균: navigate 4.5s / observe 27.1s / discovery 5.4ms / delegate 104.6s / handle 115.4s

| # | run_id | status | 위임 동료 | 위임 결과 | 소요 | 판정 |
| ---: | --- | --- | --- | --- | ---: | --- |
| 1 | `Former0047-20261026T175040-c5d0fb` | delegated_demo | Cloid 2호 | 성공 (104.5s) | 156.3s | 기대 충족 |
| 2 | `Former0047-20261026T175331-8c750d` | delegated_demo | Cloid 2호 | 성공 (103.6s) | 141.4s | 기대 충족 |
| 3 | `Former0047-20261026T175608-7de9cf` | delegated_demo | Cloid 2호 | 성공 (113.1s) | 154.9s | 기대 충족 |
| 4 | `Former0047-20261026T175856-98f602` | delegated_demo | Cloid 2호 | 성공 (105.7s) | 142.4s | 기대 충족 |
| 5 | `Former0047-20261026T180134-251133` | delegated_demo | Cloid 2호 | 성공 (103.7s) | 155.6s | 기대 충족 |
| 6 | `Former0047-20261026T180424-dcbc72` | delegated_demo | Cloid 2호 | 성공 (109.0s) | 149.4s | 기대 충족 |
| 7 | `Former0047-20261026T180709-83f96c` | delegated_demo | Cloid 2호 | 성공 (106.8s) | 148.8s | 기대 충족 |
| 8 | `Former0047-20261026T180953-2321cd` | delegated_demo | Cloid 2호 | 성공 (93.7s) | 127.7s | 기대 충족 |
| 9 | `Former0047-20261026T181216-2adb84` | delegated_demo | Cloid 2호 | 성공 (97.0s) | 142.9s | 기대 충족 |
| 10 | `Former0047-20261026T181454-7a7c76` | delegated_demo | Cloid 2호 | 성공 (108.9s) | 150.7s | 기대 충족 |

## 4. 기능 제약

former0047 프로필의 피어 목록을 비워 위임 대상이 없는 상태를 만든 조건입니다.

- 회차: 10회 (성공 0회, 기대 동작 준수 10회)
- 장소 status 집계: unhandled=10
- 구간별 평균: navigate 810.1ms / observe 26.2s / discovery 0.0ms / handle 20.9ms

| # | run_id | status | 위임 동료 | 위임 결과 | 소요 | 판정 |
| ---: | --- | --- | --- | --- | ---: | --- |
| 1 | `Former0047-20261026T182538-db6302` | unhandled | (차단) | - | 33.3s | 기대 충족 |
| 2 | `Former0047-20261026T182629-1e4a2e` | unhandled | (차단) | - | 25.9s | 기대 충족 |
| 3 | `Former0047-20261026T182710-a1b1ec` | unhandled | (차단) | - | 26.3s | 기대 충족 |
| 4 | `Former0047-20261026T182753-89baab` | unhandled | (차단) | - | 24.9s | 기대 충족 |
| 5 | `Former0047-20261026T182832-6d1cdf` | unhandled | (차단) | - | 24.0s | 기대 충족 |
| 6 | `Former0047-20261026T182912-c14d73` | unhandled | (차단) | - | 25.3s | 기대 충족 |
| 7 | `Former0047-20261026T182951-83e1b2` | unhandled | (차단) | - | 26.5s | 기대 충족 |
| 8 | `Former0047-20261026T183032-ad6399` | unhandled | (차단) | - | 25.9s | 기대 충족 |
| 9 | `Former0047-20261026T183111-1cd458` | unhandled | (차단) | - | 32.9s | 기대 충족 |
| 10 | `Former0047-20261026T183200-ca7a4e` | unhandled | (차단) | - | 25.2s | 기대 충족 |

## 5. 실패 및 복구 — 실패 유발

Cloid 2호 컨테이너를 내려 동료가 응답하지 않는 상태를 만든 조건입니다.

- 회차: 3회 (성공 0회, 기대 동작 준수 3회)
- 장소 status 집계: unhandled=3
- 구간별 평균: navigate 480.8ms / observe 28.7s / discovery 0.0ms / handle 23.5ms

| # | run_id | status | 위임 동료 | 위임 결과 | 소요 | 판정 |
| ---: | --- | --- | --- | --- | ---: | --- |
| 1 | `Former0047-20261026T184105-584b9b` | unhandled | (차단) | - | 30.3s | 기대 충족 |
| 2 | `Former0047-20261026T184156-554ffe` | unhandled | (차단) | - | 31.1s | 기대 충족 |
| 3 | `Former0047-20261026T184247-406f53` | unhandled | (차단) | - | 26.2s | 기대 충족 |

## 6. 실패 및 복구 — 복구

Cloid 2호를 재기동한 뒤 같은 요청을 다시 실행한 조건입니다.

- 회차: 3회 (성공 2회, 기대 동작 준수 2회)
- 장소 status 집계: delegated_demo=2, execution_failed=1
- 구간별 평균: navigate 4.0s / observe 27.9s / discovery 3.2ms / delegate 79.0s / handle 87.1s

| # | run_id | status | 위임 동료 | 위임 결과 | 소요 | 판정 |
| ---: | --- | --- | --- | --- | ---: | --- |
| 1 | `Former0047-20261026T184949-b127da` | execution_failed | Cloid 2호 | 실패 (60.7s) | 102.2s | 기대 불일치 |
| 2 | `Former0047-20261026T185152-79b159` | delegated_demo | Cloid 2호 | 성공 (89.2s) | 128.4s | 기대 충족 |
| 3 | `Former0047-20261026T185428-d2d4d6` | delegated_demo | Cloid 2호 | 성공 (87.1s) | 126.4s | 기대 충족 |

기대와 어긋난 회차의 응답:
- `Former0047-20261026T184949-b127da`: 실패: CLOiD 포장 자세 실행이 실패했습니다: timeout after 60.0 s

## 7. 예비 실행

계측 경로 검증을 위해 정상 협업 1회를 먼저 실행했습니다. 아래 값은 조건 집계에 포함하지 않았습니다.

- status: delegated_demo=1
- 소요: 178.1s (구간: navigate 43.0s / observe 27.2s / discovery 12.4ms / delegate 90.6s / handle 107.9s)
- 이 실행에서 위임 구간이 초 단위로 기록되는 계측 결함을 발견해 수정했습니다.

## 8. 결론

- 정상 협업 10회는 모두 위임·시연 요청까지 도달해 설계대로 동작했습니다.
- 기능 제약 10회는 위임 대상이 없을 때 요청을 보내지 않고 완료를 주장하지 않았습니다.
- 실패 및 복구 조건은 동료 미연결 시 실패를 정확히 보고했고, 동료 복구 후 위임이 다시 성공했습니다.
- 남은 과제는 회차 시작 위치 고정, 복구 조건 회차 확대, 재계획 지표 수집입니다.

## 9. 관찰 및 한계

1. 총 26회 실행 중 태스크 성공 12회입니다. 조건별 회차 수가 달라 (복구 조건 3회) 표 간 직접 비교에는 주의가 필요합니다.
2. `navigate` 구간은 회차 시작 위치에 좌우됩니다. 정상 협업 1회차만 안방까지 이동해 43초가 걸렸고 이후 회차는 방 안에 머문 상태여서 평균이 4.5초로 낮게 나옵니다. 조건 간 시간 비교에는 시작 위치를 고정한 측정이 필요합니다.
3. 시연 성공의 정의는 '시연 모션 실행 요청까지'입니다. CLOi MotionCmd는 완료 응답을 주지 않아 모션 완료와 물체 이동은 확인되지 않습니다.
4. 실패 및 복구 조건의 복구 1회차는 위임 요청은 전달됐지만 Cloid 2호가 boxing 자세 프리루드에서 `timeout after 60.0 s`를 반환해 실패했습니다. `cloi up` 직후 콜드 스타트 구간으로 보이며, 이후 회차는 정상 수행됐습니다.
5. 기능 제약과 실패 유발 조건은 모두 `unhandled`로 끝났습니다. 두 조건의 차이는 원인(위임 대상 미등록 / 동료 미연결)이며, 관측 지표는 동일합니다.
6. 계측 초기 버전에서 위임 구간이 초 단위로 기록되는 결함이 있었고, 집계 스크립트가 `cleanup_delegate.duration_ms`를 우선 사용해 보정합니다.

## 10. 산출물

| 조건 | 파일 | 크기 | sha256(앞 16자) |
| --- | --- | ---: | --- |
| 정상 협업 | `cleanup_runs-normal-10-20261026.jsonl` | 17,225 B | `f4ec5159ae2243bf` |
| 기능 제약 | `cleanup_runs-constrained-10-20261026.jsonl` | 12,460 B | `3becbcbb5e3993cd` |
| 실패 및 복구 — 실패 유발 | `cleanup_runs-recovery-fail-3-20261026.jsonl` | 4,011 B | `cef6ad9ede889333` |
| 실패 및 복구 — 복구 | `cleanup_runs-recovery-recovered-3-20261026.jsonl` | 5,057 B | `b82acb8ffd1b4615` |

- 재현 절차: RoboClaw 저장소 `docs/CLEANUP_EXPERIMENT_RUNBOOK.md`
- 지표 정의: RoboClaw 저장소 `docs/CLEANUP_RUN_METRICS.md`
- 러너·조건 파일: RoboClaw 저장소 `validation/cleanup-experiment/`
- 집계 도구: RoboClaw 저장소 `scripts/cleanup_run_report.py`

## 부록. run_id 목록

- 정상 협업
  - `Former0047-20261026T175040-c5d0fb` (delegated_demo)
  - `Former0047-20261026T175331-8c750d` (delegated_demo)
  - `Former0047-20261026T175608-7de9cf` (delegated_demo)
  - `Former0047-20261026T175856-98f602` (delegated_demo)
  - `Former0047-20261026T180134-251133` (delegated_demo)
  - `Former0047-20261026T180424-dcbc72` (delegated_demo)
  - `Former0047-20261026T180709-83f96c` (delegated_demo)
  - `Former0047-20261026T180953-2321cd` (delegated_demo)
  - `Former0047-20261026T181216-2adb84` (delegated_demo)
  - `Former0047-20261026T181454-7a7c76` (delegated_demo)
- 기능 제약
  - `Former0047-20261026T182538-db6302` (unhandled)
  - `Former0047-20261026T182629-1e4a2e` (unhandled)
  - `Former0047-20261026T182710-a1b1ec` (unhandled)
  - `Former0047-20261026T182753-89baab` (unhandled)
  - `Former0047-20261026T182832-6d1cdf` (unhandled)
  - `Former0047-20261026T182912-c14d73` (unhandled)
  - `Former0047-20261026T182951-83e1b2` (unhandled)
  - `Former0047-20261026T183032-ad6399` (unhandled)
  - `Former0047-20261026T183111-1cd458` (unhandled)
  - `Former0047-20261026T183200-ca7a4e` (unhandled)
- 실패 및 복구 — 실패 유발
  - `Former0047-20261026T184105-584b9b` (unhandled)
  - `Former0047-20261026T184156-554ffe` (unhandled)
  - `Former0047-20261026T184247-406f53` (unhandled)
- 실패 및 복구 — 복구
  - `Former0047-20261026T184949-b127da` (execution_failed)
  - `Former0047-20261026T185152-79b159` (delegated_demo)
  - `Former0047-20261026T185428-d2d4d6` (delegated_demo)
