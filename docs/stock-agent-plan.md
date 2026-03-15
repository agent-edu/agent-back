# 주식 전문가 AI 에이전트

## 개요
LangChain `create_agent()`를 사용하여 **주식 전문가 AI 에이전트**를 구현한다.
DART OpenAPI, 네이버 검색 API, yfinance를 활용하여 기업 분석, 공시 조회, 실시간 시세, 최신 뉴스까지 종합적인 주식 정보를 제공한다.

---

## 아키텍처

```
사용자 질문
    ↓
LangChain Agent (GPT-4.1)
    ↓ (LLM이 질문을 분석하여 필요한 도구를 자동 선택)
┌─────────────────────────────────────────────────┐
│  Tool 1: search_ipo_disclosure  (DART API)      │
│  Tool 2: get_company_info       (DART API)      │
│  Tool 3: get_ipo_price_info     (DART API)      │
│  Tool 4: naver_search           (Naver API)     │
│  Tool 5: get_stock_price        (yfinance)      │
│         (한국 + 해외 주식 통합 조회)              │
└─────────────────────────────────────────────────┘
    ↓
SSE 스트리밍 응답 (step:model → step:tools → step:done)
```

---

## 채팅으로 조회 가능한 기능 (5개 Tool)

### Tool 1: `search_ipo_disclosure` - IPO 공시 검색
- **API**: DART OpenAPI `list.json`
- **기능**: 증권신고서, 투자설명서 등 IPO 관련 공시 검색
- **질문 예시**: "최근 공모주 관련 공시 보여줘", "XX회사 IPO 공시 찾아줘"

### Tool 2: `get_company_info` - 기업 개황 조회
- **API**: DART OpenAPI `company.json`
- **기능**: 회사 기본 정보 (업종, 대표자, 설립일, 홈페이지 등)
- **질문 예시**: "삼성전자 회사 정보 알려줘", "대표이사가 누구야?"

### Tool 3: `get_ipo_price_info` - 공모가격 정보 조회
- **API**: DART OpenAPI `irdsSttus.json`
- **기능**: 증권 발행실적 (공모가, 발행주식수, 액면가 등)
- **질문 예시**: "XX회사 공모가 정보", "공모가 밴드가 어떻게 돼?"

### Tool 4: `naver_search` - 네이버 뉴스 검색
- **API**: Naver Search API `news.json`
- **기능**: 최신 주식/IPO 관련 뉴스 검색
- **질문 예시**: "최근 공모주 뉴스", "삼성전자 관련 뉴스 검색해줘"
- **참고**: `.env`에 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` 미설정 시 안내 메시지 반환

### Tool 5: `get_stock_price` - 한국/해외 주식 통합 시세 조회
- **라이브러리**: yfinance
- **기능**: 한국 및 해외 주식의 현재가, 등락률, 거래량, 시가총액, 52주 고/저 등
- **지원 시장**: KRX(한국), NYSE, NASDAQ, TSE(일본), HKEX(홍콩) 등
- **질문 예시**: "삼성전자 현재 주가", "애플 주가 알려줘", "NVDA 시세", "토요타 주가"

### 복합 질문 (에이전트가 Tool을 자동 조합)
- "삼성전자 종합 분석해줘" → 기업 정보 + 주가 시세 + 최신 뉴스를 순차 호출
- "XX회사 IPO 전체 분석해줘" → 공시 검색 + 기업 정보 + 공모가 정보
- "애플이랑 삼성전자 비교해줘" → 통합 주가 조회 후 비교 분석

---

## 프로젝트 구조

```
app/
├── agents/
│   ├── tools.py          # 5개 도구 함수 (DART, Naver, yfinance)
│   ├── prompts.py        # 주식 전문가 시스템 프롬프트
│   └── stock_agent.py    # create_stock_agent() - LangChain create_agent + InMemorySaver
├── services/
│   └── agent_service.py  # AgentExecutor SSE 스트리밍
├── core/
│   └── config.py         # API 키 설정 (DART, Naver, OpenAI)
└── api/
    └── routes/chat.py    # POST /api/v1/chat 엔드포인트
```

---

## 외부 API 및 의존성

### API 키 설정 (.env)
| 환경변수 | 용도 | 발급처 |
|---|---|---|
| `OPENAI_API_KEY` | LLM (GPT-4.1) | platform.openai.com |
| `DART_API_KEY` | 기업 공시/정보 | opendart.fss.or.kr (무료, 일 10,000건) |
| `NAVER_CLIENT_ID` | 네이버 뉴스 검색 | developers.naver.com (무료, 일 25,000건) |
| `NAVER_CLIENT_SECRET` | 네이버 뉴스 검색 | developers.naver.com |

### Python 패키지
| 패키지 | 용도 |
|---|---|
| `langchain` / `langchain-openai` | 에이전트 프레임워크 |
| `httpx` | DART/Naver API 비동기 호출 |
| `yfinance` | 실시간 주가 조회 |

---

## 핵심 구현 포인트

### 1. 기업코드 매핑 (corpCode.xml 캐시)
- DART API는 `corp_code`로 기업을 식별하지만, 사용자는 회사명으로 질문
- 서버 시작 시(`lifespan`) DART `corpCode.xml` ZIP을 다운로드하여 메모리 캐시 — 첫 요청 지연 방지
- 상장사 우선 매칭 → 정확 일치 → 부분 일치 순으로 검색

### 2. LangChain create_agent
- `langchain.agents.create_agent(model, tools, system_prompt, checkpointer)` 사용
- `InMemorySaver` checkpointer로 멀티턴 대화 지원
- LangGraph 기반 `CompiledStateGraph` 반환
- `astream(stream_mode="updates")`로 SSE 스트리밍

### 3. SSE 스트리밍 프로토콜
```
step: "model"  → 에이전트가 도구 호출을 결정 (tool_calls 배열)
step: "tools"  → 도구 실행 결과 반환
step: "done"   → 최종 답변 (message_id, content, metadata)
```

---

## 실행 방법
```bash
# 서버 실행
uv run uvicorn app.main:app --reload

# 테스트
curl -N -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "message": "삼성전자 현재 주가 알려줘"}'
```

## 검증 완료 항목
- [x] 일반 대화 (도구 없이 직접 답변)
- [x] DART 공시 검색 (`search_ipo_disclosure`)
- [x] 기업 정보 조회 (`get_company_info`) - 삼성전자(005930) 정확 매칭
- [x] 네이버 뉴스 검색 (`naver_search`)
- [x] 한국/해외 주식 통합 시세 조회 (`get_stock_price`) - 삼성전자, 애플 확인
- [x] SSE 스트리밍 (step:model → step:tools → step:done)
- [x] 복합 질문 다중 도구 체이닝
