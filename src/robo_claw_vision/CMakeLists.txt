cmake_minimum_required(VERSION 3.8)
project(robo_claw_vision)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# ── ROS 2 / 일반 의존성 ───────────────────────────────────────
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(vision_msgs REQUIRED)
find_package(cv_bridge REQUIRED)
find_package(OpenCV REQUIRED COMPONENTS core imgproc dnn)

set(DEPS
  rclcpp
  sensor_msgs
  vision_msgs
  cv_bridge
)

# ── ONNX Runtime (amd64: x64 / arm64: aarch64) ──────────────
# CMAKE_SYSTEM_PROCESSOR: x86_64 | aarch64
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64")
  set(_ORT_ARCH "aarch64")
else()
  set(_ORT_ARCH "x64")
endif()

set(_ORT_DIR_NAME "onnxruntime-linux-${_ORT_ARCH}-1.19.2")

set(ONNXRUNTIME_SEARCH_PATHS
    "${ONNXRUNTIME_ROOT_DIR}"
    "$ENV{ONNXRUNTIME_ROOT_DIR}"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../${_ORT_DIR_NAME}"
    "$ENV{HOME}/.local/${_ORT_DIR_NAME}"
    "/usr/local/${_ORT_DIR_NAME}"
)

find_path(ONNXRUNTIME_INCLUDE_DIR
    NAMES onnxruntime_cxx_api.h
    PATHS ${ONNXRUNTIME_SEARCH_PATHS}
    PATH_SUFFIXES include
)
find_library(ONNXRUNTIME_LIBRARY
    NAMES onnxruntime
    PATHS ${ONNXRUNTIME_SEARCH_PATHS}
    PATH_SUFFIXES lib
)

if(NOT ONNXRUNTIME_INCLUDE_DIR OR NOT ONNXRUNTIME_LIBRARY)
  message(FATAL_ERROR
    "ONNX Runtime을 찾을 수 없습니다. "
    "ONNXRUNTIME_ROOT_DIR='${ONNXRUNTIME_ROOT_DIR}' 를 확인하거나 "
    "-DONNXRUNTIME_ROOT_DIR=<경로> 로 지정하세요.")
endif()
message(STATUS "ONNX Runtime include: ${ONNXRUNTIME_INCLUDE_DIR}")
message(STATUS "ONNX Runtime library: ${ONNXRUNTIME_LIBRARY}")

# ── 공유 라이브러리 ──────────────────────────────────────────
add_library(robo_claw_vision_lib SHARED
  src/object_detector.cpp
)
target_include_directories(robo_claw_vision_lib PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
  ${ONNXRUNTIME_INCLUDE_DIR}
  ${OpenCV_INCLUDE_DIRS}
)
target_compile_features(robo_claw_vision_lib PUBLIC cxx_std_17)
target_link_libraries(robo_claw_vision_lib
  ${ONNXRUNTIME_LIBRARY}
  ${OpenCV_LIBS}
)
ament_target_dependencies(robo_claw_vision_lib ${DEPS})

# ── 메인 노드 실행 파일 ──────────────────────────────────────
add_executable(object_detector_node src/object_detector_node.cpp)
target_link_libraries(object_detector_node robo_claw_vision_lib)
ament_target_dependencies(object_detector_node ${DEPS})

# ── 설치 ─────────────────────────────────────────────────────
install(TARGETS
  robo_claw_vision_lib
  object_detector_node
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION lib/${PROJECT_NAME}
)
install(DIRECTORY include/
  DESTINATION include
)
# ONNX Runtime .so를 install 경로에 포함 (LD_LIBRARY_PATH 없이 동작)
install(FILES "${ONNXRUNTIME_LIBRARY}"
  DESTINATION lib
)

# ── 테스트 ───────────────────────────────────────────────────
if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  find_package(ament_cmake_gtest REQUIRED)
  set(ament_cmake_copyright_FOUND TRUE)
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()

  ament_add_gtest(test_object_detector
    test/test_object_detector.cpp
  )
  target_link_libraries(test_object_detector robo_claw_vision_lib)
  ament_target_dependencies(test_object_detector ${DEPS})
endif()

ament_package()
