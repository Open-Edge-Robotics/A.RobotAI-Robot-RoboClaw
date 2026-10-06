"""robo_claw_grpc 테스트 공용 fixture."""

import sys
from pathlib import Path

# proto 자동 생성 파일 경로가 패키지 내부에 있으므로 별도 경로 조작 불필요.
# 단, 테스트가 패키지 루트에서 실행될 수 있도록 path를 보정한다.
_pkg_root = Path(__file__).resolve().parent.parent
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))
