"""robo_claw_grpc 예외 클래스"""



class RoboClawGrpcError(Exception):
    def __init__(self, message: str = "An error occurred in robo_claw_grpc"):
        self.message = message
        super().__init__(self.message)


class DatabaseError(RoboClawGrpcError):
    def __init__(
        self,
        message: str = "Database error occurred",
        original_exception: Exception | None = None,
    ):
        self.original_exception = original_exception
        super().__init__(message)


class ResourceNotFoundError(RoboClawGrpcError):
    def __init__(self, resource_type: str, resource_id: str):
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__(f"{resource_type} with id '{resource_id}' not found")


class ConfigurationError(RoboClawGrpcError):
    def __init__(self, message: str = "Configuration error occurred"):
        super().__init__(message)
