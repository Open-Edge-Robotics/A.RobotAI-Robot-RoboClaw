cmake_minimum_required(VERSION 3.8)
project(robo_claw_msgs)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# buildx arm64/QEMU 환경에서 colcon의 첫 CMake configure 중 ROS 인터페이스
# export library 탐색이 간헐적으로 깨지는 경우를 방지한다.
set(CMAKE_FIND_LIBRARY_PREFIXES "lib" "")
set(CMAKE_FIND_LIBRARY_SUFFIXES ".so" ".a")

find_package(ament_cmake REQUIRED)
find_package(std_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/AgentStatus.msg"
  "msg/SkillResult.msg"
  "msg/PeerInfo.msg"
  "srv/ExecuteSkill.srv"
  "srv/QueryState.srv"
  "srv/SendMessage.srv"
  "srv/CallPeerRobot.srv"
  "srv/ListPeers.srv"
  "srv/ExecuteManipulator.srv"
  "srv/PeerMessage.srv"
  "action/ExecuteTask.action"
  DEPENDENCIES std_msgs geometry_msgs builtin_interfaces
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  set(ament_cmake_copyright_FOUND TRUE)
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
