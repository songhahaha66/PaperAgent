# 使用官方 Python 3.11 slim 镜像
FROM python:3.14-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# 安装系统依赖（含中文字体，供 Matplotlib 渲染）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    fontconfig \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -fv

# 安装 Node.js（docx-js 需要）
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g docx \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件和工作区成员
COPY pyproject.toml ./
# 安装 uv
RUN pip install --no-cache-dir uv

# 创建虚拟环境并安装所有依赖（内置到镜像）
RUN uv venv && \
    . .venv/bin/activate && \
    uv pip install -r pyproject.toml

# 清除 matplotlib 字体缓存，确保首次运行时重建索引能发现中文字体
RUN rm -rf /root/.cache/matplotlib /tmp/matplotlib-*

# 复制应用代码
COPY . .

# 确保入口脚本可执行
RUN chmod +x docker-entrypoint.sh

# 暴露端口
EXPOSE 8000

# 启动命令
ENTRYPOINT ["./docker-entrypoint.sh"]