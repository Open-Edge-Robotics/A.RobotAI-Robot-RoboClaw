#!/bin/bash
# nav2 안정 기동 스크립트 (실기 former0045/0047 공통)
#
# 적용하는 세 가지 핫픽스 (이미지/설치본 무수정, 바인드마운트만 사용)
# 1) use_composition:=False
#    - 기본은 모든 Nav2 노드를 component container 한 프로세스에 올려서,
#      AMCL SIGSEGV 한 번에 네비 스택 전체가 죽는다. 노드를 분리하면 장애가
#      국소화되고, 크래시 시 로그에 죽은 노드가 명시된다.
# 2) cmd_vel_smoothed -> /base_controller/cmd_vel_unstamped 리맵
#    - vendor launch 는 최종 속도 명령을 /cmd_vel 로만 보내는데, 이 로봇 베이스는
#      /base_controller/cmd_vel_unstamped 만 구독한다(원본은 리맵이 주석 처리됨).
# 3) 스캔 재스탬프 노드
#    - SICK 스캐너가 자체 시계로 header.stamp 를 채워 호스트 시계와 어긋나면
#      tf2 메시지 필터 드롭과 nav2_amcl 크래시가 발생한다. /scan 을 ROS 시간으로
#      다시 찍어 /scan_restamped 로 발행하고, AMCL/costmap 이 이를 사용한다.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
PATCH_DIR="$DIR/patch"
SRC_LAUNCH_DIR="$HOME/dev_ws/src/former_robot/former_navigation2/launch"

mkdir -p "$PATCH_DIR/rc"

# (3) 스캔 재스탬프 노드 스크립트
cat > "$PATCH_DIR/rc/scan_restamp.py" <<'PY'
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan


class ScanRestamp(Node):
    def __init__(self) -> None:
        super().__init__("scan_restamp")
        self.pub = self.create_publisher(LaserScan, "/scan_restamped", qos_profile_sensor_data)
        self.create_subscription(LaserScan, "/scan", self.cb, qos_profile_sensor_data)
        self.count = 0

    def cb(self, msg: LaserScan) -> None:
        msg.header.stamp = self.get_clock().now().to_msg()
        self.pub.publish(msg)
        self.count += 1
        if self.count % 300 == 0:
            self.get_logger().info(f"restamped {self.count} scans")


def main() -> None:
    rclpy.init()
    node = ScanRestamp()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
PY

# (2) velocity_smoother 최종 출력 리맵
python3 - "$SRC_LAUNCH_DIR/navigation_launch.py" "$PATCH_DIR/navigation_launch.py" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
s = open(src).read()
old = "[('cmd_vel', 'cmd_vel_nav'), ('cmd_vel_smoothed', 'cmd_vel')]"
new = "[('cmd_vel', 'cmd_vel_nav'), ('cmd_vel_smoothed', '/base_controller/cmd_vel_unstamped')]"
if old not in s and "/base_controller/cmd_vel_unstamped" not in s:
    raise SystemExit(f"velocity_smoother remap 패턴을 찾지 못했습니다: {src}")
open(dst, "w").write(s.replace(old, new))
PY

# (3) bringup 에 재스탬프 노드 실행 추가
python3 - "$SRC_LAUNCH_DIR/bringup_launch.py" "$PATCH_DIR/bringup_launch.py" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
s = open(src).read()
if "scan_restamp.py" not in s:
    if "ExecuteProcess" not in s:
        s = s.replace(
            "from launch.conditions import IfCondition",
            "from launch.actions import ExecuteProcess\nfrom launch.conditions import IfCondition",
            1,
        )
    marker = "    bringup_cmd_group = GroupAction(["
    if marker not in s:
        raise SystemExit(f"bringup group 위치를 찾지 못했습니다: {src}")
    inject = (
        "    scan_restamp = ExecuteProcess(\n"
        "        cmd=['python3', '/opt/rc/scan_restamp.py'],\n"
        "        output='screen',\n"
        "    )\n\n"
    )
    s = s.replace(marker, inject + marker, 1)
    # GroupAction 목록 안에 실행 액션 추가
    s = s.replace("    bringup_cmd_group = GroupAction([\n", "    bringup_cmd_group = GroupAction([\n        scan_restamp,\n", 1)
open(dst, "w").write(s)
PY

export RC_EXTRA_VOLUMES="-v $PATCH_DIR/bringup_launch.py:/root/dev_ws/install/former_navigation2/share/former_navigation2/launch/bringup_launch.py:ro -v $PATCH_DIR/navigation_launch.py:/root/dev_ws/install/former_navigation2/share/former_navigation2/launch/navigation_launch.py:ro -v $PATCH_DIR/rc:/opt/rc:ro"

exec "$DIR/run_former_env.sh" nav2 --daemon -m "${RC_MAP_FILE:-map_konjiam_L_107-1.yaml}" use_composition:=False "$@"
