# 이제 다음을 정리해줘.

```
1) 1순위 - 5순위까지의 변경 내용을 체계적으로 이해할 수 있도록 정리
2) 이론적으로 개선사항
3) 테스트 케이스 폴더 및 로봇 없이 검증할 수 있는 테스트 케이스 및 자동화 script 생성
```

# 생성한 파일
```
  robo-claw/
  ├── docs/rag-improvements/
  │   ├── CHANGES.md        # (1) 1~5순위 변경 내역 체계적 정리
  │   └── CONFERENCE.md     # (2) 컨퍼런스 발표용 이론 정리
  └── validation/rag_regression/
      ├── test_rag_regression.py  # (3) 로봇 없이 도는 회귀 테스트 9개
      ├── run_rag_tests.sh        # (3) 자동화 스크립트
      └── README.md               # (3) 실행 방법 안내

  1) CHANGES.md — 변경 내역

  배경(경험 축적형 RAG의 자기 오염) → 실패 케이스↔근본원인 매핑 표 → 순위별(문제/변경/효과) → 변경 파일 요약 → 남은 이슈 순으로 정리했습니다. 각 순위가 어떤 Fail 케이스를 해결하는지 추적 가능합니다.

  2) CONFERENCE.md — 발표용 이론

  코드가 아닌 일반화 가능한 문제 구조에 초점을 뒀습니다:
  - 4대 근본 원리: (A) 지식 유형 혼합, (B) 재순위화 목적 오용, (C) 조회 방향 비대칭, (D) 표현 누수
  - 다층 방어 설계도(쓰기 게이팅 → 읽기 필터/랭킹 → 표현 분리) ASCII 다이어그램
  - 핵심 메시지: "실패는 검색이 부정확해서가 아니라, 무엇을 기억으로 볼지 정의가 없어서. top-k/threshold 튜닝은 오염을 못 고친다"
  - abstract 초안 포함

  3) 회귀 테스트 + 자동화

  - 로봇 불필요: 결정론적 더미 임베더 + 인메모리 Qdrant(location=":memory:") → GPU/서버/ROS 전부 불필요, <1초 실행
  - 실행: ./validation/rag_regression/run_rag_tests.sh (또는 bash …). -k p4로 순위별 필터 가능
  - 9개 테스트가 순위·Fail 케이스에 1:1 매핑됨 (README에 표로 정리)

  참고/home/gusewan/workspace-roboclaw/robo-claw/validation

  - 스크립트 실행 권한은 샌드박스가 막아 설정 못 했습니다. chmod +x validation/rag_regression/run_rag_tests.sh 한 번 해주시거나 bash로 실행하시면 됩니다.
  - 문서에서 레거시 Qdrant 오염 데이터 정리(rag_reindex/rag_delete)와 교차언어 별칭(Fail 20)은 남은 과제로 명시해뒀습니다. 실로봇 재검증 시 레거시 정리 후 측정을 권장한다는 점도 README에 적었습니다.
```

