import json
from datetime import datetime
import uuid

from langchain_core.messages import HumanMessage

from app.utils.logger import log_execution, custom_logger


class AgentService:
    def __init__(self):
        self.agent = None

    def _create_agent(self, thread_id: uuid.UUID = None):
        """LangChain create_agent() 기반 주식 전문가 에이전트 생성"""
        from app.agents.stock_agent import create_stock_agent
        self.agent = create_stock_agent()

    @log_execution
    async def process_query(self, user_messages: str, thread_id: uuid.UUID):
        """사용자 쿼리를 처리하고 SSE 스트리밍으로 응답합니다."""
        try:
            self._create_agent(thread_id=thread_id)

            custom_logger.info(f"사용자 메시지: {user_messages}")

            agent_stream = self.agent.astream(
                {"messages": [HumanMessage(content=user_messages)]},
                config={"configurable": {"thread_id": str(thread_id)}},
                stream_mode="updates",
            )

            async for chunk in agent_stream:
                custom_logger.info(f"에이전트 청크: {chunk}")

                for step, event in chunk.items():
                    if not event or step not in ("model", "tools"):
                        continue

                    messages = event.get("messages", [])
                    if not messages:
                        continue

                    message = messages[0]

                    if step == "model":
                        tool_calls = getattr(message, "tool_calls", None)
                        if not tool_calls:
                            # 도구 호출 없이 최종 텍스트 응답
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
