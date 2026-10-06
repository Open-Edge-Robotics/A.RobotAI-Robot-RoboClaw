# 스킬 실행 결과
std_msgs/Header header

# 결과 코드
uint8 SUCCESS=0
uint8 FAILURE=1
uint8 CANCELLED=2
uint8 TIMEOUT=3
uint8 code

# 실행한 스킬 이름
string skill_name

# 결과 메시지
string message

# 실행 소요 시간 (초)
float64 duration_sec

# 스킬이 반환한 JSON 데이터 (선택적)
string result_json
