#pragma once

#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/subscription_base.hpp"
#include "sensor_msgs/msg/image.hpp"
#include "sensor_msgs/msg/imu.hpp"
#include "sensor_msgs/msg/laser_scan.hpp"

namespace robo_claw_core
{

  /// 센서 타입 열거
enum class SensorType
{
  IMU,
  LIDAR,
  CAMERA,
  ULTRASONIC,
  ENCODER,
  UNKNOWN
};

  /// 센서 등록 정보
struct SensorEntry
{
  std::string name;
  SensorType type;
  std::string topic;
  bool active{false};
  int64_t last_seen_ns{0};
};

  /// 센서 관리자 — 연결된 센서 목록 관리 및 데이터 집계
class SensorManager
{
public:
  using SharedPtr = std::shared_ptr<SensorManager>;

  explicit SensorManager(rclcpp::Node::SharedPtr node);
  ~SensorManager() = default;

    /// 센서 등록
  void register_sensor(
    const std::string & name, SensorType type,
    const std::string & topic);

    /// 등록된 센서 목록 반환
  const std::vector<SensorEntry> & get_sensors() const {return sensors_;}

    /// 특정 센서 활성 여부 확인
  bool is_active(const std::string & name) const;

    /// 전체 센서 상태 JSON 반환
  std::string get_status_json() const;

    /// 센서 활성 상태 timeout 갱신
  void refresh_activity();

private:
  void mark_sensor_seen(const std::string & name);
  void create_sensor_subscription(size_t index);
  static const char * sensor_type_to_string(SensorType type);

  rclcpp::Node::SharedPtr node_;
  std::vector<SensorEntry> sensors_;
  std::unordered_map<std::string, size_t> index_map_;
  std::vector<rclcpp::SubscriptionBase::SharedPtr> subscriptions_;
  double sensor_timeout_sec_{2.0};
};

} // namespace robo_claw_core
