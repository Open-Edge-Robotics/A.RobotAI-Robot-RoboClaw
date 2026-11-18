cmake_minimum_required(VERSION 3.8)
project(lio_fr_system_msgs)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(geometry_msgs REQUIRED)

# CLOiD lio-fr-system 의 /switch_mode 서비스와 와이어 호환되는 서브셋.
# 필드 구성이 upstream 과 다르면 ROS 2 가 type hash 불일치로 연결을 거부한다.
rosidl_generate_interfaces(${PROJECT_NAME}
  "srv/SwitchMode.srv"
  DEPENDENCIES geometry_msgs
)

ament_export_dependencies(rosidl_default_runtime geometry_msgs)
ament_package()
