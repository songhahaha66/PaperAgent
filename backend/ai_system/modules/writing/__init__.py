"""Research-owned drafts; deterministic validation, rendering and export."""
from types import SimpleNamespace
from ..registry import CapabilityProduct, Inputs


def register(registry):
    from .document import register as register_document
    register_document(registry)
    from .draft import DraftEdit, edit
    registry.register('writing','writing.edit','research_state',edit,DraftEdit,model_callable=True,
        purpose='直接编辑研究正文或TeX依赖文件。已有文件先research_read_artifact读取，系统自动绑定该读取回执的版本，无需填写哈希；支持完整文件或唯一原文局部替换，不重新计算。')
    async def run(ctx,inputs):
        from ai_system.research.control import graph_view
        from .export import export
        value=await export(graph_view(ctx.research.snapshot(),root=ctx.workspace),SimpleNamespace(context=ctx))
        failed=bool(value.get('issues'))
        return CapabilityProduct('paper',value,'failed' if failed else None,
                                 value['issues'][0].get('message') if failed else None)
    registry.register('writing','writing.export','paper',run,Inputs)
