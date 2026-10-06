#include <rclcpp/rclcpp.hpp>
#include <moveit/move_group_interface/move_group_interface.h>
#include <robo_claw_msgs/srv/execute_manipulator.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace robo_claw_core
{

class ManipulatorNode : public rclcpp::Node {
public:
  ManipulatorNode() : Node("robo_claw_manipulator_node") {
    // Reentrant Callback Group to allow nested call spin when using MoveGroupInterface
    callback_group_ = this->create_callback_group(rclcpp::CallbackGroupType::Reentrant);

    service_ = this->create_service<robo_claw_msgs::srv::ExecuteManipulator>(
      "~/execute_manipulator",
      std::bind(&ManipulatorNode::handle_execute, this, std::placeholders::_1, std::placeholders::_2),
      rmw_qos_profile_services_default,
      callback_group_
    );
    RCLCPP_INFO(this->get_logger(), "Manipulator bridge node initialization complete.");
  }

private:
  std::shared_ptr<moveit::planning_interface::MoveGroupInterface> get_move_group(const std::string& group_name) {
    auto it = move_groups_.find(group_name);
    if (it == move_groups_.end()) {
      RCLCPP_INFO(this->get_logger(), "Creating new MoveGroupInterface: group=%s", group_name.c_str());
      // MoveGroupInterface requires shared_ptr of Node to be passed
      auto move_group = std::make_shared<moveit::planning_interface::MoveGroupInterface>(shared_from_this(), group_name);
      move_group->setPlanningTime(5.0);
      move_groups_[group_name] = move_group;
      return move_group;
    }
    return it->second;
  }

  void handle_execute(
    const std::shared_ptr<robo_claw_msgs::srv::ExecuteManipulator::Request> request,
    std::shared_ptr<robo_claw_msgs::srv::ExecuteManipulator::Response> response)
  {
    RCLCPP_INFO(this->get_logger(), "Manipulation request received: type=%s, group=%s",
                request->type.c_str(), request->group_name.c_str());
    try {
      auto move_group = get_move_group(request->group_name);

      // 오래된 상태에서 planning하지 않도록 시작 상태를 현재 상태로 설정
      move_group->setStartStateToCurrentState();

      bool ok = false;
      if (request->type == "named_pose") {
        ok = move_group->setNamedTarget(request->target_name);
        if (!ok) {
          response->success = false;
          response->message = "named target 설정 실패: " + request->target_name;
          RCLCPP_WARN(this->get_logger(), "%s", response->message.c_str());
          return;
        }
      } else if (request->type == "joint") {
        std::vector<std::string> names = request->joint_names;
        std::vector<double> values = request->joint_values;
        if (names.size() != values.size()) {
          response->success = false;
          response->message = "joint_names와 joint_values의 크기가 일치하지 않습니다.";
          RCLCPP_WARN(this->get_logger(), "%s", response->message.c_str());
          return;
        }
        std::map<std::string, double> joint_targets;
        for (size_t i = 0; i < names.size(); ++i) {
          joint_targets[names[i]] = values[i];
        }
        ok = move_group->setJointValueTarget(joint_targets);
        if (!ok) {
          response->success = false;
          response->message = "joint value target 설정 실패.";
          RCLCPP_WARN(this->get_logger(), "%s", response->message.c_str());
          return;
        }
      } else if (request->type == "pose") {
        if (!request->end_effector_link.empty()) {
          move_group->setEndEffectorLink(request->end_effector_link);
        }
        ok = move_group->setPoseTarget(request->pose_target);
        if (!ok) {
          response->success = false;
          response->message = "pose target 설정 실패.";
          RCLCPP_WARN(this->get_logger(), "%s", response->message.c_str());
          return;
        }
      } else {
        response->success = false;
        response->message = "알 수 없는 요청 타입: " + request->type;
        RCLCPP_WARN(this->get_logger(), "%s", response->message.c_str());
        return;
      }

      moveit::planning_interface::MoveGroupInterface::Plan plan;
      auto plan_result = move_group->plan(plan);
      if (plan_result == moveit::core::MoveItErrorCode::SUCCESS) {
        auto exec_result = move_group->execute(plan);
        if (exec_result == moveit::core::MoveItErrorCode::SUCCESS) {
          response->success = true;
          response->message = "동작이 성공적으로 완료되었습니다.";
        } else {
          response->success = false;
          response->message = "실행 실패, 에러 코드: " + std::to_string(exec_result.val);
          RCLCPP_WARN(this->get_logger(), "%s", response->message.c_str());
        }
      } else {
        response->success = false;
        response->message = "Planning 실패, 에러 코드: " + std::to_string(plan_result.val);
        RCLCPP_WARN(this->get_logger(), "%s", response->message.c_str());
      }
    } catch (const std::exception& e) {
      response->success = false;
      response->message = std::string("예외 발생: ") + e.what();
      RCLCPP_ERROR(this->get_logger(), "Error while processing manipulation request: %s", e.what());
    }
  }

  rclcpp::CallbackGroup::SharedPtr callback_group_;
  rclcpp::Service<robo_claw_msgs::srv::ExecuteManipulator>::SharedPtr service_;
  std::map<std::string, std::shared_ptr<moveit::planning_interface::MoveGroupInterface>> move_groups_;
};

}  // namespace robo_claw_core

int main(int argc, char** argv) {
  rclcpp::init(argc, argv);
  try {
    auto node = std::make_shared<robo_claw_core::ManipulatorNode>();
    rclcpp::executors::MultiThreadedExecutor executor;
    executor.add_node(node);
    executor.spin();
  } catch (const std::exception& e) {
    RCLCPP_ERROR(rclcpp::get_logger("robo_claw_manipulator_node"), "Fatal error: %s", e.what());
    rclcpp::shutdown();
    return 1;
  }
  rclcpp::shutdown();
  return 0;
}
