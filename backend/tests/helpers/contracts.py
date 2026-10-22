"""Shared builders extracted from test_research_p2."""
from pathlib import Path
from ai_system.research.store import ResearchStore
from ai_system.research.models import Assumption, DomainCheck, Requirement, ResultSelector, WorkerResult
from ai_system.graph.state import Subproblem
from helpers.graph import ProblemAnalysis


TASK = "Estimate demand; optimize allocation with capacity <= 10; also compute an independent statistic."

def problem():
    return ProblemAnalysis(title="需求与容量约束研究", restatement=TASK, overall_analysis="estimate, validate, allocate",
        subproblems=[Subproblem(id="Q1", title="需求估计", question="Estimate demand"),
                     Subproblem(id="Q2", title="容量配置", question="Validate demand and allocate", depends_on=["Q1"]),
                     Subproblem(id="Q3", title="独立统计", question="Independent statistic")],
        requirements=[Requirement(id="RQ1", statement="estimate demand", source_quote="Estimate demand", kind="goal", task_ids=["Q1"]),
                      Requirement(id="RQ2", statement="respect capacity", source_quote="capacity <= 10", kind="constraint", task_ids=["Q2"],
                          checks=[DomainCheck(id="capacity", description="容量不超过上限", left=ResultSelector(task_id="Q2", name="allocation"), operator="le", expected=10)]),
                      Requirement(id="RQ3", statement="independent statistic", source_quote="independent statistic", kind="goal", task_ids=["Q3"])],
        assumptions=[Assumption(id="A1", statement="Mean demand is representative", task_ids=["Q1", "Q2"],
            checks=[DomainCheck(id="representative", description="估计应与独立校核一致", left=ResultSelector(task_id="Q2", name="residual"), operator="le", expected=0)])])

def store_for(tmp_path):
    store = ResearchStore(tmp_path, "p2")
    store.initialize(TASK, problem().model_dump())
    return store

def submit(store, key, values, *, order=None, issues=()):
    order = order or store.claim(key)
    results = [{"id": store.allocate_ref("R"), "subproblem": key, "kind": "number", "name": name,
                "value": str(value), "run_id": "run_test"} for name, value in values.items()]
    result = WorkerResult(order=order, execution_status="succeeded", acceptance="accepted",
        report={"status": "completed", "summary": "completed", "requirement_evidence": [
            {"requirement_id": rid, "evidence_refs": [results[0]["id"]]} for rid in store.snapshot()["experiments"][key]["requirement_ids"]]},
        results=results, issues=list(issues))
    return store.commit(result), results

async def resume_saved_contract(agent):
    """Counterevidence fixtures import a saved full contract at the recovery boundary.

    New-run lightweight decisions are exercised separately; these cases retain
    their original assumption IDs to verify repair and independent counterchecks.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from ai_system.graph.build import compile_graph
    if agent._load_thread() is None:
        thread='saved-contract'
        ResearchStore(agent.workspace_dir,thread).initialize(TASK,problem().model_dump())
        agent._save_thread(thread,'paused')
        async with AsyncSqliteSaver.from_conn_string(str(Path(agent.workspace_dir)/'.system/graph.sqlite')) as saver:
            graph=compile_graph(saver)
            await graph.aupdate_state({'configurable':{'thread_id':thread}},
                                     {'research_blocked':False},as_node='solve_join')
    return await agent.resume_failed()
