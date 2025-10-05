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
import asyncio
from datetime import datetime
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
        
        # 设置工作空间路径，目录结构应由crud.py在work创建时创建
        
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
                await self.stream_manager.print_code_execution_call("开始执行Python代码")
            
            # 直接在子进程中执行代码
            result = await self._execute_code_directly(code_content)
            
            # 发送执行结果
            if self.stream_manager:
                await self.stream_manager.print_code_execution_result(result)
            
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
                await self.stream_manager.print_code_execution_call(f"开始执行文件: {file_path}")
            
            # 从文件读取代码并执行
            result = await self._execute_from_file(file_path)
            
            # 发送执行结果
            if self.stream_manager:
                await self.stream_manager.print_code_execution_result(result)
            
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
                if file_path.startswith(('code/', 'outputs/', 'logs/', 'temp/')):
                    # 如果已经是目录开头的路径，直接拼接workspace_dir
                    full_path = os.path.join(self.workspace_dir, file_path)
                else:
                    # 默认假设是相对于code目录的文件名
                    full_path = os.path.join(self.workspace_dir, "code", file_path)
            
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
            file_path = os.path.join(self.workspace_dir, "code", safe_filename)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(code_content)
            
            logger.info(f"代码已保存到文件: {file_path}")
            
            # 通过stream_manager发送工具调用通知到前端
            if self.stream_manager:
                try:
                    await self.stream_manager.print_code_execution_call(f"代码文件 {safe_filename} 保存成功")
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
        """在子进程中运行代码（异步版本，不阻塞事件循环）"""
        try:
            # 构建环境变量
            env = os.environ.copy()
            # 设置Python路径，包含工作空间的code目录
            current_pythonpath = env.get('PYTHONPATH', '')
            workspace_code = os.path.join(self.workspace_dir, "code")
            if current_pythonpath:
                env['PYTHONPATH'] = f"{workspace_code}{os.pathsep}{current_pythonpath}"
            else:
                env['PYTHONPATH'] = workspace_code

            env['WORKSPACE_DIR'] = self.workspace_dir
            # 设置编码环境变量
            env['PYTHONIOENCODING'] = 'utf-8'

            # 使用异步subprocess，不阻塞事件循环
            proc = await asyncio.create_subprocess_exec(
                sys.executable, temp_file,
                cwd=self.workspace_dir,  # 关键：设置工作目录
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )

            try:
                # 等待进程完成，但不阻塞事件循环
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

                # 处理执行结果
                if proc.returncode != 0:
                    error_output = stderr.decode('utf-8', errors='replace').strip() if stderr else "代码执行失败，无错误信息"
                    return f"执行错误 (返回码: {proc.returncode}):\n{error_output}"

                # 返回标准输出，处理可能的None值
                if stdout is None:
                    output = ""
                else:
                    output = stdout.decode('utf-8', errors='replace').strip()

                return output if output else "代码执行完成，无输出"

            except asyncio.TimeoutError:
                # 超时后终止进程
                proc.kill()
                await proc.wait()
                return "代码执行超时（60秒），请检查是否有无限循环或耗时操作"

        except Exception as e:
            logger.error(f"子进程执行异常: {e}")
            return f"子进程执行异常: {str(e)}"

    
    async def edit_code_file(self, filename: str, new_code_content: str) -> str:
        """
        修改已存在的代码文件

        Args:
            filename: 文件名（不需要.py后缀）
            new_code_content: 新的代码内容

        Returns:
            修改结果信息
        """
        try:
            # 参数验证
            if not new_code_content or not new_code_content.strip():
                return "错误：新代码内容不能为空"

            if not filename or not filename.strip():
                return "错误：文件名不能为空"

            # 清理文件名，移除不安全的字符
            safe_filename = "".join(
                c for c in filename if c.isalnum() or c in "._-")
            if not safe_filename:
                safe_filename = "code"

            # 确保文件名有.py后缀
            if not safe_filename.endswith('.py'):
                safe_filename = safe_filename + '.py'

            # 构建完整的文件路径
            code_dir = os.path.join(self.workspace_dir, "code")
            file_path = os.path.join(code_dir, safe_filename)

            # 检查文件是否存在
            if not os.path.exists(file_path):
                return f"错误：文件 {safe_filename} 不存在，无法修改。请先使用 save_and_execute 创建文件。"

            # 备份原文件
            backup_path = file_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            with open(file_path, 'r', encoding='utf-8') as f:
                original_content = f.read()

            with open(backup_path, 'w', encoding='utf-8') as f:
                f.write(original_content)

            # 写入新代码
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_code_content)

            logger.info(f"代码文件已修改: {file_path}")

            # 通过stream_manager发送工具调用通知到前端
            if self.stream_manager:
                try:
                    # 发送工具调用开始通知
                    await self.stream_manager.send_json_block("code_agent_tool_call", f"CodeAgent正在执行工具调用: edit_code_file")

                    # 发送工具调用结果通知
                    await self.stream_manager.send_json_block("code_agent_tool_result", f"代码文件 {safe_filename} 修改成功")
                except Exception as e:
                    logger.warning(f"发送工具调用通知失败: {e}")

            # 返回相对路径，这样execute_file就能正确找到文件
            relative_path = os.path.join("code", safe_filename)

            return f"代码文件 {safe_filename} 已成功修改\n文件路径: {file_path}\n相对路径: {relative_path}\n新代码长度: {len(new_code_content)} 字符\n原文件已备份到: {os.path.basename(backup_path)}"

        except Exception as e:
            error_msg = f"修改代码文件失败: {str(e)}"
            logger.error(error_msg)

            # 发送错误通知到前端
            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block("code_agent_tool_error", f"工具调用失败: {error_msg}")
                except Exception as ws_error:
                    logger.warning(f"发送错误通知失败: {ws_error}")

            return error_msg

    async def list_code_files(self) -> str:
        """
        列出工作空间中的所有代码文件

        Returns:
            代码文件列表信息
        """
        try:
            code_dir = os.path.join(self.workspace_dir, "code")

            if not os.path.exists(code_dir):
                return "代码文件目录不存在，还没有创建任何代码文件。"

            files = os.listdir(code_dir)
            python_files = [f for f in files if f.endswith('.py')]

            if not python_files:
                return "代码文件目录为空，还没有创建任何Python代码文件。"

            # 通过stream_manager发送工具调用通知到前端
            if self.stream_manager:
                try:
                    # 发送工具调用开始通知
                    await self.stream_manager.send_json_block("code_agent_tool_call", f"CodeAgent正在执行工具调用: list_code_files")

                    # 发送工具调用结果通知
                    await self.stream_manager.send_json_block("code_agent_tool_result", f"找到 {len(python_files)} 个Python代码文件")
                except Exception as e:
                    logger.warning(f"发送工具调用通知失败: {e}")

            # 构建文件列表信息
            file_info = []
            for file in python_files:
                file_path = os.path.join(code_dir, file)
                try:
                    file_size = os.path.getsize(file_path)
                    file_info.append(f"- {file} ({file_size} bytes)")
                except OSError:
                    file_info.append(f"- {file} (无法获取文件大小)")

            return f"代码文件目录: {code_dir}\n找到 {len(python_files)} 个Python代码文件:\n" + "\n".join(file_info)

        except Exception as e:
            error_msg = f"列出代码文件失败: {str(e)}"
            logger.error(error_msg)

            # 发送错误通知到前端
            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block("code_agent_tool_error", f"工具调用失败: {error_msg}")
                except Exception as ws_error:
                    logger.warning(f"发送错误通知失败: {ws_error}")

            return error_msg
