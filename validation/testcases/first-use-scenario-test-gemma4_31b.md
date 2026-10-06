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
   Robot : 현재 제가 있는 위치의 좌표는 x: 4.14, y: 0.88입니다.

2. Success
   User : 그 위치를 키친 (Kitchen) 으로 기억해
   Robot : 지식 1건을 저장했습니다.

3. Success
   User : 키친 위치를 알려줘
   Robot : 지식 2건을 검색했습니다. 상위 결과: [스킬 rag_add 성공] 상황: '그 위치를 키친 (Kitchen) 으로 기억해'. 파라미터: {"text": "키친 (Kitchen)의 위치 좌표는 x: 4.14, y: 0.88입니다.", "metadata": {"location_name": "Kitchen", "x": 4.14, "y": 0.88, "map": "w2_2f"}}.
   
4. Success
   User : x=0.45, y=0.70 으로 이동해
   Robot : (0.45, 0.70)지점으로 이동을 시작합니다.
           (0.45, 0.70)지점에 무사히 도착했습니다. 
           (0.45, 0.70)지점으로 이동 완료
           
5. Success
   User : 현재 위치를 알려줘.
   Robot : 현재 로봇 상태 조회 완료 (배터리: unknown, 위치: {'x': 0.56, 'y': 0.75, 'heading_deg': -27.7, 'frame': 'map'})
   
6. Success
   User : 그 위치를 거실로 저장해
   robot : 지식 1건을 저장했습니다.

7. Success
   User : 거실의 위치를 알려줘
   robot : 지식 3건을 검색했습니다. 상위 결과: [스킬 rag_add 성공] 상황: '그 위치를 거실로 저장해'. 파라미터: {"text": "거실의 위치 좌표는 x: 0.56, y: 0.75입니다.", "metadata": {"location_name": "거실", "x": 0.56, "y": 0.75, "map": "w2_2f"}}.
   
8. Success
   User : 내가 조이스틱으로 이동할께.
   Robot : 네, 알겠습니다! 조이스틱으로 직접 제어해 주세요. 저는 여기서 대기하며 필요하신 때에 다시 도와드리겠습니다.
   
9. Success
   User : 현재 위치를 알려줘.
   Robot : 현재 로봇 상태 조회 완료 (배터리: unknown, 위치: {'x': -5.26, 'y': -1.26, 'heading_deg': 92.7, 'frame': 'map'})

10. Success
   User : 그 위치를 충전대 로 기억해
   Robot : 지식 1건을 저장했습니다.

11. Success
   User : 충전대 위칠르 알려줘
   Robot : 지식 2건을 검색했습니다. 상위 결과: [스킬 rag_add 성공] 상황: '그 위치를 충전대 로 기억해'. 파라미터: {"text": "충전대의 위치 좌표는 x: -5.26, y: -1.26입니다.", "metadata": {"location_name": "충전대", "x": -5.26, "y": -1.26, "map": "w2_2f"}}.

12. Fail
   User : 충전대 위치를 알려줘
   Robot : 지식 3건을 검색했습니다. 상위 결과: [스킬 rag_search 성공] 상황: '충전대 위칠르 알려줘'. 파라미터: {"query": "충전대 위치"}.

13. Fail
   User : 충전대 위치 x, y 좌표 알려줘
   Robot : 지식 3건을 검색했습니다. 상위 결과: [스킬 rag_search 성공] 상황: '충전대 위치를 알려줘'. 파라미터: {"query": "충전대 위치"}.

14. Success
   User : 거실로 이동해
   Robot : 거실 (0.56, 0.75)지점으로 이동을 시작합니다.
                     거실 (0.56, 0.75)지점에 무사히 도착했습니다.
                    거실 (0.56, 0.75)지점으로 이동 완료

15. Fail
   User : 충전대로 이동해
   Robot : 지식 3건을 검색했습니다. 상위 결과: [스킬 rag_search 성공] 상황: '충전대 위치를 알려줘'. 파라미터: {"query": "충전대 위치"}
   
16. Success
   User : x: -5.26, y: -1.26 로 이동해
   Robot :  (-5.26, -1.26)지점으로 이동을 시작합니다.
            (-5.26, -1.26)지점에 무사히 도착했습니다.
            (-5.26, -1.26)지점으로 이동 완료
            
17. Fail
   User : 현재 있는 위치의 이름이 뭐야. ?
   Robot : 사방을 둘러보며 위치를 파악합니다.
            "analysis": { "estimated_location": "사무실 또는 작업실", "summary": "로봇은 현재 사무용 의자와 바퀴 달린 책상 프레임이 있는 사무 공간에 위치해 있습니다. 주변에는 흰색과 회색 벽면이 있으며, 우측 바닥에는 빨간색 가방이 놓여 있습니다. 좌측 창문에는 블라인드가 내려져 있고 바닥에 전선들이 흩어져 있는 상태입니다." } }
            사진 4장
     "analysis": { "estimated_location": "사무실 또는 작업실", "summary": "로봇은 현재 사무용 의자와 바퀴 달린 책상 프레임이 있는 사무 공간에 위치해 있습니다. 주변에는 흰색과 회색 벽면이 있으며, 우측 바닥에는 빨간색 가방이 놓여 있습니다. 좌측 창문에는 블라인드가 내려져 있고 바닥에 전선들이 흩어져 있는 상태입니다." } }
   
18. Fail
   User : 위치 x, y 좌표와 비교해서 현재 위치의 이름을 파악해봐
   Robot : 지식 3건을 검색했습니다. 상위 결과: [스킬 get_status 성공] 상황: '위치 x, y 좌표와 비교해서 현재 위치의 이름을 파악해봐'.
   
19. Success
   User : 키친으로 이동해
   Robot : 키친 (4.14, 0.88)지점으로 이동을 시작합니다.
                  키친 (4.14, 0.88)지점에 무사히 도착했습니다.
                   키친 (4.14, 0.88)지점으로 이동 완료 
   

20. Fail
   User : go to living room
   Robot : 지식 3건을 검색했습니다. 상위 결과: [스킬 rag_search 성공] 상황: '거실의 위치를 알려줘'. 파라미터: {"query": "거실 위치"}.

21. Success
   User : 거실로 이동해
   Robot : 거실 (0.56, 0.75)지점으로 이동을 시작합니다.
                 거실 (0.56, 0.75)지점에 무사히 도착했습니다.
                거실 (0.56, 0.75)지점으로 이동 완료
                
                
22. Success
   User : 키친으로 이동해
   Robot : 키친 (4.14, 0.88)지점으로 이동을 시작합니다.
                  키친 (4.14, 0.88)지점에 무사히 도착했습니다.
                   키친 (4.14, 0.88)지점으로 이동 완료 

23. Success
   User : 반바퀴 회전해
   Robot : 로봇을 180.0도 회전시킵니다.
                    회전을 완료했습니다 (180.0도)
          180.0도 회전 완료
          
```
   
   
   
   
   


   
   
   
   
