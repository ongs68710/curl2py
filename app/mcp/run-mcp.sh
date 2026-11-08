#!/bin/sh
# 玩客云上重建并启动 curl2py-mcp 容器。
# 重复执行安全（先删同名容器），改完参数直接重跑即可。
#
# 稳定性相关的四个参数，别随手删：
#   --log-opt max-size/max-file  默认 json-file 不封顶，长期跑会写满根分区
#   --restart unless-stopped     进程崩了由 Docker 拉起
#   --init                       转发进程不做 PID 1，避免僵尸进程堆积
#   --read-only --tmpfs /tmp     挂载根只读，被写入类漏洞也改不了镜像
set -eu

NAME=curl2py-mcp
IMAGE=${IMAGE:-curl2py-web:0.4.8-armv7}
BASE_URL=${BASE_URL:-http://192.168.0.107:9997}
FILES_DIR=${FILES_DIR:-/root/mcp-files}

docker rm -f "$NAME" >/dev/null 2>&1 || true
mkdir -p "$FILES_DIR"

docker run -d --name "$NAME" \
  --restart unless-stopped --init \
  -p 9999:9999 \
  --memory 128m --cpus 1 \
  --read-only --tmpfs /tmp \
  --security-opt no-new-privileges:true --cap-drop ALL \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=2 \
  -v "$FILES_DIR:/files:ro" \
  -e CURL2PY_BASE_URL="$BASE_URL" \
  -e PYTHONUNBUFFERED=1 \
  -e PYTHONDONTWRITEBYTECODE=1 \
  --health-cmd "python -c \"import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9999/health',timeout=5).status==200 else 1)\"" \
  --health-interval=30s --health-timeout=10s --health-retries=3 --health-start-period=10s \
  --entrypoint python \
  "$IMAGE" /app/mcp/server.py \
    --transport http --host 0.0.0.0 --port 9999 \
    --max-concurrency 64 \
    --allow-local-file --local-file-root /files

sleep 2
docker ps --format '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}' | grep "$NAME"
