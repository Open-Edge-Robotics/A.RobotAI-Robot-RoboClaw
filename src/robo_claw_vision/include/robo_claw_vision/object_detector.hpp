#pragma once

#include <memory>
#include <string>
#include <vector>

#include <onnxruntime_cxx_api.h>
#include <opencv2/core.hpp>

#include "std_msgs/msg/header.hpp"
#include "vision_msgs/msg/detection2_d_array.hpp"

namespace robo_claw_vision
{

struct Detection
{
  int class_id{-1};
  std::string class_name;
  float confidence{0.0f};
  cv::Rect2f bbox;  // 원본 이미지 픽셀 좌표계
};

class ObjectDetector
{
public:
  using SharedPtr = std::shared_ptr<ObjectDetector>;

  struct Config
  {
    std::string model_path;
    std::vector<std::string> class_names;
    float confidence_threshold{0.5f};
    float nms_threshold{0.45f};
    int input_width{640};
    int input_height{640};
    int num_threads{2};

    // ── Tier 1.5: CLAHE 전처리 (저조도/역광 보정) ──
    bool use_clahe{true};
    double clahe_clip_limit{2.0};
    int clahe_tile_grid_size{8};

    // ── Tier 1.4: bbox 검증 (명백한 오탐지 필터링) ──
    // 종횡비(aspect ratio)와 전체 이미지 대비 면적 비율 범위를 벗어나면 폐기.
    // Tier 2.1: 원거리/소형 물체가 헤드(0.8~1.0m)에서 프레임의 0.1% 미만으로
    // 잡히는 경우가 흔해, 면적 하한을 0.001 → 0.0002 로 낮추고 종횡비 범위를
    // 넓혀 실제 검출이 조기 폐기되지 않게 한다.
    float min_aspect_ratio{0.1f};
    float max_aspect_ratio{10.0f};
    float min_bbox_area_ratio{0.0002f};
    float max_bbox_area_ratio{0.8f};
  };

  explicit ObjectDetector(const Config & config);
  ~ObjectDetector() = default;

  ObjectDetector(const ObjectDetector &) = delete;
  ObjectDetector & operator=(const ObjectDetector &) = delete;

  /// BGR cv::Mat (임의 해상도) → Detection 벡터
  std::vector<Detection> detect(const cv::Mat & image);

  /// Detection 벡터 → vision_msgs/Detection2DArray
  static vision_msgs::msg::Detection2DArray to_ros_msg(
    const std::vector<Detection> & detections,
    const std_msgs::msg::Header & header);

private:
  void preprocess(const cv::Mat & image, float * input_buffer);
  std::vector<Detection> postprocess(
    const float * output_data,
    int64_t num_anchors,
    int orig_w,
    int orig_h);

  Config config_;
  Ort::Env env_;
  Ort::SessionOptions session_opts_;
  Ort::Session session_;
  Ort::AllocatorWithDefaultOptions allocator_;

  std::string input_name_;
  std::string output_name_;
  std::vector<int64_t> input_shape_;  // {1, 3, H, W}

  // 사전 할당 재사용 버퍼 — 힙 할당 0회/프레임
  std::vector<float> input_buffer_;
};

}  // namespace robo_claw_vision
