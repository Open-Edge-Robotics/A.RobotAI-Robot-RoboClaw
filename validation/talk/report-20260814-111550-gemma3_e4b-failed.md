# robo_talk 시나리오 테스트 리포트

- 소프트웨어: 실행코드 `5b28aaa` (호스트 체크아웃(using_gemma4_e4b), 미커밋 1개) · 이미지 `57741f8`/`20260812-3-for_gemma4-e4b` build `2026-08-13 09:44:59 KST` · ⚠️ **테스트 기준 `b81f388` 과 다릅니다 → 로봇에서 git pull + 컨테이너 재기동 필요 (이미지 재빌드로는 반영되지 않습니다)**
- 대상(RoboMessenger): `10.159.172.69:50052`
- 시나리오: First-Use 시나리오 자동 검증 (RAG 위치 지식 개선 P1~P5)
- 시작: 2026-08-14 11:15:51
- 종료: 2026-08-14 11:20:04
- 총 소요: 252.8s
- 결과: **PASS 14 / FAIL 3 / SKIP 2**

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
- Qdrant Collection: robo_claw_former_0047_w2_2f
- Qdrant Timeout: 5
- RAG Top-K: 5
- RAG Score Threshold: 0.55

## 요약

| Step | 테스트 항목 | 결과 | 소요 | 상세 |
| :--- | :--- | :---: | ---: | :--- |
| Phase 0 | 0. 연결성 확인 | ➖ SKIP | - | prompt 없음(type=ping) |
| Phase 1 (충전대) | 1. 충전대 지점으로 이동 | ✅ PASS | 32.3s | 응답: (3.96, 0.83)지점으로 이동 완료 |
| Phase 1 (충전대) | 2. 현재 위치를 충전대로 기억 | ✅ PASS | 5.2s | 응답: 지식 1건을 저장했습니다. |
| Phase 1 (충전대) | 3. 충전대 위치 질의 (내부 로그 누수 검출) | ✅ PASS | 5.2s | 응답: '충전대'의 위치는 x: 3.75, y: 0.83 입니다. |
| Phase 1 (충전대) | 4. 충전대 좌표 질의 | ✅ PASS | 5.2s | 응답: '충전대'의 위치는 x: 3.75, y: 0.83 입니다. |
| Phase 2 (거실) | 5. 거실 지점으로 이동 | ✅ PASS | 43.0s | 응답: (0.66, 0.73)지점으로 이동 완료 |
| Phase 2 (거실) | 6. 현재 위치를 거실로 저장 | ✅ PASS | 5.5s | 응답: 지식 1건을 저장했습니다. |
| Phase 2 (거실) | 7. 거실 위치 질의 (내부 로그 누수 검출) | ✅ PASS | 5.5s | 응답: '거실'의 위치는 x: 0.85, y: 0.86 입니다. |
| Phase 3 (키친) | 8. 키친 지점으로 이동 | ✅ PASS | 40.6s | 응답: (-5.36, -1.25)지점으로 이동 완료 |
| Phase 3 (키친) | 9. 현재 위치를 키친으로 기억 | ✅ PASS | 5.2s | 응답: 지식 1건을 저장했습니다. |
| Phase 3 (키친) | 10. 키친 위치 질의 (내부 로그 누수 검출) | ✅ PASS | 5.5s | 응답: 지식 1건을 검색했습니다. 상위 결과: '키친으'의 위치는 x: -5.18, y: -1.17 입니다. |
| Phase 4 (반복 질의 회귀) | 11. 충전대 재질의 (회귀: 원본 Fail 12/24) | ✅ PASS | 4.9s | 응답: '충전대'의 위치는 x: 3.75, y: 0.83 입니다. |
| Phase 4 (반복 질의 회귀) | 12. 충전대 좌표 재질의 (회귀: 원본 Fail 25) | ✅ PASS | 5.5s | 응답: '충전대'의 위치는 x: 3.75, y: 0.83 입니다. |
| Phase 5 (이름 기반 이동) | 13. 충전대로 이동 (이름 기반, 원본 Fail 22) | ✅ PASS | 58.0s | 응답: 충전대 (3.75, 0.83)지점으로 이동 완료 |
| Phase 5 (이름 기반 이동) | 14. 거실로 이동 (이름 기반, 원본 Fail 14) | ✅ PASS | 14.4s | 응답: 거실 (0.85, 0.86)지점으로 이동 완료 |
| Phase 5 (이름 기반 이동) | 15. 키친으로 이동 (이름 기반) | ❌ FAIL | 6.8s | 응답: 현재 로봇 상태 조회 완료 (배터리: unknown, 위치: {'x': 1.08, 'y': 0.95, 'heading_deg': 71.8, 'frame': 'map'}) \| 필수 키워드 누락: '이동' |
| Phase 6 (역방향 조회) | 16. 현재 위치 이름 파악 (역방향 조회, 원본 Fail 17) | ❌ FAIL | 4.6s | 응답: 현재 위치는 '거실'입니다 (거리 0.25m). \| 필수 키워드 누락: '키친' |
| Phase 6 (역방향 조회) | 17. 좌표 비교로 위치명 파악 (역방향 조회, 원본 Fail 18) | ❌ FAIL | 5.2s | 응답: 현재 위치는 '거실'입니다 (거리 0.25m). \| 필수 키워드 누락: '키친' |
| Phase 7 (교차언어 / 알려진 한계) | 18. go to living room (교차언어 — 알려진 한계, 기본 SKIP) | ➖ SKIP | - | 비활성화 |

## 상세 로그

### [PASS] 1. 충전대 지점으로 이동  (소요 32.3s)
- 프롬프트: `x=3.96, y=0.83 으로 이동해`
- success 플래그: `True`
- 응답 전문:
```
(3.96, 0.83)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "(3.96, 0.83)지점으로 이동 완료", "duration_sec": 24.0988049209991, "result_data": {"success": true, "message": "(3.96, 0.83)지점으로 이동 완료", "x": 3.96, "y": 0.83}}]}
```

### [PASS] 2. 현재 위치를 충전대로 기억  (소요 5.2s)
- 프롬프트: `현재 위치를 충전대로 기억해`
- success 플래그: `True`
- 응답 전문:
```
지식 1건을 저장했습니다.
```
- result_json:
```json
{"skill_results": [{"skill_name": "rag_add", "code": 0, "message": "지식 1건을 저장했습니다.", "duration_sec": 0.21852007399866125, "result_data": {"success": true, "message": "지식 1건을 저장했습니다.", "text": "현재 위치를 충전대로 기억합니다.", "semantic_updated": true, "deduped": 0}}]}
```

### [PASS] 3. 충전대 위치 질의 (내부 로그 누수 검출)  (소요 5.2s)
- 프롬프트: `충전대 위치를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'충전대'의 위치는 x: 3.75, y: 0.83 입니다.
```
- 참조 Qdrant 항목:
  - 근거=시맨틱 맵 name=충전대 (3.75, 0.83)
- result_json:
```json
{"skill_results": [{"skill_name": "rag_search", "code": 0, "message": "'충전대'의 위치는 x: 3.75, y: 0.83 입니다.", "duration_sec": 0.3211785139992571, "result_data": {"success": true, "message": "'충전대'의 위치는 x: 3.75, y: 0.83 입니다.", "results": [{"text": "'충전대'의 위치는 x: 3.75, y: 0.83 입니다.", "metadata": {"location_name": "충전대", "x": 3.75, "y": 0.83, "type": "location"}, "score": 1.0, "id": null, "source": "시맨틱 맵"}], "count": 1, "referenced": [{"id": null, "source": "시맨틱 맵", "location_name": "충전대", "x": 3.75, "y": 0.83}]}}]}
```

### [PASS] 4. 충전대 좌표 질의  (소요 5.2s)
- 프롬프트: `충전대 위치 좌표를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'충전대'의 위치는 x: 3.75, y: 0.83 입니다.
```
- 참조 Qdrant 항목:
  - 근거=시맨틱 맵 name=충전대 (3.75, 0.83)
- result_json:
```json
{"skill_results": [{"skill_name": "rag_search", "code": 0, "message": "'충전대'의 위치는 x: 3.75, y: 0.83 입니다.", "duration_sec": 0.3857724080007756, "result_data": {"success": true, "message": "'충전대'의 위치는 x: 3.75, y: 0.83 입니다.", "results": [{"text": "'충전대'의 위치는 x: 3.75, y: 0.83 입니다.", "metadata": {"location_name": "충전대", "x": 3.75, "y": 0.83, "type": "location"}, "score": 1.0, "id": null, "source": "시맨틱 맵"}], "count": 1, "referenced": [{"id": null, "source": "시맨틱 맵", "location_name": "충전대", "x": 3.75, "y": 0.83}]}}]}
```

### [PASS] 5. 거실 지점으로 이동  (소요 43.0s)
- 프롬프트: `x=0.66, y=0.73 으로 이동해`
- success 플래그: `True`
- 응답 전문:
```
(0.66, 0.73)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "(0.66, 0.73)지점으로 이동 완료", "duration_sec": 37.65577394999855, "result_data": {"success": true, "message": "(0.66, 0.73)지점으로 이동 완료", "x": 0.66, "y": 0.73}}]}
```

### [PASS] 6. 현재 위치를 거실로 저장  (소요 5.5s)
- 프롬프트: `그 위치를 거실로 저장해`
- success 플래그: `True`
- 응답 전문:
```
지식 1건을 저장했습니다.
```
- result_json:
```json
{"skill_results": [{"skill_name": "rag_add", "code": 0, "message": "지식 1건을 저장했습니다.", "duration_sec": 0.43272356600027706, "result_data": {"success": true, "message": "지식 1건을 저장했습니다.", "text": "이 위치는 거실입니다.", "semantic_updated": true, "deduped": 0}}]}
```

### [PASS] 7. 거실 위치 질의 (내부 로그 누수 검출)  (소요 5.5s)
- 프롬프트: `거실의 위치를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'거실'의 위치는 x: 0.85, y: 0.86 입니다.
```
- 참조 Qdrant 항목:
  - 근거=시맨틱 맵 name=거실 (0.85, 0.86)
- result_json:
```json
{"skill_results": [{"skill_name": "rag_search", "code": 0, "message": "'거실'의 위치는 x: 0.85, y: 0.86 입니다.", "duration_sec": 0.3260010459998739, "result_data": {"success": true, "message": "'거실'의 위치는 x: 0.85, y: 0.86 입니다.", "results": [{"text": "'거실'의 위치는 x: 0.85, y: 0.86 입니다.", "metadata": {"location_name": "거실", "x": 0.85, "y": 0.86, "type": "location"}, "score": 1.0, "id": null, "source": "시맨틱 맵"}], "count": 1, "referenced": [{"id": null, "source": "시맨틱 맵", "location_name": "거실", "x": 0.85, "y": 0.86}]}}]}
```

### [PASS] 8. 키친 지점으로 이동  (소요 40.6s)
- 프롬프트: `x=-5.36, y=-1.25 로 이동해`
- success 플래그: `True`
- 응답 전문:
```
(-5.36, -1.25)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "(-5.36, -1.25)지점으로 이동 완료", "duration_sec": 35.55973915100003, "result_data": {"success": true, "message": "(-5.36, -1.25)지점으로 이동 완료", "x": -5.36, "y": -1.25}}]}
```

### [PASS] 9. 현재 위치를 키친으로 기억  (소요 5.2s)
- 프롬프트: `그 위치를 키친으로 기억해`
- success 플래그: `True`
- 응답 전문:
```
지식 1건을 저장했습니다.
```
- result_json:
```json
{"skill_results": [{"skill_name": "rag_add", "code": 0, "message": "지식 1건을 저장했습니다.", "duration_sec": 0.2657897539993428, "result_data": {"success": true, "message": "지식 1건을 저장했습니다.", "text": "이곳은 키친으입니다.", "semantic_updated": true, "deduped": 0}}]}
```

### [PASS] 10. 키친 위치 질의 (내부 로그 누수 검출)  (소요 5.5s)
- 프롬프트: `키친 위치를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
지식 1건을 검색했습니다. 상위 결과: '키친으'의 위치는 x: -5.18, y: -1.17 입니다.
```
- 참조 Qdrant 항목:
  - id=019ffe10-27b6-7000-8165-eba8eab73343 score=0.6269 type=location '이곳은 키친으입니다.'
- result_json:
```json
{"skill_results": [{"skill_name": "rag_search", "code": 0, "message": "지식 1건을 검색했습니다. 상위 결과: '키친으'의 위치는 x: -5.18, y: -1.17 입니다.", "duration_sec": 0.14575186299953202, "result_data": {"success": true, "message": "지식 1건을 검색했습니다. 상위 결과: '키친으'의 위치는 x: -5.18, y: -1.17 입니다.", "results": [{"text": "이곳은 키친으입니다.", "score": 0.6269, "metadata": {"location_name": "키친으", "x": -5.18, "y": -1.17, "type": "location"}, "timestamp": "2026-08-14T11:18:19.438191", "id": "019ffe10-27b6-7000-8165-eba8eab73343"}], "count": 1, "referenced": [{"id": "019ffe10-27b6-7000-8165-eba8eab73343", "score": 0.6269, "type": "location", "text": "이곳은 키친으입니다."}]}}]}
```

### [PASS] 11. 충전대 재질의 (회귀: 원본 Fail 12/24)  (소요 4.9s)
- 프롬프트: `충전대 위치를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'충전대'의 위치는 x: 3.75, y: 0.83 입니다.
```
- 참조 Qdrant 항목:
  - 근거=시맨틱 맵 name=충전대 (3.75, 0.83)
- result_json:
```json
{"skill_results": [{"skill_name": "rag_search", "code": 0, "message": "'충전대'의 위치는 x: 3.75, y: 0.83 입니다.", "duration_sec": 0.3285759190002864, "result_data": {"success": true, "message": "'충전대'의 위치는 x: 3.75, y: 0.83 입니다.", "results": [{"text": "'충전대'의 위치는 x: 3.75, y: 0.83 입니다.", "metadata": {"location_name": "충전대", "x": 3.75, "y": 0.83, "type": "location"}, "score": 1.0, "id": null, "source": "시맨틱 맵"}], "count": 1, "referenced": [{"id": null, "source": "시맨틱 맵", "location_name": "충전대", "x": 3.75, "y": 0.83}]}}]}
```

### [PASS] 12. 충전대 좌표 재질의 (회귀: 원본 Fail 25)  (소요 5.5s)
- 프롬프트: `충전대 위치 좌표를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'충전대'의 위치는 x: 3.75, y: 0.83 입니다.
```
- 참조 Qdrant 항목:
  - 근거=시맨틱 맵 name=충전대 (3.75, 0.83)
- result_json:
```json
{"skill_results": [{"skill_name": "rag_search", "code": 0, "message": "'충전대'의 위치는 x: 3.75, y: 0.83 입니다.", "duration_sec": 0.3901571000005788, "result_data": {"success": true, "message": "'충전대'의 위치는 x: 3.75, y: 0.83 입니다.", "results": [{"text": "'충전대'의 위치는 x: 3.75, y: 0.83 입니다.", "metadata": {"location_name": "충전대", "x": 3.75, "y": 0.83, "type": "location"}, "score": 1.0, "id": null, "source": "시맨틱 맵"}], "count": 1, "referenced": [{"id": null, "source": "시맨틱 맵", "location_name": "충전대", "x": 3.75, "y": 0.83}]}}]}
```

### [PASS] 13. 충전대로 이동 (이름 기반, 원본 Fail 22)  (소요 58.0s)
- 프롬프트: `충전대로 이동해`
- success 플래그: `True`
- 응답 전문:
```
충전대 (3.75, 0.83)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "충전대 (3.75, 0.83)지점으로 이동 완료", "duration_sec": 52.45896384699881, "result_data": {"success": true, "message": "충전대 (3.75, 0.83)지점으로 이동 완료", "x": 3.75, "y": 0.83}}]}
```

### [PASS] 14. 거실로 이동 (이름 기반, 원본 Fail 14)  (소요 14.4s)
- 프롬프트: `거실로 이동해`
- success 플래그: `True`
- 응답 전문:
```
거실 (0.85, 0.86)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "거실 (0.85, 0.86)지점으로 이동 완료", "duration_sec": 9.161717527998917, "result_data": {"success": true, "message": "거실 (0.85, 0.86)지점으로 이동 완료", "x": 0.85, "y": 0.86}}]}
```

### [FAIL] 15. 키친으로 이동 (이름 기반)  (소요 6.8s)
- 프롬프트: `키친으로 이동해`
- success 플래그: `True`
- 응답 전문:
```
현재 로봇 상태 조회 완료 (배터리: unknown, 위치: {'x': 1.08, 'y': 0.95, 'heading_deg': 71.8, 'frame': 'map'})
```
- result_json:
```json
{"skill_results": [{"skill_name": "get_status", "code": 0, "message": "현재 로봇 상태 조회 완료 (배터리: unknown, 위치: {'x': 1.08, 'y': 0.95, 'heading_deg': 71.8, 'frame': 'map'})", "duration_sec": 1.5076226290002523, "result_data": {"success": true, "message": "현재 로봇 상태 조회 완료 (배터리: unknown, 위치: {'x': 1.08, 'y': 0.95, 'heading_deg': 71.8, 'frame': 'map'})", "status_details": {"battery": {"percentage": "unknown", "status": "unknown"}, "pose": {"x": 1.08, "y": 0.95, "heading_deg": 71.8, "frame": "map"}, "status": "Normal", "timestamp": "11:19:53", "datetime_info": {"date": "2026-08-14", "time": "11:19:53", "datetime": "2026-08-14 11:19:53", "weekday": "금요일", "timezone": "KST"}}}}]}
```
- 판정 실패 사유: 필수 키워드 누락: '이동'

### [FAIL] 16. 현재 위치 이름 파악 (역방향 조회, 원본 Fail 17)  (소요 4.6s)
- 프롬프트: `현재 있는 위치의 이름이 뭐야?`
- success 플래그: `True`
- 응답 전문:
```
현재 위치는 '거실'입니다 (거리 0.25m).
```
- result_json:
```json
{"skill_results": [{"skill_name": "identify_location", "code": 0, "message": "현재 위치는 '거실'입니다 (거리 0.25m).", "duration_sec": 0.0005725240007450338, "result_data": {"success": true, "message": "현재 위치는 '거실'입니다 (거리 0.25m).", "location_name": "거실", "distance_m": 0.25, "position": {"x": 1.08, "y": 0.95}}}]}
```
- 판정 실패 사유: 필수 키워드 누락: '키친'

### [FAIL] 17. 좌표 비교로 위치명 파악 (역방향 조회, 원본 Fail 18)  (소요 5.2s)
- 프롬프트: `현재 위치 x, y 좌표와 비교해서 현재 위치의 이름을 파악해봐`
- success 플래그: `True`
- 응답 전문:
```
현재 위치는 '거실'입니다 (거리 0.25m).
```
- result_json:
```json
{"skill_results": [{"skill_name": "identify_location", "code": 0, "message": "현재 위치는 '거실'입니다 (거리 0.25m).", "duration_sec": 0.00023882100140326656, "result_data": {"success": true, "message": "현재 위치는 '거실'입니다 (거리 0.25m).", "location_name": "거실", "distance_m": 0.25, "position": {"x": 1.08, "y": 0.95}}}]}
```
- 판정 실패 사유: 필수 키워드 누락: '키친'

