"""Read-only verification of historical long source-issue review receipts."""

from ai_system.research.domain import findings as research_findings
from ai_system.research import segmented_review as seg
from ai_system.research.review_cache import science_version
from ai_system.research.store import digest
from ai_system.research.models import ResearchConflict
from ai_system.graph.state import ClaimIssue
from ai_system.modules.writing.consistency import NOT_A_CONFLICT

POLICY='long-issue-edit-v1'


def known_findings(store,reports,context,sections):
    """Only the unchanged, already-known *other* writing findings may remain."""
    snapshot=store.snapshot();allowed=[];known=context['extra']['existing_issues']
    for finding in reports:
        if finding.research_issue:raise ResearchConflict('候选发现新的科研问题，不能以文稿修改放行')
        if not finding.contradicts or NOT_A_CONFLICT.search(finding.problem):continue
        match=None
        for issue in research_findings(snapshot):
            if issue['id'] not in known or digest(issue)!=known[issue['id']] or issue.get('section')!=finding.section:continue
            quote=(issue.get('paper_origin') or {}).get('quote')
            same_text=finding.problem==issue['message'] or issue['message']=='与结论表不一致：'+finding.problem
            same_quote=bool(quote and finding.quote==quote and sections[finding.section].count(quote)==1)
            unchanged=sections.get(finding.section)==snapshot['sections'].get(finding.section,{}).get('text')
            if same_quote or unchanged and same_text:match=issue['id'];break
        if match is None:raise ResearchConflict('候选完整覆盖核查仍有新的或源意见相关的问题，禁止应用：'+finding.problem)
        allowed.append(match)
    return sorted(set(allowed))


def verify(store,record):
    from .issue_edits import IssueAudit
    from services.chat_services.review_tasks import records
    try:
        context=record['context'];extra=context['extra'];proof=record['provenance']
        if record['context_hash']!=digest(context) or extra.get('policy')!=POLICY:return False
        rows={r['id']:r for r in records(store)};coverage=rows[proof['coverage_review_id']];target=rows[proof['target_review_id']]
        for row,expected in [(coverage,extra['coverage_context_hash']),(target,extra['target_context_hash'])]:
            if row['state']!='completed' or row['context_hash']!=expected or expected!=digest(row['context']) or row.get('result_hash')!=digest(row.get('result')):return False
            if science_version(row['context'])!=science_version(context) or row['context'].get('model_id')!=context.get('model_id'):return False
        if coverage['source']!='segmented-aggregate' or coverage['context']['section_hashes']!=context['section_hashes'] or not seg.verify_proof(store,coverage):return False
        if target['stage']!='issue_target' or target['context']['extra']['source_issue']!=extra['source_issue'] or target['context']['extra']['full_candidate_hash']!=digest(context['section_hashes']):return False
        verdict=IssueAudit.model_validate(target['result'])
        if verdict.issue_id!=extra['source_issue']['id'] or verdict.verdict=='resolved' and verdict.issues or record['result']!=target['result']:return False
        plan=coverage['context']['extra']['segmented_plan'];sections={k:''.join(b['text'] for b in plan['blocks'] if b['section']==k) for k in plan['sections']}
        allowed=known_findings(store,[ClaimIssue.model_validate(i) for i in coverage['result']['issues']],context,sections)
        return allowed==proof.get('retained_issue_ids',[])
    except (KeyError,ValueError,TypeError,ResearchConflict):return False
