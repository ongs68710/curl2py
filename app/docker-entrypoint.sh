#!/bin/sh
set -eu

# A new process/container identity invalidates browser-side runner authorization.
export CURL2PY_RUNNER_INSTANCE_ID="$(python -c 'import secrets; print(secrets.token_urlsafe(12))')"

# Cookie 收藏夹的数据密钥解封后缓存在运行目录，容器一旦（重）启动必须失效，
# 否则"重启即回到锁定状态"这条承诺就不成立。/tmp 在 compose 里是 tmpfs，
# 这里再显式清一次，覆盖 docker run 直接启动、没有 tmpfs 的情况。
rm -rf /tmp/.c2p-vault "${VAULT_RUNTIME_DIR:-/tmp/.c2p-vault}" /app/data/.runtime 2>/dev/null || true

# curl 转换已改为纯 Python（毫秒级），轻量接口的瓶颈从 CPU 算力变成了
# 单进程 GIL。压测显示 2 worker 时 CPU 只用到 1.48/4 核就到顶，因此默认
# 提到 4 worker：每个 worker 独立解释器，才能真正吃满多核。
WORKERS="${GUNICORN_WORKERS:-4}"
THREADS="${GUNICORN_THREADS:-4}"

# 访问日志默认关闭，避免长时间运行时日志无限增长；排查时置 1 开启。
# timeout 120：图片接口允许单张 100MB，慢客户端上传需要比 30 秒更宽的余量。
set -- --bind 0.0.0.0:5000 --workers "$WORKERS" --threads "$THREADS" --timeout 120
if [ "${GUNICORN_ACCESS_LOG:-0}" = "1" ]; then
    set -- "$@" --access-logfile - --access-logformat '%(h)s %(r)s %(s)s %(L)ss'
fi

exec gunicorn "$@" app:app
