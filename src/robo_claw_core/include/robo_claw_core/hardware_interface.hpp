#pragma once

#include <string>
#include <memory>
#include <functional>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/msg/joint_state.hpp"
#include "geometry_msgs/msg/twist.hpp"
#include "robo_claw_msgs/msg/agent_status.hpp"

namespace robo_claw_core
{

  /// 하드웨어 조인트 정보
struct JointInfo
{
  std::string name;
  double position{0.0};
  double velocity{0.0};
  double effort{0.0};
};

  /// 하드웨어 인터페이스 — 센서/액추에이터 추상화 레이어
class HardwareInterface
{
public:
  using SharedPtr = std::shared_ptr<HardwareInterface>;
  using StatusCallback = std::function<void(const std::string &)>;

  explicit HardwareInterface(rclcpp::Node::SharedPtr node);
  ~HardwareInterface() = default;

    /// 초기화 (파라미터 로드 및 조인트 등록)
  bool init();

    /// 조인트 상태 읽기 (필요 시 명시적 업데이트)
  void read();

    /// 조인트 명령 쓰기
  void write();

    /// 조인트 정보 조회
  const JointInfo & get_joint(const std::string & name) const;

    /// 조인트 속도 명령
  void set_joint_velocity(const std::string & name, double velocity);

    /// 비상 정지
  void emergency_stop();

    /// 현재 비상 정지 상태 확인
  bool is_emergency_stopped() const {return emergency_stop_;}

    /// 에러 콜백 등록
  void set_error_callback(StatusCallback cb) {error_callback_ = std::move(cb);}

    /// 등록된 모든 조인트 이름 반환
  std::vector<std::string> get_joint_names() const;

    /// 베이스 속도 명령 설정 (write()에서 발행됨)
  void set_base_velocity(double linear, double angular);

    /// 하드웨어 상태 JSON 반환
  std::string get_status_json() const;

private:
    /// 조인트 상태 구독 콜백
  void on_joint_states(const sensor_msgs::msg::JointState::SharedPtr msg);
  void zero_motion_commands();

  rclcpp::Node::SharedPtr node_;
  std::unordered_map<std::string, JointInfo> joints_;
  std::unordered_set<std::string> warned_unknown_joint_names_;
  bool initialized_{false};
  bool emergency_stop_{false};
  bool joint_state_stale_{false};
  bool command_watchdog_active_{false};
  bool base_command_active_{false};
  bool discover_joints_from_states_{false};
  StatusCallback error_callback_;

    // ROS 2 인터페이스
  rclcpp::Subscription<sensor_msgs::msg::JointState>::SharedPtr joint_state_sub_;
  rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr cmd_vel_pub_;

    // 현재 제어 명령 상태
  geometry_msgs::msg::Twist current_cmd_vel_;
  std::string cmd_vel_topic_{"/cmd_vel"};
  double joint_state_timeout_sec_{1.0};
  double command_timeout_sec_{0.5};
  int64_t joint_state_timeout_ns_{1000000000};
  int64_t command_timeout_ns_{500000000};
  int64_t last_joint_state_ns_{0};
  int64_t last_command_ns_{0};
};

} // namespace robo_claw_core
