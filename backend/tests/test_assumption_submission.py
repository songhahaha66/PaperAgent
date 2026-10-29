"""Assumptions belong to the report, without a second contract/replay phase."""
from ai_system.graph.state import SolveReport


def test_report_keeps_plain_assumptions_and_actual_validation():
    report=SolveReport(summary="购电费用为18840.67元",approach="线性规划",
        assumptions=["不计自放电"],validation="独立复算日初日末储电量差为0")
    assert report.assumptions == ["不计自放电"]
    assert 'requirement_evidence' not in report.model_dump()
    assert 'checks' not in SolveReport.model_json_schema()['properties']
