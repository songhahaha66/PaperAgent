"""
工作空间目录结构统一管理
"""

from pathlib import Path
import logging
from typing import Optional

from .workspace_fs import WorkspaceFS, WORKSPACE_DIRECTORIES
from .template_contract import initialize_template_contract

logger = logging.getLogger(__name__)


class WorkspaceStructureManager:
    """工作空间目录结构统一管理"""

    WORKSPACE_DIRECTORIES = WORKSPACE_DIRECTORIES

    @classmethod
    def create_workspace_structure(cls, workspace_path: Path, work_id: str, template_id: Optional[int] = None, output_mode: str = "markdown") -> None:
        """创建统一的工作空间目录结构和初始文件

        Args:
            workspace_path: 工作空间路径
            work_id: 工作ID
            template_id: 可选的模板ID，如果提供则使用模板内容初始化 paper.md
            output_mode: 输出模式，可选值：markdown, word, latex
        """
        try:
            fs = WorkspaceFS(str(workspace_path))
            fs.init_structure()


            cls._create_workspace_files(workspace_path, work_id, template_id, output_mode)

            logger.info(f"工作空间目录结构和初始文件创建完成: {workspace_path}, 输出模式: {output_mode}")
        except Exception as e:
            logger.error(f"创建工作空间目录和文件失败: {e}")
            raise Exception(f"创建工作空间目录和文件失败: {e}")

    @classmethod
    def _create_workspace_files(cls, workspace_path: Path, work_id: str, template_id: Optional[int] = None, output_mode: str = "markdown") -> None:
        """创建工作空间初始文件

        Args:
            workspace_path: 工作空间路径
            work_id: 工作ID
            template_id: 可选的模板ID
            output_mode: 输出模式，可选值：markdown, word, latex
        """
        # 先初始化模板契约，并在可用时把模板骨架复制为初始论文文件。
        initialize_template_contract(workspace_path, template_id, output_mode)

        # 根据输出模式创建相应的初始文件
        if output_mode in {"markdown", "word"}:
            cls._create_paper_md(workspace_path, template_id)
            logger.info("已创建 paper.md，Word 文件在导出成功后生成")
        elif output_mode == "latex":
            # 原生 LaTeX：写作、校验和发布均使用 .tex，不创建 Markdown 正文。
            (workspace_path / "paper.tex").touch(exist_ok=True)
            logger.info("LaTeX 模式：已创建 paper.tex，完成后编译 paper.pdf")
        else:
            # 未知模式：默认创建 paper.md
            logger.warning(f"未知的输出模式 '{output_mode}'，默认创建 paper.md")
            cls._create_paper_md(workspace_path, template_id)

    @classmethod
    def _create_paper_md(cls, workspace_path: Path, template_id: Optional[int] = None) -> None:
        """创建 paper.md 文件

        Args:
            workspace_path: 工作空间路径
            template_id: 可选的模板ID，如果提供则使用模板内容

        Note:
            不抛出异常，失败时记录日志并创建空文件
        """
        paper_md_path = workspace_path / "paper.md"

        # 如果文件已存在，不覆盖
        if paper_md_path.exists():
            logger.info(f"paper.md 已存在，跳过创建: {paper_md_path}")
            return

        try:
            # 尝试获取模板内容
            content = None
            if template_id is not None:
                try:
                    from .template_files import template_file_service
                    from database.database import SessionLocal
                    from models.models import PaperTemplate

                    # 从数据库获取模板信息
                    db = SessionLocal()
                    try:
                        template = db.query(PaperTemplate).filter(PaperTemplate.id == template_id).first()
                        if template and template.file_path:
                            content = template_file_service.get_text_content(template.file_path)
                            logger.info(f"成功获取模板 {template_id} 的内容用于 paper.md")
                    finally:
                        db.close()
                except Exception as e:
                    logger.warning(f"获取模板 {template_id} 内容失败，将使用默认内容: {e}")
                    content = None

            if content is None:
                content = ""
                logger.info("无模板内容，创建空 paper.md，由 Agent 生成骨架")

            # 写入文件
            with open(paper_md_path, 'w', encoding='utf-8') as f:
                f.write(content)

            logger.info(f"成功创建 paper.md: {paper_md_path}")

        except Exception as e:
            # 如果写入失败，尝试创建最小化的空文件
            logger.error(f"创建 paper.md 失败: {e}")
            try:
                with open(paper_md_path, 'w', encoding='utf-8') as f:
                    f.write("")
                logger.info(f"创建了空的 paper.md: {paper_md_path}")
            except Exception as e2:
                logger.error(f"创建最小化 paper.md 也失败: {e2}")
