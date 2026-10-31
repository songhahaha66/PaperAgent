"""Read-only compatibility for historical comparison records."""
import json
from contextlib import closing


class LegacyComparisons:
    def __init__(self,store):
        self.store=store

    def policy(self):
        with closing(self.store._connect()) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='experiment_policy'").fetchone():
                return {'version':0,'enabled':False,'spec':None}
            row=db.execute('SELECT version,payload FROM experiment_policy WHERE id=1').fetchone()
        return {'version':row[0],**json.loads(row[1])} if row else {'version':0,'enabled':False,'spec':None}

    def read(self,task_id,revision):
        with closing(self.store._connect()) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='experiment_comparisons'").fetchone():return None
            row=db.execute('SELECT payload FROM experiment_comparisons WHERE research_id=? AND task_id=? AND revision=?',
                           (self.store.research_id,task_id,revision)).fetchone()
        return json.loads(row[0]) if row else None
