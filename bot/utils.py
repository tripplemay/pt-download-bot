"""通用工具函数"""


def truncate(text: str, max_len: int = 55) -> str:
    """截断字符串，超过 max_len 时在末尾加省略号。"""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"
