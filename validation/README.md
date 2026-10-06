# RoboClaw 자동화 테스트 가이드 (QA용)

이 폴더는 RoboClaw 에이전트의 **자동화 검증**을 위한 시나리오·스크립트·리포트를 모아둔 곳입니다.
품질팀이 로봇 유무와 무관하게 반복 실행할 수 있도록 두 가지 경로를 제공합니다.

| 경로                          | 로봇 필요? | 소요  | 용도                                                                    |
| :---------------------------- | :--------: | :---- | :---------------------------------------------------------------------- |
| **A. 코드 회귀 테스트**       | ❌ 불필요  | ~1초  | 코드/이미지에 RAG 위치 지식 개선(P1~P5)이 들어있는지 결정론적 검증      |
| **B. 실로봇 시나리오 테스트** |  ✅ 필요   | ~수분 | 실제 로봇에서 First-Use 시나리오(이동·기억·조회·역방향조회)를 자동 판정 |

> **실행 위치 선택** — B의 실로봇 테스트는 두 가지 방법이 있습니다.
>
> - **로봇/연동 환경**: Go `robo_claw_cli`(아래 B절). 설정서버 정합성까지 포함.
> - **외부 호스트/CI (권장, 확장성)**: 파이썬 독립 클라이언트 [`talk/robo_talk.py`](talk/README.md) — `<로봇IP>:50052`에 직접 접속. Go/설정서버 불필요, 대화형 지원. 두 방법은 **같은 시나리오 JSON**을 공유합니다.

> 배경(무엇을 왜 고쳤는지): [`CHANGE_LOG.md`](../CHANGE_LOG.md) 부록 "RAG 위치 지식 개선"

---

## A. 코드 회귀 테스트 (로봇 없이)

임베딩 서버·Qdrant·GPU·ROS 없이 순수 파이썬으로 P1~P5 수정이 코드에 살아있는지 검증합니다.
CI나 개발 PC에서 그대로 돌아갑니다.

```bash
cd <robo-claw>
./validation/rag_regression/run_rag_tests.sh
# 특정 항목만:  ./validation/rag_regression/run_rag_tests.sh -k p4
```

- 9개 테스트가 모두 PASS면 코드 레벨 개선이 정상입니다.
- 상세: [`validation/rag_regression/README.md`](rag_regression/README.md)

---

## B. 실로봇 시나리오 테스트

`robo_claw_cli`(Go)로 시나리오 JSON을 로봇 에이전트에 보내고 응답을 자동 판정합니다.
판정 핵심은 각 스텝의 **`fail_keywords`(예: `"[스킬"`)** 로, 내부 로그 누수/오답을 즉시 잡습니다.

- 시나리오 파일: [`validation/testcases/first-use-scenario-auto.json`](testcases/first-use-scenario-auto.json)
- 시나리오 상세/케이스 매핑: [`validation/testcases/README-auto-scenario.md`](testcases/README-auto-scenario.md)

### 토폴로지 (한 번 이해하면 쉬움)

```
[품질팀 PC] --SSH--> [로봇 former@10.159.172.69]
                         └ robo_claw_container (에이전트, 도커, ARM64)
                             ├ 50051  RosGrpc      (텔레메트리·ping)
                             └ 50052  RoboMessenger(자연어 명령 = 시나리오가 쓰는 포트) ★
[설정서버 10.159.172.74:30180] : LLM/임베딩 주소·환경변수 제공(config)
```

### 사전 준비 (담당: 로봇 운용자)

- 로봇 전원 ON, 네비게이션 스택 기동, 맵 `w2_2f_2` 로드, **초기 위치(AMCL) 셋업**.
- 시나리오 좌표 3곳(충전대 `3.96,0.83` / 거실 `0.66,0.73` / 키친 `-5.36,-1.25`)이 실제 도달 가능한지 확인. 맵이 다르면 시나리오 JSON의 좌표를 수정.

### 1) 에이전트 이미지 빌드·배포 (개선 반영이 안 돼 있을 때만)

`SKILLS.*.md`·에이전트 코드는 **이미지에 구워지므로**, 수정본을 반영하려면 재빌드·재배포가 필요합니다.
(`:latest`는 캐시 함정이 있으니 **불변 태그** 권장.)

```bash
# (개발/빌드 서버, AMD) arm64 크로스빌드 + 레지스트리 push
docker run --privileged --rm tonistiigi/binfmt --install arm64   # 최초 1회
docker buildx create --use 2>/dev/null || true                    # 최초 1회
docker login
cd <robo-claw>
TAG=20260804-p1p5
docker buildx build --platform linux/arm64 \
  --build-arg BUILD_DATE="$(date +'%Y-%m-%d %H:%M:%S %Z')" \
  --build-arg GIT_COMMIT="$(git rev-parse --short HEAD)" \
  --build-arg IMAGE_TAG="$TAG" \
  -t lgecloudroboticstask/robo-claw:$TAG --push .
```

> `--build-arg`로 넣은 빌드 날짜·git 커밋·태그는 컨테이너 시작 로그 최상단에 배너로 출력됩니다. `run_robo_claw_docker.sh`로 빌드하면 자동 주입됩니다.

### 실행 이미지 버전 확인 (배포 검증)

컨테이너 시작 로그(`docker logs robo_claw_container`) 최상단 배너, 또는 즉시 조회:

```bash
ssh former@10.159.172.69 'docker exec robo_claw_container cat /etc/robo_claw_version'
# build_date=... / git_commit=... / image_tag=...
```

→ "지금 로봇에서 도는 이미지가 방금 빌드한 그것인지"를 확실히 판별할 수 있습니다.

### 2) 에이전트 기동 (터미널 1 — 로봇에 SSH)

`--pull`로 새 이미지를 받고, `--use-grpc`로 gRPC 서버까지 띄웁니다.

```bash
ssh former@10.159.172.69
docker stop robo_claw_container 2>/dev/null; docker rm robo_claw_container 2>/dev/null
robo_claw_cli launch former w2_2f_2 --docker \
  --image-tag 20260804-p1p5 --pull --use-grpc
# 컨테이너 로그가 포그라운드로 계속 출력됨 → 이 창은 그대로 둠
```

> 개선이 이미 반영된 이미지가 로봇에 있으면 1)·`--pull` 없이 `--image-tag`만으로도 됩니다.

### 3) 시나리오 실행 (터미널 2 — 로봇에 SSH)

**반드시 `--port 50052`** (자연어 명령 = RoboMessenger 포트).

**빠른 실행 (권장)** — 래퍼 스크립트가 포트/플래그/리포트 저장·요약을 처리합니다:

```bash
ssh former@10.159.172.69
cd <robo-claw>/validation
CLI=<robo_claw_cli 경로> ./run_scenario_test.sh
# 원격 PC에서 로봇 IP로:  HOST=10.159.172.69 CLI=./robo_claw_cli ./run_scenario_test.sh
```

**직접 실행** (동등):

```bash
./robo_claw_cli test -r former -e w2_2f_2 \
  --host localhost --port 50052 \
  --scenario-file <로봇에 복사한 경로>/first-use-scenario-auto.json \
  --output ./test-report.md
```

- 콘솔에 스텝별 PASS/FAIL이 뜨고, 리포트가 마크다운으로 저장됩니다(스크립트는 타임스탬프 파일명 + PASS/FAIL 요약 출력).

### 4) 결과 판독

| 스텝                            | 기대     | 의미                                            |
| :------------------------------ | :------- | :---------------------------------------------- |
| tc00 (ping)                     | (무시)   | 50052엔 RosGrpc 없음 → FAIL 정상. 무시          |
| tc03/04/07/10/11/12 (위치 질의) | **PASS** | 이름+좌표 응답, `[스킬` 누수 없음 → 개선 반영됨 |
| tc13/15 (이름 기반 이동)        | **PASS** | 실제 주행 완료                                  |
| tc16/17 (현재 위치 이름)        | **PASS** | `현재 위치는 '키친'입니다` (좌표→이름)          |
| tc18 (교차언어)                 | SKIP     | 알려진 한계(영어 별칭 미지원)                   |

FAIL 스텝의 "실패 사유"에 어떤 `fail_keywords`가 걸렸는지(예: `금지 키워드 포함: '[스킬'`) 표시됩니다.

### 트러블슈팅 (실제로 자주 겪는 것)

| 증상                                               | 원인                                      | 조치                                                                        |
| :------------------------------------------------- | :---------------------------------------- | :-------------------------------------------------------------------------- |
| `gRPC 서버 'localhost:50051'에 연결할 수 없습니다` | 포트/서버 문제                            | 시나리오는 **`--port 50052`** 사용. gRPC 노드가 없으면 기동 시 `--use-grpc` |
| 모든 custom 스텝 `Unimplemented: Method not found` | 50051(RosGrpc)로 붙음                     | **`--port 50052`** 로 재실행                                                |
| 응답에 `[스킬 … 성공] 상황:` 노출                  | 로봇 이미지가 **옛 버전**                 | 1)→2)로 새 이미지 빌드·`--pull` 재기동                                      |
| 재기동해도 그대로                                  | `:latest` 캐시로 옛 이미지 재사용         | **불변 태그** 사용 + `--pull`                                               |
| tc16/17에서 "사무실/추정"                          | identify_location 미탑재 or 가이드 미반영 | 새 이미지 반영 확인                                                         |

#### 로봇 컨테이너에 개선이 반영됐는지 즉시 확인 (선택)

```bash
ssh former@10.159.172.69 'docker exec robo_claw_container bash -c \
  "echo P1:; grep -rl _NON_LEARNABLE_SKILLS /ros2_ws/install 2>/dev/null | head -1; \
   echo P4:; grep -rl IdentifyLocationSkill /ros2_ws/install 2>/dev/null | head -1"'
```

→ P1/P4 아래에 경로가 뜨면 반영됨, 비어 있으면 옛 이미지.

### (선택) 레거시 Qdrant 데이터 정리

이전 실행에서 쌓인 옛 `skill_episode`는 새 코드가 검색에서 자동 제외하므로 재테스트에 영향은 없지만,
완전히 깨끗이 하려면 기동 후 에이전트에 `rag_reindex` 실행.

---

## 참고: 포트 요약

|   포트    | 서비스                            | 용도                         | 시나리오 테스트에서                  |
| :-------: | :-------------------------------- | :--------------------------- | :----------------------------------- |
|   50051   | robo_claw_grpc `RosGrpc`          | 텔레메트리·ping·카메라       | ping(tc00)만. `--use-grpc`로 기동    |
| **50052** | robo_claw_channel `RoboMessenger` | **자연어 명령(SendCommand)** | **custom 스텝 전부.** `--port 50052` |
