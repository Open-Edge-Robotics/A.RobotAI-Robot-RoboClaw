# Howto use API to access to Robot


REST API 실사용 명령어
A. 위치·방향·배터리·상태 (필요한 값만)
```
  curl -s -m 20 -X POST http://10.159.172.69:8080/skill \
    -H 'Content-Type: application/json' \
    -d '{"skill_name":"get_status","params":{"include_image":false}}' 
```
B. 현재 장소명 (기억된 위치와 비교)
```
  curl -s -m 20 -X POST http://10.159.172.69:8080/skill \
    -H 'Content-Type: application/json' \
    -d '{"skill_name":"identify_location","params":{}}' 
```
C. 에이전트 헬스 (81개 스킬 설명 제거)
```
  curl -s -m 10 http://10.159.172.69:8080/status | python3 -c 'import json,sys d=json.load(sys.stdin)["state"]; m=d["memory"]; v=m["rag"]["vector_store"] \ print(json.dumps({"agent_state":d["state"],"skills":d["skill_runtime"]["registered_count"], \
    "task_queue":d["task_queue"],"semantic_objects":m["semantic_object_count"], \
    "rag":{"enabled":m["rag"]["enabled"],"backend":v["backend"],"healthy":v["healthy"], \
           "count":v["count"],"threshold":m["rag"]["score_threshold"], \
           "last_error":v["last_error"]}}, ensure_ascii=False,indent=2))' \
```
```
curl -s -m 180 -X POST http://10.159.172.69:8080/skill \
    -H 'Content-Type: application/json' \
    -d '{"skill_name":"navigate_to","params":{"target_name":"거실"}}'
```
```
curl -s -m 180 -X POST http://10.159.172.69:8080/skill \
  -H 'Content-Type: application/json' \
  -d '{"skill_name":"navigate_to","params":{"x":0.66,"y":0.73,"frame_id":"map"}}'
```


*자연어, LLM 경유 — ⚠️  로봇이 움직입니다
```
  curl -s -m 180 -X POST http://10.159.172.69:8080/task \
    -H 'Content-Type: application/json' \
    -d '{"instruction":"거실로 이동해"}'
```