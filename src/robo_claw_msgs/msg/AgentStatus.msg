# 에이전트 전체 상태
std_msgs/Header header

# 에이전트 상태 코드
uint8 IDLE=0
uint8 THINKING=1
uint8 EXECUTING=2
uint8 WAITING=3
uint8 ERROR=4
uint8 state

# 현재 실행 중인 스킬 이름 (없으면 빈 문자열)
string current_skill

# 상태 메시지 (사람이 읽을 수 있는 설명)
string message

# 에이전트 이름 식별자
string agent_id
