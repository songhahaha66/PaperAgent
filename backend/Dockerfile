# 使用官方 Python 3.11 slim 镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件和工作区成员
COPY pyproject.toml ./
COPY office_word_mcp ./office_word_mcp

# 安装 uv
RUN pip install --no-cache-dir uv

# 创建虚拟环境并安装所有依赖（内置到镜像）
RUN uv venv && \
    . .venv/bin/activate && \
    uv pip install -r pyproject.toml

# 复制应用代码
COPY . .

# 确保入口脚本可执行
RUN chmod +x docker-entrypoint.sh

# 暴露端口
EXPOSE 8000

# 启动命令
ENTRYPOINT ["./docker-entrypoint.sh"]