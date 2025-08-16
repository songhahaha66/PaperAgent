#!/usr/bin/env python3
"""
PaperAgent Demo 启动脚本
"""

import os
import sys
import subprocess
import time
from pathlib import Path


def check_dependencies():
    """检查依赖是否安装"""
    try:
        import fastapi
        import uvicorn
        import litellm
        print("✓ 所有依赖已安装")
        return True
    except ImportError as e:
        print(f"✗ 缺少依赖: {e}")
        return False


def check_env_file():
    """检查环境变量文件"""
    env_file = Path(__file__).parent / '.env'
    if not env_file.exists():
        print("✗ 未找到 .env 文件")
        print("请创建 .env 文件并设置 API_KEY")
        return False

    # 检查API_KEY是否设置
    with open(env_file, 'r', encoding='utf-8') as f:
        content = f.read()
        if 'API_KEY=' not in content:
            print("✗ .env 文件中未设置 API_KEY")
            return False

    print("✓ 环境变量文件检查通过")
    return True


def create_env_template():
    """创建环境变量模板文件"""
    env_file = Path(__file__).parent / '.env'
    if not env_file.exists():
        template = """# PaperAgent Demo 环境变量配置
# 请设置您的 API 密钥
API_KEY=your_api_key_here

# 可选：设置workspace目录路径（如果不设置，将使用默认路径）
# WORKSPACE=E:\\PaperAgent-win\\backend\\demo\\workspace

# 可选：设置其他环境变量
# BASE_URL=https://api.gemini.com/v1
# MODEL_ID=gemini-2.0-flash
"""
        with open(env_file, 'w', encoding='utf-8') as f:
            f.write(template)
        print("✓ 已创建 .env 模板文件，请编辑并设置您的 API_KEY")


def start_demo_server():
    """启动demo服务器"""
    print("🚀 启动 PaperAgent Demo 服务器...")

    # 切换到demo目录
    demo_dir = Path(__file__).parent
    os.chdir(demo_dir)

    # 启动服务器
    try:
        subprocess.run([
            sys.executable, "-m", "uvicorn",
            "demo_api:app",
            "--host", "0.0.0.0",
            "--port", "8001",
            "--reload"
        ], check=True)
    except KeyboardInterrupt:
        print("\n👋 Demo 服务器已停止")
    except subprocess.CalledProcessError as e:
        print(f"❌ 启动失败: {e}")


def main():
    """主函数"""
    print("=" * 50)
    print("PaperAgent Demo 启动器")
    print("=" * 50)

    # 检查依赖
    if not check_dependencies():
        print("\n请安装缺少的依赖:")
        print("pip install fastapi uvicorn litellm")
        return

    # 检查环境变量
    if not check_env_file():
        create_env_template()
        return

    print("\n✅ 所有检查通过，准备启动服务器...")
    print("📝 服务器将在 http://localhost:8001 启动")
    print("🌐 前端访问地址: http://localhost:5173/demo")
    print("📖 API文档地址: http://localhost:8001/docs")
    print("\n按 Ctrl+C 停止服务器")
    print("-" * 50)

    # 启动服务器
    start_demo_server()


if __name__ == "__main__":
    main()
