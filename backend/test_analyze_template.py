#!/usr/bin/env python3
"""
测试 analyze_template 函数
验证函数是否正常返回章节结构
"""

import asyncio
import sys
import os

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_system.tools.template_operations import TemplateOperations

async def test_analyze_template():
    """测试 analyze_template 函数"""
    
    # 创建测试模板内容
    test_template = """# 论文模板

## 摘要
这是一个测试摘要。

## 引言
这是引言部分。

### 研究背景
研究背景内容。

### 研究目标
研究目标描述。

## 相关工作
相关工作内容。

## 方法
研究方法描述。

### 实验设计
实验设计内容。

## 结果
实验结果。

## 结论
研究结论。
"""
    
    print("=== 测试模板内容 ===")
    print(test_template)
    print("\n" + "="*50 + "\n")
    
    # 创建 TemplateOperations 实例
    template_ops = TemplateOperations()
    
    try:
        # 调用 analyze_template 函数
        print("调用 analyze_template 函数...")
        result = await template_ops.analyze_template(test_template)
        
        print("=== 函数返回结果 ===")
        print(f"结果类型: {type(result)}")
        print(f"结果长度: {len(result)} 字符")
        print(f"结果内容:\n{result}")
        
        # 检查结果是否包含章节结构
        if "章节结构:" in result:
            print("\n✅ 结果包含 '章节结构:' 标记")
        else:
            print("\n❌ 结果不包含 '章节结构:' 标记")
        
        # 检查是否包含实际的章节标题
        if "#" in result:
            print("✅ 结果包含章节标题")
        else:
            print("❌ 结果不包含章节标题")
        
        # 检查结果是否为空或只有标记
        if result.strip() == "章节结构:":
            print("❌ 问题确认：函数只返回了标记，没有实际内容！")
        else:
            print("✅ 函数正常返回了章节结构")
            
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

async def test_template_tools_directly():
    """直接测试 template_tools 函数"""
    print("\n" + "="*50)
    print("直接测试 template_tools 函数")
    print("="*50)
    
    from ai_system.tools.template_tools import template_tools
    
    test_template = """# 测试文档

## 章节1
内容1

## 章节2
内容2

### 子章节2.1
子内容
"""
    
    try:
        # 直接调用 extract_template_structure
        print("调用 extract_template_structure...")
        structure = template_tools.extract_template_structure(test_template)
        
        print(f"提取的结构: {structure}")
        
        if 'sections' in structure:
            print(f"章节数量: {len(structure['sections'])}")
            for i, section in enumerate(structure['sections']):
                print(f"  章节{i+1}: 级别{section['level']} - {section['title']}")
        else:
            print("❌ 没有提取到章节信息")
            
    except Exception as e:
        print(f"❌ 直接测试失败: {e}")
        import traceback
        traceback.print_exc()

async def main():
    """主测试函数"""
    print("开始测试 analyze_template 函数...")
    print("="*50)
    
    # 测试 analyze_template 函数
    await test_analyze_template()
    
    # 直接测试 template_tools
    await test_template_tools_directly()
    
    print("\n" + "="*50)
    print("测试完成")

if __name__ == "__main__":
    # 运行测试
    asyncio.run(main())
