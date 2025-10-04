# 多阶段构建：使用官方 Python 3.11 镜像
FROM python:3.11-slim as builder

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY pyproject.toml ./

# 安装 uv
RUN pip install --no-cache-dir uv

# 创建虚拟环境并安装所有依赖
RUN uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv pip install --system -r pyproject.toml

# 生产镜像
FROM python:3.11-slim

# 安装运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"

# 从构建阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv

# 复制应用代码
COPY . .

# 确保入口脚本可执行
RUN chmod +x docker-entrypoint.sh

# 暴露端口
EXPOSE 8000

# 启动命令
ENTRYPOINT ["./docker-entrypoint.sh"]