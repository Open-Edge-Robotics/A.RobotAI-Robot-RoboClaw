#include "robo_claw_core/hardware_interface.hpp"
#include "robo_claw_core/json_escape.hpp"

#include <algorithm>
#include <cmath>
#include <sstream>
#include <stdexcept>

#include "geometry_msgs/msg/twist.hpp"
#include "sensor_msgs/msg/joint_state.hpp"
#include "urdf/model.h"

namespace robo_claw_core
{

namespace
{
bool is_movable_joint_type(int type)
{
  return type == urdf::Joint::REVOLUTE ||
         type == urdf::Joint::CONTINUOUS ||
         type == urdf::Joint::PRISMATIC ||
         type == urdf::Joint::PLANAR ||
         type == urdf::Joint::FLOATING;
}

std::vector<std::string> extract_joint_names_from_urdf(
  const std::string & robot_description)
{
  if (robot_description.empty()) {
    return {};
  }

  urdf::Model model;
  if (!model.initString(robot_description)) {
    return {};
  }

  std::vector<std::string> joint_names;
  joint_names.reserve(model.joints_.size());
  for (const auto &[joint_name, joint] : model.joints_) {
    if (!joint || !is_movable_joint_type(joint->type)) {
      continue;
    }
    joint_names.push_back(joint_name);
  }

  std::sort(joint_names.begin(), joint_names.end());
  return joint_names;
}
}   // namespace

HardwareInterface::HardwareInterface(rclcpp::Node::SharedPtr node)
: node_(node)
{
  RCLCPP_INFO(node_->get_logger(), "Starting HardwareInterface initialization");
}

bool HardwareInterface::init()
{
  if (initialized_) {
    RCLCPP_WARN(node_->get_logger(), "Already initialized");
    return true;
  }

  joints_.clear();
  warned_unknown_joint_names_.clear();

  std::vector<std::string> joint_names;
  std::string robot_description;
  try {
    node_->get_parameter_or(
          "joint_state_timeout_sec", joint_state_timeout_sec_, 1.0);
    node_->get_parameter_or(
          "command_timeout_sec", command_timeout_sec_, 0.5);
    joint_state_timeout_ns_ = static_cast<int64_t>(joint_state_timeout_sec_ * 1e9);
    command_timeout_ns_ = static_cast<int64_t>(command_timeout_sec_ * 1e9);
    node_->get_parameter("joint_names", joint_names);
    node_->get_parameter_or("robot_description", robot_description, std::string(""));

    const auto urdf_joint_names = extract_joint_names_from_urdf(robot_description);
    const std::vector<std::string> *selected_joint_names = nullptr;
    const char *joint_source = nullptr;

    if (!urdf_joint_names.empty()) {
      selected_joint_names = &urdf_joint_names;
      joint_source = "robot_description";
    } else if (!robot_description.empty()) {
      RCLCPP_WARN(
            node_->get_logger(),
            "Failed to parse 'robot_description'. Falling back to manual joint_names or joint_states-based discovery.");
    }

    if (selected_joint_names == nullptr && !joint_names.empty()) {
      selected_joint_names = &joint_names;
      joint_source = "joint_names";
    }

    if (selected_joint_names == nullptr) {
      discover_joints_from_states_ = true;
      RCLCPP_WARN(
            node_->get_logger(),
            "No joint configuration to register. Joints will be discovered dynamically from the first joint_states message.");
    } else {
      discover_joints_from_states_ = false;
      RCLCPP_INFO(
            node_->get_logger(), "Loaded joint configuration from %s (%zu joints)",
            joint_source, selected_joint_names->size());
      for (const auto & name : *selected_joint_names) {
        joints_[name] = JointInfo{name, 0.0, 0.0, 0.0};
        RCLCPP_INFO(node_->get_logger(), "Registered joint: %s", name.c_str());
      }
    }
  } catch (const std::exception & e) {
    RCLCPP_ERROR(node_->get_logger(), "Error while loading parameters: %s", e.what());
    return false;
  }

    // 실제 로봇(Stretch 3 등)의 상태를 받기 위한 구독 설정
  joint_state_sub_ = node_->create_subscription<sensor_msgs::msg::JointState>(
        "/joint_states", 10,
        std::bind(&HardwareInterface::on_joint_states, this, std::placeholders::_1));

    // 제어 명령 퍼블리셔 설정
  node_->get_parameter_or("cmd_vel_topic", cmd_vel_topic_, std::string("/cmd_vel"));
  cmd_vel_pub_ = node_->create_publisher<geometry_msgs::msg::Twist>(cmd_vel_topic_, 10);
  RCLCPP_INFO(node_->get_logger(), "Created control command publisher: %s", cmd_vel_topic_.c_str());

  initialized_ = true;
  RCLCPP_INFO(node_->get_logger(),
                "HardwareInterface initialization complete (%zu joints)", joints_.size());
  return true;
}

void HardwareInterface::on_joint_states(const sensor_msgs::msg::JointState::SharedPtr msg)
{
  if (!initialized_) {
    return;
  }

  last_joint_state_ns_ = node_->now().nanoseconds();
  joint_state_stale_ = false;

    // 수신된 조인트 상태로 내부 정보 업데이트
  for (size_t i = 0; i < msg->name.size(); ++i) {
    auto it = joints_.find(msg->name[i]);
    if (it == joints_.end()) {
      if (discover_joints_from_states_) {
        const auto [inserted_it, inserted] = joints_.emplace(
              msg->name[i], JointInfo{msg->name[i], 0.0, 0.0, 0.0});
        it = inserted_it;
        if (inserted) {
          RCLCPP_INFO(
                node_->get_logger(), "Registered joint from joint_states: %s",
                msg->name[i].c_str());
        }
      } else if (warned_unknown_joint_names_.insert(msg->name[i]).second) {
        RCLCPP_WARN(
              node_->get_logger(),
              "Received state for an undefined joint. Ignoring: %s",
              msg->name[i].c_str());
      }
    }

    if (it != joints_.end()) {
      if (msg->position.size() > i) {
        it->second.position = msg->position[i];
      }
      if (msg->velocity.size() > i) {
        it->second.velocity = msg->velocity[i];
      }
      if (msg->effort.size() > i) {
        it->second.effort = msg->effort[i];
      }
    }
  }
}

void HardwareInterface::read()
{
  if (!initialized_) {
    return;
  }

  const int64_t now_ns = node_->now().nanoseconds();

  if (joint_state_timeout_sec_ > 0.0 && last_joint_state_ns_ > 0) {
    const bool stale = (now_ns - last_joint_state_ns_) > joint_state_timeout_ns_;
    if (stale && !joint_state_stale_) {
      joint_state_stale_ = true;
      RCLCPP_WARN(
            node_->get_logger(),
            "joint_states has not been received for over %.2f seconds. Hardware state is stale.",
            joint_state_timeout_sec_);
      if (error_callback_) {
        error_callback_("joint_states timeout");
      }
    } else if (!stale) {
      joint_state_stale_ = false;
    }
  }

  if (!emergency_stop_ && command_timeout_sec_ > 0.0 && last_command_ns_ > 0) {
    const bool command_stale = (now_ns - last_command_ns_) > command_timeout_ns_;
    if (command_stale) {
      if (!command_watchdog_active_) {
        RCLCPP_WARN(
              node_->get_logger(),
              "Control command watchdog triggered: no new command for over %.2f seconds, stopping.",
              command_timeout_sec_);
        zero_motion_commands();
        if (cmd_vel_pub_) {
          cmd_vel_pub_->publish(current_cmd_vel_);
        }
      }
      base_command_active_ = false;
      command_watchdog_active_ = true;
    }
  }
}

void HardwareInterface::write()
{
  if (!initialized_ || emergency_stop_) {
    return;
  }

  if (!base_command_active_) {
    return;
  }

    // 베이스 제어 명령 발행. 외부 Nav2/cmd_vel을 덮어쓰지 않도록
    // RoboClaw 내부 명령이 있을 때만 발행한다.
  cmd_vel_pub_->publish(current_cmd_vel_);

    // TODO: 관절 궤적(Arm/Lift/Head) 제어 명령 발행
    // Stretch 3 등은 Action Client를 통해 궤적을 전송하는 것이 일반적임
}

void HardwareInterface::set_base_velocity(double linear, double angular)
{
  if (emergency_stop_) {
    return;
  }

  current_cmd_vel_.linear.x = linear;
  current_cmd_vel_.angular.z = angular;
  last_command_ns_ = node_->now().nanoseconds();
  command_watchdog_active_ = false;
  base_command_active_ = true;
}

const JointInfo & HardwareInterface::get_joint(const std::string & name) const
{
  auto it = joints_.find(name);
  if (it == joints_.end()) {
    throw std::runtime_error("존재하지 않는 조인트: " + name);
  }
  return it->second;
}

std::vector<std::string> HardwareInterface::get_joint_names() const
{
  std::vector<std::string> names;
  for (const auto & pair : joints_) {
    names.push_back(pair.first);
  }
  return names;
}

void HardwareInterface::set_joint_velocity(
  const std::string & name,
  double velocity)
{
  if (emergency_stop_) {
    return;
  }

  auto it = joints_.find(name);
  if (it == joints_.end()) {
    return;
  }

    // 물리적 가드레일 (Stretch 3 등에 맞춰 조정 가능)
  const double max_vel = 2.0;
  velocity = std::clamp(velocity, -max_vel, max_vel);

  it->second.velocity = velocity;
  last_command_ns_ = node_->now().nanoseconds();
  command_watchdog_active_ = false;

    // TODO: 여기서 명령을 바로 퍼블리시하거나 write()에서 일괄 처리
}

std::string HardwareInterface::get_status_json() const
{
  std::ostringstream oss;
  oss << "{"
      << json_bool_field("initialized", initialized_) << ","
      << json_bool_field("emergency_stop", emergency_stop_) << ","
      << json_bool_field("joint_state_stale", joint_state_stale_) << ","
      << json_bool_field("command_watchdog_active", command_watchdog_active_) << ","
      << json_bool_field("base_command_active", base_command_active_) << ","
      << json_string_field("cmd_vel_topic", cmd_vel_topic_) << ","
      << json_number_field("joint_count", joints_.size());
  if (last_joint_state_ns_ > 0) {
    const double age_sec =
      static_cast<double>(node_->now().nanoseconds() - last_joint_state_ns_) / 1e9;
    oss << "," << json_number_field("last_joint_state_age_sec", age_sec);
  }
  oss << "}";
  return oss.str();
}

void HardwareInterface::zero_motion_commands()
{
  current_cmd_vel_.linear.x = 0.0;
  current_cmd_vel_.linear.y = 0.0;
  current_cmd_vel_.linear.z = 0.0;
  current_cmd_vel_.angular.x = 0.0;
  current_cmd_vel_.angular.y = 0.0;
  current_cmd_vel_.angular.z = 0.0;

  for (auto &[name, joint] : joints_) {
    (void)name;
    joint.velocity = 0.0;
    joint.effort = 0.0;
  }
}

void HardwareInterface::emergency_stop()
{
  emergency_stop_ = true;
  base_command_active_ = false;

  zero_motion_commands();
  cmd_vel_pub_->publish(current_cmd_vel_);
  RCLCPP_ERROR(node_->get_logger(), "Emergency stop activated!");
  if (error_callback_) {
    error_callback_("비상 정지 활성화");
  }
}

} // namespace robo_claw_core
