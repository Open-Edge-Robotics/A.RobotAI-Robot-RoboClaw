"""데모 cue 설정/바이트 계약 테스트(순수 로직, ROS 불필요)."""

import pytest
from robo_claw_agent.skills.demo_signal import protocol as cue

pytestmark = pytest.mark.unit

BASE = {
    "enabled": True,
    "protocol": "tcp",
    "host": "192.168.50.86",
    "port": 5005,
    "payload": "shoe pick",
    "terminator": "none",
    "timeout_sec": 1.0,
}


def _build(**overrides):
    values = dict(BASE)
    values.update(overrides)
    return cue.build_config(**values)


def test_enabled_endpoint_builds_utf8_bytes_without_terminator():
    config, reason = _build()

    assert reason == ""
    assert config is not None
    assert config.protocol == "tcp"
    assert config.endpoint() == "tcp://192.168.50.86:5005"
    assert config.payload == b"shoe pick"
    assert config.timeout_sec == 1.0


@pytest.mark.parametrize("value", [False, "false", "0", "off", None, ""])
def test_disabled_config_is_skipped(value):
    config, reason = _build(enabled=value)

    assert config is None
    assert reason == cue.REASON_DISABLED


@pytest.mark.parametrize("value", [True, "true", "1", "on"])
def test_enabled_accepts_common_boolean_forms(value):
    config, reason = _build(enabled=value)

    assert reason == ""
    assert config is not None


def test_terminator_appends_configured_bytes_only():
    assert _build(terminator="lf")[0].payload == b"shoe pick\n"
    assert _build(terminator="crlf")[0].payload == b"shoe pick\r\n"
    assert _build(terminator="none")[0].payload == b"shoe pick"


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"protocol": "icmp"}, cue.REASON_INVALID_PROTOCOL),
        ({"host": ""}, cue.REASON_INVALID_HOST),
        ({"host": "http://192.168.50.86"}, cue.REASON_INVALID_HOST),
        ({"host": "192.168.50.86/path"}, cue.REASON_INVALID_HOST),
        ({"host": "192.168.50.86\n"}, cue.REASON_INVALID_HOST),
        ({"port": 0}, cue.REASON_INVALID_PORT),
        ({"port": 65536}, cue.REASON_INVALID_PORT),
        ({"port": "abc"}, cue.REASON_INVALID_PORT),
        ({"port": True}, cue.REASON_INVALID_PORT),
        ({"port": 5005.5}, cue.REASON_INVALID_PORT),
        ({"payload": ""}, cue.REASON_INVALID_PAYLOAD),
        ({"payload": "x" * 200}, cue.REASON_INVALID_PAYLOAD),
        ({"payload": "shoe\npick"}, cue.REASON_INVALID_PAYLOAD),
        ({"payload": "shoe\x00pick"}, cue.REASON_INVALID_PAYLOAD),
        ({"payload": 42}, cue.REASON_INVALID_PAYLOAD),
        ({"terminator": "cr"}, cue.REASON_INVALID_TERMINATOR),
        ({"timeout_sec": 0.0}, cue.REASON_INVALID_TIMEOUT),
        ({"timeout_sec": 5.0}, cue.REASON_INVALID_TIMEOUT),
        ({"timeout_sec": "abc"}, cue.REASON_INVALID_TIMEOUT),
    ],
)
def test_invalid_settings_are_skipped_with_reason(overrides, reason):
    config, actual = _build(**overrides)

    assert config is None
    assert actual == reason


def test_payload_limit_is_bytes_not_characters():
    """멀티바이트 문자는 바이트 상한으로 판정한다."""
    assert _build(payload="가" * 42)[0].payload == ("가" * 42).encode("utf-8")  # 126 bytes
    assert _build(payload="가" * 43)[0] is None  # 129 bytes


def test_result_reports_endpoint_and_size_without_payload_text():
    config, _reason = _build()

    sent = cue.result(cue.STATUS_SENT, config)
    assert sent["status"] == "sent"
    assert sent["bytes"] == len(b"shoe pick")
    assert sent["host"] == "192.168.50.86"
    assert sent["port"] == 5005
    assert "payload" not in sent

    skipped = cue.result(cue.STATUS_SKIPPED, None, reason=cue.REASON_DISABLED)
    assert skipped == {"status": "skipped", "reason": "disabled", "bytes": 0}
