#!/bin/sh
set -e

printf '%s\n' "Running database migrations..."
# 使用内置的虚拟环境
alembic upgrade head
printf '%s\n' "Starting application server..."

exec uvicorn main:app --host 0.0.0.0 --port 8000
