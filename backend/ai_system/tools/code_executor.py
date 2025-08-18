"""
代码执行器
专门用于安全执行Python代码的类
"""

import logging
import asyncio
import os
import io
import contextlib
import json
from typing import Dict, Any
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # 使用非交互式后端
from datetime import datetime

from ..core.stream_manager import StreamOutputManager

logger = logging.getLogger(__name__)


class CodeExecutor:
    """
    一个专门用于安全执行 Python 代码的类。
    简化版本：只保存代码、输出结果和日志
    """

    def __init__(self, stream_manager: StreamOutputManager):
        self.stream_manager = stream_manager
        # 从环境变量获取workspace路径
        workspace_dir = os.getenv("WORKSPACE_DIR")
        if not workspace_dir:
            logger.error("CodeExecutor未找到WORKSPACE_DIR环境变量，请确保环境已正确初始化")
            raise RuntimeError("工作空间目录未设置")
        self.workspace_dir = workspace_dir
        logger.info(f"CodeExecutor初始化完成，工作空间目录: {workspace_dir}")

    async def execute_code_file(self, code_file_path: str) -> Dict[str, Any]:
        """
        执行指定的Python代码文件并返回结果
        
        Args:
            code_file_path: 要执行的代码文件路径
            
        Returns:
            执行结果字典，包含状态、输出、错误等信息
        """
        logger.info(f"执行代码文件: {code_file_path}")
        
        try:
            # 读取代码文件内容
            with open(code_file_path, 'r', encoding='utf-8') as f:
                python_code = f.read()
            
            # 执行代码
            result = await self._execute_python_code(python_code)
            
            # 保存执行日志
            await self._save_execution_log(code_file_path, result)
            
            return result
            
        except Exception as e:
            error_result = {
                "success": False,
                "error": f"文件执行失败: {str(e)}",
                "output": "",
                "execution_time": datetime.now().isoformat()
            }
            await self._save_execution_log(code_file_path, error_result)
            return error_result

    async def _execute_python_code(self, python_code: str) -> Dict[str, Any]:
        """
        执行Python代码字符串并捕获其标准输出和错误
        
        Args:
            python_code: 要执行的 Python 代码
            
        Returns:
            执行结果字典
        """
        logger.info(f"执行Python代码，代码长度: {len(python_code)} 字符")
        
        start_time = datetime.now()
        
        try:
            # 创建一个包含workspace路径的全局环境
            globals_dict = {
                '__builtins__': __builtins__,
                'workspace_dir': self.workspace_dir,
                'os': os,
                'plt': plt,
                'matplotlib': matplotlib,
            }

            # 尝试导入numpy
            try:
                import numpy
                globals_dict['numpy'] = numpy
            except ImportError:
                logger.warning("numpy未安装，跳过导入")

            # 设置matplotlib后端和配置
            plt.switch_backend('Agg')
            plt.rcParams['figure.figsize'] = (10, 6)
            plt.rcParams['font.size'] = 12

            # 使用 io.StringIO 捕获 exec 的所有输出
            string_io = io.StringIO()
            with contextlib.redirect_stdout(string_io), contextlib.redirect_stderr(string_io):
                # 在包含workspace_dir的环境中执行代码
                exec(python_code, globals_dict)

            output = string_io.getvalue()
            execution_time = (datetime.now() - start_time).total_seconds()
            
            # 检查是否有图表需要保存
            plot_files = await self._save_plots()
            
            result = {
                "success": True,
                "output": output.strip(),
                "execution_time": execution_time,
                "plot_files": plot_files,
                "timestamp": datetime.now().isoformat()
            }
            
            logger.info(f"代码执行成功，输出长度: {len(output)} 字符，执行时间: {execution_time:.2f}秒")
            return result
            
        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            error_result = {
                "success": False,
                "error": str(e),
                "output": "",
                "execution_time": execution_time,
                "plot_files": {},
                "timestamp": datetime.now().isoformat()
            }
            
            logger.error(f"代码执行失败: {e}")
            return error_result

    async def _save_plots(self) -> Dict[str, str]:
        """
        保存matplotlib图表到plots目录
        
        Returns:
            保存的图片文件路径字典
        """
        plot_files = {}
        
        try:
            if plt.get_fignums():  # 如果有图表
                plots_dir = os.path.join(self.workspace_dir, "outputs", "plots")
                os.makedirs(plots_dir, exist_ok=True)
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                
                for fig_num in plt.get_fignums():
                    fig = plt.figure(fig_num)
                    plot_filename = f"plot_{timestamp}_{fig_num}.png"
                    plot_filepath = os.path.join(plots_dir, plot_filename)
                    
                    fig.savefig(plot_filepath, dpi=300, bbox_inches='tight')
                    plot_files[f"plot_{fig_num}"] = f"outputs/plots/{plot_filename}"
                    logger.info(f"图表已保存到: {plot_filepath}")
                
                plt.close('all')  # 关闭所有图表
                
        except Exception as e:
            logger.warning(f"保存图表失败: {e}")
        
        return plot_files

    async def _save_execution_log(self, code_file_path: str, result: Dict[str, Any]):
        """
        保存执行日志到execution_logs目录
        
        Args:
            code_file_path: 代码文件路径
            result: 执行结果
        """
        try:
            logs_dir = os.path.join(self.workspace_dir, "execution_logs")
            os.makedirs(logs_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_filename = f"execution_{timestamp}.log"
            log_filepath = os.path.join(logs_dir, log_filename)
            
            # 构建日志内容
            log_content = {
                "timestamp": result.get("timestamp", datetime.now().isoformat()),
                "code_file": code_file_path,
                "execution_status": "success" if result.get("success") else "failed",
                "execution_time": result.get("execution_time", 0),
                "output": result.get("output", ""),
                "error": result.get("error", ""),
                "plot_files": result.get("plot_files", {}),
                "log_file": f"execution_logs/{log_filename}"
            }
            
            with open(log_filepath, 'w', encoding='utf-8') as f:
                json.dump(log_content, f, ensure_ascii=False, indent=2)
            
            logger.info(f"执行日志已保存到: {log_filepath}")
            
        except Exception as e:
            logger.error(f"保存执行日志失败: {e}")

    def get_workspace_dir(self) -> str:
        """获取工作空间目录"""
        return self.workspace_dir

    def set_workspace_dir(self, workspace_dir: str):
        """设置工作空间目录"""
        self.workspace_dir = workspace_dir
        os.makedirs(self.workspace_dir, exist_ok=True)
        logger.info(f"工作空间目录已更新为: {workspace_dir}")
        
        # 不需要重复创建目录结构，environment.py中已经处理了

    def list_workspace_files(self) -> list:
        """列出工作空间中的文件"""
        if not os.path.exists(self.workspace_dir):
            return []
        
        try:
            return os.listdir(self.workspace_dir)
        except Exception as e:
            logger.error(f"列出工作空间文件失败: {e}")
            return []

    def get_file_info(self, filename: str) -> Dict[str, Any]:
        """获取文件信息"""
        filepath = os.path.join(self.workspace_dir, filename)
        if not os.path.exists(filepath):
            return {"exists": False}
        
        try:
            stat = os.stat(filepath)
            return {
                "exists": True,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "is_file": os.path.isfile(filepath),
                "is_dir": os.path.isdir(filepath)
            }
        except Exception as e:
            logger.error(f"获取文件信息失败: {e}")
            return {"exists": False, "error": str(e)}
