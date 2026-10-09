# Laya 서버 Docker 실행 가이드

이 문서는 System 1 Fast Router가 사용하는 Laya 서버를 별도 Docker 컨테이너로 실행하는 절차를 설명합니다. Laya를 robo-claw와 같은 호스트(예: 로봇의 AGX Orin)에서 실행하는 경우와 다른 호스트(엣지 서버, 다른 Jetson 등)에서 실행하는 경우를 모두 다룹니다. 라우터 동작과 전체 환경변수는 [SYSTEM1_FAST_ROUTER.md](SYSTEM1_FAST_ROUTER.md)를 참고합니다.

## 1. 구성

robo-claw는 `SYSTEM1_ENDPOINT`로 HTTP 요청(`POST /v1/systemone`)만 보냅니다. 따라서 Laya 컨테이너를 어느 호스트에 두든 robo-claw 코드는 같고, 설정값만 다릅니다.

| 배치 | Laya 실행 위치 | robo-claw `SYSTEM1_ENDPOINT` | 인증 |
|---|---|---|---|
| 같은 호스트 | 로봇 본체(AGX Orin 등)의 별도 컨테이너 | `http://127.0.0.1:8000` | 필요 없음(`127.0.0.1` 바인딩) |
| 다른 호스트 | 엣지 서버(x86 GPU), 다른 Jetson 등 | `http://<Laya 호스트 IP>:8000` | `LAYA_API_KEY` + `SYSTEM1_API_KEY` 필수 |

| 항목 | 같은 호스트 | 다른 호스트 |
|---|---|---|
| 장점 | 네트워크 지연·단절이 없고 로봇 단독으로 동작합니다. | 로봇의 CPU/GPU를 쓰지 않아 주행·모션 제어에 영향이 없습니다. 여러 로봇이 서버 하나를 공유할 수 있습니다. |
| 주의 | 주행·모션·비전과 CPU/GPU를 나눠 씁니다(자원 제한 필요). | 무선 구간 지연과 단절, 지시문이 네트워크로 전송되는 점(보안)을 고려해야 합니다. |

어느 경우든 Laya가 느리거나 응답하지 않으면 해당 요청은 robo-claw의 규칙 라우터가 처리합니다. 로봇 동작은 멈추지 않습니다.

> robo-claw 컨테이너 안에서 Laya를 실행하는 방식(`INSTALL_LAYA=true` + `SYSTEM1_LOCAL_SERVER=true`)은 Jetson GPU를 쓰지 못합니다. robo-claw 이미지의 베이스(`ros:humble-ros-base`)에 CUDA/cuDNN 라이브러리가 없기 때문이며, 이 방식은 CPU로만 동작합니다. Jetson에서 GPU를 쓰려면 이 문서의 별도 컨테이너 방식을 사용합니다.

## 2. 같은 호스트에서 실행 (AGX Orin)

### 2.1 상태 확인

```bash
uname -m                                                  # aarch64
cat /etc/nv_tegra_release                                 # L4T 버전 (예: "# R36 (release), REVISION: 4.3")
dpkg -l | grep -E 'nvidia-jetpack|nvidia-l4t-core'        # JetPack / L4T 패키지 버전 (예: 36.4.x, 39.2.1-20260806224157)
ls -d /usr/local/cuda-*; nvcc --version                   # CUDA 버전 (nvcc 가 없으면 디렉터리 이름으로 확인)
python3 --version                                         # 호스트 Python (JetPack 6: 3.10, JetPack 7: 3.12)
docker info --format '{{json .Runtimes}}' | grep nvidia   # nvidia 런타임 확인
sudo nvpmodel -q                                          # 전력 모드 (속도 측정은 MAXN 권장)
ss -ltnp | grep ':8000'                                   # 8000 포트가 비어 있는지 확인
```

확인한 L4T 버전으로 2.3절의 torch 인덱스와 베이스 이미지를 고릅니다. L4T R36.x는 JetPack 6, R38 이상(예: 39.x)은 JetPack 7 계열입니다.

### 2.2 1단계: CPU로 동작 확인

현재 소스 그대로 빌드할 수 있습니다.

```bash
cd ~/workspace-OpenEdgeRobotics/A.RobotAI-Robot-RoboClaw
docker build -f docker/laya/Dockerfile -t robo-claw-laya:cpu docker/laya

mkdir -p ~/robo_claw_hf
docker run -d --name laya --network host --restart unless-stopped \
  --cpus 4 --cpu-shares 256 \
  -e LAYA_HOST=127.0.0.1 -e LAYA_THREADS=4 \
  -v ~/robo_claw_hf:/opt/hf \
  robo-claw-laya:cpu
docker logs -f laya
```

| 옵션 | 목적 |
|---|---|
| `--network host` | robo-claw 컨테이너(host 네트워크)가 `127.0.0.1:8000`으로 접속합니다. |
| `--cpus 4 --cpu-shares 256` | 주행·모션 제어 프로세스보다 CPU를 덜 쓰고 우선순위를 낮춥니다. |
| `LAYA_HOST=127.0.0.1` | 로봇 내부에서만 접근할 수 있게 합니다. |
| `-v ~/robo_claw_hf:/opt/hf` | 내려받은 모델을 보존합니다. 첫 기동에는 인터넷이 필요합니다. |

CPU 추론은 요청당 약 1~3초(추정)가 걸립니다. 동작 확인용이며, 운영에는 GPU(2.3절)를 사용합니다.

### 2.3 2단계: GPU로 전환

Jetson GPU를 쓰려면 NVIDIA Jetson용 torch 휠이 필요합니다. 일반 PyPI나 pytorch.org의 aarch64 torch는 Jetson GPU를 쓰지 못합니다. NVIDIA는 Jetson AI Lab pip 인덱스(`https://pypi.jetson-ai-lab.io`)로 안내하며, 예전 주소(`pypi.jetson-ai-lab.dev`)는 더 이상 사용할 수 없습니다.

#### 버전별 인덱스와 베이스 이미지

| 호스트 L4T | JetPack | CUDA | Python | `LAYA_TORCH_INDEX_URL` | `BASE_IMAGE` |
|---|---|---|---|---|---|
| R36.4.x | 6.1 / 6.2 | 12.6 | 3.10 | `https://pypi.jetson-ai-lab.io/jp6/cu126` | `nvcr.io/nvidia/l4t-jetpack:<호스트와 같은 r36.4 태그>` |
| R36.4.x + CUDA 12.8/12.9 설치 | 6.x | 12.8 / 12.9 | 3.10 | `.../jp6/cu128`, `.../jp6/cu129` | CUDA가 그 버전인 이미지 |
| R36.3.x | 6.0 | 12.2 | 3.10 | 해당 인덱스 없음 → JetPack 6.2로 업그레이드 권장 | — |
| R38 이상 (예: 39.x) | 7.x (SBSA) | 13.x | 3.12 | `https://pypi.jetson-ai-lab.io/sbsa/cu130` | CUDA 13 + cuDNN이 포함된 Ubuntu 24.04 arm64 이미지 |

- 인덱스별 Python 태그가 다릅니다. `jp6/*`는 `cp310`, `sbsa/cu130`은 `cp312` 휠만 있습니다. 베이스 이미지의 Python이 이와 다르면 `is not a supported wheel on this platform`으로 실패합니다.
- 작성 시점 기준으로 `jp6/cu126`에는 torch 2.8.0, 2.9.1, 2.10.0, 2.11.0이 있고, `sbsa/cu130`에는 2.9.0, 2.9.1, 2.10.0, 2.11.0이 있습니다. NVIDIA 포럼에서 JetPack 6.2와 함께 가장 많이 검증된 버전은 2.8.0이므로 `LAYA_TORCH_SPEC=torch==2.8.0`으로 고정하는 것을 권장합니다.
- 베이스 이미지 태그는 NGC 카탈로그에서 호스트의 L4T/CUDA와 같은 것을 고릅니다. Jetson torch 휠은 컨테이너 안의 CUDA 라이브러리를 쓰므로, 라이브러리가 없는 이미지(`python:slim` 등)에서는 오류 없이 CPU로만 동작합니다.
- 인덱스에 원하는 버전이 있는지 미리 확인할 수 있습니다.

  ```bash
  pip index versions torch --index-url https://pypi.jetson-ai-lab.io/jp6/cu126
  # 또는 브라우저로 https://pypi.jetson-ai-lab.io/jp6/cu126/torch/
  ```

#### 빌드와 실행

JetPack 6 (AGX Orin, L4T R36.4.x):

```bash
cd ~/workspace-OpenEdgeRobotics/A.RobotAI-Robot-RoboClaw
docker build -f docker/laya/Dockerfile -t robo-claw-laya:jp6 \
  --build-arg BASE_IMAGE=nvcr.io/nvidia/l4t-jetpack:<호스트 L4T와 같은 r36.4 태그> \
  --build-arg LAYA_TORCH_INDEX_URL=https://pypi.jetson-ai-lab.io/jp6/cu126 \
  --build-arg LAYA_TORCH_SPEC=torch==2.8.0 \
  docker/laya
```

JetPack 7 (L4T R38 이상):

```bash
docker build -f docker/laya/Dockerfile -t robo-claw-laya:jp7 \
  --build-arg BASE_IMAGE=<CUDA 13 + cuDNN, Ubuntu 24.04, arm64 이미지> \
  --build-arg LAYA_TORCH_INDEX_URL=https://pypi.jetson-ai-lab.io/sbsa/cu130 \
  docker/laya
```

- `docker/laya/Dockerfile`은 베이스 이미지에 `python3`이나 `pip`이 없으면 apt로 설치합니다(`l4t-jetpack`, `nvidia/cuda` 이미지 대응).
- 설치 스크립트는 torch를 지정한 인덱스에서만(`--index-url`) 받습니다. `--extra-index-url`로 PyPI와 섞으면 CPU용 torch가 설치될 수 있습니다.

실행합니다(JetPack 7 이미지면 태그만 `jp7`로 바꿉니다).

```bash
docker rm -f laya
docker run -d --name laya --runtime nvidia --network host --restart unless-stopped \
  -e LAYA_HOST=127.0.0.1 -e LAYA_DEVICE=cuda -e LAYA_THREADS=2 \
  -v ~/robo_claw_hf:/opt/hf \
  robo-claw-laya:jp6
```

#### GPU 동작 확인

```bash
# 이미지 단독 확인 — 기대 출력 예: 2.8.0 12.6 True
docker run --rm --runtime nvidia robo-claw-laya:jp6 \
  python3 -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"

# 실행 중인 서버 확인 — 체크포인트가 cuda 에서 계산되는지
curl -s http://127.0.0.1:8000/health
```

`False`가 나오면 다음 순서로 확인합니다.

1. `docker run`에 `--runtime nvidia`가 있는지 확인합니다.
2. 베이스 이미지의 L4T/CUDA가 호스트와 같은지 확인합니다.
3. 인덱스가 호스트 CUDA와 맞는지 확인합니다(JetPack 6.2는 `cu126`).

GPU를 쓰지 못하면 Laya는 오류 없이 CPU로 실행합니다(`LAYA_DEVICE`는 선호값). 응답이 느리면 위 명령부터 확인합니다.

### 2.4 robo-claw 설정

AI Config Server 웹 설정의 System 1 Fast Router 섹션(또는 4.5절의 실행 방식별 위치)에 입력합니다.

| 항목 | 값 |
|---|---|
| System 1 router (`SYSTEM1_ROUTER`) | `laya`. 처음에는 `rule` + Shadow 켬으로 비교만 해도 됩니다. |
| System 1 endpoint (`SYSTEM1_ENDPOINT`) | `http://127.0.0.1:8000` |
| Timeout ms (`SYSTEM1_TIMEOUT_MS`) | GPU는 `800`으로 시작해 실측 p95 × 1.5로 조정합니다. CPU 확인 중이면 `3000`입니다. |
| Shadow mode (`SYSTEM1_SHADOW`) | 켬 |

`SYSTEM1_LOCAL_SERVER`는 켜지 않습니다. 이 값은 robo-claw 컨테이너 안에서 Laya를 실행하는 방식에만 씁니다.

## 3. 다른 호스트에서 실행 (엣지 서버 등)

### 3.1 Laya 호스트 준비

| Laya 호스트 | 이미지 빌드 |
|---|---|
| x86 + NVIDIA GPU | `docker/laya/compose.edge.yaml` 사용(기본 CUDA 인덱스 `cu128`, 드라이버에 맞게 `LAYA_TORCH_INDEX_URL` 변경) |
| 다른 Jetson(AGX Orin 등) | 2.3절과 같이 `l4t-jetpack` 베이스 + Jetson torch 인덱스로 빌드 |
| GPU 없는 서버 | 2.2절의 CPU 이미지(느림, 확인용) |

호스트에 NVIDIA 드라이버와 NVIDIA Container Toolkit(Jetson은 JetPack의 nvidia 런타임)이 있어야 GPU를 씁니다.

### 3.2 실행 (외부 접속 허용 + 인증)

x86 GPU 엣지 서버 예시입니다.

```bash
cd docker/laya
export LAYA_API_KEY=$(openssl rand -hex 24)        # robo-claw 의 SYSTEM1_API_KEY 에 같은 값을 넣습니다
docker compose -f compose.edge.yaml up -d --build
docker compose -f compose.edge.yaml logs -f laya
```

Jetson을 Laya 호스트로 쓸 때는 2.3절의 `docker run`에서 바인딩과 인증만 바꿉니다.

```bash
docker run -d --name laya --runtime nvidia --network host --restart unless-stopped \
  -e LAYA_HOST=0.0.0.0 -e LAYA_API_KEY=<토큰> -e LAYA_DEVICE=cuda \
  -v ~/robo_claw_hf:/opt/hf \
  robo-claw-laya:jp6
```

| 항목 | 같은 호스트와의 차이 |
|---|---|
| `LAYA_HOST` | `0.0.0.0`(외부 접속 허용). compose 예시는 이미지 기본값이 `0.0.0.0`입니다. |
| `LAYA_API_KEY` | **반드시 지정합니다.** `laya-serve`가 `Authorization: Bearer <토큰>`을 요구합니다. |
| 방화벽 | 8000 포트를 로봇이 있는 네트워크 대역에만 엽니다(예: `sudo ufw allow from 192.168.50.0/24 to any port 8000 proto tcp`). |
| CPU 제한 | 전용 서버면 `--cpus`, `--cpu-shares`는 필요 없습니다. |

### 3.3 robo-claw 설정

| 항목 | 값 |
|---|---|
| System 1 router (`SYSTEM1_ROUTER`) | `laya` |
| System 1 endpoint (`SYSTEM1_ENDPOINT`) | `http://<Laya 호스트 IP>:8000` |
| System 1 API Key (`SYSTEM1_API_KEY`) | Laya 호스트의 `LAYA_API_KEY`와 같은 값 |
| Timeout ms (`SYSTEM1_TIMEOUT_MS`) | 로봇에서 측정한 p95(네트워크 왕복 포함) × 1.5. 무선 구간이면 여유를 더 둡니다. |
| Shadow mode (`SYSTEM1_SHADOW`) | 켬(전환 초기) |

### 3.4 네트워크 고려 사항

| 항목 | 설명 |
|---|---|
| 지연 | 측정값에 네트워크 왕복 시간이 더해집니다. 주행 중 무선 로밍이나 음영 구간에서 지연이 튀면 timeout 후 규칙 라우터가 처리합니다. |
| 단절 | 연속 3회 실패하면 robo-claw가 30초 동안 Laya 호출을 멈추고 규칙 라우터로 동작합니다. 그 뒤 자동으로 다시 시도합니다. |
| 보안 | `laya-serve`는 평문 HTTP입니다. 사용자 지시문이 네트워크로 전송되므로 신뢰된 사내망·VPN에서만 쓰거나, 앞단에 TLS reverse proxy를 두고 `SYSTEM1_ENDPOINT`를 `https://...`로 지정합니다(`LAYA_ROOT_PATH`로 경로 접두사 지원). |
| 여러 로봇 공유 | 서버 하나에 여러 로봇이 붙을 수 있습니다. 동시 처리 상한은 `LAYA_MAX_CONCURRENT`(기본 16)입니다. 로봇이 많으면 p95를 다시 측정합니다. |

### 3.5 로봇에서 연결 확인

```bash
curl -s -H "Authorization: Bearer <토큰>" http://<Laya 호스트 IP>:8000/health
docker exec <robo-claw 컨테이너> curl -s -H "Authorization: Bearer <토큰>" http://<Laya 호스트 IP>:8000/health
```

robo-claw 컨테이너 안에서도 응답해야 합니다. 토큰이 틀리면 401이 반환되며, robo-claw 로그에는 `System1 router 'laya' unavailable (HTTPError ...)`로 나타납니다.

## 4. 공통: 동작 확인과 속도 측정

1. 요청 형식을 확인합니다(다른 호스트면 주소와 인증 헤더를 바꿉니다).

  ```bash
  curl -s http://127.0.0.1:8000/v1/systemone -H 'Content-Type: application/json' \
    -d '{"state":{"instruction":"지금 배터리 얼마 남았어?"},"questions":{"intent":{"type":"choice","instructions":"말의 종류?","criteria":{"smalltalk":"인사","single_skill":"기능 하나","multi_step":"여러 단계"}}}}'
  ```
  ```bash
  curl -s -H "Authorization: Bearer <System 1 Token | Laya_Token>" http://192.168.50.212:8000/v1/  systemone -H 'Content-Type: application/json' \
    -d '{"state":{"instruction":"지금 배터리 얼마 남았어?"},"questions":{"intent":{"type":"choice","instructions":"말의 종류?","criteria":{"smalltalk":"인사","single_skill":"기능 하나","multi_step":"여러 단계"}}}}'
  ```
   정상 응답에는 `answers.intent.choice`, `answers.intent.confidence`, `routing.model`(multilingual)이 들어 있습니다.

2. robo-claw를 다시 기동하고 로그를 확인합니다.

   ```text
   System1 router: router=laya shadow=True scope=readonly endpoint=http://... local_server=False
   ```

   이 줄 다음에 `System1 health check failed`가 없어야 합니다.

3. 로봇에서 지연을 측정합니다. 다른 호스트면 네트워크 왕복이 포함된 값이 나옵니다.

   ```bash
   # 인증을 켠 서버는 --env-file(LAYA_API_KEY/SYSTEM1_API_KEY) 또는 환경변수로 키를 넘깁니다. 키는 출력되지 않습니다.
   python3 scripts/system1_eval.py --router laya --endpoint http://<주소>:8000 --env-file .env --timeout-ms 5000
   ```

   `latency p50/p95`를 보고 `SYSTEM1_TIMEOUT_MS`를 p95 × 1.5로 정합니다. 같은 호스트 배치는 주행·모션 중에도 측정합니다.

### 4.1 Laya 단독 검증 (라이브 테스트)

robo-claw를 띄우지 않고 Laya 서버만 검증합니다. ROS 2가 필요 없고 `pytest`만 있으면 됩니다.

```bash
./validation/system1/run_laya_tests.sh                                   # 기본 http://192.168.50.212:8000, 저장소 .env 의 LAYA_API_KEY
LAYA_ENDPOINT=http://127.0.0.1:8000 ./validation/system1/run_laya_tests.sh
./validation/system1/run_laya_tests.sh -k latency                        # 일부만 실행 (pytest 인자 전달)
```

스크립트는 세 단계를 실행하고 결과 JSON을 `${TMPDIR:-/tmp}/laya_reports/<시각>/`에 저장합니다.

| 단계 | 내용 |
|---|---|
| 1. `validation/system1/test_laya_live.py` | 연결(`/health`), 인증(키 없음·틀린 키 → 401), 응답 스키마(choice/noul), 한국어 → multilingual 체크포인트, robo-claw 질문 세트 응답, 선택지 상한(101개 → 413), warm 지연 p95, 질문 단위 정확도(`laya_cases.jsonl`), 라우터 오실행률(`router_cases.jsonl`) |
| 1. `validation/system1/test_laya_tools_live.py` | robo-claw 전체 스킬 기준 툴·복합 명령 평가(아래 "툴 단위·복합 명령 평가") |
| 2. `scripts/system1_eval.py --router both` | 시드 케이스로 규칙 라우터와 Laya를 비교합니다. |
| 3. `scripts/system1_tool_eval.py --router both` | 툴·복합 명령 케이스로 규칙 라우터와 Laya를 비교합니다(`LAYA_SCOPE`로 scope 지정). |

#### 툴 단위·복합 명령 평가

robo-claw의 스킬 정의에서 추출한 카탈로그(`validation/system1/skill_catalog.json`)를 기준으로 평가합니다. 카탈로그는 `python3 scripts/system1_skill_catalog.py`로 갱신합니다. 스킬을 추가하면 단위 테스트(`test_system1_tool_cases.py`)가 카탈로그 갱신과 테스트 케이스 추가를 요구합니다.

| 데이터 | 내용 |
|---|---|
| `tool_cases.jsonl` | 모든 공개 스킬(95개)마다 1개 이상의 자연어 명령, 인사·질문 케이스. 필요한 인자는 `params`에 적습니다. |
| `compound_cases.jsonl` | 여러 스킬이 필요한 명령 20건과 기대 스킬 순서(`steps`) |

| 평가 | 판정 |
|---|---|
| 라우터 결정 | robo-claw와 같은 코드로 경로(direct_skill / simple_reply / llm)를 판정합니다. 기대 경로는 카탈로그에서 자동으로 정합니다. read이면서 필수 인자가 없는 스킬은 direct_skill, 기억된 장소로의 `navigate_to`는 navigation scope에서만 direct_skill, 그 외는 llm입니다. 맞는 스킬을 맞는 인자로 직접 실행한 경우(규칙 라우터의 지름길)는 `direct_ok`, 다른 스킬·다른 인자로 실행하면 `wrong`입니다. |
| 복합 명령 안전 | 복합 명령이 직접 실행되면(`compound_unsafe_direct` > 0) 기준과 무관하게 실패입니다. |
| 툴 식별 | Laya가 카테고리(14개) → 그 카테고리의 스킬 순서로 맞히는지 봅니다. 직접 실행 범위와 무관한 판단 능력 지표이며, 범위를 넓힐 수 있는지 판단하는 근거입니다. |
| 복합 명령 인식 | intent가 multi_step인지와 robo-claw 복합 명령 가드(`looks_compound`)가 잡는지를 함께 봅니다. |

```bash
python3 scripts/system1_tool_eval.py --router rule                                     # 규칙 라우터만 (서버 불필요)
python3 scripts/system1_tool_eval.py --endpoint http://192.168.50.212:8000 --env-file .env -v
python3 scripts/system1_tool_eval.py --endpoint ... --env-file .env --scope navigation --skip-tools
```

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `LAYA_ENDPOINT` | `http://192.168.50.212:8000`(스크립트) | 대상 서버 |
| `LAYA_ENV_FILE` | 저장소 루트 `.env` | `LAYA_API_KEY`/`SYSTEM1_API_KEY`를 읽을 파일 |
| `LAYA_TIMEOUT_MS` | `5000` | 요청당 대기 시간 |
| `LAYA_LATENCY_BUDGET_MS` | `1000` | warm p95 상한 |
| `LAYA_MAX_FALSE_ACT` | `0.10` | 라우터 오실행률 상한 |
| `LAYA_MIN_INTENT_ACC`, `LAYA_MIN_SKILL_ACC`, `LAYA_MIN_AMBIGUOUS_ACC` | (없음) | 지정하면 질문 단위 정확도 하한을 검사하고, 없으면 보고만 합니다. |
| `LAYA_SCOPE` | `readonly` | 툴 평가의 라우터 scope(`readonly` / `navigation`) |
| `LAYA_MIN_CATEGORY_ACC`, `LAYA_MIN_TOOL_ACC`, `LAYA_MIN_MULTI_STEP_ACC` | (없음) | 지정하면 툴 식별·복합 명령 인식 하한을 검사하고, 없으면 보고만 합니다. |
| `PYTHON` | `python3` | pytest가 설치된 Python |

- 질문 단위 정확도는 라우터 임계값과 무관한 Laya 자체 판단의 정확도입니다. intent는 smalltalk/question/single_skill/multi_step, skill은 인자 없는 read 스킬, ambiguous는 noul ≥ 0.5 기준으로 봅니다. 케이스는 `validation/system1/laya_cases.jsonl`에 추가합니다.
- 라우터 오실행률은 robo-claw와 같은 판정(임계값, 복합 명령 가드 포함)으로 계산합니다. 운영 전환의 안전 기준이므로 0에 가깝게 유지합니다.

## 5. 예상 지연 (AGX Orin, 참고)

아래는 모델 카드의 T4 측정값(질문 1개 33~40ms, 10개 배치 72~158ms)과 하드웨어 사양으로 추정한 값입니다. 실측이 아니므로 운영값은 4장의 측정으로 정합니다.

| 조건 | robo-claw 요청 1건(질문 3~4개, HTTP 포함) |
|---|---|
| GPU, MAXN + `jetson_clocks` | 약 80~200ms |
| GPU, 30W 모드 | 약 150~300ms |
| 첫 요청(모델 로드 직후) | 수 초(CUDA 초기화) |
| CPU, 스레드 4 | 약 0.5~3초 |

## 6. 문제 해결

| 증상 | 원인과 조치 |
|---|---|
| robo-claw 로그 `Connection refused` | 그 주소에서 Laya가 실행 중이 아닙니다. `docker ps`, `docker logs laya`, `ss -ltnp \| grep 8000`을 확인합니다. 다른 호스트면 `LAYA_HOST=0.0.0.0`인지, 방화벽이 열렸는지 확인합니다. |
| `HTTPError 401` | `SYSTEM1_API_KEY`와 `LAYA_API_KEY`가 다릅니다. |
| `TimeoutError`가 자주 남 | `SYSTEM1_TIMEOUT_MS`가 실측 p95보다 작습니다. GPU 사용 여부(2.3절)와 네트워크 지연을 확인합니다. |
| `torch.cuda.is_available()`가 `False` | `--runtime nvidia` 누락, 베이스 이미지와 호스트 L4T/CUDA 불일치, CUDA 라이브러리가 없는 베이스 이미지, 또는 Jetson용이 아닌 torch가 설치된 경우입니다(2.3절). |
| 빌드 중 `is not a supported wheel on this platform` | 베이스 이미지의 Python이 인덱스의 휠과 다릅니다. `jp6/*`는 Python 3.10(Ubuntu 22.04), `sbsa/cu130`은 Python 3.12(Ubuntu 24.04) 베이스가 필요합니다. |
| 빌드 중 `No matching distribution found for torch` | 인덱스 주소가 틀렸거나(예전 `.dev` 주소, 없는 `cu122`) 고정한 버전이 그 인덱스에 없습니다. `pip index versions torch --index-url <인덱스>`로 확인합니다. |
| 첫 기동이 오래 걸리거나 실패 | 모델 다운로드에 인터넷이 필요합니다. 폐쇄망이면 인터넷이 되는 곳에서 `~/robo_claw_hf`를 채워 옮깁니다. |
| 주행·모션이 Laya 추론 중 느려짐 | 같은 호스트 배치에서 `--cpus`를 줄이거나 다른 호스트(3장)로 옮깁니다. |

## 7. 참고

- [Jetson AI Lab pip 인덱스](https://pypi.jetson-ai-lab.io/) — `jp6/cu126`, `jp6/cu128`, `jp6/cu129`, `sbsa/cu130`
- [NVIDIA Developer Forums: Pypi.jetson-ai-lab.dev is Down — PyTorch for JetPack 6.2](https://forums.developer.nvidia.com/t/pypi-jetson-ai-lab-dev-is-down-pytorch-torchvision-for-jetpack6-21/340586)
- [NVIDIA Developer Forums: Install PyTorch for CUDA 12.6 JetPack 6.2](https://forums.developer.nvidia.com/t/install-pytorch-for-cuda-12-6-jetpack-6-2/348456)
- [NVIDIA Developer Forums: PyTorch 2.8 wheel for JetPack 6.2](https://forums.developer.nvidia.com/t/pytorch-2-8-wheel-for-jetpack-6-2/341339)
- [Torch-TensorRT in JetPack](https://docs.pytorch.org/TensorRT/getting_started/jetpack.html)
- [NVIDIA Docs: Installing PyTorch for Jetson Platform](https://docs.nvidia.com/deeplearning/frameworks/install-pytorch-jetson-platform/index.html)
- [jetson-containers](https://github.com/dusty-nv/jetson-containers)
- [Laya 모델 카드](https://huggingface.co/convaiinnovations/laya)
