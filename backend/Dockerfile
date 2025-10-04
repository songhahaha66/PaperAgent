# 使用官方 Python 3.11 slim 镜像作为基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# 复制依赖文件（利用 Docker 层缓存）
COPY pyproject.toml ./

# 安装 uv 和项目依赖到系统 Python（避免虚拟环境开销）
RUN pip install --no-cache-dir uv && \
    uv pip install --no-cache-dir --system -r pyproject.toml

# 复制应用代码
COPY . .

# 暴露端口
EXPOSE 8000


# 启动命令（先执行数据库迁移再启动应用）
CMD ["sh", "-c", "uv run alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000"]