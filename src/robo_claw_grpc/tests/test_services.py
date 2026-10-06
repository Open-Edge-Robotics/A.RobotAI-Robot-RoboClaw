from unittest import mock

import pytest
from robo_claw_grpc.models import ConfigModel, RobotHistory
from robo_claw_grpc.robo_pb2 import (
    EmptyRequest,
    PingRequest,
    Pong,
    RobotBattery,
    RobotHistoryList,
)
from robo_claw_grpc.robo_pb2 import (
    RobotHistory as RobotHistoryProto,
)
from robo_claw_grpc.services.ping_service import PingService
from robo_claw_grpc.services.robot_history_service import RobotHistoryService


@pytest.fixture
def config():
    return ConfigModel(
        port=50051,
        robot_id="robot-1",
        robot_name="TestBot",
        robot_description="test",
        robot_type="TEST",
        debug=False,
        max_history_count=100,
        max_db_size_mb=100,
        file_transfer_dir="/tmp/transfers",
    )


# ── PingService ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ping_service_returns_pong(config):
    svc = PingService(config=config)
    req = PingRequest(message="hello")
    ctx = mock.Mock()
    result = await svc.Ping(req, ctx)
    assert isinstance(result, Pong)
    assert result.message == "Pong"


# ── RobotHistoryService ───────────────────────────────────────


def _make_history_model() -> RobotHistory:
    return RobotHistory(
        id=1,
        robot_id="robot-1",
        is_connected=True,
        battery_percentage=75.0,
        cpu_usage=12.0,
        ram_usage=30.0,
        disk_usage=40.0,
        network_usage=1.0,
        uptime=500,
        last_seen=2000,
    )


@pytest.mark.asyncio
async def test_history_service_get_list(config):
    repo = mock.Mock()
    repo.get_history_list = mock.AsyncMock(return_value=[_make_history_model()])
    svc = RobotHistoryService(repo=repo, config=config, battery_node=None)

    result = await svc.GetRobotHistoryList(EmptyRequest(), mock.Mock())
    assert isinstance(result, RobotHistoryList)
    assert len(result.history) == 1
    assert result.history[0].robot_id == "robot-1"


@pytest.mark.asyncio
async def test_history_service_get_latest(config):
    repo = mock.Mock()
    repo.get_latest_history = mock.AsyncMock(return_value=_make_history_model())
    svc = RobotHistoryService(repo=repo, config=config, battery_node=None)

    result = await svc.GetLatestRobotHistory(EmptyRequest(), mock.Mock())
    assert isinstance(result, RobotHistoryProto)
    assert result.battery_percentage == 75.0


@pytest.mark.asyncio
async def test_history_service_get_latest_none_returns_empty_proto(config):
    repo = mock.Mock()
    repo.get_latest_history = mock.AsyncMock(return_value=None)
    svc = RobotHistoryService(repo=repo, config=config, battery_node=None)

    result = await svc.GetLatestRobotHistory(EmptyRequest(), mock.Mock())
    assert isinstance(result, RobotHistoryProto)
    assert result.robot_id == ""


@pytest.mark.asyncio
async def test_history_service_get_battery_without_node(config):
    repo = mock.Mock()
    svc = RobotHistoryService(repo=repo, config=config, battery_node=None)
    result = await svc.GetCurrentRobotBattery(EmptyRequest(), mock.Mock())
    assert isinstance(result, RobotBattery)
    assert result.percentage == 0.0


@pytest.mark.asyncio
async def test_history_service_get_battery_with_node(config):
    repo = mock.Mock()
    battery_node = mock.Mock()
    battery_node.get_battery_status.return_value = 88.0
    svc = RobotHistoryService(repo=repo, config=config, battery_node=battery_node)

    result = await svc.GetCurrentRobotBattery(EmptyRequest(), mock.Mock())
    assert result.percentage == 88.0


# ── RoboClawGrpcServicer 위임 검증 ────────────────────────────
# servicer __init__은 모든 서비스를 생성하므로 rclpy/cv_bridge 필요.
# ROS2 환경에서만 실행.


@pytest.mark.ros2
@pytest.mark.asyncio
async def test_servicer_delegates_ping(config):
    from robo_claw_grpc.servicer import RoboClawGrpcServicer

    with mock.patch(
        "robo_claw_grpc.servicer.RoboClawGrpcDatabase"
    ) as MockDB, mock.patch(
        "robo_claw_grpc.servicer.RobotHistoryRepository"
    ) as MockRepo:
        mock_db = mock.Mock()
        mock_db.init_database = mock.AsyncMock()
        MockDB.return_value = mock_db
        MockRepo.return_value = mock.Mock()

        servicer = RoboClawGrpcServicer(config=config)

    with mock.patch.object(servicer._ping_svc, "Ping", new=mock.AsyncMock(return_value=Pong(message="Pong"))) as m:
        result = await servicer.Ping(PingRequest(), mock.Mock())
        assert result.message == "Pong"
        m.assert_awaited_once()


@pytest.mark.ros2
@pytest.mark.asyncio
async def test_servicer_delegates_get_robot_info(config):
    from robo_claw_grpc.robo_pb2 import Robot
    from robo_claw_grpc.servicer import RoboClawGrpcServicer

    with mock.patch("robo_claw_grpc.servicer.RoboClawGrpcDatabase") as MockDB, mock.patch(
        "robo_claw_grpc.servicer.RobotHistoryRepository"
    ) as MockRepo:
        MockDB.return_value = mock.Mock(init_database=mock.AsyncMock())
        MockRepo.return_value = mock.Mock()

        servicer = RoboClawGrpcServicer(config=config)

    expected = Robot(id="robot-1", name="TestBot")
    with mock.patch.object(servicer._robot_info_svc, "GetRobotInfo", new=mock.AsyncMock(return_value=expected)) as m:
        result = await servicer.GetRobotInfo(EmptyRequest(), mock.Mock())
        assert result.id == "robot-1"
        m.assert_awaited_once()
