# PT Download Bot

> 通过 Telegram 搜索 PT 站影片，一键推送到 NAS 下载。支持多用户审批制。

```
Telegram 用户 ──▶ Bot (Docker) ──▶ PT 站搜索
                       │                │
                       ▼                ▼
                 SQLite (用户/日志)   Download Station / qBittorrent / Transmission
                                        │
                                        ▼
                                  Plex / Jellyfin
```

## 功能特性

- 中英文影片搜索（TMDB 自动翻译 + 渐进式精度搜索）
- 搜索结果显示做种数（🟢/🔴）、副标题、按做种数排序
- Inline Keyboard 一键下载，无需手动输入命令
- 下载进度可视化（进度条 + ETA + 速度）
- 下载完成自动推送 Telegram 通知
- 一键推送到 Download Station / qBittorrent / Transmission
- 多用户支持（Owner 审批制，用户只能查看自己的任务）
- 智能搜索（Cookie 网页版 + RSS 自动切换，5 分钟缓存防风控）
- 全部配置可通过 Telegram 对话完成，无需编辑配置文件

## 准备工作

开始前需要准备 2 样东西：

1. **Telegram Bot Token** — 打开 Telegram 搜索 `@BotFather`，发送 `/newbot` 按提示创建，复制获得的 Token
2. **你的 Telegram User ID** — 搜索 `@userinfobot`，发送任意消息，复制返回的数字 ID

其他配置（PT 站、下载客户端等）启动后在 Telegram 中通过 Bot 命令设置即可。

## 一键安装（推荐）

SSH 登录群晖后运行：

```bash
curl -sSL https://raw.githubusercontent.com/tripplemay/pt-download-bot/main/install.sh | sudo bash
```

按提示输入 Bot Token 和 User ID，安装完成后打开 Telegram 发送 `/start` 继续配置。

> 脚本会自动下载镜像并启动容器。覆盖安装时自动备份数据库和配置。

## 其他部署方式

### 方法一：SSH 命令行

```bash
# 1. SSH 连接群晖
ssh 你的用户名@群晖IP

# 2. 下载项目
cd /volume1/docker
git clone https://github.com/tripplemay/pt-download-bot.git ptbot
cd ptbot

# 3. 配置（交互式，按提示填写）
bash setup.sh

# 4. 启动
sudo docker compose up -d
```

在 Telegram 中找到你的 Bot，发送 `/start`，按引导完成配置。

### 方法二：群晖 DSM 网页操作（不用命令行）

1. **下载项目**
   - 打开 https://github.com/tripplemay/pt-download-bot
   - 点击绿色 `Code` 按钮 → `Download ZIP`
   - 解压得到项目文件夹

2. **上传到群晖**
   - DSM → File Station → 进入 `docker` 文件夹
   - 上传解压后的项目文件夹，重命名为 `ptbot`

3. **创建配置文件**
   - 在 `ptbot` 文件夹中，复制 `.env.example` 为 `.env`
   - 右键 `.env` → 用文本编辑器打开
   - 填入 `TELEGRAM_BOT_TOKEN` 和 `OWNER_TELEGRAM_ID`（只需这两项）

4. **启动容器**
   - DSM → Container Manager（或 Docker 套件）
   - 项目 → 新增 → 选择 `ptbot` 文件夹中的 `docker-compose.yml`
   - 设置项目名称为 `pt-download-bot`
   - 点击构建并启动

5. 在 Telegram 中发送 `/start`，Bot 会引导你完成 PT 站和下载客户端配置

## 使用指南

### 首次配置

启动后发送 `/start`，Bot 会显示引导向导。按步骤设置：

```
/setsite https://ptchdbits.co       ← PT 站地址
/setpasskey 你的Passkey              ← PT 站 Passkey
/setds http://localhost:5000 用户名 密码  ← Download Station
```

可选增强：
```
/setcookie Cookie值     ← 启用网页版搜索，结果更完整
/settmdb API_Key       ← 启用 TMDB 中文翻译
```

配置完成后发送 `/s 星际穿越` 试试搜索。

### 命令列表

**搜索下载**

| 命令 | 说明 | 示例 |
|------|------|------|
| `/s` | 搜索影片（结果带 Inline 下载按钮） | `/s 星际穿越` |
| `/dl` | 下载指定序号（也可点按钮） | `/dl 3` |
| `/more` | 下一页（也可点翻页按钮） | `/more` |
| `/status` | 查看下载进度（用户只看自己的） | `/status` |
| `/status mine` | Owner 只看自己的任务 | `/status mine` |

**用户管理（管理员）**

| 命令 | 说明 |
|------|------|
| `/users` | 查看所有用户 |
| `/pending` | 查看待审批 |
| `/ban` `/unban` | 封禁 / 解封用户 |

**设置命令（管理员）**

| 命令 | 说明 |
|------|------|
| `/setsite` | 设置 PT 站地址 |
| `/setpasskey` | 设置 Passkey |
| `/setcookie` | 设置 Cookie |
| `/settmdb` | 设置 TMDB API Key |
| `/setds` `/setqb` `/settr` | 设置下载客户端 |
| `/settings` | 查看所有设置 |
| `/test` | 测试连接 |

### 邀请朋友

1. 把 Bot 链接分享给朋友
2. 朋友发送 `/apply` 申请
3. 你收到通知 → 点击通过
4. 朋友即可搜索和下载

## 常见问题

**Bot 没有响应？**
- 检查群晖是否能访问 Telegram（可能需要路由器代理）
- 查看日志：`cd /volume1/docker/ptbot && sudo docker compose logs -f`

**搜索结果少？**
- 发送 `/setcookie` 配置 Cookie 启用网页版搜索
- 发送 `/settmdb` 配置 TMDB API Key 提升中文搜索精度

**下载失败？**
- Download Station 地址用 `http://localhost:5000`（容器与群晖共享网络）
- 发送 `/test` 测试连接
- 发送 `/settings` 检查配置

**支持哪些 PT 站？**
- 所有基于 NexusPHP 的站点（CHDBits、HDChina、TTG 等）

**支持哪些群晖版本？**
- DSM 6 和 DSM 7 均支持，Download Station API 自动适配

**如何更新？**
```bash
cd /volume1/docker/ptbot && sudo docker compose pull && sudo docker compose up -d
```

或重新运行一键安装（自动备份数据）：
```bash
curl -sSL https://raw.githubusercontent.com/tripplemay/pt-download-bot/main/install.sh | sudo bash
```

## 开发者

```bash
# 本地构建运行
docker compose -f docker-compose.build.yml up -d

# 运行测试
python3 -m pytest tests/
python3 -m pytest --cov=bot --cov-report=term-missing
```

## License

MIT
