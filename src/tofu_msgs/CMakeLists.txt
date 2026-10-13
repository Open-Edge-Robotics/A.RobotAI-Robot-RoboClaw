cmake_minimum_required(VERSION 3.8)
project(tofu_msgs)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)

# Wire-compatible CLOi tofu subset used by RoboClaw.
# - action/RunScript.action: tofu script_runner action contract
#   (ros2 run <package> <executable> 실행 요청/결과/피드백).
rosidl_generate_interfaces(${PROJECT_NAME}
  "action/RunScript.action"
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
