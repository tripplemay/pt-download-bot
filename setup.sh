#!/bin/bash
set -e

echo "========================================="
echo "  PT Download Bot - 快速配置"
echo "========================================="
echo ""

if [ -f .env ]; then
    read -p "检测到已有配置文件，是否重新配置？(y/N) " overwrite
    if [ "$overwrite" != "y" ] && [ "$overwrite" != "Y" ]; then
        echo "保留现有配置。"
        exit 0
    fi
fi

echo "请依次输入以下配置信息："
echo ""

# --- 必需：Telegram ---
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

echo ""
echo "✅ Telegram 配置完成"
echo ""
echo "以下配置为可选项，启动后也可以通过 Bot 命令设置。"
echo "直接回车跳过。"
echo ""

# --- 可选：PT 站 ---
read -p "PT 站地址（如 https://ptchdbits.co，回车跳过）: " pt_url
read -p "PT 站 Passkey（回车跳过）: " passkey

# --- 可选：下载客户端 ---
dl_client=""
ds_host="" ds_user="" ds_pass=""
qb_host="" qb_user="" qb_pass=""
tr_host="" tr_user="" tr_pass=""

echo ""
echo "选择下载客户端（回车跳过，启动后可用 Bot 命令设置）："
echo "  1) Download Station（群晖自带）"
echo "  2) qBittorrent"
echo "  3) Transmission"
read -p "请选择 (1/2/3): " client_choice

case $client_choice in
    1)
        dl_client="download_station"
        read -p "群晖地址（默认 http://localhost:5000）: " ds_host
        ds_host=${ds_host:-http://localhost:5000}
        read -p "群晖用户名: " ds_user
        read -sp "群晖密码: " ds_pass
        echo ""
        ;;
    2)
        dl_client="qbittorrent"
        read -p "qBittorrent 地址（默认 http://localhost:8080）: " qb_host
        qb_host=${qb_host:-http://localhost:8080}
        read -p "用户名（默认 admin）: " qb_user
        qb_user=${qb_user:-admin}
        read -sp "密码: " qb_pass
        echo ""
        ;;
    3)
        dl_client="transmission"
        read -p "Transmission 地址（默认 http://localhost:9091）: " tr_host
        tr_host=${tr_host:-http://localhost:9091}
        read -p "用户名: " tr_user
        read -sp "密码: " tr_pass
        echo ""
        ;;
esac

# --- 可选：TMDB ---
echo ""
read -p "TMDB API Key（提升中文搜索，回车跳过）: " tmdb_key

# --- 写入 .env ---
cat > .env << EOF
# === 必需配置 ===
TELEGRAM_BOT_TOKEN=${bot_token}
OWNER_TELEGRAM_ID=${owner_id}

# === 数据库 ===
DB_PATH=/app/data/bot.db
EOF

# 仅在用户填了值时写入可选配置
[ -n "$pt_url" ]    && echo "PT_SITE_URL=${pt_url}" >> .env
[ -n "$passkey" ]   && echo "PT_PASSKEY=${passkey}" >> .env
[ -n "$tmdb_key" ]  && echo "TMDB_API_KEY=${tmdb_key}" >> .env

if [ -n "$dl_client" ]; then
    echo "DOWNLOAD_CLIENT=${dl_client}" >> .env
    case $dl_client in
        download_station)
            echo "DS_HOST=${ds_host}" >> .env
            echo "DS_USERNAME=${ds_user}" >> .env
            echo "DS_PASSWORD=${ds_pass}" >> .env
            ;;
        qbittorrent)
            echo "QB_HOST=${qb_host}" >> .env
            echo "QB_USERNAME=${qb_user}" >> .env
            echo "QB_PASSWORD=${qb_pass}" >> .env
            ;;
        transmission)
            echo "TR_HOST=${tr_host}" >> .env
            echo "TR_USERNAME=${tr_user}" >> .env
            echo "TR_PASSWORD=${tr_pass}" >> .env
            ;;
    esac
fi

echo ""
echo "========================================="
echo "  ✅ 配置完成！"
echo "========================================="
echo ""
echo "启动："
echo "  docker-compose up -d"
echo ""
echo "查看日志："
echo "  docker-compose logs -f"
echo ""
if [ -z "$pt_url" ] || [ -z "$passkey" ] || [ -z "$dl_client" ]; then
    echo "提示：部分配置已跳过，启动后在 Telegram 中发送 /start"
    echo "Bot 会引导你完成剩余配置。"
else
    echo "在 Telegram 中发送 /start 开始使用！"
fi
