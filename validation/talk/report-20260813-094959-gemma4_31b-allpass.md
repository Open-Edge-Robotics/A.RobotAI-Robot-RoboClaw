# robo_talk 시나리오 테스트 리포트

- 소프트웨어: 실행코드 `57741f8` (호스트 체크아웃(using_gemma4_e4b), 미커밋 2개) · 이미지 `57741f8`/`20260812-3-for_gemma4-e4b` build `2026-08-13 09:44:59 KST` · **일치 ✅**
- 대상(RoboMessenger): `10.159.172.69:50052`
- 시나리오: First-Use 시나리오 자동 검증 (RAG 위치 지식 개선 P1~P5)
- 시작: 2026-08-13 09:50:00
- 종료: 2026-08-13 09:57:29
- 총 소요: 449.1s
- 결과: **PASS 17 / FAIL 0 / SKIP 2**

## AI Config

- 프로파일: **Former_0047_w2_2f**  (robot=former, env=0047_w2_2f, active=True)

**LLM**
- Model: `gemma4:31b`
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
| Phase 1 (충전대) | 1. 충전대 지점으로 이동 | ✅ PASS | 16.5s | 응답: (3.96, 0.83)지점으로 이동 완료 |
| Phase 1 (충전대) | 2. 현재 위치를 충전대로 기억 | ✅ PASS | 20.6s | 응답: 지식 1건을 저장했습니다. |
| Phase 1 (충전대) | 3. 충전대 위치 질의 (내부 로그 누수 검출) | ✅ PASS | 18.4s | 응답: '충전대'의 위치는 x: 3.78, y: 0.86 입니다. |
| Phase 1 (충전대) | 4. 충전대 좌표 질의 | ✅ PASS | 15.4s | 응답: '충전대'의 위치는 x: 3.78, y: 0.86 입니다. |
| Phase 2 (거실) | 5. 거실 지점으로 이동 | ✅ PASS | 51.3s | 응답: (0.66, 0.73)지점으로 이동 완료 |
| Phase 2 (거실) | 6. 현재 위치를 거실로 저장 | ✅ PASS | 20.0s | 응답: 지식 1건을 저장했습니다. |
| Phase 2 (거실) | 7. 거실 위치 질의 (내부 로그 누수 검출) | ✅ PASS | 18.7s | 응답: '거실'의 위치는 x: 0.87, y: 0.80 입니다. |
| Phase 3 (키친) | 8. 키친 지점으로 이동 | ✅ PASS | 49.8s | 응답: (-5.36, -1.25)지점으로 이동 완료 |
| Phase 3 (키친) | 9. 현재 위치를 키친으로 기억 | ✅ PASS | 20.9s | 응답: 지식 1건을 저장했습니다. |
| Phase 3 (키친) | 10. 키친 위치 질의 (내부 로그 누수 검출) | ✅ PASS | 19.9s | 응답: 지식 1건을 검색했습니다. 상위 결과: '키친으'의 위치는 x: -5.14, y: -1.21 입니다. |
| Phase 4 (반복 질의 회귀) | 11. 충전대 재질의 (회귀: 원본 Fail 12/24) | ✅ PASS | 18.2s | 응답: '충전대'의 위치는 x: 3.78, y: 0.86 입니다. |
| Phase 4 (반복 질의 회귀) | 12. 충전대 좌표 재질의 (회귀: 원본 Fail 25) | ✅ PASS | 15.0s | 응답: '충전대'의 위치는 x: 3.78, y: 0.86 입니다. |
| Phase 5 (이름 기반 이동) | 13. 충전대로 이동 (이름 기반, 원본 Fail 22) | ✅ PASS | 64.3s | 응답: 충전대 (3.78, 0.86)지점으로 이동 완료 |
| Phase 5 (이름 기반 이동) | 14. 거실로 이동 (이름 기반, 원본 Fail 14) | ✅ PASS | 28.2s | 응답: 거실 (0.87, 0.80)지점으로 이동 완료 |
| Phase 5 (이름 기반 이동) | 15. 키친으로 이동 (이름 기반) | ✅ PASS | 38.0s | 응답: 키친 (-5.14, -1.21)지점으로 이동 완료 |
| Phase 6 (역방향 조회) | 16. 현재 위치 이름 파악 (역방향 조회, 원본 Fail 17) | ✅ PASS | 17.9s | 응답: 현재 위치는 '키친으'입니다 (거리 0.22m). |
| Phase 6 (역방향 조회) | 17. 좌표 비교로 위치명 파악 (역방향 조회, 원본 Fail 18) | ✅ PASS | 15.9s | 응답: 현재 위치는 '키친으'입니다 (거리 0.22m). |
| Phase 7 (교차언어 / 알려진 한계) | 18. go to living room (교차언어 — 알려진 한계, 기본 SKIP) | ➖ SKIP | - | 비활성화 |

## 상세 로그

### [PASS] 1. 충전대 지점으로 이동  (소요 16.5s)
- 프롬프트: `x=3.96, y=0.83 으로 이동해`
- success 플래그: `True`
- 응답 전문:
```
(3.96, 0.83)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "(3.96, 0.83)지점으로 이동 완료", "duration_sec": 2.009304075999353, "result_data": {"success": true, "message": "(3.96, 0.83)지점으로 이동 완료", "x": 3.96, "y": 0.83}}]}
```

### [PASS] 2. 현재 위치를 충전대로 기억  (소요 20.6s)
- 프롬프트: `현재 위치를 충전대로 기억해`
- success 플래그: `True`
- 응답 전문:
```
지식 1건을 저장했습니다.
```
- result_json:
```json
{"skill_results": [{"skill_name": "rag_add", "code": 0, "message": "지식 1건을 저장했습니다.", "duration_sec": 1.9193881190003594, "result_data": {"success": true, "message": "지식 1건을 저장했습니다.", "text": "충전대", "semantic_updated": true, "deduped": 0}}]}
```

### [PASS] 3. 충전대 위치 질의 (내부 로그 누수 검출)  (소요 18.4s)
- 프롬프트: `충전대 위치를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'충전대'의 위치는 x: 3.78, y: 0.86 입니다.
```
- 참조 Qdrant 항목:
  - 근거=시맨틱 맵 name=충전대 (3.78, 0.86)
- result_json:
```json
{"skill_results": [{"skill_name": "rag_search", "code": 0, "message": "'충전대'의 위치는 x: 3.78, y: 0.86 입니다.", "duration_sec": 0.1763203689997681, "result_data": {"success": true, "message": "'충전대'의 위치는 x: 3.78, y: 0.86 입니다.", "results": [{"text": "'충전대'의 위치는 x: 3.78, y: 0.86 입니다.", "metadata": {"location_name": "충전대", "x": 3.78, "y": 0.86, "type": "location"}, "score": 1.0, "id": null, "source": "시맨틱 맵"}], "count": 1, "referenced": [{"id": null, "source": "시맨틱 맵", "location_name": "충전대", "x": 3.78, "y": 0.86}]}}]}
```

### [PASS] 4. 충전대 좌표 질의  (소요 15.4s)
- 프롬프트: `충전대 위치 좌표를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'충전대'의 위치는 x: 3.78, y: 0.86 입니다.
```
- 참조 Qdrant 항목:
  - 근거=시맨틱 맵 name=충전대 (3.78, 0.86)
- result_json:
```json
{"skill_results": [{"skill_name": "rag_search", "code": 0, "message": "'충전대'의 위치는 x: 3.78, y: 0.86 입니다.", "duration_sec": 0.22274455500064505, "result_data": {"success": true, "message": "'충전대'의 위치는 x: 3.78, y: 0.86 입니다.", "results": [{"text": "'충전대'의 위치는 x: 3.78, y: 0.86 입니다.", "metadata": {"location_name": "충전대", "x": 3.78, "y": 0.86, "type": "location"}, "score": 1.0, "id": null, "source": "시맨틱 맵"}], "count": 1, "referenced": [{"id": null, "source": "시맨틱 맵", "location_name": "충전대", "x": 3.78, "y": 0.86}]}}]}
```

### [PASS] 5. 거실 지점으로 이동  (소요 51.3s)
- 프롬프트: `x=0.66, y=0.73 으로 이동해`
- success 플래그: `True`
- 응답 전문:
```
(0.66, 0.73)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "(0.66, 0.73)지점으로 이동 완료", "duration_sec": 35.84012972900018, "result_data": {"success": true, "message": "(0.66, 0.73)지점으로 이동 완료", "x": 0.66, "y": 0.73}}]}
```

### [PASS] 6. 현재 위치를 거실로 저장  (소요 20.0s)
- 프롬프트: `그 위치를 거실로 저장해`
- success 플래그: `True`
- 응답 전문:
```
지식 1건을 저장했습니다.
```
- result_json:
```json
{"skill_results": [{"skill_name": "rag_add", "code": 0, "message": "지식 1건을 저장했습니다.", "duration_sec": 1.606454828999631, "result_data": {"success": true, "message": "지식 1건을 저장했습니다.", "text": "거실", "semantic_updated": true, "deduped": 0}}]}
```

### [PASS] 7. 거실 위치 질의 (내부 로그 누수 검출)  (소요 18.7s)
- 프롬프트: `거실의 위치를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'거실'의 위치는 x: 0.87, y: 0.80 입니다.
```
- 참조 Qdrant 항목:
  - 근거=시맨틱 맵 name=거실 (0.87, 0.8)
- result_json:
```json
{"skill_results": [{"skill_name": "rag_search", "code": 0, "message": "'거실'의 위치는 x: 0.87, y: 0.80 입니다.", "duration_sec": 0.41673298300065653, "result_data": {"success": true, "message": "'거실'의 위치는 x: 0.87, y: 0.80 입니다.", "results": [{"text": "'거실'의 위치는 x: 0.87, y: 0.80 입니다.", "metadata": {"location_name": "거실", "x": 0.87, "y": 0.8, "type": "location"}, "score": 1.0, "id": null, "source": "시맨틱 맵"}], "count": 1, "referenced": [{"id": null, "source": "시맨틱 맵", "location_name": "거실", "x": 0.87, "y": 0.8}]}}]}
```

### [PASS] 8. 키친 지점으로 이동  (소요 49.8s)
- 프롬프트: `x=-5.36, y=-1.25 로 이동해`
- success 플래그: `True`
- 응답 전문:
```
(-5.36, -1.25)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "(-5.36, -1.25)지점으로 이동 완료", "duration_sec": 33.846872843999336, "result_data": {"success": true, "message": "(-5.36, -1.25)지점으로 이동 완료", "x": -5.36, "y": -1.25}}]}
```

### [PASS] 9. 현재 위치를 키친으로 기억  (소요 20.9s)
- 프롬프트: `그 위치를 키친으로 기억해`
- success 플래그: `True`
- 응답 전문:
```
지식 1건을 저장했습니다.
```
- result_json:
```json
{"skill_results": [{"skill_name": "rag_add", "code": 0, "message": "지식 1건을 저장했습니다.", "duration_sec": 2.5257588990007207, "result_data": {"success": true, "message": "지식 1건을 저장했습니다.", "text": "이곳은 키친으입니다.", "semantic_updated": true, "deduped": 0}}]}
```

### [PASS] 10. 키친 위치 질의 (내부 로그 누수 검출)  (소요 19.9s)
- 프롬프트: `키친 위치를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
지식 1건을 검색했습니다. 상위 결과: '키친으'의 위치는 x: -5.14, y: -1.21 입니다.
```
- 참조 Qdrant 항목:
  - id=019ff89c-79ac-7000-8d55-0aab856293dd score=0.6269 type=location '이곳은 키친으입니다.'
- result_json:
```json
{"skill_results": [{"skill_name": "rag_search", "code": 0, "message": "지식 1건을 검색했습니다. 상위 결과: '키친으'의 위치는 x: -5.14, y: -1.21 입니다.", "duration_sec": 1.4497140490002494, "result_data": {"success": true, "message": "지식 1건을 검색했습니다. 상위 결과: '키친으'의 위치는 x: -5.14, y: -1.21 입니다.", "results": [{"text": "이곳은 키친으입니다.", "score": 0.6269, "metadata": {"location_name": "키친으", "x": -5.14, "y": -1.21, "type": "location"}, "timestamp": "2026-08-13T09:53:51.417808", "id": "019ff89c-79ac-7000-8d55-0aab856293dd"}], "count": 1, "referenced": [{"id": "019ff89c-79ac-7000-8d55-0aab856293dd", "score": 0.6269, "type": "location", "text": "이곳은 키친으입니다."}]}}]}
```

### [PASS] 11. 충전대 재질의 (회귀: 원본 Fail 12/24)  (소요 18.2s)
- 프롬프트: `충전대 위치를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'충전대'의 위치는 x: 3.78, y: 0.86 입니다.
```
- 참조 Qdrant 항목:
  - 근거=시맨틱 맵 name=충전대 (3.78, 0.86)
- result_json:
```json
{"skill_results": [{"skill_name": "rag_search", "code": 0, "message": "'충전대'의 위치는 x: 3.78, y: 0.86 입니다.", "duration_sec": 0.2878000410000823, "result_data": {"success": true, "message": "'충전대'의 위치는 x: 3.78, y: 0.86 입니다.", "results": [{"text": "'충전대'의 위치는 x: 3.78, y: 0.86 입니다.", "metadata": {"location_name": "충전대", "x": 3.78, "y": 0.86, "type": "location"}, "score": 1.0, "id": null, "source": "시맨틱 맵"}], "count": 1, "referenced": [{"id": null, "source": "시맨틱 맵", "location_name": "충전대", "x": 3.78, "y": 0.86}]}}]}
```

### [PASS] 12. 충전대 좌표 재질의 (회귀: 원본 Fail 25)  (소요 15.0s)
- 프롬프트: `충전대 위치 좌표를 알려줘`
- success 플래그: `True`
- 응답 전문:
```
'충전대'의 위치는 x: 3.78, y: 0.86 입니다.
```
- 참조 Qdrant 항목:
  - 근거=시맨틱 맵 name=충전대 (3.78, 0.86)
- result_json:
```json
{"skill_results": [{"skill_name": "rag_search", "code": 0, "message": "'충전대'의 위치는 x: 3.78, y: 0.86 입니다.", "duration_sec": 0.1818399469993892, "result_data": {"success": true, "message": "'충전대'의 위치는 x: 3.78, y: 0.86 입니다.", "results": [{"text": "'충전대'의 위치는 x: 3.78, y: 0.86 입니다.", "metadata": {"location_name": "충전대", "x": 3.78, "y": 0.86, "type": "location"}, "score": 1.0, "id": null, "source": "시맨틱 맵"}], "count": 1, "referenced": [{"id": null, "source": "시맨틱 맵", "location_name": "충전대", "x": 3.78, "y": 0.86}]}}]}
```

### [PASS] 13. 충전대로 이동 (이름 기반, 원본 Fail 22)  (소요 64.3s)
- 프롬프트: `충전대로 이동해`
- success 플래그: `True`
- 응답 전문:
```
충전대 (3.78, 0.86)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "충전대 (3.78, 0.86)지점으로 이동 완료", "duration_sec": 48.789459756000724, "result_data": {"success": true, "message": "충전대 (3.78, 0.86)지점으로 이동 완료", "x": 3.78, "y": 0.86}}]}
```

### [PASS] 14. 거실로 이동 (이름 기반, 원본 Fail 14)  (소요 28.2s)
- 프롬프트: `거실로 이동해`
- success 플래그: `True`
- 응답 전문:
```
거실 (0.87, 0.80)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "거실 (0.87, 0.80)지점으로 이동 완료", "duration_sec": 10.167153633000453, "result_data": {"success": true, "message": "거실 (0.87, 0.80)지점으로 이동 완료", "x": 0.87, "y": 0.8}}]}
```

### [PASS] 15. 키친으로 이동 (이름 기반)  (소요 38.0s)
- 프롬프트: `키친으로 이동해`
- success 플래그: `True`
- 응답 전문:
```
키친 (-5.14, -1.21)지점으로 이동 완료
```
- result_json:
```json
{"skill_results": [{"skill_name": "navigate_to", "code": 0, "message": "키친 (-5.14, -1.21)지점으로 이동 완료", "duration_sec": 19.98162243800016, "result_data": {"success": true, "message": "키친 (-5.14, -1.21)지점으로 이동 완료", "x": -5.14, "y": -1.21}}]}
```

### [PASS] 16. 현재 위치 이름 파악 (역방향 조회, 원본 Fail 17)  (소요 17.9s)
- 프롬프트: `현재 있는 위치의 이름이 뭐야?`
- success 플래그: `True`
- 응답 전문:
```
현재 위치는 '키친으'입니다 (거리 0.22m).
```
- result_json:
```json
{"skill_results": [{"skill_name": "identify_location", "code": 0, "message": "현재 위치는 '키친으'입니다 (거리 0.22m).", "duration_sec": 0.0005149819999132887, "result_data": {"success": true, "message": "현재 위치는 '키친으'입니다 (거리 0.22m).", "location_name": "키친으", "distance_m": 0.22, "position": {"x": -4.97, "y": -1.07}}}]}
```

### [PASS] 17. 좌표 비교로 위치명 파악 (역방향 조회, 원본 Fail 18)  (소요 15.9s)
- 프롬프트: `현재 위치 x, y 좌표와 비교해서 현재 위치의 이름을 파악해봐`
- success 플래그: `True`
- 응답 전문:
```
현재 위치는 '키친으'입니다 (거리 0.22m).
```
- result_json:
```json
{"skill_results": [{"skill_name": "identify_location", "code": 0, "message": "현재 위치는 '키친으'입니다 (거리 0.22m).", "duration_sec": 0.0005027580000387388, "result_data": {"success": true, "message": "현재 위치는 '키친으'입니다 (거리 0.22m).", "location_name": "키친으", "distance_m": 0.22, "position": {"x": -4.97, "y": -1.07}}}]}
```

