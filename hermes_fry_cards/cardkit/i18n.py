"""飞书卡片 i18n — 中英双语文本映射."""

from __future__ import annotations

__all__ = [
    "_LOCALES",
    "_T",
    "_content_t",
    "_i18n",
    "_t",
]

_LOCALES = ["zh_cn", "en_us"]

_T: dict[str, tuple[str, str]] = {
    "status_completed": ("✅ Completed", "✅ 已完成"),
    "status_error": ("❌ Error", "❌ 出错"),
    "status_stopped": ("🛑 Stopped", "🛑 已停止"),
    "elapsed": ("Elapsed {}", "耗时 {}"),
    "context": ("Context {}", "上下文 {}"),
    "cache_hit": ("Cache hit {}%", "缓存命中 {}%"),
    "processing": ("Processing...", "处理中..."),
    "processing_prefix": ("💭 Processing...", "💭 处理中..."),
    "tool_use": ("Tool use", "工具执行"),
    "tool_running": ("Tool running", "工具执行中"),
    "tool_omitted": ("… and {} earlier tool steps", "…另有 {} 步工具调用"),
    "tool_pending": ("🛠️ Tool use pending", "🛠️ 等待工具执行"),
    "steps": ("{} step{}", "{} 步"),
    "thought": ("Thought", "思考"),
    "thinking_panel": ("Thinking", "思考中"),
    "thought_for": ("Thought for {}", "思考了 {}"),
    "done": ("Done.", "完成。"),
    # 通知区（#43）：提示渲染 Hermes 原文，不再套「💡 提示」标签（#43 反馈），
    # 下列词条仅供外部引用/排查保留。
    "notice": ("Notice", "提示"),
    "notice_redirect": ("Redirected", "已重定向"),
    "notice_background": ("Background task", "后台任务"),
    "notice_gateway_restart": ("Gateway restarted", "网关重启"),
    # 完结卡 AI 摘要（终态卡封口后异步补一段）
    "ai_summary_label": ("🔍 **AI summary**", "🔍 **AI 摘要**"),
    "card_overflow_fallback": (
        "This reply exceeded the Feishu card element limit; the full answer is delivered as plain text below.",
        "本次回复超出飞书卡片元素上限，完整内容以纯文本在下方送达。",
    ),
    # 内容层提示语（markdown 正文内嵌，无法走 i18n_content 双语 dict），
    # 由 _content_t 按 streaming.content_lang 构建时选取。
    "table_overflow_notice": (
        "The following tables have been converted to compact field lists to fit Feishu card limits; content is fully preserved.",
        "后续表格已转换为紧凑字段列表，以兼容飞书卡片限制；内容完整保留。",
    ),
    "clamp_truncated": (
        "… (content truncated to prevent card overflow) …",
        "…（内容过长已截断，防卡片溢出）…",
    ),
    "clamp_middle_omitted": (
        "… (middle section omitted; opening context and final conclusions preserved, to prevent card overflow) …",
        "…（中段分析已省略，保留开头背景与末尾核心结论，防卡片溢出）…",
    ),
}


def _i18n(en: str, zh: str) -> dict[str, str]:
    return {"zh_cn": zh, "en_us": en}


def _t(key: str) -> dict[str, str]:
    """简写: _t("processing") → _i18n(*_T["processing"])。"""
    return _i18n(*_T[key])


_CONTENT_LANG_CACHE: str | None = None


def _content_t(key: str) -> str:
    """内容层单语词条：按 ``streaming.content_lang`` 选取，模块级缓存（重启生效）."""
    if key not in _T:
        return key  # 非词条（如 Hermes 原文）原样返回，由调用方负责语言选取
    global _CONTENT_LANG_CACHE
    if _CONTENT_LANG_CACHE is None:
        try:
            from ..config import Config

            _CONTENT_LANG_CACHE = Config().content_lang
        except Exception:
            _CONTENT_LANG_CACHE = "zh"
    en, zh = _T[key]
    return en if _CONTENT_LANG_CACHE == "en" else zh
