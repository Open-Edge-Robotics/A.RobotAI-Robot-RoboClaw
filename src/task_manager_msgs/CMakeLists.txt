cmake_minimum_required(VERSION 3.8)
project(task_manager_msgs)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)

# Wire-compatible CLOi MotionCmd subset used by the RoboClaw motion skill.
rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/MotionCmd.msg"
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
