"""Research-owned drafts; deterministic validation, rendering and export."""
from types import SimpleNamespace
from ..registry import CapabilityProduct, Inputs


def register(registry):
    from .document import register as register_document
    register_document(registry)
    from .draft import DraftEdit, edit
    registry.register('writing','writing.edit','research_state',edit,DraftEdit,model_callable=True,
        purpose='分步保存正文或TeX章节。已有文件先读取，自动绑定版本。Markdown可append追加一节；LaTeX先保存paper.tex骨架，逐节写sections/*.tex并用input引用。支持完整content、唯一原文替换、多处edits集中修稿；已有结果先落稿，再集中预览。')
    async def run(ctx,inputs):
        from ai_system.research.control import graph_view
        from .export import export
        value=await export(graph_view(ctx.research.snapshot(),root=ctx.workspace),SimpleNamespace(context=ctx))
        from ai_system.research.plan import sync_delivery_plan
        failed=bool(value.get('issues') or value.get('research_blocked') or value.get('artifact_conflict'))
        with ctx.research._transaction() as db:
            state=ctx.research._read(db)
            sync_delivery_plan(state,'blocked' if failed else 'completed')
            ctx.research._save(db,state,'delivery_finished',{'success':not failed})
        return CapabilityProduct('paper',value,'failed' if failed else None,
                                 '交付未完成，请根据诊断修正。' if failed else None)
    registry.register('writing','writing.export','paper',run,Inputs,model_callable=True,
        purpose='检查并导出当前正文，返回文件或诊断；失败时修正后重试，不重写论文。')
