"""设置命令 — Owner 通过 Telegram 对话配置 Bot 参数"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from telegram import ForceReply, Update
from telegram.ext import ContextTypes

from bot.middleware import require_owner
from bot.handlers.search import _search_result_cache
from bot.pt.nexusphp import NexusPHPSite

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 通用辅助
# ------------------------------------------------------------------

async def _delete_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """删除用户发送的包含敏感信息的消息。"""
    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
    except Exception:
        pass


def _check_setup_complete(db) -> bool:
    """检查必需配置是否齐全（site + passkey + 下载客户端）。"""
    return bool(
        db.get_setting("pt_site_url")
        and db.get_setting("pt_passkey")
        and db.get_setting("dl_client_type")
        and db.get_setting("dl_client_host")
    )


async def _notify_setup_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """如果必需配置刚刚齐全，发送提示并标记完成。"""
    db = context.bot_data["db"]
    if _check_setup_complete(db) and db.get_setting("setup_completed") != "true":
        db.set_setting("setup_completed", "true")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🎉 基础配置完成！现在可以使用 /s 搜索影片了。",
        )


def _is_valid_url(url: str) -> bool:
    """简单校验 URL 格式。"""
    try:
        r = urlparse(url)
        return r.scheme in ("http", "https") and bool(r.netloc)
    except Exception:
        return False


# ------------------------------------------------------------------
# /setsite — 设置 PT 站地址
# ------------------------------------------------------------------

@require_owner
async def setsite_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setsite <URL> — 设置 PT 站地址。"""
    if not context.args:
        await update.message.reply_text(
            "请输入 PT 站地址：\n例如：https://ptchdbits.co",
            reply_markup=ForceReply(selective=True),
        )
        return

    url = context.args[0].rstrip("/")
    if not _is_valid_url(url):
        await update.message.reply_text("URL 格式无效，请输入完整地址（以 http:// 或 https:// 开头）。")
        return

    db = context.bot_data["db"]
    db.set_setting("pt_site_url", url)

    # 重新初始化 PT 客户端
    from bot.main import init_pt_client
    pt_client = init_pt_client(db)
    context.bot_data["pt_client"] = pt_client

    _search_result_cache.clear()
    domain = urlparse(url).netloc
    await update.message.reply_text(f"PT 站地址已设置为 {domain}")

    await _notify_setup_complete(update, context)


# ------------------------------------------------------------------
# /setpasskey — 设置 Passkey
# ------------------------------------------------------------------

@require_owner
async def setpasskey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setpasskey <passkey> — 设置 PT 站 Passkey。"""
    await _delete_user_message(update, context)
    chat_id = update.effective_chat.id

    if not context.args:
        await context.bot.send_message(
            chat_id=chat_id,
            text="请输入 Passkey：",
            reply_markup=ForceReply(selective=True),
        )
        return

    passkey = context.args[0]
    db = context.bot_data["db"]

    msg = await context.bot.send_message(chat_id=chat_id, text="正在验证 Passkey...")

    # 验证：用 passkey 请求 RSS 接口
    site_url = db.get_setting("pt_site_url")
    if site_url:
        try:
            test_site = NexusPHPSite(site_url, passkey)
            ok = await test_site.test_connection()
            await test_site.close()
            if ok:
                db.set_setting("pt_passkey", passkey)
                from bot.main import init_pt_client
                context.bot_data["pt_client"] = init_pt_client(db)
                await msg.edit_text("Passkey 已保存并验证通过！搜索功能已启用。")
                await _notify_setup_complete(update, context)
                return
            else:
                await msg.edit_text(
                    "Passkey 验证失败（RSS 接口无响应）。\n"
                    "已保存，但请确认 Passkey 是否正确。"
                )
        except Exception:
            logger.exception("Passkey 验证异常")
            await msg.edit_text("验证时出错，Passkey 已保存，请用 /test 检查连接。")
    else:
        await msg.edit_text("Passkey 已保存。请先用 /setsite 设置 PT 站地址后再验证。")

    db.set_setting("pt_passkey", passkey)
    _search_result_cache.clear()
    from bot.main import init_pt_client
    context.bot_data["pt_client"] = init_pt_client(db)
    await _notify_setup_complete(update, context)


# ------------------------------------------------------------------
# /settmdb — 设置 TMDB API Key
# ------------------------------------------------------------------

@require_owner
async def settmdb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/settmdb <API_Key> — 设置 TMDB API Key。"""
    await _delete_user_message(update, context)
    chat_id = update.effective_chat.id

    if not context.args:
        await context.bot.send_message(
            chat_id=chat_id,
            text="请输入 TMDB API Key：\n免费注册：https://www.themoviedb.org/settings/api",
            reply_markup=ForceReply(selective=True),
        )
        return

    api_key = context.args[0]
    db = context.bot_data["db"]
    db.set_setting("tmdb_api_key", api_key)

    from bot.main import init_tmdb_client
    context.bot_data["tmdb_client"] = init_tmdb_client(db)

    await context.bot.send_message(
        chat_id=chat_id,
        text="TMDB API Key 已保存，中文搜索翻译已启用。",
    )


# ------------------------------------------------------------------
# /setds, /setqb, /settr — 设置下载客户端
# ------------------------------------------------------------------

async def _set_dl_client(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    client_type: str,
    display_name: str,
):
    """通用下载客户端设置逻辑。"""
    await _delete_user_message(update, context)
    chat_id = update.effective_chat.id

    if not context.args or len(context.args) < 3:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"请输入 {display_name} 连接信息：\n格式：地址 用户名 密码\n"
                 f"例如：http://localhost:5000 admin password123",
            reply_markup=ForceReply(selective=True),
        )
        return

    host = context.args[0]
    username = context.args[1]
    password = " ".join(context.args[2:])  # 密码可能包含空格

    if not _is_valid_url(host):
        await context.bot.send_message(
            chat_id=chat_id,
            text="地址格式无效，请输入完整地址（以 http:// 或 https:// 开头）。",
        )
        return

    db = context.bot_data["db"]
    db.set_setting("dl_client_type", client_type)
    db.set_setting("dl_client_host", host)
    db.set_setting("dl_client_username", username)
    db.set_setting("dl_client_password", password)

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"{display_name} 配置已保存，正在验证连接...",
    )

    # 重新初始化并测试
    from bot.main import init_dl_client
    dl_client = init_dl_client(db)
    context.bot_data["dl_client"] = dl_client

    if dl_client:
        try:
            ok = await dl_client.test_connection()
            if ok:
                await msg.edit_text(f"{display_name} 连接成功！下载功能已启用。")
                await _notify_setup_complete(update, context)
                return
        except Exception:
            logger.exception("下载客户端连接测试异常")

        await msg.edit_text(
            f"{display_name} 配置已保存，但连接测试失败。\n"
            "请检查地址和凭据是否正确，然后用 /test 重试。"
        )
    else:
        await msg.edit_text("配置保存失败，请检查参数。")

    await _notify_setup_complete(update, context)


@require_owner
async def setds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setds <地址> <用户名> <密码> — 设置 Download Station。"""
    await _set_dl_client(update, context, "download_station", "Download Station")


@require_owner
async def setqb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setqb <地址> <用户名> <密码> — 设置 qBittorrent。"""
    await _set_dl_client(update, context, "qbittorrent", "qBittorrent")


@require_owner
async def settr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/settr <地址> <用户名> <密码> — 设置 Transmission。"""
    await _set_dl_client(update, context, "transmission", "Transmission")


# ------------------------------------------------------------------
# /settings — 查看当前所有设置
# ------------------------------------------------------------------

_DL_DISPLAY_NAMES = {
    "download_station": "Download Station",
    "qbittorrent": "qBittorrent",
    "transmission": "Transmission",
}


@require_owner
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/settings — 查看当前所有设置状态。"""
    db = context.bot_data["db"]

    site_url = db.get_setting("pt_site_url")
    passkey = db.get_setting("pt_passkey")
    cookie = db.get_setting("pt_cookie")
    tmdb_key = db.get_setting("tmdb_api_key")
    dl_type = db.get_setting("dl_client_type")
    dl_host = db.get_setting("dl_client_host")

    lines = ["<b>当前设置</b>\n"]

    # PT 站
    if site_url:
        domain = urlparse(site_url).netloc
        lines.append(f"📡 PT 站: {domain} ✅")
    else:
        lines.append("📡 PT 站: 未配置 ❌")

    # Passkey
    lines.append("🔑 Passkey: 已配置 ✅" if passkey else "🔑 Passkey: 未配置 ❌")

    # Cookie
    lines.append("🍪 Cookie: 已配置 ✅" if cookie else "🍪 Cookie: 未配置 ⚠️（可选）")

    # TMDB
    lines.append("🎬 TMDB: 已配置 ✅" if tmdb_key else "🎬 TMDB: 未配置 ⚠️（可选）")

    # 下载客户端
    if dl_type and dl_host:
        name = _DL_DISPLAY_NAMES.get(dl_type, dl_type)
        host_display = urlparse(dl_host).netloc or dl_host
        lines.append(f"📥 下载客户端: {name} ({host_display}) ✅")
    else:
        lines.append("📥 下载客户端: 未配置 ❌")

    lines.append("")

    if _check_setup_complete(db):
        lines.append("✅ 所有必需配置已完成")
    else:
        lines.append("⚠️ 必需配置未完成，请使用以下命令：")
        if not site_url:
            lines.append("/setsite — 设置 PT 站地址")
        if not passkey:
            lines.append("/setpasskey — 设置 Passkey")
        if not (dl_type and dl_host):
            lines.append("/setds 或 /setqb 或 /settr — 设置下载客户端")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ------------------------------------------------------------------
# /setai — 设置 AI API Key (OpenRouter)
# ------------------------------------------------------------------

@require_owner
async def setai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setai <api_key> — 设置 OpenRouter API Key 以启用智能搜索。"""
    await _delete_user_message(update, context)

    db = context.bot_data["db"]

    if not context.args:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="请输入 OpenRouter API Key：\n注册地址：https://openrouter.ai",
            reply_markup=ForceReply(selective=True),
        )
        return

    api_key = context.args[0]
    db.set_setting("ai_api_key", api_key)

    # 重新初始化 AI 客户端
    from bot.ai import AIClient
    model = db.get_setting("ai_model") or "deepseek/deepseek-v3.2"
    context.bot_data["ai_client"] = AIClient(api_key, model=model)

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="AI API Key 已保存！\n"
             f"当前模型：{model}\n"
             "现在可以使用 /ask 进行智能搜索。",
    )
    logger.info("AI API Key 已更新")


# ------------------------------------------------------------------
# /setmodel — 切换 AI 模型
# ------------------------------------------------------------------

@require_owner
async def setmodel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setmodel <模型名> — 切换 AI 模型。"""
    db = context.bot_data["db"]

    if not context.args:
        current = db.get_setting("ai_model") or "deepseek/deepseek-v3.2"
        await update.message.reply_text(
            f"请输入模型名称：\n当前：{current}\n"
            "例如：deepseek/deepseek-v3.2\n"
            "模型列表：https://openrouter.ai/models",
            reply_markup=ForceReply(selective=True),
        )
        return

    model = context.args[0]
    db.set_setting("ai_model", model)

    # 更新现有 AI 客户端的模型
    ai_client = context.bot_data.get("ai_client")
    if ai_client:
        ai_client.model = model

    await update.message.reply_text(f"AI 模型已切换为：{model}")
    logger.info("AI 模型已切换为: %s", model)


# ------------------------------------------------------------------
# /setsearch — 设置搜索 API Key (Tavily)
# ------------------------------------------------------------------

@require_owner
async def setsearch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setsearch <api_key> — 设置 Tavily API Key 以启用联网搜索。"""
    await _delete_user_message(update, context)

    db = context.bot_data["db"]

    if not context.args:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="请输入 Tavily API Key：\n注册地址：https://tavily.com",
            reply_markup=ForceReply(selective=True),
        )
        return

    api_key = context.args[0]
    db.set_setting("search_api_key", api_key)

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="搜索 API Key 已保存！\n/ask 智能搜索现在支持联网获取最新信息。",
    )
    logger.info("搜索 API Key 已更新")
