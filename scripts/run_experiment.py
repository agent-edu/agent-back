"""
Opik Experiment 실행 스크립트

사용법:
    python -m scripts.run_experiment

Dataset CSV를 Opik에 등록하고, 에이전트를 실행하여 평가 메트릭을 측정합니다.
"""

import asyncio
import csv
import uuid

from opik import Opik, track
from opik.evaluation import evaluate
from opik.evaluation.metrics import base_metric, score_result
from langchain_core.messages import HumanMessage

from app.agents.stock_agent import create_stock_agent
from app.core.config import settings


# ──────────────────────────────────────────────
# 1) Dataset 등록
# ──────────────────────────────────────────────
def setup_dataset(client: Opik, csv_path: str, dataset_name: str) -> None:
    """CSV 파일을 읽어 Opik Dataset에 등록합니다."""
    dataset = client.get_or_create_dataset(name=dataset_name)

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        items = [
            {
                "input": row["input"],
                "expected_tool": row["expected_tool"],
                "category": row["category"],
            }
            for row in reader
        ]

    dataset.insert(items)
    print(f"Dataset '{dataset_name}' 등록 완료 ({len(items)}건)")
    return dataset


# ──────────────────────────────────────────────
# 2) 에이전트 실행 Task
# ──────────────────────────────────────────────
agent = create_stock_agent()


@track(name="stock_agent_eval")
def evaluation_task(dataset_item: dict) -> dict:
    """Dataset 항목 하나에 대해 에이전트를 실행합니다."""
    user_input = dataset_item["input"]
    thread_id = str(uuid.uuid4())

    result = asyncio.run(
        _run_agent(user_input, thread_id)
    )
    return result


async def _run_agent(user_input: str, thread_id: str) -> dict:
    """에이전트를 실행하고 호출된 도구와 최종 응답을 반환합니다."""
    called_tools = []
    final_output = ""

    async for chunk in agent.astream(
        {"messages": [HumanMessage(content=user_input)]},
        config={"configurable": {"thread_id": thread_id}},
        stream_mode="updates",
    ):
        for step, event in chunk.items():
            if not event or step not in ("model", "tools"):
                continue

            messages = event.get("messages", [])
            if not messages:
                continue

            message = messages[0]

            if step == "model":
                tool_calls = getattr(message, "tool_calls", None)
                if tool_calls:
                    called_tools.extend([t["name"] for t in tool_calls])
                else:
                    content = getattr(message, "content", "")
                    if content:
                        final_output = content

            elif step == "tools":
                pass  # 도구 실행 결과는 에이전트가 내부적으로 처리

    return {
        "output": final_output,
        "called_tools": ",".join(sorted(set(called_tools))) if called_tools else "none",
    }


# ──────────────────────────────────────────────
# 3) 평가 메트릭 정의
# ──────────────────────────────────────────────
class ToolAccuracy(base_metric.BaseMetric):
    """에이전트가 올바른 도구를 호출했는지 평가합니다."""

    name = "tool_accuracy"

    def score(self, called_tools: str, expected_tool: str, **kwargs) -> score_result.ScoreResult:
        expected_set = set(expected_tool.split(","))
        actual_set = set(called_tools.split(","))

        if expected_set == actual_set:
            value = 1.0
            reason = f"정확히 일치: {expected_set}"
        elif expected_set & actual_set:
            # 부분 일치 — 기대 도구 중 호출된 비율
            value = len(expected_set & actual_set) / len(expected_set)
            reason = f"부분 일치: 기대={expected_set}, 실제={actual_set}"
        else:
            value = 0.0
            reason = f"불일치: 기대={expected_set}, 실제={actual_set}"

        return score_result.ScoreResult(
            name=self.name,
            value=value,
            reason=reason,
        )


class ResponseQuality(base_metric.BaseMetric):
    """응답이 비어있지 않고 충분한 길이인지 평가합니다."""

    name = "response_quality"

    def score(self, output: str, **kwargs) -> score_result.ScoreResult:
        if not output or len(output.strip()) == 0:
            return score_result.ScoreResult(
                name=self.name, value=0.0, reason="응답 없음"
            )
        elif len(output) < 20:
            return score_result.ScoreResult(
                name=self.name, value=0.5, reason=f"응답이 짧음 ({len(output)}자)"
            )
        else:
            return score_result.ScoreResult(
                name=self.name, value=1.0, reason=f"정상 응답 ({len(output)}자)"
            )


# ──────────────────────────────────────────────
# 4) 실험 실행
# ──────────────────────────────────────────────
def main():
    client = Opik()

    # 기존 Dataset 가져오기 (Opik UI에서 이미 업로드한 데이터셋 사용)
    dataset = client.get_dataset(name="doo_stock_agent_eval")

    # Experiment 실행
    result = evaluate(
        dataset=dataset,
        task=evaluation_task,
        scoring_metrics=[ToolAccuracy(), ResponseQuality()],
        experiment_name="doo-stock-agent-experiment",
        scoring_key_mapping={
            "called_tools": "called_tools",
            "expected_tool": "expected_tool",
            "output": "output",
        },
        task_threads=1,  # 외부 API 호출이 많으므로 순차 실행
    )

    # 결과 출력
    print("\n=== 실험 결과 ===")
    print(result)


if __name__ == "__main__":
    main()
