"""The main Agent's current work plan, independent of topics and delivery."""
import time
from typing import Literal

from pydantic import ConfigDict, Field, model_validator
from ai_system.modules.registry import Inputs


class PlanStep(Inputs):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    status: Literal['pending', 'in_progress', 'completed', 'blocked']
    note: str = Field(default='', max_length=300, description='需要等待或调整时的简短说明。')
    kind: Literal['work', 'delivery'] = Field(default='work',
        description='普通研究与写作用work；如计划包含系统导出，将最后一项标为delivery，其状态由实际导出更新。')


class PlanUpdate(Inputs):
    steps: list[PlanStep] = Field(min_length=1, max_length=30,
        description='完整的总体工作计划，按执行顺序排列；更新时保留已完成项。')

    @model_validator(mode='after')
    def consistent_steps(self):
        if sum(step.status == 'in_progress' for step in self.steps) > 1:
            raise ValueError('只标记一个当前进行中的工作项')
        titles = [step.title.casefold() for step in self.steps]
        if len(set(titles)) != len(titles):
            raise ValueError('工作项标题不能重复')
        if any(step.kind == 'delivery' for step in self.steps[:-1]):
            raise ValueError('系统导出只能放在计划最后一项')
        return self


class ResearchPlan(PlanUpdate):
    updated_at: float


def update_plan(ctx, inputs):
    from .operations import _change

    def edit(state):
        previous = state.get('progress_plan')
        if previous and previous['steps'] == inputs['steps']:
            return {'progress_plan': previous, 'unchanged': True}
        plan = ResearchPlan(**inputs, updated_at=time.time()).model_dump()
        state['progress_plan'] = plan
        return {'progress_plan': plan}

    return _change(ctx, 'plan_updated', inputs, edit)


def sync_delivery_plan(state, status, note=''):
    """Only the explicitly bound export item follows delivery receipts."""
    plan = state.get('progress_plan')
    if not plan or plan['steps'][-1]['kind'] != 'delivery':
        return
    if status == 'in_progress' and any(s['status'] == 'in_progress' for s in plan['steps'][:-1]):
        return
    plan['steps'][-1].update(status=status, note=note[:300])
    plan['updated_at'] = time.time()
