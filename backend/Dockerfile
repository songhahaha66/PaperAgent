# 使用官方 Python 3.11 slim 镜像作为基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv（Python 包管理器）
RUN pip install uv

# 复制项目文件
COPY pyproject.toml ./

# 创建虚拟环境并安装依赖
RUN uv venv .venv && \
    . .venv/bin/activate && \
    uv pip install -r pyproject.toml

# 复制应用代码
COPY . .

# .env 文件将通过 volume 挂载，不包含在镜像中

# 激活虚拟环境并暴露端口
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000


# 启动命令
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]