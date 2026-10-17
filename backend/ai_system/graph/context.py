"""
运行时上下文：LLM、判分器、内核池、登记表、事件推送

这些对象不能也不需要进 checkpoint，通过 LangGraph 的 context 传给每个节点。
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from services.file_services.plan_reconciler import PlanReconciler
from services.file_services.workspace_fs import WorkspaceFS

from ..sandbox import PythonKernel
from .llm_utils import cap_output
from .registry import ResultRegistry

logger = logging.getLogger(__name__)


class KernelPool:
    """每个子问题一个内核，并行求解互不干扰；共用同一个 WorkspaceFS 保证 manifest 一致"""

    def __init__(self, workspace: Path, fs: WorkspaceFS, timeout: float = 180.0):
        self.workspace = workspace
        self.fs = fs
        self.timeout = timeout
        self._kernels: Dict[str, PythonKernel] = {}

    def get(self, key: str) -> PythonKernel:
        if key not in self._kernels:
            self._kernels[key] = PythonKernel(str(self.workspace), timeout=self.timeout, fs=self.fs)
        return self._kernels[key]

    def promote(self, source: str, target: str) -> None:
        """把探索分支的内核（变量都在）改挂到正式求解的 key 下"""
        self._kernels[target] = self._kernels.pop(source)

    async def release(self, key: str) -> None:
        kernel = self._kernels.pop(key, None)
        if kernel is not None:
            await kernel.close()

    async def close(self) -> None:
        kernels, self._kernels = list(self._kernels.values()), {}
        await asyncio.gather(*(k.close() for k in kernels), return_exceptions=True)


class EventSink:
    """把阶段进度推给前端（复用 stream_manager.send_json_block），没有 stream_manager 时只写日志"""

    def __init__(self, stream_manager=None, *, publish_files=True):
        self.stream_manager = stream_manager
        self.publish_files = publish_files

    async def emit(self, block_type: str, content: Any) -> None:
        logger.info("[paper-graph] %s: %s", block_type, str(content)[:200])
        if self.stream_manager is None:
            return
        try:
            await self.stream_manager.send_json_block(block_type, content)
        except Exception as exc:
            logger.warning("推送事件 %s 失败: %s", block_type, exc)

    async def stage(self, text: str) -> None:
        await self.emit("paper_stage", text)

    async def file_changed(self, path: str) -> None:
        if self.publish_files:
            await self.emit("file_changed", path)


STAGES = [
    ("analyze", "题目分析与数据概览"),
    ("design", "建模方案：检索方法、生成候选、评审打分"),
    ("confirm", "确认建模方案"),
    ("solve", "分子问题编码求解并登记结果"),
    ("write", "按登记结果撰写各章节"),
    ("validate", "校验引用与数字，修复问题章节"),
    ("render", "生成 paper.md"),
    ("export", "导出最终格式"),
]
EXPORT_STAGE_TITLES = {"latex": "LaTeX 排版并编译 PDF", "word": "导出 Word 文档"}
# 上传模板时由 services/file_services/template_contract.py 放进工作空间
TEMPLATE_CONTRACT = ".system/template_contract.md"
TEMPLATE_DOCX = ".system/_template_original.docx"
TEMPLATE_REQUIREMENTS_CHARS = 4000
# 编码循环每步只写一段代码，4096 token 足够；不设上限时一次重复输出能把请求拖到网关 300 秒超时
CODER_MAX_TOKENS = 4096
STATUS_LABEL = {"pending": "⬜ 待写", "in_progress": "⏳ 进行中", "completed": "✅ 已完成", "blocked": "❌ 阻塞"}


@dataclass
class GraphContext:
    workspace: Path
    fs: WorkspaceFS
    registry: ResultRegistry
    kernels: KernelPool
    events: EventSink
    planner_llm: Any
    coder_llm: Any
    writer_llm: Any
    research: Any = None
    judge: Any = None
    n_candidates: int = 1
    confirm_models: bool = False
    max_solver_steps: int = 40
    max_repair_rounds: int = 2
    max_review_rounds: int = 1
    explore_top_k: int = 1
    explore_steps: int = 12
    explore_margin: float = 2.0  # 前两名评审总分（满分 16）差距超过它就不试跑
    output_mode: str = "markdown"  # latex / word 时 render 之后导出 paper.pdf / paper.docx
    template_requirements: str = ""  # 用户模板的结构与格式要求，写作时遵守
    check_claims: bool = True  # 结论一致性审查（reconcile + 汇总章节核对）
    max_planning_calls: int = 2
    max_design_rounds: int = 2
    stage_status: Dict[str, str] = field(default_factory=dict)
    stage_notes: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        workspace_dir: str,
        planner_llm,
        coder_llm=None,
        writer_llm=None,
        judge=None,
        stream_manager=None,
        **options,
    ) -> "GraphContext":
        workspace = Path(workspace_dir).resolve()
        fs = WorkspaceFS(str(workspace))
        fs.init_structure()
        contract = workspace / TEMPLATE_CONTRACT
        if contract.exists() and "template_requirements" not in options:
            options["template_requirements"] = contract.read_text(encoding="utf-8")[:TEMPLATE_REQUIREMENTS_CHARS]
        return cls(
            workspace=workspace,
            fs=fs,
            registry=ResultRegistry.load(fs),
            kernels=KernelPool(workspace, fs, timeout=options.pop("kernel_timeout", 180.0)),
            events=EventSink(stream_manager),
            planner_llm=planner_llm,
            coder_llm=cap_output(coder_llm or planner_llm, CODER_MAX_TOKENS),
            writer_llm=writer_llm or planner_llm,
            judge=judge,
            **options,
        )

    async def set_stage(self, key: str, status: str, note: str = "") -> None:
        """更新进度并同步到 plan.md / plan.json（前端固定展示 plan.json）"""
        self.stage_status[key] = status
        if note:
            self.stage_notes[key] = note
        # 聊天里阶段卡片的进度条；plan_updated 由右侧计划面板消费，前端不放进消息
        await self.events.emit("paper_phase", {"stage": key, "status": status, "note": note})
        lines = ["# 数模论文生成计划", "", "| 序号 | 章节/任务 | 状态 | 说明 |", "|------|-----------|------|------|"]
        stages = [
            (k, EXPORT_STAGE_TITLES.get(self.output_mode, t) if k == "export" else t)
            for k, t in STAGES
            if k != "export" or self.output_mode in EXPORT_STAGE_TITLES
        ]
        for order, (stage_key, title) in enumerate(stages, start=1):
            label = STATUS_LABEL[self.stage_status.get(stage_key, "pending")]
            lines.append(f"| {order} | {title} | {label} | {self.stage_notes.get(stage_key, '')} |")
        markdown = "\n".join(lines) + "\n"
        try:
            reconciler = PlanReconciler(self.workspace)
            plan = reconciler.build_from_markdown(markdown, source="paper_graph")
            (self.workspace / "plan.md").write_text(markdown, encoding="utf-8")
            reconciler.write_plan_json(plan)
            await self.events.emit("plan_updated", plan)
        except Exception as exc:
            logger.warning("更新 plan.json 失败: %s", exc)

    def write_json(self, rel_path: str, data: Any) -> None:
        self.fs.write_text(rel_path, json.dumps(data, ensure_ascii=False, indent=2))


DATA_DIRS = ("data", "attachment")  # 评测把数据放 data/，用户上传的附件在 attachment/


def list_data_files(workspace: Path) -> List[str]:
    files = []
    for name in DATA_DIRS:
        folder = workspace / name
        if folder.exists():
            files.extend(str(p.relative_to(workspace)) for p in folder.rglob("*") if p.is_file())
    return sorted(files)
