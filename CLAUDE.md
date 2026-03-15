# CLAUDE.md

주식 전문가 AI 에이전트 백엔드 — FastAPI + LangChain 기반, SSE 스트리밍으로 실시간 응답 제공

## 기술 스택

FastAPI, LangChain, LangGraph, Pydantic, pydantic-settings, yfinance, httpx, uvicorn

## 개발 환경 명령어

```bash
uv sync                              # 의존성 설치
uvicorn app.main:app --reload         # 개발 서버 실행
pytest                                # 테스트 실행
```

`.env` 필수 변수: `OPENAI_API_KEY`, `OPENAI_MODEL`, `API_V1_PREFIX`

## 프로젝트 구조

- `app/main.py` — FastAPI 앱 진입점, 라우터 등록, CORS, lifespan(DART 기업코드 사전 로딩)
- `app/core/config.py` — 환경변수 설정 (pydantic-settings)
- `app/agents/` — 에이전트 팩토리(`stock_agent.py`), 도구(`tools.py`), 시스템 프롬프트(`prompts.py`)
- `app/services/` — AgentService(SSE 스트리밍), ConversationService, ThreadsService
- `app/api/routes/` — `chat.py`(POST /api/v1/chat), `threads.py`(GET /api/v1/threads)
- `app/models/` — Pydantic 요청/응답 모델
- `app/utils/` — 로거(`@log_execution` 데코레이터), JSON 읽기 헬퍼
- `app/data/` — JSON 파일 기반 저장소 (threads, favorite_questions)

## 아키텍처 규칙

4계층 구조: **API → Service → Agent → Data**

- **새 도구 추가**: `app/agents/tools.py`에 `@tool` 함수 작성 → `stock_agent.py`의 tools 리스트에 등록
- **새 API 추가**: `app/api/routes/`에 라우터 생성 → `app/main.py`에 등록
- **환경변수 추가**: `app/core/config.py` Settings 클래스에 필드 추가 + `.env` 설정
- **SSE 스트리밍 프로토콜**: `step` 3종 — `model`(도구 호출 시작), `tools`(도구 실행 결과), `done`(최종 응답)

## 코딩 컨벤션

- 한국어 docstring/주석
- 비동기(async/await) 기본
- 외부 API 호출은 `httpx.AsyncClient` 사용
- 서비스 메서드에 `@log_execution` 데코레이터 적용

## 향후 계획

- Elasticsearch 연동 예정
- Opik 연동 예정 (config.py에 OpikSettings 이미 정의됨)
