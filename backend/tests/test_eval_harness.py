import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.judge import Answers, Choice, JevJudge, LLMJudge, Noul, Score, ScoreAnswer
from evals.metrics import code_runs, number_traceability, section_coverage
from evals.mmbench import build_task_prompt, load_problem, stage_datasets
from evals.rubric import DIMENSIONS, MAX_PAPER_CHARS, level_to_ten, score_paper, select_sections
from evals.runner import score_problem, summarize


PAPER = """# 摘要
我们建立了动量模型，预测准确率为 0.873，AUC 达到 91.25%。

# 一、问题重述
题目给出 2023 年温网数据。

# 二、问题分析
三个子问题。

# 三、模型假设
假设比赛独立。

# 四、符号说明
| 符号 | 含义 |

# 五、模型的建立与求解
逻辑回归系数为 1.732，残差 0.0451。编造的结果 12.345。

```python
print(3.1415)
```

# 六、灵敏度分析
参数扰动 10% 时结果稳定。

# 七、模型评价
优点与缺点。
"""


def _make_bench(tmp_path: Path) -> Path:
    bench = tmp_path / "MMBench"
    (bench / "problem").mkdir(parents=True)
    (bench / "dataset" / "2024_C").mkdir(parents=True)
    (bench / "dataset" / "2024_C" / "matches.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (bench / "problem" / "2024_C.json").write_text(json.dumps({
        "background": "Wimbledon 2023 final. Serve win rate 0.65.",
        "problem_requirement": "Model momentum.",
        "dataset_path": ["matches.csv"],
        "dataset_description": {"matches": "point by point data"},
        "variable_description": {"a": "set number"},
        "addendum": "",
    }), encoding="utf-8")
    return bench


def _make_workspace(root: Path) -> Path:
    ws = root / "workspace"
    run = ws / "runs" / "run_1"
    run.mkdir(parents=True)
    (run / "stdout.log").write_text("accuracy=0.87312\nauc 0.912512\ncoef [1.73205 0.04511]\n", encoding="utf-8")
    (ws / "manifest.json").write_text(json.dumps({"runs": {
        "run_1": {"status": "success"},
        "run_2": {"status": "error"},
        "run_3": {"status": "success"},
    }}), encoding="utf-8")
    (ws / "paper.md").write_text(PAPER, encoding="utf-8")
    return ws


class FixedJudge:
    """每个 Score 都回答同一个等级，用来检验分数换算"""

    name = "fixed"

    def __init__(self, level: float, confidence: float = 0.9):
        self.level = level
        self.confidence = confidence
        self.states = []

    async def ask(self, state, questions):
        self.states.append(state)
        return Answers(
            scores={k: ScoreAnswer(score=self.level, probabilities={}, confidence=self.confidence) for k in questions},
            judge="fixed",
            input_tokens=100,
        )


def test_load_and_stage_problem(tmp_path):
    bench = _make_bench(tmp_path)
    problem = load_problem(bench, "2024_C")
    staged = stage_datasets(problem, tmp_path / "ws")

    assert staged == ["data/matches.csv"]
    assert (tmp_path / "ws" / "data" / "matches.csv").exists()
    prompt = build_task_prompt(problem, staged)
    assert "Model momentum." in prompt
    assert "data/matches.csv" in prompt
    assert "point by point data" in prompt


def test_build_task_prompt_accepts_grouped_descriptions(tmp_path):
    bench = _make_bench(tmp_path)
    problem = load_problem(bench, "2024_C")
    problem.dataset_description = "one csv file"
    problem.variable_description = [{"Name": "athlete"}, [{"Year": "olympic year"}]]

    prompt = build_task_prompt(problem, ["data/matches.csv"])
    assert "- one csv file" in prompt
    assert "- Name: athlete" in prompt
    assert "- Year: olympic year" in prompt


def test_stage_datasets_copies_directories(tmp_path):
    bench = _make_bench(tmp_path)
    folder = bench / "dataset" / "2024_C" / "images"
    folder.mkdir()
    (folder / "a.txt").write_text("x", encoding="utf-8")
    data = json.loads((bench / "problem" / "2024_C.json").read_text(encoding="utf-8"))
    data["dataset_path"].append("images")
    (bench / "problem" / "2024_C.json").write_text(json.dumps(data), encoding="utf-8")

    staged = stage_datasets(load_problem(bench, "2024_C"), tmp_path / "ws")
    assert staged == ["data/matches.csv", "data/images"]
    assert (tmp_path / "ws" / "data" / "images" / "a.txt").exists()


def test_load_problem_reports_missing_dataset(tmp_path):
    bench = _make_bench(tmp_path)
    (bench / "dataset" / "2024_C" / "matches.csv").unlink()
    with pytest.raises(FileNotFoundError):
        load_problem(bench, "2024_C")


def test_code_runs_counts_statuses(tmp_path):
    stats = code_runs(_make_workspace(tmp_path))
    assert stats == {"total": 3, "success": 2, "error": 1, "success_rate": 0.667}


def test_code_runs_without_manifest(tmp_path):
    assert code_runs(tmp_path)["success_rate"] is None


def test_section_coverage_finds_standard_sections():
    result = section_coverage(PAPER)
    assert result["missing"] == []
    assert result["coverage"] == 1.0

    partial = section_coverage("# 摘要\n\n# 模型建立\n")
    assert "灵敏度分析" in partial["missing"]
    assert partial["found"] == ["摘要", "模型建立与求解"]


def test_number_traceability_separates_traced_and_made_up(tmp_path):
    ws = _make_workspace(tmp_path)
    result = number_traceability(PAPER, ws, problem_text="Serve win rate 0.65.")

    # 0.873 / 91.25% / 1.732 / 0.0451 能在 stdout 中找到；12.345 是编造的；代码块里的 3.1415 不计
    assert result["checked"] == 5
    assert result["traced"] == 4
    assert result["untraced_examples"] == ["12.345"]


def test_number_traceability_excludes_numbers_from_problem(tmp_path):
    result = number_traceability("给定发球胜率 0.65。", tmp_path, problem_text="Serve win rate 0.65.")
    assert result == {"checked": 0, "traced": 0, "rate": None, "untraced_examples": []}


def test_select_sections_filters_and_truncates():
    long_paper = PAPER.replace("参数扰动 10% 时结果稳定。", "参数扰动结果稳定。" * 200)
    text = select_sections(long_paper, ("灵敏度",))
    assert text.startswith("# 六、灵敏度分析")
    assert "符号说明" not in text

    # 相关章节太短时退回整篇
    assert select_sections(PAPER, ("灵敏度",)) == PAPER

    huge = "# 灵敏度分析\n" + "很" * (MAX_PAPER_CHARS + 100)
    assert len(select_sections(huge, ("灵敏度",))) < MAX_PAPER_CHARS + 100


def test_level_to_ten_maps_band_midpoints():
    assert level_to_ten(0) == 1.5
    assert level_to_ten(4) == 9.5
    assert level_to_ten(2.5) == 6.5


def test_score_paper_aggregates_dimensions():
    judge = FixedJudge(level=3.0, confidence=0.3)
    result = asyncio.run(score_paper(judge, "bg", "req", PAPER))

    assert len(judge.states) == len(DIMENSIONS)
    assert all(state["problem_requirement"] == "req" for state in judge.states)
    assert set(result.dimensions) == {d.key for d in DIMENSIONS}
    assert result.overall == 7.5
    assert result.input_tokens == 100 * len(DIMENSIONS)
    # 置信度 0.3 低于阈值，全部子项都需要复核
    assert len(result.needs_review) == 8


def test_score_problem_and_summary(tmp_path):
    bench = _make_bench(tmp_path)
    problem = load_problem(bench, "2024_C")
    out = tmp_path / "out"
    _make_workspace(out / "2024_C")
    (out / "2024_C" / "run.json").write_text(json.dumps({
        "paper_exists": True, "error": None, "seconds": 12.0, "usage": {"total_tokens": 5000},
    }), encoding="utf-8")

    score = asyncio.run(score_problem(problem, out, FixedJudge(level=2.0)))
    assert score["judge"]["overall"] == 5.5
    assert score["code_runs"]["success_rate"] == 0.667

    summary = summarize(out, ["2024_C"])
    assert summary["mean"]["overall"] == 5.5
    assert summary["mean"]["total_tokens"] == 5000
    assert "| 2024_C | 5.50 |" in (out / "summary.md").read_text(encoding="utf-8")


def test_score_problem_without_judge_keeps_deterministic_metrics(tmp_path):
    bench = _make_bench(tmp_path)
    problem = load_problem(bench, "2024_C")
    out = tmp_path / "out"
    _make_workspace(out / "2024_C")

    score = asyncio.run(score_problem(problem, out, judge=None))
    assert score["judge"] is None
    assert score["sections"]["coverage"] == 1.0


def test_jev_judge_maps_sdk_response():
    class FakeClient:
        def __init__(self):
            self.calls = []

        async def system_one(self, state, questions):
            self.calls.append((state, questions))
            return SimpleNamespace(
                model="jev-1.14.0",
                usage=SimpleNamespace(input_tokens=321),
                nouls={"ok": SimpleNamespace(noul=0.8)},
                choices={"kind": SimpleNamespace(choice="b", probabilities={"a": 0.1, "b": 0.9}, confidence=0.7)},
                scores={"q": SimpleNamespace(score=2.6, probabilities={"0": 0.0, "1": 0.0, "2": 0.4, "3": 0.6}, confidence=0.55)},
            )

    client = FakeClient()
    judge = JevJudge(model="jev-1.14.0", client=client)
    answers = asyncio.run(judge.ask({"x": 1}, {
        "ok": Noul(instructions="ok?"),
        "kind": Choice(instructions="which?", criteria={"a": None, "b": "second"}),
        "q": Score(instructions="how good?", criteria=["bad", "meh", "good", "great"]),
    }))

    sent = client.calls[0][1]
    assert sent["kind"].criteria == {"a": None, "b": "second"}
    assert sent["q"].criteria == ["bad", "meh", "good", "great"]
    assert answers.judge == "jev:jev-1.14.0"
    assert answers.input_tokens == 321
    assert answers.nouls["ok"].noul == 0.8
    assert answers.choices["kind"].choice == "b"
    assert answers.scores["q"].probabilities == {0: 0.0, 1: 0.0, 2: 0.4, 3: 0.6}


def test_llm_judge_uses_structured_output():
    class FakeStructured:
        def __init__(self, schema):
            self.schema = schema

        async def ainvoke(self, prompt):
            fields = self.schema.model_fields
            if "level" in fields:
                return self.schema(reason="清楚", level=3)
            if "choice" in fields:
                return self.schema(reason="r", choice="b")
            return self.schema(reason="r", answer=True)

    class FakeLLM:
        model_name = "fake-model"

        def with_structured_output(self, schema):
            return FakeStructured(schema)

    answers = asyncio.run(LLMJudge(FakeLLM()).ask("材料", {
        "ok": Noul(instructions="ok?"),
        "kind": Choice(instructions="which?", criteria={"a": None, "b": None}),
        "q": Score(instructions="how good?", criteria=["0", "1", "2", "3", "4"]),
    }))

    assert answers.judge == "llm:fake-model"
    assert answers.nouls["ok"].noul == 1.0
    assert answers.choices["kind"].probabilities == {"a": 0.0, "b": 1.0}
    assert answers.choices["kind"].confidence is None
    assert answers.scores["q"].score == 3.0
    assert answers.scores["q"].reason == "清楚"
