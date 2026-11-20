# 로봇별 설정 파일 가이드 (개성·안전)

에이전트에 로봇별 파일을 주입해 개성, 물리 한계, 장애 조치를 구성하는 방법을 다룹니다. 주입되는 파일은 세 가지입니다.

| 파일                 | 형식     | 역할                                               | `.env` 변수                     |
| -------------------- | -------- | -------------------------------------------------- | ------------------------------- |
| `ROBOT.md`           | 마크다운 | 이름·성격·가치관 등 로봇 개성                      | `RC_ROBOT_SOUL_FILE`            |
| `ROBOT_LIMITS.json`  | JSON     | 상대 이동·회전·위험 구역·관절 한계 등 스킬 검증값  | `RC_ROBOT_LIMITS_FILE`          |
| `TROUBLESHOOTING.md` | 마크다운 | 내비게이션 실패·배터리 부족 등 예외 상황 조치 지침 | `RC_TROUBLESHOOTING_GUIDE_FILE` |

설정 방식은 다음 중 하나입니다.

- `agent.yaml`에서 절대 경로 지정 (아래 예시)
- `.env`의 `RC_*` 환경 변수 지정 (Docker 실행 시 마운트로 연동, 상세는 [DOCKER_GUIDE.md](DOCKER_GUIDE.md))

`src/robo_claw_bringup/config/agent.yaml`:

```yaml
robo_claw_agent_node:
  ros__parameters:
    robot_soul_file: "/path/to/ROBOT.md" # 비워두면 개성 없음
    robot_limits_file: "/path/to/ROBOT_LIMITS.json" # 비워두면 일부 코드 기본값을 적용
    troubleshooting_guide_file: "/path/to/TROUBLESHOOTING.md" # 비워두면 기본 가이드 없음
```

> 이 문서의 `TROUBLESHOOTING.md`는 **로봇마다 직접 작성해 주입하는 런타임 장애 대응 지침**입니다. 개발 환경의 빌드/실행 오류 해결 문서는 [BUILD_TROUBLESHOOTING.md](BUILD_TROUBLESHOOTING.md)와 별개입니다.

---

## 1. 로봇 개성 (`ROBOT.md`)

로봇마다 고유한 이름·성격·가치관·행동 스타일을 정의하는 기능입니다. Claude Code의 `CLAUDE.md`와 동일한 발상으로, `ROBOT.md`의 내용이 모든 LLM 추론의 시스템 프롬프트 맨 앞에 삽입됩니다.

### 설정 방법

```bash
# 1. 템플릿 복사
cp src/robo_claw_bringup/config/ROBOT.example.md /path/to/ROBOT.md

# 2. 원하는 개성으로 편집
nano /path/to/ROBOT.md

# 3. agent.yaml 또는 .env(RC_ROBOT_SOUL_FILE)에 경로 지정
```

### 적용 범위

| LLM 호출                             | 개성 주입 여부                    |
| ------------------------------------ | --------------------------------- |
| 일반 대화 / 작업 처리 (`agent_node`) | ✅ 항상 주입                      |
| 자율 행동 결정 (`LLMDecideAction`)   | ✅ 주입                           |
| 장면 VLM 분석 (`analyze_image`)      | ❌ 미주입 (객체 감지 정확도 우선) |

### ROBOT.example.md 구성 예시

```markdown
## 나는 누구인가 (Identity)

나의 이름은 **Clio**이다. 가정용 보조 로봇으로 ...

## 성격 (Personality)

- 따뜻하고 친근한 말투를 사용한다.
- 새로운 물건이나 공간을 발견하면 설레는 마음으로 탐색한다.

## 가치관 및 우선순위 (Values)

1. 사람의 안전이 최우선이다.
2. 정직하게 현재 상황을 보고한다.

## 커뮤니케이션 스타일 (Communication Style)

- 보고 시 핵심 결과를 먼저 말하고 세부 사항을 이어서 설명한다.
```

### 개체별 소울 프로필

같은 기종을 여러 대 운용하면서 개체별 성격·상태를 다르게 전달하려면 소울 파일을 개체별로
나누고 `RC_ROBOT_SOUL_FILE` 로 선택합니다. CLOiD 는 이 방식을 사용합니다.

| 파일 | 대상 |
| :--- | :--- |
| `src/robo_claw_bringup/config/ROBOT.md` | 공통 기본 소울(다른 로봇 프로필과 공유) |
| `src/robo_claw_bringup/config/ROBOT.cloid.1.md` | CLOiD 1호. 팔·상체 모션을 쓰는 정상 개체의 개성 |
| `src/robo_claw_bringup/config/ROBOT.cloid.2.md` | CLOiD 2호. 관찰·순찰 중심으로 공간을 살피는 개성 |

`robot_config:=cloid` 로 기동하면서 `robot_soul_file`/`RC_ROBOT_SOUL_FILE` 을 지정하지 않으면,
launch 가 `ROBOT.cloid.md` 를 먼저 찾고 없으면 `ROBOT.cloid.1.md` 를 씁니다. 2호는 소울 파일
경로를 명시합니다.

```bash
ros2 launch robo_claw_bringup robo_claw.launch.py \
  robot_config:=cloid \
  robot_soul_file:="$(pwd)/src/robo_claw_bringup/config/ROBOT.cloid.2.md"
```

> 소울 파일은 개성·말투·가치관만 담고, 어떤 스킬을 쓸지·무엇을 할 수 없는지 같은 기능 규칙은
> SKILLS 가이드와 ROBOT_LIMITS 로 분리해 둔니다([CLOiD_GUIDE.md](CLOiD_GUIDE.md) 의 개체별 가이드 선택 참고).

### 로봇 프로필(`<robot_config>_config.yaml`) 선택

`robot_config` 값은 `src/robo_claw_bringup/config/<robot_config>_config.yaml` 을 선택하고,
같은 값으로 `SKILLS.<robot_config>.md` 와 `ROBOT_LIMITS.<robot_config>.json` 기본값을 찾습니다.

| `robot_config` | 설정 파일 | 대상 |
| :--- | :--- | :--- |
| `former` | `former_config.yaml` | 팔이 없는 Former (주행·관측 전용, `manipulation_enabled: false`) |
| `former_arm` | `former_arm_config.yaml` | Former0045 + AgileX Nero 7-DOF 팔 (`manipulation_enabled: true`) |
| `cloid` | `cloid_config.yaml` | CLOiD 개체 |
| `stretch3` | `stretch3_config.yaml` | Stretch3 (`stretch` 백엔드) |

팔이 장착된 개체는 `former_arm` 을 사용합니다. 팔 PC 의 CAN·ROS 그래프·MoveIt 연결 조건과
프레임 정리는 [MANIPULATION.md](MANIPULATION.md) 의 "AgileX Nero 팔(agx_arm_ros) 연동" 절을
참고합니다.

---

## 2. 물리 제약 스펙 (`ROBOT_LIMITS.json`)

`ROBOT_LIMITS.json`은 프롬프트 참고 정보와 일부 스킬의 실행 직전 검증값을 제공합니다.
이 파일의 모든 필드가 하드웨어 명령에 강제 적용되는 것은 아니며, 실제 제한은 스킬별
코드 경로에 따라 다릅니다. 파일이 없더라도 상대 이동 거리 기본 상한 5 m와 회전 기본
상한 180°가 적용됩니다. 관절 목표는 필요한 제한값을 확인할 수 없으면 실행을 거부하는
경로가 있습니다.

```json
{
  "navigation": {
    "max_relative_distance_m": 5.0,
    "max_rotation_deg": 180.0,
    "arrival_tolerance_m": 0.25,
    "forbidden_zones": [
      {
        "name": "stairs_zone",
        "x_min": 1.5,
        "x_max": 2.5,
        "y_min": -1.0,
        "y_max": 1.0,
        "reason": "낙하 위험 구역 (Stairs)"
      }
    ]
  },
  "manipulation": {
    "max_payload_kg": 2.0,
    "joint_limits_deg": {
      "joint_1_base": [-150.0, 150.0],
      "joint_2_shoulder": [-30.0, 90.0]
    }
  }
}
```

`navigation.max_relative_distance_m`와 `max_rotation_deg`는 상대 이동·회전 스킬의 입력
검사에 사용합니다. `max_linear_velocity_mps`, `max_angular_velocity_radps`와
`manipulation.max_payload_kg`는 이 파일만으로 모든 제어 경로의 속도·하중 제한을
강제하지 않습니다. `navigation.arrival_tolerance_m`는 `navigate_to`의 도착 판정 허용
오차(m)입니다. 기본값은 Nav2 `xy_goal_tolerance`와 같은 `0.25`입니다. 로봇 크기와
현지화 정확도에 맞게 설정하고 실기에서 도착 판정 동작을 검증합니다.

---

## 3. 장애 조치 가이드 (`TROUBLESHOOTING.md`)

목적지 도달 실패(NAVIGATION_TIMEOUT), 경로 계획 실패(PLANNING_FAILED), 배터리 부족, 센서 유실 같은 예외 상황이 감지될 때 LLM 에이전트가 취할 즉각적 행동 지침(자가 치유·조치 가이드라인)입니다.

```markdown
# RoboClaw 에이전트 장애 조치 가이드

## 1. 내비게이션 장애 (Navigation Failures)

### NAVIGATION_TIMEOUT / GOAL_ABORTED

- **조치 가이드라인**:
  1. 기동을 멈추고 제자리에 정지합니다.
  2. 동적 장애물에 의해 차단된 경우 대기하고, 지속적이면 Home으로 복귀하거나 대기합니다.

## 2. 하드웨어 진단 에러 (Hardware Diagnostic Errors)

### BATTERY_LOW (배터리 부족)

- **조치 가이드라인**:
  1. 비필수 작업을 즉시 중단하고 안전한 홈 자세로 팔을 접습니다.
  2. 도킹 스테이션으로 자동 복귀하여 충전을 시작하십시오.
```

---

## 4. 실시간 자가진단 상태 (`RobotHealthState`) 피드백

LLM 에이전트는 실시간으로 배터리 잔량, 센서 연결 여부(Lidar, Camera, IMU), 동작 에러 리스트를 취합해 시스템 프롬프트의 `[로봇 자가진단 상태 (Health State)]`에 주입받습니다. LLM은 이를 `TROUBLESHOOTING.md`의 조치 가이드라인과 대조해 최적의 안전 행동 계획을 결정합니다.

관련 운영 파라미터(`strict_config`, `llm_fail_fast`)는 [OPERATIONS.md](OPERATIONS.md)를 참고하세요.
