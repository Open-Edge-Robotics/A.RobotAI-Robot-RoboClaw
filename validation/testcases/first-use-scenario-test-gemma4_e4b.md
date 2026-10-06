# First Use Scenario

```
gemma4:e4b
-temperatuer : 0
-Repeat Penalty : 1
-Max Predict Token : -1
-Top K : 1
-Top P : 1
-Min P : 0

qwen3-embedding:8b

```

```
1. 
   User : 현재 있는 위치의 좌표를 알려줘.
   Robot : 현재 로봇 상태 조회 완료 (배터리: unknown, 위치: {'x': -4.26, 'y': 0.69, 'heading_deg': -20.0, 'frame': 'map'})

2. 
   User : 그 위치를 키친 (Kitchen) 으로 기억해
   Robot : 지식 1건을 저장했습니다.

3. 
   User : 키친 위치를 알려줘
   Robot : 지식 1건을 검색했습니다. 상위 결과: [스킬 rag_add 성공] 상황: '그 위치를 키친 (Kitchen) 으로 기억해'. 파라미터: {"text": "현재 로봇의 위치는 W2 건물 2층의 (x: -4.26, y: 0.69) 좌표이며, 이 위치는 '키친(Kitchen)'으로 지정합니다.", "metadata": {"location_name": "Kitchen", "x": -4.26, "y": 0.69, "source": "user_command"}}.
   
4. 
   User : x=0.45, y=0.70 으로 이동해
   Robot : (0.45, 0.70)지점으로 이동을 시작합니다.
           (0.45, 0.70)지점에 무사히 도착했습니다. 
           (0.45, 0.70)지점으로 이동 완료
           
5. 
   User : 현재 위치를 알려줘.
   Robot : 현재 로봇 상태 조회 완료 (배터리: unknown, 위치: {'x': 0.48, 'y': 0.49, 'heading_deg': 114.9, 'frame': 'map'})
   
6. 
   User : 그 위치를 거실로 저장해
   robot : 지식 1건을 저장했습니다.

7. 
   User : 거실의 위치를 알려줘
   robot : 지식 1건을 검색했습니다. 상위 결과: [스킬 rag_add 성공] 상황: '그 위치를 거실로 저장해'. 파라미터: {"text": "현재 로봇의 위치는 W2 건물 2층의 (x: 0.48, y: 0.49) 좌표이며, 이 위치는 '거실(Living Room)'로 지정합니다.", "metadata": {"location_name": "거실(Living Room)", "x": 0.48, "y": 0.49, "source": "user_command"}}.
   
8. 
   User : 내가 조이스틱으로 이동할께.
   Robot : 저는 주행 및 관측 전용 로봇인 Former입니다. 물리적인 조이스틱 조작은 제가 직접 할 수 없습니다. 혹시 특정 좌표나 방향으로 이동해야 한다면, 제가 [navigate_to]나 [move_relative] 스킬을 사용해 이동을 도와드릴 수 있습니다. 아니면, 제가 현재 위치를 기준으로 이동할 방향을 말씀해주시겠어요?
   
9. 
   User : 현재 위치를 알려줘.
   Robot : 현재 로봇 상태 조회 완료 (배터리: unknown, 위치: {'x': 4.14, 'y': 0.88, 'heading_deg': 179.6, 'frame': 'map'})

10.
   User : 그 위치를 충전대 로 기억해
   Robot : 지식 1건을 저장했습니다.

11. 
   User : 충전대 위칠르 알려줘
   Robot : 상황 분석 완료 — 객체: 없음

12. 
   User : 충전대 위치를 알려줘
   Robot : 상황 분석 완료 — 객체: 없음

13.
   User : 충전대 위치 x, y 좌표 알려줘
   Robot : 지식 1건을 검색했습니다. 상위 결과: [스킬 describe_surroundings 성공] 상황: '충전대 위치를 알려줘'.
```   

   
   
   
   
   


   
   
   
   
