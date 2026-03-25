"""管理员命令：/users, /pending, /ban, /unban"""

import logging

from telegram import ForceReply, Update
from telegram.ext import ContextTypes

from bot.middleware import require_owner
from bot.pt.nexusphp import CookieExpiredError
from bot.handlers.search import _search_result_cache

ROLE_EMOJI = {
    "owner": "👑",
    "user": "✅",
    "pending": "⏳",
    "banned": "🚫",
}


@require_owner
async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """列出所有用户。"""
    db = context.bot_data["db"]
    users = db.get_all_users()

    if not users:
        await update.message.reply_text("暂无用户记录。", parse_mode="HTML")
        return

    lines = ["<b>所有用户</b>\n"]
    for u in users:
        emoji = ROLE_EMOJI.get(u.role, "❓")
        uname = f" @{u.username}" if u.username else ""
        lines.append(f"{emoji} {u.display_name}{uname} — <code>{u.telegram_id}</code>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@require_owner
async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """列出待审批用户。"""
    db = context.bot_data["db"]
    users = db.get_pending_users()

    if not users:
        await update.message.reply_text("没有待审批的用户。", parse_mode="HTML")
        return

    lines = ["<b>待审批用户</b>\n"]
    for u in users:
        uname = f" @{u.username}" if u.username else ""
        lines.append(
            f"⏳ {u.display_name}{uname} — <code>{u.telegram_id}</code>\n"
            f"   申请时间：{u.applied_at or '未知'}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@require_owner
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ban <用户ID> — 封禁用户。"""
    db = context.bot_data["db"]

    if not context.args:
        await update.message.reply_text(
            "请输入要封禁的用户 ID：",
            reply_markup=ForceReply(selective=True),
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("用户 ID 必须是数字。", parse_mode="HTML")
        return

    success = db.ban_user(target_id)
    if success:
        await update.message.reply_text(
            f"已封禁用户 <code>{target_id}</code>。", parse_mode="HTML"
        )
        # 通知被封禁用户
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="你已被管理员封禁，无法继续使用此 Bot。",
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        await update.message.reply_text(
            f"操作失败：用户 <code>{target_id}</code> 不存在或无法封禁（可能是 Owner）。",
            parse_mode="HTML",
        )


@require_owner
async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/unban <用户ID> — 解封用户。"""
    db = context.bot_data["db"]

    if not context.args:
        await update.message.reply_text(
            "请输入要解封的用户 ID：",
            reply_markup=ForceReply(selective=True),
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("用户 ID 必须是数字。", parse_mode="HTML")
        return

    success = db.unban_user(target_id)
    if success:
        await update.message.reply_text(
            f"已解封用户 <code>{target_id}</code>。", parse_mode="HTML"
        )
        # 通知被解封用户
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="你已被管理员解封，现在可以继续使用 Bot 了。\n发送 /help 查看可用命令。",
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        await update.message.reply_text(
            f"操作失败：用户 <code>{target_id}</code> 不存在或当前未被封禁。",
            parse_mode="HTML",
        )


logger = logging.getLogger(__name__)


@require_owner
async def setcookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setcookie <cookie值> — 设置 PT 站 Cookie 以启用网页版搜索。"""
    db = context.bot_data["db"]
    pt_client = context.bot_data.get("pt_client")

    # 立即删除包含 Cookie 的消息（保护安全）
    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
    except Exception:
        pass  # 可能没有删除权限

    if not context.args:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="请输入 Cookie：\n"
                 "获取方法：浏览器登录 PT 站 → F12 → Network → "
                 "点击任意请求 → 复制 Cookie 头的值",
            reply_markup=ForceReply(selective=True),
        )
        return

    cookie = " ".join(context.args)
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="正在验证 Cookie...",
    )

    # 验证 Cookie：尝试用它请求 torrents.php
    if not pt_client:
        db.set_setting("pt_cookie", cookie)
        _search_result_cache.clear()
        await msg.edit_text(
            "Cookie 已保存（PT 站尚未配置，无法验证）。\n"
            "请先用 /setsite 和 /setpasskey 配置 PT 站。"
        )
        return

    try:
        results = await pt_client.search_web("test", cookie=cookie, search_area=0)
        # 如果没抛 CookieExpiredError，说明 Cookie 有效
        db.set_setting("pt_cookie", cookie)
        _search_result_cache.clear()
        await msg.edit_text(
            "Cookie 已保存并验证通过！\n"
            "网页版搜索已启用（搜索结果将更完整）。"
        )
        logger.info("PT Cookie 已更新")
    except CookieExpiredError:
        await msg.edit_text(
            "Cookie 无效，请检查后重试。\n\n"
            "获取方法：浏览器登录 PT 站 → F12 → Network → "
            "点击任意请求 → 复制 Cookie 头的值"
        )
    except Exception:
        logger.exception("Cookie 验证异常")
        await msg.edit_text("Cookie 验证时出错，请稍后重试。")


@require_owner
async def cookiestatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cookiestatus — 查看 Cookie 状态。"""
    db = context.bot_data["db"]
    cookie = db.get_setting("pt_cookie")

    if cookie:
        updated_at = db.get_setting_updated_at("pt_cookie") or "未知"
        await update.message.reply_text(
            f"<b>Cookie 状态</b>\n\n"
            f"状态：已配置\n"
            f"设置时间：{updated_at}\n"
            f"搜索模式：网页版（结果完整）",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            "<b>Cookie 状态</b>\n\n"
            "状态：未配置\n"
            "搜索模式：RSS（结果有限，最多约10条）\n\n"
            "使用 /setcookie 配置 Cookie 以获取完整搜索结果。",
            parse_mode="HTML",
        )
