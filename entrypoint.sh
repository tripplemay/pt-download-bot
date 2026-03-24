#!/bin/sh
# 修复挂载卷权限：宿主机 ./data 挂载后可能是 root 所有
chown -R botuser:botuser /app/data 2>/dev/null || true
# 收紧数据库文件权限（仅 botuser 可读写）
chmod 600 /app/data/bot.db 2>/dev/null || true
exec gosu botuser "$@"
