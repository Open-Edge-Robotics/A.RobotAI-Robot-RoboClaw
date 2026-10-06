# 로봇 상태 초기화 (호스트 SSH)

매 테스트 전에 로봇에서 수동으로 하던 아래 초기화를 **호스트 컴퓨터에서 한 번에** 실행합니다.

> **CLI**: 이 스크립트는 저장소 루트 `scripts/` 아래에 있습니다. `rclaw reset-robot state` 로도
> 동일하게 실행할 수 있으며, `--robot` / `-y` / `--dry-run` / `--restart` 플래그를 받습니다.

```bash
# (로봇에서 하던 것)
rm -rf ~/.robo_claw/config_cache/                                 # soul/skill/troubleshooting/limits 캐시
rm -rf ~/workspace/robo-claw/robo-claw/agent_workspace/memory     # 에이전트 메모리(로컬 벡터/시맨틱)
```

- **config 캐시 삭제** → 다음 `launch` 때 config 서버에서 프로파일(soul/skill/troubleshooting/limits)을 다시 내려받음.
- **메모리 삭제** → 위치·지식 기억이 빈 상태로 시작.

## 요구사항

- 로봇에 SSH 키 접속 설정(`ssh-copy-id`)이 되어 있어야 함(비밀번호 없이 접속).

## 사용법

```bash
cd robo-claw/scripts

./reset_robot_state.sh                # 확인 프롬프트 후 실행 (기본 former@10.159.172.69)
./reset_robot_state.sh -y             # 확인 없이 실행
./reset_robot_state.sh --dry-run      # 실제 삭제 없이, 실행될 SSH 명령만 출력
./reset_robot_state.sh --restart      # 초기화 후 에이전트 컨테이너(robo_claw_container) 재시작
./reset_robot_state.sh --robot former@10.159.172.69
```

## 옵션 / 환경변수

| 인자                  | 환경변수(기본값)                                                    | 설명                           |
| :-------------------- | :------------------------------------------------------------------ | :----------------------------- |
| `--robot <user@host>` | `ROBOT=former@10.159.172.69`                                        | 대상 로봇                      |
| `-y`, `--yes`         | —                                                                   | 확인 프롬프트 생략             |
| `--dry-run`           | —                                                                   | 실행될 명령만 출력(삭제 안 함) |
| `--restart`           | —                                                                   | 초기화 후 컨테이너 재시작      |
| —                     | `CONFIG_CACHE=~/.robo_claw/config_cache`                            | 삭제할 config 캐시 경로        |
| —                     | `MEMORY_DIR=~/workspace/robo-claw/robo-claw/agent_workspace/memory` | 삭제할 메모리 경로             |
| —                     | `CONTAINER=robo_claw_container`                                     | `--restart` 대상 컨테이너명    |

## 안전장치

- 대상 경로가 비었거나 위험 경로(`~`, `~/`, `/`, `$HOME`, 1단계 이하)면 **삭제하지 않고 중단**합니다.
- 삭제 전 실행될 SSH 명령을 출력하고, `--dry-run`으로 미리 확인할 수 있습니다.
- `~`는 **로봇(원격) 셸**에서 확장됩니다(호스트가 아니라).

## 관련

- **더 깊은 초기화**(Qdrant 컬렉션까지): 로봇에서 `bash scripts/reset_memory.sh -y` — 컨테이너 정지 + 로컬 미러/시맨틱/콜드 삭제 + Qdrant 컬렉션 DELETE.
- 이 스크립트는 그 전 단계로, **config 캐시 + 로컬 메모리 폴더**만 빠르게 지웁니다.
