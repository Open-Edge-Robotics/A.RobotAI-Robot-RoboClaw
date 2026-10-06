#!/bin/bash
#
# rc_rest.sh — RoboClaw REST API 간편 호출 (자연어 아닌 구조화 제어)
#
# 왜 필요한가: curl + python 필터를 한 줄로 붙여넣다 보면 들여쓰기·따옴표가 깨진다.
# 자주 쓰는 호출을 서브커맨드로 묶어 둔다.
#
# 대상 엔드포인트 (channel_node HTTP, 기본 8080):
#   POST /skill  스킬 직접 실행 (LLM 미경유, 결정론적)
#   POST /task   자연어 명령 (LLM 경유)
#   GET  /status 에이전트 상태     GET /health 헬스체크
#
# 사용:
#   ./rc_rest.sh health                     # 연결·보안설정 확인
#   ./rc_rest.sh pose                       # x, y, heading, 배터리, 상태
#   ./rc_rest.sh where                      # 현재 위치의 장소명
#   ./rc_rest.sh agent                      # 에이전트 상태(스킬 설명 제외)
#   ./rc_rest.sh goto 거실                  # 이름 기반 이동   ⚠️ 로봇이 움직임
#   ./rc_rest.sh goto 0.66 0.73             # 좌표 이동        ⚠️ 로봇이 움직임
#   ./rc_rest.sh stop                       # 이동 중단
#   ./rc_rest.sh task "거실로 이동해"        # 자연어(LLM)     ⚠️ 로봇이 움직임
#   ./rc_rest.sh skill rag_list '{}'        # 임의 스킬 호출
#   ./rc_rest.sh raw /status                # 가공 없는 원본 JSON
#
# 환경변수: RC_HOST(기본 10.159.172.69) RC_PORT(8080) RC_TOKEN(인증 켜진 경우)
#
set -uo pipefail

HOST="${RC_HOST:-10.159.172.69}"
PORT="${RC_PORT:-8080}"
BASE="http://${HOST}:${PORT}"
TOKEN="${RC_TOKEN:-}"

_auth=()
[[ -n "$TOKEN" ]] && _auth=(-H "Authorization: Bearer ${TOKEN}")

# $1=경로  $2=JSON 본문(있으면 POST)  $3=타임아웃초(기본 20)
_call() {
  local path="$1" body="${2:-}" tmo="${3:-20}"
  if [[ -n "$body" ]]; then
    curl -s -m "$tmo" -X POST "${BASE}${path}" \
      -H 'Content-Type: application/json' "${_auth[@]}" -d "$body"
  else
    curl -s -m "$tmo" "${BASE}${path}" "${_auth[@]}"
  fi
}

# 스킬 응답에서 result_json 을 꺼내 필터 파이썬에 넘긴다.
# result_json 은 channel_node.call_skill 수정이 반영된 로봇에서만 채워진다.
# 미반영 로봇에서는 message 문자열만 오므로 항상 message 도 함께 출력한다.
_skill() { _call /skill "{\"skill_name\":\"$1\",\"params\":$2}" "${3:-20}"; }

case "${1:-}" in
  health)
    _call /health | python3 -c 'import json,sys
d=json.load(sys.stdin); s=d.get("http_security",{})
print(json.dumps({"status":d.get("status"),"clients":d.get("clients"),
  "auth_enabled":s.get("auth_enabled"),"allowed_cidrs":s.get("allowed_cidrs"),
  "blocked_skills":s.get("blocked_skills")}, ensure_ascii=False, indent=2))'
    ;;

  pose)
    _skill get_status '{"include_image":false}' | python3 -c 'import json,sys
d=json.load(sys.stdin)
s=json.loads(d.get("result_json") or "{}").get("status_details",{})
print(json.dumps({"ok":d.get("success"),"status":s.get("status"),"pose":s.get("pose"),
  "battery":s.get("battery"),"time":s.get("timestamp"),"message":d.get("message")},
  ensure_ascii=False, indent=2))'
    ;;

  where)
    _skill identify_location '{}' | python3 -c 'import json,sys
d=json.load(sys.stdin); r=json.loads(d.get("result_json") or "{}")
print(json.dumps({"ok":d.get("success"),"location":r.get("location_name"),
  "distance_m":r.get("distance_m"),"position":r.get("position"),
  "message":d.get("message")}, ensure_ascii=False, indent=2))'
    ;;

  agent)
    # /status 는 81개 스킬 설명을 전부 담아 매우 길다 → 필요한 값만 추린다.
    _call /status | python3 -c 'import json,sys
d=json.load(sys.stdin).get("state",{}); m=d.get("memory",{})
rag=m.get("rag",{}); v=rag.get("vector_store",{})
print(json.dumps({"agent_state":d.get("state"),
  "skills":d.get("skill_runtime",{}).get("registered_count"),
  "task_queue":d.get("task_queue"),
  "semantic_objects":m.get("semantic_object_count"),
  "rag":{"enabled":rag.get("enabled"),"threshold":rag.get("score_threshold"),
         "backend":v.get("backend"),"healthy":v.get("healthy"),
         "collection":v.get("collection_name"),"exists":v.get("collection_exists"),
         "count":v.get("count"),"dim":v.get("vector_dimension"),
         "last_error":v.get("last_error")}}, ensure_ascii=False, indent=2))'
    ;;

  goto)
    if [[ $# -eq 2 ]]; then
      params="{\"target_name\":\"$2\"}"
    elif [[ $# -eq 3 ]]; then
      params="{\"x\":$2,\"y\":$3,\"frame_id\":\"map\"}"
    else
      echo "사용: $0 goto <장소명> | $0 goto <x> <y>" >&2; exit 1
    fi
    echo "[goto] $params  (이동은 수십 초 걸립니다)"
    _skill navigate_to "$params" 300 | python3 -m json.tool
    ;;

  stop)    _skill stop '{}' | python3 -m json.tool ;;

  task)
    [[ $# -ge 2 ]] || { echo "사용: $0 task \"<자연어 명령>\"" >&2; exit 1; }
    body=$(python3 -c 'import json,sys; print(json.dumps({"instruction":sys.argv[1]}))' "$2")
    echo "[task] $2  (LLM 경유 — 수십 초 걸립니다)"
    _call /task "$body" 300 | python3 -m json.tool
    ;;

  skill)
    [[ $# -ge 2 ]] || { echo "사용: $0 skill <스킬명> '<params JSON>'" >&2; exit 1; }
    _skill "$2" "${3:-\{\}}" 300 | python3 -m json.tool
    ;;

  raw)
    [[ $# -ge 2 ]] || { echo "사용: $0 raw <경로>  예: $0 raw /status" >&2; exit 1; }
    _call "$2" | python3 -m json.tool
    ;;

  *)
    sed -n '2,30p' "$0" | sed -e 's/^# \{0,1\}//'
    exit 1
    ;;
esac
