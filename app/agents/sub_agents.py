"""서브에이전트 3개를 정의합니다.
각 서브에이전트는 고유한 페르소나와 전담 도구를 갖습니다.
"""
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

from app.agents.prompts import (
    market_analyst_prompt,
    fundamental_analyst_prompt,
    research_analyst_prompt,
)
from app.agents.tools import (
    get_stock_price,
    get_technical_indicators,
    get_financial_summary,
    compare_stocks,
    search_research_reports,
    naver_search,
)
from app.core.config import settings


def _create_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0,
    )


def create_market_analyst():
    """시세 분석가 — 실시간 시세 + 기술적 지표 분석"""
    return create_react_agent(
        model=_create_llm(),
        tools=[get_stock_price, get_technical_indicators],
        prompt=market_analyst_prompt,
    )


def create_fundamental_analyst():
    """재무 분석가 — 재무제표 + 종목 비교"""
    return create_react_agent(
        model=_create_llm(),
        tools=[get_financial_summary, compare_stocks],
        prompt=fundamental_analyst_prompt,
    )


def create_research_analyst():
    """리서치 분석가 — 증권사 리포트 RAG + 뉴스 감성 분석"""
    return create_react_agent(
        model=_create_llm(),
        tools=[search_research_reports, naver_search],
        prompt=research_analyst_prompt,
    )
