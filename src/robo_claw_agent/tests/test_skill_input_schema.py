from robo_claw_agent.skill_manager import BaseSkill, SkillManager


class PreconditionsSkill(BaseSkill):
    name = "precondition_test"
    preconditions = ("ready",)

    def check_preconditions(self, params):
        return bool(params.get("ready")), "ready 상태가 필요합니다."

    def execute(self, params):
        return self.success_result("ok")


class SchemaSkill(BaseSkill):
    name = "schema_test"
    description = "schema test"
    input_schema = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["a", "b"]},
            "x": {"type": "number"},
        },
        "oneOf": [{"required": ["mode"]}, {"required": ["x"]}],
        "additionalProperties": False,
    }

    def execute(self, params):
        return self.success_result("ok", params=params)


def test_input_schema_accepts_one_of_and_types():
    skill = SchemaSkill()
    assert skill.validate_input_schema({"mode": "a"}) == (True, "")
    assert skill.validate_input_schema({"x": 1.5}) == (True, "")


def test_input_schema_rejects_missing_or_unknown_values():
    skill = SchemaSkill()
    valid, message = skill.validate_input_schema({})
    assert not valid
    assert "oneOf" in message

    valid, message = skill.validate_input_schema({"mode": "c"})
    assert not valid
    assert "허용 목록" in message

    valid, message = skill.validate_input_schema({"mode": "a", "extra": True})
    assert not valid
    assert "허용되지 않은" in message


def test_skill_manager_applies_preconditions_before_schema():
    manager = SkillManager()
    manager.register(PreconditionsSkill())

    result = manager.execute("precondition_test", {})
    assert not result.success
    assert result.result_data["error_type"] == "SkillPreconditionError"
    assert "ready 상태" in result.message

    result = manager.execute("precondition_test", {"ready": True})
    assert result.success


def test_skill_manager_applies_schema_before_execution():
    manager = SkillManager()
    manager.register(SchemaSkill())

    result = manager.execute("schema_test", {"mode": "invalid"})
    assert not result.success
    assert "파라미터 형식 오류" in result.message
    assert result.result_data["error_type"] == "ParameterSchemaError"
    assert result.result_data["schema_error"]

    result = manager.execute("schema_test", {"mode": "a"})
    assert result.success


def test_input_schema_ignores_null_for_optional_properties():
    skill = SchemaSkill()
    valid, message = skill.validate_input_schema({"mode": "a", "x": None})
    assert valid is True
    assert message == ""
