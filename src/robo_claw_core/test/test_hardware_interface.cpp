#include <gtest/gtest.h>
#include <chrono>
#include <memory>
#include <thread>

#include "rclcpp/rclcpp.hpp"
#include "geometry_msgs/msg/twist.hpp"
#include "robo_claw_core/hardware_interface.hpp"
#include "robo_claw_core/sensor_manager.hpp"
#include "sensor_msgs/msg/laser_scan.hpp"

using namespace robo_claw_core;

class HardwareInterfaceTest : public ::testing::Test {
protected:
  void SetUp() override
  {
    rclcpp::init(0, nullptr);
    node_ = std::make_shared<rclcpp::Node>("test_node");
    hw_ = std::make_shared<HardwareInterface>(node_);
  }

  void TearDown() override
  {
    hw_.reset();
    node_.reset();
    rclcpp::shutdown();
  }

  rclcpp::Node::SharedPtr node_;
  HardwareInterface::SharedPtr hw_;
};

// 초기화 성공 확인
TEST_F(HardwareInterfaceTest, InitSuccess) {
  EXPECT_TRUE(hw_->init());
  EXPECT_FALSE(hw_->is_emergency_stopped());
}

TEST_F(HardwareInterfaceTest, UsesRobotDescriptionJointNames) {
  node_->declare_parameter(
      "robot_description",
      std::string(
          "<robot name='test_robot'>"
          "<link name='base_link'/>"
          "<link name='arm_link'/>"
          "<link name='gripper_link'/>"
          "<joint name='joint_arm' type='revolute'>"
          "<parent link='base_link'/>"
          "<child link='arm_link'/>"
          "<origin xyz='0 0 0' rpy='0 0 0'/>"
          "<axis xyz='0 0 1'/>"
          "<limit lower='-1.0' upper='1.0' effort='1.0' velocity='1.0'/>"
          "</joint>"
          "<joint name='joint_gripper' type='prismatic'>"
          "<parent link='arm_link'/>"
          "<child link='gripper_link'/>"
          "<origin xyz='0 0 0' rpy='0 0 0'/>"
          "<axis xyz='1 0 0'/>"
          "<limit lower='0.0' upper='0.04' effort='1.0' velocity='1.0'/>"
          "</joint>"
          "</robot>"));

  EXPECT_TRUE(hw_->init());
  EXPECT_EQ(hw_->get_joint_names().size(), 2u);
  EXPECT_NO_THROW(hw_->get_joint("joint_arm"));
  EXPECT_NO_THROW(hw_->get_joint("joint_gripper"));
}

TEST_F(HardwareInterfaceTest, UsesConfiguredJointNames) {
  node_->declare_parameter(
      "joint_names",
      std::vector<std::string>{"joint_a", "joint_b", "joint_c"});
  EXPECT_TRUE(hw_->init());
  EXPECT_EQ(hw_->get_joint_names().size(), 3u);
  EXPECT_NO_THROW(hw_->get_joint("joint_a"));
}

TEST_F(HardwareInterfaceTest, DiscoversJointNamesFromJointStatesWithoutConfig) {
  ASSERT_TRUE(hw_->init());

  auto pub = node_->create_publisher<sensor_msgs::msg::JointState>("/joint_states", 10);
  sensor_msgs::msg::JointState msg;
  msg.name = {"detected_joint"};
  msg.position = {1.2};

  rclcpp::spin_some(node_);
  std::this_thread::sleep_for(std::chrono::milliseconds(20));
  pub->publish(msg);
  rclcpp::spin_some(node_);

  ASSERT_EQ(hw_->get_joint_names().size(), 1u);
  EXPECT_NO_THROW(hw_->get_joint("detected_joint"));
}

// 존재하는 조인트 조회
TEST_F(HardwareInterfaceTest, GetExistingJoint) {
  node_->declare_parameter(
      "joint_names",
      std::vector<std::string>{"left_wheel", "right_wheel"});
  hw_->init();
  const auto & joint = hw_->get_joint("left_wheel");
  EXPECT_EQ(joint.name, "left_wheel");
  EXPECT_DOUBLE_EQ(joint.position, 0.0);
}

// 없는 조인트 조회 시 예외 발생
TEST_F(HardwareInterfaceTest, GetMissingJointThrows) {
  hw_->init();
  EXPECT_THROW(hw_->get_joint("invalid_joint"), std::runtime_error);
}

// 조인트 속도 설정
TEST_F(HardwareInterfaceTest, SetJointVelocity) {
  node_->declare_parameter(
      "joint_names",
      std::vector<std::string>{"left_wheel", "right_wheel"});
  hw_->init();
  hw_->set_joint_velocity("left_wheel", 1.5);
  const auto & joint = hw_->get_joint("left_wheel");
  EXPECT_DOUBLE_EQ(joint.velocity, 1.5);
}

// 비상 정지 후 속도 명령 무시
TEST_F(HardwareInterfaceTest, EmergencyStopBlocksCommands) {
  node_->declare_parameter(
      "joint_names",
      std::vector<std::string>{"left_wheel", "right_wheel"});
  hw_->init();
  hw_->set_joint_velocity("left_wheel", 2.0);
  hw_->emergency_stop();

  EXPECT_TRUE(hw_->is_emergency_stopped());
  // 비상 정지 후 속도 명령은 무시되어야 함
  hw_->set_joint_velocity("left_wheel", 5.0);
  const auto & joint = hw_->get_joint("left_wheel");
  EXPECT_DOUBLE_EQ(joint.velocity, 0.0);
}

// 비상 정지 시 에러 콜백 호출
TEST_F(HardwareInterfaceTest, EmergencyStopCallsCallback) {
  hw_->init();
  bool called = false;
  hw_->set_error_callback([&called](const std::string &) {called = true;});
  hw_->emergency_stop();
  EXPECT_TRUE(called);
}

TEST_F(HardwareInterfaceTest, CommandWatchdogStopsStaleBaseCommand) {
  node_->declare_parameter("command_timeout_sec", 0.01);
  ASSERT_TRUE(hw_->init());

  hw_->set_base_velocity(0.6, 0.2);
  std::this_thread::sleep_for(std::chrono::milliseconds(30));
  hw_->read();

  const auto status = hw_->get_status_json();
  EXPECT_NE(status.find("\"command_watchdog_active\":true"), std::string::npos);
}

TEST_F(HardwareInterfaceTest, WriteDoesNotPublishCmdVelWithoutCommand) {
  ASSERT_TRUE(hw_->init());

  int received = 0;
  auto sub = node_->create_subscription<geometry_msgs::msg::Twist>(
      "/cmd_vel", 10,
    [&received](const geometry_msgs::msg::Twist::SharedPtr) {++received;});

  for (int i = 0; i < 3; ++i) {
    hw_->write();
    rclcpp::spin_some(node_);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }

  EXPECT_EQ(received, 0);
}

TEST_F(HardwareInterfaceTest, WritePublishesCmdVelAfterCommand) {
  ASSERT_TRUE(hw_->init());

  int received = 0;
  auto sub = node_->create_subscription<geometry_msgs::msg::Twist>(
      "/cmd_vel", 10,
    [&received](const geometry_msgs::msg::Twist::SharedPtr msg) {
      if (msg->linear.x == 0.6 && msg->angular.z == 0.2) {
        ++received;
      }
    });

  hw_->set_base_velocity(0.6, 0.2);
  hw_->write();
  for (int i = 0; i < 5; ++i) {
    rclcpp::spin_some(node_);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }

  EXPECT_GT(received, 0);
}

class SensorManagerTest : public ::testing::Test {
protected:
  void SetUp() override
  {
    rclcpp::init(0, nullptr);
    node_ = std::make_shared<rclcpp::Node>("test_sensor_node");
    mgr_ = std::make_shared<SensorManager>(node_);
  }

  void TearDown() override
  {
    mgr_.reset();
    node_.reset();
    rclcpp::shutdown();
  }

  rclcpp::Node::SharedPtr node_;
  SensorManager::SharedPtr mgr_;
};

// 센서 등록 후 목록 확인
TEST_F(SensorManagerTest, RegisterSensor) {
  mgr_->register_sensor("imu", SensorType::IMU, "/imu/data");
  EXPECT_EQ(mgr_->get_sensors().size(), 1u);
  EXPECT_EQ(mgr_->get_sensors()[0].name, "imu");
}

// 중복 등록은 무시
TEST_F(SensorManagerTest, DuplicateRegisterIgnored) {
  mgr_->register_sensor("imu", SensorType::IMU, "/imu/data");
  mgr_->register_sensor("imu", SensorType::IMU, "/imu/data");
  EXPECT_EQ(mgr_->get_sensors().size(), 1u);
}

// 미등록 센서 활성 상태는 false
TEST_F(SensorManagerTest, UnregisteredSensorInactive) {
  EXPECT_FALSE(mgr_->is_active("unknown"));
}

// JSON 상태 출력 확인
TEST_F(SensorManagerTest, StatusJsonContainsSensor) {
  mgr_->register_sensor("lidar", SensorType::LIDAR, "/scan");
  const std::string json = mgr_->get_status_json();
  EXPECT_NE(json.find("lidar"), std::string::npos);
  EXPECT_NE(json.find("/scan"), std::string::npos);
}

TEST_F(SensorManagerTest, SensorBecomesActiveOnMessage) {
  node_->declare_parameter("sensor_timeout_sec", 1.0);
  mgr_->register_sensor("lidar", SensorType::LIDAR, "/scan");

  auto pub = node_->create_publisher<sensor_msgs::msg::LaserScan>("/scan", 10);
  sensor_msgs::msg::LaserScan msg;
  msg.header.frame_id = "laser";

  rclcpp::spin_some(node_);
  std::this_thread::sleep_for(std::chrono::milliseconds(20));
  pub->publish(msg);
  rclcpp::spin_some(node_);

  EXPECT_TRUE(mgr_->is_active("lidar"));
}

TEST_F(SensorManagerTest, SensorTimesOutWithoutMessages) {
  node_->declare_parameter("sensor_timeout_sec", 0.01);
  mgr_->register_sensor("lidar", SensorType::LIDAR, "/scan");

  auto pub = node_->create_publisher<sensor_msgs::msg::LaserScan>("/scan", 10);
  sensor_msgs::msg::LaserScan msg;
  rclcpp::spin_some(node_);
  std::this_thread::sleep_for(std::chrono::milliseconds(20));
  pub->publish(msg);
  rclcpp::spin_some(node_);
  EXPECT_TRUE(mgr_->is_active("lidar"));

  std::this_thread::sleep_for(std::chrono::milliseconds(30));
  mgr_->refresh_activity();

  EXPECT_FALSE(mgr_->is_active("lidar"));
}

int main(int argc, char **argv)
{
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
