"""Source and passage fixtures used by literature and citation tests."""
from ai_system.research.literature import LiteratureStore


def seed(workspace, text='Linear programming optimizes a linear objective subject to linear equality and inequality constraints.'):
    store = LiteratureStore(workspace)
    source = store.upsert({'title': 'A sourced method', 'doi': '10.1000/example',
                           'url': 'https://doi.org/10.1000/example', 'authors': ['Actual Author'],
                           'year': 2024, 'provider': 'fixture'})
    store.add_passages(source, [{'text': text, 'locator': '摘要段落 1'}],
                       'abstract', source['url'], 'hash-one')
    return store, source, store.passages()[0]
