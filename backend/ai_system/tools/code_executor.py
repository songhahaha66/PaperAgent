"""
代码执行器 - 简洁版本
专门用于安全执行Python代码
"""

import logging
import os
import io
import contextlib
from typing import Dict, Any, Optional
import matplotlib
matplotlib.use('Agg')  # 非交互式后端
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)


class CodeExecutor:
    """简洁的Python代码执行器"""

    def __init__(self, stream_manager=None, workspace_dir: Optional[str] = None):
        self.stream_manager = stream_manager
        
        # 设置工作空间目录
        if workspace_dir:
            # 如果传入了完整的工作空间路径（包含work_id）
            self.workspace_dir = os.path.abspath(workspace_dir)
        else:
            # 必须传入工作空间目录，不能使用默认路径
            raise ValueError("必须传入workspace_dir参数，指定具体的工作空间目录（包含work_id）")
        
        # 创建必要目录 - 保持现有结构
        os.makedirs(self.workspace_dir, exist_ok=True)
        os.makedirs(os.path.join(self.workspace_dir, "code_files"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_dir, "execution_logs"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_dir, "outputs", "plots"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_dir, "outputs", "data"), exist_ok=True)
        
        logger.info(f"CodeExecutor初始化完成，工作空间: {self.workspace_dir}")

    async def pyexec_file(self, code_file_path: str) -> str:
        """执行Python代码文件"""
        try:
            # 构建完整路径
            if os.path.isabs(code_file_path):
                full_path = code_file_path
            else:
                full_path = os.path.join(self.workspace_dir, code_file_path)
            
            # 标准化路径
            full_path = os.path.normpath(full_path)
            
            # 安全检查
            workspace_abs = os.path.abspath(self.workspace_dir)
            if not full_path.startswith(workspace_abs):
                return f"错误：文件路径 {code_file_path} 不在工作空间内"
            
            if not os.path.exists(full_path):
                return f"错误：文件不存在 {full_path}"
            
            if not full_path.endswith('.py'):
                return f"错误：文件 {full_path} 不是Python文件"
            
            # 读取并执行代码
            with open(full_path, 'r', encoding='utf-8') as f:
                code = f.read()
            
            result = await self._execute_code(code)
            return result
            
        except Exception as e:
            logger.error(f"执行代码文件失败: {e}")
            return f"执行失败: {str(e)}"

    async def _execute_code(self, code: str) -> str:
        """执行Python代码字符串"""
        try:
            # 设置环境
            globals_dict = {
                '__builtins__': __builtins__,
                'workspace_dir': self.workspace_dir,
                'os': os,
                'plt': plt,
            }
            
            # 尝试导入numpy
            try:
                import numpy
                globals_dict['numpy'] = numpy
            except ImportError:
                pass
            
            # 清除之前的图表
            plt.close('all')
            
            # 执行代码并捕获输出
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                exec(code, globals_dict)
            
            # 保存图表
            plot_files = self._save_plots()
            
            # 构建结果
            result = output.getvalue().strip()
            if plot_files:
                result += f"\n\n图表已保存: {', '.join(plot_files.values())}"
            
            return result if result else "代码执行完成，无输出"
            
        except Exception as e:
            logger.error(f"代码执行错误: {e}")
            return f"代码执行错误: {str(e)}"

    def _save_plots(self) -> Dict[str, str]:
        """保存matplotlib图表"""
        plot_files = {}
        try:
            if plt.get_fignums():
                plots_dir = os.path.join(self.workspace_dir, "outputs", "plots")
                # 使用工作空间目录名作为标识
                workspace_id = os.path.basename(self.workspace_dir)
                if not workspace_id or workspace_id == "workspaces":
                    workspace_id = "default"
                
                for fig_num in plt.get_fignums():
                    fig = plt.figure(fig_num)
                    filename = f"plot_{workspace_id}_{fig_num}.png"
                    filepath = os.path.join(plots_dir, filename)
                    fig.savefig(filepath, dpi=300, bbox_inches='tight')
                    plot_files[f"plot_{fig_num}"] = f"outputs/plots/{filename}"
                
                plt.close('all')
        except Exception as e:
            logger.warning(f"保存图表失败: {e}")
        
        return plot_files

    def get_workspace_dir(self) -> str:
        """获取工作空间目录"""
        return self.workspace_dir

    def list_workspace_files(self) -> list:
        """列出工作空间文件"""
        try:
            return os.listdir(self.workspace_dir) if os.path.exists(self.workspace_dir) else []
        except Exception:
            return []
