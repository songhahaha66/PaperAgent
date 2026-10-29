"""Research-owned drafts; deterministic validation, rendering and export."""
from types import SimpleNamespace
from importlib import import_module
from ..registry import CapabilityProduct, Inputs


def register(registry):
    from .draft import DraftEdit, edit
    registry.register('writing','writing.edit','research_state',edit,DraftEdit,model_callable=True,
        purpose='直接编辑研究正文或TeX依赖文件。已有文件先read_artifact取得version；支持完整文件或唯一原文局部替换，不重新计算。')
    for name,owner in (("validate","validation"),("render","validation"),("export","export")):
        async def run(ctx,inputs,name=name,owner=owner):
            from ai_system.research.control import graph_view
            value=await getattr(import_module('ai_system.modules.writing.'+owner),name)(
                graph_view(ctx.research.snapshot(),root=ctx.workspace),SimpleNamespace(context=ctx))
            return CapabilityProduct('paper',value)
        registry.register('writing','writing.'+name,'paper',run,Inputs)
