"""
WorkspaceFS — 工作空间文件系统抽象层

核心职责:
1. 路径解析: 所有对外接口只接受/返回 workspace-relative 路径，内部翻译为绝对路径
2. Manifest 管理: 记录每个文件的来源、可见性、关联 run_id、是否被引用
3. Run 管理: 为每次代码执行生成 run_id，组织 input/stdout/stderr/artifacts
"""
from services.file_services.workspace_layout import workspace_path

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

WORKSPACE_DIRECTORIES = [
    "code",
    "outputs",
    "outputs/intermediate",
    "data",
    "attachment",
    "template",
]

HIDDEN_PREFIXES = (".system", "runs")


class WorkspaceFS:
    """工作空间文件系统，统一管理路径、manifest 和 run 记录。"""

    def __init__(self, workspace_dir: str):
        self._root = Path(workspace_dir).absolute()
        from .workspace_layout import safe_join
        safe_join(self._root, "code")
        self._manifest_path = workspace_path(self._root, "manifest.json")
        self._manifest: Dict[str, Any] = self._load_manifest()

    @property
    def root(self) -> Path:
        return self._root

    # ------------------------------------------------------------------
    # 路径解析
    # ------------------------------------------------------------------

    def abs(self, rel_path: str) -> Path:
        """将 workspace-relative 路径转为绝对路径，并做安全检查。"""
        return workspace_path(self._root, rel_path)

    def rel(self, abs_path: str) -> str:
        """将绝对路径转为 workspace-relative 路径。"""
        path = Path(abs_path).absolute()
        from .workspace_layout import private_root, evidence_root
        base = (evidence_root(self._root) if path.is_relative_to(evidence_root(self._root)) else
                private_root(self._root) if path.is_relative_to(private_root(self._root)) else self._root)
        return path.relative_to(base).as_posix()

    def canonical_artifact(self, rel_path):
        meta=self.get_file_meta(rel_path) or {}
        if not rel_path.startswith('outputs/research/'):
            return self.abs(rel_path)
        if not meta.get('archive_path'):
            from ai_system.research.domain import retained_report
            return retained_report(self._root,rel_path)
        run_id=meta.get('source_run')
        if not run_id or not meta['archive_path'].startswith('runs/'+run_id+'/artifacts/'):
            raise ValueError('正式成果归档映射损坏')
        from ai_system.sandbox.evidence import checksum
        info=self.get_run_info(run_id) or {}
        if not info.get('evidence_archived') or info.get('status')!='success':raise ValueError('正式成果尚未封存')
        receipt=json.loads((self.run_dir(run_id)/'receipt.json').read_text())
        name=meta['archive_path'].removeprefix('runs/'+run_id+'/')
        if receipt.get('run_id')!=run_id or receipt.get('status')!='success' or receipt['files'].get(name)!=meta['sha256']:
            raise ValueError('正式成果归档回执不匹配')
        source=self.abs(meta['archive_path'])
        if checksum(source)!=meta['sha256']:raise ValueError('正式成果归档校验失败')
        return source

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
                    data = json.load(f)
                    if not isinstance(data, dict) or not isinstance(data.get('runs'), dict) or not isinstance(data.get('files'), dict):
                        raise ValueError('Invalid manifest structure')
                    return data
            except Exception as exc:
                raise ValueError("manifest.json 损坏，保留原文件，禁止继续执行") from exc
        return {
            "version": 1,
            "workspace_id": self._root.name,
            "files": {},
            "runs": {},
        }

    def _save_manifest(self) -> None:
        from .file_helper import atomic_write
        atomic_write(self._manifest_path, lambda f: f.write(json.dumps(self._manifest, ensure_ascii=False, indent=2).encode()))

    def register_file(
        self,
        rel_path: str,
        *,
        kind: str = "unknown",
        created_by: str = "system",
        source_run: Optional[str] = None,
        visibility: str = "user",
        archive_path: Optional[str] = None,
        sha256: Optional[str] = None,
    ) -> None:
        """在 manifest 中注册一个文件。"""
        existing = self._manifest["files"].get(rel_path, {})
        formal = rel_path.startswith("outputs/research/")
        if formal:
            if existing:
                if not all(existing.get(key) for key in ("source_run", "archive_path", "sha256")):
                    raise ValueError("正式成果归档绑定不完整，拒绝覆盖")
                for key, supplied in (("source_run", source_run), ("archive_path", archive_path), ("sha256", sha256)):
                    if supplied is not None and supplied != existing[key]:
                        raise ValueError("正式成果已绑定其他归档版本")
                source_run, archive_path, sha256 = (existing[key] for key in ("source_run", "archive_path", "sha256"))
            if not source_run or not archive_path or not archive_path.startswith(f"runs/{source_run}/artifacts/"):
                raise ValueError("正式成果必须同时登记运行和归档路径")
            self.abs(archive_path)
            if not sha256 or len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
                raise ValueError("正式成果必须登记有效的归档哈希")
        metadata = {
            "kind": kind,
            "created_by": created_by,
            "source_run": source_run,
            "visibility": visibility,
            "registered_at": datetime.now().isoformat(),
        }
        if formal:
            metadata.update(archive_path=archive_path, sha256=sha256)
        self._manifest["files"][rel_path] = metadata
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

        run_dir = workspace_path(self._root, "runs") / run_id
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
        return workspace_path(self._root, "runs/" + run_id)

    def run_rel(self, run_id: str, filename: str) -> str:
        """返回 run 内文件的 workspace-relative 路径。"""
        return f"runs/{run_id}/{filename}"

    def save_run_input(self, run_id: str, code: str) -> str:
        """将执行代码保存到 run 记录中，返回相对路径。"""
        dest = self.run_dir(run_id) / "input.py"
        self.write_text(self.run_rel(run_id, "input.py"), code)
        rel = self.run_rel(run_id, "input.py")
        self._manifest["runs"][run_id]["input"] = rel
        self._save_manifest()
        return rel

    def save_run_stdout(self, run_id: str, content: str) -> str:
        dest = self.run_dir(run_id) / "stdout.log"
        self.write_text(self.rel(dest), content)
        rel = self.run_rel(run_id, "stdout.log")
        self._manifest["runs"][run_id]["stdout"] = rel
        self._save_manifest()
        return rel

    def save_run_stderr(self, run_id: str, content: str) -> str:
        dest = self.run_dir(run_id) / "stderr.log"
        self.write_text(self.rel(dest), content)
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
        from .file_helper import atomic_write
        atomic_write(abs_path, lambda f: f.write(content.encode("utf-8")))
        if register_kwargs:
            self.register_file(rel_path, **register_kwargs)
        return rel_path

    def read_text(self, rel_path: str) -> str:
        abs_path = self.abs(rel_path)
        return abs_path.read_text(encoding="utf-8")

    def file_size(self, rel_path: str) -> int:
        return self.abs(rel_path).stat().st_size

