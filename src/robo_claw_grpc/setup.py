from setuptools import find_packages, setup

package_name = "robo_claw_grpc"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools", "grpcio-tools", "grpcio", "sqlmodel", "aiosqlite", "psutil"],
    zip_safe=True,
    maintainer="todo",
    maintainer_email="todo@todo.com",
    description="robo_claw gRPC 서버 — robo_mcp 연동을 위한 RosGrpc 서비스 제공",
    license="TODO: License declaration",
    entry_points={
        "console_scripts": ["robo_claw_grpc = robo_claw_grpc.robo_claw_grpc:main"],
    },
)
