"""权限校验装饰器"""

from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes


def require_auth(func):
    """要求用户已授权（role 为 user 或 owner）。"""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        db = context.bot_data["db"]

        if db.is_authorized(user_id):
            return await func(update, context)

        # 未授权 — 根据角色给出不同提示
        user = db.get_user(user_id)
        if user is None:
            await update.message.reply_text(
                "你还没有使用权限，请先发送 /apply 提交申请。",
                parse_mode="HTML",
            )
        elif user.role == "pending":
            await update.message.reply_text(
                "你的申请正在等待管理员审批，请耐心等待。",
                parse_mode="HTML",
            )
        elif user.role == "banned":
            await update.message.reply_text(
                "你已被封禁，无法使用此功能。",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                "权限不足，无法执行此操作。",
                parse_mode="HTML",
            )

    return wrapper


def require_owner(func):
    """要求用户为 Owner。"""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        db = context.bot_data["db"]

        if db.is_owner(user_id):
            return await func(update, context)

        await update.message.reply_text(
            "此命令仅限管理员使用。",
            parse_mode="HTML",
        )

    return wrapper
