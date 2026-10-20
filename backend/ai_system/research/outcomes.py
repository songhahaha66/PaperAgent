"""Read-only, task-oriented views of retained computation evidence.

This deliberately does not widen the publication registry or change the planner.
An available calculation is not an accepted scientific conclusion.
"""

from ai_system.research.domain import findings as research_findings
from ..graph.registry import numeric_series, REF_PATTERN
from .validation import report_fingerprint
from .progress import issue_next_step

CLOSED = {'resolved', 'dismissed', 'superseded'}


def check_state(task, attempt, issues):
    validation = attempt.get('validation') or {}
    if any(c['outcome'] == 'failed' for c in validation.get('checks', [])):
        return 'failed'
    if any(i.get('category') == 'service' for i in issues):
        return 'service_failed'
    if issues:
        return 'findings'
    if any(c['outcome'] != 'passed' for c in validation.get('checks', [])):
        return 'pending_check'
    if attempt and (attempt.get('revision') != task.get('revision') or attempt.get('attempt_id') != task.get('active_attempt')):
        return 'outdated'
    reviews = (attempt.get('result') or {}).get('reviews') or []
    report = (attempt.get('result') or {}).get('report') or {}
    revised = task.get('report_review') or {}
    if (revised.get('report_hash') == report_fingerprint(task.get('report') or {}) and
            revised.get('attempt_id') == task.get('active_attempt') and not revised.get('issues') and
            task['acceptance']=='accepted' and task['validity']=='current'):
        return 'passed'
    if (reviews and reviews[-1].get('report_hash') == report_fingerprint(report)
            and not reviews[-1].get('issues') and task.get('acceptance') == 'accepted'
            and task.get('validity') == 'current'):
        return 'passed'
    # Deterministic checks alone, or review_required=False, do not certify review.
    return 'unreviewed'


def result_view(item, safe_file):
    name = str(item.get('name') or '').strip()
    description = str(item.get('description') or '').strip()
    return {
        'id': str(item['id']), 'name': name or str(item['id']),
        'description': description, 'unit': str(item.get('unit') or ''),
        'kind': item.get('kind', 'number'), 'value': str(item.get('value', '')),
        'run_id': str(item.get('run_id') or ''),
        'figure_path': safe_file(item.get('path'), 'outputs/'),
        'series': numeric_series(item.get('value', '')) if item.get('kind') == 'series' else None,
    }


def claim_view(claim, items):
    by_id = {r['id']: r for r in items}
    def replace(match):
        ref = match[2]
        item = by_id.get(ref)
        if not item:
            return '[' + ref + ']'
        value = (str(item.get('value') or '') + ' ' + str(item.get('unit') or '')).strip()
        return (value if item.get('kind') != 'figure' else str(item.get('name') or ref)) + ' [' + ref + ']'
    return {**claim, 'statement': REF_PATTERN.sub(replace, claim.get('statement', ''))}


def outcomes(business, registry, safe_file, current_files=None):
    """Project persisted reports, never infer labels, units, decisions or sources."""
    if not business:
        return []
    views = []
    for key, task in business['tasks'].items():
        if task['retired']:
            continue
        attempts = [a for a in business['attempts'].values() if a['task_id'] == key]
        retained = []
        for attempt in reversed(attempts):
            aid = attempt['attempt_id']
            items = [e['payload'] for e in business['evidence'].values()
                     if e['producer_attempt'] == aid and e['active']]
            if items:
                retained.append((attempt, items))
        active = next((p for p in retained if p[0]['attempt_id'] == task.get('active_attempt')), None)
        selected = active or (retained[0] if retained else (attempts[-1] if attempts else {}, []))
        attempt, items = selected
        frozen_files = {p: h for p, h in attempt.get('input_refs', {}).items() if p.startswith(('data/', 'attachment/'))}
        inputs_match = current_files is None or frozen_files == {p: h for p, h in current_files.items() if p.startswith(('data/', 'attachment/'))}
        current = bool(active and attempt['revision'] == task['revision'] and task['validity'] == 'current' and inputs_match)
        issues = [i for i in research_findings(business) if i['task_id'] == key and i['status'] not in CLOSED]
        report = (task.get('report') if current else None) or (attempt.get('result') or {}).get('report') or {}
        design = (attempt.get('result') or {}).get('design') or task.get('design') or {}
        method = design.get('chosen') or {}
        sources = [{'path': path, 'hash': fingerprint,
                    'available': bool(safe_file(path))}
                   for path, fingerprint in attempt.get('input_refs', {}).items() if path.startswith(('data/', 'attachment/'))]
        views.append({
            'id': key, 'task_id': key, 'title': task.get('subproblem', {}).get('title') or task['objective'],
            'objective': task['objective'], 'revision': attempt.get('revision'),
            'attempt_id': attempt.get('attempt_id'), 'started_at':attempt.get('started_at'), 'computation': attempt.get('execution_status', task['execution_status']),
            'availability': ('current' if current else 'historical') if items else 'unavailable',
            'checking': check_state(task, attempt, [i for i in issues if i['category']!='writing' or task['kind']=='writing']) if inputs_match else 'outdated',
            'approach': report.get('approach') or '', 'summary': report.get('summary') or '',
            'method': {k: method[k] for k in ('name', 'idea', 'formulation', 'steps', 'validation') if method.get(k)} or None,
            'experiment':attempt.get('experiment') or task.get('experiment'),
            'results': [result_view(item, safe_file) for item in items[:500]],
            'claims': [claim_view(c['payload'], [e['payload'] for e in business['evidence'].values()]) for c in business['claims'].values()
                       if c['producer_attempt'] == attempt.get('attempt_id')],
            'issues': issues, 'sources': sources,
            'next_step': ' '.join(dict.fromkeys(issue_next_step(i,business) for i in issues)) or task.get('reason') or report.get('next_step') or None,
            'history': [{'attempt_id': a['attempt_id'], 'revision': a['revision'],
                         'execution_status': a['execution_status'], 'acceptance': a.get('acceptance'),
                         'results': [result_view(item, safe_file) for item in records[:500]]}
                        for a, records in retained if a['attempt_id'] != attempt.get('attempt_id')],
        })
    return views
