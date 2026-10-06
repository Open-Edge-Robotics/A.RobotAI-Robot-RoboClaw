# robo_talk 시나리오 테스트 리포트

- 소프트웨어: 실행코드 `9dc4190` (호스트 체크아웃(main), 미커밋 1개) · 이미지 `9dc4190`/`20260814-main` build `2026-08-14 15:22:52 KST` · **일치 ✅**
- 대상(RoboMessenger): `10.159.172.69:50052`
- 시나리오: First-Use 시나리오 자동 검증 (RAG 위치 지식 개선 P1~P5)
- 시작: 2026-08-14 15:44:25
- 종료: 2026-08-14 15:48:57
- 총 소요: 272.8s
- 결과: **PASS 17 / FAIL 0 / SKIP 2**

## AI Config

- 프로파일: **Former_0045_w2_2f**  (robot=former, env=0045_w2_2f, active=True)

**LLM**
- Model: `gemma4:e4b`
- Model URL: http://10.232.183.148:11434
- num_ctx: 32768
- temperature: 0.2
- Repeat Penalty: 1
- Repeat Last N: 64
- Seed: 42
- Max Predict Token: 1024
- Top K: 10
- Top P: 0.2
- Min P: 0

**Embedding Model**
- Model: `qwen3-embedding:0.6b`
- Qdrant URL: http://10.159.172.74:6333
- Qdrant Collection: robo_claw_former_0045_w2_2f
- Qdrant Timeout: 5
- RAG Top-K: 5
- RAG Score Threshold: 0.55

## 요약

| Step | 테스트 항목 | 결과 | 소요 | 상세 |
| :--- | :--- | :---: | ---: | :--- |
| Phase 0 | 0. 연결성 확인 | ➖ SKIP | - | prompt 없음(type=ping) |
| Phase 1 (충전대) | 1. 충전대 지점으로 이동 | ✅ PASS | 11.0s | 응답: (3.96, 0.83)지점으로 이동 완료 |
| Phase 1 (충전대) | 2. 현재 위치를 충전대로 기억 | ✅ PASS | 5.5s | 응답: 지식 1건을 저장했습니다. |
| Phase 1 (충전대) | 3. 충전대 위치 질의 (내부 로그 누수 검출) | ✅ PASS | 5.8s | 응답: '충전대'의 위치는 x: 4.19, y: 0.75 입니다. |
| Phase 1 (충전대) | 4. 충전대 좌표 질의 | ✅ PASS | 5.8s | 응답: '충전대'의 위치는 x: 4.19, y: 0.75 입니다. |
| Phase 2 (거실) | 5. 거실 지점으로 이동 | ✅ PASS | 35.9s | 응답: (0.66, 0.73)지점으로 이동 완료 |
| Phase 2 (거실) | 6. 현재 위치를 거실로 저장 | ✅ PASS | 5.8s | 응답: 지식 1건을 저장했습니다. |
| Phase 2 (거실) | 7. 거실 위치 질의 (내부 로그 누수 검출) | ✅ PASS | 5.3s | 응답: '거실'의 위치는 x: 0.66, y: 0.73 입니다. |
| Phase 3 (키친) | 8. 키친 지점으로 이동 | ✅ PASS | 46.6s | 응답: (-5.36, -1.25)지점으로 이동 완료 |
| Phase 3 (키친) | 9. 현재 위치를 키친으로 기억 | ✅ PASS | 4.9s | 응답: 지식 1건을 저장했습니다. |
| Phase 3 (키친) | 10. 키친 위치 질의 (내부 로그 누수 검출) | ✅ PASS | 5.2s | 응답: '키친'의 위치는 x: -5.16, y: -1.17 입니다. |
| Phase 4 (반복 질의 회귀) | 11. 충전대 재질의 (회귀: 원본 Fail 12/24) | ✅ PASS | 5.5s | 응답: '충전대'의 위치는 x: 4.19, y: 0.75 입니다. |
| Phase 4 (반복 질의 회귀) | 12. 충전대 좌표 재질의 (회귀: 원본 Fail 25) | ✅ PASS | 5.5s | 응답: '충전대'의 위치는 x: 4.19, y: 0.75 입니다. |
| Phase 5 (이름 기반 이동) | 13. 충전대로 이동 (이름 기반, 원본 Fail 22) | ✅ PASS | 49.3s | 응답: 충전대 (4.19, 0.75)지점으로 이동 완료 |
| Phase 5 (이름 기반 이동) | 14. 거실로 이동 (이름 기반, 원본 Fail 14) | ✅ PASS | 32.5s | 응답: 거실 (0.66, 0.73)지점으로 이동 완료 |
| Phase 5 (이름 기반 이동) | 15. 키친으로 이동 (이름 기반) | ✅ PASS | 37.5s | 응답: 키친 (-5.16, -1.17)지점으로 이동 완료 |
| Phase 6 (역방향 조회) | 16. 현재 위치 이름 파악 (역방향 조회, 원본 Fail 17) | ✅ PASS | 5.0s | 응답: 현재 위치는 '키친'입니다 (거리 0.22m). |
| Phase 6 (역방향 조회) | 17. 좌표 비교로 위치명 파악 (역방향 조회, 원본 Fail 18) | ✅ PASS | 5.4s | 응답: 현재 위치는 '키친'입니다 (거리 0.22m). |
| Phase 7 (교차언어 / 알려진 한계) | 18. go to living room (교차언어 — 알려진 한계, 기본 SKIP) | ➖ SKIP | - | 비활성화 |

## 상세 로그

### [PASS] 1. 충전대 지점으로 이동  (소요 11.0s)
- 프롬프트: `x=3.96, y=0.83 으로 이동해`
- success 플래그: `True`
- 응답 전문:
```
(3.96, 0.83)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "(3.96, 0.83)지점으로 이동 완료", "duration_sec": 3.584440866998193, "result_data": {"success": true, "message": "(3.96, 0.83)지점으로 이동 완료", "x": 3.96, "y": 0.83}}]}
```

### [PASS] 2. 현재 위치를 충전대로 기억  (소요 5.5s)
- 프롬프트: `현재 위치를 충전대로 기억해`
- success 플래그: `True`
- 응답 전문:
```
지식 1건을 저장했습니다.
```
- result_json:
```json
{"skill_results": [{"skill_name": "rag_add", "code": 0, "message": "지식 1건을 저장했습니다.", "duration_sec": 0.19804457099962747, "result_data": {"success": true, "message": "지식 1건을 저장했습니다.", "text": "현재 위치는 충전대입니다.", "semantic_updated": true, "deduped": 0}}]}
```

### [PASS] 3. 충전대 위치 질의 (내부 로그 누수 검출)  (소요 5.8s)
- 프롬프트: `충전대 위치를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'충전대'의 위치는 x: 4.19, y: 0.75 입니다.
```
- result_json:
```json
{"skill_results": [{"skill_name": "get_location", "code": 0, "message": "'충전대'의 위치는 x: 4.19, y: 0.75 입니다.", "duration_sec": 0.6002289710013429, "result_data": {"success": true, "message": "'충전대'의 위치는 x: 4.19, y: 0.75 입니다.", "location_name": "충전대", "position": {"x": 4.19, "y": 0.75}, "source": "semantic_map", "source_detail": "시맨틱 맵", "id": null}}]}
```

### [PASS] 4. 충전대 좌표 질의  (소요 5.8s)
- 프롬프트: `충전대 위치 좌표를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'충전대'의 위치는 x: 4.19, y: 0.75 입니다.
```
- result_json:
```json
{"skill_results": [{"skill_name": "get_location", "code": 0, "message": "'충전대'의 위치는 x: 4.19, y: 0.75 입니다.", "duration_sec": 0.28001308600141783, "result_data": {"success": true, "message": "'충전대'의 위치는 x: 4.19, y: 0.75 입니다.", "location_name": "충전대", "position": {"x": 4.19, "y": 0.75}, "source": "semantic_map", "source_detail": "시맨틱 맵", "id": null}}]}
```

### [PASS] 5. 거실 지점으로 이동  (소요 35.9s)
- 프롬프트: `x=0.66, y=0.73 으로 이동해`
- success 플래그: `True`
- 응답 전문:
```
(0.66, 0.73)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "(0.66, 0.73)지점으로 이동 완료", "duration_sec": 30.506511587002024, "result_data": {"success": true, "message": "(0.66, 0.73)지점으로 이동 완료", "x": 0.66, "y": 0.73}}]}
```

### [PASS] 6. 현재 위치를 거실로 저장  (소요 5.8s)
- 프롬프트: `그 위치를 거실로 저장해`
- success 플래그: `True`
- 응답 전문:
```
지식 1건을 저장했습니다.
```
- result_json:
```json
{"skill_results": [{"skill_name": "rag_add", "code": 0, "message": "지식 1건을 저장했습니다.", "duration_sec": 0.398712369002169, "result_data": {"success": true, "message": "지식 1건을 저장했습니다.", "text": "현재 위치는 거실입니다.", "semantic_updated": true, "deduped": 0}}]}
```

### [PASS] 7. 거실 위치 질의 (내부 로그 누수 검출)  (소요 5.3s)
- 프롬프트: `거실의 위치를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'거실'의 위치는 x: 0.66, y: 0.73 입니다.
```
- result_json:
```json
{"skill_results": [{"skill_name": "get_location", "code": 0, "message": "'거실'의 위치는 x: 0.66, y: 0.73 입니다.", "duration_sec": 0.25950515200020163, "result_data": {"success": true, "message": "'거실'의 위치는 x: 0.66, y: 0.73 입니다.", "location_name": "거실", "position": {"x": 0.66, "y": 0.73}, "source": "semantic_map", "source_detail": "시맨틱 맵", "id": null}}]}
```

### [PASS] 8. 키친 지점으로 이동  (소요 46.6s)
- 프롬프트: `x=-5.36, y=-1.25 로 이동해`
- success 플래그: `True`
- 응답 전문:
```
(-5.36, -1.25)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "(-5.36, -1.25)지점으로 이동 완료", "duration_sec": 41.293320182998286, "result_data": {"success": true, "message": "(-5.36, -1.25)지점으로 이동 완료", "x": -5.36, "y": -1.25}}]}
```

### [PASS] 9. 현재 위치를 키친으로 기억  (소요 4.9s)
- 프롬프트: `그 위치를 키친으로 기억해`
- success 플래그: `True`
- 응답 전문:
```
지식 1건을 저장했습니다.
```
- result_json:
```json
{"skill_results": [{"skill_name": "rag_add", "code": 0, "message": "지식 1건을 저장했습니다.", "duration_sec": 0.14767764499993064, "result_data": {"success": true, "message": "지식 1건을 저장했습니다.", "text": "이곳은 키친입니다.", "semantic_updated": true, "deduped": 0}}]}
```

### [PASS] 10. 키친 위치 질의 (내부 로그 누수 검출)  (소요 5.2s)
- 프롬프트: `키친 위치를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'키친'의 위치는 x: -5.16, y: -1.17 입니다.
```
- result_json:
```json
{"skill_results": [{"skill_name": "get_location", "code": 0, "message": "'키친'의 위치는 x: -5.16, y: -1.17 입니다.", "duration_sec": 0.2781170950001979, "result_data": {"success": true, "message": "'키친'의 위치는 x: -5.16, y: -1.17 입니다.", "location_name": "키친", "position": {"x": -5.16, "y": -1.17}, "source": "semantic_map", "source_detail": "시맨틱 맵", "id": null}}]}
```

### [PASS] 11. 충전대 재질의 (회귀: 원본 Fail 12/24)  (소요 5.5s)
- 프롬프트: `충전대 위치를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'충전대'의 위치는 x: 4.19, y: 0.75 입니다.
```
- result_json:
```json
{"skill_results": [{"skill_name": "get_location", "code": 0, "message": "'충전대'의 위치는 x: 4.19, y: 0.75 입니다.", "duration_sec": 0.27785897300054785, "result_data": {"success": true, "message": "'충전대'의 위치는 x: 4.19, y: 0.75 입니다.", "location_name": "충전대", "position": {"x": 4.19, "y": 0.75}, "source": "semantic_map", "source_detail": "시맨틱 맵", "id": null}}]}
```

### [PASS] 12. 충전대 좌표 재질의 (회귀: 원본 Fail 25)  (소요 5.5s)
- 프롬프트: `충전대 위치 좌표를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'충전대'의 위치는 x: 4.19, y: 0.75 입니다.
```
- result_json:
```json
{"skill_results": [{"skill_name": "get_location", "code": 0, "message": "'충전대'의 위치는 x: 4.19, y: 0.75 입니다.", "duration_sec": 0.20776042199941003, "result_data": {"success": true, "message": "'충전대'의 위치는 x: 4.19, y: 0.75 입니다.", "location_name": "충전대", "position": {"x": 4.19, "y": 0.75}, "source": "semantic_map", "source_detail": "시맨틱 맵", "id": null}}]}
```

### [PASS] 13. 충전대로 이동 (이름 기반, 원본 Fail 22)  (소요 49.3s)
- 프롬프트: `충전대로 이동해`
- success 플래그: `True`
- 응답 전문:
```
충전대 (4.19, 0.75)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "충전대 (4.19, 0.75)지점으로 이동 완료", "duration_sec": 44.069526994000626, "result_data": {"success": true, "message": "충전대 (4.19, 0.75)지점으로 이동 완료", "x": 4.19, "y": 0.75}}]}
```

### [PASS] 14. 거실로 이동 (이름 기반, 원본 Fail 14)  (소요 32.5s)
- 프롬프트: `거실로 이동해`
- success 플래그: `True`
- 응답 전문:
```
거실 (0.66, 0.73)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "거실 (0.66, 0.73)지점으로 이동 완료", "duration_sec": 27.510393134998594, "result_data": {"success": true, "message": "거실 (0.66, 0.73)지점으로 이동 완료", "x": 0.66, "y": 0.73}}]}
```

### [PASS] 15. 키친으로 이동 (이름 기반)  (소요 37.5s)
- 프롬프트: `키친으로 이동해`
- success 플래그: `True`
- 응답 전문:
```
키친 (-5.16, -1.17)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "키친 (-5.16, -1.17)지점으로 이동 완료", "duration_sec": 32.39149214000281, "result_data": {"success": true, "message": "키친 (-5.16, -1.17)지점으로 이동 완료", "x": -5.16, "y": -1.17}}]}
```

### [PASS] 16. 현재 위치 이름 파악 (역방향 조회, 원본 Fail 17)  (소요 5.0s)
- 프롬프트: `현재 있는 위치의 이름이 뭐야?`
- success 플래그: `True`
- 응답 전문:
```
현재 위치는 '키친'입니다 (거리 0.22m).
```
- result_json:
```json
{"skill_results": [{"skill_name": "identify_location", "code": 0, "message": "현재 위치는 '키친'입니다 (거리 0.22m).", "duration_sec": 0.0004580250024446286, "result_data": {"success": true, "message": "현재 위치는 '키친'입니다 (거리 0.22m).", "location_name": "키친", "distance_m": 0.22, "position": {"x": -4.96, "y": -1.08}}}]}
```

### [PASS] 17. 좌표 비교로 위치명 파악 (역방향 조회, 원본 Fail 18)  (소요 5.4s)
- 프롬프트: `현재 위치 x, y 좌표와 비교해서 현재 위치의 이름을 파악해봐`
- success 플래그: `True`
- 응답 전문:
```
현재 위치는 '키친'입니다 (거리 0.22m).
```
- result_json:
```json
{"skill_results": [{"skill_name": "identify_location", "code": 0, "message": "현재 위치는 '키친'입니다 (거리 0.22m).", "duration_sec": 0.00046032899990677834, "result_data": {"success": true, "message": "현재 위치는 '키친'입니다 (거리 0.22m).", "location_name": "키친", "distance_m": 0.22, "position": {"x": -4.96, "y": -1.08}}}]}
```

