"""Supervisor + 3개 서브에이전트 기반 주식 전문가 멀티에이전트.

StateGraph를 사용하여 Supervisor가 질문을 분류하고,
적절한 서브에이전트에 위임한 뒤 결과를 종합합니다.
"""
from typing import Literal, TypedDict, Annotated
import operator

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END, MessagesState
import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.agents.prompts import supervisor_prompt
from app.agents.sub_agents import (
    create_market_analyst,
    create_fundamental_analyst,
    create_research_analyst,
)
from app.core.config import settings

# 모듈 레벨 싱글턴 — 서버 재시작 후에도 대화 맥락 유지
_checkpointer: AsyncSqliteSaver | None = None


async def _get_checkpointer() -> AsyncSqliteSaver:
    global _checkpointer
    if _checkpointer is None:
        conn = await aiosqlite.connect("data/conversations.db")
        _checkpointer = AsyncSqliteSaver(conn)
        await _checkpointer.setup()
    return _checkpointer


# ──────────────────────────────────────────────
# State 정의
# ──────────────────────────────────────────────
class SupervisorState(TypedDict):
    """Supervisor 그래프의 상태."""
    messages: Annotated[list, operator.add]
    query_type: str  # market / fundamental / research / comprehensive / general
    sub_results: Annotated[list[str], operator.add]  # 서브에이전트 결과 수집


# ──────────────────────────────────────────────
# 노드 함수들
# ──────────────────────────────────────────────
async def classify_query(state: SupervisorState) -> dict:
    """사용자 질문을 분류하여 적절한 서브에이전트로 라우팅합니다."""
    llm = ChatOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0,
    )

    last_message = state["messages"][-1].content if state["messages"] else ""

    classification_prompt = f"""사용자 질문을 아래 5가지 유형 중 하나로 분류하세요.
반드시 유형명만 답하세요.

유형:
- market: 주가, 시세, 차트, 기술적 분석, RSI, MACD, 이동평균 관련
- fundamental: 재무제표, 매출, 이익, ROE, PER, 종목 비교 관련
- research: 증권사 리포트, 목표주가, 투자의견, 뉴스, 전망 관련
- comprehensive: 종합 분석, 전체 분석, "분석해줘" 같은 포괄적 요청
- general: 투자 용어 설명, 일반 지식 질문 등 도구 호출이 불필요한 질문

사용자 질문: {last_message}

유형:"""

    resp = await llm.ainvoke([HumanMessage(content=classification_prompt)])
    query_type = resp.content.strip().lower()

    # 유효하지 않은 분류 결과 보정
    valid_types = {"market", "fundamental", "research", "comprehensive", "general"}
    if query_type not in valid_types:
        query_type = "comprehensive"

    return {"query_type": query_type}


async def market_analyst_node(state: SupervisorState) -> dict:
    """시세 분석가 서브에이전트를 실행합니다."""
    agent = create_market_analyst()
    user_msg = state["messages"][-1].content if state["messages"] else ""

    result = await agent.ainvoke({"messages": [HumanMessage(content=user_msg)]})
    last_msg = result["messages"][-1].content if result["messages"] else ""

    return {
        "sub_results": [f"[시세 분석가]\n{last_msg}"],
        "messages": [AIMessage(content=f"[시세 분석가 완료]")],
    }


async def fundamental_analyst_node(state: SupervisorState) -> dict:
    """재무 분석가 서브에이전트를 실행합니다."""
    agent = create_fundamental_analyst()
    user_msg = state["messages"][-1].content if state["messages"] else ""

    # 비교 요청이면 원문 그대로, 아니면 재무 분석 요청으로
    result = await agent.ainvoke({"messages": [HumanMessage(content=user_msg)]})
    last_msg = result["messages"][-1].content if result["messages"] else ""

    return {
        "sub_results": [f"[재무 분석가]\n{last_msg}"],
        "messages": [AIMessage(content=f"[재무 분석가 완료]")],
    }


async def research_analyst_node(state: SupervisorState) -> dict:
    """리서치 분석가 서브에이전트를 실행합니다."""
    agent = create_research_analyst()
    user_msg = state["messages"][-1].content if state["messages"] else ""

    result = await agent.ainvoke({"messages": [HumanMessage(content=user_msg)]})
    last_msg = result["messages"][-1].content if result["messages"] else ""

    return {
        "sub_results": [f"[리서치 분석가]\n{last_msg}"],
        "messages": [AIMessage(content=f"[리서치 분석가 완료]")],
    }


async def supervisor_synthesize(state: SupervisorState) -> dict:
    """서브에이전트 결과를 종합하여 최종 응답을 생성합니다."""
    llm = ChatOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0,
    )

    sub_results = state.get("sub_results", [])
    user_msg = ""
    for msg in state["messages"]:
        if isinstance(msg, HumanMessage):
            user_msg = msg.content
            break

    if not sub_results:
        # general 타입: 서브에이전트 없이 직접 응답
        resp = await llm.ainvoke([
            SystemMessage(content=supervisor_prompt),
            HumanMessage(content=user_msg),
        ])
        return {"messages": [AIMessage(content=resp.content)]}

    # 서브에이전트 결과 종합
    combined = "\n\n---\n\n".join(sub_results)

    synthesis_prompt = f"""{supervisor_prompt}

아래는 서브에이전트들의 분석 결과입니다. 이를 종합하여 최종 답변을 작성하세요.

사용자 질문: {user_msg}

서브에이전트 분석 결과:
{combined}

위 분석 결과를 바탕으로 종합적인 최종 답변을 작성하세요.
여러 서브에이전트가 분석한 경우 Bull/Bear 관점을 포함하세요."""

    resp = await llm.ainvoke([HumanMessage(content=synthesis_prompt)])
    return {"messages": [AIMessage(content=resp.content)]}


# ──────────────────────────────────────────────
# 라우팅 함수
# ──────────────────────────────────────────────
def route_by_query_type(state: SupervisorState) -> str:
    """질문 유형에 따라 다음 노드를 결정합니다."""
    query_type = state.get("query_type", "comprehensive")
    routing = {
        "market": "market_analyst",
        "fundamental": "fundamental_analyst",
        "research": "research_analyst",
        "comprehensive": "planner",
        "general": "synthesize",
    }
    return routing.get(query_type, "all_analysts")


# ──────────────────────────────────────────────
# Planner: 종합 분석 시 분석 계획 제시
# ──────────────────────────────────────────────
async def planner_node(state: SupervisorState) -> dict:
    """종합 분석 전 분석 계획을 사용자에게 제시합니다."""
    user_msg = ""
    for msg in state["messages"]:
        if isinstance(msg, HumanMessage):
            user_msg = msg.content
            break

    plan = (
        f"📋 '{user_msg}'에 대해 다음 분석을 진행합니다:\n"
        f"1. 시세 분석가: 현재 시세 + 기술적 지표 (RSI, MACD, 이동평균)\n"
        f"2. 재무 분석가: 재무제표 분석 (매출, 영업이익, ROE)\n"
        f"3. 리서치 분석가: 증권사 리포트 + 뉴스 감성 분석\n"
        f"4. Supervisor: Bull/Bear 관점 종합"
    )
    return {"messages": [AIMessage(content=f"[분석 계획]\n{plan}")]}


# ──────────────────────────────────────────────
# 종합 분석용: 3개 서브에이전트 병렬 호출
# ──────────────────────────────────────────────
async def _run_sub_agent(name: str, create_fn, user_msg: str) -> tuple[str, str]:
    """서브에이전트를 실행하고 (name, result) 튜플을 반환합니다."""
    agent = create_fn()
    result = await agent.ainvoke({"messages": [HumanMessage(content=user_msg)]})
    last_msg = result["messages"][-1].content if result["messages"] else ""
    return name, last_msg


async def all_analysts_node(state: SupervisorState) -> dict:
    """3개 서브에이전트를 병렬 호출하여 종합 분석 데이터를 수집합니다."""
    import asyncio

    user_msg = ""
    for msg in state["messages"]:
        if isinstance(msg, HumanMessage):
            user_msg = msg.content
            break

    # 3개 서브에이전트 병렬 실행
    tasks = [
        _run_sub_agent("시세 분석가", create_market_analyst, user_msg),
        _run_sub_agent("재무 분석가", create_fundamental_analyst, user_msg),
        _run_sub_agent("리서치 분석가", create_research_analyst, user_msg),
    ]
    results = await asyncio.gather(*tasks)

    sub_results = [f"[{name}]\n{content}" for name, content in results]
    status_messages = [AIMessage(content=f"[{name} 완료]") for name, _ in results]

    return {
        "sub_results": sub_results,
        "messages": status_messages,
    }


# ──────────────────────────────────────────────
# 그래프 빌드
# ──────────────────────────────────────────────
async def create_stock_agent():
    """Supervisor + 서브에이전트 멀티에이전트 그래프를 생성합니다."""
    checkpointer = await _get_checkpointer()
    graph = StateGraph(SupervisorState)

    # 노드 등록
    graph.add_node("classify", classify_query)
    graph.add_node("planner", planner_node)
    graph.add_node("market_analyst", market_analyst_node)
    graph.add_node("fundamental_analyst", fundamental_analyst_node)
    graph.add_node("research_analyst", research_analyst_node)
    graph.add_node("all_analysts", all_analysts_node)
    graph.add_node("synthesize", supervisor_synthesize)

    # 엣지 연결
    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", route_by_query_type)

    # Planner → 전체 분석
    graph.add_edge("planner", "all_analysts")

    # 개별 서브에이전트 → 종합
    graph.add_edge("market_analyst", "synthesize")
    graph.add_edge("fundamental_analyst", "synthesize")
    graph.add_edge("research_analyst", "synthesize")
    graph.add_edge("all_analysts", "synthesize")

    # 종합 → 종료
    graph.add_edge("synthesize", END)

    return graph.compile(checkpointer=checkpointer)
