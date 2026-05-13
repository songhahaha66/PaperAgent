"""
代码执行器 - 重构版本
专门用于安全执行Python代码，每个work_id在独立进程中执行
提供三个明确的执行模式供AI调用
"""

import logging
import os
import re
import sys
import tempfile
import subprocess
import time
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional
from services.file_services.workspace_fs import WorkspaceFS
import matplotlib
matplotlib.use('Agg')
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

        self.fs = WorkspaceFS(self.workspace_dir)
        self.fs.init_structure()

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
            if self.stream_manager:
                await self.stream_manager.print_code_execution_call("开始执行Python代码")

            run_id = self.fs.create_run()
            self.fs.save_run_input(run_id, code_content)

            result = await self._execute_code_directly(code_content, run_id)

            self.fs.save_run_stdout(run_id, result)
            self._register_run_artifacts(run_id)
            status = "error" if any(kw in result for kw in ("执行错误", "执行失败", "异常")) else "success"
            self.fs.finish_run(run_id, status)
            
            if self.stream_manager:
                await self.stream_manager.print_code_execution_result(result)
                await self.stream_manager.send_json_block("file_changed", "code_execution")
            
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
            save_result = await self._save_code_only(code_content, filename)

            run_id = self.fs.create_run()
            self.fs.save_run_input(run_id, code_content)

            execute_result = await self._execute_code_directly(code_content, run_id)

            self.fs.save_run_stdout(run_id, execute_result)
            self._register_run_artifacts(run_id)
            status = "error" if any(kw in execute_result for kw in ("执行错误", "执行失败", "异常")) else "success"
            self.fs.finish_run(run_id, status)

            return f"{save_result}\n\n=== 执行结果 ===\n{execute_result}"
            
        except Exception as e:
            error_msg = f"保存并执行代码失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    async def execute_file(self, file_path: str) -> str:
        """
        执行指定的Python文件
        
        Args:
            file_path: 文件路径（相对于工作空间）
            
        Returns:
            执行结果
        """
        try:
            if self.stream_manager:
                await self.stream_manager.print_code_execution_call(f"开始执行文件: {file_path}")
            
            result = await self._execute_from_file(file_path)
            
            if self.stream_manager:
                await self.stream_manager.print_code_execution_result(result)
            
            return result
            
        except Exception as e:
            error_msg = f"执行文件失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    async def _execute_code_directly(self, code: str, run_id: str) -> str:
        """直接在子进程中执行代码，所有产物写入 runs/<run_id>/"""
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                enhanced_code = self._prepare_code_for_subprocess(code, run_id)
                f.write(enhanced_code)
                temp_file = f.name
            
            try:
                result = await self._run_in_subprocess(temp_file)
                return result
                
            finally:
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
            if os.path.isabs(file_path):
                full_path = file_path
            else:
                if file_path.startswith(('code/', 'outputs/')):
                    full_path = os.path.join(self.workspace_dir, file_path)
                else:
                    full_path = os.path.join(self.workspace_dir, "code", file_path)
            
            full_path = os.path.normpath(full_path)
            
            workspace_abs = os.path.abspath(self.workspace_dir)
            if not full_path.startswith(workspace_abs):
                return f"错误：文件路径 {file_path} 不在工作空间内"
            
            if not os.path.exists(full_path):
                return f"错误：文件不存在 {file_path}\n请检查文件路径是否正确"
            
            if not full_path.endswith('.py'):
                return f"错误：文件 {file_path} 不是Python文件"
            
            logger.info(f"准备执行文件: {full_path}")
            
            with open(full_path, 'r', encoding='utf-8') as f:
                code_content = f.read()

            run_id = self.fs.create_run()
            self.fs.save_run_input(run_id, code_content)

            result = await self._execute_code_directly(code_content, run_id)

            self.fs.save_run_stdout(run_id, result)
            self._register_run_artifacts(run_id)
            status = "error" if any(kw in result for kw in ("执行错误", "执行失败", "异常")) else "success"
            self.fs.finish_run(run_id, status)

            return result
            
        except Exception as e:
            logger.error(f"从文件执行代码失败: {e}")
            return f"从文件执行失败: {str(e)}"

    async def _save_code_only(self, code_content: str, filename: str) -> str:
        """仅保存代码到文件（正式源码）"""
        try:
            safe_filename = "".join(c for c in filename if c.isalnum() or c in "._-")
            if not safe_filename:
                safe_filename = f"code_{int(time.time())}"
            
            if not safe_filename.endswith('.py'):
                safe_filename += '.py'

            rel_path = f"code/{safe_filename}"
            self.fs.write_text(
                rel_path, code_content,
                kind="source", created_by="ai", visibility="user",
            )

            logger.info(f"代码已保存: {rel_path}")
            
            if self.stream_manager:
                try:
                    await self.stream_manager.print_code_execution_call(f"代码文件 {safe_filename} 保存成功")
                    await self.stream_manager.send_json_block("file_changed", safe_filename)
                except Exception as e:
                    logger.warning(f"发送工具调用通知失败: {e}")
            
            return f"代码已成功保存到文件: code/{safe_filename}\n代码长度: {len(code_content)} 字符"
            
        except Exception as e:
            logger.error(f"保存代码失败: {e}")
            return f"保存代码失败: {str(e)}"

    def _prepare_code_for_subprocess(self, code: str, run_id: str) -> str:
        """为子进程执行准备代码，产物写入 runs/<run_id>"""
        # 添加必要的导入和设置
        header = f'''# -*- coding: utf-8 -*-
import os
import sys
import matplotlib
matplotlib.use('Agg')  # 非交互式后端
import matplotlib.pyplot as plt

# 配置中文字体支持
import platform
_system = platform.system()
_cn_font_found = False
if _system == 'Darwin':
    _cn_candidates = ['PingFang SC', 'Heiti SC', 'STHeiti', 'Songti SC', 'Arial Unicode MS']
elif _system == 'Windows':
    _cn_candidates = ['Microsoft YaHei', 'SimHei', 'SimSun', 'FangSong', 'KaiTi']
else:
    _cn_candidates = ['WenQuanYi Micro Hei', 'WenQuanYi Zen Hei', 'Noto Sans CJK SC', 'Noto Sans SC', 'Source Han Sans SC', 'AR PL UMing CN', 'Droid Sans Fallback']

from matplotlib.font_manager import fontManager as _fm
_available = set(f.name for f in _fm.ttflist)
for _font in _cn_candidates:
    if _font in _available:
        matplotlib.rcParams['font.sans-serif'] = [_font] + matplotlib.rcParams.get('font.sans-serif', [])
        _cn_font_found = True
        break

if not _cn_font_found:
    _cjk = [f.name for f in _fm.ttflist if any(kw in f.name.lower() for kw in ['cjk', 'chinese', 'hei', 'song', 'fang', 'kai', 'ming', 'gothic', 'pingfang', 'yahei', 'noto sans sc'])]
    if _cjk:
        matplotlib.rcParams['font.sans-serif'] = [_cjk[0]] + matplotlib.rcParams.get('font.sans-serif', [])
        _cn_font_found = True

if not _cn_font_found:
    import glob as _glob
    from matplotlib.font_manager import FontProperties as _FP
    _font_dirs = ['/usr/share/fonts', '/usr/local/share/fonts', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')]
    for _fd in _font_dirs:
        _ttfs = _glob.glob(os.path.join(_fd, '**', '*.ttf'), recursive=True) + _glob.glob(os.path.join(_fd, '**', '*.otf'), recursive=True)
        for _ttf in _ttfs:
            try:
                _fp = _FP(fname=_ttf)
                if any(kw in _fp.get_name().lower() for kw in ['cjk', 'noto', 'hei', 'song', 'fang', 'gothic', 'ming']):
                    _fm.addfont(_ttf)
                    _registered = _FP(fname=_ttf).get_name()
                    matplotlib.rcParams['font.sans-serif'] = [_registered] + matplotlib.rcParams.get('font.sans-serif', [])
                    _cn_font_found = True
                    break
            except Exception:
                pass
        if _cn_font_found:
            break

matplotlib.rcParams['axes.unicode_minus'] = False

_saved_cn_font = list(matplotlib.rcParams['font.sans-serif'])
_original_style_use = plt.style.use
def _patched_style_use(*args, **kwargs):
    _original_style_use(*args, **kwargs)
    matplotlib.rcParams['font.sans-serif'] = _saved_cn_font
    matplotlib.rcParams['axes.unicode_minus'] = False
plt.style.use = _patched_style_use

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

# 设置输出目录 (run-based)
plt_output_dir = "runs/{run_id}/artifacts"
log_output_dir = "runs/{run_id}"
os.makedirs(plt_output_dir, exist_ok=True)

# 设置日志文件
log_file = os.path.join(log_output_dir, "stdout.log")

# 重定向print输出到日志文件
import sys
class TeeOutput:
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, 'w', encoding='utf-8')

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()

    def __del__(self):
        self.log.close()

sys.stdout = TeeOutput(log_file)

# 禁用 plt.show() 以避免 GUI 线程问题
# 在非交互式后端下，plt.show() 会被自动替换为无操作
def _disabled_show(*args, **kwargs):
    """在非交互式后端下禁用 plt.show()，避免 GUI 线程错误"""
    pass

plt.show = _disabled_show

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
    plot_files.append(os.path.join(plt_output_dir, filename))
    plt.close(fig)

if plot_files:
    print(f"\\n图表已保存: {', '.join(plot_files)}")
'''
        
        code = self._strip_user_font_config(code)
        return header + code + footer

    @staticmethod
    def _strip_user_font_config(code: str) -> str:
        code = re.sub(
            r"^[^\S\n]*matplotlib\.rcParams\s*\[\s*['\"]font\.sans-serif['\"]\s*\].*$",
            "",
            code,
            flags=re.MULTILINE,
        )
        code = re.sub(
            r"^[^\S\n]*matplotlib\.rcParams\s*\[\s*['\"]axes\.unicode_minus['\"]\s*\].*$",
            "",
            code,
            flags=re.MULTILINE,
        )
        code = re.sub(
            r"^[^\S\n]*plt\.rcParams\s*\[\s*['\"]font\.sans-serif['\"]\s*\].*$",
            "",
            code,
            flags=re.MULTILINE,
        )
        code = re.sub(
            r"^[^\S\n]*plt\.rcParams\s*\[\s*['\"]axes\.unicode_minus['\"]\s*\].*$",
            "",
            code,
            flags=re.MULTILINE,
        )
        code = re.sub(
            r"^[^\S\n]*rcParams\s*\[\s*['\"]font\.sans-serif['\"]\s*\].*$",
            "",
            code,
            flags=re.MULTILINE,
        )
        code = re.sub(
            r"^[^\S\n]*rcParams\s*\[\s*['\"]axes\.unicode_minus['\"]\s*\].*$",
            "",
            code,
            flags=re.MULTILINE,
        )
        return code

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

    def _ensure_workspace_dirs(self):
        """确保工作空间基本目录存在"""
        self.fs.init_structure()
        for legacy in ("code", "outputs"):
            os.makedirs(os.path.join(self.workspace_dir, legacy), exist_ok=True)

    def _register_run_artifacts(self, run_id: str) -> None:
        """扫描 runs/<run_id>/artifacts/ 下的文件并注册到 manifest"""
        artifacts_dir = self.fs.run_dir(run_id) / "artifacts"
        if not artifacts_dir.exists():
            return
        for item in artifacts_dir.iterdir():
            if item.is_file():
                self.fs.register_run_artifact(run_id, item.name)

    
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
            if not new_code_content or not new_code_content.strip():
                return "错误：新代码内容不能为空"

            if not filename or not filename.strip():
                return "错误：文件名不能为空"

            safe_filename = "".join(
                c for c in filename if c.isalnum() or c in "._-")
            if not safe_filename:
                safe_filename = "code"

            if not safe_filename.endswith('.py'):
                safe_filename = safe_filename + '.py'

            rel_path = f"code/{safe_filename}"
            abs_path = self.fs.abs(rel_path)

            if not abs_path.exists():
                return f"错误：文件 {safe_filename} 不存在，无法修改。请先使用 save_and_execute 创建文件。"

            backup_name = f"{safe_filename}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            backup_rel = f".system/temp/{backup_name}"
            original_content = abs_path.read_text(encoding='utf-8')
            self.fs.write_text(backup_rel, original_content)

            abs_path.write_text(new_code_content, encoding='utf-8')

            logger.info(f"代码文件已修改: {rel_path}")

            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block("code_agent_tool_call", f"CodeAgent正在执行工具调用: edit_code_file")
                    await self.stream_manager.send_json_block("code_agent_tool_result", f"代码文件 {safe_filename} 修改成功")
                    await self.stream_manager.send_json_block("file_changed", safe_filename)
                except Exception as e:
                    logger.warning(f"发送工具调用通知失败: {e}")

            return f"代码文件 {safe_filename} 已成功修改\n文件: {rel_path}\n新代码长度: {len(new_code_content)} 字符"

        except Exception as e:
            error_msg = f"修改代码文件失败: {str(e)}"
            logger.error(error_msg)

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
                return "还没有创建任何代码文件。"

            files = os.listdir(code_dir)
            python_files = [f for f in files if f.endswith('.py') and not f.startswith('autosave_')]

            if not python_files:
                return "还没有创建任何Python代码文件。"

            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block("code_agent_tool_call", f"CodeAgent正在执行工具调用: list_code_files")
                    await self.stream_manager.send_json_block("code_agent_tool_result", f"找到 {len(python_files)} 个Python代码文件")
                except Exception as e:
                    logger.warning(f"发送工具调用通知失败: {e}")

            file_info = []
            for file in python_files:
                file_path = os.path.join(code_dir, file)
                try:
                    file_size = os.path.getsize(file_path)
                    file_info.append(f"- code/{file} ({file_size} bytes)")
                except OSError:
                    file_info.append(f"- code/{file}")

            return f"找到 {len(python_files)} 个Python代码文件:\n" + "\n".join(file_info)

        except Exception as e:
            error_msg = f"列出代码文件失败: {str(e)}"
            logger.error(error_msg)

            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block("code_agent_tool_error", f"工具调用失败: {error_msg}")
                except Exception as ws_error:
                    logger.warning(f"发送错误通知失败: {ws_error}")

            return error_msg
