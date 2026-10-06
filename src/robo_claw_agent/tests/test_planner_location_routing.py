from types import SimpleNamespace

from robo_claw_agent.agent_node.planner import _route_location_query


class _Logger:
    def info(self, *_args, **_kwargs):
        pass


def test_reverse_location_query_routes_to_identify_location():
    chain = [{"skill": "describe_surroundings", "params": {"capture_4way": True}}]

    routed = _route_location_query(
        chain, "여기가 어디야?", SimpleNamespace(_memory=None), _Logger()
    )

    assert routed == [{"skill": "identify_location", "params": {}}]


def test_reverse_location_query_replaces_read_only_multi_step_plan():
    chain = [
        {"skill": "get_status", "params": {}},
        {"skill": "rag_search", "params": {"query": "현재 위치"}},
    ]

    routed = _route_location_query(
        chain, "현재 위치 이름 알려줘", SimpleNamespace(_memory=None), _Logger()
    )

    assert routed == [{"skill": "identify_location", "params": {}}]
