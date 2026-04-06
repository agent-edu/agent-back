# 주식 전문가 AI 에이전트 — 현황 분석 및 고도화 계획

## 1. Agent의 문제범위

### 현재 에이전트가 해결하는 문제

> "한국/해외 주식에 관심 있는 투자자가 자연어로 질문하면, 여러 데이터 소스를 조합해 종합적인 답변을 제공하는 AI 어시스턴트"

### 지원하는 질문 유형

| 카테고리 | 예시 질문 | 사용 도구 |
|---------|---------|---------|
| 시세 조회 | "삼성전자 현재 주가 알려줘" | `get_stock_price` (yfinance) |
| 해외 주식 | "테슬라 주가 얼마야?" | `get_stock_price` (한글명→티커 매핑) |
| 기업 정보 | "카카오 기업 개황 알려줘" | `get_company_info` (DART API) |
| IPO 공시 | "최근 IPO 공시 검색해줘" | `search_ipo_disclosure` (DART API) |
| 공모가격 | "XX기업 공모가 정보" | `get_ipo_price_info` (DART API) |
| 뉴스 검색 | "삼성전자 관련 최신 뉴스" | `naver_search` (네이버 뉴스 API) |
| 종합 분석 | "카카오 종합 분석해줘" | 여러 도구 조합 |

### 데이터 소스

- **DART OpenAPI** — 기업 공시, 기업 개황, IPO/공모가 정보
- **네이버 검색 API** — 최신 뉴스
- **yfinance** — 한국/해외 실시간 시세, PER, 시가총액 등

---

## 2. 현재 아키텍처

```
[사용자] → POST /chat (SSE)
              ↓
         chat_router
              ↓
        AgentService.process_query()
              ↓
        create_stock_agent()  ← create_agent() + InMemorySaver
              ↓
        LangGraph astream (stream_mode="updates")
              ↓
        ┌─────────────────────────────┐
        │  model 단계 (LLM 추론)       │
        │    ↓                        │
        │  tools 단계 (도구 실행)       │
        │    ↓                        │
        │  done (최종 응답)            │
        └─────────────────────────────┘
              ↓
         SSE 스트리밍 → [사용자]
```

### 계층 구조

```
API (routes/)  →  Service (services/)  →  Agent (agents/)  →  Data (data/)
   chat.py          AgentService           stock_agent.py      threads.json
   threads.py       ConversationService    tools.py (5개)      favorite_questions.json
                    ThreadsService         prompts.py          threads/{id}.json
```

### 핵심 구성 요소

| 구성 요소 | 현재 구현 | 파일 |
|---------|---------|------|
| 페르소나 | 한국/글로벌 주식시장 전문가 | `agents/prompts.py` |
| LLM | ChatOpenAI (temperature=0) | `agents/stock_agent.py` |
| 도구 | 5개 (DART 3개 + 네이버 + 시세) | `agents/tools.py` |
| 메모리 | InMemorySaver (서버 재시작 시 초기화) | `agents/stock_agent.py` |
| 트레이싱 | Opik (선택적) | `services/agent_service.py` |
| 대화 저장 | JSON 파일 기반 (수동) | `data/` |

---

## 3. 현재 문제점 분석

### 문제 A: 단일 에이전트의 한계

**증상**: "삼성전자 종합 분석해줘"라고 하면 도구를 순차적으로 하나씩 호출 → 응답이 느리고, 분석 깊이가 얕음

**원인**: `create_agent()` 기반 단일 ReAct 루프는 LLM이 알아서 도구를 선택하는 구조. 복잡한 분석에서는 도구 호출 순서나 조합을 제어할 수 없음.

**영향**: 
- 종합 분석 시 일부 도구만 호출하고 끝나는 경우 발생
- 도구 호출 실패 시 재시도 로직 없음
- 분석 흐름을 사용자가 제어할 수 없음

### 문제 B: 분석 도구 부족

**증상**: 시세 조회는 되지만 "이 종목 사야 할까?"에 대한 판단 근거 부족

**원인**: 재무제표, 기술적 지표(이동평균, RSI 등), 종목 비교 기능이 없음

**영향**:
- 현재가만 알려주고 트렌드/맥락 정보 부재
- 투자 판단에 필요한 정량적 데이터 미제공
- 경쟁 종목과의 비교 불가

### 문제 C: 대화 지속성 부재

**증상**: 서버 재시작 시 모든 대화 맥락 소실

**원인**: `InMemorySaver`는 프로세스 메모리에만 저장

**영향**:
- 서버 배포/재시작 시 모든 대화 초기화
- 이전 대화를 참조한 후속 질문 불가

### 문제 D: 에이전트 사고 흐름 제어 불가

**증상**: 에이전트가 어떤 순서로 도구를 호출할지 예측 불가

**원인**: `create_agent()`는 LLM에게 전적으로 위임하는 고수준 API → 사고 단계를 직접 정의할 수 없음

**영향**:
- 같은 질문에도 다른 도구 호출 패턴
- 불필요한 도구 호출로 토큰/시간 낭비
- 디버깅이 어려움

---

## 4. 고도화 계획

### Phase 1: 도구 확장 (즉시 구현 가능)

| 새 도구 | 설명 | 데이터 소스 |
|--------|------|-----------|
| `get_financial_statements` | 매출/영업이익/순이익 등 재무제표 | DART API (`fnlttSinglAcnt.json`) |
| `get_technical_indicators` | 이동평균, RSI, 거래량 추이 등 기술 지표 | yfinance `history()` |
| `compare_stocks` | 2~3 종목 PER/시총/수익률 비교 | yfinance 조합 |

### Phase 2: StateGraph 전환 (구조적 개선)

```
현재: create_agent() → 단일 ReAct 루프 (LLM 자율)
목표: StateGraph → 사고 단계를 노드로 정의 → 흐름 직접 제어

[질문분석] → [정보수집] → [분석종합] → [응답생성]
   Node        Node         Node         Node
```

### Phase 3: 대화 영속성 (SqliteSaver)

```python
# 변경 전
_checkpointer = InMemorySaver()

# 변경 후  
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
_checkpointer = AsyncSqliteSaver.from_conn_string("data/conversations.db")
```

### Phase 4: 멀티에이전트 (선택적)

- 시세 분석 에이전트 / 공시 분석 에이전트 / 뉴스 분석 에이전트 분리
- Supervisor 패턴으로 조율

---

## 5. 발표 시연 시나리오 (예시)

### 기본 시연
1. "삼성전자 현재 주가" → 시세 조회 도구 동작 확인
2. "최근 삼성전자 뉴스" → 네이버 검색 도구 동작 확인
3. "삼성전자 기업 정보" → DART 기업 개황 조회

### 고도화 후 시연
4. "삼성전자 재무제표 보여줘" → 새 도구: 재무제표 분석
5. "삼성전자 vs SK하이닉스 비교" → 새 도구: 종목 비교
6. "삼성전자 종합 분석" → StateGraph 기반 멀티스텝 분석 흐름

---

## 6. 어려웠던 점 및 극복 방법 (작성 예정)

> 고도화 구현 과정에서 겪는 문제들을 여기에 기록해 나갈 예정

| 문제 | 원인 | 해결 방법 |
|------|------|---------|
| (구현 후 기록) | | |
