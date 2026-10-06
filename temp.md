# RoboClaw 소스 복사 명령어

Git 메타데이터, 가상환경, 비밀 파일 및 빌드 산출물을 제외하고 소스를 복사합니다.

```bash
mkdir -p ~/workspace-OpenEdgeRobotics/A.RobotAI-Robot-RoboClaw

rsync -a --info=progress2 \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='.env' \
  --exclude='.ssh_pwd' \
  --exclude='.git_passwd' \
  --exclude='.key.sh' \
  --exclude='build/' \
  --exclude='install/' \
  --exclude='log/' \
  --exclude='__pycache__/' \
  ~/workspace-roboclaw/robo-claw/ \
  ~/workspace-OpenEdgeRobotics/A.RobotAI-Robot-RoboClaw/
```

실제 복사 전에 변경 대상을 확인하려면 다음 명령어를 사용합니다.

```bash
rsync -an --itemize-changes \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='.env' \
  --exclude='.ssh_pwd' \
  --exclude='.git_passwd' \
  --exclude='.key.sh' \
  --exclude='build/' \
  --exclude='install/' \
  --exclude='log/' \
  --exclude='__pycache__/' \
  ~/workspace-roboclaw/robo-claw/ \
  ~/workspace-OpenEdgeRobotics/A.RobotAI-Robot-RoboClaw/
```
