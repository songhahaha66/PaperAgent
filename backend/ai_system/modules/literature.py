"""Literature produces source records and evidence; it never certifies a claim."""
from .registry import CapabilityProduct, LiteratureInputs, SourceInputs, CitationInputs, Inputs
from pydantic import Field


class ImportInputs(Inputs):
    url: str = Field(min_length=1)


async def search(ctx, inputs):
    from ai_system.research.literature import maybe_search
    value = await maybe_search(ctx.workspace, inputs["question"], inputs.get("title", ""))
    return CapabilityProduct("literature", value)


async def collect(ctx, inputs):
    from ai_system.research.literature import LiteratureService
    service = LiteratureService(ctx.workspace)
    value = await service.search(**inputs)
    return CapabilityProduct("literature", value)


async def verify(ctx, inputs):
    from ai_system.research.citations import audit_citations, literature_root
    value = await audit_citations(literature_root(ctx), inputs["texts"], ctx.planner_llm, active=True)
    return CapabilityProduct("literature", value)


async def import_source(ctx, inputs):
    from ai_system.research.literature import LiteratureService
    return CapabilityProduct('literature', await LiteratureService(ctx.workspace).import_url(inputs['url']))


def register(registry):
    registry.register("literature", "literature.search", "literature", search, LiteratureInputs)
    registry.register("literature", "literature.collect", "literature", collect, SourceInputs,model_callable=True,purpose="按当前研究信息需求检索文献并保留来源，不将检索摘要当作计算结果。")
    registry.register("literature", "literature.verify", "literature", verify, CitationInputs)
    registry.register("literature", "literature.import", "literature", import_source, ImportInputs)
