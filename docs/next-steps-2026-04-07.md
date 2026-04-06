# 주식 에이전트 고도화 — 다음 작업 목록

> 작성일: 2026-04-07
> 현재 상태: 도구 3개 (시세 조회, 뉴스 검색, 증권사 리포트 RAG 검색) 운영 중

---

## 현재 완료된 기능

| 기능 | 도구 | 데이터 소스 |
|------|------|-----------|
| 실시간 시세 조회 | `get_stock_price` | yfinance + DART 기업코드 |
| 뉴스 검색 | `naver_search` | 네이버 뉴스 API |
| 증권사 리포트 RAG 검색 | `search_research_reports` | Elasticsearch (하이브리드: BM25 + kNN) |
| PDF 크롤링/파싱/적재 파이프라인 | pipeline/ | 네이버 증권 리서치 → ES |

---

## 남은 작업 목록

### 1. 도구 추가 (3개)

현재 3개 → 6개로 확장. 에이전트의 분석 깊이를 높이기 위함.

#### 1-1. `get_financial_summary` — 재무제표 요약

```
입력: 회사명 또는 티커
출력: 최근 4분기/연간 매출, 영업이익, 순이익, ROE, 부채비율
소스: yfinance의 financials, balance_sheet, cashflow
```

- yfinance `Ticker.financials` → 손익계산서 (매출, 영업이익, 순이익)
- yfinance `Ticker.balance_sheet` → 재무상태표 (총자산, 부채, 자본)
- yfinance `Ticker.cashflow` → 현금흐름표
- 한국 주식은 DART 기업코드 변환 후 `.KS`/`.KQ` 접미사 사용
- 데이터 출처를 `[yfinance]` 태그로 명시

#### 1-2. `get_technical_indicators` — 기술적 지표

```
입력: 회사명 또는 티커, 기간 (기본 3개월)
출력: 이동평균(20/60/120일), RSI(14일), MACD, 볼린저밴드, 거래량 추이
소스: yfinance의 history()
```

- `yfinance.Ticker.history(period="3mo")` 로 일봉 데이터 수집
- 이동평균선: 종가 기반 rolling mean
- RSI: 14일 기준 상대강도지수 (과매수 70 이상, 과매도 30 이하)
- MACD: 12일 EMA - 26일 EMA, 시그널선 9일 EMA
- 볼린저밴드: 20일 이동평균 ± 2표준편차
- 현재 추세 판단 문구 포함 (예: "RSI 72로 과매수 구간")

#### 1-3. `compare_stocks` — 종목 비교

```
입력: 종목명 2~3개 (콤마 구분)
출력: 비교 테이블 (시총, PER, PBR, 수익률, 배당률 등)
소스: yfinance
```

- 입력값을 파싱하여 각 종목별 yfinance 조회
- 비교 항목: 현재가, 시가총액, PER, PBR, 배당률, 52주 수익률, 거래량
- 테이블 형태의 문자열로 반환
- 한국/해외 종목 혼합 비교도 지원 (통화 변환은 하지 않고 원본 표시)

---

### 2. 멀티에이전트 (Supervisor 패턴) + StateGraph 전환

현재 `create_agent()` 단일 에이전트 → **Supervisor + 3개 서브에이전트** 구조로 전환.
StateGraph를 사용하여 Supervisor가 질문을 분류하고 적절한 서브에이전트에 위임한다.

#### 목표 아키텍처

```
[Supervisor 에이전트] — 질문 의도 파악 → 적절한 서브에이전트에 위임 → 결과 종합
    │
    ├── [시세 분석가 (Market Analyst)]
    │    도구: get_stock_price, get_technical_indicators
    │    페르소나: 트레이더/기술적 분석 전문가
    │    역할: 차트 패턴, 추세, 매매 타이밍 판단
    │
    ├── [재무 분석가 (Fundamental Analyst)]
    │    도구: get_financial_summary, compare_stocks
    │    페르소나: 펀더멘털 애널리스트
    │    역할: 재무 건전성, 밸류에이션, 성장성 평가
    │
    └── [리서치 분석가 (Research Analyst)]
         도구: search_research_reports, naver_search
         페르소나: 리서치 애널리스트
         역할: 증권사 의견 종합, 뉴스 맥락 해석
```

#### 라우팅 로직 (Supervisor의 질문 분류)

```
"삼성전자 주가"          → 시세 분석가만 호출
"삼성전자 재무제표"      → 재무 분석가만 호출
"삼성전자 전망"          → 리서치 분석가만 호출
"삼성전자 vs SK하이닉스" → 재무 분석가만 호출 (compare_stocks)
"삼성전자 종합 분석"     → 3개 서브에이전트 모두 호출 → Supervisor가 종합
"PER이 뭐야?"           → Supervisor가 LLM 지식으로 직접 응답
```

#### StateGraph 구조

```
START → [Supervisor: 질문 분류]
              │
              ├─ "market"    → [시세 분석가] ──────────┐
              ├─ "fundamental"→ [재무 분석가] ──────────┤
              ├─ "research"  → [리서치 분석가] ─────────┤
              ├─ "comprehensive" → [시세] + [재무] + [리서치] (병렬) ─┤
              └─ "general"   → [Supervisor 직접 응답] ─┤
                                                       │
                                              [Supervisor: 결과 종합]
                                                       │
                                                      END
```

#### 변경 대상 파일

- `app/agents/stock_agent.py` — `create_agent()` → `StateGraph` + Supervisor 정의
- `app/agents/sub_agents.py` (신규) — 3개 서브에이전트 정의 (각각 고유 페르소나 + 도구)
- `app/agents/state.py` (신규) — AgentState TypedDict 정의
- `app/agents/prompts.py` — Supervisor + 서브에이전트별 페르소나 4개

#### 서브에이전트별 페르소나 예시

```python
# Supervisor
supervisor_prompt = """당신은 수석 투자 애널리스트입니다.
사용자의 질문을 분석하여 적절한 전문가에게 위임합니다.
각 전문가의 분석 결과를 종합하여 최종 투자 의견을 제시합니다."""

# 시세 분석가
market_analyst_prompt = """당신은 기술적 분석 전문 트레이더입니다.
차트 패턴, 이동평균선, RSI, MACD 등을 기반으로 매매 타이밍을 판단합니다."""

# 재무 분석가
fundamental_analyst_prompt = """당신은 펀더멘털 분석 전문가입니다.
재무제표를 기반으로 기업의 내재가치를 평가합니다."""

# 리서치 분석가
research_analyst_prompt = """당신은 리서치 애널리스트입니다.
증권사 리포트와 뉴스를 종합하여 시장 센티먼트를 분석합니다."""
```

#### SSE 스트리밍 표시 (프론트 연동)

서브에이전트가 작업 중일 때 프론트에 실시간 표시:
```json
{"step": "model", "tool_calls": ["시세 분석가가 분석 중..."]}
{"step": "tools", "name": "get_stock_price", "content": "..."}
{"step": "model", "tool_calls": ["재무 분석가가 분석 중..."]}
{"step": "tools", "name": "get_financial_summary", "content": "..."}
{"step": "model", "tool_calls": ["리서치 분석가가 리포트 검색 중..."]}
{"step": "tools", "name": "search_research_reports", "content": "..."}
{"step": "done", "content": "[Supervisor 종합 의견] ..."}
```

#### 참고

- LangGraph의 `create_supervisor()` 또는 수동 StateGraph로 구현 가능
- 종합 분석 시 항상 일관된 순서로 서브에이전트 호출 보장
- 디버깅 용이: 어느 서브에이전트에서 실패했는지 추적 가능
- 단순 질문은 서브에이전트 1개만 호출하여 응답 속도 유지

---

### 3. 시스템 프롬프트 개선

#### 3-1. 추가할 원칙

```
# 데이터 신뢰 원칙:
- 모르는 정보는 "해당 정보를 확인할 수 없습니다"로 명시. 절대 추측하지 않습니다.
- 모든 수치에 출처를 표기합니다: [yfinance], [네이버뉴스], [증권리포트], [LLM지식]

# 종합 분석 프레임워크:
- 종합 분석 요청 시 다음 구조로 답변합니다:
  1. 현재 시세 요약
  2. 기술적 지표 해석 (추세 판단)
  3. 재무 건전성 평가
  4. 증권사 리포트 요약 (목표주가, 투자의견)
  5. 최신 뉴스 + 감성 분석
  6. Bull/Bear 종합 의견
```

#### 3-2. 뉴스 감성 분석 (리서치 분석가 프롬프트)

**해결하는 문제**: 뉴스 링크만 나열하고 좋은 뉴스인지 나쁜 뉴스인지 모름
**참고**: PrimoAgent의 NLP 기반 뉴스 감성 7개 지표

```
# 뉴스 감성 분석 지침:
- 각 뉴스에 감성 점수를 부여합니다: 긍정(0.5~1.0), 중립(0.3~0.5), 부정(0.0~0.3)
- 전체 뉴스의 평균 센티먼트를 계산합니다
- 투자에 미치는 영향을 한 줄로 요약합니다
- 예시: "HBM3E 양산 본격화" — 긍정 (0.85)
```

#### 3-3. Bull/Bear 토론 구조 (Supervisor 프롬프트)

**해결하는 문제**: 한쪽 관점만 제시하여 균형 잡힌 판단 불가
**참고**: TradingAgents의 Bull/Bear 리서처 토론 구조

```
# Bull/Bear 종합 지침:
- 종합 분석 시 반드시 두 관점을 제시합니다:
  Bull (긍정): 최소 2개 근거 + 목표 시나리오
  Bear (부정): 최소 2개 근거 + 리스크 시나리오
- 양쪽 근거를 종합하여 균형 잡힌 최종 의견을 제시합니다
- 어느 한쪽으로 치우치지 않습니다
```

#### 3-4. Planner 단계 (Human-in-the-loop)

**해결하는 문제**: 사용자가 분석 범위를 제어할 수 없음
**참고**: LangAlpha의 Planner 에이전트

```
# Planner 지침 (종합 분석 시에만 적용):
- 분석 시작 전 분석 계획을 사용자에게 제시합니다
- 사용자가 항목을 추가/제거할 수 있습니다
- 단순 질문(시세 조회, 뉴스 검색 등)에는 Planner를 적용하지 않습니다
```

구현: LangGraph의 `interrupt_before` 기능 사용

```python
# StateGraph에서 종합 분석 노드 진입 전 interrupt
graph.add_edge("classify", "planner")
graph.add_node("planner", planner_node)
# planner 노드에서 interrupt_before로 사용자 승인 대기
```

---

### 4. 대화 영속성 (SqliteSaver)

```python
# 변경 전 (app/agents/stock_agent.py)
from langgraph.checkpoint.memory import InMemorySaver
_checkpointer = InMemorySaver()

# 변경 후
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
_checkpointer = AsyncSqliteSaver.from_conn_string("data/conversations.db")
```

- 서버 재시작 후에도 대화 맥락 유지
- `data/conversations.db` 파일로 SQLite 저장
- 기존 JSON 파일 기반 threads 저장과 병행 가능

---

### 5. 프론트엔드 시각화 (agent-web)

> 프로젝트 경로: `/Users/doo/Desktop/project/agent-study/agent-web`

#### 현재 agent-web 기술 스택

| 분류 | 기술 |
|------|------|
| 프레임워크 | React 19 + TypeScript + Vite |
| 상태관리 | Jotai (atoms) |
| UI | Material-UI (MUI) 6 |
| 차트 | Highcharts 12 (Column, Pie, Line 지원) |
| 데이터 그리드 | @highcharts/grid-lite |
| SSE 처리 | @microsoft/fetch-event-source |
| 스타일링 | Styled-Components + SCSS Modules |

#### 현재 UI 구조

```
Layout
├── Menu (64px 사이드바) — 대시보드, Chat, 설정 아이콘
├── SubMenu (280px 확장) — 즐겨찾기, 최근 대화, 검색
└── Content
    ├── InitPage — 초기 화면 (즐겨찾기 카드)
    └── ChatPage — 채팅 인터페이스
         ├── 메시지 영역 (max-width: 768px)
         │    ├── 사용자 메시지 (오른쪽, 보라색 배경)
         │    └── 봇 메시지 (왼쪽)
         │         ├── 텍스트 응답
         │         └── metadata 기반 시각화
         │              ├── CodeEditor (SQL 코드)
         │              ├── GridViewer (데이터 테이블)
         │              └── ChartViewer (Highcharts 차트)
         └── MessageInput (하단 고정, 전송 버튼)
```

#### SSE 이벤트 처리 흐름

```
MessageInput → useChat.handleSendMessage()
  → chatService.sendMessage(threadId, message, handleChunk)
    → fetchEventSource POST /api/v1/chat
      → 백엔드 SSE 스트림 수신
        → onmessage: JSON 파싱 → snake_case→camelCase 변환
          → handleChunk(step, content, metadata, toolCalls, name)
            → answerAtom 업데이트 → ChatPage 리렌더
```

#### 현재 metadata 구조 (백엔드 → 프론트)

현재 프론트가 인식하는 metadata 형식:
```typescript
{
  sql: string;           // CodeEditor에 표시
  data: {                // GridViewer에 표시
    dataTable: {
      columns: Record<string, any[]>
    }
  },
  chart: {               // ChartViewer에 표시
    chart_data: {
      type: 'column' | 'pie' | 'line',
      title?: string,
      series: any[],
      xAxis?: any,
      yAxis?: any
    }
  }
}
```

#### 프론트엔드 수정 계획

**5-1. 도구 결과 시각화** — 백엔드에서 metadata에 차트 데이터를 포함하여 전달

| 도구 | metadata 추가 내용 | 프론트 컴포넌트 |
|------|-------------------|---------------|
| `get_financial_summary` | `chart.chart_data` (column 타입, 매출/이익 추이) | 기존 ChartViewer 재사용 |
| `get_technical_indicators` | `chart.chart_data` (line 타입, 주가+이동평균선) | 기존 ChartViewer 재사용 |
| `compare_stocks` | `data.dataTable` (종목별 비교 테이블) | 기존 GridViewer 재사용 |

핵심: **기존 ChartViewer/GridViewer를 그대로 활용 가능**. 백엔드에서 metadata 형식만 맞춰주면 된다.

**5-2. 백엔드 SSE 응답 수정** — `step:"tools"` 이벤트에 metadata 포함

현재 백엔드의 tools step 응답:
```json
{"step": "tools", "name": "get_stock_price", "content": "종목: 삼성전자..."}
```

변경 후 (metadata 추가):
```json
{
  "step": "tools",
  "name": "get_financial_summary",
  "content": "삼성전자 재무 요약...",
  "metadata": {
    "chart": {
      "chart_data": {
        "type": "column",
        "title": "삼성전자 연간 재무 추이",
        "xAxis": { "categories": ["2023", "2024", "2025"] },
        "series": [
          { "name": "매출", "data": [258, 279, 301] },
          { "name": "영업이익", "data": [6.5, 36.8, 45.2] }
        ]
      }
    }
  }
}
```

**5-3. 프론트 수정 필요 사항**

| 파일 | 수정 내용 |
|------|---------|
| `src/hooks/useChat.ts` | handleChunk에서 `step:"tools"` 일 때도 metadata 저장하도록 수정 |
| `src/pages/ChatPage.tsx` | tools step 메시지에도 ChartViewer/GridViewer 렌더링 |
| `src/services/agent_service.py` (백엔드) | tools step 응답에 metadata 필드 추가 |

#### 프론트 주요 파일 경로

| 파일 | 역할 |
|------|------|
| `src/hooks/useChat.ts` | SSE 청크 처리, 메시지 상태 관리 |
| `src/pages/ChatPage.tsx` | 메시지 렌더링, 도구 결과 표시 |
| `src/services/common.ts` | fetchEventSource SSE 핸들러 |
| `src/services/chatService.ts` | 백엔드 API 호출 |
| `src/components/ChartViewer/index.tsx` | Highcharts 차트 (Column/Pie/Line) |
| `src/components/GridViewer/index.tsx` | 데이터 테이블 (Highcharts Grid) |
| `src/store/answer.ts` | Jotai 봇 답변 상태 atom |
| `src/types/chatVM.ts` | 메시지 타입 정의 |

---

### 6. 현행 문서 업데이트

작업 완료 후 `docs/current-state-2026-04-06.md`를 새 날짜로 갱신할 것.

변경 반영 항목:
- 도구 목록 (3개 → 6개)
- 에이전트 구조 (create_agent → StateGraph)
- 아키텍처 흐름도 (mermaid)
- 체크포인터 (InMemorySaver → SqliteSaver)
- 시스템 프롬프트 변경사항

---

## 구현 우선순위

```
[높음] 1. 도구 3개 추가 (재무제표, 기술지표, 종목비교)
[높음] 2. 멀티에이전트 전환 (Supervisor + 3개 서브에이전트 + StateGraph)
[높음] 3. 서브에이전트별 페르소나 정의 (역할 + Bull/Bear 토론 구조)
[높음] 4. 뉴스 감성 분석 (리서치 분석가 프롬프트)
[중간] 5. Planner 단계 (Human-in-the-loop, 종합 분석 시에만)
[중간] 6. SqliteSaver 전환
[중간] 7. 프론트엔드 시각화 (백엔드 metadata 전달 + 프론트 렌더링)
[낮음] 8. 문서 갱신
```

### 참고한 오픈소스

| 프로젝트 | 가져온 아이디어 |
|---------|--------------|
| [kipeum86/stock-analysis-agent](https://github.com/kipeum86/stock-analysis-agent) | Blank > Wrong 원칙, 출처 태그 |
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | Bull/Bear 토론 구조 |
| [LangAlpha](https://github.com/Chen-zexi/LangAlpha) | Planner 에이전트 (분석 계획 → 사용자 승인) |
| [PrimoAgent](https://github.com/ivebotunac/PrimoAgent) | 뉴스 감성 분석 |

---

## 빠른 참조: 주요 파일 경로

### 백엔드 (agent-back)

| 파일 | 역할 |
|------|------|
| `app/agents/tools.py` | 도구 정의 (여기에 새 도구 추가) |
| `app/agents/stock_agent.py` | 에이전트 생성 (도구 등록, StateGraph 전환 대상) |
| `app/agents/prompts.py` | 시스템 프롬프트 |
| `app/core/config.py` | 환경변수 설정 |
| `app/services/agent_service.py` | SSE 스트리밍 처리 (metadata 전달 수정 대상) |
| `pipeline/` | PDF 크롤링/파싱/ES 적재 파이프라인 |

### 프론트엔드 (agent-web)

| 파일 | 역할 |
|------|------|
| `src/hooks/useChat.ts` | SSE 청크 처리 (handleChunk 수정 대상) |
| `src/pages/ChatPage.tsx` | 메시지 렌더링 (도구 결과 시각화 수정 대상) |
| `src/services/common.ts` | SSE 핸들러 |
| `src/components/ChartViewer/index.tsx` | Highcharts 차트 (재사용) |
| `src/components/GridViewer/index.tsx` | 데이터 테이블 (재사용) |
| `src/store/answer.ts` | 봇 답변 상태 atom |
| `src/types/chatVM.ts` | 메시지 타입 정의 |
