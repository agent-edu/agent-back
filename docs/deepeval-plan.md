# DeepEval 도입 계획서

## 1. 배경: 왜 DeepEval을 추가로 도입하는가

| | LLM-as-Judge 직접 구현 | DeepEval 도입 |
|---|---|---|
| **방식** | GPT-4o 판사 프롬프트를 직접 작성하고 Opik 메트릭으로 구현 | DeepEval 라이브러리의 내장 메트릭 활용 |
| **평가 대상** | 답변의 정확성/완전성/유용성 (응답 품질) | 에이전트의 실행 흐름, 도구 선택, 인자 정확성 (행동 품질) |
| **관점** | "답변이 좋은가?" | "에이전트가 올바르게 행동했는가?" |

### 왜 둘 다 필요한가

LLM-as-Judge는 **최종 답변**만 평가한다. 하지만 에이전트는 답변 전에 여러 단계를 거친다:

```
질문 → [도구 선택] → [인자 생성] → [도구 실행] → [결과 해석] → 답변
         ↑              ↑              ↑              ↑           ↑
     목요일 평가     목요일 평가     목요일 평가     목요일 평가   수요일 평가
```

- 수요일: 마지막 "답변"만 채점 → 답변이 좋으면 중간 과정은 모름
- 목요일: 중간 과정(도구 선택, 인자, 실행 효율)까지 평가 → **왜 잘/못했는지** 원인 파악 가능

---

## 2. DeepEval이란

**DeepEval**은 LLM 애플리케이션 전용 평가 프레임워크로, pytest처럼 LLM 출력을 테스트할 수 있다.
50개 이상의 내장 메트릭을 제공하며, LangChain과 직접 연동 가능하다.

### 핵심 특징

- **에이전트 전용 메트릭**: 도구 선택, 인자 정확성, 실행 효율성 등 에이전트 특화 평가
- **G-Eval**: 커스텀 기준으로 LLM-as-Judge 평가 (수요일에 직접 만든 것의 라이브러리 버전)
- **@observe 트레이싱**: 에이전트 실행 흐름을 자동으로 추적
- **LangChain 연동**: 콜백 핸들러로 쉽게 통합

---

## 3. 사용할 DeepEval 메트릭

### 3-1. ToolCorrectnessMetric (도구 선택 정확성)

기존 Opik ToolAccuracy와 유사하지만, DeepEval 버전은 **순서 고려**, **정확 일치 옵션** 등 더 세밀한 설정이 가능하다.

```python
from deepeval.metrics import ToolCorrectnessMetric

metric = ToolCorrectnessMetric(
    expected_tools=["get_stock_price"],      # 기대하는 도구 목록
    should_consider_ordering=False,           # 순서 무시
    should_exact_match=True,                  # 정확히 일치해야 하는지
)
```

**기존 Opik ToolAccuracy와의 차이:**

| | Opik ToolAccuracy (기존) | DeepEval ToolCorrectnessMetric |
|---|---|---|
| 순서 고려 | 불가 | `should_consider_ordering` 옵션 |
| 정확 일치 | 부분 일치 시 비율 계산 | `should_exact_match` 옵션 |
| 불필요한 도구 감지 | 불가 | 기대 목록에 없는 도구 호출 시 감점 |

### 3-2. G-Eval (커스텀 LLM 평가)

수요일에 직접 프롬프트로 만든 LLM-as-Judge를 **G-Eval 메트릭**으로 더 표준화된 방식으로 구현할 수 있다.

```python
from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCaseParams

correctness_metric = GEval(
    name="답변 정확성",
    criteria="주어진 질문에 대해 에이전트가 정확한 정보를 제공했는지 평가",
    evaluation_params=[
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
    ],
    threshold=0.5,
)
```

**G-Eval의 장점:**
- 연구 기반(논문 backed)의 표준화된 LLM-as-Judge 방식
- 점수 정규화 및 일관성이 직접 구현보다 높음
- `threshold` 설정으로 pass/fail 자동 판정

### 3-3. TaskCompletionMetric (작업 완수도)

에이전트가 사용자의 **전체 요청을 완수했는지** 실행 트레이스 기반으로 평가한다.

```python
from deepeval.metrics import TaskCompletionMetric

metric = TaskCompletionMetric(
    threshold=0.5,
    model="gpt-4o",
)
```

**수요일 completeness 메트릭과의 차이:**
- 수요일: 최종 답변 텍스트만 보고 "빠진 정보가 있는가" 판단
- 목요일: 실행 트레이스(도구 호출 순서, 중간 결과)까지 보고 "작업을 완수했는가" 판단

---

## 4. 구현 계획

### 4-1. 설치

```bash
uv add deepeval
```

### 4-2. 프로젝트 구조

```
scripts/
├── run_experiment.py          # 기존 Opik 실험 (수요일에 LLM Judge 추가)
└── run_deepeval_experiment.py  # 새로 만드는 DeepEval 실험 (목요일)
```

### 4-3. DeepEval 실험 스크립트 구조

```python
"""
DeepEval 기반 에이전트 평가 스크립트

기존 Opik 실험과 동일한 데이터셋(36개)을 사용하되,
DeepEval의 에이전트 전용 메트릭으로 실행 흐름과 도구 활용을 평가한다.
"""
import csv
from deepeval import evaluate
from deepeval.test_case import LLMTestCase
from deepeval.metrics import GEval, ToolCorrectnessMetric

# 1) 데이터셋 로드 (동일한 CSV 사용)
test_cases = []
with open("datasets/stock_agent_eval.csv") as f:
    for row in csv.DictReader(f):
        # 에이전트 실행하여 결과 수집
        result = run_agent(row["input"])

        test_case = LLMTestCase(
            input=row["input"],
            actual_output=result["output"],
            expected_output=None,
            tools_called=result["called_tools_list"],
            expected_tools=row["expected_tool"].split(","),
        )
        test_cases.append(test_case)

# 2) 메트릭 정의
metrics = [
    ToolCorrectnessMetric(should_exact_match=False),
    GEval(
        name="답변 정확성",
        criteria="주식 관련 질문에 대해 정확한 데이터를 포함하여 답변했는가",
        evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
    ),
    GEval(
        name="답변 유용성",
        criteria="투자자에게 유용한 형태로 정보를 정리하여 제공했는가",
        evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
    ),
]

# 3) 평가 실행
evaluate(test_cases=test_cases, metrics=metrics)
```

### 4-4. @observe 트레이싱 연동

에이전트 내부 실행 흐름을 DeepEval이 추적하도록 데코레이터를 추가한다.

```python
from deepeval.tracing import observe, TraceType

@observe(type=TraceType.AGENT)
async def run_stock_agent(query: str):
    """에이전트 실행 — DeepEval이 내부 도구 호출을 자동 추적"""
    ...

@observe(type=TraceType.TOOL)
async def get_stock_price(query: str):
    """도구 실행 — 개별 도구 호출도 추적"""
    ...
```

---

## 5. 기대 효과

### 수요일 + 목요일 통합 평가 체계

```
┌─────────────────────────────────────────────────────┐
│                    전체 평가 체계                       │
├──────────────────┬──────────────────────────────────┤
│  기존 (Opik)      │  ToolAccuracy, ResponseQuality   │
│  수요일 (Opik)    │  LLM Judge: 정확성/완전성/유용성    │
│  목요일 (DeepEval) │  ToolCorrectness, G-Eval,       │
│                   │  TaskCompletion                  │
└──────────────────┴──────────────────────────────────┘
```

### 발표에서 보여줄 수 있는 비교 포인트

| 비교 축 | 내용 |
|---------|------|
| **직접 구현 vs 라이브러리** | 수요일에 판사 프롬프트를 직접 작성 → 목요일에 G-Eval로 같은 걸 더 간단하게 구현 → 점수 비교 |
| **응답 평가 vs 행동 평가** | 수요일은 "답변이 좋은가", 목요일은 "과정이 올바른가" → 둘이 불일치하는 케이스 분석 |
| **Opik vs DeepEval** | 같은 데이터셋, 같은 에이전트에 두 프레임워크를 적용 → 장단점 비교 |

### 발견할 수 있는 인사이트 예시

```
케이스: "카카오 투자 정보 종합적으로 알려줘"

수요일 결과:
  - LLM Judge 정확성: 4/5 (답변 자체는 정확)
  - LLM Judge 완전성: 2/5 (뉴스 누락)

목요일 결과:
  - ToolCorrectness: 0.67 (3개 중 2개만 호출)
  - TaskCompletion: 0.6 (작업 미완수)

→ 원인 진단: 에이전트가 naver_search를 호출하지 않아서 뉴스 정보가 빠짐
→ 개선: 프롬프트에 "종합 분석 시 반드시 3개 도구 모두 사용" 지시 추가
```

---

## 6. 주의 사항

1. **의존성 충돌**: Opik과 DeepEval을 동시에 쓸 때 버전 충돌 가능 → 별도 스크립트로 분리
2. **비용**: G-Eval도 내부적으로 GPT-4o를 호출하므로 수요일과 합치면 API 호출 2배
3. **트레이싱 오버헤드**: @observe 데코레이터가 프로덕션 코드에 들어가면 성능 영향 → 평가 시에만 사용
4. **학습 비용**: DeepEval API를 새로 익혀야 하므로 목요일 오전에 공식 문서 먼저 읽기

---

## 7. 작업 순서

| 단계 | 작업 | 산출물 |
|------|------|--------|
| 1 | DeepEval 설치 및 기본 예제 실행 | `uv add deepeval`, 동작 확인 |
| 2 | 기존 에이전트에 @observe 트레이싱 추가 | 도구 호출 트레이스 수집 |
| 3 | ToolCorrectnessMetric 적용 | 기존 ToolAccuracy와 결과 비교 |
| 4 | G-Eval로 답변 정확성/유용성 평가 | 수요일 LLM Judge와 결과 비교 |
| 5 | 36개 데이터셋 전체 실험 실행 | DeepEval 실험 결과 |
| 6 | Opik 결과 vs DeepEval 결과 비교 분석 | 비교 리포트 (발표 자료) |

---

## 참고 자료

- [DeepEval 공식 문서 - Getting Started](https://deepeval.com/docs/getting-started)
- [DeepEval Tool Correctness 메트릭](https://deepeval.com/docs/metrics-tool-correctness)
- [DeepEval G-Eval 메트릭](https://deepeval.com/docs/metrics-llm-evals)
- [DeepEval AI Agent 평가 가이드](https://deepeval.com/guides/guides-ai-agent-evaluation)
- [DeepEval Agent 평가 메트릭](https://deepeval.com/guides/guides-ai-agent-evaluation-metrics)
- [G-Eval 설명 블로그](https://www.confident-ai.com/blog/g-eval-the-definitive-guide)
