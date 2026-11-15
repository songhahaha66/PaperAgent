"""Text replacements and versions observed in the current agent conversation."""
import json
from langchain_core.messages import ToolMessage
from pydantic import BaseModel, ConfigDict


class TextEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    old_text: str
    new_text: str


def observed_version(messages, name):
    for message in reversed(messages):
        if (not isinstance(message, ToolMessage) or message.status == 'error'
                or message.name not in {'research_read_artifact', 'writing_edit', 'workspace_edit'}):
            continue
        try:
            value = json.loads(message.content)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and value.get('path') == name:
            return value.get('version')
    return None


def edited_text(before, content, old_text, edits):
    from ai_system.research.models import ResearchConflict
    if not edits and content is None:
        raise ValueError('请提供 content 或 edits')
    replacements = edits or ([{'old_text': old_text, 'new_text': content}] if old_text is not None else [])
    if not replacements:
        return content
    text = before
    for edit in replacements:
        old = edit['old_text']
        if not old or text.count(old) != 1:
            raise ResearchConflict('待替换原文必须唯一，请重新读取当前文件')
        text = text.replace(old, edit['new_text'], 1)
    return text
