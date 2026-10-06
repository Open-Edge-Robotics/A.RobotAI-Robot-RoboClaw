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
cat /etc/nv_tegra_release                                 # L4T 버전 (예: R36 ... REVISION 4.x → JetPack 6.x)
dpkg -l | grep -E 'nvidia-jetpack|nvidia-l4t-core'        # 예: 39.2.1-20260806224157 
docker info --format '{{json .Runtimes}}' | grep nvidia   # nvidia 런타임 확인
sudo nvpmodel -q                                          # 전력 모드 (속도 측정은 MAXN 권장)
ss -ltnp | grep ':8000'                                   # 8000 포트가 비어 있는지 확인
```

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

```bash
docker build -f docker/laya/Dockerfile -t robo-claw-laya:jp6 \
  --build-arg BASE_IMAGE=nvcr.io/nvidia/l4t-jetpack:<호스트 L4T와 같은 태그, 예: r36.x.x> \
  --build-arg LAYA_TORCH_INDEX_URL=<JetPack 6용 Jetson torch 인덱스> \
  docker/laya

docker rm -f laya
docker run -d --name laya --runtime nvidia --network host --restart unless-stopped \
  -e LAYA_HOST=127.0.0.1 -e LAYA_DEVICE=cuda -e LAYA_THREADS=2 \
  -v ~/robo_claw_hf:/opt/hf \
  robo-claw-laya:jp6
```

- 베이스 이미지는 호스트의 L4T 버전과 맞춰야 CUDA/cuDNN이 맞습니다.
- torch는 NVIDIA Jetson용(JetPack 6, Python 3.10) 휠이 필요합니다. PyPI나 pytorch.org의 기본 aarch64 torch는 Jetson GPU를 쓰지 못할 수 있습니다. 인덱스 주소는 JetPack 버전에 맞게 NVIDIA 안내에서 확인합니다.
- `l4t-jetpack` 이미지에는 `pip`이 없을 수 있습니다. 현재 `docker/laya/Dockerfile`은 `python3 -m pip`이 있다고 가정하므로, 빌드가 `No module named pip`로 실패하면 Dockerfile에 `python3-pip` 설치 단계를 추가해야 합니다.

GPU 동작을 확인합니다.

```bash
docker exec laya python3 -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"   # True 여야 함
curl -s http://127.0.0.1:8000/health                                                                # 장치가 cuda 인지 확인
```

GPU를 쓰지 못하면 Laya는 오류 없이 CPU로 실행합니다(`LAYA_DEVICE`는 선호값). 응답이 느리면 위 두 명령부터 확인합니다.

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

   정상 응답에는 `answers.intent.choice`, `answers.intent.confidence`, `routing.model`(multilingual)이 들어 있습니다.

2. robo-claw를 다시 기동하고 로그를 확인합니다.

   ```text
   System1 router: router=laya shadow=True scope=readonly endpoint=http://... local_server=False
   ```

   이 줄 다음에 `System1 health check failed`가 없어야 합니다.

3. 로봇에서 지연을 측정합니다. 다른 호스트면 네트워크 왕복이 포함된 값이 나옵니다.

   ```bash
   python3 scripts/system1_eval.py --router laya --endpoint http://<주소>:8000 --timeout-ms 5000
   ```

   `latency p50/p95`를 보고 `SYSTEM1_TIMEOUT_MS`를 p95 × 1.5로 정합니다. 같은 호스트 배치는 주행·모션 중에도 측정합니다. 평가 스크립트는 현재 `SYSTEM1_API_KEY`를 보내지 않으므로, 인증을 켠 서버는 측정하는 동안 인증 없이 띄우거나 로봇의 robo-claw 로그(그림자 기록의 `latency_ms`)로 확인합니다.

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
| `torch.cuda.is_available()`가 `False` | `--runtime nvidia` 누락, 베이스 이미지와 호스트 L4T 불일치, 또는 Jetson용이 아닌 torch가 설치된 경우입니다. |
| 빌드 중 `No module named pip` | `l4t-jetpack` 베이스에 pip가 없습니다. Dockerfile에 `python3-pip` 설치 단계를 추가합니다. |
| 첫 기동이 오래 걸리거나 실패 | 모델 다운로드에 인터넷이 필요합니다. 폐쇄망이면 인터넷이 되는 곳에서 `~/robo_claw_hf`를 채워 옮깁니다. |
| 주행·모션이 Laya 추론 중 느려짐 | 같은 호스트 배치에서 `--cpus`를 줄이거나 다른 호스트(3장)로 옮깁니다. |
