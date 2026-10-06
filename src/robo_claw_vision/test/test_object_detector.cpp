#include <gtest/gtest.h>
#include <memory>
#include <string>
#include <vector>

#include "rclcpp/rclcpp.hpp"
#include "robo_claw_vision/object_detector.hpp"
#include "std_msgs/msg/header.hpp"

using namespace robo_claw_vision;

// ── to_ros_msg 변환 단위 테스트 (ONNX 모델 불필요) ─────────────

TEST(ObjectDetectorTest, ToRosMsgEmpty)
{
  std_msgs::msg::Header hdr;
  hdr.frame_id = "camera";
  auto arr = ObjectDetector::to_ros_msg({}, hdr);
  EXPECT_TRUE(arr.detections.empty());
  EXPECT_EQ(arr.header.frame_id, "camera");
}

TEST(ObjectDetectorTest, ToRosMsgBBoxCenterCorrect)
{
  Detection det;
  det.class_id   = 0;
  det.class_name = "person";
  det.confidence = 0.9f;
  det.bbox       = cv::Rect2f(10.0f, 20.0f, 100.0f, 200.0f);

  std_msgs::msg::Header hdr;
  auto arr = ObjectDetector::to_ros_msg({det}, hdr);

  ASSERT_EQ(arr.detections.size(), 1u);
  // 중심 = (10 + 50, 20 + 100) = (60, 120)
  EXPECT_DOUBLE_EQ(arr.detections[0].bbox.center.position.x, 60.0);
  EXPECT_DOUBLE_EQ(arr.detections[0].bbox.center.position.y, 120.0);
  EXPECT_DOUBLE_EQ(arr.detections[0].bbox.size_x, 100.0);
  EXPECT_DOUBLE_EQ(arr.detections[0].bbox.size_y, 200.0);
  EXPECT_EQ(arr.detections[0].results[0].hypothesis.class_id, "0");
  EXPECT_FLOAT_EQ(
    static_cast<float>(arr.detections[0].results[0].hypothesis.score),
    0.9f);
}

TEST(ObjectDetectorTest, ToRosMsgMultipleDetections)
{
  std::vector<Detection> dets;
  for (int i = 0; i < 3; ++i) {
    Detection d;
    d.class_id   = i;
    d.class_name = "obj" + std::to_string(i);
    d.confidence = 0.5f + i * 0.1f;
    d.bbox       = cv::Rect2f(
      static_cast<float>(i * 10),
      static_cast<float>(i * 10),
      50.0f, 50.0f);
    dets.push_back(d);
  }

  std_msgs::msg::Header hdr;
  auto arr = ObjectDetector::to_ros_msg(dets, hdr);
  EXPECT_EQ(arr.detections.size(), 3u);
}

// ── Tier 1.4/1.5: 새 설정 기본값 검증 ─────────────────────────
TEST(ObjectDetectorTest, ConfigDefaultsForTier1)
{
  ObjectDetector::Config cfg;
  // CLAHE 전처리 기본 활성화
  EXPECT_TRUE(cfg.use_clahe);
  EXPECT_DOUBLE_EQ(cfg.clahe_clip_limit, 2.0);
  EXPECT_EQ(cfg.clahe_tile_grid_size, 8);
  // bbox 검증 기본값 (Tier 2.1: 원거리/소형 물체 보존을 위해 완화)
  EXPECT_FLOAT_EQ(cfg.min_aspect_ratio, 0.1f);
  EXPECT_FLOAT_EQ(cfg.max_aspect_ratio, 10.0f);
  EXPECT_FLOAT_EQ(cfg.min_bbox_area_ratio, 0.0002f);
  EXPECT_FLOAT_EQ(cfg.max_bbox_area_ratio, 0.8f);
}

// ── 모델 로드 테스트 (ONNX 파일 존재 시만 실행) ────────────────
TEST(ObjectDetectorTest, ModelLoadOrSkip)
{
  const std::string model_path = "/ros2_ws/models/yolov8n.onnx";
  if (access(model_path.c_str(), F_OK) != 0) {
    GTEST_SKIP() << "ONNX 모델 파일 없음, 테스트 스킵: " << model_path;
  }

  ObjectDetector::Config cfg;
  cfg.model_path   = model_path;
  cfg.class_names  = {"person", "bicycle", "car"};
  cfg.input_width  = 640;
  cfg.input_height = 640;

  EXPECT_NO_THROW({ ObjectDetector detector(cfg); });
}

int main(int argc, char ** argv)
{
  ::testing::InitGoogleTest(&argc, argv);
  rclcpp::init(argc, argv);
  int result = RUN_ALL_TESTS();
  rclcpp::shutdown();
  return result;
}
