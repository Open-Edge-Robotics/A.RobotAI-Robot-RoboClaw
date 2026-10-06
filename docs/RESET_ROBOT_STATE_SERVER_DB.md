# 로봇 상태 + 벡터 DB 완전 초기화 (호스트 SSH)

`reset_robot_state.sh`(호스트→SSH, config 캐시 + 메모리)와 `reset_memory.sh`(로봇에서 실행,
Qdrant 컬렉션까지)를 **하나로 합친** 스크립트입니다. 호스트 한 곳에서 아래를 순서대로 처리합니다.

> **CLI**: 이 스크립트는 저장소 루트 `scripts/` 아래에 있습니다. `rclaw reset-robot full`
> (`state` / `memory` 개별 초기화는 `rclaw reset-robot state|memory`) 로도 동일하게 실행할 수 있으며
> `--robot` / `--qdrant-url` / `--collection` / `-y` / `--dry-run` / `--restart` / `--skip-*`
> 플래그를 받습니다.

```
1) 컨테이너 정지        docker stop robo_claw_container
2) config 캐시 삭제     rm -rf ~/.robo_claw/config_cache
3) 메모리 파일 삭제     rm -rf <memory_dir>/memory.*  <memory_dir>/places.db*
4) Qdrant 컬렉션 삭제   DELETE <qdrant-url>/collections/<collection>
```

## 왜 이 순서인가 (중요)

**컨테이너를 먼저 멈춰야 합니다.** `rag_local_mirror=true` 이면 `DualVectorStore`(Qdrant primary
\+ 로컬 미러)를 쓰고, 백그라운드 `reconcile` 스레드가 양쪽을 **합집합 머지**합니다. 컨테이너가
도는 상태에서 Qdrant 컬렉션만 지우면 로컬 미러(`memory.kb.json`)에서 **원래 timestamp 그대로
되살아납니다.** 반대로 로컬만 지워도 Qdrant에서 되살아납니다.

각 항목의 효과:

| 대상                   | 지우면                                                                                |
| :--------------------- | :------------------------------------------------------------------------------------ |
| config 캐시            | 다음 `launch` 때 config 서버에서 프로파일(soul/skill/limits/troubleshooting) 재-fetch |
| `memory.kb.json`       | 로컬 벡터 미러 초기화                                                                 |
| `memory.semantic.json` | 시맨틱 맵(이름→좌표) 초기화                                                           |
| `memory.cold.db`       | 콜드 아카이브(만료된 관찰 기록) 초기화                                                |
| Qdrant 컬렉션          | 원격 벡터 DB 초기화. 첫 저장 시 임베더 차원으로 자동 재생성                           |

## 요구사항

- 로봇에 SSH 키 접속(`ssh-copy-id`) — `BatchMode=yes` 로 실행하므로 비밀번호 입력은 안 받습니다.
- Qdrant 삭제는 **호스트에서 먼저** 시도하고, 실패하면 **로봇 경유**로 재시도합니다.
  (호스트가 Qdrant 망에 닿지 않는 환경 대응)

## 사용법

```bash
cd robo-claw/scripts

# 대화형 — 세 값을 물어봅니다(엔터 시 기본값)
./reset_robot_state_server_db.sh

# 플래그
./reset_robot_state_server_db.sh \
  --robot former@10.159.172.69 \
  --qdrant-url http://10.159.172.74:6333 \
  --collection robo_claw_former_0045_w2_2f -y --restart

# 위치 인자 (순서: qdrant-url, collection, robot)
./reset_robot_state_server_db.sh http://10.159.172.74:6333 robo_claw_former_0045_w2_2f former@10.159.172.69 -y

# 먼저 계획만 확인 (권장)
./reset_robot_state_server_db.sh --dry-run

# Qdrant만 초기화
./reset_robot_state_server_db.sh --skip-config --skip-memory -y
```

입력 3개(**Qdrant URL / 컬렉션명 / 로봇 `user@ip`**)는 **플래그·환경변수·위치 인자·대화형 프롬프트**
네 가지 방식으로 모두 받습니다. 우선순위는 `플래그 > 환경변수 > 위치 인자 > 프롬프트 > 기본값`입니다.

## 옵션 / 환경변수

| 인자                    | 환경변수(기본값)                         | 설명                                            |
| :---------------------- | :--------------------------------------- | :---------------------------------------------- |
| `--robot <user@ip>`     | `ROBOT=former@10.159.172.69`             | 로봇 SSH 대상                                   |
| `--qdrant-url <url>`    | `QDRANT_URL=http://10.159.172.74:6333`   | Qdrant 주소                                     |
| `--collection <name>`   | `COLLECTION=robo_claw_former_0045_w2_2f` | Qdrant 컬렉션명                                 |
| `--container <name>`    | `CONTAINER=robo_claw_container`          | 에이전트 컨테이너명                             |
| `--memory-dir <path>`   | `MEMORY_DIR=`(자동 탐지)                 | 메모리 경로. 미지정 시 `docker inspect` 로 탐지 |
| `--config-cache <path>` | `CONFIG_CACHE=~/.robo_claw/config_cache` | config 캐시 경로                                |
| `--skip-config`         | —                                        | config 캐시 단계 생략                           |
| `--skip-memory`         | —                                        | 메모리 파일 단계 생략                           |
| `--skip-qdrant`         | —                                        | Qdrant 단계 생략                                |
| `--restart`             | —                                        | 초기화 후 컨테이너 재시작                       |
| `-y`, `--yes`           | —                                        | 확인 프롬프트 생략                              |
| `--dry-run`             | —                                        | 실행될 명령만 출력(삭제 안 함)                  |
| `-h`, `--help`          | —                                        | 도움말                                          |

### 컬렉션명 확인하는 법

컬렉션명은 **env 이름과 다를 수 있고, env 를 바꿔도 자동으로 따라오지 않습니다.**
지난 리포트의 `Qdrant Collection` 값은 **그 실행 당시**의 것이라 현재와 다를 수 있으니,
초기화 전에는 반드시 **live 값**을 확인하세요. (실제로 env 가 `0047_w2_2f` → `0045_w2_2f` 로
바뀐 이력이 있어, 이전 리포트에는 `robo_claw_former_0047_w2_2f` 가 찍혀 있습니다)

**① config 서버의 현재 매핑** (admin 토큰 필요):

```bash
ADMIN_TOKEN=xxxx python3 -c '
import json, os, urllib.request
u = "http://10.159.172.74:30180/api/v1/admin/configs?robot_name=former"
r = urllib.request.Request(u, headers={"Authorization": "Bearer " + os.environ["ADMIN_TOKEN"]})
for c in json.loads(urllib.request.urlopen(r, timeout=15).read()):
    print("ID=%s env=%-14s collection=%s" % (c.get("ID"), c.get("environment"), c.get("qdrant_collection")))'
```

2026-08-14 기준 (`former`):

|     ID | env            | collection                        |
| -----: | :------------- | :-------------------------------- |
|      2 | lab            | `robo_claw_former`                |
|      3 | w2_2f          | `robo_claw_former_w2_2f`          |
|      8 | lab2           | `robo_claw_former_lab2`           |
|     12 | w2_2f_2        | `robo_claw_former_w2_2f_2`        |
|     13 | w2_2f_3        | `robo_claw_former_w2_2f_3`        |
| **14** | **0045_w2_2f** | **`robo_claw_former_0045_w2_2f`** |

**② Qdrant 에 실제로 있는 컬렉션과 건수** (토큰 불필요):

```bash
python3 -c '
import json, urllib.request
b = "http://10.159.172.74:6333"
cols = json.loads(urllib.request.urlopen(b + "/collections", timeout=15).read())["result"]["collections"]
for c in cols:
    n = c["name"]
    i = json.loads(urllib.request.urlopen(b + "/collections/" + n, timeout=10).read())["result"]
    print("%-40s points=%s" % (n, i["points_count"]))'
```

⚠️ 위 조회로 **더 이상 쓰지 않는 고아 컬렉션**도 함께 확인하세요. 2026-08-14 기준 Qdrant 에는
`robo_claw_former_local`(244), `robo_claw_former_lab2`(247), `robo_claw_stretch_*` 등 과거
프로파일 컬렉션이 남아 있습니다. 초기화 대상이 아니면 건드리지 마세요.

## 안전장치

- **메모리는 디렉터리가 아니라 내용만 지웁니다.** 이 디렉터리는 컨테이너의 `/ros2_ws/memory`
  **bind mount 소스**입니다. 디렉터리째 지우면 다음 기동 때 docker 가 root 소유로 재생성해
  권한 문제가 생깁니다. 그래서 `memory.*` / `places.db*` 만 삭제합니다.
  (원본 `reset_robot_state.sh` 는 `rm -rf <dir>` 였습니다 — 이 부분이 개선점입니다)
- 대상 경로가 비었거나 위험 경로(`~`, `~/`, `/`, `.`, `..`, `$HOME`) 또는 1단계 이하이면
  **삭제하지 않고 중단**합니다(exit 3).
- `--robot` 은 `user@ip` 형식, `--qdrant-url` 은 `http(s)://` 를 검사합니다(exit 2).
- `~` 는 **로봇(원격) 셸**에서 확장됩니다(호스트가 아니라).
- **Qdrant 삭제 후 검증**: `GET /collections/<name>` 이 404 인지 확인해 `✅` 또는
  `[WARN] 아직 존재` 를 출력합니다.
- 경고가 하나라도 있으면 **종료 코드 1** 을 반환합니다(CI 판정용).

## 초기화 후

컨테이너는 정지 상태입니다(`--restart` 를 안 줬다면). 재기동하세요:

```bash
ssh former@10.159.172.69 'docker start robo_claw_container'
# 또는 프로파일을 새로 받으며 기동
robo_claw_cli launch former <env> --docker --image-tag <태그> --use-grpc
```

기동 로그에서 초기화가 반영됐는지 확인:

```
[agent_node-2] No knowledge stored in vector DB          ← 메모리 비어 있음
[agent_node-2] Qdrant RAG connection ready: collection=… ← 컬렉션은 첫 저장 시 재생성
```

## 관련 스크립트

| 스크립트                             | 실행 위치 | 범위                                        |
| :----------------------------------- | :-------- | :------------------------------------------ |
| **`reset_robot_state_server_db.sh`** | 호스트    | config 캐시 + 메모리 + **Qdrant** (이 문서) |
| `reset_robot_state.sh`               | 호스트    | config 캐시 + 메모리 (Qdrant 제외)          |
| `reset_memory.sh`                    | **로봇**  | 메모리 + Qdrant (config 캐시 제외)          |

`reset_robot_state_server_db.sh` 가 위 둘을 포괄하므로 보통 이것만 쓰면 됩니다.
`reset_memory.sh` 는 로봇에 직접 접속해 있을 때의 대안으로 남겨 둡니다.

## 참고: 파이썬 코드 변경은 이 스크립트로 반영되지 않습니다

로봇은 **호스트 소스 트리를 컨테이너 install 공간에 bind mount** 합니다. 따라서 에이전트
파이썬 코드를 바꿨다면 이 초기화가 아니라 아래가 필요합니다(이미지 재빌드도 불필요):

```bash
ssh former@10.159.172.69 'cd ~/workspace/robo-claw/robo-claw && git pull'
ssh former@10.159.172.69 'docker restart robo_claw_container'
```

반영 여부는 `robo_talk.py` 리포트 첫 줄의 `실행코드 <commit> … 일치 ✅` 로 확인합니다.
자세한 내용은 `validation/talk/README.md` 참고.
