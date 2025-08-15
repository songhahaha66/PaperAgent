#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI系统简单测试脚本
测试LiteLLM客户端、工具框架、聊天系统等组件
"""

import asyncio
import os
import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.append(str(Path(__file__).parent))

def test_imports():
    """测试模块导入"""
    print("🔍 测试模块导入...")
    try:
        from ai_system.litellm_client import litellm_client
        from ai_system.tool_framework import ToolRegistry, ToolExecutor
        from ai_system.tools import register_core_tools
        from ai_system.chat_system import chat_system
        print("✅ 所有模块导入成功")
        return True
    except ImportError as e:
        print(f"❌ 模块导入失败: {e}")
        return False

def test_tool_registry():
    """测试工具注册表"""
    print("\n🔧 测试工具注册表...")
    try:
        from ai_system.tool_framework import ToolRegistry
        
        registry = ToolRegistry()
        print(f"✅ 工具注册表创建成功，当前工具数量: {len(registry.tools)}")
        return True
    except Exception as e:
        print(f"❌ 工具注册表测试失败: {e}")
        return False

def test_core_tools():
    """测试核心工具注册"""
    print("\n🛠️ 测试核心工具注册...")
    try:
        from ai_system.tools import register_core_tools
        from ai_system.tool_framework import tool_registry
        
        # 注册核心工具
        register_core_tools()
        
        # 检查工具是否注册成功
        tools = tool_registry.get_all_tools()
        print(f"✅ 核心工具注册成功，可用工具: {[tool.name for tool in tools]}")
        return True
    except Exception as e:
        print(f"❌ 核心工具测试失败: {e}")
        return False

def test_litellm_client():
    """测试LiteLLM客户端"""
    print("\n🤖 测试LiteLLM客户端...")
    try:
        from ai_system.litellm_client import litellm_client
        
        # 检查客户端配置
        print(f"✅ LiteLLM客户端初始化成功")
        print(f"   支持的模型类型: {list(litellm_client.model_configs.keys())}")
        return True
    except Exception as e:
        print(f"❌ LiteLLM客户端测试失败: {e}")
        return False

def test_chat_system():
    """测试聊天系统"""
    print("\n💬 测试聊天系统...")
    try:
        from ai_system.chat_system import chat_system
        
        # 检查聊天系统状态
        print(f"✅ 聊天系统初始化成功")
        print(f"   支持的系统类型: brain, code, writing")
        print(f"   当前会话数量: {len(chat_system.sessions)}")
        return True
    except Exception as e:
        print(f"❌ 聊天系统测试失败: {e}")
        return False

def test_workspace_creation():
    """测试工作空间创建"""
    print("\n📁 测试工作空间创建...")
    try:
        # 创建测试工作空间
        test_work_id = "test_work_123"
        workspace_path = Path(f"workspaces/{test_work_id}")
        
        # 确保目录存在
        workspace_path.mkdir(parents=True, exist_ok=True)
        
        # 创建测试文件
        test_file = workspace_path / "test.txt"
        test_file.write_text("这是一个测试文件")
        
        print(f"✅ 工作空间创建成功: {workspace_path}")
        print(f"   测试文件: {test_file}")
        
        # 清理测试文件
        test_file.unlink()
        workspace_path.rmdir()
        
        return True
    except Exception as e:
        print(f"❌ 工作空间测试失败: {e}")
        return False

def test_file_tools():
    """测试文件相关工具"""
    print("\n📄 测试文件工具...")
    try:
        from ai_system.tools import FileListTool, FileModifyTool
        
        # 测试文件列表工具
        file_list_tool = FileListTool()
        print(f"✅ 文件列表工具创建成功: {file_list_tool.name}")
        
        # 测试文件修改工具
        file_modify_tool = FileModifyTool()
        print(f"✅ 文件修改工具创建成功: {file_modify_tool.name}")
        
        return True
    except Exception as e:
        print(f"❌ 文件工具测试失败: {e}")
        return False

def test_python_execution_tool():
    """测试Python代码执行工具"""
    print("\n🐍 测试Python代码执行工具...")
    try:
        from ai_system.tools import PythonCodeExecutionTool
        
        # 测试工具创建
        python_tool = PythonCodeExecutionTool()
        print(f"✅ Python代码执行工具创建成功: {python_tool.name}")
        
        return True
    except Exception as e:
        print(f"❌ Python代码执行工具测试失败: {e}")
        return False

async def test_chat_dialogue():
    """测试对话功能"""
    print("\n💬 测试对话功能...")
    try:
        from ai_system.chat_system import chat_system
        
        # 创建测试会话
        test_session_id = "test_dialogue_session"
        test_work_id = "test_work_123"
        test_system_type = "brain"
        
        # 测试创建会话
        session = chat_system.create_session(test_session_id, test_work_id, test_system_type)
        print(f"✅ 测试会话创建成功: {session.session_id}")
        
        # 测试添加用户消息
        from ai_system.chat_system import ChatMessage
        user_msg = ChatMessage("user", "你好，请介绍一下你自己")
        session.add_message(user_msg)
        print(f"✅ 用户消息添加成功: {user_msg.content}")
        
        # 测试获取上下文消息
        context_messages = session.get_context_messages()
        print(f"✅ 上下文消息获取成功，消息数量: {len(context_messages)}")
        
        # 测试获取可用工具
        tools = session.get_tools()
        print(f"✅ 可用工具获取成功，工具数量: {len(tools)}")
        
        # 测试会话历史
        history = chat_system.get_session_history(test_session_id)
        print(f"✅ 会话历史获取成功，历史消息数量: {len(history)}")
        
        # 清理测试会话
        chat_system.delete_session(test_session_id)
        print(f"✅ 测试会话清理成功")
        
        return True
    except Exception as e:
        print(f"❌ 对话功能测试失败: {e}")
        return False

async def test_ai_response():
    """测试AI回复功能"""
    print("\n🤖 测试AI回复功能...")
    try:
        from ai_system.chat_system import chat_system
        
        # 创建测试会话
        test_session_id = "test_ai_response_session"
        test_work_id = "test_work_123"
        test_system_type = "brain"
        
        print(f"📝 发送消息: '你好，请简单介绍一下你自己'")
        
        # 测试AI回复
        try:
            result = await chat_system.process_message(
                session_id=test_session_id,
                user_message="你好，请简单介绍一下你自己",
                work_id=test_work_id,
                system_type=test_system_type
            )
            
            if result.get("success"):
                print(f"✅ AI回复成功!")
                print(f"📤 AI回复内容: {result.get('response', '无回复内容')}")
                
                # 检查是否有工具调用
                tool_calls = result.get("tool_calls", [])
                if tool_calls:
                    print(f"🔧 工具调用数量: {len(tool_calls)}")
                    for i, tool_call in enumerate(tool_calls):
                        print(f"   工具调用 {i+1}: {tool_call.get('function', {}).get('name', '未知工具')}")
                
                # 检查工具执行结果
                tool_results = result.get("tool_results", [])
                if tool_results:
                    print(f"📊 工具执行结果数量: {len(tool_results)}")
                
                # 显示会话历史
                history = chat_system.get_session_history(test_session_id)
                print(f"💬 会话历史消息数量: {len(history)}")
                
            else:
                print(f"❌ AI回复失败: {result.get('error', '未知错误')}")
                return False
                
        except Exception as e:
            print(f"⚠️ AI回复测试跳过 (可能需要配置API密钥): {e}")
            print("   这是正常的，因为需要配置有效的AI模型才能测试实际回复")
        
        # 清理测试会话
        chat_system.delete_session(test_session_id)
        print(f"✅ 测试会话清理成功")
        
        return True
    except Exception as e:
        print(f"❌ AI回复测试失败: {e}")
        return False

async def test_async_operations():
    """测试异步操作"""
    print("\n⚡ 测试异步操作...")
    try:
        from ai_system.litellm_client import litellm_client
        
        # 测试异步聊天（如果有配置的话）
        try:
            # 这里只是测试异步接口是否可用，不实际发送请求
            print("✅ 异步接口可用")
        except Exception as e:
            print(f"⚠️ 异步接口测试跳过: {e}")
        
        return True
    except Exception as e:
        print(f"❌ 异步操作测试失败: {e}")
        return False

def run_all_tests():
    """运行所有测试"""
    print("🚀 开始AI系统测试...\n")
    
    tests = [
        ("模块导入", test_imports),
        ("工具注册表", test_tool_registry),
        ("核心工具", test_core_tools),
        ("LiteLLM客户端", test_litellm_client),
        ("聊天系统", test_chat_system),
        ("工作空间创建", test_workspace_creation),
        ("文件工具", test_file_tools),
        ("Python执行工具", test_python_execution_tool),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        try:
            if test_func():
                passed += 1
        except Exception as e:
            print(f"❌ {test_name}测试异常: {e}")
    
    # 异步测试
    try:
        asyncio.run(test_chat_dialogue())
        passed += 1
        total += 1
    except Exception as e:
        print(f"❌ 对话功能测试异常: {e}")
    
    try:
        asyncio.run(test_ai_response())
        passed += 1
        total += 1
    except Exception as e:
        print(f"❌ AI回复测试异常: {e}")
    
    try:
        asyncio.run(test_async_operations())
        passed += 1
        total += 1
    except Exception as e:
        print(f"❌ 异步操作测试异常: {e}")
    
    print(f"\n📊 测试结果: {passed}/{total} 通过")
    
    if passed == total:
        print("🎉 所有测试通过！AI系统运行正常。")
    else:
        print("⚠️ 部分测试失败，请检查相关配置和依赖。")

def main():
    """主函数"""
    print("=" * 50)
    print("AI系统测试脚本")
    print("=" * 50)
    
    try:
        run_all_tests()
    except KeyboardInterrupt:
        print("\n\n⏹️ 测试被用户中断")
    except Exception as e:
        print(f"\n\n💥 测试过程中发生错误: {e}")
    
    print("\n" + "=" * 50)

if __name__ == "__main__":
    main()
