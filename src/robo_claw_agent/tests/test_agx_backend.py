"""AGX 드라이버 토픽 백엔드 단위 테스트.

실제 드라이버/하드웨어 없이 토픽 발행 내용과 완료 판정/실패 경로를 검증한다.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from geometry_msgs.msg import PoseStamped
from robo_claw_agent.manipulation_runtime import agx_backend
from robo_claw_agent.manipulation_runtime.agx_backend import AgxDriverBackend
from robo_claw_agent.manipulation_runtime.exceptions import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
)
from robo_claw_agent.manipulation_runtime.types import ManipulationConfig, PoseGoal
from sensor_msgs.msg import JointState

pytestmark = pytest.mark.unit


class _RecordingPublisher:
    def __init__(self) -> None:
        self.messages: list[object] = []
        #: 발행 직후 1회 실행되는 훅. 드라이버가 명령에 반응해 진단을 발행하는 상황을 모사한다.
        self.on_publish: Any = None

    def publish(self, msg: object) -> None:
        self.messages.append(msg)
        hook = self.on_publish
        if hook is not None:
            self.on_publish = None
            hook()


class _DoneFuture:
    def __init__(self, response: object = None) -> None:
        self._response = response

    def done(self) -> bool:
        return True

    def result(self) -> object:
        return self._response

    def cancel(self) -> None:
        return None


class _FakeClient:
    def __init__(self, service_available: bool = True, response: object = None) -> None:
        self.service_available = service_available
        self.calls = 0
        self.requests: list[object] = []
        self.response = response

    def wait_for_service(self, timeout_sec: float) -> bool:
        del timeout_sec
        return self.service_available

    def call_async(self, request: object) -> _DoneFuture:
        self.calls += 1
        self.requests.append(request)
        return _DoneFuture(self.response)


class _FakeNode:
    """create_publisher/create_subscription/create_client 만 제공하는 최소 노드 대역."""

    def __init__(self, service_available: bool = True) -> None:
        self.publishers: dict[str, list[_RecordingPublisher]] = {}
        self.subscriptions: dict[str, object] = {}
        self.clients: dict[str, _FakeClient] = {}
        self._service_available = service_available

    def create_client(
        self, srv_type: object, name: str, *args: object, **kwargs: object
    ) -> _FakeClient:
        del srv_type, args, kwargs
        if name not in self.clients:
            self.clients[name] = _FakeClient(self._service_available)
        return self.clients[name]

    def create_publisher(
        self, msg_type: object, topic: str, qos: int, *args: object, **kwargs: object
    ) -> _RecordingPublisher:
        del msg_type, qos, args, kwargs
        publisher = _RecordingPublisher()
        self.publishers.setdefault(topic, []).append(publisher)
        return publisher

    def create_subscription(
        self,
        msg_type: object,
        topic: str,
        callback: object,
        qos: int,
        *args: object,
        **kwargs: object,
    ) -> object:
        del msg_type, qos, args, kwargs
        self.subscriptions[topic] = callback
        return callback


def _config(**overrides: object) -> ManipulationConfig:
    defaults: dict[str, object] = {
        "backend": "agx",
        "base_frame": "base_link",
        "agx_motion_timeout_sec": 0.2,
        "agx_feedback_timeout_sec": 0.05,
        "agx_joint_tolerance_rad": 0.02,
        "agx_pose_tolerance_m": 0.01,
        "named_pose_joint_values": {"home": {"joint1": 0.0, "joint2": 0.0}},
    }
    defaults.update(overrides)
    return ManipulationConfig(**defaults)  # type: ignore[arg-type]


def _published_messages(node: _FakeNode, topic: str) -> list[object]:
    return [msg for publisher in node.publishers.get(topic, []) for msg in publisher.messages]


def _joint_state(names: list[str], positions: list[float]) -> JointState:
    msg = JointState()
    msg.name = list(names)
    msg.position = list(positions)
    return msg


def _tcp_pose(x: float, y: float, z: float) -> PoseStamped:
    msg = PoseStamped()
    msg.header.frame_id = "base_link"
    msg.pose.position.x = x
    msg.pose.position.y = y
    msg.pose.position.z = z
    msg.pose.orientation.w = 1.0
    return msg


def _pose_goal(x: float, y: float, z: float, frame_id: str = "base_link") -> PoseGoal:
    return PoseGoal(
        frame_id=frame_id,
        position={"x": x, "y": y, "z": z},
        orientation={"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    )


def test_move_to_joint_target_publishes_and_returns_when_feedback_reached() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2"], [0.0, 0.0])
    )
    backend.move_to_joint_target("arm", {"joint1": 0.0, "joint2": 0.0})

    published = node.publishers[config.agx_move_joint_topic][0].messages
    assert len(published) == 1
    assert isinstance(published[0], JointState)
    assert list(published[0].name) == ["joint1", "joint2"]
    assert list(published[0].position) == [0.0, 0.0]

    assert not _published_messages(node, config.agx_move_pose_topic)
    assert not _published_messages(node, config.agx_move_linear_topic)


def test_move_to_joint_target_raises_when_target_not_reached() -> None:
    node = _FakeNode()
    config = _config(agx_motion_timeout_sec=0.15)
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    with pytest.raises(ManipulationError, match="도달하지 못했습니다"):
        backend.move_to_joint_target("arm", {"joint1": 1.0})


def test_move_to_joint_target_raises_when_feedback_missing() -> None:
    node = _FakeNode()
    config = _config(agx_motion_timeout_sec=5.0, agx_feedback_timeout_sec=0.05)
    backend = AgxDriverBackend(node, config)

    with pytest.raises(ManipulationError, match="피드백을 받지 못했습니다"):
        backend.move_to_joint_target("arm", {"joint1": 0.0})


def test_move_to_named_pose_uses_configured_joint_map() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2"], [0.0, 0.0])
    )
    backend.move_to_named_pose("arm", "home")

    published = node.publishers[config.agx_move_joint_topic][0].messages
    assert list(published[0].name) == ["joint1", "joint2"]


def test_move_to_named_pose_home_calls_driver_service() -> None:
    node = _FakeNode()
    config = _config(named_pose_joint_values={})
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2"], [0.0, 0.0])
    )
    backend.move_to_named_pose("arm", "home")

    client = node.clients[config.agx_home_service]
    assert client.calls == 1
    assert not _published_messages(node, config.agx_move_joint_topic)


def test_move_to_named_pose_home_reports_missing_service() -> None:
    config = _config(named_pose_joint_values={})
    node = _FakeNode(service_available=True)
    node.clients[config.agx_home_service] = _FakeClient(service_available=False)
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2"], [0.0, 0.0])
    )
    with pytest.raises(ManipulationRuntimeUnavailableError, match="move_home"):
        backend.move_to_named_pose("arm", "home")


def test_move_to_named_pose_without_joint_map_is_rejected() -> None:
    node = _FakeNode()
    backend = AgxDriverBackend(node, _config(named_pose_joint_values={}))

    with pytest.raises(ManipulationConfigError, match="조인트 값을 알 수 없습니다"):
        backend.move_to_named_pose("arm", "ready")


def test_move_to_pose_target_uses_pose_topic_and_waits_for_tcp_feedback() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_tcp_pose_topic](  # type: ignore[operator]
        _tcp_pose(0.3, 0.0, 0.4)
    )
    backend.move_to_pose_target("arm", _pose_goal(0.3, 0.0, 0.4), "link7")

    published = node.publishers[config.agx_move_pose_topic][0].messages
    assert isinstance(published[0], PoseStamped)
    assert published[0].header.frame_id == "base_link"
    assert published[0].pose.position.x == pytest.approx(0.3)
    assert not _published_messages(node, config.agx_move_linear_topic)


def test_move_to_pose_target_uses_linear_topic_for_cartesian() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_tcp_pose_topic](  # type: ignore[operator]
        _tcp_pose(0.0, 0.0, 0.0)
    )
    backend.move_to_pose_target("arm", _pose_goal(0.0, 0.0, 0.0), "link7", cartesian=True)

    assert not _published_messages(node, config.agx_move_pose_topic)
    assert len(node.publishers[config.agx_move_linear_topic][0].messages) == 1


def test_move_to_pose_target_rejects_other_frames() -> None:
    node = _FakeNode()
    backend = AgxDriverBackend(node, _config())

    with pytest.raises(ManipulationConfigError, match="base frame"):
        backend.move_to_pose_target("arm", _pose_goal(0.1, 0.0, 0.2, "map"), "link7")


def test_pose_target_requires_all_position_components() -> None:
    node = _FakeNode()
    backend = AgxDriverBackend(node, _config())
    goal = PoseGoal(
        frame_id="base_link",
        position={"x": 0.1, "y": 0.0},
        orientation={"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    )

    with pytest.raises(ManipulationConfigError, match="'z' 값이 없습니다"):
        backend.move_to_pose_target("arm", goal, "link7")


def test_move_enables_arm_and_control_gate_before_motion() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2"], [0.0, 0.0])
    )
    backend.move_to_joint_target("arm", {"joint1": 0.0, "joint2": 0.0})
    backend.move_to_joint_target("arm", {"joint1": 0.0, "joint2": 0.0})

    enable_client = node.clients[config.agx_enable_service]
    gate_client = node.clients[config.agx_control_enable_service]
    assert enable_client.calls == 1
    assert getattr(enable_client.requests[0], "data", None) is True
    assert gate_client.calls == 1
    assert getattr(gate_client.requests[0], "data", None) is True


def test_move_raises_when_enable_service_returns_false() -> None:
    class _FailedResp:
        success = False
        message = "Agx_arm is not connected"

    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)
    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2"], [0.0, 0.0])
    )
    node.create_client(object, config.agx_enable_service).response = _FailedResp()

    with pytest.raises(ManipulationError, match="Agx_arm is not connected"):
        backend.move_to_named_pose("arm", "home")

    assert not _published_messages(node, config.agx_move_joint_topic)


def test_move_to_joint_target_preserves_unspecified_arm_joints_from_feedback() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2", "joint3"], [0.5, -0.35, 0.2])
    )
    backend.move_to_joint_target("arm", {"joint1": 0.5})

    published = node.publishers[config.agx_move_joint_topic][0].messages
    assert len(published) >= 1
    msg = published[0]
    assert isinstance(msg, JointState)
    joint_map = dict(zip(msg.name, msg.position, strict=False))
    assert joint_map == {
        "joint1": pytest.approx(0.5),
        "joint2": pytest.approx(-0.35),
        "joint3": pytest.approx(0.2),
    }


def test_move_to_joint_target_publishes_only_gripper_when_targets_is_gripper_only() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2", "gripper"], [0.5, -0.35, 0.05])
    )
    backend.move_to_joint_target("gripper", {"gripper": 0.05})

    published = node.publishers[config.agx_move_joint_topic][0].messages
    assert len(published) >= 1
    msg = published[0]
    assert isinstance(msg, JointState)
    assert list(msg.name) == ["gripper"]
    assert list(msg.position) == [pytest.approx(0.05)]


class _FakeClock:
    """time 모듈 대역. monotonic/sleep 만 제공한다."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_joint_state_feedback_logging_is_throttled(monkeypatch, caplog) -> None:
    """피드백 콜백마다 INFO 를 남기지 않고 일정 간격으로만 남긴다."""
    node = _FakeNode()
    config = _config()
    AgxDriverBackend(node, config)
    callback = node.subscriptions[config.agx_joint_state_topic]

    clock = _FakeClock()
    monkeypatch.setattr(agx_backend, "time", clock)

    with caplog.at_level(logging.INFO, logger=agx_backend.__name__):
        # 드라이버가 100Hz 로 발행하는 상황을 모사한다.
        for _ in range(100):
            callback(_joint_state(["gripper", "joint1"], [0.02, 0.01]))
            clock.now += 0.01

        clock.now += agx_backend._FEEDBACK_LOG_INTERVAL_SEC
        callback(_joint_state(["gripper", "joint1"], [0.03, 0.02]))

    feedback_logs = [
        record.message for record in caplog.records if "AGX feedback" in record.message
    ]
    assert len(feedback_logs) == 2
    assert "gripper=0.020000" in feedback_logs[0]
    assert "gripper=0.030000" in feedback_logs[1]


# ── home 검증 / arm 상태 감시 / 위치만 지정한 pose 목표 ──


def _arm_status_entry(
    level: int,
    message: str,
    name: str = "arm",
    code: int | str | None = None,
    *,
    err_status: int | str | None = None,
    motion_status: int | str | None = None,
):
    """DiagnosticStatus 한 엔트리(팔 상태 진단의 단일 status)."""
    from diagnostic_msgs.msg import DiagnosticStatus, KeyValue

    status = DiagnosticStatus()
    status.level = level
    status.name = name
    status.message = message
    values = []
    if code is not None:
        values.append(KeyValue(key="arm_status", value=str(code)))
    if err_status is not None:
        values.append(KeyValue(key="err_status", value=str(err_status)))
    if motion_status is not None:
        values.append(KeyValue(key="motion_status", value=str(motion_status)))
    status.values = values
    return status


def _arm_status_array(*statuses):
    from diagnostic_msgs.msg import DiagnosticArray

    msg = DiagnosticArray()
    msg.status = list(statuses)
    return msg


def _arm_status(
    level: int,
    message: str,
    name: str = "arm",
    code: int | str | None = None,
    *,
    err_status: int | str | None = None,
    motion_status: int | str | None = None,
):
    return _arm_status_array(
        _arm_status_entry(
            level,
            message,
            name,
            code,
            err_status=err_status,
            motion_status=motion_status,
        )
    )


def _motor_status(*values: tuple[str, str], level: int = 0):
    """모터 진단 DiagnosticArray(단일 status)."""
    from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue

    status = DiagnosticStatus()
    status.level = level
    status.name = "motor"
    status.values = [KeyValue(key=key, value=value) for key, value in values]
    msg = DiagnosticArray()
    msg.status = [status]
    return msg


def _motor_topic(config: ManipulationConfig) -> str:
    return str(getattr(config, "agx_motor_status_topic", "feedback/motor_status"))


def test_move_to_named_pose_home_fails_when_arm_does_not_move() -> None:
    """드라이버 /move_home 은 Empty 응답만 주므로 실제 자세 수렴을 확인해야 한다."""
    node = _FakeNode()
    config = _config(named_pose_joint_values={}, agx_motion_timeout_sec=0.15)
    backend = AgxDriverBackend(node, config)

    # 팔이 home(zero) 자세가 아닌 상태에서 피드백이 전혀 변하지 않는 상황
    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2"], [0.0, 1.2])
    )

    with pytest.raises(ManipulationError, match="도달하지 못했습니다"):
        backend.move_to_named_pose("arm", "home")

    assert node.clients[config.agx_home_service].calls == 1


def test_move_to_named_pose_home_reports_already_at_target() -> None:
    node = _FakeNode()
    config = _config(named_pose_joint_values={})
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2", "gripper"], [0.0, 0.0, 0.05])
    )
    backend.move_to_named_pose("arm", "home")

    assert backend.last_motion is not None
    assert backend.last_motion.already_at_target is True


def test_move_rejects_when_arm_status_is_error() -> None:
    """비상정지·통신 오류 같은 실제 모션 차단 상태는 즉시 거부한다."""
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    node.subscriptions[config.agx_arm_status_topic](  # type: ignore[operator]
        _arm_status(
            2,
            "arm_status=JOINT_COMMUNICATION_ERR(0x5), err_status=0",
            code=0x05,
        )
    )

    with pytest.raises(ManipulationError, match="JOINT_COMMUNICATION_ERR"):
        backend.move_to_joint_target("arm", {"joint1": 0.5})


def test_move_to_joint_target_allows_stale_no_solution() -> None:
    """NO_SOLUTION 은 직전 pose(IK) 명령의 흔적이므로 조인트 이동을 막지 않는다."""
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    node.subscriptions[config.agx_arm_status_topic](  # type: ignore[operator]
        _arm_status(
            2,
            "arm_status=NO_SOLUTION(0x2), err_status=0",
            code=0x02,
            err_status=0,
            motion_status=0,
        )
    )

    backend.move_to_joint_target("arm", {"joint1": 0.0})
    assert node.publishers[config.agx_move_joint_topic][0].messages


def test_move_to_named_pose_allows_stale_no_solution() -> None:
    """프리셋(조인트 맵) home/stow 이동도 NO_SOLUTION 에 막히지 않는다."""
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2"], [0.0, 0.0])
    )
    node.subscriptions[config.agx_arm_status_topic](  # type: ignore[operator]
        _arm_status(
            2,
            "arm_status=NO_SOLUTION(0x2), err_status=0",
            code=0x02,
            err_status=0,
            motion_status=0,
        )
    )

    backend.move_to_named_pose("arm", "home")
    assert node.publishers[config.agx_move_joint_topic][0].messages


def test_move_to_pose_target_rejects_stale_no_solution() -> None:
    """pose(자세) 목표는 NO_SOLUTION 에서 IK 가 실패하므로 먼저 거부한다."""
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_tcp_pose_topic](  # type: ignore[operator]
        _tcp_pose(0.3, 0.0, 0.4)
    )
    node.subscriptions[config.agx_arm_status_topic](  # type: ignore[operator]
        _arm_status(
            2,
            "arm_status=NO_SOLUTION(0x2), err_status=0",
            code=0x02,
            err_status=0,
            motion_status=0,
        )
    )

    with pytest.raises(ManipulationError, match=r"자세\(IK\)"):
        backend.move_to_pose_target("arm", _pose_goal(0.3, 0.0, 0.4), "link7")

    assert not _published_messages(node, config.agx_move_pose_topic)


def test_gripper_only_move_ignores_arm_status_error() -> None:
    """그리퍼 단독 제어는 팔 오류와 무관하게 동작해야 한다."""
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "gripper"], [0.0, 0.0])
    )
    node.subscriptions[config.agx_arm_status_topic](  # type: ignore[operator]
        _arm_status(2, "arm_status=JOINT_COMMUNICATION_ERR(0x5)", code=0x05)
    )
    backend.move_to_joint_target("gripper", {"gripper": 0.0})


def test_move_to_pose_target_keeps_current_orientation_when_not_explicit() -> None:
    """위치만 지정한 요청은 identity 자세가 아니라 현재 자세를 유지해야 한다."""
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    current = _tcp_pose(0.3, 0.0, 0.4)
    current.pose.orientation.x = 0.0
    current.pose.orientation.y = 0.0
    current.pose.orientation.z = 0.7071068
    current.pose.orientation.w = 0.7071068
    node.subscriptions[config.agx_tcp_pose_topic](current)  # type: ignore[operator]

    goal = PoseGoal(
        frame_id="base_link",
        position={"x": 0.3, "y": 0.0, "z": 0.4},
        orientation={"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        orientation_explicit=False,
    )
    backend.move_to_pose_target("arm", goal, "link7")

    published = node.publishers[config.agx_move_pose_topic][0].messages
    assert published[0].pose.orientation.z == pytest.approx(0.7071068)
    assert published[0].pose.orientation.w == pytest.approx(0.7071068)


def test_pose_goal_from_params_marks_orientation_explicit() -> None:
    from robo_claw_agent.manipulation_runtime import pose_goal_from_params

    implicit = pose_goal_from_params({"x": 0.1, "y": 0.0, "z": 0.2})
    assert implicit.orientation_explicit is False

    explicit = pose_goal_from_params({"x": 0.1, "y": 0.0, "z": 0.2, "qw": 1.0})
    assert explicit.orientation_explicit is True


# ── 진단 엔트리별 판정 / 이동 중 새 진단 / 피드백 신선도 ──


def test_joint_move_rejects_hardware_error_masked_by_no_solution_in_another_status() -> None:
    """한 엔트리의 NO_SOLUTION 이 다른 엔트리의 통신 오류를 가리면 안 된다."""
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    node.subscriptions[config.agx_arm_status_topic](  # type: ignore[operator]
        _arm_status_array(
            _arm_status_entry(
                2, "arm_status=NO_SOLUTION(0x2), err_status=0", code="02", err_status="0"
            ),
            _arm_status_entry(
                2,
                "arm_status=JOINT_COMMUNICATION_ERR(0x5), err_status=0",
                name="joint7",
                code="0x5",
                err_status="0",
            ),
        )
    )

    with pytest.raises(ManipulationError, match="JOINT_COMMUNICATION_ERR"):
        backend.move_to_joint_target("arm", {"joint1": 0.0})

    assert not _published_messages(node, config.agx_move_joint_topic)


def test_joint_move_rejects_no_solution_when_err_status_is_nonzero() -> None:
    """NO_SOLUTION 이라도 err_status 가 0 이 아니면 실제 오류다."""
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    node.subscriptions[config.agx_arm_status_topic](  # type: ignore[operator]
        _arm_status(
            2,
            "arm_status=NO_SOLUTION(0x2), err_status=0x5",
            code=0x02,
            err_status="0x5",
        )
    )

    with pytest.raises(ManipulationError, match="err_status=5"):
        backend.move_to_joint_target("arm", {"joint1": 0.0})

    assert not _published_messages(node, config.agx_move_joint_topic)


@pytest.mark.parametrize("code", [0x03, 0x04])
def test_joint_move_rejects_singularity_and_target_limit(code: int) -> None:
    """0x03 SINGULARITY_POINT / 0x04 TARGET_POS_EXCEEDS_LIMIT 는 더 이상 면제하지 않는다."""
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2"], [0.0, 0.0])
    )
    node.subscriptions[config.agx_arm_status_topic](  # type: ignore[operator]
        _arm_status(2, f"arm_status code 0x{code:02x}", code=code, err_status="0")
    )

    with pytest.raises(ManipulationError, match="팔 드라이버 상태 오류"):
        backend.move_to_named_pose("arm", "home")

    assert not _published_messages(node, config.agx_move_joint_topic)


def test_joint_move_completes_while_motion_status_reports_not_arrived(monkeypatch) -> None:
    """motion_status=1(아직 도착하지 않음)은 정상 진행이므로 계속 대기한다."""
    clock = _FakeClock()
    monkeypatch.setattr(agx_backend, "time", clock)
    node = _FakeNode()
    config = _config(agx_motion_timeout_sec=1.0, agx_feedback_timeout_sec=0.1)
    backend = AgxDriverBackend(node, config)
    joint_callback = node.subscriptions[config.agx_joint_state_topic]
    arm_callback = node.subscriptions[config.agx_arm_status_topic]
    joint_callback(_joint_state(["joint1"], [0.0]))  # type: ignore[operator]

    positions = iter([0.3, 0.6, 1.0])

    def step() -> None:
        clock.now += 0.05
        joint_callback(_joint_state(["joint1"], [next(positions, 1.0)]))  # type: ignore[operator]
        arm_callback(  # type: ignore[operator]
            _arm_status(0, "not arrived", code="0", err_status="0", motion_status="1")
        )

    monkeypatch.setattr(backend, "_spin_once", step)

    backend.move_to_joint_target("arm", {"joint1": 1.0})

    publisher = node.publishers[config.agx_move_joint_topic][0]
    assert len(publisher.messages) == 1
    assert backend.last_motion is not None
    assert backend.last_motion.moved is True


def test_joint_move_times_out_with_single_publish_when_motion_status_never_arrives(
    monkeypatch,
) -> None:
    """motion_status=1 이 계속되어도 재발행 없이 한 번만 발행하고 타임아웃한다."""
    clock = _FakeClock()
    monkeypatch.setattr(agx_backend, "time", clock)
    node = _FakeNode()
    config = _config(agx_motion_timeout_sec=0.6, agx_feedback_timeout_sec=0.1)
    backend = AgxDriverBackend(node, config)
    joint_callback = node.subscriptions[config.agx_joint_state_topic]
    arm_callback = node.subscriptions[config.agx_arm_status_topic]
    joint_callback(_joint_state(["joint1"], [0.0]))  # type: ignore[operator]

    def step() -> None:
        clock.now += 0.05
        joint_callback(_joint_state(["joint1"], [0.0]))  # type: ignore[operator]
        arm_callback(  # type: ignore[operator]
            _arm_status(0, "not arrived", code="0", err_status="0", motion_status="1")
        )

    monkeypatch.setattr(backend, "_spin_once", step)

    with pytest.raises(ManipulationError) as excinfo:
        backend.move_to_joint_target("arm", {"joint1": 1.0})

    message = str(excinfo.value)
    assert "도달하지 못했습니다" in message
    assert "motion_status=1" in message
    assert "팔 상태 오류" not in message
    publisher = node.publishers[config.agx_move_joint_topic][0]
    assert len(publisher.messages) == 1
    assert backend.last_motion is None


def test_pre_existing_not_arrived_status_does_not_report_success_off_target(monkeypatch) -> None:
    """명령 전부터 motion_status=1 이어도 목표 미수렴을 성공으로 보고하지 않는다."""
    clock = _FakeClock()
    monkeypatch.setattr(agx_backend, "time", clock)
    node = _FakeNode()
    config = _config(agx_motion_timeout_sec=0.2, agx_feedback_timeout_sec=0.1)
    backend = AgxDriverBackend(node, config)
    joint_callback = node.subscriptions[config.agx_joint_state_topic]
    arm_callback = node.subscriptions[config.agx_arm_status_topic]
    joint_callback(_joint_state(["joint1"], [0.0]))  # type: ignore[operator]
    arm_callback(  # type: ignore[operator]
        _arm_status(0, "not arrived", code="0", err_status="0", motion_status="1")
    )

    def step() -> None:
        clock.now += 0.05
        joint_callback(_joint_state(["joint1"], [0.0]))  # type: ignore[operator]
        arm_callback(  # type: ignore[operator]
            _arm_status(0, "not arrived", code="0", err_status="0", motion_status="1")
        )

    monkeypatch.setattr(backend, "_spin_once", step)

    with pytest.raises(ManipulationError, match="도달하지 못했습니다"):
        backend.move_to_joint_target("arm", {"joint1": 1.0})

    assert backend.last_motion is None
    assert len(node.publishers[config.agx_move_joint_topic][0].messages) == 1


def test_joint_move_stops_publishing_when_new_hardware_error_arrives_during_wait() -> None:
    """도착 전 비트가 켜져 있어도 새 하드웨어 오류는 즉시 거부한다."""
    node = _FakeNode()
    config = _config(agx_motion_timeout_sec=0.3)
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    arm_callback = node.subscriptions[config.agx_arm_status_topic]
    publisher = node.publishers[config.agx_move_joint_topic][0]
    publisher.on_publish = lambda: arm_callback(  # type: ignore[operator]
        _arm_status(
            2,
            "arm_status=JOINT_COMMUNICATION_ERR(0x5)",
            code="0x5",
            err_status="0",
            motion_status="1",
        )
    )

    with pytest.raises(ManipulationError, match="JOINT_COMMUNICATION_ERR"):
        backend.move_to_joint_target("arm", {"joint1": 1.0})

    assert len(publisher.messages) == 1


def test_joint_move_stops_publishing_when_new_err_status_arrives_during_wait() -> None:
    node = _FakeNode()
    config = _config(agx_motion_timeout_sec=0.3)
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    arm_callback = node.subscriptions[config.agx_arm_status_topic]
    publisher = node.publishers[config.agx_move_joint_topic][0]
    publisher.on_publish = lambda: arm_callback(  # type: ignore[operator]
        _arm_status(0, "err_status=3", code="0", err_status="3", motion_status="1")
    )

    with pytest.raises(ManipulationError, match="err_status=3"):
        backend.move_to_joint_target("arm", {"joint1": 1.0})

    assert len(publisher.messages) == 1


def test_joint_move_fails_closed_when_control_topic_has_no_subscriber(monkeypatch) -> None:
    """제어 토픽 구독자가 없으면 명령을 발행하지 않고 실패한다."""
    clock = _FakeClock()
    monkeypatch.setattr(agx_backend, "time", clock)
    node = _FakeNode()
    config = _config(agx_feedback_timeout_sec=0.05)
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    publisher = node.publishers[config.agx_move_joint_topic][0]
    publisher.get_subscription_count = lambda: 0  # type: ignore[attr-defined]

    with pytest.raises(ManipulationError, match="구독자"):
        backend.move_to_joint_target("arm", {"joint1": 0.0})

    assert publisher.messages == []


def test_joint_move_waits_for_delayed_control_subscriber(monkeypatch) -> None:
    """agx_feedback_timeout_sec 안에 구독자가 나타나면 정상 발행한다."""
    clock = _FakeClock()
    monkeypatch.setattr(agx_backend, "time", clock)
    node = _FakeNode()
    config = _config(agx_feedback_timeout_sec=0.1)
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    publisher = node.publishers[config.agx_move_joint_topic][0]
    counts: list[int] = []

    def count() -> int:
        value = 1 if clock.now >= 1000.05 else 0
        counts.append(value)
        return value

    publisher.get_subscription_count = count  # type: ignore[attr-defined]

    backend.move_to_joint_target("arm", {"joint1": 0.0})

    assert counts == [0, 1]
    assert len(publisher.messages) == 1


def test_joint_move_fails_closed_when_control_topic_has_two_subscribers() -> None:
    """제어 대상이 모호한 다중 구독이면 발행하지 않고 거부한다."""
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    publisher = node.publishers[config.agx_move_joint_topic][0]
    publisher.get_subscription_count = lambda: 2  # type: ignore[attr-defined]

    with pytest.raises(ManipulationError, match="모호"):
        backend.move_to_joint_target("arm", {"joint1": 0.0})

    assert publisher.messages == []


def test_pose_move_fails_closed_when_control_topic_has_two_subscribers() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_tcp_pose_topic](  # type: ignore[operator]
        _tcp_pose(0.3, 0.0, 0.4)
    )
    publisher = node.publishers[config.agx_move_pose_topic][0]
    publisher.get_subscription_count = lambda: 2  # type: ignore[attr-defined]

    with pytest.raises(ManipulationError, match="모호"):
        backend.move_to_pose_target("arm", _pose_goal(0.3, 0.0, 0.4), "link7")

    assert publisher.messages == []


def test_fake_publisher_without_subscription_count_keeps_working() -> None:
    """get_subscription_count 가 없는 대역 publisher 는 기존처럼 발행한다."""
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    backend.move_to_joint_target("arm", {"joint1": 0.0})

    publisher = node.publishers[config.agx_move_joint_topic][0]
    assert not hasattr(publisher, "get_subscription_count")
    assert len(publisher.messages) == 1


def test_joint_move_is_attempted_when_not_arrived_status_predates_the_command() -> None:
    """명령 전부터 남은 motion_status=1(아직 도착하지 않음)은 새 조인트 이동을 막지 않는다."""
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    node.subscriptions[config.agx_arm_status_topic](  # type: ignore[operator]
        _arm_status(
            2,
            "motion_status=NOT_YET_ARRIVED(0x1)",
            code="0",
            err_status="0",
            motion_status="1",
        )
    )

    backend.move_to_joint_target("arm", {"joint1": 0.0})

    assert len(node.publishers[config.agx_move_joint_topic][0].messages) == 1
    assert backend.last_motion is not None
    assert backend.last_motion.already_at_target is True


def test_motor_status_subscription_uses_default_topic() -> None:
    """ "모터 진단 토픽은 새 ROS 파라미터 없이 getattr 기본값을 쓴다."""
    node = _FakeNode()
    config = _config()
    AgxDriverBackend(node, config)

    assert _motor_topic(config) in node.subscriptions


def test_move_rejects_before_enabling_when_motor_status_reports_collision() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[_motor_topic(config)](_motor_status(("collision", "true")))  # type: ignore[operator]
    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )

    with pytest.raises(ManipulationError, match="모터 오류"):
        backend.move_to_joint_target("arm", {"joint1": 0.0})

    assert config.agx_enable_service not in node.clients
    assert not _published_messages(node, config.agx_move_joint_topic)


def test_move_stops_publishing_when_motor_is_disabled_during_wait() -> None:
    node = _FakeNode()
    config = _config(agx_motion_timeout_sec=0.3)
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    motor_callback = node.subscriptions[_motor_topic(config)]
    publisher = node.publishers[config.agx_move_joint_topic][0]
    publisher.on_publish = lambda: motor_callback(_motor_status(("enabled", "false")))  # type: ignore[operator]

    with pytest.raises(ManipulationError, match="disabled"):
        backend.move_to_joint_target("arm", {"joint1": 1.0})

    assert len(publisher.messages) == 1


def test_move_rejects_non_finite_target_before_enabling() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )

    with pytest.raises(ManipulationConfigError, match="유한"):
        backend.move_to_joint_target("arm", {"joint1": float("nan")})

    assert config.agx_enable_service not in node.clients
    assert not _published_messages(node, config.agx_move_joint_topic)


def test_move_rejects_nan_in_preserved_feedback_before_enabling() -> None:
    """생략 관절을 피드백 값으로 보존할 때 NaN 이 섞이면 발행하지 않는다."""
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2"], [0.0, float("nan")])
    )

    with pytest.raises(ManipulationError, match="유한"):
        backend.move_to_joint_target("arm", {"joint1": 0.0})

    assert config.agx_enable_service not in node.clients
    assert not _published_messages(node, config.agx_move_joint_topic)


def test_nan_joint_feedback_is_not_treated_as_reached() -> None:
    """abs(nan - target) 비교는 False 이므로 NaN 을 목표 도달로 오판하면 안 된다."""
    node = _FakeNode()
    config = _config(named_pose_joint_values={}, agx_motion_timeout_sec=0.15)
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2"], [float("nan"), 0.0])
    )

    with pytest.raises(ManipulationError, match="도달하지 못했습니다"):
        backend.move_to_named_pose("arm", "home")


def test_stale_joint_feedback_is_not_treated_as_reached(monkeypatch) -> None:
    """agx_feedback_timeout_sec 보다 오래된 피드백은 도달 판정에 쓰지 않는다."""
    node = _FakeNode()
    config = _config(agx_motion_timeout_sec=0.15, agx_feedback_timeout_sec=0.05)
    backend = AgxDriverBackend(node, config)

    clock = _FakeClock()
    monkeypatch.setattr(agx_backend, "time", clock)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    clock.now += 1.0

    with pytest.raises(ManipulationError, match="피드백을 받지 못했습니다"):
        backend.move_to_joint_target("arm", {"joint1": 0.0})

    assert not _published_messages(node, config.agx_move_joint_topic)
    assert node.clients == {}


def test_stale_tcp_feedback_is_not_treated_as_reached(monkeypatch) -> None:
    node = _FakeNode()
    config = _config(agx_motion_timeout_sec=0.15, agx_feedback_timeout_sec=0.05)
    backend = AgxDriverBackend(node, config)

    clock = _FakeClock()
    monkeypatch.setattr(agx_backend, "time", clock)

    node.subscriptions[config.agx_tcp_pose_topic](_tcp_pose(0.3, 0.0, 0.4))  # type: ignore[operator]
    clock.now += 1.0

    with pytest.raises(ManipulationError, match="피드백을 받지 못했습니다"):
        backend.move_to_pose_target("arm", _pose_goal(0.3, 0.0, 0.4), "link7")

    assert not _published_messages(node, config.agx_move_pose_topic)
    assert node.clients == {}


def test_move_resets_last_motion_before_each_attempt() -> None:
    """실패한 이동 뒤에도 이전 이동의 last_motion 이 남지 않아야 한다."""
    node = _FakeNode()
    config = _config(agx_motion_timeout_sec=0.15)
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    backend.move_to_joint_target("arm", {"joint1": 0.0})
    assert backend.last_motion is not None
    assert backend.last_motion.already_at_target is True

    with pytest.raises(ManipulationError):
        backend.move_to_joint_target("arm", {"joint1": 1.0})

    assert backend.last_motion is None
