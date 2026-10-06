# First-Use 시나리오 자동 검증 실행 가이드

`first-use-scenario-auto.json` 을 `robo_claw_cli`(= robo_claw_talker 역할)로 로봇에
직접 송신해 자동 판정한다. 방식 A(navigate_to 좌표 이동 후 현재 위치 저장)라 조이스틱
개입 없이 완전 자동 실행된다.

핵심 판정: 각 조회/이동 스텝의 `fail_keywords: ["[스킬", ...]` — 응답에 내부 스킬 로그가
새어나오면 즉시 FAIL. 이번 개선(P1~P5)이 실제로 적용됐는지 이 한 줄로 판별된다.

---

## 0단계 (중요) — 로봇에 개선 코드가 실제로 올라갔는지 먼저 확인

지난 테스트에서 개선이 안 보인 가장 유력한 원인은 **로봇에서 도는 코드가 옛 버전**이라는
점이다. 로봇 소스는 `former@10.159.172.69:~/workspace/robo-claw` 에 있다. 아래를 실행해
개선 마커가 있는지 확인한다. (0이 나오면 옛 코드 → 먼저 동기화·재배포 필요)

```bash
ssh former@10.159.172.69 'cd ~/workspace/robo-claw && \
  echo "[P1] NON_LEARNABLE:" $(grep -rc "_NON_LEARNABLE_SKILLS" src/robo_claw_agent/robo_claw_agent/memory_manager/skill_learning.py) && \
  echo "[P1] type_exclude :" $(grep -rc "type_exclude" src/robo_claw_agent/robo_claw_agent/vector_store/qdrant.py) && \
  echo "[P4] identify    :" $(grep -rc "IdentifyLocationSkill" src/robo_claw_agent/robo_claw_agent/skills/system_skill/rag.py)'
```

> 로봇이 **소스가 아니라 도커 이미지/컨테이너**로 구동된다면, 위 소스 확인 대신 실제
> 실행 중인 컨테이너 내부를 확인해야 한다:
> ```bash
> ssh former@10.159.172.69 'docker ps --format "{{.Names}}\t{{.Image}}"'
> # 해당 컨테이너에서:
> ssh former@10.159.172.69 'docker exec <컨테이너명> \
>   grep -rc _NON_LEARNABLE_SKILLS /ros2_ws/install/robo_claw_agent/local/lib/python3.10/dist-packages/robo_claw_agent/memory_manager/skill_learning.py'
> ```
> 0이면 옛 이미지가 실행 중 → 새 이미지 pull/재배포(불변 태그 권장) 후 재기동.

**코드/이미지 갱신 후 반드시 에이전트(노드/컨테이너)를 재기동**해야 반영된다.

### (권장) 레거시 Qdrant 오염 데이터 정리
갱신 후에도 이전에 쌓인 `type=skill_episode` 항목이 컬렉션에 남아 있다. 새 코드는 검색에서
제외하지만, 깨끗이 하려면 로봇에서 `rag_reindex` 스킬 실행 또는 해당 항목 삭제.

---

## 1단계 — CLI 빌드

```bash
cd ~/workspace-roboclaw/robo-claw/robo_claw_cli   # (빌드 머신 기준)
go build -o robo_claw_cli .        # 또는: task build
```

`config.yaml` 의 server 가 `10.159.172.74:30180` 인지 확인(다르면 아래 --host/--port 사용).

## 2단계 — 로봇 준비 (회원님)

- 로봇 전원/네비게이션 스택 기동, 맵 `w2_2f_2` 로드, **초기 위치(AMCL) 셋업**.
- `first-use-scenario-auto.json` 의 좌표 3곳(충전대 3.96/0.83, 거실 0.66/0.73,
  키친 -5.36/-1.25)이 **실제 도달 가능한지 확인**하고, 아니면 맵에 맞게 수정.

## 3단계 — 시나리오 실행

```bash
./robo_claw_cli test -r former -e w2_2f_2 \
  --scenario-file /path/to/first-use-scenario-auto.json \
  --output ./report_first_use.md
# 서버가 config.yaml과 다르면:  --host 10.159.172.74 --port 30180
```

- 각 스텝 PASS/FAIL 가 콘솔에 출력되고, `--output` 으로 Markdown 보고서가 저장된다.
- FAIL 스텝의 "실패 사유"에 어떤 `fail_keywords` 가 걸렸는지(예: `금지 키워드 포함: '[스킬'`)
  표시된다.

---

## 판정 해석

| 결과 | 의미 |
| :--- | :--- |
| 조회/이동 스텝 **PASS** | 좌표 사실이 깔끔히 반환됨 → 개선 반영됨 |
| `금지 키워드 '[스킬'` 로 **FAIL** | 여전히 내부 스킬 로그 누수 → **로봇 코드 미갱신**(0단계 재확인) |
| tc16/tc17 **FAIL**(사무실/추정) | 역방향 조회 미동작 → identify_location 미탑재 or 가이드 미반영 |
| tc18 | 교차언어(영어) — 알려진 한계라 기본 SKIP(enabled:false) |

## 주의 / 한계

- `custom` 러너는 중간 일시정지가 없어 방식 A로 좌표 이동을 넣었다. 저장 좌표는 로봇
  로컬라이제이션에 따라 조금씩 달라지므로, 판정은 **정확 좌표값이 아니라 "이름 포함 +
  '[스킬' 미포함" 패턴**으로 한다.
- 이름 기반 이동(tc13~15)은 먼 지점 간 이동이라 `timeout_ms` 를 넉넉히(240s) 잡았다.
  주행이 더 오래 걸리면 늘릴 것.
- 역방향 조회(tc16/17)는 직전 스텝(tc15)에서 로봇이 **키친에 도착해 있어야** 정답이
  "키친"이 된다. 순서를 바꾸면 기대 키워드도 함께 조정할 것.
