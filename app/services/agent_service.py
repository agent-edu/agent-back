import json
from datetime import datetime
import uuid

from langchain_core.messages import HumanMessage

from app.core.config import settings
from app.utils.logger import log_execution, custom_logger


class AgentService:
    def __init__(self):
        self.agent = None

    async def _create_agent(self, thread_id: uuid.UUID = None):
        """Supervisor + 서브에이전트 멀티에이전트 생성"""
        from app.agents.stock_agent import create_stock_agent
        self.agent = await create_stock_agent()

    @log_execution
    async def process_query(self, user_messages: str, thread_id: uuid.UUID):
        """사용자 쿼리를 처리하고 SSE 스트리밍으로 응답합니다."""
        try:
            await self._create_agent(thread_id=thread_id)

            custom_logger.info(f"사용자 메시지: {user_messages}")

            callbacks = []
            if settings.OPIK:
                from opik.integrations.langchain import OpikTracer
                opik_tracer = OpikTracer(
                    project_name=settings.OPIK.PROJECT,
                    tags=["chat"],
                    metadata={"thread_id": str(thread_id)},
                )
                callbacks.append(opik_tracer)

            stream_config = {"configurable": {"thread_id": str(thread_id)}}
            if callbacks:
                stream_config["callbacks"] = callbacks

            agent_stream = self.agent.astream(
                {"messages": [HumanMessage(content=user_messages)]},
                config=stream_config,
                stream_mode="updates",
            )

            async for chunk in agent_stream:
                custom_logger.info(f"에이전트 청크: {chunk}")

                for step, event in chunk.items():
                    if not event:
                        continue

                    messages = event.get("messages", [])
                    if not messages:
                        continue

                    # 분류 단계는 스킵
                    if step in ("classify",):
                        continue

                    # Planner: 분석 계획 표시
                    if step == "planner":
                        message = messages[-1]
                        content = getattr(message, "content", "")
                        if content:
                            yield json.dumps(
                                {
                                    "step": "model",
                                    "tool_calls": [content],
                                },
                                ensure_ascii=False,
                            )
                        continue

                    # 서브에이전트 진행 상태 표시
                    if step in ("market_analyst", "fundamental_analyst", "research_analyst", "all_analysts"):
                        for message in messages:
                            content = getattr(message, "content", "")
                            if content and content.startswith("[") and content.endswith("완료]"):
                                yield json.dumps(
                                    {
                                        "step": "model",
                                        "tool_calls": [content.strip("[]")],
                                    },
                                    ensure_ascii=False,
                                )
                        continue

                    # 최종 종합 응답 (synthesize)
                    if step == "synthesize":
                        message = messages[-1]
                        content = getattr(message, "content", "")
                        if content:
                            yield json.dumps(
                                {
                                    "step": "done",
                                    "message_id": str(uuid.uuid4()),
                                    "role": "assistant",
                                    "content": content,
                                    "metadata": {},
                                    "created_at": datetime.utcnow().isoformat(),
                                },
                                ensure_ascii=False,
                            )
                        continue

                    # 기존 호환: model/tools step (서브에이전트 내부 동작)
                    if step == "model":
                        message = messages[0]
                        tool_calls = getattr(message, "tool_calls", None)
                        if not tool_calls:
                            content = getattr(message, "content", "")
                            if content:
                                yield json.dumps(
                                    {
                                        "step": "done",
                                        "message_id": str(uuid.uuid4()),
                                        "role": "assistant",
                                        "content": content,
                                        "metadata": {},
                                        "created_at": datetime.utcnow().isoformat(),
                                    },
                                    ensure_ascii=False,
                                )
                        else:
                            yield json.dumps(
                                {
                                    "step": "model",
                                    "tool_calls": [t["name"] for t in tool_calls],
                                },
                                ensure_ascii=False,
                            )

                    elif step == "tools":
                        message = messages[0]
                        tool_name = getattr(message, "name", "")
                        tool_content = getattr(message, "content", "")
                        yield json.dumps(
                            {
                                "step": "tools",
                                "name": tool_name,
                                "content": tool_content,
                            },
                            ensure_ascii=False,
                        )

        except Exception as e:
            import traceback
            custom_logger.error(f"Error in process_query: {e}")
            custom_logger.error(traceback.format_exc())

            yield json.dumps(
                {
                    "step": "done",
                    "message_id": str(uuid.uuid4()),
                    "role": "assistant",
                    "content": "처리 중 오류가 발생했습니다. 다시 시도해주세요.",
                    "metadata": {},
                    "created_at": datetime.utcnow().isoformat(),
                    "error": "internal_error",
                },
                ensure_ascii=False,
            )
