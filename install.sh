#!/bin/bash
set -e

REPO="tripplemay/pt-download-bot"
RAW_BASE="https://raw.githubusercontent.com/${REPO}/main"
INSTALL_DIR="/volume1/docker/ptbot"
BACKUP_DIR="/volume1/docker/ptbot.bak.$(date +%Y%m%d%H%M%S)"

# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------

info()  { echo -e "\033[32m[INFO]\033[0m  $*"; }
warn()  { echo -e "\033[33m[WARN]\033[0m  $*"; }
error() { echo -e "\033[31m[ERROR]\033[0m $*"; }

# 兼容 docker compose (v2) 和 docker-compose (v1)
detect_compose() {
    if docker compose version >/dev/null 2>&1; then
        COMPOSE="docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE="docker-compose"
    else
        error "未找到 docker compose 或 docker-compose 命令"
        echo "  请在群晖 DSM 中安装 Container Manager 套件"
        exit 1
    fi
}

# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------

echo ""
echo "========================================="
echo "  PT Download Bot — 一键安装"
echo "========================================="
echo ""

# 1. 检查 Docker
if ! command -v docker >/dev/null 2>&1; then
    error "未检测到 Docker"
    echo "  请先在群晖 DSM → 套件中心 安装 Container Manager"
    exit 1
fi
detect_compose
info "Docker 已就绪（${COMPOSE}）"

# 2. 检测已有安装
if [ -d "$INSTALL_DIR" ]; then
    warn "检测到已有安装：${INSTALL_DIR}"
    read -p "是否覆盖安装？已有数据会自动备份 (y/N) " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "已取消。"
        exit 0
    fi

    info "备份到 ${BACKUP_DIR} ..."
    mkdir -p "$BACKUP_DIR"
    # 备份 .env 和数据库
    [ -f "$INSTALL_DIR/.env" ]          && cp "$INSTALL_DIR/.env" "$BACKUP_DIR/.env"
    [ -d "$INSTALL_DIR/data" ]          && cp -r "$INSTALL_DIR/data" "$BACKUP_DIR/data"
    [ -f "$INSTALL_DIR/docker-compose.yml" ] && cp "$INSTALL_DIR/docker-compose.yml" "$BACKUP_DIR/docker-compose.yml"

    # 停止旧容器
    info "停止旧容器..."
    cd "$INSTALL_DIR"
    $COMPOSE down 2>/dev/null || true
    cd /
fi

# 3. 创建安装目录
mkdir -p "$INSTALL_DIR/data"
cd "$INSTALL_DIR"
info "安装目录：${INSTALL_DIR}"

# 4. 下载配置文件
info "下载 docker-compose.yml ..."
curl -sSL "${RAW_BASE}/docker-compose.yml" -o docker-compose.yml
curl -sSL "${RAW_BASE}/.env.example"       -o .env.example

# 5. 恢复数据库备份（覆盖安装时保留用户数据）
if [ -d "$BACKUP_DIR/data" ] && [ -f "$BACKUP_DIR/data/bot.db" ]; then
    info "恢复数据库备份（保留用户数据）..."
    cp "$BACKUP_DIR/data/bot.db" "$INSTALL_DIR/data/bot.db"
fi

# 6. 交互式配置
echo ""
echo "请输入以下 2 项必填配置："
echo ""

read -p "Telegram Bot Token（从 @BotFather 获取）: " bot_token
while [ -z "$bot_token" ]; do
    echo "  ❌ Bot Token 不能为空"
    read -p "Telegram Bot Token: " bot_token
done

read -p "你的 Telegram User ID（从 @userinfobot 获取）: " owner_id
while [ -z "$owner_id" ]; do
    echo "  ❌ User ID 不能为空"
    read -p "Telegram User ID: " owner_id
done

# 7. 写入 .env
cat > .env << EOF
TELEGRAM_BOT_TOKEN=${bot_token}
OWNER_TELEGRAM_ID=${owner_id}
DB_PATH=/app/data/bot.db
EOF

info ".env 已生成"

# 8. 拉取镜像
echo ""
info "拉取 Docker 镜像（首次可能需要几分钟）..."
$COMPOSE pull

# 9. 启动容器
info "启动容器..."
$COMPOSE up -d

# 10. 检查状态
sleep 2
if $COMPOSE ps | grep -q "running"; then
    echo ""
    echo "========================================="
    echo "  ✅ 安装成功！"
    echo "========================================="
    echo ""
    echo "  打开 Telegram，找到你的 Bot，发送 /start"
    echo "  Bot 会引导你完成 PT 站和下载客户端配置"
    echo ""
    echo "  常用命令："
    echo "    查看日志：cd ${INSTALL_DIR} && sudo ${COMPOSE} logs -f"
    echo "    停止：    cd ${INSTALL_DIR} && sudo ${COMPOSE} down"
    echo "    重启：    cd ${INSTALL_DIR} && sudo ${COMPOSE} restart"
    echo ""
else
    echo ""
    error "容器启动异常，请检查日志："
    echo "  cd ${INSTALL_DIR} && sudo ${COMPOSE} logs"
    exit 1
fi
