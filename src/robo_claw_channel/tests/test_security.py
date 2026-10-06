import pytest
from robo_claw_channel.security import parse_string_list


def test_parse_string_list_valid():
    assert parse_string_list('["127.0.0.1/32"]', parameter_name="test") == ["127.0.0.1/32"]
    assert parse_string_list('["a", "b"]', parameter_name="test") == ["a", "b"]


def test_parse_string_list_humble_quoted():
    # humble Docker: ros2 launch 가 := 값을 raw 로 전달해 yamlStringArg 의
    # 작은따옴표가 리터럴로 남는 경우.
    assert parse_string_list("'[\"127.0.0.1/32\"]'", parameter_name="test") == ["127.0.0.1/32"]
    assert parse_string_list("'[\"a\", \"b\"]'", parameter_name="test") == ["a", "b"]


def test_parse_string_list_python_repr():
    # jazzy: raw JSON 이 YAML 컬렉션으로 해석된 뒤 str() 로 문자열화된 repr.
    assert parse_string_list("['127.0.0.1/32', '::1/128']", parameter_name="test") == [
        "127.0.0.1/32",
        "::1/128",
    ]

def test_parse_string_list_empty():
    assert parse_string_list('', parameter_name="test") == []
    assert parse_string_list('   ', parameter_name="test") == []
    assert parse_string_list('""', parameter_name="test") == []
    assert parse_string_list("''", parameter_name="test") == []
    assert parse_string_list('  ""  ', parameter_name="test") == []
    assert parse_string_list("  ''  ", parameter_name="test") == []
    assert parse_string_list(None, parameter_name="test") == []

def test_parse_string_list_invalid():
    with pytest.raises(ValueError):
        parse_string_list('{"a": 1}', parameter_name="test")
    with pytest.raises(ValueError):
        parse_string_list('invalid-json', parameter_name="test")
