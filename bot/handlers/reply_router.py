"""ForceReply 回复路由 — 处理用户对提示消息的回复。"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# 命令提示文本 → 处理函数的映射
# Key 是 bot 发送的提示消息的前缀文本，value 是要调用的命令名
_REPLY_PROMPTS = {
    "请输入搜索关键词": "s",
    "请描述你想找的影片": "ask",
    "请输入 PT 站地址": "setsite",
    "请输入 Passkey": "setpasskey",
    "请输入 Cookie": "setcookie",
    "请输入 TMDB API Key": "settmdb",
    "请输入 OpenRouter API Key": "setai",
    "请输入模型名称": "setmodel",
    "请输入 Tavily API Key": "setsearch",
    "请输入 Download Station 连接信息": "setds",
    "请输入 qBittorrent 连接信息": "setqb",
    "请输入 Transmission 连接信息": "settr",
    "请输入要封禁的用户 ID": "ban",
    "请输入要解封的用户 ID": "unban",
    "请输入消息内容（格式：用户ID 消息）": "msg",
    "请输入广播消息内容": "broadcast",
}


async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理用户对 ForceReply 提示消息的回复。

    通过匹配 reply_to_message 的文本前缀，确定原始命令，
    将用户输入设为 context.args，然后调用对应的命令处理函数。
    """
    if not update.message or not update.message.reply_to_message:
        return

    # 只处理回复给 bot 的消息
    bot_user = await context.bot.get_me()
    replied_to = update.message.reply_to_message
    if not replied_to.from_user or replied_to.from_user.id != bot_user.id:
        return

    original_text = replied_to.text or ""
    user_text = update.message.text or ""
    if not user_text.strip():
        return

    # 匹配提示文本
    matched_command = None
    matched_prefix = None
    for prompt_prefix, command in _REPLY_PROMPTS.items():
        if original_text.startswith(prompt_prefix):
            matched_command = command
            matched_prefix = prompt_prefix
            break

    if not matched_command:
        return

    # 设置 context.args 为用户输入的内容（按空格分割）
    context.args = user_text.strip().split()

    # 路由到对应的命令处理函数
    from bot.handlers.search import search_command, ask_command
    from bot.handlers.settings import (
        setsite_command, setpasskey_command, settmdb_command,
        setds_command, setqb_command, settr_command,
        setai_command, setmodel_command, setsearch_command,
    )
    from bot.handlers.admin import ban_command, unban_command, setcookie_command, msg_command, broadcast_command

    handlers = {
        "s": search_command,
        "ask": ask_command,
        "setsite": setsite_command,
        "setpasskey": setpasskey_command,
        "setcookie": setcookie_command,
        "settmdb": settmdb_command,
        "setai": setai_command,
        "setmodel": setmodel_command,
        "setsearch": setsearch_command,
        "setds": setds_command,
        "setqb": setqb_command,
        "settr": settr_command,
        "ban": ban_command,
        "unban": unban_command,
        "msg": msg_command,
        "broadcast": broadcast_command,
    }

    handler_func = handlers.get(matched_command)
    if handler_func:
        logger.info("ForceReply 路由: %s → /%s (args=%s)",
                     matched_prefix, matched_command, context.args[:3])
        await handler_func(update, context)
