# 아키텍처 개요 (Architecture Overview)

## 1. 프로젝트 개요

이 프로젝트는 **주식 전문가 AI 에이전트 백엔드 서버**입니다.

사용자가 채팅으로 "삼성전자 주가 알려줘", "테슬라 최근 뉴스" 같은 질문을 보내면, AI 에이전트가 자동으로 필요한 외부 API(DART, 네이버, yfinance)를 호출해서 정보를 수집하고, 이를 종합해 답변을 스트리밍으로 돌려줍니다.

**핵심 가치:** 사용자가 여러 사이트를 돌아다닐 필요 없이, 하나의 채팅 인터페이스에서 기업 정보 + 주가 + 뉴스를 한번에 받을 수 있습니다.


## 2. 기술 스택

| 기술 | 역할 | 왜 필요한가? |
|------|------|-------------|
| **FastAPI** | 웹 서버 프레임워크 | 비동기(async) 지원이 뛰어나서 SSE 스트리밍에 적합. 자동 API 문서(`/docs`) 제공 |
| **LangChain** | AI 에이전트 프레임워크 | LLM(GPT)에게 "도구(tool)"를 쥐어주고, 질문에 따라 어떤 도구를 쓸지 스스로 판단하게 만드는 프레임워크 |
| **LangGraph** | 에이전트 상태 관리 | `InMemorySaver`로 대화 히스토리를 메모리에 저장해서, 같은 thread 안에서 이전 대화를 기억 |
| **Pydantic** | 데이터 검증/모델 | 요청/응답 데이터의 타입을 정의하고 자동으로 검증. FastAPI와 찰떡궁합 |
| **pydantic-settings** | 환경변수 관리 | `.env` 파일의 API 키들을 타입 안전하게 불러옴 |
| **yfinance** | 주가 조회 | Yahoo Finance에서 실시간 주가, 시가총액, PER 등을 가져오는 라이브러리 |
| **httpx** | HTTP 클라이언트 | DART API, 네이버 API 호출에 사용. 비동기(async) 지원 |
| **uvicorn** | ASGI 서버 | FastAPI 앱을 실제로 실행하는 서버 |


## 3. 디렉토리 구조

```
agent-back/
├── app/
│   ├── main.py                  # FastAPI 앱 진입점 (서버 시작 지점)
│   ├── __init__.py
│   │
│   ├── core/
│   │   ├── config.py            # 환경변수 설정 (API 키, 모델명 등)
│   │   └── __init__.py
│   │
│   ├── agents/                  # AI 에이전트 관련 코드
│   │   ├── stock_agent.py       # 에이전트 생성 팩토리
│   │   ├── tools.py             # 에이전트가 사용하는 도구들 (DART, 네이버, yfinance)
│   │   ├── prompts.py           # 시스템 프롬프트 (에이전트의 성격/역할 정의)
│   │   ├── dummy.py             # (테스트/교육용 더미)
│   │   └── __init__.py
│   │
│   ├── services/                # 비즈니스 로직 계층
│   │   ├── agent_service.py     # 에이전트 실행 + SSE 스트리밍 처리
│   │   ├── threads_service.py   # 대화 스레드 JSON 파일 읽기
│   │   ├── conversation_service.py  # 대화 세션 관리 (메모리 기반)
│   │   └── __init__.py
│   │
│   ├── api/
│   │   ├── routes/
│   │   │   ├── chat.py          # POST /chat - 채팅 엔드포인트
│   │   │   ├── threads.py       # GET /threads - 대화 목록/상세 조회
│   │   │   └── __init__.py
│   │   └── __init__.py
│   │
│   ├── models/                  # 데이터 모델 (Pydantic)
│   │   ├── __init__.py          # 공통 모델 (LangChainMessage, 응답 DTO 등)
│   │   ├── chat.py              # ChatRequest, ChatResponse
│   │   └── threads.py           # ThreadDataResponse, 메시지 모델
│   │
│   ├── utils/                   # 유틸리티
│   │   ├── logger.py            # 커스텀 로거 + 실행시간 측정 데코레이터
│   │   ├── read_json.py         # JSON 파일 읽기 헬퍼
│   │   └── __init__.py
│   │
│   └── data/                    # JSON 파일 기반 데이터 저장소
│       ├── threads.json         # 대화 목록
│       ├── favorite_questions.json  # 즐겨찾기 질문
│       └── threads/             # 개별 대화 내용 (UUID별 JSON 파일)
│
├── tests/                       # 테스트 코드
├── docs/                        # 문서
├── pyproject.toml               # 의존성 및 프로젝트 설정
├── uv.lock                      # 의존성 잠금 파일
└── env.sample                   # 환경변수 샘플
```


## 4. 레이어드 아키텍처

이 프로젝트는 **4개의 계층(Layer)**으로 나뉘어 있습니다. 각 계층은 자기 역할만 담당하고, 아래 계층의 기능을 호출합니다.

```
┌─────────────────────────────────────────────────┐
│                  API Layer                       │
│         (chat.py, threads.py)                    │
│   HTTP 요청을 받고, 응답 형식을 결정             │
├─────────────────────────────────────────────────┤
│                Service Layer                     │
│   (agent_service.py, threads_service.py,         │
│    conversation_service.py)                      │
│   비즈니스 로직 처리, 에이전트 실행 조율         │
├─────────────────────────────────────────────────┤
│                Agent Layer                        │
│   (stock_agent.py, tools.py, prompts.py)         │
│   LLM + 도구 조합으로 질문에 답변               │
├─────────────────────────────────────────────────┤
│                Data Layer                         │
│   (외부 API: DART, 네이버, yfinance)             │
│   (내부 저장소: app/data/*.json)                 │
└─────────────────────────────────────────────────┘
```

**왜 계층을 나누는가?**
- API Layer는 "어떤 형식으로 받고 보낼지"만 신경 씀
- Service Layer는 "무엇을 할지" 결정
- Agent Layer는 "AI가 어떻게 생각하고 행동할지" 담당
- Data Layer는 "데이터를 어디서 가져올지" 담당

이렇게 나누면 한 계층을 수정해도 다른 계층에 영향이 적습니다.


## 5. 요청 흐름도

사용자가 "삼성전자 주가 알려줘"라고 질문했을 때의 전체 흐름입니다.

```
사용자 (프론트엔드)
  │
  │  POST /api/v1/chat
  │  { "thread_id": "...", "message": "삼성전자 주가 알려줘" }
  │
  ▼
┌──────────────────────────────────┐
│  1. API Layer (chat.py)          │
│  - ChatRequest로 요청 파싱       │
│  - AgentService 인스턴스 생성    │
│  - StreamingResponse 반환 시작   │
└──────────┬───────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│  2. Service Layer                │
│     (agent_service.py)           │
│  - create_stock_agent() 호출     │
│  - agent.astream()으로 스트리밍  │
│  - 청크를 SSE JSON으로 변환      │
└──────────┬───────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│  3. Agent Layer                  │
│     (stock_agent.py + tools.py)  │
│  - LLM이 질문을 분석             │
│  - "주가 조회가 필요하다" 판단   │
│  - get_stock_price 도구 호출     │
└──────────┬───────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│  4. Data Layer                   │
│  - DART에서 종목코드 검색        │
│  - yfinance로 실시간 주가 조회   │
│  - 결과를 포맷팅하여 반환        │
└──────────┬───────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│  5. 응답 스트리밍 (SSE)          │
│                                  │
│  data: {"step":"model",          │
│         "tool_calls":["get_..."]}│
│                                  │
│  data: {"step":"tools",          │
│         "name":"get_stock_price",│
│         "content":"종목: ..."}   │
│                                  │
│  data: {"step":"done",           │
│         "content":"삼성전자의    │
│          현재 주가는..."}        │
└──────────────────────────────────┘
           │
           ▼
      사용자에게 실시간 표시
```


## 6. 핵심 파일별 상세 설명

### 6.1 `app/main.py` - 앱 진입점

서버가 시작될 때 가장 먼저 실행되는 파일입니다.

**하는 일:**
- `lifespan`: 서버 시작 시 DART 기업코드를 미리 로딩 (콜드 스타트 방지)
- `FastAPI` 앱 인스턴스 생성 (제목, 설명, 버전 설정)
- `CORSMiddleware`: 프론트엔드에서 API를 호출할 수 있도록 CORS 설정
- HTTP 요청 로깅 미들웨어: 모든 요청의 시작/종료/실행시간을 기록
- `/` : 루트 경로 (API 상태 확인)
- `/health` : 헬스체크 엔드포인트
- 라우터 등록: `chat_router`, `threads_router`를 `/api/v1` 접두사로 등록

**라이프사이클(lifespan)이란?**
서버가 켜질 때 한 번 실행되는 초기화 코드입니다. `yield` 전이 시작 시, `yield` 후가 종료 시 실행됩니다. 여기서는 DART 기업코드 XML(약 10만 건)을 메모리에 미리 올려두어, 첫 요청이 느려지지 않도록 합니다.


### 6.2 `app/core/config.py` - 환경변수 관리

`.env` 파일에서 API 키와 설정값을 읽어오는 파일입니다.

**주요 설정값:**

| 변수명 | 용도 |
|--------|------|
| `API_V1_PREFIX` | API 경로 접두사 (예: `/api/v1`) |
| `OPENAI_API_KEY` | OpenAI GPT API 키 |
| `OPENAI_MODEL` | 사용할 모델명 (예: `gpt-4o`) |
| `DART_API_KEY` | DART OpenAPI 인증 키 |
| `NAVER_CLIENT_ID` / `SECRET` | 네이버 검색 API 인증 |
| `DEEPAGENT_RECURSION_LIMIT` | 에이전트 최대 반복 횟수 (무한루프 방지) |

**Pydantic Settings의 장점:**
- `.env` 파일을 자동으로 읽어옴
- 타입이 맞지 않으면 서버 시작 시 바로 에러 발생 (런타임 버그 방지)
- `settings.OPENAI_API_KEY`처럼 자동완성으로 접근 가능


### 6.3 `app/agents/stock_agent.py` - 에이전트 생성 팩토리

LangChain 에이전트를 조립하는 "공장" 역할입니다.

**구성 요소 3가지:**
1. **LLM** (`ChatOpenAI`): GPT 모델. `temperature=0`으로 설정해 일관된 답변 생성
2. **Tools** (5개 도구): 에이전트가 호출할 수 있는 외부 기능들
3. **System Prompt**: 에이전트의 역할과 행동 지침

**`InMemorySaver` (체크포인터):**
모듈 레벨에서 싱글턴으로 생성됩니다. 같은 `thread_id`로 대화하면 이전 대화를 기억합니다. 서버가 재시작되면 메모리가 초기화됩니다.

**`create_agent()` 함수:**
LangChain이 제공하는 함수로, LLM + 도구 + 프롬프트를 조합해 "스스로 생각하고 도구를 선택하는 에이전트"를 만듭니다.


### 6.4 `app/agents/tools.py` - 5개 도구

에이전트가 사용할 수 있는 도구(tool) 모음입니다. 각 도구는 `@tool` 데코레이터로 정의되며, LLM이 함수명과 docstring을 읽고 언제 호출할지 스스로 판단합니다.

| 도구명 | 데이터 소스 | 기능 |
|--------|------------|------|
| `search_ipo_disclosure` | DART API | IPO 관련 공시(증권신고서 등) 검색 |
| `get_company_info` | DART API | 기업 개황 조회 (업종, 대표자, 설립일) |
| `get_ipo_price_info` | DART API | 공모가격 정보 조회 |
| `naver_search` | 네이버 검색 API | 최신 뉴스 검색 |
| `get_stock_price` | yfinance | 한국/해외 주식 실시간 시세 조회 |

**내부 헬퍼 함수들:**
- `_dart_get()`: DART API 호출을 추상화 (에러 처리 포함)
- `_load_corp_codes()`: DART 기업코드 XML을 다운로드하고 캐싱
- `_find_corp_code()`: 회사명 → DART 기업코드 변환 (정확 매칭 → 부분 매칭 순서)
- `_format_stock_info()`: 주가 정보를 읽기 좋은 텍스트로 포맷팅


### 6.5 `app/agents/prompts.py` - 시스템 프롬프트

에이전트의 **페르소나와 행동 규칙**을 정의하는 파일입니다.

시스템 프롬프트는 에이전트에게 "너는 누구이고, 어떻게 행동해야 하는지"를 알려줍니다:
- **역할**: 한국 및 글로벌 주식시장 전문가 AI
- **사용 도구**: 6개 도구의 용도 설명
- **응답 지침**: 한국어 답변, 숫자 포맷팅, 투자 조언 면책 안내 등


### 6.6 `app/services/agent_service.py` - SSE 스트리밍 처리

에이전트 실행과 응답 스트리밍을 담당하는 핵심 서비스입니다.

**`AgentService.process_query()` 동작 과정:**

1. `create_stock_agent()`로 에이전트 생성
2. `agent.astream()`으로 비동기 스트리밍 시작
3. 스트림에서 오는 청크(chunk)를 하나씩 처리:
   - **`step: "model"`** + `tool_calls` 있음 → 에이전트가 도구를 호출하려 함
   - **`step: "tools"`** → 도구 실행 결과
   - **`step: "done"`** → 최종 텍스트 응답
4. 각 청크를 JSON으로 변환하여 `yield` (SSE 스트리밍)

**`@log_execution` 데코레이터:**
실행 시작/종료 시간을 자동으로 로깅합니다. 비동기 제너레이터도 지원합니다.


### 6.7 `app/api/routes/chat.py` - 채팅 엔드포인트

```
POST /api/v1/chat
```

**요청 형식:**
```json
{
  "thread_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "message": "삼성전자 주가 알려줘"
}
```

**응답 형식:** SSE (Server-Sent Events) 스트리밍

이 엔드포인트는 `StreamingResponse`를 반환합니다. 일반 REST API처럼 한 번에 응답을 보내는 것이 아니라, 에이전트가 생각하는 과정을 실시간으로 클라이언트에 전달합니다.

**SSE란?**
서버가 클라이언트에게 데이터를 실시간으로 "밀어주는" 기술입니다. 각 메시지는 `data: {...}\n\n` 형식으로 전송됩니다. 챗봇이 글자를 하나씩 타이핑하는 것처럼 보이게 만드는 기술이 바로 이것입니다.


### 6.8 `app/api/routes/threads.py` - 대화 관리 엔드포인트

```
GET /api/v1/favorites/questions   # 즐겨찾기 질문 목록
GET /api/v1/threads               # 전체 대화 목록
GET /api/v1/threads/{thread_id}   # 특정 대화 상세 조회
```

JSON 파일(`app/data/`)에서 데이터를 읽어 반환합니다. 현재는 DB 없이 파일 기반으로 동작합니다.


### 6.9 `app/models/` - 데이터 모델

Pydantic 모델로 요청/응답의 데이터 구조를 정의합니다.

**`models/chat.py`:**
- `ChatRequest`: 채팅 요청 (`thread_id` + `message`)
- `ChatResponse`: 채팅 응답 (`message_id` + `content` + `metadata`)

**`models/threads.py`:**
- `ThreadDataResponse`: 대화 스레드 (제목 + 메시지 리스트)
- `UserMessageData` / `AIMessageData`: 사용자/AI 메시지
- `RootBaseModel[T]`: 제네릭 응답 래퍼 (`{ "response": T }`)

**`models/__init__.py`:**
- `LangChainMessage`: LangChain 호환 메시지 포맷
- `QueryRequest` / `AIMessageResponse`: 쿼리 요청/응답 DTO
- 차트, 그리드 등 시각화 관련 모델들


## 7. 에이전트 동작 원리

### 에이전트란?

보통의 LLM(ChatGPT)은 텍스트만 생성합니다. 하지만 **에이전트**는 LLM에게 "도구"를 쥐어주어, 필요할 때 스스로 도구를 호출하고, 그 결과를 바탕으로 다시 생각하는 구조입니다.

### 동작 과정 (ReAct 패턴)

```
사용자: "삼성전자 주가랑 최근 뉴스 알려줘"
         │
         ▼
    ┌─────────┐
    │  LLM    │  "주가와 뉴스 두 가지를 요청했으니,
    │  생각   │   get_stock_price와 naver_search를
    │         │   호출해야겠다"
    └────┬────┘
         │
    ┌────▼────┐
    │  도구   │  get_stock_price("삼성전자") 호출
    │  호출   │  naver_search("삼성전자 주식") 호출
    └────┬────┘
         │
    ┌────▼────┐
    │  LLM    │  도구 결과를 종합해서
    │  종합   │  사용자 친화적인 답변 생성
    └────┬────┘
         │
         ▼
    최종 답변 출력
```

### `create_agent()` 함수

LangChain의 `create_agent()`는 위 과정을 자동으로 처리하는 에이전트를 만듭니다:
- **model**: 어떤 LLM을 쓸지 (GPT-4o 등)
- **tools**: 어떤 도구를 줄지 (5개 도구)
- **system_prompt**: 어떤 역할/규칙을 따를지
- **checkpointer**: 대화 기록을 어디에 저장할지 (`InMemorySaver`)

### `InMemorySaver` (대화 기억)

같은 `thread_id`로 대화하면, 에이전트는 이전 대화 내용을 기억합니다.

```python
# 첫 번째 요청
config = {"configurable": {"thread_id": "abc-123"}}
agent.astream({"messages": [HumanMessage("삼성전자 주가")]}, config=config)

# 두 번째 요청 (같은 thread_id → 이전 대화를 기억)
agent.astream({"messages": [HumanMessage("아까 그 회사 뉴스도 알려줘")]}, config=config)
# → "삼성전자"를 기억하고 있으므로 삼성전자 뉴스를 검색
```

단, `InMemorySaver`는 서버 메모리에 저장되므로 서버 재시작 시 모든 대화 기록이 사라집니다.


## 8. 데이터 흐름

### 8.1 요청/응답 모델

```
[클라이언트]                              [서버]

ChatRequest ──────────────────────► chat.py
{                                    │
  "thread_id": UUID,                 ▼
  "message": "..."             AgentService
}                                    │
                                     ▼
                              에이전트 스트리밍
                                     │
SSE 스트림 ◄─────────────────────────┘

data: {"step":"model", "tool_calls":["get_stock_price"]}
data: {"step":"tools", "name":"get_stock_price", "content":"..."}
data: {"step":"done", "content":"삼성전자의 현재 주가는...", "role":"assistant", ...}
```

### 8.2 SSE 스트리밍 프로토콜

SSE 응답은 3가지 `step` 타입으로 구성됩니다:

| step | 의미 | 포함 데이터 |
|------|------|------------|
| `model` | LLM이 도구를 호출하려 함 | `tool_calls`: 호출할 도구명 배열 |
| `tools` | 도구 실행 결과 | `name`: 도구명, `content`: 실행 결과 |
| `done` | 최종 응답 완료 | `content`: 최종 답변 텍스트, `message_id`, `role`, `created_at` |

프론트엔드는 이 `step` 값을 보고 UI를 업데이트합니다:
- `model` → "도구 호출 중..." 표시
- `tools` → 도구 실행 결과 표시 (선택)
- `done` → 최종 답변 표시

### 8.3 JSON 파일 기반 저장소

현재 대화 이력은 `app/data/` 디렉토리의 JSON 파일로 관리됩니다.

```
app/data/
├── threads.json                # 대화 목록 (제목, 생성일 등)
├── favorite_questions.json     # 즐겨찾기 질문 목록
└── threads/
    ├── {uuid-1}.json           # 개별 대화 내용
    ├── {uuid-2}.json
    └── ...
```

이 방식은 DB 설정 없이 바로 사용할 수 있어 교육용으로 적합합니다. 프로덕션 환경에서는 PostgreSQL 등의 DB로 교체할 수 있습니다.


## 9. 외부 API 연동

### 9.1 DART OpenAPI (금융감독원 전자공시)

| 항목 | 내용 |
|------|------|
| **역할** | 기업 공시 정보, 기업 개황, IPO/공모가 정보 조회 |
| **Base URL** | `https://opendart.fss.or.kr/api` |
| **인증 방식** | 쿼리 파라미터 `crtfc_key` (API 키) |
| **사용하는 도구** | `search_ipo_disclosure`, `get_company_info`, `get_ipo_price_info` |

**기업코드 로딩 과정:**
1. 서버 시작 시 `corpCode.xml` ZIP 파일을 다운로드
2. XML을 파싱하여 약 10만 건의 기업코드를 메모리에 캐싱
3. 이후 회사명 → 기업코드 변환 시 캐시에서 검색

### 9.2 네이버 검색 API

| 항목 | 내용 |
|------|------|
| **역할** | 최신 뉴스 검색 (IPO, 주식 관련 소식) |
| **URL** | `https://openapi.naver.com/v1/search/news.json` |
| **인증 방식** | HTTP 헤더 `X-Naver-Client-Id` + `X-Naver-Client-Secret` |
| **사용하는 도구** | `naver_search` |

### 9.3 yfinance (Yahoo Finance)

| 항목 | 내용 |
|------|------|
| **역할** | 한국/해외 주식 실시간 시세 조회 |
| **인증 방식** | 불필요 (공개 API) |
| **지원 시장** | 한국(.KS/.KQ), 미국, 일본(.T), 홍콩(.HK) 등 |
| **사용하는 도구** | `get_stock_price` |

**주가 조회 우선순위:**
1. 해외 주식 한글명 매핑 (예: "테슬라" → `TSLA`)
2. DART 기업코드에서 한국 종목코드 검색 (예: "삼성전자" → `005930.KS`)
3. 티커 심볼 직접 조회 (예: `AAPL`, `7203.T`)
