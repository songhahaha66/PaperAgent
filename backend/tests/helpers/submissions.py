"""Shared builders extracted from test_report_submission."""
from ai_system.sandbox.evidence import seal_run


def branch(sid="Q1", *, kind="compute", figure=False):
    return {
        "source": "计算 1、2、3 的整数和，并保留代码与结果。",
        "subproblem": {"id": sid, "title": "整数求和", "question": "计算整数和" + ("，绘制结果图" if figure else ""),
                       "kind": kind, "depends_on": [], "deliverables": []},
        "data_profile": "无需外部数据", "dependencies": {}, "analysis": {},
        "design": {"chosen": {"name": "直接求和", "idea": "执行求和", "formulation": "", "steps": ["计算并登记"],
                               "method_keys": [], "assumptions": [], "validation": "固定指标检查"}},
        "research_contract": {
            "requirements": [] if kind == "verify" else [{"id": "RQ1"}], "assumptions": [],
            "checks": [{"owner_kind": "requirement", "owner_id": "RQ1", "owner_revision": 1,
                        "check": {"id": "answer_finite", "description": "结果有限", "left": {"task_id": "Q1", "name": "answer"},
                                  "operator": "finite", "expected": None, "right": None, "tolerance": 0}}],
        },
    }

def seed(ctx, runs, sid="Q1", name="answer", value="6"):
    rid = ctx.fs.create_run()
    ctx.fs.save_run_input(rid, "print(sum([1, 2, 3]))")
    ctx.fs.save_run_stdout(rid, value)
    ctx.fs.finish_run(rid, "success"); seal_run(ctx.fs, rid)
    runs.append({"run_id": rid, "ok": True, "code": "print(sum([1, 2, 3]))"})
    return ctx.registry.record_value(sid, name, value, rid)

def report(ref, *, requirement="RQ1"):
    return {"summary": f"已完成整数求和，结果为 {{{{R:{ref}}}}}", "approach": "Python直接求和",
            "key_findings": [{"statement": f"求和结果为 {{{{R:{ref}}}}}", "strength": "显著"}],
            "requirement_evidence": [{"requirement_id": requirement, "evidence_refs": [ref]}] if requirement else []}
