"""/start, /apply, /help 命令及审批回调"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """根据用户角色显示不同欢迎语。"""
    db = context.bot_data["db"]
    user = db.get_user(update.effective_user.id)

    if user is None:
        text = (
            "欢迎使用 <b>PT 下载助手</b>！\n\n"
            "你目前还没有使用权限。\n"
            "请发送 /apply 提交使用申请。"
        )
    elif user.role == "pending":
        text = (
            "你的申请已提交，正在等待管理员审批。\n"
            "审批通过后会收到通知。"
        )
    elif user.role == "banned":
        text = "你已被封禁，无法使用此 Bot。"
    elif user.role == "owner":
        text = (
            "欢迎回来，<b>管理员</b>！\n\n"
            "发送 /help 查看可用命令。"
        )
    else:
        text = (
            "欢迎回来！\n\n"
            "发送 /help 查看可用命令。"
        )

    await update.message.reply_text(text, parse_mode="HTML")


async def apply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """用户提交使用申请，通知 Owner 审批。"""
    db = context.bot_data["db"]
    tg_user = update.effective_user
    user_id = tg_user.id
    username = tg_user.username or ""
    display_name = tg_user.full_name or str(user_id)

    # 检查是否已有记录
    existing = db.get_user(user_id)
    if existing is not None:
        role_msg = {
            "owner": "你是管理员，无需申请。",
            "user": "你已经是授权用户了，无需重复申请。",
            "pending": "你已经提交过申请，请等待管理员审批。",
            "banned": "你已被封禁，无法提交申请。",
        }
        await update.message.reply_text(
            role_msg.get(existing.role, "操作失败。"), parse_mode="HTML"
        )
        return

    ok = db.apply_user(user_id, username, display_name)
    if not ok:
        await update.message.reply_text("申请提交失败，请稍后重试。", parse_mode="HTML")
        return

    await update.message.reply_text(
        "申请已提交！请等待管理员审批。", parse_mode="HTML"
    )

    # 通知 Owner
    owner_id = context.bot_data["owner_id"]
    at_username = f"@{username}" if username else "无"
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ 通过", callback_data=f"approve:{user_id}"),
                InlineKeyboardButton("❌ 拒绝", callback_data=f"reject:{user_id}"),
            ]
        ]
    )
    await context.bot.send_message(
        chat_id=owner_id,
        text=(
            f"<b>新用户申请</b>\n\n"
            f"昵称：{display_name}\n"
            f"用户名：{at_username}\n"
            f"ID：<code>{user_id}</code>"
        ),
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 Owner 点击 审批/拒绝 按钮的回调。"""
    query = update.callback_query
    await query.answer()

    db = context.bot_data["db"]
    owner_id = context.bot_data["owner_id"]

    # 仅 Owner 可操作
    if query.from_user.id != owner_id:
        await query.answer("仅管理员可操作。", show_alert=True)
        return

    data = query.data  # approve:123456 or reject:123456
    action, user_id_str = data.split(":", 1)
    user_id = int(user_id_str)

    if action == "approve":
        success = db.approve_user(user_id, owner_id)
        if success:
            await query.edit_message_text(
                f"{query.message.text}\n\n✅ <b>已通过</b>", parse_mode="HTML"
            )
            # 通知用户
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="🎉 你的申请已通过！现在可以使用 Bot 了。\n发送 /help 查看可用命令。",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        else:
            await query.edit_message_text(
                f"{query.message.text}\n\n⚠️ 操作失败（用户可能已被处理）",
                parse_mode="HTML",
            )

    elif action == "reject":
        success = db.reject_user(user_id)
        if success:
            await query.edit_message_text(
                f"{query.message.text}\n\n❌ <b>已拒绝</b>", parse_mode="HTML"
            )
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="很遗憾，你的申请未通过。如有疑问请联系管理员。",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        else:
            await query.edit_message_text(
                f"{query.message.text}\n\n⚠️ 操作失败（用户可能已被处理）",
                parse_mode="HTML",
            )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """根据角色显示不同命令列表。"""
    db = context.bot_data["db"]
    user = db.get_user(update.effective_user.id)

    base_commands = (
        "<b>可用命令</b>\n\n"
        "/start — 开始使用\n"
        "/help — 查看帮助\n"
    )

    if user is None or user.role == "pending":
        text = base_commands + "/apply — 提交使用申请\n"
    elif user.role == "banned":
        text = "你已被封禁，无法使用此 Bot。"
    elif user.role == "owner":
        text = (
            base_commands
            + "/search — 搜索种子\n"
            + "/rss — RSS 订阅管理\n"
            + "\n<b>管理命令</b>\n\n"
            + "/users — 查看所有用户\n"
            + "/pending — 查看待审批用户\n"
            + "/ban &lt;用户ID&gt; — 封禁用户\n"
            + "/unban &lt;用户ID&gt; — 解封用户\n"
        )
    else:
        text = (
            base_commands
            + "/search — 搜索种子\n"
            + "/rss — RSS 订阅管理\n"
        )

    await update.message.reply_text(text, parse_mode="HTML")
