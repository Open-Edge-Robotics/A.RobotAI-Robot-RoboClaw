# CLOiD EtherCAT 라이선스 시간 제한과 관절 제어 중단

이 문서는 CLOiD 2호기에서 약 62분마다 반복된 tofu 관절 제어 중단의 원인, 실기 검증 근거와 운영 점검 절차를 설명합니다. 2026-11-09~10 로그와 1·2호기 비교 결과를 기준으로 작성합니다.

## 1. 요약

**상태는 미해결이며, 2호기 EtherCAT NIC용 유효한 라이선스 발급이 필요합니다.**

- 2호기의 실제 제어기 설정에서 `acontis_license`가 비어 있음을 확인합니다.
- Acontis EC-Master의 Protected 버전은 유효한 키가 없으면 약 1시간 후 프레임 송신을 중단합니다. 실기 로그에서 기동 약 62분 뒤 중단을 5회 확인합니다.
- 배포 라이브러리에 내장된 후보 27개를 실제 2호기 MAC에 대해 검사한 결과, 모두 `EC_E_LICENSE_MISSING`으로 거부됩니다. 라이선스 설정은 변경하지 않습니다.
- 재시작은 시간 제한을 일시적으로 초기화할 뿐이며 해결책으로 취급하지 않습니다.
- Docker가 `running`·`healthy`여도 EtherCAT과 관절 제어기는 중단될 수 있으므로, 관절 상태와 TF를 함께 확인합니다.

## 2. 기기와 설정 경로

| 항목 | 1호기 | 2호기 |
| --- | --- | --- |
| SSH 별칭 | `cloid1`, `cloid01` | `cloid2`, `cloid02` |
| IP | `192.168.50.131` | `192.168.50.105` |
| EtherCAT NIC MAC | `00-18-1A-F3-3F-61` | `00-18-1A-F3-3E-53` |
| 실제 `acontis_license` | 값이 설정되어 있음을 확인합니다. | 빈 값임을 확인합니다. |
| 2026-11-10 최종 비교 | 재시작 없이 약 137분 운용하며 관절 상태·TF가 정상임을 확인합니다. | 약 62분 뒤 관절 상태·팔 TF가 중단됨을 확인합니다. |

두 기기 모두 `cloi-tofu` 컨테이너를 사용합니다. 2호기의 NIC는 Intel I210이며 `atemsys_pci`에 바인딩되어 있으므로, 일반 `ip link` 목록에 EtherCAT 인터페이스가 없다는 이유만으로 장애로 판단하지 않습니다.

2호기의 설정 전달 경로는 다음과 같습니다.

| 경로 | 역할과 확인 결과 |
| --- | --- |
| 호스트 `/nv_robot/config/tofu/` | 컨테이너 `/tofu/config/`에 마운트되는 영구 설정 디렉터리입니다. |
| `/tofu/config/identity.yaml` | 기기별 `license`를 전달하는 파일입니다. 조사 당시 2호기에는 파일이 없습니다. |
| `/tofu/.config/identity.yaml` | 기본값과 영구 설정을 병합한 런타임 파일이며, 조사 당시 `license`가 비어 있음을 확인합니다. 직접 수정 대상으로 사용하지 않습니다. |
| `/tofu/config/system.yaml` | CPU affinity와 EtherCAT 어댑터 설정 등을 보관합니다. |
| `/ws/install/tofu_control/share/tofu_control/launch/tofu_control.launch.py` | 2호기 배포본에서 `identity.yaml`의 `license`로 `system_cfg["acontis"]["license"]`를 덮어씁니다. |

따라서 2호기에서는 `system.yaml`의 라이선스 값만 변경하는 것으로 적용을 보장하지 않습니다. 실제 제어기의 `robot_description`에 전달된 `acontis_license`를 값 노출 없이 확인합니다.

## 3. 장애 증거

### 3.1 Protected 버전 경고와 반복 주기

2호기의 기동 로그에서 다음 경고를 확인합니다.

```text
Protected version, stop sending ethernet frames after 60 minutes if not licensed!
```

아래 시각은 모두 KST이며, 컨테이너 시작 시각이 아니라 `EtherCAT is up` 로그부터 첫 read timeout까지의 경과 시간을 계산합니다.

| 날짜 | EtherCAT 기동 | 첫 read timeout | 경과 시간 |
| --- | --- | --- | --- |
| 2026-11-09 | 12:36:32 | 13:38:30 | 61.970분 |
| 2026-11-09 | 14:08:00 | 15:10:00 | 61.986분 |
| 2026-11-09 | 15:40:36 | 16:42:35 | 61.986분 |
| 2026-11-10 | 13:08:33 | 14:10:28 | 61.921분 |
| 2026-11-10 | 14:55:29 | 15:57:29 | 61.987분 |

시각은 초 단위로 표시하고 경과 시간은 원본 로그의 소수 초로 계산합니다. 운영자가 강제로 중지한 약 15분 경과 사례와 유지보수 중 재시작은 자연 장애 횟수에서 제외합니다.

### 3.2 통신 중단 이후의 연쇄 오류

2026-11-10 14:10:28 로그에서 다음 순서를 확인합니다.

1. cyclic frame의 `EC_NOTIFY_FRAME_RESPONSE_ERROR`와 acyclic frame timeout이 발생합니다.
2. 첫 slave 1001부터 장치가 응답하지 않고 `0 slaves found`가 발생합니다.
3. `EtherCAT cycle did not arrive within 5000 us`가 발생합니다.
4. `omnihand_controller`, `joint_impedance_controller`, `joint_state_broadcaster`, `joint_motion_controller`가 비활성화됩니다.
5. 관절 상태 발행과 팔·허리 TF 갱신이 중단됩니다.

15:57:29 재발에서도 같은 read timeout과 제어기 비활성화를 확인합니다. 6초 관찰에서 `/joint_states` 수신은 0건이며 팔·허리 TF도 수신되지 않습니다. 베이스 TF는 계속 수신되므로, 로봇 전체 ROS 통신 단절과 구분합니다.

`System overload`와 `atemsys pci_release`는 원인으로 단정하지 않습니다. 첫 사례의 `pci_release`는 통신 실패와 제어기 차단 이후에 발생하며, 같은 시각의 선행 NIC reset·PCIe AER·OOM을 뒷받침하는 로그는 확인하지 못합니다.

### 3.3 실제 MAC을 사용한 라이선스 검사

배포본 `/ws/install/ethercat_hardware_interface/lib/libethercat_hardware_interface_plugin.so`의 `LicenseList`에서 후보 27개를 확인합니다.

정지된 로봇의 유지보수 상태에서 tofu를 잠시 중지하고 다음 범위로 검사합니다.

1. 배포 SDK로 `emInitMaster()`를 호출합니다.
2. `emGetSrcMacAddress()` 결과가 2호기 MAC과 일치함을 확인합니다.
3. 후보마다 `emSetLicenseKey()`를 호출하고 반환 코드만 기록합니다.
4. `emDeinitMaster()`로 진단 인스턴스를 종료하고 tofu를 다시 기동합니다.

진단 프로그램은 네트워크 구성, 버스 운전 상태 전환, cyclic job 또는 모터 명령을 호출하지 않습니다. **후보 27개 모두 `EC_E_LICENSE_MISSING`(`0x98110039`)으로 거부됨을 확인합니다.** 임시 키 사본은 삭제하며, 영구 설정에는 어떤 후보도 등록하지 않습니다.

검사 후 관절 상태 약 100Hz와 팔·베이스 TF 복구를 확인하지만, 라이선스 제한이 남아 있어 15:57:29에 다시 중단됩니다.

## 4. Docker 상태와 실제 제어 상태의 차이

2호기는 15:57:29 장애 이후에도 Docker `running`·`healthy`, restart count 0, OOM false로 표시됩니다. 실제로는 대부분의 관절 제어기와 `joint_state_broadcaster`가 `inactive`입니다.

배포본 `/ws/healthcheck_tofu.sh`는 일반 모드에서 `/tofu_stage/status` 토픽의 **존재 여부**만 검사합니다. EtherCAT 상태, 토픽 데이터의 최신성 또는 제어기 활성 상태는 이 결과만으로 판단할 수 없습니다.

Docker health나 `/safety/motor_enable` 값만으로 정상 운용을 판단하지 않습니다. 장애 당시 `motor_enable=true`, `estop=false`도 관측되므로, 실제 관절 피드백과 제어기 상태를 우선 확인합니다.

## 5. Nav2 영향과 별도 문 통과 문제

tofu는 다음 경로로 Nav2 충돌 판단에 영향을 줍니다.

```text
tofu joint states -> robot_state_publisher TF -> dynamic footprint -> Nav2 collision checks
```

팔 TF가 멈추면 동적 footprint 계산에서 future extrapolation과 `Compute link points failed. Abort.`가 반복됩니다. 관절 상태가 없는 동안 상체 동작과 새로운 주행 요청을 보류합니다.

다만 2026-11-10 거실 이동의 첫 진척 실패는 14:09:57이며, tofu 통신 중단 14:10:28보다 먼저 발생합니다. TF 복구 후에도 정체가 남고, 수동 문 통과 후 주행이 재개되면서 진척 실패가 추가 관측됩니다. 따라서 **라이선스 문제를 문 통과 실패의 유일한 원인으로 취급하지 않습니다.**

별도로 MPPI의 `vx_min=-0.05`와 velocity smoother의 `min_velocity[0]=0.0` 불일치를 확인합니다. 원격 조작 구간과 Nav2 전용 출력은 구분하며, 후진 허용 정책·문 주변 costmap·경로 추종 문제는 독립적으로 검증합니다. 안전 여유나 충돌 검사를 임의로 축소하지 않습니다.

## 6. 읽기 전용 점검 절차

아래 명령은 `ssh-remote-dev`가 설치된 진단 PC에서 실행합니다. `cloid1`으로 별칭을 바꾸면 1호기를 같은 방식으로 확인합니다.

1. 기동 시각과 컨테이너 상태를 확인합니다.

   ```bash
   python3 ~/.agents/skills/ssh-remote-dev/scripts/ssh_runner.py exec \
     -H cloid2 --host-shell -- \
     'date -Is; docker inspect cloi-tofu --format "status={{.State.Status}} started={{.State.StartedAt}} restarts={{.RestartCount}}"'
   ```

2. 최신 safety 로그에서 read timeout과 제어기 비활성화를 확인합니다. 로그는 호스트 `/opt/lge/logs/tofu/`와 컨테이너 `/tofu/logs/`에서 확인합니다.

   ```bash
   python3 ~/.agents/skills/ssh-remote-dev/scripts/ssh_runner.py exec \
     -H cloid2 --host-shell -- \
     'latest=$(ls -t /opt/lge/logs/tofu/safety_*.log | head -n 1); grep -nE "EtherCAT cycle did not arrive|Deactivating following controllers|EtherCAT stopped" "$latest" | tail -n 12'
   ```

3. ROS 환경을 source한 뒤 제어기와 관절 상태를 확인합니다. 아래 `--allow-mutating`은 SSH 도구가 `source`를 실행 코드로 분류하기 때문에 사용하며, 실제 진단 명령은 조회만 수행합니다.

   ```bash
   python3 ~/.agents/skills/ssh-remote-dev/scripts/ssh_runner.py exec \
     -H cloid2 -c cloi-tofu --allow-mutating -- \
     'source /opt/ros/jazzy/setup.bash; source /ws/install/setup.bash; timeout 8s ros2 control list_controllers -c /controller_manager; timeout 6s ros2 topic hz /joint_states'
   ```

4. `/joint_states`가 약 100Hz로 수신되는지와 팔·허리 TF의 시각이 현재 시각으로 갱신되는지 함께 확인합니다. 노드나 publisher가 목록에 존재하는 것만으로 정상으로 판단하지 않습니다.

## 7. 해결 조건과 안전 주의사항

1. 2호기 EtherCAT NIC MAC `00-18-1A-F3-3E-53`에 대응하는 유효한 키를 Acontis 또는 라이선스 담당자로부터 발급받습니다.
2. 영구 설정 `/nv_robot/config/tofu/identity.yaml`의 `license`로 전달하며, 기존 기기 정보는 보존하고 파일 권한은 `0600`으로 제한합니다. 1호기 키를 다른 MAC의 2호기에 복사하지 않습니다.
3. 로봇의 안전한 정지·관절 지지 상태를 확보한 유지보수 시간에 적용하고, 실제 SDK의 키 승인 여부와 런타임 전달 상태를 값 노출 없이 확인합니다.
4. 마지막 EtherCAT 기동 이후 **70분 이상 연속 운용**하면서 read timeout, 장치 소실, 관절 상태·TF 중단이 재발하지 않는지 검증합니다.

키 값은 로그·문서·Git에 기록하거나 채팅으로 전달하지 않습니다. 본 문서 작성 시점에는 유효한 2호기 키가 없으므로, 라이선스 적용과 적용 후 70분 검증은 미완료입니다. 반복 재시작을 정식 해결책으로 사용하지 않습니다.

## 8. 참고 자료

- [CLOiD 연동 가이드](CLOiD_GUIDE.md)를 참고합니다.
- [Acontis Protected 버전과 MAC 기반 라이선스](https://public.acontis.com/manuals/EC-Master/3.3/html/ec-master-class-b/intro_protected-version.html)를 참고합니다. 조사한 배포 SDK는 EC-Master V3.2.3.04이며, 이 문서의 실제 승인 결과는 해당 배포 SDK로 검사합니다.
- [Acontis API 문서](https://public.acontis.com/manuals/EC-Master/3.3/html/ec-master-class-b/api_general_functions.html)의 `emSetLicenseKey()` 초기화 후·구성 전 호출 조건과 오류 코드를 참고합니다.
