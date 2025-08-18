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
        workspace_dir = os.getenv("WORKSPACE_DIR")
        if not workspace_dir:
            logger.error("CodeExecutor未找到WORKSPACE_DIR环境变量，请确保环境已正确初始化")
            raise RuntimeError("工作空间目录未设置")
        self.workspace_dir = workspace_dir
        logger.info(f"CodeExecutor初始化完成，工作空间目录: {workspace_dir}")

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

            # 代码执行成功后，保存代码和结果到正确的目录
            saved_files = await self._save_execution_results(python_code, result)
            if saved_files:
                result += f"\n\n[执行结果已保存到以下位置:]\n"
                for file_type, file_path in saved_files.items():
                    result += f"- {file_type}: {file_path}\n"

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

    async def _save_execution_results(self, python_code: str, execution_result: str) -> Dict[str, str]:
        """
        将执行结果保存到正确的workspace目录结构
        
        Args:
            python_code: 要保存的Python代码
            execution_result: 代码执行结果
            
        Returns:
            保存的文件路径字典，包含各种类型的文件路径
        """
        saved_files = {}
        
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            code_hash = str(hash(python_code))[-8:]  # 取hash的后8位
            
            # 1. 保存代码到 generated_code 目录
            code_filename = f"code_{timestamp}_{code_hash}.py"
            code_filepath = os.path.join(self.workspace_dir, "generated_code", code_filename)
            
            code_content = f"""# 自动生成的代码文件
# 生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
# 执行结果: {execution_result[:200]}{'...' if len(execution_result) > 200 else ''}

{python_code}

# 代码执行完成
"""
            
            with open(code_filepath, 'w', encoding='utf-8') as f:
                f.write(code_content)
            
            saved_files["代码文件"] = f"generated_code/{code_filename}"
            logger.info(f"代码已保存到: {code_filepath}")
            
            # 2. 保存执行输出到 execution_results 目录
            output_filename = f"output_{timestamp}_{code_hash}.log"
            output_filepath = os.path.join(self.workspace_dir, "execution_results", output_filename)
            
            output_content = f"""# 代码执行输出日志
# 执行时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
# 代码文件: {code_filename}

## 执行输出:
{execution_result}

## 执行状态: 成功
"""
            
            with open(output_filepath, 'w', encoding='utf-8') as f:
                f.write(output_content)
            
            saved_files["执行输出"] = f"execution_results/{output_filename}"
            logger.info(f"执行输出已保存到: {output_filepath}")
            
            # 3. 检查并保存matplotlib图表到 execution_results/plots 目录
            if plt.get_fignums():  # 如果有图表
                for fig_num in plt.get_fignums():
                    fig = plt.figure(fig_num)
                    plot_filename = f"plot_{timestamp}_{code_hash}_{fig_num}.png"
                    plot_filepath = os.path.join(self.workspace_dir, "execution_results", "plots", plot_filename)
                    
                    fig.savefig(plot_filepath, dpi=300, bbox_inches='tight')
                    saved_files[f"图表{fig_num}"] = f"execution_results/plots/{plot_filename}"
                    logger.info(f"图表已保存到: {plot_filepath}")
                
                plt.close('all')  # 关闭所有图表
            
            # 4. 检查并保存数据输出到 execution_results/data_output 目录
            data_files = await self._save_data_outputs(timestamp, code_hash)
            saved_files.update(data_files)
            
            # 5. 保存执行元数据到 execution_results 目录
            metadata_filename = f"metadata_{timestamp}_{code_hash}.json"
            metadata_filepath = os.path.join(self.workspace_dir, "execution_results", metadata_filename)
            
            metadata = {
                "execution_time": datetime.now().isoformat(),
                "code_file": code_filename,
                "output_file": output_filename,
                "code_length": len(python_code),
                "output_length": len(execution_result),
                "has_plots": len(plt.get_fignums()) > 0,
                "plot_files": [f for f in saved_files.keys() if "图表" in f],
                "data_files": [f for f in saved_files.keys() if "数据" in f],
                "status": "success"
            }
            
            with open(metadata_filepath, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            
            saved_files["执行元数据"] = f"execution_results/{metadata_filename}"
            logger.info(f"执行元数据已保存到: {metadata_filepath}")
            
            return saved_files
            
        except Exception as e:
            logger.error(f"保存执行结果失败: {e}")
            return {}

    async def _save_data_outputs(self, timestamp: str, code_hash: str) -> Dict[str, str]:
        """
        检测并保存数据输出到 data_output 目录
        
        Args:
            timestamp: 时间戳
            code_hash: 代码hash
            
        Returns:
            保存的数据文件路径字典
        """
        data_files = {}
        
        try:
            # 检查全局变量中是否有数据对象
            import sys
            import inspect
            
            # 获取当前执行环境中的变量
            frame = sys._getframe(1)  # 获取调用者的frame
            if frame:
                local_vars = frame.f_locals
                global_vars = frame.f_globals
                
                # 合并变量
                all_vars = {**global_vars, **local_vars}
                
                for var_name, var_value in all_vars.items():
                    # 跳过内置变量和函数
                    if var_name.startswith('_') or callable(var_value):
                        continue
                    
                    # 检测numpy数组
                    if hasattr(var_value, 'shape') and hasattr(var_value, 'dtype'):
                        try:
                            import numpy as np
                            if isinstance(var_value, np.ndarray):
                                data_filename = f"data_{var_name}_{timestamp}_{code_hash}.npy"
                                data_filepath = os.path.join(self.workspace_dir, "execution_results", "data_output", data_filename)
                                
                                np.save(data_filepath, var_value)
                                data_files[f"数据数组_{var_name}"] = f"execution_results/data_output/{data_filename}"
                                logger.info(f"numpy数组已保存到: {data_filepath}")
                        except Exception as e:
                            logger.warning(f"保存numpy数组失败: {e}")
                    
                    # 检测pandas数据框
                    elif hasattr(var_value, 'to_csv'):
                        try:
                            import pandas as pd
                            if hasattr(var_value, 'columns'):
                                data_filename = f"data_{var_name}_{timestamp}_{code_hash}.csv"
                                data_filepath = os.path.join(self.workspace_dir, "execution_results", "data_output", data_filename)
                                
                                var_value.to_csv(data_filepath, index=False, encoding='utf-8')
                                data_files[f"数据表格_{var_name}"] = f"execution_results/data_output/{data_filename}"
                                logger.info(f"pandas数据框已保存到: {data_filepath}")
                        except Exception as e:
                            logger.warning(f"保存pandas数据框失败: {e}")
                    
                    # 检测字典数据
                    elif isinstance(var_value, dict) and len(var_value) > 0:
                        try:
                            data_filename = f"data_{var_name}_{timestamp}_{code_hash}.json"
                            data_filepath = os.path.join(self.workspace_dir, "execution_results", "data_output", data_filename)
                            
                            with open(data_filepath, 'w', encoding='utf-8') as f:
                                json.dump(var_value, f, ensure_ascii=False, indent=2, default=str)
                            
                            data_files[f"数据字典_{var_name}"] = f"execution_results/data_output/{data_filename}"
                            logger.info(f"字典数据已保存到: {data_filepath}")
                        except Exception as e:
                            logger.warning(f"保存字典数据失败: {e}")
                    
                    # 检测列表数据
                    elif isinstance(var_value, list) and len(var_value) > 0:
                        try:
                            data_filename = f"data_{var_name}_{timestamp}_{code_hash}.json"
                            data_filepath = os.path.join(self.workspace_dir, "execution_results", "data_output", data_filename)
                            
                            with open(data_filepath, 'w', encoding='utf-8') as f:
                                json.dump(var_value, f, ensure_ascii=False, indent=2, default=str)
                            
                            data_files[f"数据列表_{var_name}"] = f"execution_results/data_output/{data_filename}"
                            logger.info(f"列表数据已保存到: {data_filepath}")
                        except Exception as e:
                            logger.warning(f"保存列表数据失败: {e}")
                            
        except Exception as e:
            logger.warning(f"检测数据输出时出错: {e}")
        
        return data_files

    def get_workspace_dir(self) -> str:
        """获取工作空间目录"""
        return self.workspace_dir

    def set_workspace_dir(self, workspace_dir: str):
        """设置工作空间目录"""
        self.workspace_dir = workspace_dir
        os.makedirs(self.workspace_dir, exist_ok=True)
        logger.info(f"工作空间目录已更新为: {workspace_dir}")
        
        # 重新确保目录结构
        # self._ensure_workspace_structure() # This line is removed as per the edit hint

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
