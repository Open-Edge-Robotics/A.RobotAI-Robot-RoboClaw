# 로봇 한 대의 현재 자세·상태 공유 (자리 양보·점유 판정용)

std_msgs/Header header

string agent_id          # 이 자세를 보낸 로봇의 agent_id
float64 x                # map frame x (m)
float64 y                # map frame y (m)
float64 yaw              # map frame heading (rad)
string frame_id          # 좌표 프레임 (map 이 아니면 점유 판정에 쓰지 않음)

string current_skill     # 현재 실행 중 스킬 이름 (없으면 빈 문자열)
bool yieldable           # 지금 자리를 비켜줄 수 있는 상태인지 (조작/작업 중이면 false)
