import time
import uuid


def generate_index() -> str:
    timestamp = time.time_ns()
    unique_suffix = str(uuid.uuid4())[:8]
    return f"{timestamp}-{unique_suffix}"
