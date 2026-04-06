from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.prompts import system_prompt
from app.agents.tools import get_stock_price, naver_search
from app.core.config import settings

# 모듈 레벨 싱글턴 — 멀티턴 대화를 위한 체크포인터
_checkpointer = InMemorySaver()


def create_stock_agent():
    """LangChain create_agent()를 사용하여 주식 전문가 에이전트를 생성합니다."""
    llm = ChatOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0,
    )

    tools = [get_stock_price, naver_search]

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=_checkpointer,
    )

    return agent
