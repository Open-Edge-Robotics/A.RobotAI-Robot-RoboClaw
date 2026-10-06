# RoboClaw 개발 가이드

이 문서는 RoboClaw 저장소에서 작업하는 사람과 코딩 에이전트를 위한 개발 규칙입니다.
특히 로봇 제어 코드의 안전성과 TDD 기반 회귀 방지를 우선합니다.

## 1. 프로젝트 개요

RoboClaw는 ROS 2 기반 로봇 에이전트 런타임입니다.

| 패키지                | 책임                                       | 주 언어      |
| --------------------- | ------------------------------------------ | ------------ |
| `robo_claw_msgs`      | ROS msg/srv/action 및 proto 정의           | IDL/proto    |
| `robo_claw_core`      | 하드웨어 인터페이스, 제어 루프, 속도 제한  | C++          |
| `robo_claw_agent`     | LLM, planner, skill, memory, safety policy | Python       |
| `robo_claw_channel`   | HTTP/gRPC/메신저 채널                      | Python       |
| `robo_claw_discovery` | ROS 그래프 및 peer 탐색                    | Python       |
| `robo_claw_grpc`      | 외부 시스템용 RosGrpc 서비스               | Python       |
| `robo_claw_bringup`   | launch/config 및 통합 실행                 | Python/ROS 2 |
| `robo_claw_vision`    | 실시간 객체 인식                           | C++          |

## 2. 작업 기본 원칙

1. **TDD를 기본으로 한다.**
   - 요구사항을 먼저 테스트 가능한 문장으로 만든다.
   - 실패하는 테스트를 먼저 추가한다(Red).
   - 최소 구현으로 통과시킨다(Green).
   - 통과한 뒤 구조를 개선한다(Refactor).
2. **작은 변경을 유지한다.** 기능 변경, 리팩터링, 포맷 변경을 한 커밋에 섞지 않는다.
3. **안전 우선이다.** 실제 로봇을 움직일 수 있는 코드에는 입력 검증, timeout, 실패 결과, emergency stop 경로를 함께 검토한다.
4. **외부 시스템을 테스트에서 격리한다.** LLM, ROS action/service, 카메라, Nav2, MoveIt, Qdrant, gRPC는 fake/mock/contract adapter를 사용한다.
5. **생성물과 실행 환경을 구분한다.** `build/`, `install/`, `log/`, `__pycache__/`는 소스 수정 대상으로 취급하지 않는다. ROS 메시지/서비스 변경 후에는 반드시 workspace를 다시 build한다.
6. **기존 테스트와 문서를 함께 갱신한다.** public skill schema, API, config, launch argument를 바꾸면 관련 테스트와 문서를 같은 변경에 포함한다.

## 3. 권장 구조

Python 도메인 로직은 ROS 2 import에서 최대한 분리한다.

```text
robo_claw_agent/
  domain/          # 순수 규칙, 상태, 정책; ROS2/네트워크 import 금지
  application/     # 유스케이스와 orchestration
  adapters/        # ROS2, LLM, DB, gRPC adapter
  infrastructure/  # 외부 provider 및 저장소 구현
  agent_node/      # ROS lifecycle, parameter, wiring
  skills/          # 사용자에게 노출되는 skill과 skill sequence
```

현재 저장소가 모든 디렉터리를 위 구조로 완전히 분리한 상태는 아니므로, 새 코드를 작성할 때부터 위 경계를 적용한다. ROS2 의존성이 없는 함수는 `rclpy` 또는 generated message를 import하지 않도록 한다.

## 4. 테스트 계층

테스트에는 가능한 한 다음 마커를 붙인다.

- `unit`: 외부 시스템 없는 빠른 테스트
- `component`: 하나의 컴포넌트와 fake dependency를 검증
- `integration`: 여러 컴포넌트 연결을 검증
- `ros2`: `rclpy`, generated messages, Nav2 등 ROS2 환경 필요
- `hardware`: 실제 하드웨어 또는 hardware-like 환경 필요
- `slow`: 일반 개발 루프에서 제외 가능한 테스트
- `qdrant`, `mcp`: 해당 선택 의존성 필요

새 테스트 예시:

```python
import pytest

pytestmark = pytest.mark.unit


def test_invalid_joint_target_is_rejected():
    result = check_joint_targets({"wrist_roll": 99.0})
    assert result.allowed is False
```

테스트는 다음 순서로 작성한다.

1. 정상 경로
2. 잘못된 입력 및 경계값
3. timeout/취소/외부 의존성 실패
4. 안전 차단 및 recovery
5. side effect와 결과 메시지

특히 로봇 skill은 `success`, 실패 이유, timeout, 실행된 command/goal, safety rejection을 검증한다. 단순히 예외가 발생하지 않았다는 것만 검증하지 않는다.

## 5. 테스트 명령

### 빠른 TDD 루프

```bash
# workspace가 한 번 이상 build되어 있어야 한다
task unit-test

# 특정 테스트
task unit-test -- -k "test_skill_manager"
```

`unit-test`는 ROS2/hardware 테스트를 제외하고 Python 테스트만 실행한다. 현재 일부 레거시 테스트는 collection 단계에서 ROS generated service를 import하므로, ROS2 관련 테스트 파일은 fast loop에서 제외된다.

### 전체 Python 및 ROS2 테스트

```bash
task test
```

또는 ROS2 환경에서 직접 실행한다.

```bash
source /opt/ros/jazzy/setup.bash  # 환경에 맞게 humble/jazzy 선택
colcon build --symlink-install
source install/setup.bash
colcon test --event-handlers console_direct+
colcon test-result --verbose
```

### 정적 검사

```bash
uv run ruff check src/
uv run ruff format --check src/
```

coverage 의존성이 설치된 경우:

```bash
task unit-test
# 또는
pytest --cov=robo_claw_agent --cov-report=term-missing
```

테스트 실패가 있으면 실패 테스트를 먼저 좁혀 실행한다.

```bash
pytest path/to/test_file.py::test_name -vv
```

## 6. ROS2 및 생성 코드 주의사항

- `robo_claw_msgs`의 msg/srv/action을 변경하면 관련 패키지를 먼저 build한다.
- `install/`에 오래된 generated Python 모듈이 남아 있으면 source 코드와 다른 service API를 사용하게 된다.
- 다음과 같은 import 오류가 발생하면 먼저 clean build를 고려한다.

```text
cannot import name 'ExecuteManipulator' from 'robo_claw_msgs.srv'
cannot import name 'ListPeers' from 'robo_claw_msgs.srv'
```

권장 절차:

```bash
task build
source install/setup.bash
task unit-test
```

ROS2 node를 테스트할 때는 실제 node 초기화를 단위 테스트에 끌어들이지 말고, 필요한 `create_publisher`, `create_subscription`, action client, clock, parameter API만 제공하는 fake를 사용한다.

## 7. Skill 및 API 변경 규칙

새 skill 또는 skill parameter를 추가할 때 다음을 함께 변경한다.

1. skill의 input schema와 default/limit
2. 정상/실패/경계값 테스트
3. `SkillManager` 등록 또는 discovery 경로
4. `docs/SKILLS.md` 및 관련 운영 문서
5. ROS service/action 또는 gRPC 노출이 있다면 contract 테스트
6. 실제 로봇에서 위험한 동작이면 allow/block policy와 limits 테스트

LLM 응답을 다룰 때는 provider 응답을 직접 실행하지 않는다. 반드시 다음 단계를 둔다.

```text
LLM response -> parse -> schema validate -> safety/policy validate -> execute
```

산문 응답, 잘못된 JSON, 빈 tool call, 알 수 없는 skill, 잘못된 parameter, retry budget 소진을 각각 테스트한다.

## 8. 안전 관련 변경 규칙

다음 코드는 변경 시 테스트를 필수로 추가한다.

- joint/velocity/position limit
- emergency stop
- navigation goal timeout/cancel/rejection
- manipulation precondition 및 grasp failure
- sensor health 및 stale data
- peer task delegation
- HTTP/gRPC control authorization

실기기에서 새 기능을 검증하기 전에 simulation 또는 mock backend에서 다음을 확인한다.

- 기본값이 안전한가
- timeout 이후 정지하는가
- 실패 시 partial execution을 보고하는가
- 재시도 횟수가 제한되는가
- emergency stop이 모든 실행 경로보다 우선하는가

## 9. 에이전트 작업 절차

코딩 에이전트는 작업 시작 전에 다음을 수행한다.

```bash
git status --short
find . -maxdepth 2 -type f | sort
```

그 다음:

1. 관련 source, test, package `CMakeLists.txt`, 문서를 함께 읽는다.
2. 변경 전 관련 테스트를 실행하여 baseline을 확인한다.
3. 요구사항을 검증하는 실패 테스트를 먼저 작성한다.
4. 최소한의 구현을 한다.
5. 빠른 테스트와 정적 검사를 실행한다.
6. ROS2 관련 변경이면 build/colcon test도 실행한다.
7. 변경 파일, 실행 명령, 통과/실패 결과, 남은 환경 문제를 최종 보고한다.

다른 에이전트가 만든 변경 또는 untracked 파일을 임의로 삭제하지 않는다. 작업 범위를 벗어난 변경은 최종 보고에 명시한다.

## 10. 커밋 전 체크리스트

- [ ] 실패 조건과 경계값 테스트가 있는가?
- [ ] 테스트가 외부 시스템에 불필요하게 의존하지 않는가?
- [ ] `task unit-test`를 실행했는가?
- [ ] `task test` 또는 관련 `colcon test`를 실행했는가?
- [ ] `uv run ruff check src/`를 실행했는가?
- [ ] ROS generated code가 최신 상태인가?
- [ ] skill/API/config/문서가 서로 일치하는가?
- [ ] 문서를 추가/이름 변경/삭제했다면 README 표와 전체 참조를 갱신하고 링크를 검증했는가? (§12)
- [ ] 로봇을 움직일 수 있는 변경에 timeout과 safety rejection 테스트가 있는가?
- [ ] `git diff --check` 및 `git status --short`를 확인했는가?

## 11. Shared Configuration Contract

RoboClaw 설정의 canonical source는 별도 `rcf-config-contract` 저장소의 GitLab
Release artifact다. 이 저장소의 `contracts/contract.lock.json`이 사용하는 버전과
checksum을 고정한다.

### 소유권

- 공통 runtime 설정: `rcf-config-contract`
- RoboClaw Go/Python/shell consumer mapping: 이 저장소
- robot topic/URDF/manipulation: `src/robo_claw_bringup/config/<robot>_config.yaml`
- host path/ROS distro/DDS: `.env`, `scripts/`
- AI Config Server Dart/schema: `ai-config-server` 저장소

vendored contract가 있는 상태에서 local Registry를 직접 수정하지
않는다. 공통 저장소에서 Registry를 수정하고 release artifact를 만든 뒤 다음처럼
업데이트한다.

```bash
./rclaw contract update --from /path/to/release-bundle
# 또는 원격 GitLab Release 직접 다운로드:
./rclaw contract update --version v2.0.1

# 검증 (lock 체크섬, .env.example 문서화, 생성 아티팩트 drift 일괄 검증)
./rclaw contract check
```

### 생성 파일

다음 파일은 Registry/contract generator가 소유한다. 직접 편집하지 않는다.

- `robo_claw_cli/contract/runtime_config_generated.go`
- `src/robo_claw_bringup/launch/_generated_runtime_config.py`
- `scripts/generated_runtime_env.sh`
- `config-schema/generated/*`

### 저장소 독립성

RoboClaw generator는 AI Config Server sibling 경로를 참조하지 않는다. 단독 clone에서도
contract lock, implementation coverage, launch/shell mapping, generated drift 검증이
통과해야 한다.

### 필수 검증

```bash
task contract-check
task unit-test
```

설정 변경에는 `.env.example`, 설정 가이드, contract/launch mapping 테스트를 함께
검토한다. 안전 관련 설정은 simulation 또는 mock에서 먼저 검증한다.

## 12. 문서 작성 규칙

이 저장소의 모든 마크다운 문서는 아래 규칙을 따른다. 문서를 생성·변경·삭제·이름 변경하는
에이전트는 이 섹션을 기준으로 판단한다.

### 12.1 문서 종류 정책

커밋할 수 있는 문서와 없는 문서를 구분한다.

| 허용                                                                                       | 금지                                                                                                                                            |
| ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| 기능 가이드, 스킬/API 레퍼런스, 운영 절차, 트러블슈팅, 아키텍처 설명, 배포 가이드, QA 절차 | 개발 계획문서(P0/P1 우선순위 목록 등), 작업 점검 보고서, AI 세션 노트/변경 노트, 별도 변경 이록 문서, 단발성 분석 메모, 미구현 설계안 단독 문서 |

- 변경 이력은 개별 문서를 만들지 않고 루트 `CHANGE_LOG.md`에 기록한다. 상세한 변경
  배경은 `CHANGE_LOG.md`의 날짜별 항목/부록에 정리한다.
- 설계 결정이 실제로 구현되었으면 해당 기능의 가이드 문서에 반영하고, 설계안 단독
  문서는 남기지 않는다. 미구현 설계안은 커밋하지 않는다.
- 작업 중 AI 세션 산출물(점검 리포트, changenote, 치트시트)을 저장소에 커밋하지 않는다.
- 대화체("~했습니다. 정리해드릴게요")나 세션 맥락이 드러난 문장을 문서에 남기지 않는다.

### 12.2 파일 위치와 명명

- 기능 가이드/레퍼런스는 `docs/`에 두고, 파일명은 `UPPER_SNAKE_CASE.md`로 작성한다.
  (예: `HTTP_API.md`, `ROBOT_CONFIG.md`. 소문자·하이픈 혼용 금지)
- 주제당 파일 1개를 유지한다. 같은 주제의 문서가 분산되면 통합한다. 예:
  `ROBOT_SOUL` + `ROBOT_SAFETY_CONFIG` → `ROBOT_CONFIG`, Docker 실행 + 빌드 → `DOCKER_GUIDE`.
- 런타임에 에이전트에게 주입되는 파일(`SKILLS.<robot>.md`, `ROBOT.md` 등)은
  `src/robo_claw_bringup/config/`의 런타임 설정이므로 `docs/` 문서와 구분한다.
- 생성 파일(`config-schema/generated/` 등)은 직접 수정하지 않는다(§11 참고).

### 12.3 언어와 문체

- 모든 서술은 한국어로 작성한다. 고유명사·기술 용어·오류 메시지·설정값은 영문 원문을 유지한다.
- 문체는 **"~합니다"체로 통일**한다. 예외 없이 쓰며, 예시 인용문(ROBOT.md 템플릿 내용 등)
  안의 문장은 그대로 둔다.
- H1은 파일명을 자기참조하지 않는 한글 제목(`# 자율 행동 트리 아키텍처` 등)으로 쓰고,
  이모지를 붙이지 않는다. 첫 문단에서 문서의 목적과 범위를 한두 문장으로 명시한다.
- 문장은 간결하게 쓰고, 여러 값을 비교할 때는 표를, 절차는 번호 목록과 실행 가능한
  코드 블록(언어 태그 포함)을 사용한다.

### 12.4 문서 생성·변경·삭제 절차

1. 새 문서를 만들면 `README.md`의 "📚 상세 가이드" 표에 한 행을 추가한다.
2. 문서를 이름 변경·삭제할 때는 저장소 전체(`*.md`, `*.py`, `*.yaml`, `*.sh`, `*.yml`,
   `*.go`)에서 해당 파일명을 검색해 참조(소스 주석·config 주석 포함)를 함께 갱신한다.
3. 문서가 참조하는 소스 파일·스킬·파라미터가 실제로 존재하는지 확인한다. 스킬을
   추가/변경하면 `docs/SKILLS.md`와 `task lint-skills`로 일치시킨다(§7 연계).
4. 커밋 전 저장소 전체 마크다운의 상대 링크를 검증한다.

   ```bash
   python3 - <<'EOF'
   import os, re, glob
   broken = []
   for f in glob.glob('docs/**/*.md', recursive=True) + ['README.md']:
       text = open(f, encoding='utf-8').read()
       for m in re.finditer(r'\]\(([^)#\s]+?)(?:#[^)]*)?\)', text):
           link = m.group(1)
           if link.startswith(('http://', 'https://', 'mailto:')):
               continue
           target = os.path.normpath(os.path.join(os.path.dirname(f), link))
           if not os.path.exists(target):
               broken.append(f"{f} -> {link}")
   print('\n'.join(broken) or 'no broken links')
   ```

5. 문서만의 변경은 커밋 전 별도 테스트를 요구하지 않지만, 소스 문자열(프롬프트 등)을
   함께 바꿨다면 `task build` 후 해당 스킬 테스트를 실행한다.

## 13. 파이썬 의존성 관리

이미지 빌드는 `uv.lock`을 사용하지 않고 `Dockerfile`에서 pip로 직접 설치합니다. 따라서
개발 환경과 배포 이미지의 패키지 버전이 어긋날 수 있습니다. 실제로 `mcp>=1.0.0`
범위가 빌드 시점에 mcp 2.x를 설치하면서 `ClientSession.list_tools` 시그니처가
달라져 MCP 스킬 로딩이 실패한 사례가 있습니다.

### 적용 규칙

1. `Dockerfile`과 `pyproject.toml`에 모두 있는 패키지는 버전 범위를 동일하게 유지합니다.
2. 런타임 API가 버전에 민감한 패키지는 `pyproject.toml`에 범위를, `Dockerfile`에
   `uv.lock`과 같은 버전의 정확한 고정(`==<version>`)을 둡니다. 현재 대상은
   `scripts/check_docker_deps.py`의 `LOCK_PINNED`에 등록되어 있습니다.
3. `pyproject.toml`에 없는 이미지 전용 패키지는 같은 스크립트의 `DOCKER_ONLY`에 사유와
   함께 등록합니다. ROS/colcon 빌드 도구처럼 이미지에서 다른 버전을 고정하는 패키지는
   `DOCKER_OVERRIDE_ALLOWED`에 등록합니다.

### 필수 검증

```bash
task deps-check
```

`task lint`에 포함되어 있으므로 의존성을 변경하면 함께 실행합니다. 검사를 통과시키기
위해 목록을 늘리기 전에, 왜 그 패키지가 예외여야 하는지 주석에 남깁니다.

### 코드 호환 계층

버전 차이를 흡수하는 코드는 `robo_claw_agent/mcp_adapter.py`의 호환 헬퍼
(`result_next_cursor`, `tool_input_schema`, `result_is_error`, `read_timeout_value` 등)처럼
경계 모듈 한 곳에 모으고, 설치 버전에 의존하지 않는 대역(fake)으로 양쪽 동작을
테스트합니다.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
