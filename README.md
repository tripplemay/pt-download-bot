# PT Download Bot

通过 Telegram 搜索 PT 站影片，一键推送到 NAS 下载。支持多用户（Owner 审批制）。

## 架构

```
Telegram 用户 ──▶ Bot (Python/Docker) ──▶ PT 站 (RSS 搜索)
                         │                      │
                         ▼                      ▼
                   SQLite (用户/日志)      Download Station / qBittorrent / Transmission
                                                │
                                                ▼
                                          Plex / Jellyfin
```

## 快速部署

```bash
git clone <repo-url> && cd pt-download-bot
cp .env.example .env
# 编辑 .env 填入配置
docker-compose up -d
```

## 配置说明

编辑 `.env` 文件：

| 变量 | 必填 | 说明 |
|------|------|------|
| `TELEGRAM_BOT_TOKEN` | 是 | @BotFather 获取 |
| `OWNER_TELEGRAM_ID` | 是 | @userinfobot 获取你的 ID |
| `PT_SITE_URL` | 是 | PT 站地址，如 `https://ptchdbits.co` |
| `PT_PASSKEY` | 是 | PT 站 Passkey |
| `DOWNLOAD_CLIENT` | 否 | `download_station`(默认) / `qbittorrent` / `transmission` |
| `DS_HOST` / `DS_USERNAME` / `DS_PASSWORD` | 视客户端 | Download Station 连接信息 |
| `QB_HOST` / `QB_USERNAME` / `QB_PASSWORD` | 视客户端 | qBittorrent 连接信息 |
| `TR_HOST` / `TR_USERNAME` / `TR_PASSWORD` | 视客户端 | Transmission 连接信息 |
| `DB_PATH` | 否 | 数据库路径，默认 `/app/data/bot.db` |

## 命令列表

### 所有用户

| 命令 | 说明 |
|------|------|
| `/start` | 查看状态 |
| `/apply` | 申请使用权限 |
| `/help` | 帮助信息 |

### 已授权用户

| 命令 | 说明 | 示例 |
|------|------|------|
| `/s` `/search` | 搜索影片 | `/s 星际穿越` |
| `/dl` | 下载指定序号 | `/dl 3` |
| `/more` | 搜索结果下一页 | `/more` |
| `/status` | 查看下载任务 | `/status` |

### 管理员 (Owner)

| 命令 | 说明 | 示例 |
|------|------|------|
| `/users` | 查看所有用户 | `/users` |
| `/pending` | 查看待审批 | `/pending` |
| `/ban` | 移除用户 | `/ban 987654321` |
| `/unban` | 恢复用户 | `/unban 987654321` |
| `/test` | 测试连接 | `/test` |

## 用户流程

1. 朋友搜索 Bot → `/start` → `/apply` 申请
2. Owner 收到 InlineKeyboard 通知 → 一键审批
3. 朋友获得权限 → `/s 影片名` 搜索 → `/dl 序号` 下载
4. 影片下载到 NAS → Plex/Jellyfin 观看

## 技术栈

- Python 3.11+
- python-telegram-bot >= 20.0
- httpx（异步 HTTP）
- feedparser（RSS 解析）
- SQLite（标准库，零依赖）
- Docker 部署，`network_mode: host`

## FAQ

**Q: 如何获取 PT 站 Passkey？**
A: 登录 PT 站 → 控制面板 → 密钥管理

**Q: 群组中能用吗？**
A: 可以。将 Bot 添加到群组，已授权用户直接使用命令。

**Q: 支持哪些 PT 站？**
A: 所有基于 NexusPHP 的站点（CHDBits、HDChina、TTG 等）。

**Q: 下载客户端怎么选？**
A: 群晖 NAS 用 Download Station；其他环境可选 qBittorrent 或 Transmission。

## License

MIT
