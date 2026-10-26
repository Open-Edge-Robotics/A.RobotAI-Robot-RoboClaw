from types import SimpleNamespace

from robo_claw_agent.agent_node.planner import _location_chain_for, _route_location_query


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


class _PlaceMemory:
    """시맨틱 맵 최소 대역 — 기억된 장소명만 제공한다."""

    def __init__(self, places=None):
        self._places = dict(places or {})

    def get_all_objects(self):
        return [{"name": name, "metadata": {}} for name in self._places]

    def get_object_location(self, name):
        if name not in self._places:
            return None
        x, y = self._places[name]
        return {"name": name, "position": {"x": x, "y": y}}

    def search_knowledge(self, *args, **kwargs):
        return []


def test_initial_pose_request_routes_to_set_initial_pose_with_place():
    """'안방 위치를 초기 위치로 지정해줘' → set_initial_pose(target_name='안방')."""
    chain = [{"skill": "get_location", "params": {"location_name": "안방"}}]
    node = SimpleNamespace(_memory=_PlaceMemory({"안방": (3.2, -1.4)}))

    routed = _route_location_query(chain, "안방 위치를 초기 위치로 지정해줘", node, _Logger())

    assert routed == [{"skill": "set_initial_pose", "params": {"target_name": "안방"}}]


def test_initial_pose_chain_for_known_place_without_llm_plan():
    chain = _location_chain_for(
        "안방을 초기 위치로 잡아줘",
        SimpleNamespace(_memory=_PlaceMemory({"안방": (3.2, -1.4)})),
        _Logger(),
    )

    assert chain == [{"skill": "set_initial_pose", "params": {"target_name": "안방"}}]


def test_initial_pose_request_without_known_place_keeps_llm_chain():
    chain = [{"skill": "get_status", "params": {}}]

    routed = _route_location_query(chain, "초기 위치를 0,0으로 지정해줘", SimpleNamespace(_memory=None), _Logger())

    assert routed == chain


def test_initial_pose_request_does_not_replace_motion_plan():
    """물리 이동 계획은 조용히 바꿔치기하지 않는다(사용자 요청 삼킴 방지)."""
    chain = [{"skill": "navigate_to", "params": {"target_name": "안방"}}]
    node = SimpleNamespace(_memory=_PlaceMemory({"안방": (3.2, -1.4)}))

    routed = _route_location_query(chain, "안방 위치를 초기 위치로 지정해줘", node, _Logger())

    assert routed == chain


def test_last_pose_request_routes_without_place_registration():
    """'마지막 위치를 초기 위치로' → 장소 등록 여부와 무관하게 set_initial_pose."""
    chain = [{"skill": "get_status", "params": {}}]

    routed = _route_location_query(
        chain, "마지막 위치를 초기 위치로 해줘", SimpleNamespace(_memory=None), _Logger()
    )

    assert routed == [{"skill": "set_initial_pose", "params": {"target_name": "마지막 위치"}}]


def test_last_pose_chain_for_empty_llm_plan():
    chain = _location_chain_for(
        "네비 재기동했으니 직전 위치로 초기 위치 잡아줘",
        SimpleNamespace(_memory=None),
        _Logger(),
    )

    assert chain == [{"skill": "set_initial_pose", "params": {"target_name": "마지막 위치"}}]


def test_initial_pose_without_place_or_last_pose_keeps_llm_chain():
    chain = [{"skill": "get_status", "params": {}}]

    routed = _route_location_query(
        chain, "초기 위치를 다시 잡아줘", SimpleNamespace(_memory=None), _Logger()
    )

    assert routed == chain
