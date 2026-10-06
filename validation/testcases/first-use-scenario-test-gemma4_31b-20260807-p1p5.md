# First Use Scenario

## Setting for Hyperparameter of LLM & Embedding Model

```
LLM : 
-Model : gemma4:31b
-Model URL :
-num_ctx : 32768
-temperature : 0.2
-Repeat Penalty : 1
-Repeat Last N : 64
-Seed : 
-Max Predict Token : 1024
-Top K : 10
-Top P : 0.9
-Min P : 0.1

Embedding Model
-Model : qwen3-embedding:8b
-Qdrant URL : 
-Qdrant Collection Name: 
-Qdrant Timeout : 
-RAG Top-K:
-RAG Score Threshold:
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
   Robot : 현재 로봇 상태 조회 완료 (배터리: unknown, 위치: {'x': 3.86, 'y': 0.83, 'heading_deg': -164.9, 'frame': 'map'})

2. Success
   User : 그 위치를 충전대로 기억해
   Robot : 지식 1건을 저장했습니다.

*Qdrant Collection 저장 내용:

"text":"충전대의 위치 좌표는 x: 3.86, y: 0.83 (map 프레임)입니다."
"metadata":{
"location_name":"충전대"
"x":3.86
"y":0.83
"frame":"map"
"type":"location"
}
"timestamp":"2026-08-07T10:45:57.109564"


3. Success
   User : 충전대 위치를 알려줘
   Robot : 지식 1건을 검색했습니다. 상위 결과: '충전대'의 위치는 x: 3.86, y: 0.83 입니다.
   
   
3-1. Success
   User : 현재 있는 위치가 충전대 인데, 방향도 함께 저장해
   Robot : 지식 1건을 저장했습니다.

*Qdrant Collection 저장 내용:

{
"text":"충전대의 위치는 x: 3.86, y: 0.83이며, 방향(heading)은 -164.9도입니다."
"metadata":{
"location":"충전대"
"x":3.86
"y":0.83
"heading_deg":-164.9
}
"timestamp":"2026-08-07T10:49:47.320091"
}
   
   
4. Success
   User : x=0.45, y=0.70 으로 이동해
   Robot : (0.45, 0.70)지점으로 이동을 시작합니다.
           (0.45, 0.70)지점에 무사히 도착했습니다. 
           (0.45, 0.70)지점으로 이동 완료
           
*Qdrant Collection 저장 내용:
{
"text":"이동 성공 좌표: x=0.45, y=0.70 ((0.45, 0.70))"
"metadata":{
"type":"navigated_coordinate"
"kind":"place"
"x":0.45
"y":0.7
"source":"navigate_to"
}
"timestamp":"2026-08-07T10:50:40.574096"
}


{
"text":"[스킬 navigate_to 성공] 상황: 'x=0.45, y=0.70 으로 이동해'. 파라미터: {"x": 0.45, "y": 0.7, "frame_id": "map"}."
"metadata":{
"type":"skill_episode"
"skill_name":"navigate_to"
"success":true
"duration_sec":11.199
"error":""
"params_brief":"{"x": 0.45, "y": 0.7, "frame_id": "map"}"
"instruction":"x=0.45, y=0.70 으로 이동해"
"replanned":false
"recovered":false
"value_reason":"sparse"
}
"timestamp":"2026-08-07T10:50:40.891332"
}


5. Success
   User : 현재 위치를 알려줘
   Robot : 현재 위치(x: 0.60, y: 0.73) 반경 2.0m 안에 기억된 장소가 없습니다.

*Qdrant Collection 저장 내용:
{
"text":"[스킬 identify_location 성공] 상황: '현재 위치를 알려줘'."
"metadata":{
"type":"skill_episode"
"skill_name":"identify_location"
"success":true
"duration_sec":0
"error":""
"params_brief":""
"instruction":"현재 위치를 알려줘"
"replanned":false
"recovered":false
"value_reason":"sparse"
}
"timestamp":"2026-08-07T10:51:59.421697"

6. Success
   User : 그 위치를 방향과 함께 거실로 저장해
   robot : 지식 1건을 저장했습니다.

*Qdrant Collection 저장 내용:
{
"text":"거실의 위치는 x: 0.60, y: 0.73이며, 방향(heading)은 -97.8도입니다."
"metadata":{
"location":"거실"
"x":0.6
"y":0.73
"heading_deg":-97.8
}
"timestamp":"2026-08-07T10:53:41.192107"
}


7. Success
   User : 거실의 위치를 알려줘
   robot : 지식 1건을 검색했습니다. 상위 결과: 거실의 위치는 x: 0.60, y: 0.73이며, 방향(heading)은 -97.8도입니다.
   
   
8. Success
   User : 내가 조이스틱으로 이동할께
   Robot : 네, 알겠습니다! 조이스틱으로 직접 제어해 주세요. 저는 여기서 대기하며 필요하신 때에 다시 말씀해 주시면 바로 도와드리겠습니다.
   
9. Success
   User : 현재 위치를 알려줘.
   Robot : 현재 위치(x: -5.23, y: -1.24) 반경 2.0m 안에 기억된 장소가 없습니다.

*Qdrant Collection 저장 내용:
{
"text":"[스킬 identify_location 성공] 상황: '현재 위치를 알려줘.'."
"metadata":{
"type":"skill_episode"
"skill_name":"identify_location"
"success":true
"duration_sec":0.001
"error":""
"params_brief":""
"instruction":"현재 위치를 알려줘."
"replanned":false
"recovered":false
"value_reason":"sparse"
}
"timestamp":"2026-08-07T10:57:10.001353"
}


10. Success
   User : 그 위치를 키친으로 기억해
   Robot : 지식 1건을 저장했습니다.

*Qdrant Collection 저장 내용:
{
"text":"키친의 위치는 x: -5.23, y: -1.24이며, 현재 방향은 97.1도입니다."
"metadata":{
"location":"kitchen"
"x":-5.23
"y":-1.24
"heading":97.1
}
"timestamp":"2026-08-07T10:58:55.894099"
}


11. Success
   User : 키친 위치를 알려줘
   Robot : 지식 1건을 검색했습니다. 상위 결과: 키친의 위치는 x: -5.23, y: -1.24이며, 현재 방향은 97.1도입니다.


12. Success
   User : 충전대 위치를 알려줘
   Robot : 지식 2건을 검색했습니다. 상위 결과: '충전대'의 위치는 x: 3.86, y: 0.83 입니다.


13. Success
   User : 충전대 위치 x, y 좌표 알려줘
   Robot : 충전대의 위치 좌표는 x: 3.86, y: 0.83입니다.


14. Success
   User : 거실로 이동해
   Robot : 거실 (0.65, 0.70)지점으로 이동을 시작합니다.
                     거실 (0.65, 0.70)지점에 무사히 도착했습니다.
                     거실 (0.65, 0.70)지점으로 이동 완료

*Qdrant Collection 저장 내용:
{
"text":"[스킬 navigate_to 성공] 상황: '거실로 이동해'. 파라미터: {"target_name": "거실"}."
"metadata":{
"type":"skill_episode"
"skill_name":"navigate_to"
"success":true
"duration_sec":20.792
"error":""
"params_brief":"{"target_name": "거실"}"
"instruction":"거실로 이동해"
"replanned":false
"recovered":false
"value_reason":"sparse"
}
"timestamp":"2026-08-07T11:04:36.112320"
}


{
"text":"이동 성공 좌표: x=0.60, y=0.73 (거실 (0.60, 0.73))"
"metadata":{
"type":"navigated_coordinate"
"kind":"place"
"x":0.6
"y":0.73
"source":"navigate_to"
}
"timestamp":"2026-08-07T11:04:35.694130"
}

15. Success
   User : 충전대로 이동해
   Robot : 충전대 (3.86, 0.83)지점으로 이동을 시작합니다.
                    충전대 (3.86, 0.83)지점에 무사히 도착했습니다.
                    충전대 (3.86, 0.83)지점으로 이동 완료
 
*Qdrant Collection 저장 내용: 
{
"text":"[스킬 navigate_to 성공] 상황: '충전대로 이동해'. 파라미터: {"target_name": "충전대"}."
"metadata":{
"type":"skill_episode"
"skill_name":"navigate_to"
"success":true
"duration_sec":35.701
"error":""
"params_brief":"{"target_name": "충전대"}"
"instruction":"충전대로 이동해"
"replanned":false
"recovered":false
"value_reason":"sparse"
}
"timestamp":"2026-08-07T11:06:59.250663"
}


{
"text":"이동 성공 좌표: x=3.86, y=0.83 (충전대 (3.86, 0.83))"
"metadata":{
"type":"navigated_coordinate"
"kind":"place"
"x":3.86
"y":0.83
"source":"navigate_to"
}
"timestamp":"2026-08-07T11:06:58.848837"
}

                    
16. Success
   User : x: -5.26, y: -1.26 로 이동해
   Robot :  (-5.26, -1.26)지점으로 이동을 시작합니다.
            (-5.26, -1.26)지점에 무사히 도착했습니다.
            (-5.26, -1.26)지점으로 이동 완료
            
*Qdrant Collection 저장 내용: 
{
"text":"[스킬 navigate_to 성공] 상황: 'x: -5.26, y: -1.26 로 이동해'. 파라미터: {"x": -5.26, "y": -1.26, "frame_id": "map"}."
"metadata":{
"type":"skill_episode"
"skill_name":"navigate_to"
"success":true
"duration_sec":31.591
"error":""
"params_brief":"{"x": -5.26, "y": -1.26, "frame_id": "map"}"
"instruction":"x: -5.26, y: -1.26 로 이동해"
"replanned":false
"recovered":false
"value_reason":"sparse"
}
"timestamp":"2026-08-07T11:08:49.239237"
}

{
"text":"[스킬 identify_location 성공] 상황: '현재 있는 위치의 이름이 뭐야?'."
"metadata":{
"type":"skill_episode"
"skill_name":"identify_location"
"success":true
"duration_sec":0.001
"error":""
"params_brief":""
"instruction":"현재 있는 위치의 이름이 뭐야?"
"replanned":false
"recovered":false
"value_reason":"sparse"
}
"timestamp":"2026-08-07T11:09:20.271346"
}



17. Fail
   User : 현재 있는 위치의 이름이 뭐야?
   Robot : 현재 위치(x: -5.09, y: -1.14) 반경 2.0m 안에 기억된 장소가 없습니다.
   
17-1. Success
   User : 키친 위치 알려줘
   Robot : 지식 1건을 검색했습니다. 상위 결과: 키친의 위치는 x: -5.23, y: -1.24이며, 현재 방향은 97.1도입니다.
   
17-2. Success
   User : 현재 위치와 키친의 위치 사이 거리는 얼마야?
   Robot : 현재 제 위치는 (-5.09, -1.14)이고, 키친의 위치는 (-5.23, -1.24)입니다. 두 지점 사이의 직선 거리는 약 0.17m로, 매우 가까운 거리에 있습니다.
   
17-3. Fail
   User : 그럼 현재 위치가 키친 이라고 해도 되는 거 아닌가?
   Robot : 현재 위치(x: -5.09, y: -1.14) 반경 2.0m 안에 기억된 장소가 없습니다.

*Qdrant Collection 저장 내용: 
{
"text":"[스킬 identify_location 성공] 상황: '그럼 현재 위치가 키친 이라고 해도 되는 거 아닌가?'."
"metadata":{
"type":"skill_episode"
"skill_name":"identify_location"
"success":true
"duration_sec":0.001
"error":""
"params_brief":""
"instruction":"그럼 현재 위치가 키친 이라고 해도 되는 거 아닌가?"
"replanned":false
"recovered":false
"value_reason":"sparse"
}
"timestamp":"2026-08-07T11:12:31.025143"
}


18. Fail
   User : 현재 위치 x, y 좌표와 비교해서 현재 위치의 이름을 파악해봐
   Robot : 현재 위치(x: -5.09, y: -1.14) 반경 2.0m 안에 기억된 장소가 없습니다.
 
  
19. Success or Fail
   User : 키친으로 이동해
   Robot : 키친 (-5.23, -1.24)지점으로 이동을 시작합니다.
                  키친 (-5.23, -1.24)지점에 무사히 도착했습니다.
                   키친 (-5.23, -1.24)지점으로 이동 완료
   **전혀 움직임은 없었음**

20. Fail
   User : go to living room
   Robot : 실패: 지식 2건을 검색했습니다. 상위 결과: 이동 성공 좌표: x=0.60, y=0.73 (거실 (0.60, 0.73))


21. Success
   User : 거실로 이동해
   Robot : 거실  (0.60, 0.73)지점으로 이동을 시작합니다.
                 거실  (0.60, 0.73)지점에 무사히 도착했습니다.
                거실  (0.60, 0.73)지점으로 이동 완료
                
*Qdrant Collection 저장 내용:                 
{
"text":"이동 성공 좌표: x=0.60, y=0.73 (거실 (0.60, 0.73))"
"metadata":{
"type":"navigated_coordinate"
"kind":"place"
"x":0.6
"y":0.73
"source":"navigate_to"
}
"timestamp":"2026-08-07T11:18:23.050856"
}                
                
22. Success
   User : 충전대로 이동해
   Robot : 충전대 (3.86, 0.83)지점으로 이동을 시작합니다.
                    충전대 (3.86, 0.83)지점에 무사히 도착했습니다.
                    충전대 (3.86, 0.83)지점으로 이동 완료

*Qdrant Collection 저장 내용: 
{
"text":"이동 성공 좌표: x=3.86, y=0.83 (충전대 (3.86, 0.83))"
"metadata":{
"type":"navigated_coordinate"
"kind":"place"
"x":3.86
"y":0.83
"source":"navigate_to"
}
"timestamp":"2026-08-07T11:20:02.799567"
}

23. Success
   User : 반바퀴 회전해
   Robot : 로봇을 180.0도 회전시킵니다.
                    회전을 완료했습니다 (180.0도)
          180.0도 회전 완료
          
23-1. Success
   User : 60도 회전해
   Robot : 로봇을 60.0도 회전시킵니다.
                    회전을 완료했습니다 (60.0도).
          60.0도 회전 완료


24. Success
   User : 충전대 위치를 알려줘
   Robot : 지식 3건을 검색했습니다. 상위 결과: 이동 성공 좌표: x=3.86, y=0.83 (충전대 (3.86, 0.83))
          
          
25. Partially Success (거실 위치가 없음)
   User : 키친과 거실 위치 알려줘
   Robot : 지식 3건을 검색했습니다. 상위 결과: 이동 성공 좌표: x=-5.23, y=-1.24 (키친 (-5.23, -1.24))


26. Partially Success (도착했으나, 스스로 실패라고 함)
   User : go to Kitchen
   Robot : 키친 (-5.23, -1.24)지점으로 이동을 시작합니다.
                키친 (-5.23, -1.24)지점에 무사히 도착했습니다.
                 키친 (-5.23, -1.24)지점으로 이동 완료

*Qdrant Collection 저장 내용: 
{
"text":"이동 성공 좌표: x=-5.23, y=-1.24 (키친 (-5.23, -1.24))"
"metadata":{
"type":"navigated_coordinate"
"kind":"place"
"x":-5.23
"y":-1.24
"source":"navigate_to"
}
"timestamp":"2026-08-07T11:25:50.671175"
}



27. Success
   User : go to charging station
   Robot : 충전대 (3.86, 0.83)지점으로 이동을 시작합니다.
                    충전대 (3.86, 0.83)지점에 무사히 도착했습니다.
                    충전대 (3.86, 0.83)지점으로 이동 완료

*Qdrant Collection 저장 내용: 
{
"text":"이동 성공 좌표: x=3.86, y=0.83 (충전대 (3.86, 0.83))"
"metadata":{
"type":"navigated_coordinate"
"kind":"place"
"x":3.86
"y":0.83
"source":"navigate_to"
}
"timestamp":"2026-08-07T11:27:33.736597"
}




   
   
   
   
