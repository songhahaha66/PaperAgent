"""
测试ContextManager功能
验证上下文压缩、摘要生成等功能
"""

import asyncio
import sys
import os

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_system.core.context_manager import ContextManager


async def test_context_manager():
    """测试ContextManager的主要功能"""
    print("=== 测试ContextManager功能 ===\n")
    
    # 创建上下文管理器
    context_manager = ContextManager(max_tokens=1000, max_messages=10)
    print("✓ ContextManager初始化成功")
    
    # 创建模拟消息数据
    mock_messages = [
        {"role": "system", "content": "你是AI助手，专门帮助用户解决数学问题。"},
        {"role": "user", "content": "请帮我分析这个微分方程：dy/dx = x^2 + y"},
        {"role": "assistant", "content": "这是一个一阶非线性微分方程。我们可以使用分离变量法来求解。"},
        {"role": "user", "content": "能给出具体的求解步骤吗？"},
        {"role": "assistant", "content": "好的，让我给出详细的求解步骤：\n1. 首先将方程重写为 dy/dx = x^2 + y\n2. 分离变量：dy/(y+1) = x^2 dx\n3. 两边积分：ln|y+1| = x^3/3 + C\n4. 解得：y = Ce^(x^3/3) - 1"},
        {"role": "user", "content": "这个解有什么物理意义？"},
        {"role": "assistant", "content": "这个解表示一个指数增长函数，在物理上可以描述某些增长过程，比如人口增长、放射性衰变等。"},
        {"role": "user", "content": "能画出这个函数的图像吗？"},
        {"role": "assistant", "content": "当然可以！这个函数y = Ce^(x^3/3) - 1的图像是一个指数增长曲线，当x增大时，y值会快速增长。"},
        {"role": "user", "content": "谢谢你的详细解释！"},
        {"role": "assistant", "content": "不客气！如果你还有其他数学问题，随时可以问我。"},
        {"role": "user", "content": "我想了解偏微分方程"},
        {"role": "assistant", "content": "偏微分方程是包含多个自变量的微分方程，比如热传导方程、波动方程等。它们比常微分方程更复杂，但应用更广泛。"}
    ]
    
    print(f"✓ 创建了 {len(mock_messages)} 条模拟消息")
    
    # 测试token估算
    print("\n--- 测试Token估算 ---")
    for i, msg in enumerate(mock_messages[:3]):
        tokens = context_manager.estimate_tokens(msg['content'])
        print(f"消息 {i+1}: {len(msg['content'])} 字符 -> 估算 {tokens} tokens")
    
    # 测试关键主题提取
    print("\n--- 测试关键主题提取 ---")
    key_topics = context_manager.extract_key_topics(mock_messages)
    print(f"提取的关键主题: {key_topics}")
    
    # 测试上下文状态检查
    print("\n--- 测试上下文状态检查 ---")
    status = context_manager.get_context_status(mock_messages)
    print(f"上下文状态: {status}")
    
    # 测试上下文压缩
    print("\n--- 测试上下文压缩 ---")
    compressed_messages, compression_results = context_manager.compress_context(
        mock_messages, strategy='medium'
    )
    print(f"压缩前消息数: {len(mock_messages)}")
    print(f"压缩后消息数: {len(compressed_messages)}")
    print(f"压缩结果数量: {len(compression_results)}")
    
    if compression_results:
        print("压缩详情:")
        for i, comp in enumerate(compression_results):
            print(f"  压缩 {i+1}: {comp.role} - 压缩率 {comp.compression_ratio:.2%}")
    
    # 测试摘要生成
    print("\n--- 测试摘要生成 ---")
    summary = await context_manager.generate_context_summary(mock_messages, "test_session_001")
    print(f"摘要ID: {summary.summary_id}")
    print(f"摘要内容: {summary.content}")
    print(f"关键主题: {summary.key_topics}")
    print(f"重要信息点: {summary.important_points}")
    
    # 测试长期记忆
    print("\n--- 测试长期记忆 ---")
    memory = context_manager.get_long_term_memory("test_session_001", limit=3)
    print(f"长期记忆数量: {len(memory)}")
    
    # 测试压缩历史
    print("\n--- 测试压缩历史 ---")
    history = context_manager.compression_history
    print(f"压缩历史数量: {len(history)}")
    
    # 测试数据导出
    print("\n--- 测试数据导出 ---")
    export_data = context_manager.export_context_data()
    print(f"导出数据包含 {len(export_data['summaries'])} 个摘要")
    print(f"导出数据包含 {len(export_data['compression_history'])} 条压缩记录")
    
    # 测试清理功能
    print("\n--- 测试清理功能 ---")
    context_manager.cleanup_old_summaries(max_age_days=1)
    print("✓ 清理功能测试完成")
    
    print("\n=== 所有测试完成 ===")


def test_sync_functions():
    """测试同步功能"""
    print("\n=== 测试同步功能 ===\n")
    
    context_manager = ContextManager()
    
    # 测试关键词提取
    text = "这是一个关于人工智能和机器学习的测试文本"
    keywords = context_manager._extract_keywords(text)
    print(f"文本: {text}")
    print(f"提取的关键词: {keywords}")
    
    # 测试概念提取
    concepts = context_manager._extract_concepts(text)
    print(f"提取的概念: {concepts}")
    
    # 测试中间摘要生成
    messages = [
        {"role": "user", "content": "问题1"},
        {"role": "assistant", "content": "回答1"},
        {"role": "user", "content": "问题2"},
        {"role": "assistant", "content": "回答2"}
    ]
    
    middle_summary = context_manager._generate_middle_summary(messages)
    print(f"中间摘要: {middle_summary}")
    
    print("✓ 同步功能测试完成")


if __name__ == "__main__":
    # 运行同步测试
    test_sync_functions()
    
    # 运行异步测试
    asyncio.run(test_context_manager())
