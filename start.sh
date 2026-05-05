#!/bin/bash
set -e

# 1. 确保目录存在且权限正确
mkdir -p /data/.hermes/cron /data/.hermes/sessions /data/.hermes/logs \
         /data/.hermes/memories /data/.hermes/skills /data/.hermes/pairing \
         /data/.hermes/hooks /data/.hermes/image_cache /data/.hermes/audio_cache \
         /data/.hermes/workspace

# 2. 初始化配置文件（如果不存在）
if [ ! -f /data/.hermes/config.yaml ] && [ -f /opt/hermes-agent/cli-config.yaml.example ]; then
  cp /opt/hermes-agent/cli-config.yaml.example /data/.hermes/config.yaml
fi

[ ! -f /data/.hermes/.env ] && touch /data/.hermes/.env

# 清理旧的进程标识
rm -f /data/.hermes/gateway.pid

# 3. 设置环境变量，指引 server.py 找到正确位置
export HERMES_HOME="/data/.hermes"
export PORT=8000  # Databricks 对外暴露的端口

# 4. 🚀 关键修改：启动 server.py 管理器，而不是直接启动 dashboard
# server.py 会在内部自动管理 dashboard (端口 9119) 和 gateway
echo "[start.sh] Starting Hermes Admin Server on port $PORT..."
python3 /app/server.py
