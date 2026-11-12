"""대시보드 지도 선택 목표(map goal) 계약/스코프/검증.

하위 모듈을 직접 임포트한다(순환 임포트 방지를 위해 재-export 하지 않는다):

- ``contract``: 정규 문장/컨텍스트 검증/지오메트리 ID/free 셀
- ``scope``: 요청 단위 ContextVar 스코프
- ``enforcement``: LLM 계획 검증 + 기존 주행 안전 prelude 적용
- ``tracker``: 요청 기록/멱등성/lease
- ``dispatch``: 실제 주행 직전 지도 재검증과 결과 증거
"""
