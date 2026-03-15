# Issue #1: 코드 리뷰 - 문제점 및 개선 사항

- **이슈**: https://github.com/yoosungung/agent-back/issues/1
- **작성자**: yoosungung
- **등록일**: 2026-03-13
- **해결일**: 2026-03-15

---

## 피드백 항목 및 조치 결과

### 1. `response_format` 미설정 (stock_agent.py)
- **문제**: `create_agent()` 호출 시 `response_format` 없음 — 응답 파싱 실패 가능
- **심각도**: 주의
- **조치**: 현재 `agent_service.py`가 `getattr(message, "content")`로 텍스트를 직접 추출하므로 `response_format` 없이도 정상 동작. 추후 구조화된 응답이 필요할 때 추가 예정
- **상태**: 관찰 중

### 2. `checkpointer` 미전달 (stock_agent.py)
- **문제**: `InMemorySaver` 미사용 → 멀티턴 대화 불가
- **심각도**: 높음
- **조치**: `InMemorySaver`를 모듈 레벨 싱글턴으로 생성하고 `create_agent(checkpointer=_checkpointer)` 전달
- **상태**: 해결 완료

### 3. `_load_corp_codes()` 초기 로딩 지연 (tools.py)
- **문제**: 서버 첫 호출 시 DART corpCode.xml 다운로드(수십 MB) → 응답 지연
- **심각도**: 중간
- **조치**: FastAPI `lifespan`에서 서버 시작 시 `_load_corp_codes()`를 미리 호출하여 캐시 워밍업
- **상태**: 해결 완료

### 4. 네이버 API 키 미설정 시 실패 (tools.py)
- **문제**: `.env`에 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` 미설정 시 런타임 에러
- **심각도**: 중간
- **조치**: `naver_search` 함수 진입 시 키 존재 여부 확인, 미설정이면 안내 메시지 반환
- **상태**: 해결 완료

### 5. 도구 수 과다 (stock_agent.py)
- **문제**: 6개 Tool이 LLM에 동시 등록 → 컨텍스트 길이 증가, Tool 선택 혼란 가능
- **심각도**: 낮음
- **조치**: `get_stock_price`와 `get_global_stock_price`를 하나의 `get_stock_price`로 통합 (6개 → 5개)
- **상태**: 해결 완료

---

## 수정된 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/agents/stock_agent.py` | checkpointer 추가, 도구 목록 5개로 축소 |
| `app/agents/tools.py` | 네이버 API 키 검증 추가, 한국/해외 주가 조회 통합 |
| `app/main.py` | lifespan으로 DART 기업코드 startup 로딩 |
| `docs/stock-agent-plan.md` | 도구 통합 및 설계 변경 반영 |
