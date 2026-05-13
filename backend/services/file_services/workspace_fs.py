"""
WorkspaceFS — 工作空间文件系统抽象层

核心职责:
1. 路径解析: 所有对外接口只接受/返回 workspace-relative 路径，内部翻译为绝对路径
2. Manifest 管理: 记录每个文件的来源、可见性、关联 run_id、是否被引用
3. Run 管理: 为每次代码执行生成 run_id，组织 input/stdout/stderr/artifacts
4. 产物晋升: 将 run artifacts 晋升到 outputs/
"""

import json
import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

WORKSPACE_DIRECTORIES = [
    "code",
    "outputs",
    "runs",
    ".system",
    ".system/temp",
]

HIDDEN_PREFIXES = (".system", "runs")


class WorkspaceFS:
    """工作空间文件系统，统一管理路径、manifest 和 run 记录。"""

    def __init__(self, workspace_dir: str):
        self._root = Path(workspace_dir).resolve()
        self._manifest_path = self._root / "manifest.json"
        self._manifest: Dict[str, Any] = self._load_manifest()

    @property
    def root(self) -> Path:
        return self._root

    # ------------------------------------------------------------------
    # 路径解析
    # ------------------------------------------------------------------

    def abs(self, rel_path: str) -> Path:
        """将 workspace-relative 路径转为绝对路径，并做安全检查。"""
        resolved = (self._root / rel_path).resolve()
        if not str(resolved).startswith(str(self._root)):
            raise ValueError(f"路径 {rel_path} 超出工作空间范围")
        return resolved

    def rel(self, abs_path: str) -> str:
        """将绝对路径转为 workspace-relative 路径。"""
        return str(Path(abs_path).resolve().relative_to(self._root))

    def exists(self, rel_path: str) -> bool:
        return self.abs(rel_path).exists()

    def ensure_dir(self, rel_path: str) -> Path:
        p = self.abs(rel_path)
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def _load_manifest(self) -> Dict[str, Any]:
        if self._manifest_path.exists():
            try:
                with open(self._manifest_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                logger.warning("manifest.json 损坏，重新创建")
        return {
            "version": 1,
            "workspace_id": self._root.name,
            "files": {},
            "runs": {},
        }

    def _save_manifest(self) -> None:
        try:
            with open(self._manifest_path, "w", encoding="utf-8") as f:
                json.dump(self._manifest, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 manifest 失败: {e}")

    def register_file(
        self,
        rel_path: str,
        *,
        kind: str = "unknown",
        created_by: str = "system",
        source_run: Optional[str] = None,
        visibility: str = "user",
    ) -> None:
        """在 manifest 中注册一个文件。"""
        self._manifest["files"][rel_path] = {
            "kind": kind,
            "created_by": created_by,
            "source_run": source_run,
            "visibility": visibility,
            "registered_at": datetime.now().isoformat(),
        }
        self._save_manifest()

    def unregister_file(self, rel_path: str) -> None:
        self._manifest["files"].pop(rel_path, None)
        self._save_manifest()

    def get_file_meta(self, rel_path: str) -> Optional[Dict]:
        return self._manifest["files"].get(rel_path)

    def list_visible_files(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出用户可见的文件，可按 kind 过滤。"""
        result = []
        for path, meta in self._manifest["files"].items():
            if meta.get("visibility") != "user":
                continue
            if category and meta.get("kind") != category:
                continue
            result.append({"path": path, **meta})
        return result

    # ------------------------------------------------------------------
    # Run 管理
    # ------------------------------------------------------------------

    def create_run(self) -> str:
        """创建一次新的执行记录，返回 run_id。"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_uuid = uuid.uuid4().hex[:6]
        run_id = f"run_{ts}_{short_uuid}"

        run_dir = self._root / "runs" / run_id
        (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)

        self._manifest["runs"][run_id] = {
            "status": "created",
            "created_at": datetime.now().isoformat(),
            "input": None,
            "stdout": None,
            "stderr": None,
            "artifacts": [],
        }
        self._save_manifest()
        return run_id

    def run_dir(self, run_id: str) -> Path:
        return self._root / "runs" / run_id

    def run_rel(self, run_id: str, filename: str) -> str:
        """返回 run 内文件的 workspace-relative 路径。"""
        return f"runs/{run_id}/{filename}"

    def save_run_input(self, run_id: str, code: str) -> str:
        """将执行代码保存到 run 记录中，返回相对路径。"""
        dest = self.run_dir(run_id) / "input.py"
        dest.write_text(code, encoding="utf-8")
        rel = self.run_rel(run_id, "input.py")
        self._manifest["runs"][run_id]["input"] = rel
        self._save_manifest()
        return rel

    def save_run_stdout(self, run_id: str, content: str) -> str:
        dest = self.run_dir(run_id) / "stdout.log"
        dest.write_text(content, encoding="utf-8")
        rel = self.run_rel(run_id, "stdout.log")
        self._manifest["runs"][run_id]["stdout"] = rel
        self._save_manifest()
        return rel

    def save_run_stderr(self, run_id: str, content: str) -> str:
        dest = self.run_dir(run_id) / "stderr.log"
        dest.write_text(content, encoding="utf-8")
        rel = self.run_rel(run_id, "stderr.log")
        self._manifest["runs"][run_id]["stderr"] = rel
        self._save_manifest()
        return rel

    def register_run_artifact(self, run_id: str, filename: str) -> str:
        """注册一个 run 产生的 artifact，返回相对路径。"""
        rel = f"runs/{run_id}/artifacts/{filename}"
        artifacts = self._manifest["runs"][run_id].setdefault("artifacts", [])
        if rel not in artifacts:
            artifacts.append(rel)
        self._save_manifest()
        return rel

    def finish_run(self, run_id: str, status: str = "success") -> None:
        if run_id in self._manifest["runs"]:
            self._manifest["runs"][run_id]["status"] = status
            self._manifest["runs"][run_id]["finished_at"] = datetime.now().isoformat()
            self._save_manifest()

    def get_run_info(self, run_id: str) -> Optional[Dict]:
        return self._manifest["runs"].get(run_id)

    # ------------------------------------------------------------------
    # 产物晋升
    # ------------------------------------------------------------------

    def promote_artifact(
        self, run_id: str, artifact_name: str, output_name: Optional[str] = None
    ) -> str:
        """将 run artifact 晋升到 outputs/ 目录。

        Returns:
            晋升后的 outputs/ 相对路径
        """
        src_rel = f"runs/{run_id}/artifacts/{artifact_name}"
        src_abs = self.abs(src_rel)
        if not src_abs.exists():
            raise FileNotFoundError(f"Artifact 不存在: {src_rel}")

        dest_name = output_name or artifact_name
        dest_rel = f"outputs/{dest_name}"
        dest_abs = self.abs(dest_rel)
        dest_abs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src_abs), str(dest_abs))

        self.register_file(
            dest_rel,
            kind="artifact",
            created_by="ai",
            source_run=run_id,
            visibility="user",
        )
        logger.info(f"产物晋升: {src_rel} -> {dest_rel}")
        return dest_rel

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    def init_structure(self) -> None:
        """确保工作空间基本目录存在。"""
        for d in WORKSPACE_DIRECTORIES:
            (self._root / d).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def write_text(self, rel_path: str, content: str, **register_kwargs) -> str:
        """写入文本文件并注册到 manifest，返回相对路径。"""
        abs_path = self.abs(rel_path)
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")
        if register_kwargs:
            self.register_file(rel_path, **register_kwargs)
        return rel_path

    def read_text(self, rel_path: str) -> str:
        abs_path = self.abs(rel_path)
        return abs_path.read_text(encoding="utf-8")

    def file_size(self, rel_path: str) -> int:
        return self.abs(rel_path).stat().st_size
