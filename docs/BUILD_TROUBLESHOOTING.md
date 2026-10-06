# 빌드 / 설치 / 실행 트러블슈팅

> 로봇의 런타임 장애(내비게이션 실패, 배터리 부족 등)에 LLM 에이전트가 대응하도록 만드는 `TROUBLESHOOTING.md` 설정 파일과는 다른 문서입니다. 그건 [ROBOT_CONFIG.md](ROBOT_CONFIG.md)를 참고하세요. 이 문서는 개발 환경에서 흔히 겪는 빌드/설치/실행 오류를 다룹니다.

## 빌드 오류

### `vision_msgs` 패키지를 찾을 수 없음

```bash
CMake Error: Could not find a package configuration file provided by "vision_msgs"
```

`vision_msgs`가 설치되어 있지 않은 경우입니다.

```bash
sudo apt install ros-jazzy-vision-msgs
```

---

### ONNX Runtime을 찾을 수 없음

```bash
CMake Error: ONNX Runtime을 찾을 수 없습니다. ONNXRUNTIME_ROOT_DIR='' 를 확인하거나 -DONNXRUNTIME_ROOT_DIR=<경로> 로 지정하세요.
```

**원인:**
`task setup-deps`를 실행하지 않았거나, ONNX Runtime C++ SDK 라이브러리가 탐색 경로(프로젝트 루트, `/usr/local/`, `~/.local/`)에 설치되어 있지 않은 경우 발생합니다.

**해결 방법:**

1. **자동 설치 (권장)**:
   `task setup-deps` 명령어를 실행하면 시스템 아키텍처(`x64` / `aarch64`)를 자동 감지하여 프로젝트 루트에 ONNX Runtime 1.19.2 C++ SDK를 다운로드 및 설정합니다.

   ```bash
   task setup-deps
   ```

2. **수동 설치 및 경로 지정**:
   직접 다운로드하여 프로젝트 루트나 원하는 경로에 해제하거나, 빌드 시 `-DONNXRUNTIME_ROOT_DIR` 옵션으로 경로를 전달할 수 있습니다.

   ```bash
   # x86_64 환경 수동 설치 (프로젝트 루트)
   wget -q "https://github.com/microsoft/onnxruntime/releases/download/v1.19.2/onnxruntime-linux-x64-1.19.2.tgz" -O /tmp/ort.tgz && \
   tar -xzf /tmp/ort.tgz -C ./ && rm /tmp/ort.tgz

   # ARM64 (aarch64) 환경 수동 설치 (프로젝트 루트)
   wget -q "https://github.com/microsoft/onnxruntime/releases/download/v1.19.2/onnxruntime-linux-aarch64-1.19.2.tgz" -O /tmp/ort.tgz && \
   tar -xzf /tmp/ort.tgz -C ./ && rm /tmp/ort.tgz
   ```

---

### `cv_bridge/cv_bridge.h: No such file or directory`

```bash
fatal error: cv_bridge/cv_bridge.h: No such file or directory
```

ROS 2 Jazzy부터 `cv_bridge` 헤더 확장자가 `.h` → `.hpp`로 변경되었습니다.
소스 코드의 include를 아래와 같이 수정하세요.

```cpp
// 변경 전
#include "cv_bridge/cv_bridge.h"

// 변경 후
#include "cv_bridge/cv_bridge.hpp"
```

---

## 패키지 설치 오류

### `ros-humble-moveit-ros` 설치 시 `libdw-dev` 의존성 충돌

`ros-humble-moveit-ros` 패키지를 설치할 때 아래와 같이 `libdw-dev` 및 `libdw1` 간의 버젼 충돌이 발생하는 경우입니다:

```text
The following packages have unmet dependencies:
 libdw-dev : Depends: libelf-dev but it is not going to be installed
             Depends: libdw1 (= 0.186-1ubuntu0.1) but 0.188-1~bpo22.04.1 is to be installed
E: Unable to correct problems, you have held broken packages.
```

**원인:**
시스템에 이미 `jammy-backports` 저장소에서 가져온 `libdw1` (버전 `0.188-1~bpo22.04.1`) 패키지가 설치되어 있으나, `apt`가 `libdw-dev` 및 `libelf-dev`를 설치할 때 기본 `jammy-updates` 저장소의 버전(`0.186-1ubuntu0.1`)을 기본값으로 가져오려 하면서 버전 불일치로 충돌이 발생합니다.

**해결 방법:**
아래와 같이 의존성 패키지를 명시적으로 `jammy-backports` 저장소로 지정하여 함께 설치합니다:

```bash
sudo apt install ros-humble-moveit-ros libdw-dev/jammy-backports libelf-dev/jammy-backports
```

---

## 실행 오류

### `ModuleNotFoundError: No module named 'rclpy._rclpy_pybind11'`

실행 시 아래와 같이 `rclpy` 모듈 내 C 확장 패키지를 찾을 수 없다는 오류가 발생하는 경우입니다:

```text
ModuleNotFoundError: No module named 'rclpy._rclpy_pybind11'
The C extension '/opt/ros/humble/lib/python3.10/site-packages/_rclpy_pybind11.cpython-312-x86_64-linux-gnu.so' isn't present on the system.
```

**원인:**
ROS 2 Humble는 Ubuntu 22.04의 기본 Python 버전인 3.10 버전용으로 컴파일된 C 확장 모듈(`_rclpy_pybind11.cpython-310-*.so`)을 사용합니다. 하지만 `uv`를 통해 가상환경을 구성할 때 최신 Python 버전(예: 3.12)을 기반으로 `.venv`를 생성하여 실행함으로써 ROS의 Python C 확장 모듈을 로드하지 못해 발생합니다.

**해결 방법:**
가상환경을 시스템 기본 Python인 3.10 버전으로 다시 생성해 주어야 합니다.

1. 기존 가상환경 제거 후 시스템 Python 경로(`/usr/bin/python3`)를 명시하여 재생성합니다:

   ```bash
   rm -rf .venv
   uv venv --python /usr/bin/python3 --system-site-packages
   uv sync
   ```

   _(팁: 개발용 대용량 의존성 패키지(PyTorch 등) 다운로드가 너무 오래 걸리는 경우, 런타임에 필요한 패키지만 설치하도록 `uv sync --no-dev` 명령어를 사용할 수 있습니다.)_

2. 의존성 패키지를 다시 불러옵니다:

   ```bash
   task setup-deps
   ```
