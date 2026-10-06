# robo_talk — 외부 호스트용 로봇 명령 클라이언트

로봇 내부 도구(Go `robo_claw_cli`)나 설정서버에 의존하지 않고, **외부 호스트에서 파이썬만으로**
로봇에 자연어 명령을 보내고 응답을 받는 독립 gRPC 클라이언트입니다. 테스트 케이스 파일도
외부 호스트에 두고 재사용합니다. (확장/CI 연동에 유리)

- 접속 대상: 로봇의 **RoboMessenger** gRPC = **`<로봇IP>:50052`** (자연어 명령 `SendCommand`).
  - ⚠️ `50051`은 텔레메트리(RosGrpc)라 명령용으로 쓰면 `Method not found`가 납니다.
- 두 가지 모드: **시나리오 자동 실행**(케이스 파일을 하나씩 전송·판정) / **대화형**(직접 입력).

## rag_score_probe.py — RAG threshold 데이터 기반 산출 (별도 도구)

`RAG Score Threshold` 적정값을 감이 아니라 **실제 점수 분포**로 정하는 도구입니다.
에이전트와 동일하게 질의를 임베딩(Ollama `/api/embeddings`, `prompt=text`)한 뒤 Qdrant를
**threshold 없이** 검색해 각 히트의 score를 보여줍니다. 표준 라이브러리만 쓰므로 설치 불필요.

```bash
python3 rag_score_probe.py \
  --ollama-url http://<ollama-host>:11434 \
  --embed-model qwen3-embedding:0.6b \
  --qdrant-url http://10.159.172.74:6333 \
  --collection robo_claw_former_0047_w2_2f
# 사이트별 질의셋:  --queries-file myqueries.json   (JSON: [{"query":"...","expect":"..."}])
# 대칭(옛 방식)과 A/B 비교:  --no-query-instruction
```

- 각 질의의 상위 N개 히트 score + '정답' 매치를 표시하고, 마지막에 **정답 매치 score의
  min/avg/max**와 **권장 threshold(정답 최저점 −0.05)** 를 출력합니다.
- **비대칭 임베딩**: `qwen3-embedding` 모델이면 에이전트와 동일하게 쿼리에 instruction을
  붙여 측정합니다(헤더에 `query instruction: ON`). `--no-query-instruction`으로 끄면 옛(대칭)
  방식과 비교할 수 있고, `--query-instruction "<문구>"`로 지시문을 바꿀 수 있습니다.
- ⚠️ `--ollama-url`/`--embed-model`은 에이전트가 쓰는 값(`RC_LLM_EMBEDDING_BASE_URL`,
  `RC_LLM_EMBEDDING_MODEL`)과 **동일**해야 점수가 일치합니다.
- 이 값으로 config 서버의 **RAG Score Threshold**를 조정 → 재기동하세요. (임베딩 **모델**을 바꾸면
  점수/차원이 달라지므로 컬렉션 재색인 후 다시 측정. 쿼리 instruction 변경은 재색인 불필요)

## apply_rag_params.py — 활성 config의 RAG threshold/top_k 갱신

프로브로 산출한 값을 config 서버에 반영합니다. **코드 기본값(`params.py`)은 config 서버 값에
덮어써지므로**, 코드만 바꾸고 config를 안 바꾸면 옛 값으로 계속 동작합니다.
(실제로 2026-08-12 리포트가 `0.6/4`로 돌았던 원인)

```bash
ADMIN_TOKEN=xxxx ./apply_rag_params.py --env 0047_w2_2f --dry-run   # 미리보기
ADMIN_TOKEN=xxxx ./apply_rag_params.py --env 0047_w2_2f             # 반영 (기본 0.55 / 5)
ADMIN_TOKEN=xxxx ./apply_rag_params.py --env 0047_w2_2f --threshold 0.5 --top-k 6
```

- `UpdateConfig`가 레코드를 통째로 교체(GORM Save)하므로 **GET → 필드 교체 → 전체 PUT**
  라운드트립을 씁니다(`apply_skills_guide.py`와 동일 방식). 마스킹된 `********` 비밀값은
  서버가 원본으로 복원합니다. 실행 전 `config-<id>-<ts>.bak.json`으로 백업합니다.
- ⚠️ **`--env`를 반드시 지정하세요.** 운영 DB에는 `is_active=true` 레코드가 여러 개 있어
  (2026-08-12 기준 former 7건) 자동 선택이 안전하지 않습니다.
- ⚠️ 반영 후 **에이전트 컨테이너 재기동**이 필요합니다. 확인은 `robo_talk.py` 리포트 헤더의
  `RAG Top-K` / `RAG Score Threshold`.

---

## 설치 (외부 호스트, 최초 1회)

```bash
pip install grpcio grpcio-tools
cd validation/talk
./gen_proto.sh        # messenger_pb2.py, messenger_pb2_grpc.py 생성
```

## 사용

### 1) 시나리오 자동 실행 (로봇이 실제로 이동)

```bash
python3 robo_talk.py --host 10.159.172.69 \
  --scenario ../testcases/first-use-scenario-auto.json \
  --env 0047_w2_2f --config-token "$TOKEN" \
  --output report-$(date +%Y%m%d-%H%M%S).md
```

- 케이스별 PASS/FAIL이 실시간 출력되고, `--output`에 마크다운 리포트가 저장됩니다.
- 리포트 **맨 첫 줄**에 소프트웨어 버전과 **실행 코드 최신성**이 출력됩니다(실행 전 콘솔에도 먼저 표시):

  ```
  - 소프트웨어: 실행코드 `08233e2` (호스트 체크아웃(using_gemma4_e4b)) · 이미지 `08233e2`/`202612-3-...` build `2026-08-12 16:21:29 KST` · 일치 ✅
  ```

  ⚠️ **이미지 태그·commit 은 실행 코드와 무관할 수 있습니다.**
  `robo_claw_cli launch --docker` 는 **로봇 호스트의 소스 트리**를 컨테이너 install 공간 위에
  bind mount 합니다:

  ```
  /home/former/workspace/robo-claw/robo-claw/src/robo_claw_agent/robo_claw_agent
    → /ros2_ws/install/robo_claw_agent/{lib,local/lib}/python3.{10,12}/{site,dist}-packages/...
  ```

  즉 **파이썬 코드는 이미지에서 오지 않습니다**(이미지 install 산출물은 마운트에 가려짐).
  `/etc/robo_claw_version` 이 최신이어도, `--no-cache` 로 재빌드해도 실행 코드는 그대로입니다.
  2026-08-12 에 이 사실을 몰라 재빌드 3회를 헛돌고 몇 시간을 오진했습니다.

  그래서 이 도구는 **실행 코드의 실제 신원 = 로봇 호스트 체크아웃의 git commit** 을 읽어
  테스트 기준(이 저장소 HEAD)과 비교합니다. 불일치면 조치까지 명시합니다:

  ```
  ⚠️ 테스트 기준 `08233e2` 과 다릅니다 → 로봇에서 git pull + 컨테이너 재기동 필요
     (이미지 재빌드로는 반영되지 않습니다)
  ```

  **파이썬 코드만 바뀐 경우 반영 절차는 재빌드가 아닙니다:**

  ```bash
  ssh former@10.159.172.69 'cd /home/former/workspace/robo-claw/robo-claw && git pull'
  ssh former@10.159.172.69 'docker restart robo_claw_container'
  ```

  (이미지 재빌드가 필요한 것은 의존성·`robo_claw_msgs`·비파이썬 구성이 바뀔 때입니다.)

  | 옵션              | 기본값                                       | 설명                               |
  | :---------------- | :------------------------------------------- | :--------------------------------- |
  | `--robot-ssh`     | `former@<--host>`                            | 조회용 ssh 대상                    |
  | `--container`     | `robo_claw_container`                        | 에이전트 컨테이너 이름             |
  | `--robot-repo`    | `/home/former/workspace/robo-claw/robo-claw` | 로봇 호스트 저장소(실행 코드 출처) |
  | `--no-build-info` | 꺼짐                                         | 조회 생략(ssh 불가 환경)           |

  ssh가 안 되면 조회 실패 사유만 한 줄로 남고 **시나리오는 정상 진행**됩니다.

- 리포트 맨 위 **`## AI Config`** 섹션에 실행에 쓰인 프로파일·LLM(Model/Model URL/num_ctx/
  temperature/Repeat Penalty/Repeat Last N/Seed/Max Predict Token/Top K/Top P/Min P)·임베딩
  (Model/Qdrant URL/Collection/Timeout/RAG Top-K/Score Threshold)이 출력됩니다.
  (`scripts/show_ai_config.py`와 동일 항목)
- 판정 규칙은 케이스 `params`의 `expect_success` / `expected_keywords`(포함 필수) /
  `fail_keywords`(포함 금지, 예: `"[스킬"`, `"이동 성공 좌표"`)를 따릅니다.
- **참조 Qdrant 항목**: RAG 검색이 collection의 어떤 데이터를 참조했는지, 응답의 `referenced`가
  result_json에 실려 오면 상세 로그에 **"참조 Qdrant 항목"**(id·score·type·근거·좌표)으로 출력됩니다.
  (에이전트 측 docker logs에도 `[rag_search] … 참조 N건: id=… score=… type=…`으로 남습니다.)

특정 스텝만:

```bash
python3 robo_talk.py --host 10.159.172.69 --scenario ../testcases/first-use-scenario-auto.json --only tc03,tc16,tc17
```

#### AI Config 헤더 조회 (인증 필요)

config 서버에 토큰이 설정돼 있으면 `## AI Config` 조회에 **Bearer 토큰**이 필요합니다
(없으면 `조회 실패 — 사유: HTTP 401 …`이 헤더에 표시됨).

- 토큰: `--config-token <TOKEN>` 또는 환경변수 `ROBOCLAW_CONFIG_TOKEN`/`ADMIN_TOKEN`/`DEVICE_TOKEN`
- env 지정: `--env 0047_w2_2f` (미지정 시 `is_active` 자동 탐색 — 단, 자동탐색 경로 `/configs`는
  **admin 토큰** 필요. device 토큰만 있으면 `--env`를 명시해 `/configs/active`로 조회)
- 헤더가 필요 없으면 `--no-config`로 조회 자체를 생략

```bash
export ROBOCLAW_CONFIG_TOKEN=xxxx
python3 robo_talk.py --host 10.159.172.69 --env 0047_w2_2f \
  --scenario ../testcases/first-use-scenario-auto.json --output report.md
```

| 옵션             | 기본값                       | 설명                                           |
| :--------------- | :--------------------------- | :--------------------------------------------- |
| `--config-url`   | `http://10.159.172.74:30180` | AI Config 서버 URL                             |
| `--robot`        | `former`                     | 조회용 로봇명(비우면 필터 없음)                |
| `--env`          | (없음)                       | 조회용 환경명(미지정 시 `is_active` 자동 탐색) |
| `--config-token` | 환경변수                     | Bearer 토큰                                    |
| `--no-config`    | 꺼짐                         | AI Config 조회 생략                            |

### 2) 대화형 (robo_claw_talk 처럼 직접 명령)

```bash
python3 robo_talk.py --host 10.159.172.69 -i
# you> 충전대로 이동해
# robot[ok]> 충전대 (3.86, 0.83)지점으로 이동 완료
```

### 로봇 위에서 직접 실행할 때

```bash
python3 robo_talk.py --host localhost --scenario ...
```

## 사전 조건

- 로봇에서 에이전트 컨테이너가 기동되어 RoboMessenger(50052)가 떠 있어야 합니다.
  (기동: 터미널1에서 `robo_claw_cli launch former 0047_w2_2f --docker --image-tag <태그> --pull --use-grpc`)
- 외부 호스트에서 `<로봇IP>:50052`로 네트워크 접근이 가능해야 합니다(방화벽 확인).
- 로봇 준비: 맵 `w2_2f` 로드 + 초기 위치(AMCL) + 시나리오 좌표 도달 가능.
- (선택) 테스트 간 메모리 격리가 필요하면 실행 전 `validation/reset_memory.sh -y` 후 컨테이너 재기동.

## 테스트 케이스 형식 (확장 방법)

`../testcases/first-use-scenario-auto.json`과 동일한 형식입니다. 케이스 추가는 배열에
아래 항목을 넣기만 하면 됩니다(로봇/코드 수정 불필요):

```json
{
  "id": "tc_new",
  "name": "새 검증 항목",
  "step": "Phase X",
  "type": "custom",
  "timeout_ms": 120000,
  "enabled": true,
  "params": {
    "prompt": "로봇에게 보낼 자연어 명령",
    "expect_success": true,
    "expected_keywords": ["응답에 반드시 포함"],
    "fail_keywords": ["[스킬", "이동 성공 좌표"]
  }
}
```

- `type`이 `custom`이거나 `params.prompt`가 있는 케이스만 전송합니다.
- `ping` 등 prompt 없는 케이스는 SKIP됩니다(이 클라이언트는 명령 채널만 사용).

## robo_claw_cli test 와의 관계

|             | robo_talk (이 도구)       | robo_claw_cli test         |
| :---------- | :------------------------ | :------------------------- |
| 언어/의존성 | Python + grpcio           | Go 바이너리                |
| 접속        | `<ip>:50052` 직접         | 설정 fetch(.74) + gRPC     |
| 실행 위치   | **외부 호스트/CI 어디든** | 주로 로봇/설정 연동 환경   |
| 용도        | 확장/자동화/대화형        | 프로필 정합성 포함 종합 QA |

두 도구는 **같은 시나리오 JSON**을 공유하므로 케이스는 한 번만 관리하면 됩니다.
