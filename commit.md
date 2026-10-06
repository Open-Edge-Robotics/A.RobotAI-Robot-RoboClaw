merge: origin/main 통합 및 multi-robot 변경사항 정리

로컬 main과 최신 origin/main 사이의 중복 이력을 정리하고 충돌을 해소한다.
multi-robot 브랜치의 기존 작업 이력을 유지하면서 최신 원격 구현을 기준으로 통합한다.

주요 변경사항:
- 웹 대시보드 노드와 HTTP 인터페이스를 추가한다.
- 런타임 설정 스키마, 계약 파일 및 설정 관리 CLI를 통합한다.
- 자율 협동, 피어 통신, 내비게이션 및 로봇 안전 처리를 개선한다.
- 조작, 인식, 지도 및 시스템 스킬 구현과 테스트를 보강한다.
- Docker, 시뮬레이션, launch 및 로봇별 설정을 최신 구조로 정리한다.
- 운영, 설정, 검증 및 스킬 관련 문서를 개편한다.
- 사용하지 않는 레거시 CLI, 스크립트 및 중복 문서를 제거한다.

검증:
- Dashboard 테스트: 65 passed
- SkillManager 테스트: 24 passed
- Python compileall 통과
- 충돌 마커 및 unstaged 변경 없음
