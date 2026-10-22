"""Transactional, idempotent cutover. Uninterpretable history stays verbatim.

Only this module reads the retired issue collection and comparison table names.
Archives are provenance, never input to the active scheduler or publication gate.
"""
import json
from contextlib import closing

from .domain import FAULT_CATEGORIES, SCHEMA_VERSION

MIGRATION_VERSION = 8


def migrate_runtime(db, root):
    statements = (
        'CREATE TABLE IF NOT EXISTS failure_guards(id TEXT PRIMARY KEY,payload TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS runtime_stop(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS runtime_policy(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS request_budgets(research_id TEXT PRIMARY KEY,payload TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS model_requests(research_id TEXT NOT NULL,sequence INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(research_id,sequence))',
    )
    for statement in statements:
        db.execute(statement)
    row = db.execute('SELECT payload FROM runtime_policy WHERE id=1').fetchone()
    frozen = db.execute("SELECT payload FROM request_budgets WHERE research_id='workspace-model-budget'").fetchone()
    traces = []
    for name in ('llm_calls.json', 'editor_llm_calls.json'):
        path = root / '.system' / name
        if path.is_file() and path.resolve().is_relative_to(root):
            raw = path.read_bytes()
            db.execute('INSERT OR IGNORE INTO legacy_records VALUES (?,?)', (f'.system/{name}', raw))
            try:
                traces.extend(json.loads(raw).get('calls', []))
            except (ValueError, TypeError, AttributeError):
                pass  # Raw bytes are preserved; no invented usage.
    old = bool(frozen or traces or (root / '.system/graph_thread.json').exists() or (root / '.system/graph.sqlite').exists())
    settings = json.loads(row[0]) if row else {}
    if settings.get('policy_version') != 2:
        budget = json.loads(frozen[0]) if frozen else {}
        historical = old or bool(settings)
        settings = {'request_limit': budget.get('request_limit', 40) if historical else None,
                    'timeout_seconds': budget.get('timeout_seconds', 90) if historical else None,
                    'compact_paper': False, **settings,
                    'policy_version': 2, 'limit_source': 'legacy' if historical else 'default',
                    'timeout_source': 'legacy' if historical else 'model_connection',
                    'preference_source': 'legacy' if historical else 'default'}
        db.execute('INSERT OR REPLACE INTO runtime_policy VALUES (1,?)', (json.dumps(settings),))
    retired=[]
    for row in db.execute('SELECT id,payload FROM failure_guards').fetchall():
        guard=json.loads(row['payload'])
        if guard.get('kind')!='invalid_response':continue
        db.execute('INSERT OR IGNORE INTO legacy_records VALUES (?,?)',('invalid-response-guard/'+row['id'],row['payload'].encode()))
        db.execute('DELETE FROM failure_guards WHERE id=?',(row['id'],));retired.append(row['id'])
    stop=db.execute('SELECT payload FROM runtime_stop WHERE id=1').fetchone()
    if stop and json.loads(stop[0]).get('operation') in retired:
        db.execute('INSERT OR IGNORE INTO legacy_records VALUES (?,?)',('invalid-response-stop',stop[0].encode()))
        db.execute('DELETE FROM runtime_stop WHERE id=1')
    for record in db.execute('SELECT research_id,payload FROM request_budgets').fetchall():
        budget = json.loads(record[1])
        if budget.get('metering_version') != 2:
            known = [c for c in traces if isinstance(c, dict) and c.get('usage_returned')] if budget.get('used') else []
            budget.update(metering_version=2, input_tokens=sum(int(c.get('input') or 0) for c in known),
                          output_tokens=sum(int(c.get('output') or 0) for c in known), usage_reported_calls=len(known))
        if budget.get('state') == 'provider_blocked':
            budget.update(state='service_interrupted', legacy_provider_blocked=True)
        db.execute('UPDATE request_budgets SET payload=? WHERE research_id=?', (json.dumps(budget), record[0]))


def retire_legacy_workflow(state):
    """Normalize retired workflow names once at import, never in the dispatcher."""
    from copy import deepcopy
    writing={eid for eid,task in state['experiments'].items() if task['kind']=='writing'}
    for eid in writing:
        task=state['experiments'][eid]
        if not task.get('retired'):
            state.setdefault('legacy_records',[]).append({'source':'retired-writing-experiment','raw':deepcopy(task)})
            task.update(retired=True,reason='写作改为研究级交付；保留原实验记录')
    def dependencies(eid):
        return [item for dep in state['experiments'][eid]['dependencies']
                for item in (dependencies(dep) if dep in writing else [dep])]
    for task in state['experiments'].values():
        if not task['retired']:task['dependencies']=list(dict.fromkeys(dependencies(task['task_id'])))
    for qid,question in state['questions'].items():
        owned={eid for eid,task in state['experiments'].items() if task['question_id']==qid}
        related=owned | set(question.get('related_experiment_ids',[]))
        if owned and owned <= writing and not any(not state['experiments'][eid]['retired'] for eid in related):
            question.update(retired=True,reason='文书属于研究级交付，保留原问题历史')
    action=state.get('action') or {}
    if action.get('capability') in {'research.review_report','writing.reconcile'} or action.get('target') in writing:
        state.setdefault('legacy_records',[]).append({'source':'retired-review-action','raw':action})
        state['action']=None
    if (state.get('publication') or {}).get('stage')=='reconcile':state['publication']=None


def migrate_interpretations(state):
    """Keep original records; current claims derive from the actual report, never a reviewer."""
    from .domain import experiment_report
    from ai_system.graph.registry import find_refs
    number=max((int(k[1:]) for k in state['claims'] if k.startswith('C') and k[1:].isdigit()),default=0)
    for key, task in state['experiments'].items():
        report=experiment_report(state,task)
        current=[]
        for finding in report.get('key_findings',[]):
            candidate=next((c for c in state['claims'].values() if c['producer_attempt']==task['execution_id']
                            and c['payload']['statement']==finding['statement']),None)
            if candidate is None:
                number+=1;ref='C'+str(number)
                candidate={'payload':{'id':ref,'subproblem':key,**finding,
                           'evidence':list(dict.fromkeys(find_refs(finding['statement'])))},
                           'experiment_id':key,'producer_attempt':task['execution_id'],'assumption_refs':{}}
                state['claims'][ref]=candidate
            current.append(candidate['payload']['id'])
        for ref,claim in state['claims'].items():
            if claim['experiment_id']!=key:continue
            claim.update(active=ref in current,status='current' if ref in current else 'historical',
                         report_ref=task['report_ref'] if ref in current else claim.get('report_ref'))
            if ref not in current:claim.setdefault('reason','与当前实验解释不对应，原观点与来源保留')
    for q in state['questions'].values():
        q.pop('statement',None)
        q.setdefault('version',1)
        q.setdefault('history',[])
        q.setdefault('related_experiment_ids',[])
        q.setdefault('answer_status','needs_update' if q.get('judgment') else 'unanswered')
    for issue in [*state['feedback'],*state['faults']]:
        if not issue.get('repair_targets') and (issue['source'] in {'local_review','reconcile','sections'}
                or issue['source'].startswith(('review:','review-task:'))):
            issue['blocking']=False
    retire_legacy_workflow(state)
    if state.get('publication'):
        stage=state['publication']['stage']
        if stage in {'reconcile','validate','repair'}:state['publication']=None
    # Old unified wording is kept as history, never another current state.
    if state.get('consistency'):
        state.setdefault('legacy_records',[]).append({'source':'consistency','raw':state['consistency']})
    state.pop('consistency',None)


def migrate_workspace(store):
    # Stores are also opened from inside existing business transactions. Once
    # migrated, checking the version must not acquire a second writer lease.
    with closing(store._connect()) as probe:
        if probe.execute("SELECT 1 FROM sqlite_master WHERE name='domain_migrations'").fetchone():
            if probe.execute('SELECT 1 FROM domain_migrations WHERE version=?', (MIGRATION_VERSION,)).fetchone():
                return
    with store._transaction() as db:
        db.execute("CREATE TABLE IF NOT EXISTS domain_migrations(version INTEGER PRIMARY KEY)")
        if db.execute("SELECT 1 FROM domain_migrations WHERE version=?", (MIGRATION_VERSION,)).fetchone():
            return
        db.execute("CREATE TABLE IF NOT EXISTS legacy_records(source TEXT PRIMARY KEY, raw BLOB NOT NULL)")
        for name in ('.system/results.json','.system/validation.json','.system/consistency.json','plan.md','plan.json','metadata.json'):
            source=store.root/name
            if source.is_file() and source.resolve().is_relative_to(store.root):
                db.execute('INSERT OR IGNORE INTO legacy_records VALUES (?,?)',(name,source.read_bytes()))
        for row in db.execute("SELECT id,payload FROM research").fetchall():
            state = json.loads(row[1])
            if state.get('schema_version') in {4, SCHEMA_VERSION}:
                db.execute('INSERT OR IGNORE INTO legacy_records VALUES (?,?)',
                           (f'research/{row[0]}/v{state["schema_version"]}',row[1].encode('utf-8')))
                if state['schema_version']==4:migrate_interpretations(state)
                else:retire_legacy_workflow(state)
                state['schema_version']=SCHEMA_VERSION
                db.execute('UPDATE research SET payload=? WHERE id=?',(json.dumps(state,ensure_ascii=False),row[0]))
                continue
            if state.get("schema_version") not in {2, 3}:
                db.execute('INSERT OR IGNORE INTO legacy_records VALUES (?,?)', (f'research/{row[0]}', row[1].encode('utf-8')))
                db.execute('DELETE FROM research WHERE id=?', (row[0],))
                continue
            db.execute('INSERT OR IGNORE INTO legacy_records VALUES (?,?)',
                       (f'research/{row[0]}/v{state["schema_version"]}', row[1].encode('utf-8')))
            if state['schema_version'] == 2:
                state["feedback"], state["faults"] = [], []
                state.setdefault("legacy_records", [])
                for item in state.pop("issues", []):
                    category = item.get("category") if isinstance(item, dict) else None
                    from .models import ContentFeedback, RuntimeFault
                    model = RuntimeFault if category in FAULT_CATEGORIES else ContentFeedback
                    try:
                        if not isinstance(item, dict) or item.get('task_id') not in state.get('tasks', {}):
                            raise ValueError('历史记录没有可核对的方案关联')
                        normalized = model.model_validate({key:value for key,value in item.items() if key in model.model_fields})
                    except (ValueError, TypeError):
                        state["legacy_records"].append({"source": f"research/{row[0]}/issues", "raw": item})
                    else:
                        state['faults' if category in FAULT_CATEGORIES else 'feedback'].append({**normalized.model_dump(), **item})
                for task in state.get('tasks', {}).values():
                    design = task.get('design') or {}
                    original = design.pop('exploration', None)
                    if original:
                        if isinstance(original, dict) and original.get('id'):
                            design['experiment_id'] = original['id']
                        else:
                            state['legacy_records'].append({'source':f"research/{row[0]}/design/exploration",'raw':original})
            state['experiments'] = state.pop('tasks')
            state['executions'] = state.pop('attempts')
            state['artifacts'] = state.pop('evidence')
            state['questions'] = {}
            from .domain import attach_question
            groups = {}
            for key, experiment in state['experiments'].items():
                experiment['execution_id'] = experiment.pop('active_attempt')
                group = experiment.pop('question_key', None) or key
                statement = experiment.pop('research_question', None)
                if group in groups:
                    attach_question(state, experiment, question_id=groups[group])
                else:
                    question = attach_question(state, experiment, statement=statement,
                        title=statement or experiment['subproblem'].get('title'))
                    groups[group] = question['id']
                if experiment.get('report'):
                    question = state['questions'][experiment['question_id']]
                    question['judgment'] = experiment['report'].get('summary', '')
            for artifact in state['artifacts'].values():
                execution = state['executions'][artifact['producer_attempt']]
                artifact.update(experiment_id=execution['task_id'], type='result')
            from .domain import save_report_artifact
            for experiment in state['experiments'].values():
                report=experiment.pop('report',None)
                experiment.pop('design',None)
                experiment['report_ref']=None
                if report and experiment['execution_id']:
                    save_report_artifact(state,store.root,experiment,report)
                    artifact=state['artifacts'][experiment['report_ref']]
                    artifact['review']=experiment.pop('report_review',None)
                    artifact['versions']=experiment.pop('report_versions',[])
            from .domain import register_execution_artifacts
            for execution in state['executions'].values():
                register_execution_artifacts(state, store.root, execution)
            for claim in state['claims'].values():
                claim['experiment_id']=state['executions'][claim['producer_attempt']]['task_id']
            state.pop('workflow', None)
            state.pop('proposal_hashes', None)
            # Old proposals are archived with their original bytes, never recompiled.
            state['action'] = None
            state['publication'] = None
            for command in db.execute('SELECT id,digest,response FROM commands WHERE research_id=?',(row[0],)).fetchall():
                if command[0].startswith(('research-next:','planner-repair:','revision:')):
                    db.execute('INSERT OR IGNORE INTO legacy_records VALUES (?,?)',
                        (f'commands/{row[0]}/{command[0]}',json.dumps(list(command),ensure_ascii=False).encode()))
                    db.execute('DELETE FROM commands WHERE research_id=? AND id=?',(row[0],command[0]))
            migrate_interpretations(state)
            state["schema_version"] = SCHEMA_VERSION
            db.execute("UPDATE research SET payload=? WHERE id=?", (json.dumps(state, ensure_ascii=False), row[0]))
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for old, new in (("comparison_policy", "experiment_policy"),
                         ("comparison_budgets", "experiment_budgets"),
                         ("comparisons", "experiment_comparisons")):
            if old in names:
                if new in names:
                    raise ValueError(f"发现两套 {old}/{new}，停止迁移以避免覆盖")
                db.execute(f'ALTER TABLE "{old}" RENAME TO "{new}"')
        db.execute("""CREATE TABLE IF NOT EXISTS research_activity (
            research_id TEXT NOT NULL, id TEXT NOT NULL, started_at REAL NOT NULL,
            updated_at REAL NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(research_id,id))""")
        db.execute('CREATE INDEX IF NOT EXISTS research_activity_recent ON research_activity(research_id,started_at DESC,id DESC)')
        for row in db.execute('SELECT id,payload FROM research').fetchall():
            state = json.loads(row[1])
            for item in state.pop('activity', []):
                db.execute('INSERT OR IGNORE INTO research_activity VALUES (?,?,?,?,?)',
                    (row[0], item['id'], item['started_at'], item.get('finished_at') or item['started_at'], json.dumps(item, ensure_ascii=False)))
            db.execute('UPDATE research SET payload=? WHERE id=?', (json.dumps(state, ensure_ascii=False), row[0]))
        from services.chat_services.chat_history_manager import migrate_chat
        migrate_chat(db, store.root)
        migrate_runtime(db, store.root)
        policy = json.loads(db.execute('SELECT payload FROM runtime_policy WHERE id=1').fetchone()[0])
        for name in ('confirm_models', 'max_design_rounds', 'explore_top_k', 'n_candidates'):
            policy.pop(name, None)
        db.execute('UPDATE runtime_policy SET payload=? WHERE id=1', (json.dumps(policy),))
        db.execute("INSERT INTO domain_migrations VALUES (?)", (MIGRATION_VERSION,))
