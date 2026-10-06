cmake_minimum_required(VERSION 3.8)
project(robo_claw_core)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_action REQUIRED)
find_package(std_msgs REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(urdf REQUIRED)
find_package(robo_claw_msgs REQUIRED)
find_package(moveit_ros_planning_interface REQUIRED)

set(DEPS
  rclcpp
  rclcpp_action
  std_msgs
  sensor_msgs
  geometry_msgs
  urdf
  robo_claw_msgs
  moveit_ros_planning_interface
)

# 공유 라이브러리 (hw_interface, sensor_manager)
add_library(robo_claw_core_lib SHARED
  src/hardware_interface.cpp
  src/sensor_manager.cpp
)
target_include_directories(robo_claw_core_lib PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
)
target_compile_features(robo_claw_core_lib PUBLIC cxx_std_17)
ament_target_dependencies(robo_claw_core_lib ${DEPS})

# 메인 노드 실행 파일
add_executable(core_node src/core_node.cpp)
target_link_libraries(core_node robo_claw_core_lib)
ament_target_dependencies(core_node ${DEPS})

# MoveIt 2 C++ 중계 노드 실행 파일
add_executable(manipulator_node src/manipulator_node.cpp)
ament_target_dependencies(manipulator_node ${DEPS})

install(TARGETS
  robo_claw_core_lib
  core_node
  manipulator_node
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION lib/${PROJECT_NAME}
)
install(DIRECTORY include/
  DESTINATION include
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  find_package(ament_cmake_gtest REQUIRED)
  set(ament_cmake_copyright_FOUND TRUE)
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()

  ament_add_gtest(test_hardware_interface
    test/test_hardware_interface.cpp
  )
  target_link_libraries(test_hardware_interface robo_claw_core_lib)
  ament_target_dependencies(test_hardware_interface ${DEPS})
endif()

ament_package()
