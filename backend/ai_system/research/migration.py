"""Transactional, idempotent cutover. Uninterpretable history stays verbatim.

Only this module reads the retired issue collection and comparison table names.
Archives are provenance, never input to the active scheduler or publication gate.
"""
import json
from contextlib import closing

from .domain import FAULT_CATEGORIES, SCHEMA_VERSION


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
                    'compact_paper': False, 'confirm_models': False, **settings,
                    'policy_version': 2, 'limit_source': 'legacy' if historical else 'default',
                    'timeout_source': 'legacy' if historical else 'model_connection',
                    'preference_source': 'legacy' if historical else 'default'}
        db.execute('INSERT OR REPLACE INTO runtime_policy VALUES (1,?)', (json.dumps(settings),))
    for record in db.execute('SELECT research_id,payload FROM request_budgets').fetchall():
        budget = json.loads(record[1])
        if budget.get('metering_version') != 2:
            known = [c for c in traces if isinstance(c, dict) and c.get('usage_returned')] if budget.get('used') else []
            budget.update(metering_version=2, input_tokens=sum(int(c.get('input') or 0) for c in known),
                          output_tokens=sum(int(c.get('output') or 0) for c in known), usage_reported_calls=len(known))
        if budget.get('state') == 'provider_blocked':
            budget.update(state='service_interrupted', legacy_provider_blocked=True)
        db.execute('UPDATE request_budgets SET payload=? WHERE research_id=?', (json.dumps(budget), record[0]))


def migrate_workspace(store):
    # Stores are also opened from inside existing business transactions. Once
    # migrated, checking the version must not acquire a second writer lease.
    with closing(store._connect()) as probe:
        if probe.execute("SELECT 1 FROM sqlite_master WHERE name='domain_migrations'").fetchone():
            if probe.execute('SELECT 1 FROM domain_migrations WHERE version=?', (SCHEMA_VERSION,)).fetchone():
                return
    with store._transaction() as db:
        db.execute("CREATE TABLE IF NOT EXISTS domain_migrations(version INTEGER PRIMARY KEY)")
        if db.execute("SELECT 1 FROM domain_migrations WHERE version=?", (SCHEMA_VERSION,)).fetchone():
            return
        db.execute("CREATE TABLE IF NOT EXISTS legacy_records(source TEXT PRIMARY KEY, raw BLOB NOT NULL)")
        for name in ('.system/results.json','.system/validation.json','.system/consistency.json','plan.md','plan.json','metadata.json'):
            source=store.root/name
            if source.is_file() and source.resolve().is_relative_to(store.root):
                db.execute('INSERT OR IGNORE INTO legacy_records VALUES (?,?)',(name,source.read_bytes()))
        for row in db.execute("SELECT id,payload FROM research").fetchall():
            state = json.loads(row[1])
            if state.get("schema_version") == SCHEMA_VERSION:
                continue
            if state.get("schema_version") != 2:
                db.execute('INSERT OR IGNORE INTO legacy_records VALUES (?,?)', (f'research/{row[0]}', row[1].encode('utf-8')))
                db.execute('DELETE FROM research WHERE id=?', (row[0],))
                continue
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
        migrate_runtime(db, store.root)
        db.execute("INSERT INTO domain_migrations VALUES (?)", (SCHEMA_VERSION,))
