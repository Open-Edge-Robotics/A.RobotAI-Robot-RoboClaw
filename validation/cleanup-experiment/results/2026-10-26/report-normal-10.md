# 정리 태스크 실험 결과 — 정상 협업

- 조건 키: `normal`
- 대상: 127.0.0.1:50052
- 측정 시각: 2026-10-26 18:17:25
- 실행 기록: 10건 / 시도 10회

## 표 2 행

| 실험 조건 | 반복 횟수 | 전체 성공률 | 할당 정확도 | 위임 성공률 | 평균 시간 | 재계획 성공률 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 정상 협업 | 10 | 100.0% | 100.0% | 100.0% | 147.0s | n/a |

기대 동작 준수: 10/10회 (100.0%)

판정 기준: 전체 성공률은 태스크 실행 성공(`cleanup_run.success`) 비율입니다. 조건이
기대한 실패(기능 제약)를 올바르게 처리한 경우는 '기대 동작 준수'로 따로 셉니다.
할당 정확도는 위임이 실제로 이루어진 회차 중 기대 동료와 일치한 비율이고(기대 동료
미지정 시 n/a), 위임 성공률은 `cleanup_delegate` 레코드의 성공 비율입니다. 재계획
성공률은 `--agent-log` 를 준 경우에만 계산합니다.

## 회차 상세

| # | 단계 | 응답 | 응답 시간 | run_id | 장소 status | 위임 동료 | 판정 |
| ---: | --- | --- | ---: | --- | --- | --- | --- |
| 1 | normal | 1개 기억 장소를 대상으로 한 번의 정리 작업을 시작했습니다. | 7.6s | Former0047-20261026T175040-c5d0fb | delegated_demo | Cloid 2호 | PASS |
| 2 | normal | 1개 기억 장소를 대상으로 한 번의 정리 작업을 시작했습니다. | 4.6s | Former0047-20261026T175331-8c750d | delegated_demo | Cloid 2호 | PASS |
| 3 | normal | 1개 기억 장소를 대상으로 한 번의 정리 작업을 시작했습니다. | 5.2s | Former0047-20261026T175608-7de9cf | delegated_demo | Cloid 2호 | PASS |
| 4 | normal | 1개 기억 장소를 대상으로 한 번의 정리 작업을 시작했습니다. | 4.7s | Former0047-20261026T175856-98f602 | delegated_demo | Cloid 2호 | PASS |
| 5 | normal | 1개 기억 장소를 대상으로 한 번의 정리 작업을 시작했습니다. | 4.4s | Former0047-20261026T180134-251133 | delegated_demo | Cloid 2호 | PASS |
| 6 | normal | 1개 기억 장소를 대상으로 한 번의 정리 작업을 시작했습니다. | 4.3s | Former0047-20261026T180424-dcbc72 | delegated_demo | Cloid 2호 | PASS |
| 7 | normal | 1개 기억 장소를 대상으로 한 번의 정리 작업을 시작했습니다. | 4.8s | Former0047-20261026T180709-83f96c | delegated_demo | Cloid 2호 | PASS |
| 8 | normal | 1개 기억 장소를 대상으로 한 번의 정리 작업을 시작했습니다. | 4.2s | Former0047-20261026T180953-2321cd | delegated_demo | Cloid 2호 | PASS |
| 9 | normal | 1개 기억 장소를 대상으로 한 번의 정리 작업을 시작했습니다. | 6.9s | Former0047-20261026T181216-2adb84 | delegated_demo | Cloid 2호 | PASS |
| 10 | normal | 1개 기억 장소를 대상으로 한 번의 정리 작업을 시작했습니다. | 5.3s | Former0047-20261026T181454-7a7c76 | delegated_demo | Cloid 2호 | PASS |

## 구간별 평균

- delegate: 0.1s
- discovery: 0.0s
- handle: 115.4s
- navigate: 4.5s
- observe: 27.1s

기대 동료: `Cloid 2호`
