#include <chrono>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp_action/rclcpp_action.hpp"
#include "sensor_msgs/msg/joint_state.hpp"
#include "std_msgs/msg/string.hpp"

#include "robo_claw_core/hardware_interface.hpp"
#include "robo_claw_core/sensor_manager.hpp"
#include "robo_claw_msgs/msg/agent_status.hpp"
#include "robo_claw_msgs/srv/query_state.hpp"

using namespace std::chrono_literals;

namespace robo_claw_core
{

namespace
{
SensorType infer_sensor_type(const std::string & name, const std::string & topic)
{
  const std::string combined = name + " " + topic;
  if (combined.find("imu") != std::string::npos) {
    return SensorType::IMU;
  }
  if (combined.find("scan") != std::string::npos ||
    combined.find("lidar") != std::string::npos)
  {
    return SensorType::LIDAR;
  }
  if (combined.find("camera") != std::string::npos ||
    combined.find("image") != std::string::npos)
  {
    return SensorType::CAMERA;
  }
  return SensorType::UNKNOWN;
}
}   // namespace

  /// 코어 노드 — HardwareInterface/SensorManager 통합 관리
class CoreNode : public rclcpp::Node
{
public:
  CoreNode()
  : Node("robo_claw_core_node")
  {
    // 파라미터 선언
    declare_parameter("loop_rate_hz", 50.0);
    declare_parameter("agent_id", std::string("robo_claw_agent"));
    declare_parameter("command_timeout_sec", 0.5);
    declare_parameter("joint_state_timeout_sec", 1.0);
    declare_parameter("sensor_timeout_sec", 2.0);
    declare_parameter("cmd_vel_topic", std::string("/cmd_vel"));

    // 조인트/센서 구성을 위한 빈 파라미터 선언 (YAML에서 채워짐)
    declare_parameter("joint_names", std::vector<std::string>{});
    declare_parameter("robot_description", std::string(""));
    declare_parameter("sensor_names", std::vector<std::string>{});
    // sensor_topics 등은 각 센서 이름별로 조회 가능
  }

  /// shared_ptr 완성 후 호출해야 하는 초기화 (shared_from_this 사용)
  void initialize()
  {
    double rate_hz = get_parameter("loop_rate_hz").as_double();
    agent_id_ = get_parameter("agent_id").as_string();

    // 하드웨어/센서 초기화
    hw_ = std::make_shared<HardwareInterface>(shared_from_this());
    sen_ = std::make_shared<SensorManager>(shared_from_this());

    hw_->set_error_callback([this](const std::string & msg) {
      RCLCPP_ERROR(get_logger(), "[HW error] %s", msg.c_str());
      publish_status(robo_claw_msgs::msg::AgentStatus::ERROR, msg);
    });

    if (!hw_->init()) {
      RCLCPP_FATAL(get_logger(), "Hardware initialization failed");
      throw std::runtime_error("HardwareInterface init failed");
    }

    // 센서 등록 (파라미터 기반)
    std::vector<std::string> sensor_names;
    get_parameter("sensor_names", sensor_names);

    if (sensor_names.empty()) {
      RCLCPP_INFO(get_logger(), "No sensor parameters found; registering default sensors.");
      sen_->register_sensor("imu", SensorType::IMU, "/imu/data");
      sen_->register_sensor("lidar", SensorType::LIDAR, "/scan");
    } else {
      for (const auto & name : sensor_names) {
        // 각 센서별 상세 파라미터 읽기 (예: sensor_config.lidar.topic)
        std::string topic = "/default_topic";

        // 간단한 파라미터 구조 사용 (sensor_topics.lidar)
        declare_parameter("sensor_topics." + name, topic);
        get_parameter("sensor_topics." + name, topic);

        sen_->register_sensor(name, infer_sensor_type(name, topic), topic);
      }
    }

    // 퍼블리셔
    status_pub_ = create_publisher<robo_claw_msgs::msg::AgentStatus>(
      "~/status", rclcpp::QoS(10));
    joint_state_pub_ = create_publisher<sensor_msgs::msg::JointState>(
      "~/joint_states", rclcpp::QoS(10));

    // 서비스
    query_srv_ = create_service<robo_claw_msgs::srv::QueryState>(
      "~/query_state",
      [this](const robo_claw_msgs::srv::QueryState::Request::SharedPtr req,
        robo_claw_msgs::srv::QueryState::Response::SharedPtr res) {
        handle_query(req, res);
      });

    // 제어 루프 타이머
    auto period = std::chrono::duration<double>(1.0 / rate_hz);
    timer_ = create_wall_timer(period, [this]() { control_loop(); });

    publish_status(robo_claw_msgs::msg::AgentStatus::IDLE, "시작");
    RCLCPP_INFO(get_logger(), "CoreNode ready (%.1f Hz)", rate_hz);
  }

private:
  // 제어 루프 (read → write)
  void control_loop()
  {
    hw_->read();
    sen_->refresh_activity();
    hw_->write();
  }

  // 상태 퍼블리시 헬퍼
  void publish_status(
    uint8_t state, const std::string & msg,
    const std::string & skill = "")
  {
    auto status = robo_claw_msgs::msg::AgentStatus();
    status.header.stamp = now();
    status.state = state;
    status.message = msg;
    status.current_skill = skill;
    status.agent_id = agent_id_;
    status_pub_->publish(status);
  }

  // 상태 쿼리 서비스 핸들러
  void handle_query(
    const robo_claw_msgs::srv::QueryState::Request::SharedPtr /*req*/,
    robo_claw_msgs::srv::QueryState::Response::SharedPtr res)
  {
    res->success = true;
    res->state_json =
      std::string("{\"hardware\":") + hw_->get_status_json() +
      ",\"sensors\":" + sen_->get_status_json() + "}";
    res->message = "OK";
  }

  std::string agent_id_;
  std::shared_ptr<HardwareInterface> hw_;
  std::shared_ptr<SensorManager> sen_;

  rclcpp::Publisher<robo_claw_msgs::msg::AgentStatus>::SharedPtr status_pub_;
  rclcpp::Publisher<sensor_msgs::msg::JointState>::SharedPtr joint_state_pub_;
  rclcpp::Service<robo_claw_msgs::srv::QueryState>::SharedPtr query_srv_;
  rclcpp::TimerBase::SharedPtr timer_;
};

} // namespace robo_claw_core

int main(int argc, char **argv)
{
  rclcpp::init(argc, argv);
  try {
    auto node = std::make_shared<robo_claw_core::CoreNode>();
    // shared_ptr 생성 완료 후 initialize() 호출해야 shared_from_this() 사용 가능
    node->initialize();
    rclcpp::spin(node);
  } catch (const std::exception & e) {
    RCLCPP_FATAL(rclcpp::get_logger("main"), "Exception occurred: %s", e.what());
  }
  rclcpp::shutdown();
  return 0;
}
