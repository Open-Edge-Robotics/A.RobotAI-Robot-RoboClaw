cmake_minimum_required(VERSION 3.8)
project(robo_claw_discovery)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(rclpy REQUIRED)

ament_python_install_package(${PROJECT_NAME})

install(PROGRAMS
  scripts/discovery_node
  DESTINATION lib/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  set(ament_cmake_copyright_FOUND TRUE)
  set(ament_cmake_flake8_FOUND TRUE)
  set(ament_cmake_pep257_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()

  find_package(ament_cmake_pytest REQUIRED)
  file(GLOB DISCOVERY_TEST_FILES CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_*.py"
  )
  foreach(TEST_FILE IN LISTS DISCOVERY_TEST_FILES)
    get_filename_component(TEST_NAME "${TEST_FILE}" NAME_WE)
    ament_add_pytest_test("${TEST_NAME}" "${TEST_FILE}")
  endforeach()
endif()

ament_package()
