# Change note from claude code 20260804

```
● 분석 완료했습니다. 코드까지 확인해보니 실패 케이스 6건이 사실상 하나의 근본 원인에서 나옵니다. 진단 → 실패 매핑 → 수정 의견 순으로 드릴게요.

  근본 원인: 벡터 DB가 "사실"과 "스킬 실행 로그"를 한 통에 섞어 저장

  memory_manager/skill_learning.py:60-121의 record_skill_episode가 모든 스킬 실행(rag_search, rag_add, get_status 포함)을 [스킬 X 성공] 상황: '...'. 파라미터: {...} 형태의 텍스트로
  만들어, 위치 사실과 똑같은 벡터 컬렉션에 add_knowledge로 저장합니다 (:121, metadata.type="skill_episode").

  문제가 두 겹입니다:

  1. rag_search 실행 기록까지 지식으로 저장됨 → 이 기록엔 좌표가 없고 query 문자열만 있습니다. 순수 노이즈인데도 다음 검색에서 상위로 잡힙니다.
  2. recency 재정렬이 사실을 밀어냄 — vector_store/base.py:66-96의 _sort_by_score_and_recency가 점수가 비슷한(epsilon 이내) 결과들 중 더 최신 것을 위로 올립니다. 그래서 처음 질문 때
  만들어진 rag_search 에피소드가, 그보다 오래된 진짜 rag_add 좌표 기록을 눌러버립니다.

  실패 케이스 매핑

  ┌────────┬─────────────────────────────────────────────────────────────────────┬─────────────────────────────────────────────────────────────────────────────────────┐
  │   #    │                                증상                                 │                                        원인                                         │
  ├────────┼─────────────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
  │ 12, 13 │ "충전대 위치 알려줘" 재질문 → 좌표 대신 이전 rag_search 기록을 답변 │ 최신 검색 에피소드가 좌표 기록을 recency로 추월                                     │
  ├────────┼─────────────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
  │ 15     │ "충전대로 이동해" → 이동 안 하고 검색 기록 echo                     │ 최상위 hit이 좌표 없는 에피소드라 네비 좌표 추출 실패                               │
  ├────────┼─────────────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
  │ 20     │ "go to living room" → 이동 안 함                                    │ 영어 질의는 임베딩상 거실과 매칭됐으나(교차언어 OK), 역시 rag_search 에피소드가 hit │
  ├────────┼─────────────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
  │ 17     │ "현재 위치 이름이 뭐야" → 비전 스캔으로 "사무실 추정"               │ 좌표→이름 역방향 조회(reverse geocoding) 미구현, 비전으로 폴백                      │
  ├────────┼─────────────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
  │ 18     │ "x,y 비교해서 위치 이름 파악" → get_status 기록 반환                │ 현재 pose + 저장된 위치 지식 결합 로직 없음                                         │
  └────────┴─────────────────────────────────────────────────────────────────────┴─────────────────────────────────────────────────────────────────────────────────────┘

  부수 문제: 성공 케이스(11)에서도 답변이 [스킬 rag_add 성공] 상황:... 원문을 그대로 사용자에게 노출합니다. 내부 로그 포맷이 UX로 새고 있어요.

  수정 의견 (우선순위순)

  1순위 — 검색 시 스킬 에피소드 오염 차단 (가장 효과 큼)
  - 사용자 사실 질의(위치 조회/이동)에서는 metadata.type="skill_episode"를 검색에서 제외하도록 필터를 겁니다. Qdrant 스토어에 이미 metadata 필터 인프라가 있으니(qdrant.py:133-173)
  저비용입니다.
  - 특히 rag_search/rag_add/rag_delete/get_status 같은 자기참조·조회성 스킬은 애초에 에피소드로 저장하지 않도록 record_skill_episode에 skip 목록을 두세요. 검색 행위를 지식으로 학습하는
  건 무의미합니다.

  2순위 — 위치를 구조화 엔트리로 저장하고 전용 조회 경로 사용
  - 지금 "충전대 좌표"의 실체는 rag_add 에피소드 텍스트라 취약합니다. type="location", location_name, x, y를 metadata에 담은 전용 엔트리로 저장하고, 이름→좌표 해석은 의미검색 top_k가 
  아니라 이름 정확매칭/구조화 조회로 결정론적으로 처리하세요. (이동 명령이 fuzzy 검색에 의존하면 안 됨)

  3순위 — 사실 질의에 recency 재정렬 끄기
  - recency 가중은 관찰(observation) 데이터엔 맞지만 안정적 사실(위치)엔 해롭습니다. type=location 조회는 순수 score 정렬로, 혹은 사실이 에피소드보다 항상 우선하도록 카테고리 정렬.

  4순위 — reverse geocoding 구현 (케이스 17/18)
  - 스토어가 이미 metadata.x/y 반경 필터(current_pose/radius_m, rag.py)를 지원합니다. "현재 위치 이름"은 pose 조회 → type=location 공간검색(반경 내 최근접) → 이름 반환으로 처리. 지금은
  이 인텐트에서 비전 스캔으로 폴백하고 있습니다.

  5순위 — 응답 포맷팅
  - planner가 hit의 원문([스킬 ... 성공] ...)을 그대로 내보내지 말고, metadata에서 좌표/이름을 추출해 자연어로 답하도록. 성공 케이스에서도 내부 로그가 노출되는 걸 막아야 합니다.

  ---
  참고: top_k=2, score_threshold=0.7을 올려서 해결하려는 유혹이 있는데, 오염이 남아 있으면 top_k를 키울수록 노이즈만 더 딸려옵니다. 1·2순위(오염 차단 + 구조화 저장)를 먼저 하는 게
  맞습니다.

  원하시면 1순위(스킬 에피소드 검색 제외 + 조회성 스킬 저장 skip)부터 실제 코드 수정으로 들어갈 수 있습니다. 어느 것부터 진행할까요?


==========================================================================================================


● 1순위를 구현하겠습니다. 두 가지죠: (1) 조회성 스킬은 에피소드로 저장하지 않기, (2) 사실 질의 검색에서 skill_episode 제외. 관련 코드를 먼저 정확히 읽겠습니다.


● 1순위 구현 완료했고, 관련 테스트 전부 통과했습니다 (52건 통과, 1건 skip).

  변경 내용

  A. 조회성/자기참조 스킬은 학습 에피소드로 저장 안 함
  memory_manager/skill_learning.py
  - _NON_LEARNABLE_SKILLS 집합 추가: rag_search, rag_add, rag_add_file, rag_delete, rag_list, rag_status, rag_reindex, get_status
  - record_skill_episode 진입부에서 이 스킬들은 성공/실패 무관하게 False 반환하고 저장 스킵 → [스킬 rag_search 성공]... 같은 좌표 없는 노이즈가 애초에 안 쌓임

  B. 벡터 스토어에 타입 제외 필터 추가
  vector_store/qdrant.py, vector_store/local.py
  - filter_metadata에 type_exclude: [...] 키 지원. Qdrant는 must_not, 로컬은 매칭 스킵으로 구현. 기존 positive 필터/공간 필터와 함께 조합 가능

  C. 사용자 지식 검색에서 skill_episode 제외
  skills/system_skill/rag.py
  - RAGSearchSkill이 search_knowledge(..., filter_metadata={"type_exclude": ["skill_episode"]})로 호출 → 이전 스킬 실행 로그가 실제 위치/사실 항목을 밀어내지 못함

  테스트

  - test_non_learnable_skills_never_recorded 추가 (A 검증)
  - test_qdrant_vector_store_type_exclude_filters_out_episodes 추가 (B/C 검증: 최신 skill_episode가 있어도 제외되고 location 항목만 반환)
  - 기존 get_status 기반 샘플링 테스트 2건은 학습 대상 스킬(navigate_to)로 교체
  - 실행 결과: test_skill_learning / test_vector_store / test_local_vector_store 42 passed, 회귀 확인용 rag 관련 테스트 10 passed, 1 skipped

  이 변경이 해결하는 실패 케이스

  테스트 시나리오의 Fail 12, 13, 15, 20(재질문·이동 시 좌표 대신 rag_search 기록이 튀어나오던 문제)이 직접 해소됩니다.

  남은 항목 (다음 우선순위)

  - 17, 18(좌표→이름 역방향 조회)은 2·4순위(위치 구조화 저장 + reverse geocoding)에서 다룹니다.
  - 성공 케이스에서도 [스킬 rag_add 성공]... 원문이 사용자에게 노출되는 UX 누수는 5순위(응답 포맷팅) 사안입니다.

  이어서 2순위(위치를 type=location 구조화 엔트리로 저장 + 이름↔좌표 결정론적 해석)로 진행할까요?
  
==========================================================================================================
● 2순위를 구현하기 전에, 현재 위치가 어떻게 저장되고 이름→좌표 해석이 어디서 일어나는지 정확히 파악하겠습니다.


  2순위 구현 완료했습니다.

  변경 내용

  1. 이름 조회 정규화 매칭 — memory_manager/semantic.py
  - _normalize_location_name 추가 (공백 제거 + 소문자화)
  - get_object_location: 정확 일치(O(1)) 우선, 실패 시 정규화 매칭 폴백. 이제 "거 실"→거실, "kitchen"/"KITCHEN"→Kitchen이 같은 위치로 해석됨. 이름→좌표 해석이 대소문자·공백에 흔들리지
  않고 결정론적으로 동작

  2. 위치 지식을 type="location"으로 태깅 — skills/system_skill/rag.py
  - _is_location_metadata 헬퍼 추가 (이름 + x/y 좌표 모두 존재 판정)
  - RAGAddSkill.execute가 위치 메타데이터를 저장할 때 type="location"을 자동 스탬프 → 스킬 로그(skill_episode)·관찰(observation)과 구분되는 명확한 "사실" 항목으로 저장됨. 1순위의
  type_exclude 필터, 그리고 앞으로의 사실-우선 랭킹(3순위)·역방향 조회(4순위)의 구조적 기반이 됨

  이미 존재하던 구조 (재확인)

  _resolve_target_coordinates(navigation_skill/core.py)가 이미 시맨틱 맵 → RAG 검색 → 풀스캔 순으로 조회하고, RAGAddSkill이 위치를 시맨틱 맵에
  동기화(_sync_location_to_semantic_map)하고 있었습니다. 즉 구조화 저장소(semantic map) 자체는 있었고, 이번엔 그 조회 견고성과 RAG 측 타입 일관성을 보강한 것입니다.

  테스트

  - test_get_object_location_normalized_match — 공백/대소문자 변형 매칭 검증
  - test_rag_add_stamps_location_type — 위치는 type=location 태깅, 좌표 없는 일반 지식은 미태깅 검증
  - 결과: 관련 스위트 24 passed, 1 skipped, 전체 수집 가능 테스트 250 passed

  남은 항목

  - 3순위: 사실 질의에서 recency 재정렬 끄기(= 위치 사실이 관찰/에피소드보다 항상 우선). 이제 type=location이 붙었으니 깔끔하게 구현 가능
  - 4순위: 좌표→이름 역방향 조회(Fail 17/18). 시맨틱 맵에 최근접 위치 조회 메서드 추가 + 스킬 연결
  - 참고: Fail 20("go to living room"→거실)의 교차언어 매핑은 구조화 정확매칭만으로는 안 되고, aliases에 영어 별칭 등록이 필요합니다(별도 논의)

  3순위로 이어서 진행할까요?
==========================================================================================================
● 3순위를 구현하겠습니다. 먼저 재정렬 로직 전체(_sort_by_score_and_recency, _sort_recency_group)와 상수를 확인합니다.


● 확인됐습니다. 로컬 스토어(local.py:184)도 동일한 _sort_by_score_and_recency를 사용하므로, 이번 수정이 Qdrant·Local 양쪽 백엔드에 일관되게 적용됩니다.

  3순위 구현 완료했습니다.

  변경 내용 — vector_store/base.py

  사실 우선 랭킹 도입
  - _is_stable_fact(entry) 추가: metadata.type == "location"(2순위에서 태깅) 또는 kind == "location"을 안정적 사실로 판정
  - _sort_recency_group 수정: 점수 동률 구간(≤0.02 차이) 정렬 키를 (is_fact, timestamp) 내림차순으로 변경
    - 부류 간: 위치 사실 → 관찰/스킬로그 순 (사실이 항상 위)
    - 부류 내: 최신 우선 (기존 동작 유지)

  왜 이 설계인가

  recency 재정렬 자체를 없애면 "위치 좌표 갱신 시 최신값 우선" 동작(기존 test_...prefers_recent_memory)이 깨집니다. 그래서 사실 vs 변동성 항목 경계에서만 recency를 억제하고, 같은 부류
  안에서는 recency를 유지했습니다.
  - 같은 위치를 다시 저장(좌표 갱신) → 둘 다 사실 → recency로 최신값 우선 ✓
  - 위치 사실 vs 더 최신 관찰(점수 비슷) → 사실이 상위 ✓

  1순위와의 관계 (상호 보완)

  - 1순위: 사용자 지식 검색에서 skill_episode를 아예 제외
  - 3순위: 제외되지 않는 경로(예: _resolve_target_coordinates의 search_knowledge, 관찰 데이터)에서도 점수가 비슷하면 위치 사실이 관찰에 밀리지 않도록 보장

  테스트

  - test_qdrant_vector_store_stable_fact_outranks_recent_observation 추가: 더 최신인 관찰(2026-01-05)이 있어도 위치 사실(2026-01-01)이 상위에 오는지 검증
  - 기존 test_qdrant_vector_store_prefers_recent_memory_when_scores_are_similar(위치 갱신 시 최신 우선) 여전히 통과
  - 결과: vector_store / local / skill_learning / navigation_rag 스위트 46 passed

  진행 현황

  - ✅ 1순위: 스킬 에피소드 오염 차단
  - ✅ 2순위: 위치 구조화 저장 + 이름 조회 정규화
  - ✅ 3순위: 사실 우선 랭킹
  - ⬜ 4순위: 좌표→이름 역방향 조회 (Fail 17/18)
  - ⬜ 5순위: 응답 포맷팅 (내부 스킬 로그 노출 제거)

  4순위로 이어서 진행할까요?
==========================================================================================================
● 4순위(좌표→이름 역방향 조회)를 구현하겠습니다. 먼저 (1) 현재 pose를 어떻게 얻는지, (2) 스킬이 어떻게 등록되는지, (3) system_skill 구조를 파악합니다.


● 253 passed (up from 250 — my 3 new tests). The same 4 failures + 2 errors are the identical pre-existing environmental issues (ROS deps / nonexistent module) — no new regressions.

  4순위 구현 완료했습니다.

  변경 내용

  1. 시맨틱 맵에 좌표→이름 역방향 조회 추가 — memory_manager/semantic.py
  - get_nearest_object(x, y, radius_m): 명명된 위치 중 최근접 항목 반환. alias 항목 제외(표준 이름만), 반경 필터, distance_m 포함. math.hypot 유클리드 거리

  2. identify_location 스킬 신설 — skills/system_skill/rag.py
  - 현재 pose(get_map_pose()) 또는 지정 x/y 좌표를 받아 get_nearest_object로 장소명 판별
  - "여기가 어디야", "현재 위치 이름이 뭐야", "이 좌표는 어디야"류 질문 대응. 반경 내 매칭 없으면 location_name: None 반환
  - system_skill/__init__.py에 export → 자동 등록 + list_skills()로 프롬프트에 설명 자동 주입

  3. 스킬 가이드 수정 — SKILLS.former.md / SKILLS.butler.md / SKILLS.stretch3.md
  - 이게 Fail 17의 직접 원인이었습니다: former 가이드가 "위치 파악 = describe_surroundings/analyze_scene"이라고 명시 → LLM이 비전 스캔으로 폴백
  - 이제 "현재 위치 이름/여기 어디"는 먼저 identify_location으로 기억된 좌표와 비교, 반경 내 없을 때만 시각 추정하도록 지침 변경. 3개 프로필 모두 반영

  테스트

  - test_get_nearest_object_reverse_lookup: 최근접/반경밖/반경미지정 검증
  - test_identify_location_skill_returns_name: 좌표→이름 성공/미매칭 검증
  - 결과: 관련 스위트 40 passed, 전체 253 passed (신규 3건 반영, 회귀 없음)

  Fail 17/18 해소

  - 17 ("현재 위치 이름이 뭐야?") → 가이드 수정 + identify_location으로 저장된 장소명 판별
  - 18 ("x,y 좌표와 비교해서 위치 이름 파악") → 좌표 기반 역방향 조회로 처리

  진행 현황

  - ✅ 1~4순위 완료
  - ⬜ 5순위: 응답 포맷팅 — 성공 케이스에서도 [스킬 rag_add 성공] 상황:... 내부 로그가 사용자에게 노출되는 문제 (planner가 hit 원문 대신 metadata에서 답 추출하도록)

  마지막 5순위로 진행할까요?

==========================================================================================================

● 5순위(응답 포맷팅)를 구현하겠습니다. 핵심은 RAGSearchSkill이 저장된 원문(내부 스킬 로그 포맷 포함)을 그대로 사용자 메시지에 덤프하는 부분입니다. 위치 결과는 metadata에서 좌표를 뽑아   깔끔히 답하고, 혹시 남아있는 레거시 스킬 로그 접두어는 방어적으로 제거하겠습니다.
  
  
  
● 255 passed (up from 253 — my 2 new tests). Same 4 pre-existing failures + 2 environmental errors, no new regressions.

  5순위 구현 완료했습니다. 이로써 1~5순위 전부 완료입니다.

  5순위 변경 내용 — skills/system_skill/rag.py

  1. 위치 결과 metadata 기반 요약
  - _summarize_top_result: 상위 결과가 위치 지식(이름+좌표)이면 저장 원문 대신 metadata에서 '충전대'의 위치는 x: -5.26, y: -1.26 입니다. 형태로 자연어 생성

  2. 내부 스킬 로그 접두어 방어적 제거
  - _SKILL_LOG_PREFIX 정규식 + _sanitize_knowledge_text: [스킬 X 성공/실패] 접두어 제거. 1순위 필터 이전에 이미 적재된 레거시 오염 데이터가 노출되는 걸 막는 안전장치 (기존 Qdrant
  데이터는 코드 수정으로 안 지워지므로 중요)

  3. RAGSearchSkill.execute 적용
  - message는 _summarize_top_result로, 각 results[].text는 _sanitize_knowledge_text로 정리해서 반환

  테스트

  - test_rag_search_formats_location_from_metadata: 위치 검색 시 메시지가 metadata 좌표로 요약되고 [스킬 미노출 검증
  - test_sanitize_strips_skill_log_prefix: 접두어 제거 + 일반 텍스트 보존 검증
  - 결과: 전체 255 passed (신규 2건), 회귀 없음
  

==========================================================================================================
  
전체 요약 (1~5순위)

  ┌──────┬─────────────────────────────────┬────────────────────────────────────────┬──────────────────┐
  │ 순위 │              문제               │                  수정                  │   해소 케이스    │
  ├──────┼─────────────────────────────────┼────────────────────────────────────────┼──────────────────┤
  │ 1    │ 스킬 실행 로그가 사실 검색 오염 │ 조회성 스킬 미기록 + type_exclude 필터 │ Fail 12,13,15,20 │
  ├──────┼─────────────────────────────────┼────────────────────────────────────────┼──────────────────┤
  │ 2    │ 위치 조회 취약(정확매칭만)      │ 이름 정규화 + type=location 태깅       │ 15,20 견고화     │
  ├──────┼─────────────────────────────────┼────────────────────────────────────────┼──────────────────┤
  │ 3    │ 최신 관찰이 위치 사실을 밀어냄  │ 사실 우선 랭킹                         │ 12,13 재발 방지  │
  ├──────┼─────────────────────────────────┼────────────────────────────────────────┼──────────────────┤
  │ 4    │ 좌표→이름 조회 부재             │ identify_location 스킬 + 가이드 수정   │ Fail 17,18       │
  ├──────┼─────────────────────────────────┼────────────────────────────────────────┼──────────────────┤
  │ 5    │ 내부 로그가 사용자에 노출       │ metadata 요약 + 로그 접두어 제거       │ 3,7,11 UX        │
  └──────┴─────────────────────────────────┴────────────────────────────────────────┴──────────────────┘

  변경 파일: memory_manager/{skill_learning,semantic}.py, vector_store/{base,qdrant,local}.py, skills/system_skill/{rag,__init__}.py, config/SKILLS.{former,butler,stretch3}.md + 테스트
  4개 파일. 신규 테스트 총 8건 추가.

  참고 사항 두 가지

  1. 레거시 데이터: 이미 Qdrant(robo_claw_sim_azure 등)에 쌓인 skill_episode 오염 항목은 코드로 안 지워집니다. 5순위 sanitizer가 노출은 막지만, 근본 정리는 rag_reindex 또는
  rag_delete로 별도 수행이 필요합니다.
  2. Fail 20 교차언어("go to living room"→거실): 구조화 정확매칭으로는 안 되고 영어 aliases 등록이 필요합니다(별도 논의 대상). 다만 1·3순위로 검색 노이즈는 제거돼 최소한 오답 echo는
  사라집니다.

  실기(로봇/시뮬)에서 재검증하시려면 시나리오를 다시 돌려보시길 권합니다. 추가로 손볼 부분 있으면 말씀해 주세요.

```
