"""Source and passage fixtures used by literature and citation tests."""
import json

from ai_system.research.citations import SupportBatch, SupportVerdict
from ai_system.research.literature import LiteratureStore


def seed(workspace, text='Linear programming optimizes a linear objective subject to linear equality and inequality constraints.'):
    store = LiteratureStore(workspace)
    source = store.upsert({'title': 'A sourced method', 'doi': '10.1000/example',
                           'url': 'https://doi.org/10.1000/example', 'authors': ['Actual Author'],
                           'year': 2024, 'provider': 'fixture'})
    store.add_passages(source, [{'text': text, 'locator': '摘要段落 1'}],
                       'abstract', source['url'], 'hash-one')
    return store, source, store.passages()[0]


class Judge:
    def __init__(self, status='supported', quote=None):
        self.status, self.quote, self.calls = status, quote, 0

    def with_structured_output(self, *args, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        assert '不可信' in messages[0].content and '不是' not in messages[-1].content[:1]
        rows = json.loads(messages[-1].content)
        return SupportBatch(verdicts=[SupportVerdict(key=row['key'], status=self.status,
            reason='Scoped source support check',
            supporting_quote=self.quote if self.quote is not None else row['excerpt']) for row in rows])
