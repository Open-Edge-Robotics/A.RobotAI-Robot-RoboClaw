# System 1 Fast Router(Laya) 사용 가이드

이 문서는 robo-claw에 System 1 Fast Router를 설정해 사용하는 절차를 설명합니다. 다루는 범위는 Laya 서버 실행, robo-claw에 추가할 환경변수, 실행 방식별 환경변수 전달 방법, 단계별 적용, 롤백, 문제 해결입니다. 설계 배경과 단계별 도입 계획은 [SYSTEM1_FAST_ROUTER_DESIGN.md](SYSTEM1_FAST_ROUTER_DESIGN.md)를 참고합니다.

> Jev(TypeSafe AI)는 유료 API(billing 등록 필요)라 기본 경로로 사용하지 않습니다. 이 가이드는 자체 호스팅하는 오픈 웨이트 모델 [Laya](https://huggingface.co/convaiinnovations/laya)(Apache 2.0)를 기준으로 합니다. 두 모델은 같은 `POST /v1/systemone` 스키마를 쓰므로 나중에 Jev로 바꿀 때는 환경변수만 변경합니다(4.3절).

## 1. 동작 개요

```text
사용자 지시문
  │
  ▼
사전 라우팅 (SYSTEM1_ROUTER 로 하나만 선택)
  ├─ rule : 기존 규칙(check_direct_skill, 단순 인사 패턴)
  └─ laya : Laya 서버 질의 ── 장애 시 해당 요청만 rule 로 자동 대체
  │
  ├─ 인사/잡담 (확신 높음)               → 고정 응답
  ├─ 인자 없는 조회 스킬 (확신 높음)      → 스킬 직접 실행
  ├─ 기억된 장소로 이동 (navigation scope) → navigate_to 직접 실행 (nav_safety 적용)
  └─ 그 외 / 모호 / 저신뢰 / 복합 명령    → 기존 LLM planner (System 2)
```

- 기본값은 `SYSTEM1_ROUTER=rule`입니다. 환경변수를 하나도 추가하지 않으면 기존 동작과 같습니다.
- LLM 출력 교정(`_force_navigation_if_misrouted`, `_route_location_query`)과 가드(`_extract_save_place`, `nav_safety`, `sensor_health`)는 라우터 모드와 무관하게 항상 동작합니다.
- `laya` 모드에서는 규칙 라우터의 지름길이 동작하지 않습니다. "앞으로 2미터"(`move_relative`), 컵 집기, 카메라/지도 캡처 직접 실행이 해당합니다. 이 요청들은 LLM planner가 처리합니다. Laya가 숫자나 자유 텍스트 인자를 추출하지 못하기 때문입니다.

## 2. 사전 준비

| 항목 | 요구사항 |
|---|---|
| robo-claw 코드 | System 1 라우터가 포함된 브랜치(`jev-laya`)를 빌드합니다(`task build`). |
| Laya 서버 | Python 3.10 이상, `torch>=2.0`, `transformers>=5.0`. GPU 권장(모델 카드 기준 T4에서 질문 1개 약 33~40ms). CPU에서도 동작하지만 느립니다. 메모리는 multilingual 체크포인트 기준 약 650MB입니다. |
| 네트워크 | robo-claw에서 Laya 서버 포트(기본 8000)로 HTTP 접속이 가능해야 합니다. 모델은 첫 기동 때 Hugging Face에서 내려받으므로, 폐쇄망이면 미리 받아 둡니다(3.5절). |
| 평가 데이터 | `validation/system1/router_cases.jsonl`(시드 32건)을 그대로 쓰거나 현장 명령을 추가합니다. |

## 3. Laya 서버 배치

robo-claw는 `SYSTEM1_ENDPOINT`로 HTTP 요청만 보내므로, Laya 서버를 어디에 두든 같은 코드로 동작합니다. 배치 방식은 세 가지이고 환경변수만 다릅니다.

| 방식 | 실행 위치 | GPU | 적합한 경우 |
|---|---|---|---|
| A. 엣지 서버 | 별도 서버의 Laya 컨테이너(`docker/laya/`) | 엣지 서버 GPU | 여러 로봇이 공유할 때. 로봇 CPU/GPU를 쓰지 않으므로 주행·모션 제어에 영향이 없습니다. |
| B. Thor 내장 | robo-claw 컨테이너 안(launch가 함께 실행) | Thor GPU, 없으면 CPU | 네트워크 없이 로봇 단독으로 쓸 때 |
| C. CPU | B와 같거나 별도 호스트 | 없음 | 동작 확인, GPU가 없는 장비 |

세 방식 모두 `docker/laya/install_laya.sh` 한 가지 설치 절차를 씁니다. GPU 사용 여부는 torch 휠 인덱스(`LAYA_TORCH_INDEX_URL`)로 정합니다.

| 대상 | `LAYA_TORCH_INDEX_URL` |
|---|---|
| CPU (amd64/arm64, 기본값) | `https://download.pytorch.org/whl/cpu` |
| x86 엣지 서버 + NVIDIA GPU | 드라이버에 맞는 CUDA 인덱스(예: `https://download.pytorch.org/whl/cu128`) |
| Thor (aarch64) | JetPack의 CUDA 버전에 맞는 aarch64 CUDA 인덱스. PyTorch 설치 안내와 JetPack 릴리스 노트에서 확인합니다. |

> Thor용 CUDA torch 인덱스와 Thor에서의 실제 GPU 추론은 아직 검증하지 않았습니다. 처음에는 CPU 이미지로 동작을 확인한 뒤 GPU 이미지로 바꾸는 것을 권장합니다. GPU를 쓰지 못하면 Laya가 스스로 CPU로 실행하므로(`LAYA_DEVICE`는 선호값), 잘못된 이미지라도 동작은 합니다.

### 3.1 A. 엣지 서버 (GPU)

엣지 서버에 NVIDIA 드라이버와 NVIDIA Container Toolkit이 있어야 합니다.

```bash
cd docker/laya
export LAYA_API_KEY=$(openssl rand -hex 24)        # 외부에 여는 서버는 인증을 켭니다
docker compose -f compose.edge.yaml up -d --build  # 기본 cu128. 다른 CUDA는 LAYA_TORCH_INDEX_URL 로 지정
docker compose -f compose.edge.yaml logs -f laya   # 모델 다운로드 후 listening 확인
```

robo-claw 설정:

```dotenv
SYSTEM1_ROUTER=laya
SYSTEM1_ENDPOINT=http://<엣지서버 IP>:8000
SYSTEM1_API_KEY=<위 LAYA_API_KEY 와 같은 값>
SYSTEM1_TIMEOUT_MS=300
```

`laya-serve`는 `LAYA_API_KEY`가 설정되면 `Authorization: Bearer <키>`를 요구하고, robo-claw는 `SYSTEM1_API_KEY`를 같은 헤더로 보냅니다.

### 3.2 B. Thor 내장 (robo-claw 컨테이너 안)

1. Laya를 포함한 robo-claw 이미지를 Thor에서 빌드합니다. 기본 이미지에는 Laya가 들어 있지 않습니다(`INSTALL_LAYA=false`).

   ```bash
   # CPU 로 먼저 확인
   task docker-build-native INSTALL_LAYA=true
   # GPU (인덱스는 JetPack 에 맞게 지정)
   task docker-build-native INSTALL_LAYA=true LAYA_TORCH_INDEX_URL=<aarch64 CUDA 인덱스>
   ```

   `scripts/run_robo_claw_docker.sh`가 이미지를 직접 빌드할 때는 `RC_INSTALL_LAYA=true`, `RC_LAYA_TORCH_INDEX_URL=...`을 사용합니다.

2. 컨테이너에 GPU를 넘겨 실행합니다.

   ```bash
   ./scripts/run_robo_claw_docker.sh --gpu --hf-cache ~/robo_claw_hf --robot-config cloid
   ```

   | 옵션 | 동작 |
   |---|---|
   | `--gpu` (`RC_DOCKER_GPU=true`) | nvidia 런타임이 있으면 `--runtime nvidia`(Jetson/Thor), 없으면 `--gpus all`을 붙입니다. `RC_DOCKER_GPU_MODE=runtime\|gpus`로 강제할 수 있습니다. |
   | `--hf-cache <dir>` (`RC_HF_CACHE_DIR`) | 모델 캐시를 호스트에 보존합니다(컨테이너의 `/opt/hf`). 재시작해도 다시 받지 않습니다. |

   다른 방법(compose 등)으로 컨테이너를 띄운다면 `runtime: nvidia`(또는 GPU device 예약)와 `NVIDIA_VISIBLE_DEVICES=all`, `NVIDIA_DRIVER_CAPABILITIES=compute,utility`를 같은 의미로 지정합니다. `./rclaw launch --docker`는 아직 GPU 옵션을 지원하지 않습니다.

3. robo-claw 설정:

   ```dotenv
   SYSTEM1_ROUTER=laya
   SYSTEM1_LOCAL_SERVER=true        # launch 가 같은 컨테이너에서 laya-serve 를 실행
   # SYSTEM1_ENDPOINT 는 비워 둡니다 → http://127.0.0.1:8000 으로 자동 접속
   SYSTEM1_LOCAL_DEVICE=auto        # cuda 가능하면 GPU, 아니면 CPU
   SYSTEM1_LOCAL_THREADS=2          # CPU 추론 스레드 상한
   SYSTEM1_TIMEOUT_MS=1000          # CPU 로 돌 수 있으면 넉넉히. GPU 확인 후 줄입니다
   ```

4. 기동 로그를 확인합니다.

   ```text
   [system1-local] starting laya-serve: device=cuda host=127.0.0.1 port=8000 threads=2 nice=10
   System1 router: router=laya shadow=... scope=readonly endpoint=http://127.0.0.1:8000 local_server=True
   System1 server ready (http://127.0.0.1:8000) after 25s
   ```

   `device=cpu`와 `CUDA not available — using cpu`가 보이면 GPU가 컨테이너에 전달되지 않았거나 이미지의 torch가 CPU 빌드입니다. `laya-serve not found`가 보이면 `INSTALL_LAYA=true`로 빌드하지 않은 이미지입니다. 두 경우 모두 에이전트는 규칙 라우터로 계속 동작합니다.

### 3.3 주행·모션 로봇에서 함께 실행할 때

Thor 내장 방식은 Laya가 주행(Nav2)·팔/손 모션 제어와 같은 CPU/GPU를 씁니다. 제어를 방해하지 않도록 다음이 기본 적용됩니다.

| 항목 | 기본값 | 설정 |
|---|---|---|
| CPU 우선순위 | nice 10 (제어 프로세스보다 낮음) | `SYSTEM1_LOCAL_NICE` |
| torch/OpenMP 스레드 | 2 | `SYSTEM1_LOCAL_THREADS` |
| 상주 체크포인트 | multilingual 1개 (`LAYA_MAX_LOADED=1`) | `LAYA_MODELS` 등 직접 지정 시 우선 |
| 바인딩 | `127.0.0.1` (로봇 내부에서만 접근) | `SYSTEM1_LOCAL_HOST` |
| 비정상 종료 | 최대 5회 재시작(5초부터 최대 60초 간격) 후 중단 | `SYSTEM1_LOCAL_MAX_RESTARTS` |

- Laya가 느리거나 죽어도 로봇 동작은 멈추지 않습니다. timeout이나 오류가 난 요청은 규칙 라우터가 처리합니다.
- 주행·모션 중 Laya 추론이 제어 주기(예: `core_node` 50Hz)나 Nav2 지연에 영향을 주는지 실측합니다. 영향이 있으면 `SYSTEM1_LOCAL_THREADS`를 줄이거나 엣지 서버(A)로 옮깁니다.

### 3.4 C. CPU / 직접 실행

GPU 없이 확인하거나 이미지 없이 실행할 때는 호스트에서 직접 실행합니다.

```bash
python3 -m venv ~/laya-venv && source ~/laya-venv/bin/activate
bash docker/laya/install_laya.sh                     # 기본 CPU torch
LAYA_MODELS=multilingual LAYA_DEFAULT_MODEL=multilingual LAYA_THREADS=2 laya-serve
```

엣지 서버용 단독 이미지를 CPU로 만들 수도 있습니다(`docker build -f docker/laya/Dockerfile -t robo-claw-laya:cpu docker/laya`).

### 3.5 모델 캐시와 폐쇄망

- 모델은 첫 기동 때 Hugging Face에서 받아 `HF_HOME`(이미지 기본 `/opt/hf`)에 저장합니다. 첫 기동은 다운로드와 로딩으로 수십 초 이상 걸릴 수 있습니다.
- 캐시를 보존하려면 볼륨을 마운트합니다(B: `--hf-cache`, A: compose의 `laya-hf` 볼륨).
- 폐쇄망 로봇은 인터넷이 되는 곳에서 한 번 기동해 캐시 디렉터리를 채운 뒤 그 디렉터리를 옮겨 마운트하거나, 빌드 인자 `LAYA_PREFETCH_REPOS`로 이미지에 미리 넣습니다. 저장소 ID는 [Laya 모델 카드](https://huggingface.co/convaiinnovations/laya)에서 확인합니다.

### 3.6 응답 확인

robo-claw가 도는 호스트(또는 컨테이너 안)에서 확인합니다.

```bash
curl -s http://<laya-host>:8000/v1/systemone \
  -H 'Content-Type: application/json' \
  -d '{
    "state": {"instruction": "지금 배터리 얼마나 남았어?"},
    "questions": {
      "intent": {
        "type": "choice",
        "instructions": "사용자가 로봇에게 한 말의 종류는?",
        "criteria": {"smalltalk": "인사/잡담", "single_skill": "기능 하나로 처리", "multi_step": "여러 단계"}
      }
    }
  }'
```

- 정상 응답에는 `answers.intent.choice`, `answers.intent.confidence`, `routing.model`이 들어 있습니다. 인증을 켠 서버는 `-H "Authorization: Bearer <키>"`를 붙입니다.
- `routing.model`이 **multilingual** 체크포인트인지 확인합니다. 루트(영어) 체크포인트는 한국어를 지원하지 않습니다.
- `GET /health`는 체크포인트가 실제로 어느 장치(cuda/cpu)에서 계산되는지 보여 줍니다.

> 모델 카드 기준으로 기본 체크포인트는 zero-shot 정확도가 낮습니다(typed-decisions 0.362, fine-tune 후 0.766). 운영 적용 전에 5장의 평가를 반드시 수행합니다. 오실행이 많으면 robo-claw 데이터로 fine-tune한 체크포인트를 사용합니다(설계 문서 3.5절).

## 4. robo-claw 환경변수

### 4.1 필수 환경변수

`laya` 모드로 동작시키려면 아래 두 개를 **반드시** 추가합니다.

| 환경변수 | 값 | 설명 |
|---|---|---|
| `SYSTEM1_ROUTER` | `laya` | 사전 라우터 선택. `rule`(기본) 또는 `laya`입니다. 그 외 값은 경고 후 `rule`로 동작합니다. |
| `SYSTEM1_ENDPOINT` | `http://<laya-host>:8000` | Laya 서버 주소입니다. 끝에 `/v1/systemone`을 붙이지 않습니다. 비어 있으면 `laya` 모드여도 경고 후 `rule`로 동작합니다. |

그림자 모드(4.4절 ①)에서는 `SYSTEM1_ROUTER`를 `rule`로 두고 `SYSTEM1_SHADOW=true`와 `SYSTEM1_ENDPOINT`를 추가합니다.

Thor 내장 방식(3.2절)은 `SYSTEM1_ENDPOINT` 대신 `SYSTEM1_LOCAL_SERVER=true`를 넣습니다. `SYSTEM1_ENDPOINT`가 비어 있으면 `http://127.0.0.1:<SYSTEM1_LOCAL_PORT>`로 접속합니다.

### 4.2 선택 환경변수

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `SYSTEM1_SHADOW` | `false` | `true`면 선택되지 않은 라우터도 백그라운드로 실행해 판단만 기록합니다. 실행 결과에는 영향이 없습니다. `rule` 모드에서는 Laya를, `laya` 모드에서는 규칙을 그림자로 실행합니다. |
| `SYSTEM1_SHADOW_LOG` | (없음) | 그림자 기록을 남길 JSONL 파일 경로입니다. 없으면 노드 로그에만 남습니다. |
| `SYSTEM1_SCOPE` | `readonly` | `laya` 모드에서 Laya가 직접 실행할 수 있는 범위입니다. `readonly`는 인자 없는 read 스킬, `navigation`은 여기에 기억된 장소로의 `navigate_to`를 더합니다. 그 외 값은 `readonly`로 동작합니다. |
| `SYSTEM1_TIMEOUT_MS` | `300` | Laya 응답 대기 시간(ms)입니다. 초과하면 해당 요청은 규칙 라우터가 처리합니다. CPU 서버라면 늘립니다. |
| `SYSTEM1_CONF_THRESHOLDS_JSON` | `{"smalltalk":0.9,"single_skill":0.85,"skill":0.8,"target_place":0.8,"ambiguous":0.5}` | 직접 처리에 필요한 confidence 하한입니다. `ambiguous`만 상한이며, 이 값 이상이면 LLM으로 넘깁니다. 일부 키만 지정하면 나머지는 기본값을 씁니다. 형식이 잘못되면 무시합니다. |
| `SYSTEM1_SKILLS` | (없음) | Laya 후보 스킬 목록(쉼표 구분)입니다. 비어 있으면 `risk_level=read`이고 필수 인자가 없는 공개 스킬 전체를 씁니다. 선택지가 적을수록 정확합니다. |
| `SYSTEM1_MAX_OPTIONS` | `12` | 스킬과 장소 선택지 상한입니다. 초과하면 경고 후 잘라냅니다. 장소는 지시문에 이름이 들어간 장소 하나로 좁힙니다. |
| `SYSTEM1_PROVIDER` | `laya` | 로그와 trace에 표시되는 provider 이름입니다(`route.source`). |
| `SYSTEM1_API_KEY` | (없음) | `Authorization: Bearer`로 전송됩니다. 인증을 켠 엣지 서버(`LAYA_API_KEY`)나 외부 유료 provider(Jev 등)를 쓸 때 넣습니다. `rclaw config-effective`에서 마스킹됩니다. |
| `SYSTEM1_HEALTH_WAIT_SEC` | 내장 서버 `120`, 그 외 `0` | 기동 시 health check가 서버 준비를 기다리는 최대 시간(초)입니다. 모델 로딩 중 실패는 장애로 세지 않습니다. |

Thor 내장 서버(`SYSTEM1_LOCAL_SERVER=true`)에만 쓰는 항목입니다.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `SYSTEM1_LOCAL_SERVER` | `false` | `true`면 launch가 같은 컨테이너에서 `laya-serve`를 실행합니다. 이미지에 Laya가 없으면 경고만 남기고 넘어갑니다. |
| `SYSTEM1_LOCAL_DEVICE` | `auto` | `auto`/`cuda`는 CUDA를 쓸 수 있으면 GPU, 아니면 CPU로 실행합니다. `cpu`는 항상 CPU입니다. |
| `SYSTEM1_LOCAL_PORT` | `8000` | 내장 서버 포트입니다. |
| `SYSTEM1_LOCAL_HOST` | `127.0.0.1` | 바인딩 주소입니다. 다른 장비에서 접근하게 하려면 `0.0.0.0`으로 바꾸고 `LAYA_API_KEY`로 인증을 켭니다. |
| `SYSTEM1_LOCAL_THREADS` | `2` | torch/OpenMP 스레드 상한입니다. 제어 루프와 CPU를 나눠 쓰므로 작게 유지합니다. |
| `SYSTEM1_LOCAL_NICE` | `10` | 내장 서버의 CPU 우선순위(nice)입니다. |
| `SYSTEM1_LOCAL_MAX_RESTARTS` | `5` | 비정상 종료 시 재시작 횟수 상한입니다. |

`LAYA_*` 변수(`LAYA_MODELS`, `LAYA_API_KEY` 등)를 직접 지정하면 내장 서버가 그 값을 우선합니다. 단 `LAYA_DEVICE`와 `LAYA_PORT`는 위 `SYSTEM1_LOCAL_*` 값으로 정합니다.

circuit breaker(연속 3회 실패 시 30초 동안 Laya 호출 중단)는 환경변수로 바꿀 수 없는 고정값입니다.

### 4.3 Jev로 바꿀 때 (참고)

결제가 가능해지면 같은 코드로 Jev를 쓸 수 있습니다. 아래 두 값은 Jev 문서에서 확인한 값으로 바꿉니다.

```dotenv
SYSTEM1_ROUTER=laya
SYSTEM1_ENDPOINT=<Jev API base URL>
SYSTEM1_PROVIDER=jev
SYSTEM1_API_KEY=<Jev API key>
```

### 4.4 단계별 설정 예시

아래 블록을 4.5절의 전달 방법에 맞는 위치에 넣습니다.

**① 그림자 모드**: 실행은 규칙 라우터가 하고, Laya 판단은 기록만 합니다.

```dotenv
SYSTEM1_ROUTER=rule
SYSTEM1_SHADOW=true
SYSTEM1_ENDPOINT=http://<laya-host>:8000
SYSTEM1_SHADOW_LOG=/tmp/robo_claw_system1_shadow.jsonl
```

**② Laya 전환 (readonly)**: Laya가 인사와 인자 없는 조회 스킬을 직접 처리합니다. 규칙 판단은 계속 그림자로 기록합니다.

```dotenv
SYSTEM1_ROUTER=laya
SYSTEM1_ENDPOINT=http://<laya-host>:8000
SYSTEM1_SCOPE=readonly
SYSTEM1_SHADOW=true
SYSTEM1_SHADOW_LOG=/tmp/robo_claw_system1_shadow.jsonl
```

**③ 범위 확대 (navigation)**: ②에 기억된 장소로의 이동을 더합니다.

```dotenv
SYSTEM1_ROUTER=laya
SYSTEM1_ENDPOINT=http://<laya-host>:8000
SYSTEM1_SCOPE=navigation
SYSTEM1_SHADOW=true
```

**Thor 내장 (GPU 자동)**: robo-claw 컨테이너 안에서 Laya를 실행합니다(3.2절).

```dotenv
SYSTEM1_ROUTER=laya
SYSTEM1_LOCAL_SERVER=true
SYSTEM1_LOCAL_DEVICE=auto
SYSTEM1_SCOPE=readonly
SYSTEM1_SHADOW=true
SYSTEM1_TIMEOUT_MS=1000
```

**롤백**: 기존 동작으로 되돌립니다.

```dotenv
SYSTEM1_ROUTER=rule
```

### 4.5 실행 방식별 환경변수 전달 방법

`SYSTEM1_*`는 launch 인자나 ROS 파라미터가 아닙니다. 에이전트 프로세스가 **환경변수로** 직접 읽습니다. 따라서 실행 방식마다 넣는 위치가 다릅니다.

| 실행 방식 | `.env`에 넣으면 | 넣는 방법 |
|---|---|---|
| `scripts/run_robo_claw_docker.sh` | **전달됨** | 저장소 루트 `.env`(또는 `RC_ENV_FILE`로 지정한 파일)에 추가합니다. `--env-file`로 컨테이너에 전달됩니다. |
| 워크스페이스 `run_robo_claw_docker.sh` | **전달됨** | 실행하는 디렉터리의 `.env`에 추가합니다. |
| `./rclaw launch <robot> <env>` (로컬) | 서버 프로필로 전달 | AI Config Server 웹 설정의 **System 1 Fast Router** 섹션에 입력합니다. 서버가 캐시 `.env`(`~/.robo_claw/config_cache/<robot>_<env>/.env`)로 내려주며, 이 파일은 동기화 때마다 다시 생성되므로 직접 수정하지 않습니다. 웹 설정에 없는 항목(예: `SYSTEM1_LOCAL_*`)은 셸에서 `export`합니다. |
| `./rclaw launch <robot> <env> --docker` | 서버 프로필로 전달 | 웹 설정의 System 1 섹션 값이 캐시 `.env`(`--env-file`)로 컨테이너에 전달됩니다. 웹 설정에 없는 항목은 전달할 방법이 없으므로 `scripts/run_robo_claw_docker.sh`를 사용합니다. GPU 옵션도 아직 지원하지 않습니다. |
| `./rclaw run` / `./rclaw sim` | 전달 안 됨 | 실행 전에 셸에서 `export`합니다. `./rclaw run`은 `.env`를 launch 인자를 만드는 데만 쓰고 프로세스 환경변수로 넘기지 않습니다. |
| `ros2 launch robo_claw_bringup ...` 직접 실행 | 해당 없음 | 실행 전에 셸에서 `export`합니다. |

셸에서 `export`하는 예시입니다.

```bash
export SYSTEM1_ROUTER=laya
export SYSTEM1_ENDPOINT=http://<laya-host>:8000
export SYSTEM1_SCOPE=readonly
./rclaw run            # 또는 ./rclaw launch <robot> <env>
```

Docker 컨테이너는 `--network host`로 실행되므로, Laya 서버가 같은 호스트에 있으면 `SYSTEM1_ENDPOINT=http://localhost:8000`을 그대로 쓸 수 있습니다.

설정은 에이전트 기동 시 한 번 읽습니다. 값을 바꾸면 에이전트를 재기동해야 반영됩니다.

## 5. 적용 전 평가

로봇 없이 저장소에서 실행합니다. ROS 2가 필요 없습니다.

```bash
# 규칙 라우터 기준선 (Laya 서버 불필요)
python3 scripts/system1_eval.py --router rule

# 규칙과 Laya 비교
python3 scripts/system1_eval.py --router both --endpoint http://<laya-host>:8000

# navigation scope, 임계값 변경, 결과 저장
python3 scripts/system1_eval.py --router laya --endpoint http://<laya-host>:8000 \
  --scope navigation --thresholds '{"single_skill":0.9}' --json-out /tmp/system1_eval.json
```

결과 판정은 다음과 같습니다.

| 판정 | 의미 | 기준 |
|---|---|---|
| `correct` | 기대한 경로와 스킬/인자가 일치합니다. | 높을수록 좋습니다. |
| `escalated` | 직접 처리할 수 있었지만 LLM으로 넘겼습니다. 느리지만 안전합니다. | 줄이는 것이 목표입니다. |
| `wrong` | 다른 스킬을 실행했거나, LLM으로 넘겨야 할 요청을 직접 처리했습니다. **오실행**입니다. | **0에 가깝게 유지**합니다. |
| `error` | Laya 호출 실패(연결 거부, timeout 등)입니다. | 서버와 네트워크를 확인합니다. |

규칙 라우터 기준선은 시드 32건 기준 `correct 18 / escalated 14 / wrong 0`입니다. Laya가 `wrong` 0을 유지하면서 `escalated`를 줄일 때 전환할 가치가 있습니다. `wrong`이 나오면 `--thresholds`로 해당 항목의 임계값을 올리고 다시 측정합니다. 정한 값은 `SYSTEM1_CONF_THRESHOLDS_JSON`에 넣습니다. 현장 명령은 `validation/system1/router_cases.jsonl`에 추가합니다.

## 6. 동작 확인

1. 기동 로그에서 라우터 구성을 확인합니다.

   ```text
   System1 router: router=laya shadow=True scope=readonly endpoint=http://<laya-host>:8000
   ```

   `router=rule`로 나오면 환경변수가 전달되지 않은 것입니다(8장).

2. 조회 명령을 보내 봅니다.

   ```bash
   curl -X POST http://127.0.0.1:8080/task \
     -H "Authorization: Bearer ${RC_HTTP_CONTROL_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"instruction": "지금 배터리 얼마나 남았어?", "context": ""}'
   ```

3. 아래 로그로 경로를 확인합니다.

   | 로그 | 의미 |
   |---|---|
   | `Direct skill routing: get_status (params={})` | 사전 라우터가 스킬을 직접 실행했습니다. |
   | `Starting LLM planning (round 1)` | LLM planner(System 2)로 넘어갔습니다. |
   | `System1 router 'laya' unavailable (...) — using rule router` | Laya 장애로 해당 요청을 규칙 라우터가 처리했습니다. |
   | `System1 health check failed (...)` | 기동 시 Laya 서버에 접속하지 못했습니다. |
   | `System1 shadow: {"primary": ..., "shadow": ..., "agree": ...}` | 그림자 판단 기록입니다. |

4. LangSmith를 쓰면 task trace의 metadata에서 `route.kind`, `route.source`(실제 판단한 라우터), `route.fallback`, `route.intent`, `route.confidence`, `route.hint`를 확인합니다.

5. 그림자 로그로 두 라우터의 일치율을 봅니다.

   ```bash
   python3 - <<'EOF'
   import json
   rows = [json.loads(l) for l in open("/tmp/robo_claw_system1_shadow.jsonl", encoding="utf-8")]
   agree = sum(r["agree"] for r in rows)
   print(f"total={len(rows)} agree={agree} ({agree / max(1, len(rows)):.0%})")
   for r in rows:
       if not r["agree"]:
           print(r["instruction"], "| primary:", r["primary"]["kind"], r["primary"]["skill"],
                 "| shadow:", r["shadow"].get("kind"), r["shadow"].get("skill"), r["shadow"].get("error") or "")
   EOF
   ```

## 7. 롤백

| 상황 | 조치 |
|---|---|
| Laya 판단 품질이 기대보다 낮음 | `SYSTEM1_ROUTER=rule`로 바꾸고 에이전트를 재기동합니다. 출력 교정과 가드는 항상 켜져 있으므로 안전망은 그대로입니다. |
| 직접 실행 범위만 줄이고 싶음 | `SYSTEM1_SCOPE=navigation`을 `readonly`로 바꿉니다. |
| Laya 서버 일시 장애 | 조치가 필요 없습니다. timeout이나 오류가 난 요청은 자동으로 규칙 라우터가 처리합니다. 연속 3회 실패하면 30초 동안 Laya 호출을 멈췄다가 다시 시도합니다. |

## 8. 문제 해결

| 증상 | 원인과 조치 |
|---|---|
| 기동 로그가 `router=rule`로 나옴 | 환경변수가 에이전트 프로세스에 전달되지 않았습니다. 4.5절에서 실행 방식별 위치를 확인합니다. `./rclaw launch`는 웹 설정을 저장한 뒤 다시 동기화해야 하고, `./rclaw run`은 셸 `export`가 필요합니다. |
| `SYSTEM1_ROUTER=laya but SYSTEM1_ENDPOINT is empty` | `SYSTEM1_ENDPOINT`를 추가합니다. |
| `Connection refused` (`http://localhost:8000` 등) | 그 주소에서 Laya 서버가 실행 중이 아닙니다. robo-claw 이미지에는 기본적으로 Laya가 없으므로, 엣지 서버(3.1절)를 띄우거나 `INSTALL_LAYA=true` 이미지와 `SYSTEM1_LOCAL_SERVER=true`(3.2절)를 사용합니다. |
| `[system1-local] laya-serve not found` | `INSTALL_LAYA=true`로 빌드하지 않은 이미지입니다. 3.2절 1단계로 다시 빌드합니다. |
| `[system1-local] ... device=cpu` (GPU 기대) | 컨테이너에 GPU가 전달되지 않았거나(`--gpu` 누락, nvidia 런타임 없음) 이미지의 torch가 CPU 빌드입니다. 컨테이너에서 `python3 -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"`로 확인합니다. |
| `System1 health check failed`가 내장 서버에서 남 | 모델 다운로드·로딩이 `SYSTEM1_HEALTH_WAIT_SEC`(기본 120초)보다 오래 걸렸습니다. 첫 기동이면 기다린 뒤 요청을 보내 보고, 반복되면 캐시 볼륨(3.5절)을 마운트하거나 값을 늘립니다. |
| 주행·모션이 Laya 추론 중 끊기거나 느려짐 | `SYSTEM1_LOCAL_THREADS`를 줄이고 `SYSTEM1_LOCAL_NICE`를 높입니다. 해결되지 않으면 엣지 서버(3.1절)로 옮깁니다. |
| `System1 health check failed` 또는 `unavailable (URLError ...)` | 로봇 호스트에서 `curl http://<laya-host>:8000/v1/systemone ...`(3장)으로 접속을 확인합니다. 방화벽과 포트를 점검합니다. |
| `unavailable (TimeoutError ...)`가 자주 남 | `SYSTEM1_TIMEOUT_MS`를 늘리거나 Laya를 GPU에서 실행합니다. |
| 조회 명령이 계속 LLM으로 넘어감 | confidence가 임계값보다 낮습니다. 5장 평가에서 `-v`로 confidence를 보고, `wrong`이 늘지 않는 범위에서 임계값을 낮춥니다. `routing.model`이 multilingual인지도 확인합니다. |
| `skill options exceed SYSTEM1_MAX_OPTIONS` 경고 | 후보 스킬이 너무 많습니다. `SYSTEM1_SKILLS`로 후보를 지정합니다. |
| `navigation` scope인데 이동이 직접 실행되지 않음 | 시맨틱 맵에 기억된 장소가 없거나, 장소가 `SYSTEM1_MAX_OPTIONS`보다 많은데 지시문에 장소 이름이 없는 경우입니다. 이때는 LLM이 처리합니다. |
| "앞으로 2미터"가 예전보다 느림 | `laya` 모드에서는 규칙 지름길이 꺼져 LLM이 처리합니다(1장). 이 명령이 중요하면 `rule` 모드를 유지합니다. |
| `Invalid SYSTEM1_CONF_THRESHOLDS_JSON ignored` | JSON 객체 형식인지 확인합니다. `.env`에서는 따옴표 없이 한 줄로 씁니다. |

## 9. 제약 사항

- 입력은 지시문 텍스트만 씁니다. 이미지나 센서 값은 Laya에 보내지 않습니다.
- Laya는 숫자나 자유 텍스트 인자(거리, 새 장소 이름, 검색어)를 추출하지 못합니다. 인자가 필요한 스킬은 `navigate_to`(기억된 장소)를 제외하고 직접 실행 범위에 넣지 않습니다.
- 복합 명령(`looks_compound`)은 Laya를 호출하지 않고 LLM으로 넘깁니다.
- AI Config Server 웹 설정의 System 1 섹션은 라우터 항목(`SYSTEM1_ROUTER`~`SYSTEM1_API_KEY`)을 제공합니다. Thor 내장 서버 항목(`SYSTEM1_LOCAL_*`, `SYSTEM1_HEALTH_WAIT_SEC`)을 중앙에서 관리하려면 서버에 필드를 추가해야 합니다.
- `./rclaw launch --docker`는 컨테이너에 GPU 옵션을 넘기지 않습니다. Thor GPU를 쓰려면 `scripts/run_robo_claw_docker.sh --gpu` 또는 같은 의미의 compose 설정을 사용합니다.
