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
    """

    def __init__(self, stream_manager: StreamOutputManager):
        self.stream_manager = stream_manager
        # 从环境变量获取workspace路径
        self.workspace_dir = os.getenv("WORKSPACE_DIR")
        if not self.workspace_dir:
            self.workspace_dir = os.path.join(
                os.path.dirname(__file__), "workspace")
            logger.warning("CodeExecutor未找到WORKSPACE_DIR环境变量，使用默认路径")
        os.makedirs(self.workspace_dir, exist_ok=True)
        logger.info(f"CodeExecutor初始化完成，工作空间目录: {self.workspace_dir}")

    async def pyexec(self, python_code: str) -> str:
        """
        执行Python代码字符串并捕获其标准输出和错误。

        Args:
            python_code: 要执行的 Python 代码。

        Returns:
            执行结果的字符串（包括输出或错误信息）。
        """
        logger.info(f"执行Python代码，代码长度: {len(python_code)} 字符")
        await self.stream_manager.print_xml_open("call_exec")
        await self.stream_manager.print_content(python_code)
        await self.stream_manager.print_xml_close("call_exec")

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

            result = string_io.getvalue()
            logger.info(f"代码执行成功，输出长度: {len(result)} 字符")

            # 代码执行成功后，自动保存代码到文件
            saved_file_path = await self._save_successful_code(python_code, result)
            if saved_file_path:
                result += f"\n\n[代码已自动保存到: {saved_file_path}]"

            await self.stream_manager.print_xml_open("ret_exec")
            await self.stream_manager.print_content(result.strip())
            await self.stream_manager.print_xml_close("ret_exec")

            return result
        except Exception as e:
            error_message = f"代码执行出错: {str(e)}"
            logger.error(f"代码执行失败: {e}")
            await self.stream_manager.print_xml_open("ret_exec")
            await self.stream_manager.print_content(error_message)
            await self.stream_manager.print_xml_close("ret_exec")
            return error_message

    async def _save_successful_code(self, python_code: str, execution_result: str) -> str:
        """
        将成功执行的代码保存到文件
        
        Args:
            python_code: 要保存的Python代码
            execution_result: 代码执行结果
            
        Returns:
            保存的文件路径，如果保存失败返回空字符串
        """
        try:
            # 生成文件名：使用时间戳和代码内容的hash
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            code_hash = str(hash(python_code))[-8:]  # 取hash的后8位
            filename = f"code_{timestamp}_{code_hash}.py"
            filepath = os.path.join(self.workspace_dir, filename)
            
            # 创建代码文件内容，包含执行结果注释
            file_content = f"""# 自动生成的代码文件
# 生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
# 执行结果: {execution_result[:200]}{'...' if len(execution_result) > 200 else ''}

{python_code}

# 代码执行完成
"""
            
            # 保存文件
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(file_content)
            
            logger.info(f"代码已成功保存到文件: {filepath}")
            return filename
            
        except Exception as e:
            logger.error(f"保存代码文件失败: {e}")
            return ""

    def get_workspace_dir(self) -> str:
        """获取工作空间目录"""
        return self.workspace_dir

    def set_workspace_dir(self, workspace_dir: str):
        """设置工作空间目录"""
        self.workspace_dir = workspace_dir
        os.makedirs(workspace_dir, exist_ok=True)
        logger.info(f"工作空间目录已更新为: {workspace_dir}")

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
