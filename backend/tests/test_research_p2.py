"""Retired metric gates stay historical and cannot re-enter recovery."""
from copy import deepcopy
from ai_system.research.migration import MIGRATION_VERSION
from ai_system.research.store import ResearchStore
from helpers.research import make_store, finish


def test_migration_archives_gates_without_deleting_execution_or_results(tmp_path):
    store=make_store(tmp_path,limit=10)
    finish(store,'Q1')
    with store._transaction() as db:
        state=store._read(db)
        fault={'id':'old-gate','task_id':'Q1','category':'execution','message':'步数已用尽，缺少唯一登记指标',
               'blocking':True,'repair_targets':{'Q1':1},'status':'confirmed'}
        state['faults'].append(fault)
        before=deepcopy(state)
        store._save(db,state,'fixture_old_gate',{})
        db.execute('DELETE FROM domain_migrations WHERE version=?',(MIGRATION_VERSION,))
    migrated=ResearchStore(tmp_path,'research').snapshot()
    assert migrated['executions']==before['executions']
    assert migrated['artifacts']==before['artifacts']
    assert all(not r['checks'] for r in migrated['spec']['requirements'])
    assert not migrated['faults'][-1]['blocking'] and not migrated['faults'][-1]['repair_targets']
    assert any(r['source']=='retired-evidence-gate' and r['raw']==fault for r in migrated['legacy_records'])
    assert ResearchStore(tmp_path,'research').snapshot()==migrated
