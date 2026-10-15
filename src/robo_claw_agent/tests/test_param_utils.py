"""공용 coercion/battery 헬퍼 계약 테스트.

여러 스킬 모듈이 복사해 갖고 있던 ``_bool_param``/``_bounded_timeout``/
``_read_sysfs_battery`` 를 ``skills._param_utils``/``skills._battery`` 로 통합했다.
통합 이후에도 단일 구현을 쓰는지, 그리고 경계값이 기존과 동일한지 고정한다.
"""

import pytest
from robo_claw_agent.skills import _battery, _param_utils

pytestmark = pytest.mark.unit


class TestBoolParam:
    def test_none_returns_default(self):
        assert _param_utils.bool_param(None, True) is True
        assert _param_utils.bool_param(None, False) is False

    def test_bool_passthrough(self):
        assert _param_utils.bool_param(True, False) is True
        assert _param_utils.bool_param(False, True) is False

    @pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
    def test_truthy_strings(self, value):
        assert _param_utils.bool_param(value, False) is True

    @pytest.mark.parametrize("value", ["0", "false", "No", " OFF "])
    def test_falsy_strings(self, value):
        assert _param_utils.bool_param(value, True) is False

    def test_other_values_use_builtin_truthiness(self):
        assert _param_utils.bool_param(1, False) is True
        assert _param_utils.bool_param(0, True) is False
        assert _param_utils.bool_param("maybe", True) is True


class TestBoundedTimeout:
    def test_none_returns_default(self):
        assert (
            _param_utils.bounded_timeout(None, default=5.0, minimum=1.0, maximum=10.0) == 5.0
        )

    def test_bool_is_rejected(self):
        assert _param_utils.bounded_timeout(True, default=5.0, minimum=1.0, maximum=10.0) is None

    def test_out_of_range_is_rejected(self):
        assert _param_utils.bounded_timeout(0.5, default=5.0, minimum=1.0, maximum=10.0) is None
        assert _param_utils.bounded_timeout(11.0, default=5.0, minimum=1.0, maximum=10.0) is None

    def test_non_numeric_is_rejected(self):
        assert (
            _param_utils.bounded_timeout("abc", default=5.0, minimum=1.0, maximum=10.0) is None
        )

    def test_nan_is_rejected(self):
        assert (
            _param_utils.bounded_timeout(float("nan"), default=5.0, minimum=1.0, maximum=10.0)
            is None
        )

    def test_in_range_returns_float(self):
        assert _param_utils.bounded_timeout("3.5", default=5.0, minimum=1.0, maximum=10.0) == 3.5


class TestSingleImplementation:
    def test_cloid_skills_share_bounded_timeout(self):
        from robo_claw_agent.skills import cloid_motion_skill, cloid_packing_pose_skill

        assert (
            cloid_motion_skill._bounded_timeout is cloid_packing_pose_skill._bounded_timeout
        )
        assert cloid_motion_skill._bounded_timeout is _param_utils.bounded_timeout

    def test_sequence_params_delegates_to_shared_bool_param(self):
        from robo_claw_agent.skills.manipulation_skill.sequence import params

        for value in (None, True, False, "yes", "no", 1, 0):
            assert params._bool_param(value, True) == _param_utils.bool_param(value, True)


class TestReadSysfsBattery:
    def test_reads_capacity_from_battery_supply(self, tmp_path, monkeypatch):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("87\n")

        monkeypatch.setattr(_battery, "BATTERY_SUPPLY_GLOB", str(tmp_path))

        assert _battery.read_sysfs_battery() == 87.0

    def test_returns_default_when_no_battery(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_battery, "BATTERY_SUPPLY_GLOB", str(tmp_path))

        assert _battery.read_sysfs_battery(default=12.5) == 12.5
