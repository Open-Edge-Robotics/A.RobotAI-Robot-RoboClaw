import asyncio

import grpc
from robo_claw_grpc.fleet_connector.client import FleetConnector
from robo_claw_grpc.fleet_connector.config import FleetConnectorConfig

from robo_claw_grpc import fleet_control_pb2 as pb2
from robo_claw_grpc import fleet_control_pb2_grpc as pb2_grpc


class FakeHandlers:
    def __init__(self):
        self.skill_calls = 0

    async def execute_task(self, instruction, timeout_sec):
        return {"success": True, "message": instruction}

    async def execute_skill(self, name, params, timeout_sec):
        self.skill_calls += 1
        return {"success": True, "message": name}

    async def emergency_stop(self, reason):
        return {"success": True, "message": reason}

    async def request_snapshot(self):
        return {"success": True, "image_base64": "abc"}

    async def cancel_task(self, task_id):
        return {"success": False, "error_code": "CANCEL_UNSUPPORTED"}


class FakeMaestro(pb2_grpc.FleetControlServicer):
    def __init__(self):
        self.registered = asyncio.Event()
        self.completed = asyncio.Event()
        self.registration = None
        self.updates = []

    async def Connect(self, request_iterator, context):
        self.registration = await anext(request_iterator)
        self.registered.set()
        yield pb2.MaestroToRobot(
            protocol_version="1",
            registration_result=pb2.RegistrationResult(accepted=True, message="ok"),
        )
        yield pb2.MaestroToRobot(
            protocol_version="1",
            command_id="command-1",
            execute_skill=pb2.ExecuteSkillCommand(
                task_id="task-1",
                idempotency_key="key-1",
                skill_name="get_status",
                params_json="{}",
                deadline_ms=9999999999999,
            ),
        )
        async for message in request_iterator:
            payload = message.WhichOneof("payload")
            if payload in {"command_ack", "task_update"}:
                self.updates.append(payload)
            if payload == "task_update":
                self.completed.set()
                return


def test_connector_registers_and_executes_command_over_real_grpc(tmp_path):
    async def scenario():
        maestro = FakeMaestro()
        server = grpc.aio.server()
        pb2_grpc.add_FleetControlServicer_to_server(maestro, server)
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        handlers = FakeHandlers()
        connector = FleetConnector(
            FleetConnectorConfig(
                maestro_ip="127.0.0.1",
                robot_port=port,
                robot_id="robot-realistic-1",
                site_id="lab",
                map_id="floor-1",
                map_version="v1",
                map_frame_id="map",
                journal_path=str(tmp_path / "commands.db"),
                heartbeat_sec=0.05,
            ),
            handlers,
            [],
            lambda: {"battery_percentage": 80},
        )
        task = asyncio.create_task(connector.run())
        try:
            await asyncio.wait_for(maestro.registered.wait(), 2)
            await asyncio.wait_for(maestro.completed.wait(), 2)
            assert maestro.registration.robot_id == "robot-realistic-1"
            assert maestro.registration.register.descriptor.map_id == "floor-1"
            assert maestro.registration.register.descriptor.map_version == "v1"
            assert maestro.registration.register.descriptor.frame_id == "map"
            assert handlers.skill_calls == 1
            assert maestro.updates == ["command_ack", "task_update"]
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await connector.stop()
            await server.stop(0)

    asyncio.run(scenario())
