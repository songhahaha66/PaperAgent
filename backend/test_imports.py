#!/usr/bin/env python3
"""
测试所有模块的导入
"""

import sys
import os

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(__file__))

def test_imports():
    """测试所有模块的导入"""
    print("开始测试模块导入...")
    
    # 测试AI系统模块
    try:
        from ai_system.core_agents import Agent
        print("✅ Agent基类导入成功")
    except Exception as e:
        print(f"❌ Agent基类导入失败: {e}")
    
    try:
        from ai_system.core_agents import MainAgent
        print("✅ MainAgent导入成功")
    except Exception as e:
        print(f"❌ MainAgent导入失败: {e}")
    
    try:
        from ai_system.core_handlers.llm_handler import LLMHandler
        print("✅ LLMHandler导入成功")
    except Exception as e:
        print(f"❌ LLMHandler导入失败: {e}")
    
    try:
        from ai_system.core_managers.stream_manager import StreamOutputManager
        print("✅ StreamOutputManager导入成功")
    except Exception as e:
        print(f"❌ StreamOutputManager导入失败: {e}")
    
    try:
        from ai_system.core_tools.file_tools import FileTools
        print("✅ FileTools导入成功")
    except Exception as e:
        print(f"❌ FileTools导入失败: {e}")
    
    # 测试服务模块
    try:
        from services.data_services import crud
        print("✅ CRUD服务导入成功")
    except Exception as e:
        print(f"❌ CRUD服务导入失败: {e}")
    
    try:
        from services.file_services import FileHelper
        print("✅ FileHelper导入成功")
    except Exception as e:
        print(f"❌ FileHelper导入失败: {e}")
    
    # 测试路由模块
    try:
        from routers.auth_routes import auth_router
        print("✅ 认证路由导入成功")
    except Exception as e:
        print(f"❌ 认证路由导入失败: {e}")
    
    try:
        from routers.chat_routes import chat_router
        print("✅ 聊天路由导入成功")
    except Exception as e:
        print(f"❌ 聊天路由导入失败: {e}")
    
    print("\n导入测试完成！")

if __name__ == "__main__":
    test_imports()
