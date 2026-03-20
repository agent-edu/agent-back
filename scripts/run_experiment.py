"""
Opik Experiment 실행 스크립트

사용법:
    python -m scripts.run_experiment

Dataset CSV를 Opik에 등록하고, 에이전트를 실행하여 평가 메트릭을 측정합니다.
기존 규칙 기반 메트릭(ToolAccuracy, ResponseQuality)에 더해
LLM-as-Judge 메트릭(정확성, 완전성, 유용성)으로 답변 품질을 평가합니다.
"""

import asyncio
import csv
import json
import uuid

from openai import OpenAI
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
# 4) LLM-as-Judge 메트릭 정의
# ──────────────────────────────────────────────
JUDGE_PROMPT = """당신은 AI 주식 에이전트의 응답 품질을 평가하는 전문 평가자입니다.

아래 정보를 바탕으로 에이전트의 응답을 평가해주세요.

## 평가 대상
- 사용자 질문: {input}
- 에이전트 응답: {output}
- 호출된 도구: {called_tools}
- 카테고리: {category}

## 평가 기준
각 항목을 1~5점으로 채점하고, 반드시 아래 JSON 형식으로만 응답하세요.

1. accuracy (정확성): 응답에 포함된 정보가 정확한가? 잘못된 회사 정보, 틀린 숫자, 조회 실패를 정상처럼 답한 경우 낮은 점수.
2. completeness (완전성): 사용자 질문이 요구하는 범위를 충분히 커버했는가? "올해"라고 물었는데 과거 데이터를 주거나, 종합 분석을 요청했는데 일부만 답한 경우 낮은 점수.
3. helpfulness (유용성): 실제 투자자/사용자에게 유용한 형태로 정리했는가? 숫자 포맷, 구조화, 가독성, 투자 참고 가치를 기준으로 평가.

## 채점 기준 상세
- 5점: 완벽함. 정확하고 빠짐없이 유용하게 정리됨
- 4점: 대체로 좋으나 사소한 누락/아쉬운 점 있음
- 3점: 핵심은 있으나 중요한 부분이 빠지거나 부정확
- 2점: 일부만 답하거나 부정확한 내용 포함
- 1점: 질문에 답하지 못함, 조회 실패, 완전히 잘못된 정보

## 응답 형식 (반드시 이 JSON만 출력, 다른 텍스트 없이)
{{"accuracy": <1-5>, "completeness": <1-5>, "helpfulness": <1-5>, "reasoning": "<평가 사유 한국어 2~3문장>"}}"""


_openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)


def _call_judge(input: str, output: str, called_tools: str, category: str) -> dict:
    """GPT-4o 판사를 호출하여 응답 품질을 채점합니다."""
    prompt = JUDGE_PROMPT.format(
        input=input,
        output=output,
        called_tools=called_tools,
        category=category,
    )

    response = _openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=300,
    )

    content = response.choices[0].message.content.strip()

    # JSON 파싱 (코드블록으로 감싸져 있을 수 있음)
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()

    return json.loads(content)


# 판사 결과 캐시 (같은 입력에 대해 3개 메트릭이 각각 호출되므로 중복 방지)
_judge_cache: dict[str, dict] = {}


def _get_judge_result(input: str, output: str, called_tools: str, category: str) -> dict:
    """판사 결과를 캐시하여 동일 입력에 대한 중복 호출을 방지합니다."""
    cache_key = f"{input}||{output}"
    if cache_key not in _judge_cache:
        _judge_cache[cache_key] = _call_judge(input, output, called_tools, category)
    return _judge_cache[cache_key]


class LLMJudgeAccuracy(base_metric.BaseMetric):
    """LLM 판사가 응답의 정확성을 1~5점으로 평가합니다."""

    name = "llm_judge_accuracy"

    def score(self, output: str, input: str, called_tools: str, category: str, **kwargs) -> score_result.ScoreResult:
        try:
            result = _get_judge_result(input, output, called_tools, category)
            value = result["accuracy"] / 5.0  # 0~1 스케일로 정규화
            return score_result.ScoreResult(
                name=self.name,
                value=value,
                reason=f"정확성 {result['accuracy']}/5 — {result.get('reasoning', '')}",
            )
        except Exception as e:
            return score_result.ScoreResult(
                name=self.name, value=0.0, reason=f"판사 호출 실패: {e}"
            )


class LLMJudgeCompleteness(base_metric.BaseMetric):
    """LLM 판사가 응답의 완전성을 1~5점으로 평가합니다."""

    name = "llm_judge_completeness"

    def score(self, output: str, input: str, called_tools: str, category: str, **kwargs) -> score_result.ScoreResult:
        try:
            result = _get_judge_result(input, output, called_tools, category)
            value = result["completeness"] / 5.0
            return score_result.ScoreResult(
                name=self.name,
                value=value,
                reason=f"완전성 {result['completeness']}/5 — {result.get('reasoning', '')}",
            )
        except Exception as e:
            return score_result.ScoreResult(
                name=self.name, value=0.0, reason=f"판사 호출 실패: {e}"
            )


class LLMJudgeHelpfulness(base_metric.BaseMetric):
    """LLM 판사가 응답의 유용성을 1~5점으로 평가합니다."""

    name = "llm_judge_helpfulness"

    def score(self, output: str, input: str, called_tools: str, category: str, **kwargs) -> score_result.ScoreResult:
        try:
            result = _get_judge_result(input, output, called_tools, category)
            value = result["helpfulness"] / 5.0
            return score_result.ScoreResult(
                name=self.name,
                value=value,
                reason=f"유용성 {result['helpfulness']}/5 — {result.get('reasoning', '')}",
            )
        except Exception as e:
            return score_result.ScoreResult(
                name=self.name, value=0.0, reason=f"판사 호출 실패: {e}"
            )


# ──────────────────────────────────────────────
# 5) 실험 실행
# ──────────────────────────────────────────────
def main():
    client = Opik()

    # 기존 Dataset 가져오기 (Opik UI에서 이미 업로드한 데이터셋 사용)
    dataset = client.get_dataset(name="doo_stock_agent_eval")

    # 판사 캐시 초기화
    _judge_cache.clear()

    # Experiment 실행
    result = evaluate(
        dataset=dataset,
        task=evaluation_task,
        scoring_metrics=[
            # 기존 규칙 기반 메트릭
            ToolAccuracy(),
            ResponseQuality(),
            
            # LLM-as-Judge 메트릭
            LLMJudgeAccuracy(),
            LLMJudgeCompleteness(),
            LLMJudgeHelpfulness(),
        ],
        experiment_name="doo-stock-agent-experiment-with-judge",
        scoring_key_mapping={
            "called_tools": "called_tools",
            "expected_tool": "expected_tool",
            "output": "output",
            "input": "input",
            "category": "category",
        },
        task_threads=1,  # 외부 API 호출이 많으므로 순차 실행
    )

    # 결과 출력
    print("\n=== 실험 결과 ===")
    print(result)


if __name__ == "__main__":
    main()
