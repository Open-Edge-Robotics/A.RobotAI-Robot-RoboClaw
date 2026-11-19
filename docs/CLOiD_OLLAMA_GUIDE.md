# CLOiD 로컬 Ollama API 서버 구동 및 RoboClaw 연동 가이드

이 문서는 CLOiD 로봇(NVIDIA Jetson AGX Thor) 호스트에서 Docker로 로컬 Ollama API 서버를 구동하고 `cloi-robo-claw` 컨테이너와 연동하는 방법, 그리고 디스크 용량 관리 절차를 정리합니다.

---

## 1. 환경 요약 및 연동 구조

실제 `cloid1`/`cloid2` 호스트 점검 기준으로 확인된 환경은 다음과 같습니다.

- **하드웨어 / OS**: NVIDIA Jetson AGX Thor T5000 (`linux/arm64`, L4T R38.4.0, `PREEMPT_RT` 커널)
- **통합 메모리**: 총 `122 GiB` (상시 로봇 컨테이너 구동 상태에서 약 `105 GiB` 가용)
- **Docker 환경**:
  - Docker `v29.2.1`, `docker compose` 설치됨
  - `/etc/docker/daemon.json`에 `nvidia` 런타임(`nvidia-container-runtime`) 등록됨 (`Default Runtime: runc`)
  - `data-root`: `/home/cloi/ws/docker` (`/home/cloi/ws` 196G 파티션 사용, 루트 `/`는 6.5G로 소용량)
- **RoboClaw 호환성**:
  - `cloi-robo-claw` 컨테이너가 `network_mode: host`로 실행되므로 호스트의 `127.0.0.1:11434`에 별도 브리지 설정 없이 **`http://localhost:11434`**로 직접 접근 가능
  - `robo_claw_agent`에 `OllamaBridge`(`robo_claw_agent/llm_bridge/ollama.py`)와 Python `ollama==0.6.3` 패키지가 이미 포함되어 있어 이미지 재빌드 불필요

---

## 2. Ollama Docker 컨테이너 배포

로봇 호스트(`cloid1`, `cloid2`)에서 아래 명령으로 Ollama 컨테이너를 실행합니다.

```bash
docker run -d \
  --name ollama \
  --restart unless-stopped \
  --runtime=nvidia \
  --gpus all \
  -p 127.0.0.1:11434:11434 \
  -v ollama_data:/root/.ollama \
  -e OLLAMA_HOST=0.0.0.0:11434 \
  -e OLLAMA_KEEP_ALIVE=5m \
  -e OLLAMA_MAX_LOADED_MODELS=2 \
  -e OLLAMA_NUM_PARALLEL=2 \
  ollama/ollama:latest
```

### 주요 옵션 설명

- **`-p 127.0.0.1:11434:11434`**: Ollama는 자체 API Key 인증이 없으므로 외부 LAN 노출을 막고 호스트/`cloi-robo-claw`(host network)에서만 접근하도록 루프백 바인딩
- **`-v ollama_data:/root/.ollama`**: Docker named volume 사용 시 호스트의 `/home/cloi/ws/docker/volumes/ollama_data/_data`(`/home/cloi/ws` 파티션)에 모델이 영구 저장됨
  - **주의**: 루트(`/`) 파티션은 여유 공간이 3~3.5G뿐이므로 루트 경로에 모델을 바인드 마운트하지 마세요.
- **`--restart unless-stopped`**: 호스트 `docker.service`가 `enabled` 상태이므로 로봇 전원을 껐다 켜도 `ollama` 컨테이너가 자동 재실행됨 (수동 `docker stop ollama` 제외)
- **`OLLAMA_MAX_LOADED_MODELS=2`**: 채팅 모델과 RAG 임베딩 모델(`nomic-embed-text`)을 동시에 메모리에 유지하기 위한 설정

---

## 3. 기동 및 연동 검증

### 3.1 컨테이너 상태, 볼륨, GPU 인식 확인 (호스트)

```bash
# 컨테이너 상태 및 포트 확인
docker ps --filter name=^/ollama$

# 볼륨 마운트 및 재시작 정책 확인
docker inspect --format 'RestartPolicy={{.HostConfig.RestartPolicy.Name}} Mount={{range .Mounts}}{{.Type}}:{{.Name}}({{.Source}}->{{.Destination}}){{end}}' ollama

# NVIDIA Thor CUDA 인식 확인 (library=CUDA, compute=11.0, cuda_v13 출력 확인)
docker logs ollama 2>&1 | grep -E "Listening|inference compute|default_num_ctx"
```

정상 기동 시 로그에 다음과 같이 NVIDIA Thor GPU가 인식됩니다.

```text
msg="inference compute" id=0 filter_id=0 library=CUDA compute=11.0 name=CUDA0 description="NVIDIA Thor" libdirs=ollama,cuda_v13 driver=13.0 type=iGPU total="122.8 GiB" available="105.0 GiB"
```

### 3.2 `cloi-robo-claw` 컨테이너 내부 통신 확인

```bash
docker exec cloi-robo-claw python3 -c '
import urllib.request, ollama
print("HTTP / :", urllib.request.urlopen("http://localhost:11434/", timeout=5).read().decode().strip())
print("HTTP /api/tags :", urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5).read().decode().strip())
c = ollama.Client(host="http://localhost:11434", timeout=5)
print("ollama.Client.list() :", c.list())
'
```

### 3.3 모델 다운로드 및 확인

```bash
# Tool Calling 지원 모델 다운로드 예시
docker exec -it ollama ollama pull qwen2.5:14b

# RAG 임베딩도 로컬 사용 시
docker exec -it ollama ollama pull nomic-embed-text

# 다운로드된 모델 및 현재 GPU 적재 모델 확인
docker exec ollama ollama list
docker exec ollama ollama ps
```

---

## 4. RoboClaw 설정 및 운영 주의사항

AI Config Server 프로필(또는 런치 오버라이드)에서 다음 파라미터를 설정합니다.

| 파라미터 | 설정 값 예시 | 비고 |
| :--- | :--- | :--- |
| `llm_provider` | `ollama` | `OllamaBridge` 사용 |
| `llm_base_url` | `http://localhost:11434` | `OllamaBridge` 기본값과 동일 |
| `llm_model` | `qwen2.5:14b` 등 | **Tool Calling 지원 모델** 선택 필요 |
| `llm_embedding_base_url` | `http://localhost:11434` | RAG 로컬 임베딩 사용 시 (`nomic-embed-text`) |
| `ollama_options_json` | `{"num_ctx": 8192}` | 컨텍스트 상한 지정 권장 |

### 운영 시 주의사항

1. **컨텍스트 크기(`num_ctx`) 제한 권장**
   - Thor의 통합 메모리(`122.8 GiB`)를 기준으로 Ollama가 `default_num_ctx=262144`를 자동 선택합니다.
   - `cloi-cuda-lidar-filter` 등 로봇 제어·인식 컨테이너와 GPU/메모리를 공유하므로, KV 캐시 과점유를 막기 위해 `ollama_options_json`에 `{"num_ctx": 8192}`(또는 `16384`)를 지정하거나 컨테이너 환경변수 `OLLAMA_CONTEXT_LENGTH`로 상한을 두세요.
2. **디버그 덤프 용량 확인**
   - `robo_claw_agent/llm_bridge/ollama.py`에 `_DEBUG_DUMP_ENABLED = True`(`chat_history/` JSON 저장)가 활성화되어 있으므로 장기 운용 시 디스크 증가 여부를 확인하세요.

---

## 5. 디스크 용량 관리 및 트러블슈팅

모델 다운로드 전 `df -h /home/cloi/ws`로 여유 공간을 확인합니다. 용량이 부족할 경우 아래 순서로 점검·정리합니다.

### 5.1 Docker 미사용 이미지 및 빌드 캐시 정리 (`cloi` CLI)

```bash
# 빌드 캐시 정리
cloi prune builder --dry-run
cloi prune builder --yes

# 미사용 로컬 CLOi 이미지 확인 및 정리
cloi prune image list
cloi prune image previous          # 이전 릴리스 후보 조회 (--yes로 삭제)

# 일괄 정리 스크립트 (실행 중/compose 참조 이미지 자동 보호)
/home/cloi/ws/cloi_entropos/scripts/image_prune.sh --dry-run
/home/cloi/ws/cloi_entropos/scripts/image_prune.sh --force
```

### 5.2 컨테이너 Writable Layer 비대화 정리 (`cloi-secure-sys-bridge`, `cloi-robot-hub`)

- **증상**: `docker ps -s` 확인 시 `cloi-secure-sys-bridge`, `cloi-robot-hub` 컨테이너의 쓰기 레이어가 각각 수십 GB(실제 `cloid1` 합계 `94.1 GB`, `cloid2` 합계 `52.2 GB`)까지 커짐.
- **원인**: 두 컨테이너는 Bazel `py_binary` 기반 distroless 이미지로, 재부팅/재시작마다 `/tmp/Bazel.runfiles_<random>`(~500MB)을 압축 해제하며 기존 컨테이너의 `/tmp`가 정리되지 않고 누적됨.
- **조치 방법**: 두 컨테이너의 설정·로그는 호스트 마운트에 유지되므로, `cloi down` → `cloi up`으로 두 서비스만 재생성하면 누적된 임시 레이어를 안전하게 회수할 수 있습니다.

```bash
# 현재 컨테이너별 레이어 크기 확인
docker ps -s --format "{{.Size}}\t{{.Names}}" | sort -hr | head -n 10

# 두 서비스만 안전하게 중지·삭제 후 재생성
cloi down secure-sys-bridge robot-hub -y
cloi up secure-sys-bridge robot-hub

# 회수된 용량 및 컨테이너 상태 확인
df -h /home/cloi/ws
docker ps -s --filter name=^/cloi-secure-sys-bridge$ --filter name=^/cloi-robot-hub$
```

> **재발 방지 참고**: `cloi_entropos`의 compose 정의에서 `secure-sys-bridge`와 `robot-hub` 서비스에 `tmpfs: [/tmp]`를 추가하면 재부팅 시 `/tmp/Bazel.runfiles_*`가 디스크에 영구 누적되는 현상을 방지할 수 있습니다.

### 5.3 호스트 로그 용량 확인

`cloi` CLI에는 로그 삭제 전용 명령이 없으므로, `/home/cloi/ws/logs` 사용량이 클 경우 아래 명령으로 디렉터리별 용량을 확인한 뒤 오래된 로그를 정리합니다.

```bash
du -sh /home/cloi/ws/logs/* 2>/dev/null | sort -h
```
