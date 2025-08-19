"""
代码执行器 - 重构版本
专门用于安全执行Python代码，每个work_id在独立进程中执行
提供三个明确的执行模式供AI调用
"""

import logging
import os
import sys
import tempfile
import subprocess
import time
from typing import Dict, Any, Optional
import matplotlib
matplotlib.use('Agg')  # 非交互式后端
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)


class CodeExecutor:
    """重构后的Python代码执行器，提供明确的执行模式"""

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

    async def execute_code(self, code_content: str) -> str:
        """
        执行Python代码内容
        
        Args:
            code_content: 要执行的Python代码字符串
            
        Returns:
            执行结果
        """
        try:
            # 发送执行开始通知
            if self.stream_manager:
                await self.stream_manager.print_xml_open("execution_start")
                await self.stream_manager.print_content("开始执行Python代码")
                await self.stream_manager.print_xml_close("execution_start")
            
            # 直接在子进程中执行代码
            result = await self._execute_code_directly(code_content)
            
            # 发送执行完成通知
            if self.stream_manager:
                await self.stream_manager.print_xml_open("execution_complete")
                await self.stream_manager.print_content("代码执行完成")
                await self.stream_manager.print_xml_close("execution_complete")
            
            return result
            
        except Exception as e:
            error_msg = f"执行代码失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    async def save_and_execute(self, code_content: str, filename: str) -> str:
        """
        保存代码到文件并立即执行
        
        Args:
            code_content: 要保存和执行的Python代码
            filename: 文件名（不需要.py后缀）
            
        Returns:
            保存结果 + 执行结果
        """
        try:
            # 1. 保存代码
            save_result = await self._save_code_only(code_content, filename)
            
            # 2. 执行代码
            execute_result = await self._execute_code_directly(code_content)
            
            # 3. 返回组合结果
            return f"{save_result}\n\n=== 执行结果 ===\n{execute_result}"
            
        except Exception as e:
            error_msg = f"保存并执行代码失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    async def execute_file(self, file_path: str) -> str:
        """
        执行指定的Python文件
        
        Args:
            file_path: 文件路径（相对于工作空间或绝对路径）
            
        Returns:
            执行结果
        """
        try:
            # 发送执行开始通知
            if self.stream_manager:
                await self.stream_manager.print_xml_open("execution_start")
                await self.stream_manager.print_content(f"开始执行文件: {file_path}")
                await self.stream_manager.print_xml_close("execution_start")
            
            # 从文件读取代码并执行
            result = await self._execute_from_file(file_path)
            
            # 发送执行完成通知
            if self.stream_manager:
                await self.stream_manager.print_xml_open("execution_complete")
                await self.stream_manager.print_content(f"文件 {file_path} 执行完成")
                await self.stream_manager.print_xml_close("execution_complete")
            
            return result
            
        except Exception as e:
            error_msg = f"执行文件失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    async def _execute_code_directly(self, code: str) -> str:
        """直接在子进程中执行代码"""
        try:
            # 创建临时代码文件
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                # 添加必要的导入和设置
                enhanced_code = self._prepare_code_for_subprocess(code)
                f.write(enhanced_code)
                temp_file = f.name
            
            try:
                # 在子进程中执行，设置工作目录为workspace_dir
                result = await self._run_in_subprocess(temp_file)
                return result
                
            finally:
                # 清理临时文件
                try:
                    os.unlink(temp_file)
                except Exception as e:
                    logger.warning(f"清理临时文件失败: {e}")
                    
        except Exception as e:
            logger.error(f"直接执行代码错误: {e}")
            return f"直接执行代码错误: {str(e)}"

    async def _execute_from_file(self, file_path: str) -> str:
        """从文件执行代码"""
        try:
            # 构建完整路径
            if os.path.isabs(file_path):
                full_path = file_path
            else:
                # 相对路径处理
                if file_path.startswith('code_files/'):
                    # 如果已经是code_files/开头的路径，直接拼接workspace_dir
                    full_path = os.path.join(self.workspace_dir, file_path)
                elif file_path.startswith(('outputs/', 'execution_logs/')):
                    # 其他目录的路径
                    full_path = os.path.join(self.workspace_dir, file_path)
                else:
                    # 默认假设是相对于code_files目录的文件名
                    full_path = os.path.join(self.workspace_dir, "code_files", file_path)
            
            # 标准化路径
            full_path = os.path.normpath(full_path)
            
            # 安全检查 - 确保文件在工作空间内
            workspace_abs = os.path.abspath(self.workspace_dir)
            if not full_path.startswith(workspace_abs):
                return f"错误：文件路径 {file_path} 不在工作空间内\n工作空间: {workspace_abs}\n尝试路径: {full_path}"
            
            if not os.path.exists(full_path):
                return f"错误：文件不存在 {full_path}\n请检查文件路径是否正确"
            
            if not full_path.endswith('.py'):
                return f"错误：文件 {full_path} 不是Python文件"
            
            logger.info(f"准备执行文件: {full_path}")
            
            # 读取文件内容
            with open(full_path, 'r', encoding='utf-8') as f:
                code_content = f.read()
            
            # 直接执行代码内容
            return await self._execute_code_directly(code_content)
            
        except Exception as e:
            logger.error(f"从文件执行代码失败: {e}")
            return f"从文件执行失败: {str(e)}"

    async def _save_code_only(self, code_content: str, filename: str) -> str:
        """仅保存代码到文件"""
        try:
            # 清理文件名
            safe_filename = "".join(c for c in filename if c.isalnum() or c in "._-")
            if not safe_filename:
                safe_filename = f"code_{int(time.time())}"
            
            if not safe_filename.endswith('.py'):
                safe_filename += '.py'
            
            # 保存文件
            file_path = os.path.join(self.workspace_dir, "code_files", safe_filename)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(code_content)
            
            logger.info(f"代码已保存到文件: {file_path}")
            
            # 通过stream_manager发送工具调用通知到前端
            if self.stream_manager:
                try:
                    await self.stream_manager.print_xml_open("tool_call")
                    await self.stream_manager.print_content(f"代码文件 {safe_filename} 保存成功")
                    await self.stream_manager.print_xml_close("tool_call")
                except Exception as e:
                    logger.warning(f"发送工具调用通知失败: {e}")
            
            return f"代码已成功保存到文件: {safe_filename}\n文件路径: {file_path}\n代码长度: {len(code_content)} 字符"
            
        except Exception as e:
            logger.error(f"保存代码失败: {e}")
            return f"保存代码失败: {str(e)}"

    def _prepare_code_for_subprocess(self, code: str) -> str:
        """为子进程执行准备代码"""
        # 添加必要的导入和设置
        header = f'''# -*- coding: utf-8 -*-
import os
import sys
import matplotlib
matplotlib.use('Agg')  # 非交互式后端
import matplotlib.pyplot as plt

# 设置工作空间目录
os.chdir(r"{self.workspace_dir}")  # 改变当前工作目录

# 尝试导入常用科学计算库
try:
    import numpy as np
except ImportError:
    pass

try:
    import scipy
except ImportError:
    pass

try:
    import pandas as pd
except ImportError:
    pass

# 设置matplotlib输出目录
plt_output_dir = "outputs/plots"
os.makedirs(plt_output_dir, exist_ok=True)

# 用户代码开始
'''
        
        # 添加代码执行后的图表保存逻辑
        footer = '''

# 保存所有图表
plot_files = []
for fig_num in plt.get_fignums():
    fig = plt.figure(fig_num)
    filename = f"plot_{fig_num}.png"
    filepath = os.path.join(plt_output_dir, filename)
    fig.savefig(filepath, dpi=300, bbox_inches='tight')
    plot_files.append(f"outputs/plots/{filename}")
    plt.close(fig)

# 输出图表保存信息
if plot_files:
    print(f"\\n图表已保存: {', '.join(plot_files)}")
'''
        
        return header + code + footer

    async def _run_in_subprocess(self, temp_file: str) -> str:
        """在子进程中运行代码"""
        try:
            # 构建环境变量
            env = os.environ.copy()
            # 设置Python路径，包含工作空间的code_files目录
            current_pythonpath = env.get('PYTHONPATH', '')
            workspace_code_files = os.path.join(self.workspace_dir, "code_files")
            if current_pythonpath:
                env['PYTHONPATH'] = f"{workspace_code_files}{os.pathsep}{current_pythonpath}"
            else:
                env['PYTHONPATH'] = workspace_code_files
            
            env['WORKSPACE_DIR'] = self.workspace_dir
            # 设置编码环境变量
            env['PYTHONIOENCODING'] = 'utf-8'
            
            # 在子进程中执行，设置工作目录为workspace_dir
            result = subprocess.run(
                [sys.executable, temp_file],
                cwd=self.workspace_dir,  # 关键：设置工作目录
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=60,  # 60秒超时
                env=env,
                errors='replace'  # 处理编码错误
            )
            
            # 处理执行结果
            if result.returncode != 0:
                error_output = result.stderr.strip() if result.stderr else "代码执行失败，无错误信息"
                return f"执行错误 (返回码: {result.returncode}):\n{error_output}"
            
            # 返回标准输出，处理可能的None值
            if result.stdout is None:
                output = ""
            else:
                output = result.stdout.strip()
            
            return output if output else "代码执行完成，无输出"
            
        except subprocess.TimeoutExpired:
            return "代码执行超时（60秒），请检查是否有无限循环或耗时操作"
        except subprocess.CalledProcessError as e:
            return f"子进程执行失败: {str(e)}"
        except Exception as e:
            logger.error(f"子进程执行异常: {e}")
            return f"子进程执行异常: {str(e)}"

    # 保留兼容性方法，但标记为过时
    async def pyexec_file(self, code_file_path: str) -> str:
        """过时方法：使用execute_file替代"""
        logger.warning("pyexec_file方法已过时，请使用execute_file方法")
        return await self.execute_file(code_file_path)

    def _save_plots(self) -> Dict[str, str]:
        """过时方法：图表保存现在在子进程中完成"""
        logger.info("_save_plots方法已过时，图表保存现在在子进程中完成")
        return {}

    def get_workspace_dir(self) -> str:
        """获取工作空间目录"""
        return self.workspace_dir

    def list_workspace_files(self) -> list:
        """列出工作空间文件"""
        try:
            return os.listdir(self.workspace_dir) if os.path.exists(self.workspace_dir) else []
        except Exception:
            return []
