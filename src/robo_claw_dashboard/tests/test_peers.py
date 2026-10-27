"""동료 로봇 표시 정규화 테스트 — ListPeers 응답을 화면용으로 변환한다."""

import pytest
from robo_claw_dashboard.domain.peers import normalize_peers

pytestmark = pytest.mark.unit


class TestNormalizePeers:
    def test_peer_name_field_is_used(self):
        peers = normalize_peers(
            [{"peer_name": "Cloid 2호", "host": "192.168.50.105", "port": 50152, "connected": True}]
        )
        assert peers == [
            {
                "name": "Cloid 2호",
                "host": "192.168.50.105",
                "port": 50152,
                "connected": True,
            }
        ]

    def test_legacy_name_field_is_supported(self):
        peers = normalize_peers([{"name": "Former0047", "connected": False}])
        assert peers[0]["name"] == "Former0047"
        assert peers[0]["connected"] is False

    def test_missing_name_falls_back_to_index_label(self):
        peers = normalize_peers([{"peer_name": "", "connected": True}, {}])
        assert peers[0]["name"] == "동료 1"
        assert peers[1]["name"] == "동료 2"

    def test_invalid_port_becomes_zero(self):
        peers = normalize_peers([{"peer_name": "x", "port": "not-a-number"}])
        assert peers[0]["port"] == 0

    def test_non_mapping_entries_are_skipped(self):
        assert normalize_peers([None, "peer", {"peer_name": "ok"}])[0]["name"] == "ok"

    def test_empty_or_missing_list_returns_empty(self):
        assert normalize_peers(None) == []
        assert normalize_peers([]) == []
