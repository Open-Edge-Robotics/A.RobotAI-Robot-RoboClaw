from types import SimpleNamespace

from robo_claw_agent.skills.hri_skill import SendMessageSkill


class _FakeFuture:
    def __init__(self, result):
        self._result = result

    def add_done_callback(self, cb):
        # 동기적으로 즉시 완료 처리 (call_service의 threading.Event를 즉시 set)
        cb(self)

    def result(self):
        return self._result


class _FakeSendMsgClient:
    def __init__(self, *, ready=True, response=None):
        self._ready = ready
        self._response = response

    def wait_for_service(self, timeout_sec):
        return self._ready

    def call_async(self, req):
        return _FakeFuture(self._response)


class _FakeNode:
    def __init__(self, client):
        self._send_msg_client = client


def test_send_message_skill_succeeds():
    skill = SendMessageSkill()
    response = SimpleNamespace(success=True, error_message="")
    skill.node = _FakeNode(_FakeSendMsgClient(ready=True, response=response))

    result = skill.execute({"message": "hello"})

    assert result["success"] is True
    assert result["message"] == "메신저로 전송 완료"


def test_send_message_skill_fails_when_service_not_ready():
    skill = SendMessageSkill()
    skill.node = _FakeNode(_FakeSendMsgClient(ready=False))

    result = skill.execute({"message": "hello"})

    assert result["success"] is False
    assert "실패" in result["message"]


def test_send_message_skill_fails_when_client_missing():
    skill = SendMessageSkill()
    skill.node = SimpleNamespace()  # _send_msg_client 속성 없음

    result = skill.execute({"message": "hello"})

    assert result["success"] is False
    assert "전송 클라이언트" in result["message"]


def test_send_message_skill_reports_response_error():
    skill = SendMessageSkill()
    response = SimpleNamespace(success=False, error_message="채널 연결 끊김")
    skill.node = _FakeNode(_FakeSendMsgClient(ready=True, response=response))

    result = skill.execute({"message": "hello"})

    assert result["success"] is False
    assert "채널 연결 끊김" in result["message"]


def test_send_message_skill_requires_message_or_file():
    skill = SendMessageSkill()
    skill.node = _FakeNode(_FakeSendMsgClient(ready=True))

    result = skill.execute({})

    assert result["success"] is False
    assert "전송할 내용" in result["message"]


def test_send_message_input_schema_accepts_null_file_path():
    skill = SendMessageSkill()
    valid, message = skill.validate_input_schema({"message": "test", "file_path": None})
    assert valid is True
    assert message == ""


def test_send_message_input_schema_accepts_null_message():
    skill = SendMessageSkill()
    valid, message = skill.validate_input_schema({"message": None, "file_path": "/tmp/test.png"})
    assert valid is True
    assert message == ""
