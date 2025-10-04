#!/bin/sh
set -e

printf '%s\n' "Running database migrations..."
# 使用预装的虚拟环境（避免运行时下载）
. .venv/bin/activate
alembic upgrade head
printf '%s\n' "Starting application server..."

# 使用虚拟环境中的 uvicorn
exec .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
