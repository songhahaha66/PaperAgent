"""Writers receive actual references and adopted premise versions without padding requirements."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.graph.context import GraphContext
from ai_system.graph.nodes.write import section_request, STYLE
from test_paper_graph import FakeModel, _analysis
from test_report_submission import seed


def test_writing_context_has_only_real_scoped_references_and_no_phantom_figure(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel())
    own = seed(ctx, [], name='answer')
    unrelated = seed(ctx, [], sid='Q2', name='unrelated', value='12')
    state = {'analysis': _analysis().model_dump(), 'solutions': {}, 'designs': {}, 'sections': {}}
    request = section_request('solve:Q1', '求解', state, ctx)
    assert '{{R:' + own.id + '}}' in request
    assert '{{R:' + unrelated.id + '}}' not in request
    assert '本节没有可用图表' in request
    assert '{{F:F1}}' not in STYLE + request
    run = ctx.fs.create_run()
    ctx.fs.save_run_stdout(run, 'figure ready')
    ctx.fs.finish_run(run, 'success')
    (ctx.fs.run_dir(run) / 'artifacts/plot.png').write_bytes(b'png')
    figure = ctx.registry.record_figure('Q1', run, 'plot.png', '实际图表')
    request = section_request('solve:Q1', '求解', state, ctx)
    assert '{{F:' + figure.id + '}}' in request and '本节没有可用图表' not in request


def test_assumptions_section_uses_only_active_premises_and_never_pads_given_facts(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel())
    analysis = _analysis().model_dump()
    analysis['assumptions'] = [
        {'id': 'A1', 'revision': 2, 'statement': '当前已采纳前提', 'task_ids': ['Q1'], 'status': 'active'},
        {'id': 'A2', 'revision': 1, 'statement': '已被反证前提', 'task_ids': ['Q1'], 'status': 'refuted'},
        {'id': 'A3', 'revision': 1, 'statement': '已停用前提', 'task_ids': ['Q1'], 'status': 'retired'}]
    state = {'analysis': analysis}
    request = section_request('assumptions', '假设', state, ctx)
    assert 'A1 v2：当前已采纳前提' in request
    assert '已被反证前提' not in request and '已停用前提' not in request
    assert '5~8' not in request
    state['designs'] = {'Q1': {'chosen': {'name': '原方案', 'idea': '建模', 'formulation': 'x', 'validation': '核对',
                                         'assumptions': [{'statement': '原方案中过期的前提'}]}}}
    for key in ('analysis', 'solve:Q1'):
        state['solutions'] = {}
        request = section_request(key, '正文', state, ctx)
        assert '当前已采纳前提' in request
        assert '原方案中过期的前提' not in request
    analysis['assumptions'] = []
    request = section_request('assumptions', '假设', state, ctx)
    assert '没有假设时如实说明' in request


def test_semantic_audit_preserves_actual_references_and_includes_auxiliary_evidence(tmp_path, monkeypatch):
    import asyncio
    from ai_system.graph.nodes import consistency
    from ai_system.graph.state import ClaimAudit
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel())
    objective = seed(ctx, [], name='objective', value='13')
    decision = seed(ctx, [], name='x_opt', value='3')
    ctx.registry.set_claims('Q1', [{'statement': f'目标为 {{{{R:{objective.id}}}}}', 'strength': '显著'}])
    seen = []
    async def audit(llm, schema, messages):
        seen.extend(messages)
        return ClaimAudit()
    monkeypatch.setattr(consistency, 'call_structured', audit)
    state = {'analysis': _analysis().model_dump(), 'sections': {'sensitivity': f'决策为 {{{{R:{decision.id}}}}}，目标为 {{{{R:{objective.id}}}}}。'}}
    assert asyncio.run(consistency.audit_sections(state, ctx)) == []
    prompt = seen[-1].content
    body = prompt.split('# 待检查的章节')[1]
    assert '{{R:' + decision.id + '}}' in body
    assert f'| {{{{R:{decision.id}}}}} | Q1 | x_opt | 3 |' in prompt
    assert '正文写引用，不要照抄数值' not in prompt
    assert '不是证据全集' in seen[0].content
