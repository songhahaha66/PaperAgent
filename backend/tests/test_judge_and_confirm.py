import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.graph.nodes.intent import classify_intent
from ai_system.graph.nodes.judge_node import judge_draft
from ai_system.graph.runner import run_pipeline
from ai_system.graph.state import PaperState
from ai_system.judge.base import Answer
from ai_system.judge.heuristic import infer_intent_kind
from ai_system.judge.jev import JevJudge, parse_jev_answers, serialize_question
from ai_system.judge.questions.intent import intent_questions
from ai_system.schemas.draft import Draft
from ai_system.schemas.paper_ir import Paragraph
from ai_system.schemas.template_spec import Slot


def test_infer_intent_kind_confirm_and_question():
    assert infer_intent_kind("确认采用当前稿") == "confirm"
    assert infer_intent_kind("现在写到哪一步了？") == "question"
    assert infer_intent_kind("修改第3节") == "edit"


def test_jev_serializes_and_parses_closed_questions():
    questions = intent_questions()
    payload = {key: serialize_question(question) for key, question in questions.items()}
    assert payload["kind"]["type"] == "choice"
    assert "confirm" in payload["kind"]["criteria"]
    answers = parse_jev_answers(
        {"answers": {"kind": {"choice": "question", "confidence": 0.91}}},
        questions,
    )
    assert answers["kind"].value == "question"
    assert answers["kind"].confidence == 0.91


def test_jev_uses_injected_request_and_falls_back():
    def request_fn(payload):
        assert payload["model"] == "jev-latest"
        assert "kind" in payload["questions"]
        return {"answers": {"kind": {"choice": "confirm", "confidence": 0.8}}}

    judge = JevJudge(api_key="test-key", request_fn=request_fn)
    answers = judge.ask({"message": "确认采用当前稿"}, intent_questions())
    assert answers["kind"].value == "confirm"

    fallback = JevJudge(api_key=None)
    answers = fallback.ask({"message": "确认采用当前稿"}, intent_questions())
    assert answers["kind"].value == "confirm"


def test_classify_intent_uses_judge_kind():
    class FakeJudge:
        def ask(self, state, questions):
            return {"kind": Answer(value="question", confidence=0.99)}

    state = classify_intent(
        PaperState(work_id="i1", user_message="继续写第三章"),
        judge=FakeJudge(),
    )
    assert state.intent.kind == "question"
    assert state.intent.reason == "judge"


def test_pending_confirmation_forces_confirm():
    state = PaperState(
        work_id="i2",
        user_message="确认采用当前稿",
        pending_confirmation=True,
    )
    state = classify_intent(state)
    assert state.intent.kind == "confirm"


def test_judge_draft_merges_model_answers():
    class FakeJudge:
        def ask(self, state, questions):
            assert "substantive" in questions
            return {
                "substantive": Answer(value=True, confidence=0.9),
                "example_left": Answer(value=False, confidence=0.9),
                "follows_rules": Answer(value="完全遵守", confidence=0.8),
            }

    draft = Draft(slot_id="slot.body", blocks=[Paragraph(text="足够长的实质性实验步骤说明，覆盖操作与结果。")])
    slot = Slot(id="slot.body", role="placeholder_fill", anchor_block="P001", title="实验步骤")
    judgement = judge_draft(draft, slot, judge=FakeJudge())
    assert judgement.passed is True
    assert judgement.reason == "judge"
    assert judgement.follows_rules == "完全遵守"


def test_judge_draft_keeps_deterministic_example_left():
    class FakeJudge:
        def ask(self, state, questions):
            return {
                "substantive": Answer(value=True, confidence=0.9),
                "example_left": Answer(value=False, confidence=0.2),
                "follows_rules": Answer(value="完全遵守", confidence=0.8),
            }

    draft = Draft(slot_id="slot.body", blocks=[Paragraph(text="这里还留着 print(\"hello\") 示例")])
    slot = Slot(
        id="slot.body",
        role="placeholder_fill",
        anchor_block="P001",
        title="实验步骤",
        examples=['print("hello")'],
    )
    judgement = judge_draft(draft, slot, judge=FakeJudge())
    assert judgement.example_left is True
    assert judgement.passed is False


def test_confirm_intent_skips_rewrite(tmp_path: Path):
    workspace = tmp_path / "confirm-work"
    workspace.mkdir()
    asyncio.run(
        run_pipeline(
            work_id="confirm-1",
            workspace_dir=str(workspace),
            user_message="写实验报告",
            output_mode="markdown",
        )
    )
    first = json.loads((workspace / ".system" / "paper_ir.json").read_text(encoding="utf-8"))
    summary = asyncio.run(
        run_pipeline(
            work_id="confirm-1",
            workspace_dir=str(workspace),
            user_message="确认采用当前稿",
            output_mode="markdown",
        )
    )
    second = json.loads((workspace / ".system" / "paper_ir.json").read_text(encoding="utf-8"))
    assert "已确认采用当前稿" in summary
    assert first["sections"] == second["sections"]
    assert first["revision"] == second["revision"]
