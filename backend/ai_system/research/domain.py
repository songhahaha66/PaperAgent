"""Domain boundaries. Checkpoints and file registries are disposable projections.

Project (owner in the application DB) -> research -> task revisions (plans) ->
attempts (experiments) -> evidence/claims/sections (artifacts). A plan describes
intent; an experiment freezes inputs, protocol and experience versions. Content
feedback and runtime faults have independent lifecycles and durable collections.
"""
from itertools import chain

SCHEMA_VERSION = 3
FAULT_CATEGORIES = frozenset({"execution", "service"})


def findings(state):
    """A transient union for gates; never another stored source of truth."""
    return list(chain(state.get("feedback", []), state.get("faults", [])))


def plan_view(task):
    design = task.get("design") or {}
    if not design and task.get('experiment'):
        from ai_system.modules.experiments.planning import experiment_design
        design=experiment_design(task['experiment'])
    return {"id": task["task_id"], "version": task["revision"],
            "objective": task["objective"], "dependencies": task["dependencies"],
            "methods": design.get("candidates", []), "selected": design.get("chosen_index"),
            "status": task["acceptance"], "current": task["validity"] == "current"}
