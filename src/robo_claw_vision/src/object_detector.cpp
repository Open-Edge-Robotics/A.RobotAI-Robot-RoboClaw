#include "robo_claw_vision/object_detector.hpp"

#include <algorithm>
#include <cstring>
#include <stdexcept>

#include <opencv2/dnn.hpp>
#include <opencv2/imgproc.hpp>

#include "vision_msgs/msg/detection2_d.hpp"
#include "vision_msgs/msg/object_hypothesis_with_pose.hpp"

namespace robo_claw_vision
{

ObjectDetector::ObjectDetector(const Config & config)
: config_(config),
  env_(ORT_LOGGING_LEVEL_WARNING, "robo_claw_vision"),
  session_(nullptr)
{
  session_opts_.SetIntraOpNumThreads(config_.num_threads);
  session_opts_.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
  // Tier 2.2: 추론 최적화 — 순차 실행 모드 명시 + 메모리 아레나 확장으로
  // 반복 추론 시 힙 할당/해제 오버헤드를 줄인다.
  session_opts_.SetExecutionMode(ExecutionMode::ORT_SEQUENTIAL);
  session_opts_.AddConfigEntry("session.use_env_allocators", "1");
  session_opts_.AddConfigEntry("session.intra_op.allow_spinning", "1");

  session_ = Ort::Session(env_, config_.model_path.c_str(), session_opts_);

  // 입/출력 이름 캐시
  auto in_name  = session_.GetInputNameAllocated(0, allocator_);
  auto out_name = session_.GetOutputNameAllocated(0, allocator_);
  input_name_  = in_name.get();
  output_name_ = out_name.get();

  // 입력 shape 캐시
  input_shape_ = {
    1, 3,
    static_cast<int64_t>(config_.input_height),
    static_cast<int64_t>(config_.input_width)
  };

  // 입력 버퍼 사전 할당 (재사용)
  const size_t input_size =
    static_cast<size_t>(config_.input_height) *
    static_cast<size_t>(config_.input_width) * 3;
  input_buffer_.resize(input_size, 0.0f);
}

void ObjectDetector::preprocess(const cv::Mat & image, float * buf)
{
  cv::Mat src = image;

  // Tier 1.5: CLAHE(대비 제한 적응형 히스토그램 평활화)로 저조도/역광 보정.
  // LAB 색공간의 L(명도) 채널에만 적용해 색상은 보존하고 대비만 강화한다.
  // 로봇 카메라(헤드/그리퍼)는 조명이 일정하지 않아 이 보정이 탐지율을 높인다.
  if (config_.use_clahe) {
    cv::Mat lab;
    cv::cvtColor(src, lab, cv::COLOR_BGR2Lab);
    std::vector<cv::Mat> planes;
    cv::split(lab, planes);
    cv::Ptr<cv::CLAHE> clahe = cv::createCLAHE(
      config_.clahe_clip_limit,
      cv::Size(config_.clahe_tile_grid_size, config_.clahe_tile_grid_size));
    clahe->apply(planes[0], planes[0]);
    cv::merge(planes, lab);
    cv::cvtColor(lab, src, cv::COLOR_Lab2BGR);
  }

  cv::Mat resized, rgb;
  cv::resize(src, resized, cv::Size(config_.input_width, config_.input_height));
  cv::cvtColor(resized, rgb, cv::COLOR_BGR2RGB);

  // blobFromImage: HWC(uint8) → CHW(float32 [0,1]), SIMD 최적화
  cv::Mat blob = cv::dnn::blobFromImage(
    rgb, 1.0 / 255.0,
    cv::Size(config_.input_width, config_.input_height),
    cv::Scalar(), false, false, CV_32F);
  std::memcpy(buf, blob.ptr<float>(), blob.total() * sizeof(float));
}

std::vector<Detection> ObjectDetector::detect(const cv::Mat & image)
{
  const int orig_w = image.cols;
  const int orig_h = image.rows;

  // 1. 전처리 (사전 할당 버퍼 재사용)
  preprocess(image, input_buffer_.data());

  // 2. 입력 텐서 생성 (input_buffer_ 직접 참조, zero-copy)
  auto memory_info = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
  Ort::Value input_tensor = Ort::Value::CreateTensor<float>(
    memory_info,
    input_buffer_.data(),
    input_buffer_.size(),
    input_shape_.data(),
    input_shape_.size());

  // 3. 추론
  const char * input_names[]  = {input_name_.c_str()};
  const char * output_names[] = {output_name_.c_str()};

  auto outputs = session_.Run(
    Ort::RunOptions{nullptr},
    input_names, &input_tensor, 1,
    output_names, 1);

  // 4. 후처리
  // YOLOv8 ONNX 출력: shape [1, 84, 8400]
  //   행 0~3  = cx, cy, w, h (모델 입력 공간)
  //   행 4~83 = 80 COCO 클래스 점수
  const auto out_shape = outputs[0].GetTensorTypeAndShapeInfo().GetShape();
  if (out_shape.size() < 3) {
    throw std::runtime_error(
      "예상치 못한 ONNX 출력 shape (rank=" + std::to_string(out_shape.size()) +
      ", 기대값: rank 3, 예: [1, 84, 8400])");
  }
  const int64_t num_anchors = out_shape[2];  // 8400
  const float * data = outputs[0].GetTensorMutableData<float>();

  return postprocess(data, num_anchors, orig_w, orig_h);
}

std::vector<Detection> ObjectDetector::postprocess(
  const float * data,
  int64_t num_anchors,
  int orig_w,
  int orig_h)
{
  const int num_classes =
    static_cast<int>(config_.class_names.size());
  const float scale_x =
    static_cast<float>(orig_w) / static_cast<float>(config_.input_width);
  const float scale_y =
    static_cast<float>(orig_h) / static_cast<float>(config_.input_height);

  std::vector<cv::Rect2d> raw_boxes;   // NMSBoxes 오버로드는 Rect2d/Rect만 지원
  std::vector<float>      raw_scores;
  std::vector<int>        raw_class_ids;

  raw_boxes.reserve(512);
  raw_scores.reserve(512);
  raw_class_ids.reserve(512);

  for (int64_t i = 0; i < num_anchors; ++i) {
    // data[row * num_anchors + anchor_idx]
    const float cx = data[0 * num_anchors + i];
    const float cy = data[1 * num_anchors + i];
    const float w  = data[2 * num_anchors + i];
    const float h  = data[3 * num_anchors + i];

    float max_score = -1.0f;
    int   best_cls  = -1;
    for (int c = 0; c < num_classes; ++c) {
      const float s = data[(4 + c) * num_anchors + i];
      if (s > max_score) {
        max_score = s;
        best_cls  = c;
      }
    }

    if (max_score < config_.confidence_threshold) {
      continue;
    }

    const float x1 = (cx - w / 2.0f) * scale_x;
    const float y1 = (cy - h / 2.0f) * scale_y;
    const float bw = w * scale_x;
    const float bh = h * scale_y;

    // Tier 1.4: bbox 검증 — 명백한 오탐지(비정상 종횡비/면적)를 폐기한다.
    // YOLO가 배경 조각이나 그림자를 물체로 오인할 때 이런 비정상 bbox가 나온다.
    const float aspect = bh > 0.0f ? bw / bh : 0.0f;
    const float area_ratio =
      (static_cast<float>(orig_w) * static_cast<float>(orig_h)) > 0.0f
        ? (bw * bh) / (static_cast<float>(orig_w) * static_cast<float>(orig_h))
        : 0.0f;
    if (aspect < config_.min_aspect_ratio || aspect > config_.max_aspect_ratio) {
      continue;
    }
    if (area_ratio < config_.min_bbox_area_ratio ||
        area_ratio > config_.max_bbox_area_ratio) {
      continue;
    }

    raw_boxes.emplace_back(x1, y1, bw, bh);
    raw_scores.push_back(max_score);
    raw_class_ids.push_back(best_cls);
  }

  // NMS
  std::vector<int> nms_indices;
  cv::dnn::NMSBoxes(
    raw_boxes, raw_scores,
    0.0f,                       // 이미 위에서 필터링 완료
    config_.nms_threshold,
    nms_indices);

  std::vector<Detection> result;
  result.reserve(nms_indices.size());
  for (int idx : nms_indices) {
    Detection det;
    det.class_id   = raw_class_ids[idx];
    det.confidence = raw_scores[idx];
    det.bbox       = raw_boxes[idx];
    if (det.class_id < static_cast<int>(config_.class_names.size())) {
      det.class_name = config_.class_names[det.class_id];
    } else {
      det.class_name = "unknown";
    }
    result.push_back(std::move(det));
  }
  return result;
}

vision_msgs::msg::Detection2DArray ObjectDetector::to_ros_msg(
  const std::vector<Detection> & detections,
  const std_msgs::msg::Header & header)
{
  vision_msgs::msg::Detection2DArray arr;
  arr.header = header;
  arr.detections.reserve(detections.size());

  for (const auto & det : detections) {
    vision_msgs::msg::Detection2D d;
    d.header = header;

    d.bbox.center.position.x = det.bbox.x + det.bbox.width  / 2.0;
    d.bbox.center.position.y = det.bbox.y + det.bbox.height / 2.0;
    d.bbox.size_x = det.bbox.width;
    d.bbox.size_y = det.bbox.height;

    vision_msgs::msg::ObjectHypothesisWithPose hyp;
    hyp.hypothesis.class_id = std::to_string(det.class_id);
    hyp.hypothesis.score    = det.confidence;
    d.results.push_back(hyp);

    arr.detections.push_back(std::move(d));
  }
  return arr;
}

}  // namespace robo_claw_vision
