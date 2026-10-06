#include "robo_claw_core/sensor_manager.hpp"
#include "robo_claw_core/json_escape.hpp"

#include <chrono>
#include <functional>
#include <sstream>

namespace robo_claw_core
{

namespace
{
template<typename MessageT>
rclcpp::SubscriptionBase::SharedPtr create_activity_subscription(
  const rclcpp::Node::SharedPtr & node,
  const std::string & topic,
  const std::function<void()> & on_message)
{
  auto sub = node->create_subscription<MessageT>(
          topic, rclcpp::QoS(10),
    [on_message](const typename MessageT::SharedPtr)
    {
      on_message();
          });
  return std::static_pointer_cast<rclcpp::SubscriptionBase>(sub);
}
}   // namespace

SensorManager::SensorManager(rclcpp::Node::SharedPtr node)
: node_(node)
{
  node_->get_parameter_or("sensor_timeout_sec", sensor_timeout_sec_, 2.0);
  RCLCPP_INFO(node_->get_logger(), "SensorManager initialized");
}

void SensorManager::register_sensor(
  const std::string & name, SensorType type,
  const std::string & topic)
{
  if (index_map_.count(name) > 0) {
    RCLCPP_WARN(node_->get_logger(), "Sensor already registered: %s", name.c_str());
    return;
  }

  SensorEntry entry{name, type, topic, false};
  index_map_[name] = sensors_.size();
  sensors_.push_back(entry);
  subscriptions_.push_back(nullptr);

  RCLCPP_INFO(node_->get_logger(), "Registered sensor: %s [topic: %s]", name.c_str(),
                topic.c_str());
  create_sensor_subscription(index_map_[name]);
}

bool SensorManager::is_active(const std::string & name) const
{
  auto it = index_map_.find(name);
  if (it == index_map_.end()) {
    return false;
  }
  return sensors_[it->second].active;
}

std::string SensorManager::get_status_json() const
{
  std::ostringstream oss;
  oss << "{\"sensors\":[";
  for (size_t i = 0; i < sensors_.size(); ++i) {
    const auto & s = sensors_[i];
    oss << "{"
        << json_string_field("name", s.name) << ","
        << json_string_field("type", sensor_type_to_string(s.type)) << ","
        << json_string_field("topic", s.topic) << ","
        << json_bool_field("active", s.active) << "}";
    if (i + 1 < sensors_.size()) {
      oss << ",";
    }
  }
  oss << "]}";
  return oss.str();
}

void SensorManager::refresh_activity()
{
  node_->get_parameter_or("sensor_timeout_sec", sensor_timeout_sec_, sensor_timeout_sec_);
  if (sensor_timeout_sec_ <= 0.0) {
    return;
  }

  const int64_t now_ns = node_->now().nanoseconds();
  const int64_t timeout_ns =
    static_cast<int64_t>(sensor_timeout_sec_ * 1e9);

  for (auto & sensor : sensors_) {
    if (!sensor.active || sensor.last_seen_ns <= 0) {
      continue;
    }

    if ((now_ns - sensor.last_seen_ns) > timeout_ns) {
      sensor.active = false;
    }
  }
}

void SensorManager::mark_sensor_seen(const std::string & name)
{
  const auto it = index_map_.find(name);
  if (it == index_map_.end()) {
    return;
  }

  auto & sensor = sensors_[it->second];
  sensor.active = true;
  sensor.last_seen_ns = node_->now().nanoseconds();
}

void SensorManager::create_sensor_subscription(size_t index)
{
  if (index >= sensors_.size()) {
    return;
  }

  const auto sensor = sensors_[index];
  const auto on_message = [this, name = sensor.name]()
    {
      mark_sensor_seen(name);
    };

  switch (sensor.type) {
    case SensorType::IMU:
      subscriptions_[index] = create_activity_subscription<sensor_msgs::msg::Imu>(
          node_, sensor.topic, on_message);
      break;
    case SensorType::LIDAR:
      subscriptions_[index] =
        create_activity_subscription<sensor_msgs::msg::LaserScan>(
              node_, sensor.topic, on_message);
      break;
    case SensorType::CAMERA:
      subscriptions_[index] = create_activity_subscription<sensor_msgs::msg::Image>(
          node_, sensor.topic, on_message);
      break;
    default:
      RCLCPP_WARN(
          node_->get_logger(),
          "Sensor type is UNKNOWN, so active status is not tracked automatically: %s",
          sensor.name.c_str());
      break;
  }
}

const char * SensorManager::sensor_type_to_string(SensorType type)
{
  switch (type) {
    case SensorType::IMU:
      return "IMU";
    case SensorType::LIDAR:
      return "LIDAR";
    case SensorType::CAMERA:
      return "CAMERA";
    case SensorType::ULTRASONIC:
      return "ULTRASONIC";
    case SensorType::ENCODER:
      return "ENCODER";
    default:
      return "UNKNOWN";
  }
}

} // namespace robo_claw_core
