"""Capture and automatic delivery with real image encoding and fake transport."""

import asyncio
import base64
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from robo_claw_agent.agent_node.execution import ExecutionMixin
from robo_claw_agent.skill_manager import SkillManager
from robo_claw_agent.skills.map_skill.capture import CaptureMapSkill

pytestmark = pytest.mark.component


class FakeNode(ExecutionMixin):
    _map_frame = "map"

    def __init__(self):
        self.sent = []
        self._skills = SkillManager(self)

    def has_parameter(self, name):
        return name == "map_topic"

    def get_parameter(self, name):
        assert name == "map_topic"
        return SimpleNamespace(
            get_parameter_value=lambda: SimpleNamespace(string_value="/lio/grid_map")
        )

    def get_logger(self):
        return logging.getLogger("test_map_capture_delivery")

    def _set_state(self, *args):
        pass

    async def _run_blocking(self, label, function, *args):
        return function(*args)

    def _start_channel_send(self, message, file_path):
        self.sent.append((message, file_path))
        event = threading.Event()
        event.set()
        return event


@pytest.mark.parametrize("received", [True, False])
def test_cloid_capture_creates_png_and_only_sends_on_success(tmp_path, received):
    node = FakeNode()
    skill = CaptureMapSkill()
    topics = []
    grid = SimpleNamespace(
        header=SimpleNamespace(frame_id="map"),
        info=SimpleNamespace(
            width=4,
            height=4,
            resolution=0.1,
            origin=SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0)),
        ),
        data=[0, 0, 100, -1] * 4,
    )

    def wait_for_message(msg_type, topic, **kwargs):
        topics.append(topic)
        return grid if received else None

    skill.wait_for_message = wait_for_message
    node._skills.register(skill)
    output = tmp_path / "cloid_map.png"
    route = SimpleNamespace(
        skill="capture_map", params={"output_path": str(output)}, background=False
    )
    _, results, success, events = asyncio.run(
        node._execute_direct_route(route, "현재 지도 보내줘", 15.0, lambda *args: None)
    )

    assert topics == ["/lio/grid_map"]
    assert success is received
    assert results[-1].success is received
    if received:
        assert Path(node.sent[0][1]) == output
        assert len(node.sent) == len(events) == 1
        assert events[0].is_set()
        assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert base64.b64decode(results[-1].result_data["image_base64"]).startswith(b"\x89PNG")
        assert results[-1].result_data["map_metadata"]["map_topic"] == "/lio/grid_map"
    else:
        assert node.sent == events == []
        assert not output.exists()
