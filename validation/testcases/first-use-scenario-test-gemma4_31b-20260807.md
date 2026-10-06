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
   Robot : 현재 로봇 상태 조회 완료 (배터리: unknown, 위치: {'x': 4.04, 'y': 0.75, 'heading_deg': 168.6, 'frame': 'map'})

2. Success
   User : 그 위치를 충전대로 기억해
   Robot : 지식 1건을 저장했습니다.

*Qdrant Collection 저장 내용:

{
"text":"충전대의 위치 좌표는 x: 4.04, y: 0.75 (frame: map)입니다."
"metadata":{
"location_name":"충전대"
"coordinates":{}
"frame":"map"
}
"timestamp":"2026-08-07T09:25:28.028132"
}


3. Success
   User : 충전대 위치를 알려줘
   Robot : 지식 1건을 검색했습니다. 상위 결과: 충전대의 위치 좌표는 x: 4.04, y: 0.75 (frame: map)입니다.
   
   
3-1. Success
   User : 현재 있는 위치가 충전대 인데, 방향도 함께 저장해
   Robot : 지식 1건을 저장했습니다.

*Qdrant Collection 저장 내용:

{
"text":"충전대의 위치는 x: 4.04, y: 0.75 (frame: map)이며, 방향(heading)은 168.6도입니다."
"metadata":{
"location":"충전대"
"type":"position_info"
}
"timestamp":"2026-08-07T09:28:14.959316"
   
   
4. Success
   User : x=0.45, y=0.70 으로 이동해
   Robot : (0.45, 0.70)지점으로 이동을 시작합니다.
           (0.45, 0.70)지점에 무사히 도착했습니다. 
           (0.45, 0.70)지점으로 이동 완료
*Qdrant Collection 저장 내용:
{
"text":"[스킬 navigate_to 성공] 상황: 'x=0.45, y=0.70 으로 이동해'. 파라미터: {"x": 0.45, "y": 0.7}."
"metadata":{
"type":"skill_episode"
"skill_name":"navigate_to"
"success":true
"duration_sec":12.6
"error":""
"params_brief":"{"x": 0.45, "y": 0.7}"
"instruction":"x=0.45, y=0.70 으로 이동해"
"replanned":false
"recovered":false
"value_reason":"sparse"
}
"timestamp":"2026-08-07T09:30:48.066061"
}


{
"text":"이동 성공 좌표: x=0.45, y=0.70 ((0.45, 0.70))"
"metadata":{
"type":"navigated_coordinate"
"kind":"place"
"x":0.45
"y":0.7
"source":"navigate_to"
}
"timestamp":"2026-08-07T09:30:47.597809"



5. Success
   User : 현재 위치를 알려줘
   Robot : 현재 위치(x: 0.65, y: 0.70) 반경 2.0m 안에 기억된 장소가 없습니다.

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
"timestamp":"2026-08-07T09:31:20.626933"
}

6. Success
   User : 그 위치를 거실로 저장해
   robot : 지식 1건을 저장했습니다.

*Qdrant Collection 저장 내용:
{
"text":"거실의 위치 좌표는 x: 0.65, y: 0.70 (frame: map)입니다."
"metadata":{
"location_name":"거실"
"coordinates":{}
}
"timestamp":"2026-08-07T09:32:04.326475"



7. Success
   User : 거실의 위치를 알려줘
   robot : 지식 1건을 검색했습니다. 상위 결과: 거실의 위치 좌표는 x: 0.65, y: 0.70 (frame: map)입니다.
   
   
8. Success
   User : 내가 조이스틱으로 이동할께
   Robot : 네, 알겠습니다! 조이스틱으로 직접 제어해 주세요. 저는 여기서 대기하며 필요하신 일이 생기면 언제든 말씀해 주세요. [Former]가 대기하고 있겠습니다.
   
9. Success
   User : 현재 위치를 알려줘.
   Robot : 현재 위치(x: -5.21, y: -1.15) 반경 2.0m 안에 기억된 장소가 없습니다.

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
"timestamp":"2026-08-07T09:40:18.236810"
}


10. Success
   User : 그 위치를 키친으로 기억해
   Robot : 지식 1건을 저장했습니다.

*Qdrant Collection 저장 내용:
{
"text":"키친의 위치 좌표는 x: -5.21, y: -1.15 (frame: map)입니다."
"metadata":{
"location":"kitchen"
"x":-5.21
"y":-1.15
"frame":"map"
}
"timestamp":"2026-08-07T09:41:10.966486"
}


10-1. Success
   User : 그 위치가 키친인데, 방향도 함께 기억해
   Robot : 지식 1건을 저장했습니다.

*Qdrant Collection 저장 내용:
{
"text":"키친의 위치는 x: -5.21, y: -1.15이며, 바라보고 있는 방향(heading)은 약 90.4도입니다. (frame: map)"
"metadata":{
"location":"kitchen"
"coordinates":{}
"heading":90.4
}
"timestamp":"2026-08-07T09:43:11.582609"
}


11. Success
   User : 키친 위치를 알려줘
   Robot : 지식 2건을 검색했습니다. 상위 결과: 키친의 위치는 x: -5.21, y: -1.15이며, 바라보고 있는 방향(heading)은 약 90.4도입니다. (frame: map)



12. Success
   User : 충전대 위치를 알려줘
   Robot : 지식 2건을 검색했습니다. 상위 결과: 충전대의 위치 좌표는 x: 4.04, y: 0.75 (frame: map)입니다.

13. Success
   User : 충전대 위치 x, y 좌표 알려줘
   Robot : 충전대의 위치 좌표는 x: 4.04, y: 0.75 (frame: map)입니다.

14. Success
   User : 거실로 이동해
   Robot : 거실 (0.65, 0.70)지점으로 이동을 시작합니다.
                     거실 (0.65, 0.70)지점에 무사히 도착했습니다.
                     거실 (0.65, 0.70)지점으로 이동 완료

*Qdrant Collection 저장 내용:
{
"text":"이동 성공 좌표: x=0.65, y=0.70 (거실 (0.65, 0.70))"
"metadata":{
"type":"navigated_coordinate"
"kind":"place"
"x":0.65
"y":0.7
"source":"navigate_to"
}
"timestamp":"2026-08-07T09:48:06.462646"
}

{
"text":"[스킬 navigate_to 성공] 상황: '거실로 이동해'. 파라미터: {"target_name": "거실"}."
"metadata":{
"type":"skill_episode"
"skill_name":"navigate_to"
"success":true
"duration_sec":22.636
"error":""
"params_brief":"{"target_name": "거실"}"
"instruction":"거실로 이동해"
"replanned":false
"recovered":false
"value_reason":"sparse"
}
"timestamp":"2026-08-07T09:48:06.748202"
}


15. Success
   User : 충전대로 이동해
   Robot : 충전대 (4.04, 0.75)지점으로 이동을 시작합니다.
                    충전대 (4.04, 0.75)지점에 무사히 도착했습니다.
                    충전대 (4.04, 0.75)지점으로 이동 완료
 
*Qdrant Collection 저장 내용: 
{
"text":"이동 성공 좌표: x=4.04, y=0.75 (충전대 (4.04, 0.75))"
"metadata":{
"type":"navigated_coordinate"
"kind":"place"
"x":4.04
"y":0.75
"source":"navigate_to"
}
"timestamp":"2026-08-07T09:49:32.933477"
}     

*충전대로 이동 [스킬 navigate_to 성공] 관련 데이터는 Qdrant Collection 에 없음

                    
16. Success
   User : x: -5.26, y: -1.26 로 이동해
   Robot :  (-5.26, -1.26)지점으로 이동을 시작합니다.
            (-5.26, -1.26)지점에 무사히 도착했습니다.
            (-5.26, -1.26)지점으로 이동 완료
            
17. Fail
   User : 현재 있는 위치의 이름이 뭐야?
   Robot : 현재 위치(x: -5.06, y: -1.17) 반경 2.0m 안에 기억된 장소가 없습니다.
   
17-1. Success
   User : 키친 위치 알려줘
   Robot : 지식 2건을 검색했습니다. 상위 결과: 키친의 위치는 x: -5.21, y: -1.15이며, 바라보고 있는 방향(heading)은 약 90.4도입니다. (frame: map)
   
17-2. Success
   User : 현재 위치와 키친의 위치 사이 거리는 얼마야?
   Robot : 현재 제 위치는 (-5.06, -1.17)이고, 키친의 위치는 (-5.21, -1.15)입니다. 두 지점 사이의 직선 거리는 약 0.15m로, 매우 가까운 거리에 있습니다.
   
17-3. Fail
   User : 그럼 현재 위치가 키친 이라고 해도 되는 거 아닌가?
   Robot : 현재 위치(x: -5.06, y: -1.17) 반경 2.0m 안에 기억된 장소가 없습니다.

*Qdrant Collection 저장 내용:
{
"text":"[스킬 identify_location 성공] 상황: '그럼 현재 위치가 키친 이라고 해도 되는 거 아닌가?'."
"metadata":{
"type":"skill_episode"
"skill_name":"identify_location"
"success":true
"duration_sec":0
"error":""
"params_brief":""
"instruction":"그럼 현재 위치가 키친 이라고 해도 되는 거 아닌가?"
"replanned":false
"recovered":false
"value_reason":"sparse"
}
"timestamp":"2026-08-07T10:02:31.865558"


18. Fail
   User : 현재 위치 x, y 좌표와 비교해서 현재 위치의 이름을 파악해봐
   Robot : 현재 위치(x: -5.06, y: -1.17) 반경 2.0m 안에 기억된 장소가 없습니다.
 
*Qdrant Collection 저장 내용: 
{
"text":"[스킬 identify_location 성공] 상황: '현재 위치 x, y 좌표와 비교해서 현재 위치의 이름을 파악해봐'."
"metadata":{
"type":"skill_episode"
"skill_name":"identify_location"
"success":true
"duration_sec":0.001
"error":""
"params_brief":""
"instruction":"현재 위치 x, y 좌표와 비교해서 현재 위치의 이름을 파악해봐"
"replanned":false
"recovered":false
"value_reason":"fast"
}
"timestamp":"2026-08-07T10:03:16.095004"   
   
19. Success or Fail
   User : 키친으로 이동해
   Robot : 키친  (-5.21, -1.15)지점으로 이동을 시작합니다.
                  키친  (-5.21, -1.15)지점에 무사히 도착했습니다.
                   키친  (-5.21, -1.15)지점으로 이동 완료 
   **전혀 움직임은 없었음**

20. Success
   User : go to living room
   Robot : 거실 (0.65, 0.70)지점으로 이동을 시작합니다.
                    거실 (0.65, 0.70)지점에 무사히 도착했습니다.
                    거실 (0.65, 0.70)지점으로 이동 완료

21. Success
   User : 거실로 이동해
   Robot : 거실  (0.66, 0.73)지점으로 이동을 시작합니다.
                 거실  (0.66, 0.73)지점에 무사히 도착했습니다.
                거실  (0.66, 0.73)지점으로 이동 완료
                
                
22. Success
   User : 충전대로 이동해
   Robot : 충전대 (4.04, 0.75)지점으로 이동을 시작합니다.
                    충전대 (4.04, 0.75)지점에 무사히 도착했습니다.
                    충전대 (4.04, 0.75)지점으로 이동 완료

*Qdrant Collection 저장 내용: 
{
"text":"[스킬 navigate_to 성공] 상황: '충전대로 이동해'. 파라미터: {"target_name": "충전대"}."
"metadata":{
"type":"skill_episode"
"skill_name":"navigate_to"
"success":true
"duration_sec":35.308
"error":""
"params_brief":"{"target_name": "충전대"}"
"instruction":"충전대로 이동해"
"replanned":false
"recovered":false
"value_reason":"sampled"
}
"timestamp":"2026-08-07T10:08:00.013830"

23. Success
   User : 반바퀴 회전해
   Robot : 로봇을 180.0도 회전시킵니다.
                    회전을 완료했습니다 (180.0도)
          180.0도 회전 완료
          
*Qdrant Collection 저장 내용: 
{
"text":"[스킬 rotate 성공] 상황: '반바퀴 회전해'. 파라미터: {"angle_deg": 180.0}."
"metadata":{
"type":"skill_episode"
"skill_name":"rotate"
"success":true
"duration_sec":5.308
"error":""
"params_brief":"{"angle_deg": 180.0}"
"instruction":"반바퀴 회전해"
"replanned":false
"recovered":false
"value_reason":"sparse"
}
"timestamp":"2026-08-07T10:13:28.082538"
}


24. Success
   User : 충전대 위치를 알려줘
   Robot : 지식 3건을 검색했습니다. 상위 결과: 이동 성공 좌표: x=4.04, y=0.75 (충전대 (4.04, 0.75))
          
          
25. Partially Success (거실 위치가 없음)
   User : 키친과 거실 위치 알려줘
   Robot : 지식 3건을 검색했습니다. 상위 결과: 이동 성공 좌표: x=-5.21, y=-1.15 (키친 (-5.21, -1.15))  


26. Partially Success (도착했으나, 스스로 실패라고 함)
   User : go to Kitchen
   Robot : (-5.21, -1.15)지점으로 이동을 시작합니다.
        (-5.21, -1.15)지점에 무사히 도착했습니다.
                 실패: (-5.21, -1.15)지점으로 이동 완료

27. Success
   User : go to charging station
   Robot : 충전대 (4.04, 0.75)지점으로 이동을 시작합니다.
                    충전대 (4.04, 0.75)지점에 무사히 도착했습니다.
                    충전대 (4.04, 0.75)지점으로 이동 완료

**추가된 *Qdrant Collection 저장 내용**
{
"text":"[스킬 교훈: navigate_to] { "lessons": [ "특정 장소 이름이 주어질 때는 target_name 파라미터를 사용하라", "좌표 값이 주어질 때는 x, y 파라미터를 사용하라" ] }"
"metadata":{
"type":"skill_lesson"
"skill_name":"navigate_to"
"lesson":"{ "lessons": [ "특정 장소 이름이 주어질 때는 target_name 파라미터를 사용하라", "좌표 값이 주어질 때는 x, y 파라미터를 사용하라" ] }"
"evidence_count":6
"success_rate":1
"updated_at":"2026-08-07T10:13:45.367564"
}
"timestamp":"2026-08-07T10:13:46.707820"


```
   
   
   
   
   


   
   
   
   
