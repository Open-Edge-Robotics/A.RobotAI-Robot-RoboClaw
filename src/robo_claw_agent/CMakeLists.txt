cmake_minimum_required(VERSION 3.8)
project(robo_claw_agent)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(rclpy REQUIRED)
find_package(task_manager_msgs REQUIRED)

# Python 패키지 설치
ament_python_install_package(${PROJECT_NAME})

# 실행 가능한 스크립트 등록
install(PROGRAMS
  scripts/agent_node
  DESTINATION lib/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  set(ament_cmake_copyright_FOUND TRUE)
  set(ament_cmake_flake8_FOUND TRUE)
  set(ament_cmake_pep257_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()

  find_package(ament_cmake_pytest REQUIRED)
  find_package(Python3 REQUIRED COMPONENTS Interpreter)
  execute_process(
    COMMAND "${Python3_EXECUTABLE}" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    OUTPUT_VARIABLE PYTHON_VERSION_SHORT
    OUTPUT_STRIP_TRAILING_WHITESPACE
  )
  get_filename_component(WORKSPACE_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
  set(PYTHONPATH_ENTRIES "${CMAKE_CURRENT_SOURCE_DIR}")
  if(EXISTS "/opt/ros/jazzy/lib/python${PYTHON_VERSION_SHORT}/site-packages")
    list(APPEND PYTHONPATH_ENTRIES "/opt/ros/jazzy/lib/python${PYTHON_VERSION_SHORT}/site-packages")
  elseif(EXISTS "/opt/ros/humble/lib/python${PYTHON_VERSION_SHORT}/site-packages")
    list(APPEND PYTHONPATH_ENTRIES "/opt/ros/humble/lib/python${PYTHON_VERSION_SHORT}/site-packages")
  endif()
  list(APPEND PYTHONPATH_ENTRIES "${WORKSPACE_ROOT}/.venv/lib/python${PYTHON_VERSION_SHORT}/site-packages")
  list(JOIN PYTHONPATH_ENTRIES ":" PYTHONPATH_VALUE)
  set(PYTEST_ENV "PYTHONPATH=${PYTHONPATH_VALUE}")

  # Keep colcon test in sync with the pytest test suite.  A manually maintained
  # list silently allowed most of the agent tests to be skipped by colcon.
  file(GLOB AGENT_TEST_FILES CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_*.py"
  )
  foreach(TEST_FILE IN LISTS AGENT_TEST_FILES)
    get_filename_component(TEST_NAME "${TEST_FILE}" NAME_WE)
    ament_add_pytest_test(
      "${TEST_NAME}"
      "${TEST_FILE}"
      ENV "${PYTEST_ENV}"
    )
  endforeach()
endif()

ament_package()
