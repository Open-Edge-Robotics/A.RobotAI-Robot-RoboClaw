# First Use Scenario

## Setting for Hyperparameter of LLM & Embedding Model

```
gemma4:31b
-temperatuer : 0.2
-Repeat Penalty : 1
-Max Predict Token : 512
-Top K : 10
-Top P : 0.9
-Min P : 0.1

qwen3-embedding:8b
```

## Loaded Profile 
```
http://10.159.172.74:30180
 - Former, w2_2f_2
```

## Testcases and Results
```
1. Success
   User : 현재 있는 위치의 좌표를 알려줘.
   Robot : 현재 로봇 상태 조회 완료 (배터리: unknown, 위치: {'x': 3.96, 'y': 0.83, 'heading_deg': -74.1, 'frame': 'map'})

2. Success
   User : 그 위치를 충전대로 기억해
   Robot : 지식 1건을 저장했습니다.

3. Success
   User : 충전대 위치를 알려줘
   Robot : 지식 2건을 검색했습니다. 상위 결과: [스킬 rag_add 성공] 상황: '그 위치를 충전대로 기억해'. 파라미터: {"text": "충전대의 위치는 x: 3.96, y: 0.83 (frame: map)입니다.", "metadata": {"location_name": "충전대", "x": 3.96, "y": 0.83, "frame": "map"}}
   
4. Success
   User : x=0.45, y=0.70 으로 이동해
   Robot : (0.45, 0.70)지점으로 이동을 시작합니다.
           (0.45, 0.70)지점에 무사히 도착했습니다. 
           (0.45, 0.70)지점으로 이동 완료
           
5. Success
   User : 현재 위치를 알려줘
   Robot : 현재 로봇 상태 조회 완료 (배터리: unknown, 위치: {'x': 0.66, 'y': 0.73, 'heading_deg': 70.8, 'frame': 'map'})
   
6. Success
   User : 그 위치를 거실로 저장해
   robot : 지식 1건을 저장했습니다.

7. Success
   User : 거실의 위치를 알려줘
   robot : 지식 2건을 검색했습니다. 상위 결과: [스킬 rag_add 성공] 상황: '그 위치를 거실로 저장해'. 파라미터: {"text": "거실의 위치는 x: 0.66, y: 0.73 (frame: map)입니다.", "metadata": {"location_name": "거실", "x": 0.66, "y": 0.73, "frame": "map"}}.
   
8. Success
   User : 내가 조이스틱으로 이동할께
   Robot : 네, 알겠습니다! 조이스틱으로 직접 제어해 주세요. 저는 여기서 대기하며 필요하신 일이 생기면 언제든 말씀해 주세요.
   
9. Success
   User : 현재 위치를 알려줘.
   Robot : 현재 로봇 상태 조회 완료 (배터리: unknown, 위치: {'x': -5.36, 'y': -1.25, 'heading_deg': 85.5, 'frame': 'map'})

10. Success
   User : 그 위치를 키친으로 기억해
   Robot : 지식 1건을 저장했습니다.

11. Success
   User : 키친 위치를 알려줘
   Robot : 지식 1건을 검색했습니다. 상위 결과: [스킬 rag_add 성공] 상황: '그 위치를 키친으로 기억해'. 파라미터: {"text": "키친의 위치는 x: -5.36, y: -1.25 (frame: map)입니다.", "metadata": {"location_name": "키친", "x": -5.36, "y": -1.25, "frame": "map"}}

12. Fail
   User : 충전대 위치를 알려줘
   Robot : 지식 3건을 검색했습니다. 상위 결과: [스킬 rag_search 성공] 상황: '충전대 위치를 알려줘'. 파라미터: {"query": "충전대 위치"}.

13. Success
   User : 충전대 위치 x, y 좌표 알려줘
   Robot : 지식 3건을 검색했습니다. 상위 결과: [스킬 rag_add 성공] 상황: '그 위치를 충전대로 기억해'. 파라미터: {"text": "충전대의 위치는 x: 3.96, y: 0.83 (frame: map)입니다.", "metadata": {"location_name": "충전대", "x": 3.96, "y": 0.83, "frame": "map"}}.

14. Fail
   User : 거실로 이동해
   Robot : 지식 3건을 검색했습니다. 상위 결과: [스킬 rag_search 성공] 상황: '거실의 위치를 알려줘'. 파라미터: {"query": "거실의 위치"}.

15. Success
   User : 충전대로 이동해
   Robot : 충전대 (3.96, 0.83)지점으로 이동을 시작합니다.
                    충전대 (3.96, 0.83)지점에 무사히 도착했습니다.
                    충전대 (3.96, 0.83)지점으로 이동 완료
                    
16. Success
   User : x: -5.26, y: -1.26 로 이동해
   Robot :  (-5.26, -1.26)지점으로 이동을 시작합니다.
            (-5.26, -1.26)지점에 무사히 도착했습니다.
            (-5.26, -1.26)지점으로 이동 완료
            
17. Fail
   User : 현재 있는 위치의 이름이 뭐야. ?
   Robot : 사방을 둘러보며 위치를 파악합니다.
           { "location_analysis": { "estimated_place": "사무실 또는 연구실", "summary": "로봇은 현재 사무용 의자와 금속제 작업대가 배치된 사무실 또는 연구실 환경에 위치해 있습니다. 정면과 후방으로는 흰색 벽면과 형광등 조명이 보이며, 우측에는 검은색 가림막이 설치되어 공간이 구분되어 있습니다. 전반적으로 평평한 바닥과 작업 장비들이 배치되어 있는 정돈된 실내 공간으로 판단됩니다." } }
            사진 4장
     { "location_analysis": { "estimated_place": "사무실 또는 연구실", "summary": "로봇은 현재 사무용 의자와 금속제 작업대가 배치된 사무실 또는 연구실 환경에 위치해 있습니다. 정면과 후방으로는 흰색 벽면과 형광등 조명이 보이며, 우측에는 검은색 가림막이 설치되어 공간이 구분되어 있습니다. 전반적으로 평평한 바닥과 작업 장비들이 배치되어 있는 정돈된 실내 공간으로 판단됩니다."
   
18. Fail
   User : 현재 위치 x, y 좌표와 비교해서 현재 위치의 이름을 파악해봐
   Robot : 지식 3건을 검색했습니다. 상위 결과: [스킬 get_status 성공] 상황: '현재 위치 x, y 좌표와 비교해서 현재 위치의 이름을 파악해봐'.
   
19. Success
   User : 키친으로 이동해
   Robot : 키친  (-5.36, -1.25)지점으로 이동을 시작합니다.
                  키친  (-5.36, -1.25)지점에 무사히 도착했습니다.
                   키친  (-5.36, -1.25)지점으로 이동 완료 
   

20. Fail
   User : go to living room
   Robot : 지식 3건을 검색했습니다. 상위 결과: [스킬 rag_search 성공] 상황: '거실의 위치를 알려줘'. 파라미터: {"query": "거실의 위치"}.

21. Success
   User : 거실로 이동해
   Robot : 거실  (0.66, 0.73)지점으로 이동을 시작합니다.
                 거실  (0.66, 0.73)지점에 무사히 도착했습니다.
                거실  (0.66, 0.73)지점으로 이동 완료
                
                
22. Fail
   User : 충전대로 이동해
   Robot : 지식 3건을 검색했습니다. 상위 결과: [스킬 rag_search 성공] 상황: '충전대 위치를 알려줘'. 파라미터: {"query": "충전대 위치"}. 

23. Success
   User : 반바퀴 회전해
   Robot : 로봇을 180.0도 회전시킵니다.
                    회전을 완료했습니다 (180.0도)
          180.0도 회전 완료
          
24. Fail
   User : 충전대 위치를 알려줘
   Robot : 지식 3건을 검색했습니다. 상위 결과: [스킬 rag_search 성공] 상황: '충전대 위치를 알려줘'. 파라미터: {"query": "충전대 위치"}.
          
          
25. Fail
   User : 충전대 위치 좌표를 알려줘
   Robot : 지식 3건을 검색했습니다. 상위 결과: [스킬 rag_search 성공] 상황: '충전대 위치를 알려줘'. 파라미터: {"query": "충전대 위치"}.      
```
   
   
   
   
   


   
   
   
   
