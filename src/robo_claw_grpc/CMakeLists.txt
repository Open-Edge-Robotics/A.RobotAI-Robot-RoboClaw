cmake_minimum_required(VERSION 3.8)
project(robo_claw_grpc)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(rclpy REQUIRED)
# robo_claw_msgs 는 런타임(python) 의존이라 package.xml 의 <depend> 로 충분하다.
# 순수 python 패키지에서 find_package(robo_claw_msgs) 를 하면 std_msgs ->
# builtin_interfaces export 라이브러리 해석 체인을 타는데, C++ 컨텍스트가 없어
# find_library 가 .so 를 못 찾아 빌드가 깨진다. CMake 단계에서는 불필요하므로 제거.

ament_python_install_package(${PROJECT_NAME})

install(PROGRAMS
  scripts/robo_claw_grpc
  DESTINATION lib/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  set(ament_cmake_copyright_FOUND TRUE)
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()

  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(test_robo_claw_grpc tests
    PYTHON_EXECUTABLE ${Python3_EXECUTABLE}
  )
endif()

ament_package()
