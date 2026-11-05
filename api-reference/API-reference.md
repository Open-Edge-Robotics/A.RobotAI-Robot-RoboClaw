# HTTP API 호출 예시

이 문서는 HTTP 채널의 조회·스킬·자연어 호출 예시를 제공합니다. 인증·정책·오류 응답은
[HTTP API 레퍼런스](../docs/HTTP_API.md)를 기준으로 합니다.
프로젝트 `.env`에서 HTTP 토큰을 설정하고 서버를 실행한 뒤, 별도 호출 터미널에서
실제 주소와 토큰을 지정합니다.

```bash
export ROBOCLAW_HTTP_URL=http://127.0.0.1:8080
export RC_HTTP_READONLY_TOKEN='<설정한 읽기 토큰>'
export RC_HTTP_CONTROL_TOKEN='<설정한 제어 토큰>'
```

## 상태와 현재 장소 조회

```bash
curl -sS --max-time 20 "$ROBOCLAW_HTTP_URL/status" \
  -H "Authorization: Bearer $RC_HTTP_READONLY_TOKEN" \
  | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), ensure_ascii=False, indent=2))'

curl -sS --max-time 20 -X POST "$ROBOCLAW_HTTP_URL/skill" \
  -H "Authorization: Bearer $RC_HTTP_CONTROL_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"skill_name":"identify_location","params":{}}'
```

## 장소 좌표 조회

```bash
curl -sS --max-time 20 -X POST "$ROBOCLAW_HTTP_URL/skill" \
  -H "Authorization: Bearer $RC_HTTP_CONTROL_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"skill_name":"get_location","params":{"location_name":"거실"}}'
```

## 이동 요청

다음 요청은 실제 로봇 이동을 유발합니다. 등록 장소·측위·주행 안전 준비를 확인한 뒤 실행합니다.
직접 `/skill` 호출은 planner의 사전 준비 단계를 만들지 않으므로 안전 게이트가 거부하면
로봇별 자세 준비를 먼저 완료합니다. 클라이언트 curl timeout은 로봇 취소를 뜻하지 않습니다.

```bash
curl -sS --max-time 180 -X POST "$ROBOCLAW_HTTP_URL/skill" \
  -H "Authorization: Bearer $RC_HTTP_CONTROL_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"skill_name":"navigate_to","params":{"target_name":"거실"}}'

curl -sS --max-time 180 -X POST "$ROBOCLAW_HTTP_URL/task" \
  -H "Authorization: Bearer $RC_HTTP_CONTROL_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"instruction":"거실로 이동해"}'
```
