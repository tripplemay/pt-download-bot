"""设置命令 — Owner 通过 Telegram 对话配置 Bot 参数"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from telegram import Update
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
            "用法：/setsite &lt;PT站地址&gt;\n"
            "例如：/setsite https://ptchdbits.co",
            parse_mode="HTML",
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
            text="用法：/setpasskey &lt;Passkey&gt;",
            parse_mode="HTML",
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
            text=(
                "用法：/settmdb &lt;API_Key&gt;\n\n"
                "免费注册：https://www.themoviedb.org/settings/api"
            ),
            parse_mode="HTML",
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
            text=f"用法：/set{client_type[:2]} &lt;地址&gt; &lt;用户名&gt; &lt;密码&gt;\n"
                 f"例如：/set{client_type[:2]} http://localhost:5000 admin password123",
            parse_mode="HTML",
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
