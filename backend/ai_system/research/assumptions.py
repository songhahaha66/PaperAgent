"""Dependency scope for retained historical premise records."""

def assumption_scope(tasks, root):
    scope = {root}
    while True:
        more = {key for key, task in tasks.items()
                if task["kind"] != "verify" and set(task["dependencies"]) & scope}
        if more <= scope:
            return [root, *sorted(scope - {root})]
        scope |= more
