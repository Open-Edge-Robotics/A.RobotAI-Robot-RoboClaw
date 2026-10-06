"""robo_claw_grpc 패키지"""

# serve()는 rclpy 등 무거운 의존성을 필요로 하므로, 패키지 임포트 시점이
# 아닌 명시적 import 시에만 로드되도록 지연 임포트를 사용한다.
# 이를 통해 테스트 등 ROS2 환경 없이도 하위 모듈(config/repositories/services)
# 를 개별적으로 import 할 수 있다.

def __getattr__(name: str):
    if name == "serve":
        from robo_claw_grpc.server import serve as _serve
        return _serve
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["serve"]
