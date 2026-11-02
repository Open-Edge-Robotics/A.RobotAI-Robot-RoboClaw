# CLOi 모션 카탈로그

CLOi(CLOi-D) 로봇에 등록된 상체 모션 목록과 분류를 정리합니다. `list_cloid_motions` 스킬과 `MotionCmd` 실행 경로가 참조하는 카탈로그의 스냅샷이며, 모션 선택·검색 시 어떤 id와 카테고리를 쓸 수 있는지 판단하는 근거로 사용합니다.

## 1. 수집 정보

| 항목 | 값 |
| --- | --- |
| 수집일 | 2026-11-02 |
| 수집 호스트 | `cloid2` (192.168.50.105), 컨테이너 `cloi-robo-claw` |
| 원본 경로 | `/ws/motion_list/motion_map.json` |
| 사본 경로 | `cloid/motion_list/motion_map.json` |
| 크기 / MD5 | 36,176 bytes / `71150dde55a962d3603e3f1b210d4956` |
| 모션 수 | 83 |
| 카테고리 수 | 18 |

원본은 로봇 컨테이너의 읽기 전용 마운트 경로에 있고, RoboClaw는 `/task_manager/motion_map` 토픽을 우선 사용하며 토픽이 비어 있을 때만 파일을 fallback으로 읽습니다. 이 문서의 표는 위 사본을 기준으로 작성합니다.

## 2. 파일 형식

| 필드 | 의미 |
| --- | --- |
| `id` | `MotionCmd`로 모션을 지정할 때 쓰는 id입니다. |
| `intent_id` | 모션 카테고리(한글)입니다. |
| `display_name` | 영문 표시 이름입니다. 중복이 있으므로 실행 시에는 `id`를 사용합니다. |
| `description` | 국문 모션 설명입니다. |
| `asset_file` | 실제 모션 데이터 JSON 파일명입니다. |
| `mask` | 관절 사용 범위입니다. `W`(허리), `L`(왼팔·손), `R`(오른팔·손), `H`(머리) 조합입니다. |
| `fps` | 모션 재생 속도(Hz)입니다. |
| `pre_id` | 선행 필수 모션 id입니다. 값이 있으면 해당 모션을 먼저 실행합니다. |
| `hand_type` | 모션 기준 손 하드웨어 타입입니다. |
| `pd_gain`, `lim` | 모션별 gain·제한 override입니다. 없으면 `defaults`를 사용합니다. |

`defaults`는 `pd_gain = [300.0, 10.0]`, `lim = [1.5, 50.0, 1000.0]`(`[qdot_max, qacc_max, qjerk_max]`)입니다.

## 3. 카테고리 요약

| 카테고리 | 개수 | id 범위 | fps | hand_type |
| --- | :-: | --- | --- | --- |
| `대화` | 13 | 1~13 | 90 | omni_pro |
| `손흔들기` | 5 | 14~18 | 90 | omni_pro |
| `인사` | 3 | 19~21 | 90 | omni_pro |
| `탐색 모션` | 3 | 22~24 | 90 | omni_pro |
| `아령` | 2 | 25~26 | 90 | omni_pro |
| `실패 모션` | 1 | 27~27 | 90 | omni_pro |
| `대기` | 5 | 28~32 | 90 | omni_pro |
| `숨쉬기` | 5 | 33~37 | 90 | omni_pro |
| `하이파이브` | 2 | 38~39 | 30, 90 | omni_pro |
| `물병 전달` | 3 | 40~42 | 90 | omni_pro |
| `고개움직임` | 13 | 43~55 | 90 | omni_pro |
| `악수` | 3 | 56~58 | 30 | omni |
| `식탁청소` | 2 | 59~60 | 30 | omni |
| `IFA` | 3 | 61~71 | 30 | omni |
| `야구` | 3 | 62~64 | 30 | omni |
| `하트` | 3 | 65~67 | 30 | omni |
| `시상식` | 2 | 68~69 | 30 | omni |
| `BS` | 12 | 225~236 | 30 | gripper |
| **합계** | **83** | | | |

## 4. 카테고리별 모션 목록

### 대화 (1~13, 13개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 1 | talk1 | 대화 모션1 | LR | 90 | - | omni_pro |
| 2 | talk2 | 대화 모션2 | WLRH | 90 | - | omni_pro |
| 3 | talk3 | 대화 모션3 | WLRH | 90 | - | omni_pro |
| 4 | talk4 | 대화 모션4 | WLRH | 90 | - | omni_pro |
| 5 | talk5 | 대화 모션5 | WLRH | 90 | - | omni_pro |
| 6 | talk6 | 대화 모션6 | WLRH | 90 | - | omni_pro |
| 7 | talk7 | 대화 모션7 | WLRH | 90 | - | omni_pro |
| 8 | talk8 | 대화 모션8 | WLRH | 90 | - | omni_pro |
| 9 | talk9 | 대화 모션9 | R | 90 | - | omni_pro |
| 10 | talk10 | 대화 모션10 | WLH | 90 | - | omni_pro |
| 11 | talk11 | 대화 모션11 | WRH | 90 | - | omni_pro |
| 12 | talk12 | 대화 모션12 | WRH | 90 | - | omni_pro |
| 13 | talk13 | 대화 모션13 | WRH | 90 | - | omni_pro |

### 손흔들기 (14~18, 5개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 14 | wave | 설명 후 인사 | WLRH | 90 | - | omni_pro |
| 15 | wave1 | (왼쪽) 양손 인사 | WLRH | 90 | - | omni_pro |
| 16 | wave2 | (중앙) 양손 인사 | WLRH | 90 | - | omni_pro |
| 17 | wave3 | (오른쪽) 양손 인사 | WLRH | 90 | - | omni_pro |
| 18 | wave4 | 양손 인사 | WLRH | 90 | - | omni_pro |

### 인사 (19~21, 3개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 19 | greet1 | 양손 들며 인사 | WLRH | 90 | - | omni_pro |
| 20 | greet2 | 배꼽 인사 | WLRH | 90 | 19 | omni_pro |
| 21 | gentleman_greet | 신사 인사 | WRH | 90 | - | omni_pro |

### 탐색 모션 (22~24, 3개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 22 | scan | 좌우 탐색 | WH | 90 | - | omni_pro |
| 23 | scan | 왼쪽 탐색 | WH | 90 | - | omni_pro |
| 24 | scan | 오른쪽 탐색 | WH | 90 | - | omni_pro |

### 아령 (25~26, 2개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 25 | dumbbell | 아령2회 | WLRH | 90 | - | omni_pro |
| 26 | dumbbell | 아령4회 | WLRH | 90 | - | omni_pro |

### 실패 모션 (27~27, 1개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 27 | fail | 실패 | LRH | 90 | - | omni_pro |

### 대기 (28~32, 5개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 28 | idle1 | 대기모션1 | WLRH | 90 | - | omni_pro |
| 29 | idle2 | 대기모션2 | WLRH | 90 | - | omni_pro |
| 30 | idle3 | 대기모션3 | WLRH | 90 | - | omni_pro |
| 31 | idle4 | 대기모션4 | WLRH | 90 | - | omni_pro |
| 32 | idle5 | 대기모션5 | H | 90 | - | omni_pro |

### 숨쉬기 (33~37, 5개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 33 | mobile_vivid1 | 주행 숨쉬기1 | WLRH | 90 | - | omni_pro |
| 34 | mobile_vivid2 | 주행 숨쉬기2 | WLRH | 90 | - | omni_pro |
| 35 | mobile_vivid3 | 주행 숨쉬기3 | WRH | 90 | - | omni_pro |
| 36 | stop_vivid | 정지 숨쉬기 | WLRH | 90 | - | omni_pro |
| 37 | walk_vivid | 걷는 모션 | WLRH | 90 | - | omni_pro |

### 하이파이브 (38~39, 2개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 38 | hi_five | 하이파이브 준비 | R | 30 | - | omni_pro |
| 39 | hi_five | 하이파이브 복귀 | R | 90 | 38 | omni_pro |

### 물병 전달 (40~42, 3개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 40 | bottle1 | 왼손 들기 | L | 90 | - | omni_pro |
| 41 | bottle2 | 물병 전달 | WLRH | 90 | 40 | omni_pro |
| 42 | bottle3 | 물병 전달 후 복귀 | WLRH | 90 | 41 | omni_pro |

### 고개움직임 (43~55, 13개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 43 | head_up_down_x2 | 두번 끄덕이는 모션 | H | 90 | - | omni_pro |
| 44 | head_left_up | 고개 왼쪽위1 | H | 90 | - | omni_pro |
| 45 | head_right_up | 고개 오른쪽위1 | H | 90 | - | omni_pro |
| 46 | head_left1 | 고개 왼쪽 1 | H | 90 | - | omni_pro |
| 47 | head_right | 고개 오른쪽1 | H | 90 | - | omni_pro |
| 48 | head_down1 | 고개 숙이기 | H | 90 | - | omni_pro |
| 49 | head_down2 | 고개 아래에서 돌아오기 | H | 90 | 48 | omni_pro |
| 50 | head-left1 | 왼쪽 바라보기 | H | 90 | - | omni_pro |
| 51 | head-left2 | 고개 왼쪽에서 끄덕임 | H | 90 | 50 | omni_pro |
| 52 | head-left3 | 고개 왼쪽에서 복귀 | H | 90 | 51 | omni_pro |
| 53 | head-right | 오른쪽 바라보기 | H | 90 | - | omni_pro |
| 54 | head-right2 | 고개 오른쪽에서 끄덕임 | H | 90 | 53 | omni_pro |
| 55 | head-right3 | 고개 오른쪽에서 복귀 | H | 90 | 54 | omni_pro |

### 악수 (56~58, 3개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 56 | hand_shake1 | 손 내밀기 | WLRH | 30 | - | omni |
| 57 | hand_shake2 | 손 흔들기 | WLRH | 30 | - | omni |
| 58 | hand_shake3 | 손 내리기 | WLRH | 30 | - | omni |

### 식탁청소 (59~60, 2개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 59 | wipe_1 | 손 들고 식탁위 행주 잡기 | WLRH | 30 | - | omni |
| 60 | wipe_2 | 식탁 위 행주로 닦기 | WLRH | 30 | 59 | omni |

### IFA (61~71, 3개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 61 | ifa_ready | ifa준비모션 | WLRH | 30 | - | omni |
| 70 | ifa highlight | ifa 연주 하이라이트 모션 | WLRH | 30 | - | omni |
| 71 | ifa full motion | ifa 연주 전체 모션 | WLRH | 30 | - | omni |

### 야구 (62~64, 3개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 62 | baseball_throw | 야구공던지는모션 | WLRH | 30 | - | omni |
| 63 | baseball_bat | 야구배트모션 | WLRH | 30 | - | omni |
| 64 | baseball_throw_real_ball | 야구공 진짜 던지는 모션 | WLRH | 30 | - | omni |

### 하트 (65~67, 3개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 65 | two_hand_heart | 양손 하트 | WLRH | 30 | - | omni |
| 66 | left_hand_heart | 왼손 하트 | WLH | 30 | - | omni |
| 67 | right_hand_heart | 오른손 하트 | WRH | 30 | - | omni |

### 시상식 (68~69, 2개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 68 | present certificate | 상장 전달 모션 | WLRH | 30 | - | omni |
| 69 | present plaque | 상패 전달 모션 | WLRH | 30 | - | omni |

### BS (225~236, 12개)

| id | display_name | description | mask | fps | pre_id | hand_type |
| :-: | --- | --- | :-: | :-: | :-: | --- |
| 225 | hand_up | 오른손 올리는 모션 | WLRH | 30 | - | gripper |
| 226 | pick_actuator | 액츄에이터 잡는 모션 | WLRH | 30 | - | gripper |
| 227 | turn waist | 허리 돌리기 | WLRH | 30 | - | gripper |
| 228 | put down actuator | 액츄에이터 내려놓는 모션 | WLRH | 30 | - | gripper |
| 229 | return | 225 자세로 돌아오는 모션 | WLRH | 30 | - | gripper |
| 230 | reset pose | 기본 자세로 돌아오는 모션 | WLRH | 30 | - | gripper |
| 231 | hand up | 오른손 올리는 모션 | WLRH | 30 | - | gripper |
| 232 | pick_gear | 감속기 잡는 모션 | WLRH | 30 | - | gripper |
| 233 | turn waist | 허리 돌리는 모션 | WLRH | 30 | - | gripper |
| 234 | put down gear | 감속기 내려놓는 모션 | WLRH | 30 | - | gripper |
| 235 | return | 기본 자세로 돌아오는 모션 | WLRH | 30 | - | gripper |
| 236 | hand down | 오른손 내리는 모션 | WLRH | 30 | - | gripper |

## 5. 선행 모션이 있는 항목

`pre_id`가 지정된 모션은 선행 모션을 실행한 뒤에만 실행합니다.

| id | description | 선행 id | 선행 모션 |
| :-: | --- | :-: | --- |
| 20 | 배꼽 인사 | 19 | 양손 들며 인사 |
| 39 | 하이파이브 복귀 | 38 | 하이파이브 준비 |
| 41 | 물병 전달 | 40 | 왼손 들기 |
| 42 | 물병 전달 후 복귀 | 41 | 물병 전달 |
| 49 | 고개 아래에서 돌아오기 | 48 | 고개 숙이기 |
| 51 | 고개 왼쪽에서 끄덕임 | 50 | 왼쪽 바라보기 |
| 52 | 고개 왼쪽에서 복귀 | 51 | 고개 왼쪽에서 끄덕임 |
| 54 | 고개 오른쪽에서 끄덕임 | 53 | 오른쪽 바라보기 |
| 55 | 고개 오른쪽에서 복귀 | 54 | 고개 오른쪽에서 끄덕임 |
| 60 | 식탁 위 행주로 닦기 | 59 | 손 들고 식탁위 행주 잡기 |

## 6. 주의 사항

| 항목 | 내용 |
| --- | --- |
| 미사용 id 구간 | `blank_id` 값이 `72~224`이며 이 구간에는 모션이 없습니다. |
| 카테고리 범위 불일치 | `IFA (61~71)` 선언과 달리 실제 IFA는 61·70·71뿐이고, 62~64(야구), 65~67(하트), 68~69(시상식)가 이 범위를 차지합니다. |
| display_name 중복 | `scan`(22·23·24), `dumbbell`(25·26), `hi_five`(38·39), `head_right`(47·53), `return`(229·235), `turn waist`(227·233)은 이름이 같으므로 id로 지정해야 합니다. |
| 손 하드웨어 타입 | `omni_pro` 55건, `omni` 16건, `gripper` 12건입니다. `gripper` 항목(225~236)은 BS 계열 조작 모션입니다. |
| 미확인 호스트 | `cloid1`(192.168.50.130)은 수집 시점에 접속 불가(`No route to host`)여서 이 목록은 cloid2 기준입니다. |
| 갱신 방법 | 로봇 모션 목록이 바뀌면 `docker cp cloi-robo-claw:/ws/motion_list/motion_map.json` 으로 다시 받아 이 표를 재생성합니다. |
