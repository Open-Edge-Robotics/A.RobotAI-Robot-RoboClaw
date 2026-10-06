# 동료 로봇(gRPC 피어) 한 개에 대한 정보

string peer_name    # target_peers_json에서 설정한 agent_name (연결된 피어 식별자)
string host
int32 port
bool connected      # 현재 gRPC ChatStream이 연결되어 있는지 여부
