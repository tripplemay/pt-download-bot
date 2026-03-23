"""管理员命令：/users, /pending, /ban, /unban"""

from telegram import Update
from telegram.ext import ContextTypes

from bot.middleware import require_owner

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
            "用法：/ban &lt;用户ID&gt;", parse_mode="HTML"
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
            "用法：/unban &lt;用户ID&gt;", parse_mode="HTML"
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
