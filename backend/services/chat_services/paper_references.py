"""Versioned paper references carried by the ordinary workspace chat."""
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PaperSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    file: str = Field(min_length=1, max_length=500)
    version: str = Field(pattern=r'^[a-f0-9]{64}$')
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str = Field(max_length=100000)


class PaperReference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    quote: str = Field(min_length=1, max_length=100000)
    selection: PaperSelection | None = None
    read_version: str | None = None
    read_quote: str | None = Field(default=None, max_length=100000)


class PaperContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    research_id: str
    references: list[PaperReference] = Field(min_length=1, max_length=24)
    thread_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode='after')
    def bounded_quotes(self):
        if sum(len(r.quote) + len(r.read_quote or '') + len(r.selection.quote if r.selection else '')
               for r in self.references) > 100000:
            raise ValueError('引用总长度不能超过100000字符')
        return self


def validate_context(root, research_id, context):
    """Selections are CAS-bound; reading quotes remain explicitly non-editable."""
    from . import paper_project as project
    context = PaperContext.model_validate(context)
    if context.research_id != research_id:
        raise ValueError('引用对应的研究已改变，请重新选择')
    for reference in context.references:
        if reference.selection:
            project.current_selection(root, reference.selection.model_dump())
    return context.model_dump()


def context_text(context, limit=12000):
    if not context:
        return ''
    rows=[]
    for index, reference in enumerate(context['references']):
        text=reference['quote'][:limit]
        selection=reference.get('selection')
        rows.append({'reference': index, 'quote': text, 'file': selection['file'] if selection else None,
                     'editable': bool(selection), 'read_version': reference.get('read_version')})
        limit-=len(text)
        if limit<=0:break
    import json
    return json.dumps(rows,ensure_ascii=False)
