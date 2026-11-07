"""The durable client contract. Tool return values are not conversation Items."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

Status = Literal['running', 'completed', 'failed', 'interrupted']


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Thread(Model):
    id: str
    research_id: str | None = None
    parent_thread_id: str | None = None
    parent_item_id: str | None = None
    created_at: float


class Turn(Model):
    id: str
    thread_id: str
    root_turn_id: str
    status: Status = 'running'
    started_at: float
    finished_at: float | None = None


class ItemBase(Model):
    id: str
    thread_id: str
    turn_id: str
    sequence: int = 0
    status: Status = 'running'
    started_at: float
    finished_at: float | None = None
    updated_at: float | None = None


class MessageItem(ItemBase):
    type: Literal['userMessage', 'agentMessage']
    text: str
    phase: Literal['commentary', 'final_answer'] = 'commentary'


class ToolItem(ItemBase):
    type: Literal['toolCall', 'commandExecution', 'fileChange', 'collabToolCall']
    tool_name: str
    call_id: str
    label: str
    detail: str = ''
    message: str = ''
    error_type: str | None = None
    phase: str | None = None
    research_id: str | None = None
    action_id: str | None = None
    observation_id: str | None = None
    question_id: str | None = None
    experiment_id: str | None = None
    task_id: str | None = None
    subject: str = ''
    run_id: str | None = None
    child_thread_id: str | None = None
    child_turn_id: str | None = None
    file: str | None = None
    output_file: str | None = None
    script_file: str | None = None
    error_file: str | None = None
    files: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    output_tail: str = ''


class PlanStep(Model):
    step: str
    status: Literal['pending', 'in_progress', 'completed', 'blocked']
    note: str = ''


class PlanItem(ItemBase):
    type: Literal['plan'] = 'plan'
    explanation: str = ''
    steps: list[PlanStep] = Field(default_factory=list)


class NoticeItem(ItemBase):
    type: Literal['notice', 'inputRequest']
    name: str
    content: str | dict


Item = Annotated[MessageItem | ToolItem | PlanItem | NoticeItem, Field(discriminator='type')]
ITEM = TypeAdapter(Item)
TOOL_TYPES = {'toolCall', 'commandExecution', 'fileChange', 'collabToolCall'}


class AgentEvent(Model):
    seq: int
    method: Literal['thread/started', 'turn/started', 'turn/completed',
                    'item/started', 'item/updated', 'item/completed', 'item/agentMessage/delta']
    thread_id: str
    turn_id: str | None = None
    item: Item | None = None
    turn: Turn | None = None
    thread: Thread | None = None
