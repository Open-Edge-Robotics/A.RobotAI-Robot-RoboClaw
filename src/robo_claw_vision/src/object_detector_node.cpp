#include <filesystem>
#include <deque>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

#if __has_include("cv_bridge/cv_bridge.hpp")
#include "cv_bridge/cv_bridge.hpp"  // Iron / Jazzy+
#else
#include "cv_bridge/cv_bridge.h"    // Humble
#endif
#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/msg/image.hpp"
#include "vision_msgs/msg/detection2_d_array.hpp"

#include "robo_claw_vision/object_detector.hpp"

namespace robo_claw_vision
{

class ObjectDetectorNode : public rclcpp::Node
{
public:
  ObjectDetectorNode()
  : Node("object_detector_node")
  {
    declare_parameter("camera_topic",         std::string("/oakd/rgb/preview/image_raw"));
    declare_parameter("model_path",           std::string("/ros2_ws/models/yolov8s.onnx"));
    declare_parameter("confidence_threshold", 0.5);
    declare_parameter("nms_threshold",        0.45);
    declare_parameter("input_width",          640);
    declare_parameter("input_height",         640);
    declare_parameter("num_threads",          2);
    declare_parameter("max_inference_hz",     10.0);

    // Tier 1.5: CLAHE 전처리
    declare_parameter("use_clahe",            true);
    declare_parameter("clahe_clip_limit",     2.0);
    declare_parameter("clahe_tile_grid_size", 8);

    // Tier 1.4: bbox 검증
    declare_parameter("min_aspect_ratio",     0.1);
    declare_parameter("max_aspect_ratio",     10.0);
    declare_parameter("min_bbox_area_ratio", 0.0002);
    declare_parameter("max_bbox_area_ratio",  0.8);

    // Tier 1.3: temporal 확인 필터 — 최근 N 프레임 중 K회 이상 등장한 클래스만 publish.
    // 낮은 confidence threshold(0.25)로 인한 오탐지를 줄이고 일시적 가림에 강인해진다.
    // Tier 2.1: 헤드 pan/tilt 스윕 중 모션 블러로 검출이 깜빡이는 것을 감안해
    // 기본을 2-of-5 로 완화한다 (기존 2-of-3).
    declare_parameter("temporal_history_size",          5);
    declare_parameter("temporal_confirmation_frames",   2);
    declare_parameter("class_names",          std::vector<std::string>{
      "person","bicycle","car","motorcycle","airplane","bus","train","truck","boat",
      "traffic light","fire hydrant","stop sign","parking meter","bench","bird","cat",
      "dog","horse","sheep","cow","elephant","bear","zebra","giraffe","backpack",
      "umbrella","handbag","tie","suitcase","frisbee","skis","snowboard","sports ball",
      "kite","baseball bat","baseball glove","skateboard","surfboard","tennis racket",
      "bottle","wine glass","cup","fork","knife","spoon","bowl","banana","apple",
      "sandwich","orange","broccoli","carrot","hot dog","pizza","donut","cake","chair",
      "couch","potted plant","bed","dining table","toilet","tv","laptop","mouse",
      "remote","keyboard","cell phone","microwave","oven","toaster","sink",
      "refrigerator","book","clock","vase","scissors","teddy bear","hair drier",
      "toothbrush"
    });
  }

  void initialize()
  {
    ObjectDetector::Config cfg;
    cfg.model_path           = get_parameter("model_path").as_string();
    cfg.confidence_threshold =
      static_cast<float>(get_parameter("confidence_threshold").as_double());
    cfg.nms_threshold =
      static_cast<float>(get_parameter("nms_threshold").as_double());
    cfg.input_width  = get_parameter("input_width").as_int();
    cfg.input_height = get_parameter("input_height").as_int();
    cfg.num_threads  = get_parameter("num_threads").as_int();
    cfg.class_names  = get_parameter("class_names").as_string_array();

    // Tier 1.5: CLAHE 전처리
    cfg.use_clahe            = get_parameter("use_clahe").as_bool();
    cfg.clahe_clip_limit     = get_parameter("clahe_clip_limit").as_double();
    cfg.clahe_tile_grid_size = get_parameter("clahe_tile_grid_size").as_int();

    // Tier 1.4: bbox 검증
    cfg.min_aspect_ratio     = static_cast<float>(get_parameter("min_aspect_ratio").as_double());
    cfg.max_aspect_ratio     = static_cast<float>(get_parameter("max_aspect_ratio").as_double());
    cfg.min_bbox_area_ratio  = static_cast<float>(get_parameter("min_bbox_area_ratio").as_double());
    cfg.max_bbox_area_ratio  = static_cast<float>(get_parameter("max_bbox_area_ratio").as_double());

    max_inference_hz_ = get_parameter("max_inference_hz").as_double();

    // Tier 1.3: temporal 확인 필터 설정
    temporal_history_size_         = get_parameter("temporal_history_size").as_int();
    temporal_confirmation_frames_  = get_parameter("temporal_confirmation_frames").as_int();

    if (!std::filesystem::exists(cfg.model_path)) {
      RCLCPP_FATAL(get_logger(),
        "ONNX model file not found: %s\n"
        "  1) Download the model first: ./download_model.sh        (yolov8s, default)\n"
        "                                ./download_model.sh n      (yolov8n, lightweight)\n"
        "  2) After downloading, verify the model_path parameter matches the actual save location\n"
        "     (launch argument: vision_model_path:=/ros2_ws/models/yolov8s.onnx)",
        cfg.model_path.c_str());
      throw std::runtime_error(
        "ONNX 모델 파일 없음: " + cfg.model_path +
        " (./download_model.sh 로 다운로드 필요)");
    }

    try {
      detector_ = std::make_shared<ObjectDetector>(cfg);
      RCLCPP_INFO(get_logger(), "ONNX model loaded: %s", cfg.model_path.c_str());
    } catch (const Ort::Exception & e) {
      RCLCPP_FATAL(get_logger(), "Failed to load ONNX model: %s", e.what());
      throw;
    }

    detection_pub_ = create_publisher<vision_msgs::msg::Detection2DArray>(
      "~/detections", rclcpp::QoS(10));

    const std::string cam_topic = get_parameter("camera_topic").as_string();

    // SensorDataQoS: BEST_EFFORT — OAK-D / RealSense 드라이버 QoS와 호환
    image_sub_ = create_subscription<sensor_msgs::msg::Image>(
      cam_topic,
      rclcpp::SensorDataQoS(),
      [this](sensor_msgs::msg::Image::ConstSharedPtr msg) {
        on_image(msg);
      });

    RCLCPP_INFO(get_logger(),
      "ObjectDetectorNode ready — subscribed: %s, publishing: ~/detections",
      cam_topic.c_str());
  }

private:
  void on_image(sensor_msgs::msg::Image::ConstSharedPtr msg)
  {
    if (!detector_) { return; }  // initialize() 완료 전 방어

    // 구독자가 없으면(어떤 스킬도 detections를 요청 중이 아니면) 추론을 건너뛴다 —
    // wait_for_message가 호출 동안만 임시 구독을 만들기 때문에 사실상 온디맨드 게이팅이 된다.
    if (detection_pub_->get_subscription_count() == 0) { return; }

    // 카메라 프레임레이트(수십 Hz)로 매 프레임 추론하면 저사양 로봇에 부담이 크므로,
    // max_inference_hz로 상한을 둔다 (0 이하면 제한 없음).
    if (max_inference_hz_ > 0.0) {
      const rclcpp::Time now = msg->header.stamp.sec != 0 || msg->header.stamp.nanosec != 0
        ? rclcpp::Time(msg->header.stamp)
        : this->now();
      const double min_period_sec = 1.0 / max_inference_hz_;
      if (last_inference_time_.nanoseconds() != 0 &&
          (now - last_inference_time_).seconds() < min_period_sec)
      {
        return;
      }
      last_inference_time_ = now;
    }

    // toCvShare: 인코딩이 맞으면 원본 버퍼 참조 (복사 없음)
    cv_bridge::CvImageConstPtr cv_ptr;
    try {
      cv_ptr = cv_bridge::toCvShare(msg, "bgr8");
    } catch (const cv_bridge::Exception & e) {
      RCLCPP_WARN(get_logger(), "cv_bridge error: %s", e.what());
      return;
    }

    try {
      auto detections = detector_->detect(cv_ptr->image);
      // Tier 1.3: temporal 확인 필터 — 최근 N 프레임 중 K회 이상 등장한 클래스만 통과.
      auto confirmed = apply_temporal_filter(detections);
      auto det_arr   = ObjectDetector::to_ros_msg(confirmed, msg->header);
      detection_pub_->publish(det_arr);
    } catch (const std::exception & e) {
      RCLCPP_ERROR(get_logger(), "Object detection inference failed: %s", e.what());
    }
  }

  // Tier 1.3: 최근 temporal_history_size_ 프레임의 클래스 출현 이력을 유지하고,
  // temporal_confirmation_frames_ 회 이상 등장한 클래스의 detection만 남긴다.
  // 낮은 confidence threshold로 인한 프레임 단위 오탐지(깜빡임)를 억제한다.
  std::vector<Detection> apply_temporal_filter(const std::vector<Detection> & detections)
  {
    if (temporal_history_size_ <= 1 || temporal_confirmation_frames_ <= 1) {
      return detections;  // 필터 비활성화
    }

    std::set<int> frame_classes;
    for (const auto & d : detections) {
      frame_classes.insert(d.class_id);
    }
    recent_classes_.push_back(frame_classes);
    while (static_cast<int>(recent_classes_.size()) > temporal_history_size_) {
      recent_classes_.pop_front();
    }

    std::map<int, int> counts;
    for (const auto & frame : recent_classes_) {
      for (int c : frame) {
        counts[c]++;
      }
    }

    std::vector<Detection> confirmed;
    for (const auto & d : detections) {
      if (counts[d.class_id] >= temporal_confirmation_frames_) {
        confirmed.push_back(d);
      }
    }
    return confirmed;
  }

  std::shared_ptr<ObjectDetector> detector_;
  rclcpp::Subscription<sensor_msgs::msg::Image>::SharedPtr       image_sub_;
  rclcpp::Publisher<vision_msgs::msg::Detection2DArray>::SharedPtr detection_pub_;

  double max_inference_hz_{0.0};
  rclcpp::Time last_inference_time_{0, 0, RCL_ROS_TIME};

  // Tier 1.3: temporal 확인 필터 상태
  int temporal_history_size_{3};
  int temporal_confirmation_frames_{2};
  std::deque<std::set<int>> recent_classes_;
};

}  // namespace robo_claw_vision

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  try {
    auto node = std::make_shared<robo_claw_vision::ObjectDetectorNode>();
    node->initialize();
    rclcpp::spin(node);
  } catch (const std::exception & e) {
    RCLCPP_FATAL(rclcpp::get_logger("main"), "Exception: %s", e.what());
  }
  rclcpp::shutdown();
  return 0;
}
