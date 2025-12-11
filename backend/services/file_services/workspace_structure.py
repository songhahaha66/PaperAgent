"""
工作空间目录结构统一管理
"""

from pathlib import Path
import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class WorkspaceStructureManager:
    """工作空间目录结构统一管理"""

    # 统一的目录结构定义
    WORKSPACE_DIRECTORIES = [
        "code",          # 所有代码文件
        "outputs",       # 所有输出文件
        "logs",          # 所有日志文件
        "temp",          # 临时文件
    ]

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
            # 创建目录结构
            for directory in cls.WORKSPACE_DIRECTORIES:
                dir_path = workspace_path / directory
                dir_path.mkdir(parents=True, exist_ok=True)

            # 创建初始文件
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
        # 创建初始元数据文件
        metadata = {
            "work_id": work_id,
            "created_at": str(datetime.now()),
            "status": "created",
            "progress": 0
        }

        metadata_file = workspace_path / "metadata.json"
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        # 创建初始对话历史文件
        chat_history = {
            "work_id": work_id,
            "session_id": f"{work_id}_session",
            "messages": [],
            "context": {
                "current_topic": "",
                "generated_files": [],
                "workflow_state": "created"
            },
            "created_at": str(datetime.now()),
            "version": "2.0"
        }
        chat_file = workspace_path / "chat_history.json"
        with open(chat_file, 'w', encoding='utf-8') as f:
            json.dump(chat_history, f, ensure_ascii=False, indent=2)
        
        # 根据输出模式创建相应的初始文件
        if output_mode == "markdown":
            # Markdown 模式：创建 paper.md 文件
            cls._create_paper_md(workspace_path, template_id)
            logger.info(f"Markdown 模式：已创建 paper.md")
        elif output_mode == "word":
            # Word 模式：创建空的 paper.docx 文件
            cls._create_paper_docx(workspace_path)
            logger.info(f"Word 模式：已创建 paper.docx")
        elif output_mode == "latex":
            # LaTeX 模式：暂时回退到 Markdown
            cls._create_paper_md(workspace_path, template_id)
            logger.info(f"LaTeX 模式暂未实现，回退到 Markdown 模式：已创建 paper.md")
        else:
            # 未知模式：默认创建 paper.md
            logger.warning(f"未知的输出模式 '{output_mode}'，默认创建 paper.md")
            cls._create_paper_md(workspace_path, template_id)
    
    @classmethod
    def _create_paper_docx(cls, workspace_path: Path) -> None:
        """创建初始的 paper.docx 文件
        
        Args:
            workspace_path: 工作空间路径
            
        Note:
            创建一个空的 Word 文档，供 AI 后续添加内容
        """
        paper_docx_path = workspace_path / "paper.docx"
        
        # 如果文件已存在，不覆盖
        if paper_docx_path.exists():
            logger.info(f"paper.docx 已存在，跳过创建: {paper_docx_path}")
            return
        
        try:
            from docx import Document
            
            # 创建空文档
            doc = Document()
            
            # 保存文档
            doc.save(str(paper_docx_path))
            
            logger.info(f"成功创建空的 paper.docx: {paper_docx_path}")
            
        except Exception as e:
            logger.error(f"创建 paper.docx 失败: {e}")
            # Word 文档创建失败不应该阻止工作空间创建
            # 只记录错误，让 AI 后续通过工具创建
    
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
            
            # 如果没有模板内容，使用默认内容
            if content is None:
                content = """# 论文标题

## 摘要

## 引言

## 方法

## 结果

## 讨论

## 结论

## 参考文献
"""
                logger.info("使用默认内容创建 paper.md")
            
            # 写入文件
            with open(paper_md_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            logger.info(f"成功创建 paper.md: {paper_md_path}")
            
        except Exception as e:
            # 如果写入失败，尝试创建最小化的空文件
            logger.error(f"创建 paper.md 失败: {e}")
            try:
                with open(paper_md_path, 'w', encoding='utf-8') as f:
                    f.write("# 论文标题\n")
                logger.info(f"创建了最小化的 paper.md: {paper_md_path}")
            except Exception as e2:
                logger.error(f"创建最小化 paper.md 也失败: {e2}")
