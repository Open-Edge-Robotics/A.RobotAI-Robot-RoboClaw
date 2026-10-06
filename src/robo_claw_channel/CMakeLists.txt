cmake_minimum_required(VERSION 3.8)
project(robo_claw_channel)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(rclpy REQUIRED)

# Python 패키지 설치
ament_python_install_package(${PROJECT_NAME})

# 실행 가능한 스크립트 등록
install(PROGRAMS
  scripts/channel_node
  scripts/client_node
  DESTINATION lib/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  set(ament_cmake_copyright_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()

  find_package(ament_cmake_pytest REQUIRED)
  file(GLOB CHANNEL_TEST_FILES CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_*.py"
  )
  foreach(TEST_FILE IN LISTS CHANNEL_TEST_FILES)
    get_filename_component(TEST_NAME "${TEST_FILE}" NAME_WE)
    ament_add_pytest_test("${TEST_NAME}" "${TEST_FILE}")
  endforeach()
endif()

ament_package()
