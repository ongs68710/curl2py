#!/bin/sh
# curl2py-mcp 看门狗，由 cron 每 2 分钟跑一次。
#
# 存在的理由：Docker 对 unhealthy 容器只会把状态标红，不会重启
# （除非另装 autoheal）。没有这一步，进程卡死/半死不活就一直挂着，
# 而 restart:unless-stopped 只覆盖「进程退出」这一类故障。
set -u

NAME=curl2py-mcp
LOG=/var/log/mcp-watchdog.log

# 日志自己也要封顶，否则看门狗反过来把磁盘写满
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 262144 ]; then
    : > "$LOG"
fi

say() { echo "$(date '+%F %T') $*" >> "$LOG"; }

state=$(docker inspect -f '{{.State.Status}}' "$NAME" 2>/dev/null || true)
if [ -z "$state" ]; then
    say "容器不存在，重新拉起"
    sh /root/run-mcp.sh >>"$LOG" 2>&1
    exit 0
fi

if [ "$state" != "running" ]; then
    say "状态=$state，尝试启动"
    docker start "$NAME" >>"$LOG" 2>&1
    exit 0
fi

health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$NAME" 2>/dev/null || echo none)
if [ "$health" = "unhealthy" ]; then
    say "unhealthy，重启"
    docker restart "$NAME" >>"$LOG" 2>&1
fi
