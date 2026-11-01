"""CardKit v2.0 卡片构建器 — i18n、元素构建、卡片组装."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..streaming.segments import Segment, SegmentType
from ..streaming.tooluse import ToolDisplayStep
from .i18n import _LOCALES, _T, _i18n, _t
from .markdown import (
    _REASONING_MAX_BYTES,
    _downgrade_tables,
    _split_long_text,
    clamp_utf8,
    optimize_markdown_style,
    parse_table_block,
    split_markdown_tables,
)

STREAMING_ELEMENT_ID = "streaming_content"
REASONING_ELEMENT_ID = "reasoning_content"
REASONING_TEXT_ELEMENT_ID = "reasoning_text"
TOOL_PANEL_ELEMENT_ID = "tool_panel"
UNIFIED_PANEL_ELEMENT_ID = "unified_panel"
LOADING_ELEMENT_ID = "loading_icon"
_LOADING_ELEMENT_ID = LOADING_ELEMENT_ID  # 兼容旧私有引用
_LOADING_IMG_KEY = "img_v3_02vb_496bec09-4b43-4773-ad6b-0cdd103cd2bg"

# 工具面板最多渲染的步骤数（#46）：50+ 步长任务时整卡 JSON 随步数线性膨胀
# （实测 50 步 ≈ 37KB），10 Hz 整卡重绘会卡死飞书客户端。header 保留总步数，
# 更早的步骤折叠成一行摘要；估算端（segment_helper.estimate_tool_elements）按同一上限取值。
MAX_TOOL_PANEL_STEPS = 30


def _truncate_model(name: str) -> str:
    """截断模型名：or/lc/LongCat-2.0 → ⇲LongCat-2.0（保留最后一段）.

    如果开启 truncate_model_name 才调用；未开启时原样返回。
    """
    if not name:
        return name
    parts = name.split("/")
    if len(parts) > 1:
        return f"⇲{parts[-1]}"
    return name


def _display_model(name: str) -> str:
    """模型显示名：别名优先（~/.hermes/model_aliases.json 子串匹配，支持时段人设），未命中回落截断.

    别名文件由 Config().model_aliases() 每次重读（热更新）；对象条目读取时按北京时间
    解析为当前时段显示名。model_aliases_enabled 关闭时整体忽略别名回落截断。
    """
    if not name:
        return name
    from ..config import Config

    cfg = Config()
    if cfg.model_aliases_enabled:
        aliases = cfg.model_aliases()
        lowered = name.lower()
        for key, alias in aliases.items():
            if key and key in lowered:
                return alias
    if cfg.truncate_model_name:
        return _truncate_model(name)
    return name


def _collapsible_panel(
    *,
    expanded: bool,
    title_el: dict,
    elements: list[dict],
    vertical_spacing: str = "4px",
    icon_position: str = "right",
) -> dict:
    icon_el = {
        "tag": "standard_icon",
        "token": "down-small-ccm_outlined",
        "size": "16px 16px",
    }
    if icon_position == "right":
        icon_el["color"] = "grey"
    return {
        "tag": "collapsible_panel",
        "expanded": expanded,
        "header": {
            "title": title_el,
            "vertical_align": "center",
            "icon": icon_el,
            "icon_position": icon_position,
            "icon_expanded_angle": -180,
        },
        "border": {"color": "grey", "corner_radius": "5px"},
        "vertical_spacing": vertical_spacing,
        "padding": "8px 8px 8px 8px",
        "elements": elements,
    }


def _streaming_element(
    content: str = "",
    *,
    element_id: str = STREAMING_ELEMENT_ID,
    text_size: str = "normal_v2",
) -> dict:
    return {
        "tag": "markdown",
        "content": content,
        "text_align": "left",
        "text_size": text_size,
        "margin": "0px 0px 0px 0px",
        "element_id": element_id,
    }


_HEADER_STATES: dict[str, dict[str, str]] = {
    "streaming": {"template": "blue", "i18n_key": "processing_prefix"},
    "completed": {"template": "green", "i18n_key": "status_completed"},
    "error": {"template": "red", "i18n_key": "status_error"},
    "stopped": {"template": "red", "i18n_key": "status_stopped"},
}


def _build_header(status: str) -> dict[str, Any]:
    """构建卡片级 header — 流式蓝 / 完成绿 / 停止红."""
    cfg = _HEADER_STATES.get(status, _HEADER_STATES["completed"])
    en_text, zh_text = _T[cfg["i18n_key"]]
    return {
        "title": {
            "tag": "plain_text",
            "content": en_text,
            "i18n_content": _i18n(en_text, zh_text),
        },
        "template": cfg["template"],
    }


_NOTICE_ELEMENT_ID = "fry_notice"
# 公开别名：控制器建卡时要把这个 id 登记给段状态（首段正文原地接管提示元素）。
NOTICE_ELEMENT_ID = _NOTICE_ELEMENT_ID


def notice_element_id(index: int) -> str:
    """第 ``index`` 条提示的元素 id：``fry_notice`` / ``fry_notice_1`` / ``fry_notice_2`` …

    完成态可能同时命中多条提示（重定向 + 后台回执），`notices` 只按文本去重，
    不同文本会各占一个元素；固定 id 会让卡里出现重复 element_id（#43 评审）。
    """
    return _NOTICE_ELEMENT_ID if index == 0 else f"{_NOTICE_ELEMENT_ID}_{index}"


def build_notice_elements(texts: list[str] | None, *, offset: int = 0) -> list[dict]:
    """通知区元素列表：每条提示一个元素，id 按序号派生，保证卡内唯一.

    ``offset``：顶部区与底部区共存时，底部元素 id 接在顶部序号之后派生，
    避免两个区撞同一个 ``fry_notice``（#43 反馈）。
    """
    return [
        build_notice_element(text, element_id=notice_element_id(offset + index))
        for index, text in enumerate(texts or [])
    ]


def build_notice_element(text: str, *, element_id: str = _NOTICE_ELEMENT_ID) -> dict:
    """通知区的单条提示（Hermes 原文，原样渲染）.

    文案不新增也不包装（#43 反馈）：忙碌回执 / 重定向文案本身已是完整一句话，
    再套「💡 提示」标签反而像一段新内容。原样写入；视觉区分由位置（底部通知位）
    承担，不写进 content。
    """
    return {
        "tag": "markdown",
        "element_id": element_id,
        "content": text,
    }


def build_streaming_notice_element(
    text: str, *, element_id: str, text_size: str = "normal_v2"
) -> dict:
    """流式态通知槽元素（与建卡时写入的提示元素同构）.

    走流式元素形态（固定 id + 正文字号），所以首段正文能在同一个元素上原地覆盖；
    回合中途到达的回执由控制器用同一形态补画到 loading 之前，视觉位置与建卡时一致。
    """
    return _streaming_element(content=text, element_id=element_id, text_size=text_size)


def _loading_element() -> dict:
    return {
        "tag": "markdown",
        "content": " ",
        "icon": {
            "tag": "custom_icon",
            "img_key": _LOADING_IMG_KEY,
            "size": "16px 16px",
        },
        "element_id": _LOADING_ELEMENT_ID,
    }


_TABLE_PAGE_SIZE = 10
_TABLE_ROW_MAX_HEIGHT = "999px"


def _append_content_elements(
    elements: list[dict], content: str, *, text_size: str | None = None
) -> None:
    """正文 → 元素列表：文本片段出 markdown 元素，表格片段出原生 table 元素.

    仅传字符串/纯文本组件的卡片（完成态、cron、后台推送）共用此入口，
    保证同一个 markdown 表格在任何卡片里都渲染成同一种元素。
    """
    content = _downgrade_tables(optimize_markdown_style(content))
    content = clamp_utf8(content, preserve_tail=True)
    for kind, part in split_markdown_tables(content):
        if kind == "table":
            parsed = parse_table_block(part)
            if parsed:
                elements.append(_build_table_element(*parsed))
                continue
        for chunk in _split_long_text(part):
            if not chunk.strip():
                continue
            element: dict[str, Any] = {"tag": "markdown", "content": chunk}
            if text_size:
                element["text_size"] = text_size
            elements.append(element)


def _build_table_element(header: list[str], rows: list[list[str]]) -> dict[str, Any]:
    """飞书原生 table 元素 — 列宽 auto + 行高 auto.

    markdown 表格不支持设置列宽/行高：长单元格折行后被固定行高裁掉。
    原生表格可设 `columns[].width: auto`（按内容给宽）与 `row_height: auto`，
    长文本完整显示；三列总宽超出画布时表格左右可滑动。
    """
    columns = [
        {
            "name": f"c{index}",
            "display_name": name,
            "data_type": "lark_md",
            "width": "auto",
        }
        for index, name in enumerate(header)
    ]
    table_rows = [
        {
            f"c{index}": row[index] if index < len(row) else ""
            for index in range(len(header))
        }
        for row in rows
    ]
    return {
        "tag": "table",
        "page_size": _TABLE_PAGE_SIZE,
        "row_height": "auto",
        "row_max_height": _TABLE_ROW_MAX_HEIGHT,
        "header_style": {
            "text_align": "left",
            "text_size": "notation",
            "background_style": "none",
            "text_color": "default",
            "bold": True,
            "lines": 1,
        },
        "columns": columns,
        "rows": table_rows,
    }


def _build_tool_panel(
    steps: list[ToolDisplayStep],
    elapsed_ms: float = 0,
    *,
    expanded: bool = True,
    element_id: str | None = TOOL_PANEL_ELEMENT_ID,
    running: bool = False,
    compact: bool = False,
) -> dict:
    en_t, zh_t = _T["tool_running"] if running else _T["tool_use"]
    en_parts, zh_parts = [en_t], [zh_t]
    if steps:
        tpl_en, tpl_zh = _T["steps"]
        en_parts.append(tpl_en.format(len(steps), "s" if len(steps) > 1 else ""))
        zh_parts.append(tpl_zh.format(len(steps), ""))
    if elapsed_ms > 0:
        en_parts.append(f"({_format_elapsed(elapsed_ms)})")
        zh_parts.append(f"({_format_elapsed(elapsed_ms)})")

    children: list[dict] = []
    # 面板只渲染最近 N 步（#46）：全部步骤都进面板时卡片体积随任务长度无界增长，
    # 10 Hz 整卡重绘会把飞书客户端渲染打满。header 保留总步数，更早的折叠成一行摘要。
    shown = steps
    omitted = 0
    if len(steps) > MAX_TOOL_PANEL_STEPS:
        omitted = len(steps) - MAX_TOOL_PANEL_STEPS
        shown = steps[-MAX_TOOL_PANEL_STEPS:]
        tpl_en, tpl_zh = _T["tool_omitted"]
        summary_en = tpl_en.format(omitted)
        summary_zh = tpl_zh.format(omitted)
        children.append({
            "tag": "markdown",
            "content": summary_en,
            "i18n_content": _i18n(summary_en, summary_zh),
            "text_size": "notation",
        })
    for s in shown:
        # 紧凑模式（#53）：每步只渲染标题行（工具名 · 状态），不渲染入参与输出。
        # 完成态整卡重建会把面板子元素连同流式期已提交的一并累计，CardKit 按
        # 「已有 + 本次」计数，30 步全量面板足以越过 200 硬上限 → 300305。
        if compact:
            children.append(_build_tool_step_title(s))
        else:
            children.extend(_build_tool_step_elements(s))

    panel = _collapsible_panel(
        expanded=expanded,
        title_el={
            "tag": "plain_text",
            "content": f"🔧 {' · '.join(en_parts)}",
            "i18n_content": _i18n(f"🔧 {' · '.join(en_parts)}", f"🔧 {' · '.join(zh_parts)}"),
            "text_color": "grey",
            "text_size": "notation",
        },
        elements=children,
    )
    if element_id:
        panel["element_id"] = element_id
    return panel


def _build_tool_step_elements(step: ToolDisplayStep) -> list[dict]:
    elements: list[dict] = [_build_tool_step_title(step)]
    detail = _build_tool_step_detail(step)
    if detail:
        elements.append(detail)
    output = _build_tool_step_output(step)
    if output:
        elements.append(output)
    return elements


def _build_tool_step_title(step: ToolDisplayStep) -> dict:
    status = step.get("status", "running")
    status_info = _tool_status_info(status)
    title = step.get("title", step.get("name", "tool"))
    content = f"**{_escape_md(title)}** · <font color='{status_info['color']}'>{status_info['label']}</font>"
    return {
        "tag": "div",
        "icon": {
            "tag": "standard_icon",
            "token": step.get("icon", "tool_02"),
            "color": "grey",
        },
        "text": {
            "tag": "lark_md",
            "content": content,
            "text_size": "notation",
        },
    }


def _build_tool_step_detail(step: ToolDisplayStep) -> dict | None:
    detail = step.get("detail", "").strip()
    if not detail:
        return None
    return {
        "tag": "div",
        "margin": "0px 0px 0px 22px",
        "text": {
            "tag": "plain_text",
            "content": detail,
            "text_color": "grey",
            "text_size": "notation",
        },
    }


def _build_tool_step_output(step: ToolDisplayStep) -> dict | None:
    error_block = step.get("error_block")
    result_block = step.get("result_block")

    lines: list[str] = []
    if error_block:
        lines.append("**Error**")
        lines.append(
            error_block.get("fenced")
            or _format_code_block(error_block.get("content", ""), error_block.get("language", "text"))
        )
    elif result_block:
        lines.append("**Result**")
        lines.append(
            result_block.get("fenced")
            or _format_code_block(result_block.get("content", ""), result_block.get("language", "json"))
        )

    if not lines:
        return None

    return {
        "tag": "div",
        "margin": "0px 0px 0px 22px",
        "text": {
            "tag": "lark_md",
            "content": "\n".join(lines),
            "text_size": "notation",
        },
    }


def _tool_status_info(status: str) -> dict[str, str]:
    return {
        "running": {"label": "Running", "color": "turquoise"},
        "success": {"label": "Succeeded", "color": "green"},
        "error": {"label": "Failed", "color": "red"},
    }.get(status, {"label": status.capitalize(), "color": "grey"})


def _format_code_block(content: str, language: str) -> str:
    normalized = content.replace("\r\n", "\n").strip()
    fence = "`" * max(3, _longest_backtick_run(normalized) + 1)
    return f"{fence}{language}\n{normalized}\n{fence}"


def _longest_backtick_run(value: str) -> int:
    matches = re.findall(r"`+", value)
    return max((len(m) for m in matches), default=0)


def _escape_md(value: str) -> str:
    return re.sub(r"([`*_{}\[\]<>])", r"\\\1", value.replace("\\", "\\\\"))


def _build_reasoning_panel(
    text: str, elapsed_ms: float = 0, *, expanded: bool = False, element_id: str | None = None,
    text_element_id: str | None = REASONING_TEXT_ELEMENT_ID,
) -> dict:
    if elapsed_ms > 0:
        d = _format_elapsed(elapsed_ms)
        en_label, zh_label = _T["thought_for"][0].format(d), _T["thought_for"][1].format(d)
    elif not text.strip():
        en_label, zh_label = _T["thinking_panel"]
    else:
        en_label, zh_label = _T["thought"]
    panel = _collapsible_panel(
        expanded=expanded,
        title_el={
            "tag": "plain_text",
            "content": f"💭 {en_label}",
            "i18n_content": _i18n(f"💭 {en_label}", f"💭 {zh_label}"),
            "text_color": "grey",
            "text_size": "notation",
        },
        elements=[{
            "tag": "markdown",
            "content": text,
            "text_size": "notation",
            **({"element_id": text_element_id} if text_element_id else {}),
        }],
        vertical_spacing="8px",
    )
    if element_id:
        panel["element_id"] = element_id
    return panel


def _render_panel_field(
    name: str,
    data: dict,
    *,
    reasoning_count: int,
    tool_count: int,
    panel_elapsed_ms: int | float,
    show_context: bool,
    context_mode: str,
) -> tuple[str | None, str | None]:
    """统一面板 header 字段渲染 — 与 footer 共用字段池，外加面板专属计数.

    面板 flair：model 前缀 🍟、elapsed 前缀 ⏱️（工具执行/会话耗时）、💭/🔧 为面板
    专属计数（推理轮数/工具步数）；context 按 context_display_mode 渲染；
    其余共享字段（speed/cache/tokens/status）原样回落 footer 渲染器。
    """
    if name == "model":
        v = (data.get("model") or "").strip()
        if v:
            v = f"🍟 {_display_model(v)}"
            return v, v
        return None, None
    if name == "reasoning":
        if reasoning_count > 0:
            return f"💭{reasoning_count}", f"💭{reasoning_count}"
        return None, None
    if name == "tools":
        if tool_count > 0:
            return f"🔧{tool_count}", f"🔧{tool_count}"
        return None, None
    if name == "elapsed":
        if panel_elapsed_ms > 0:
            v = f"⏱️ {_format_elapsed(panel_elapsed_ms)}"
            return v, v
        return None, None
    if name == "context":
        used = data.get("context_used", 0) or 0
        max_c = data.get("context_max", 0) or 0
        if show_context and used > 0 and max_c > 0:
            if context_mode == "text":
                v = _context_text(used, max_c)
            elif context_mode in ("bar", "block"):
                # block 样式桌面/移动端显示不一致，回落 bar
                v = f"[{_context_progress_bar(used, max_c)}] {_context_pct(used, max_c)}"
            else:  # text_bar / block_text
                v = _context_progress_with_text(used, max_c)
            return v, v
        return None, None
    return _render_footer_field(name, data, False, False, False)


_PANEL_DEFAULT_FIELDS = ("model", "reasoning", "tools", "context", "elapsed")


def _build_panel_header(
    fields: list[str] | None,
    data: dict,
    *,
    reasoning_count: int,
    tool_count: int,
    panel_elapsed_ms: int | float,
    show_context: bool,
    context_mode: str,
) -> tuple[str, str]:
    """组装统一面板 header 文本，返回 (en, zh)。无效/缺数据字段自动隐藏."""
    if not fields:
        fields = list(_PANEL_DEFAULT_FIELDS)
    en_parts: list[str] = []
    zh_parts: list[str] = []
    for name in fields:
        en, zh = _render_panel_field(
            name,
            data,
            reasoning_count=reasoning_count,
            tool_count=tool_count,
            panel_elapsed_ms=panel_elapsed_ms,
            show_context=show_context,
            context_mode=context_mode,
        )
        if en:
            en_parts.append(en)
            zh_parts.append(zh or en)
    # 面板出现的前提是有推理/工具内容，默认字段下 header 必非空；
    # 自定义组合全部隐藏时回落 🍟 占位，避免空标题栏
    return " · ".join(en_parts) or "🍟", " · ".join(zh_parts) or "🍟"


def _build_footer_elements(
    footer_data: dict | None,
    is_error: bool = False,
    is_aborted: bool = False,
    fields: list[list[str]] | None = None,
    show_label: bool = False,
    text_size: str = "notation",
) -> list[dict]:
    if fields is None:
        fields = [["status", "elapsed", "speed", "cache", "context", "model"]]

    data = footer_data or {}
    en_lines: list[str] = []
    zh_lines: list[str] = []
    for row in fields:
        en_parts: list[str] = []
        zh_parts: list[str] = []
        for field in row:
            en, zh = _render_footer_field(field, data, is_error, is_aborted, show_label)
            if en:
                en_parts.append(en)
                if zh:
                    zh_parts.append(zh)
        if en_parts:
            en_lines.append(" · ".join(en_parts))
            zh_lines.append(" · ".join(zh_parts))

    if not en_lines:
        return []

    en_content = "\n".join(en_lines)
    zh_content = "\n".join(zh_lines)
    if is_error:
        en_content = f"<font color='red'>{en_content}</font>"
        zh_content = f"<font color='red'>{zh_content}</font>"

    return [
        {
            "tag": "markdown",
            "content": en_content,
            "i18n_content": _i18n(en_content, zh_content),
            "text_size": text_size,
        },
    ]


def _render_footer_field(
    name: str,
    data: dict,
    is_error: bool,
    is_aborted: bool,
    show_label: bool,
) -> tuple[str | None, str | None]:
    if name == "status":
        if is_error:
            return _T["status_error"]
        if is_aborted:
            return _T["status_stopped"]
        return _T["status_completed"]

    if name == "elapsed":
        duration = data.get("duration", 0)
        if isinstance(duration, (int, float)) and duration > 0:
            val = _format_elapsed(duration * 1000)
            if show_label:
                return _T["elapsed"][0].format(val), _T["elapsed"][1].format(val)
            return val, val
        return None, None

    if name == "model":
        v = data.get("model") or None
        if v:
            v = _display_model(v)
        return v, v

    if name == "tokens":
        input_t = data.get("input_tokens", 0) or 0
        output_t = data.get("output_tokens", 0) or 0
        if input_t or output_t:
            v = f"↑ {_compact(input_t)} ↓ {_compact(output_t)}"
            return v, v
        return None, None

    if name == "speed":
        tps = data.get("tokens_per_sec", 0) or 0
        if tps > 0:
            v = f"{tps:.0f} tok/s"
            return v, v
        return None, None

    if name == "cache":
        # 口径同 Hermes（cli_status_bar_mixin）：hit = cache_read / prompt_tokens，
        # prompt（input_tokens）已含缓存部分；零读取时隐藏（无数据 ≠ 0%）。
        cache_read = data.get("cache_read_tokens", 0) or 0
        input_t = data.get("input_tokens", 0) or 0
        if cache_read > 0 and input_t > 0:
            pct = min(round(cache_read / input_t * 100), 100)
            return _T["cache_hit"][0].format(pct), _T["cache_hit"][1].format(pct)
        return None, None

    if name == "context":
        used = data.get("context_used", 0) or 0
        max_c = data.get("context_max", 0) or 0
        if max_c:
            pct = int(used / max_c * 100)
            val = f"{_compact(used)}/{_compact(max_c)} ({pct}%)"
            if show_label:
                return _T["context"][0].format(val), _T["context"][1].format(val)
            return val, val
        return None, None

    return None, None


def _compact(n: int) -> str:
    if n >= 1_000_000:
        m = n / 1_000_000
        return f"{int(m)}M" if m >= 100 else f"{m:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _format_elapsed(ms: float) -> str:
    seconds = ms / 1000
    return f"{seconds:.1f}s" if seconds < 60 else f"{int(seconds // 60)}m {int(seconds % 60)}s"


_CONTEXT_BAR_STEPS = ("█", "▓", "▒", "░")  # 实 → 半实 → 半空 → 空，渐变阴影


def _context_progress_bar(used: int, total: int, width: int = 8) -> str:
    """生成渐变阴影进度条，格式: ███▓▒░░░（实心到空的密度渐变）"""
    if total <= 0:
        return ""
    pct = min(used / total * 100, 100)
    n = pct / 100 * width  # 浮点进度位置（0..width）
    cells: list[str] = []
    for i in range(width):
        pos = i + 0.5  # 每格中心
        if pos <= n:
            # 完全填满的格子：除了最靠近边界的一格，其余都是实心
            if n - pos >= 0.5:
                cells.append("█")
            elif n - pos >= 0.25:
                cells.append("▓")
            else:
                cells.append("▒")
        elif pos - 1 <= n:
            # 边界格：按剩余进度选密度
            frac = n - (pos - 1)  # 0..1
            if frac > 0.66:
                cells.append("▓")
            elif frac > 0.33:
                cells.append("▒")
            else:
                cells.append("░")
        else:
            cells.append("░")
    return "".join(cells)


def _context_progress_block(used: int, total: int, width: int = 10) -> str:
    """[已废弃] block 样式在桌面端/移动端显示不一致，不再使用."""
    return _context_progress_bar(used, total, width)


def _context_text(used: int, total: int) -> str:
    """生成上下文纯文本，格式: 55.6k/1.0m (5%)"""
    if total <= 0:
        return ""
    pct = min(used / total * 100, 100)
    if total >= 1_000_000:
        # total 用 m，used 根据大小用 k 或 m
        total_str = f"{total / 1_000_000:.1f}m"
        if used < 1_000_000:
            used_str = f"{used / 1_000:.1f}k"
        else:
            used_str = f"{used / 1_000_000:.1f}m"
        return f"{used_str}/{total_str} ({pct:.0f}%)"
    if total >= 1_000:
        return f"{used / 1_000:.1f}k/{total / 1_000:.1f}k ({pct:.0f}%)"
    return f"{used}/{total} ({pct:.0f}%)"


def _context_pct(used: int, total: int) -> str:
    """生成上下文使用百分比，格式: 35%"""
    if total <= 0:
        return ""
    pct = min(used / total * 100, 100)
    return f"{pct:.0f}%"


def _context_progress_with_text(used: int, total: int, width: int = 8) -> str:
    """生成文本+渐变进度条，格式: 55.6k/1.0m [███▓▒░░░] 5%"""
    if total <= 0:
        return ""
    pct = min(used / total * 100, 100)
    bar = _context_progress_bar(used, total, width)
    if total >= 1_000_000:
        total_str = f"{total / 1_000_000:.1f}m"
        if used < 1_000_000:
            used_str = f"{used / 1_000:.1f}k"
        else:
            used_str = f"{used / 1_000_000:.1f}m"
        return f"{used_str}/{total_str} [{bar}] {pct:.0f}%"
    if total >= 1_000:
        return f"{used / 1_000:.1f}k/{total / 1_000:.1f}k [{bar}] {pct:.0f}%"
    return f"{used}/{total} [{bar}] {pct:.0f}%"


def build_streaming_tool_use_pending_panel() -> dict[str, Any]:
    panel = _collapsible_panel(
        expanded=False,
        title_el={
            "tag": "plain_text",
            "content": _T["tool_pending"][0],
            "i18n_content": _t("tool_pending"),
            "text_color": "grey",
            "text_size": "notation",
        },
        elements=[],
    )
    panel["element_id"] = TOOL_PANEL_ELEMENT_ID
    return panel


def build_streaming_card_v2(
    *,
    tool_steps: list[ToolDisplayStep] | None = None,
    elapsed_ms: float = 0,
    show_tool_use: bool = True,
    show_reasoning: bool = False,
    show_streaming_element: bool = True,
    header_enabled: bool = False,
    text_size: str = "normal_v2",
    width_mode: str = "default",
    notice: str | None = None,
) -> dict[str, Any]:
    """CardKit 2.0 流式占位卡片 — 工具面板合并到底部."""
    elements: list[dict] = []

    if show_reasoning:
        elements.append(
            _build_reasoning_panel(" ", expanded=True, element_id=REASONING_ELEMENT_ID)
        )

    if show_streaming_element:
        elements.append(_streaming_element(text_size=text_size))

    # 工具面板放在底部（answer之后，loading之前）
    if show_tool_use:
        if tool_steps:
            elements.append(_build_tool_panel(tool_steps, elapsed_ms, running=True))
        else:
            elements.append(build_streaming_tool_use_pending_panel())

    if notice:
        # 通知区：流式态只显示最新一条（Hermes 原文），落卡片底部（loading 之前），
        # 不和工具输出混在一起（#43 反馈）；完成态按提示种类分区呈现。
        # 用流式元素承载（固定 element_id + 正文字号）：首段正文到达时在同一个元素上
        # 原地覆盖成正文，不在旁边新增一段（#issue 提示元素未被正文覆盖）。
        elements.append(
            build_streaming_notice_element(
                notice, element_id=_NOTICE_ELEMENT_ID, text_size=text_size
            )
        )

    elements.append(_loading_element())

    card = {
        "schema": "2.0",
        "config": {
            "width_mode": width_mode,
            "streaming_mode": True,
            "streaming_config": {
                "print_frequency_ms": {"default": 15},
                "print_step": {"default": 1},
                "print_strategy": "fast",
            },
            "locales": _LOCALES,
            "summary": {
                "content": _T["processing"][0],
                "i18n_content": _t("processing"),
            },
        },
        "body": {"elements": elements},
    }
    if header_enabled:
        card["header"] = _build_header("streaming")
    return card


def build_complete_card(
    *,
    segments: list[Segment],
    all_tool_steps: list[ToolDisplayStep],
    footer_data: dict | None = None,
    is_error: bool = False,
    is_aborted: bool = False,
    footer_fields: list[list[str]] | None = None,
    footer_show_label: bool = True,
    footer_enabled: bool = True,
    footer_text_size: str = "notation",
    panel_fields: list[str] | None = None,
    panel_expanded: bool = False,
    panel_text_size: str = "notation",
    header_enabled: bool = False,
    body_text_size: str = "normal_v2",
    show_tool_use: bool = True,
    width_mode: str = "default",
    notices: list[str] | None = None,
    progress_notices: list[str] | None = None,
) -> dict[str, Any]:
    """完成态流式卡片 — 推理+工具合并成底部统一面板，答案在上面.

    统一面板内部按 segments 的**真实发生顺序**交错渲染：
    💭 思考1 → 🔧 工具组1 → 💭 思考2 → 🔧 工具组2 …（对齐工作流时间线）。

    通知区分两个展示位（#43 反馈）：``notices``（后台回执等完成后通知）在正文
    顶部；``progress_notices``（忙碌回执 / 重定向等本轮进行中的提示）在卡片
    底部（footer 之前），不与工具输出混排。元素 id 跨区连续派生，保证唯一。
    """
    elements: list[dict] = []
    has_answer = False
    # 收集所有 reasoning rounds + tool steps，合并成一个底部统一面板
    reasoning_rounds: list[dict] = []
    tool_steps_total: list[ToolDisplayStep] = []
    tool_elapsed_ms: int | float = 0
    # 过程时间线：按 segments 到达顺序保序记录 reasoning / tool 组，
    # 终态据此交错渲染（不再「先所有思考、再所有工具」两堆堆叠）。
    timeline: list[dict] = []

    for seg in segments:
        if seg.type == SegmentType.REASONING:
            if seg.text:
                # 尾部窗口钳制（#52）：与流式 reasoning 同一预算。完成态整卡重建把
                # 全文再发一遍，28KB 级 reasoning 叠加正文会越过 ~30KB 卡片 JSON 上限；
                # 钳制后完成态与流式期观感也保持一致（同一份头部摘要+尾部结论）。
                round_entry = {
                    "text": clamp_utf8(
                        seg.text, max_bytes=_REASONING_MAX_BYTES, preserve_tail=True,
                    ),
                    "elapsed_ms": seg.elapsed_ms,
                    "text_el_id": seg.text_el_id,  # 复用流式阶段的 text element id，避免完成态 Duplicate ID
                }
                reasoning_rounds.append(round_entry)
                timeline.append({"kind": "reasoning", "round": round_entry})
        elif seg.type == SegmentType.TOOL:
            if not show_tool_use:
                continue
            start = seg.tool_offset
            end = seg.tool_end_offset if seg.tool_end_offset else len(all_tool_steps)
            steps = all_tool_steps[start:end]
            if steps:
                tool_steps_total.extend(steps)
                tool_elapsed_ms += seg.elapsed_ms or 0
                timeline.append({
                    "kind": "tools",
                    "steps": steps,
                    "elapsed_ms": seg.elapsed_ms or 0,
                })
        elif seg.type == SegmentType.ANSWER and seg.text:
            has_answer = True
            _append_content_elements(elements, seg.text, text_size=body_text_size)

    # 推理+工具合并成底部一个统一面板（在答案之后、footer 之前）
    # 短回复或无工具调用时不展示统一面板（避免冗余 header）
    _panel_duration_ms = 0
    if footer_data:
        _d = footer_data.get("duration")
        if isinstance(_d, (int, float)) and _d > 0:
            _panel_duration_ms = _d * 1000
    _min_duration_ms = 0
    try:
        from ..config import Config
        _min_duration_ms = float(Config().unified_panel_min_duration) * 1000
    except Exception:
        _min_duration_ms = 5000
    _show_unified_panel = (
        (reasoning_rounds or tool_steps_total)
        and show_tool_use
        and (tool_steps_total or _panel_duration_ms >= _min_duration_ms)
    )
    if _show_unified_panel:
        # 状态边框颜色：完成=绿色, 中断=黄色, 异常=红色
        if is_error:
            border_color = "red"
        elif is_aborted:
            border_color = "yellow"
        else:
            border_color = "green"
        # 构建统一面板内容：按 timeline 真实顺序交错（💭思考 → 🔧工具组 → 💭思考 …）
        unified_children: list[dict] = []
        _reasoning_seq = 0
        _tool_group_seq = 0
        for entry in timeline:
            if entry["kind"] == "reasoning":
                rnd = entry["round"]
                if not rnd["text"].strip():
                    continue
                # 复用流式阶段 text_el_id，避免与已完成卡片上现有元素重名（Duplicate ID）。
                # 流式阶段 text_el_id 形如 reasoning_{c}_text，非空；若为空则用带索引后缀的唯一 ID，
                # 绝不回落到固定 REASONING_TEXT_ELEMENT_ID，防止单轮 reasoning 在 merge 更新下重复。
                text_el_id = rnd.get("text_el_id") or f"reasoning_text_{_reasoning_seq}"
                _reasoning_seq += 1
                unified_children.append(_build_reasoning_panel(
                    text=rnd["text"],
                    elapsed_ms=rnd["elapsed_ms"],
                    expanded=panel_expanded,
                    text_element_id=text_el_id,
                ))
            elif entry["kind"] == "tools":
                # 每个工具组独立成一个嵌套 🔧 面板（保留 fry 现有工具面板样式），
                # 交错在思考轮次之间，而非全部堆到末尾。
                # compact=True（#53）：完成态整卡重建会把面板子元素重复计数，
                # 每步只留标题行把子元素数压回 1/3，长工具轮次不再撞 300305。
                _tool_group_seq += 1
                tool_panel = _build_tool_panel(
                    entry["steps"],
                    entry["elapsed_ms"],
                    expanded=panel_expanded,
                    element_id=f"tool_panel_{_tool_group_seq}",
                    compact=True,
                )
                unified_children.append(tool_panel)
        # header: 字段化组合（默认 🍟 model · 💭n · 🔧n · context · ⏱️ elapsed），
        # 与 footer 共用字段池，可经 display.platforms.feishu.panel_fields 自由组合
        from ..config import Config
        _panel_cfg = Config()
        # 优先用 tool_elapsed_ms，否则用 footer_data 的 duration，否则用 session 总耗时
        panel_elapsed_ms = tool_elapsed_ms
        if not panel_elapsed_ms and footer_data:
            _p_duration = footer_data.get("duration")
            if isinstance(_p_duration, (int, float)) and _p_duration > 0:
                panel_elapsed_ms = _p_duration * 1000
        header_en, header_zh = _build_panel_header(
            panel_fields,
            footer_data or {},
            reasoning_count=len(reasoning_rounds),
            tool_count=len(tool_steps_total),
            panel_elapsed_ms=panel_elapsed_ms,
            show_context=_panel_cfg.show_context,
            context_mode=_panel_cfg.context_display_mode,
        )
        unified_panel = {
            "tag": "collapsible_panel",
            # 固定 id：封卡撞 300305 时按 id 精确剥离整个面板重发（#53 修法 2）
            "element_id": UNIFIED_PANEL_ELEMENT_ID,
            "expanded": panel_expanded,
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": header_en,
                    "i18n_content": _i18n(header_en, header_zh),
                },
                "text_color": "grey", "text_size": panel_text_size,
            },
            "border": {"color": border_color, "corner_radius": "5px"},
            "elements": unified_children,
        }
        elements.append(unified_panel)

    if not has_answer:
        elements.append({"tag": "markdown", "content": _T["done"][0], "text_size": body_text_size})

    # 进行中的提示（忙碌回执 / 重定向）落底部：正文之后、footer 之前（#43 反馈）。
    # 元素 id 接在顶部提示序号之后派生，两个区不撞 id。
    elements.extend(build_notice_elements(progress_notices, offset=len(notices or [])))

    if footer_enabled:
        elements.extend(
            _build_footer_elements(
                footer_data,
                is_error,
                is_aborted,
                fields=footer_fields,
                show_label=footer_show_label,
                text_size=footer_text_size,
            )
        )

    summary_text = ""
    for seg in reversed(segments):
        if seg.type in (SegmentType.ANSWER, SegmentType.REASONING) and seg.text:
            summary_text = seg.text
            break
    summary = summary_text[:120].replace("\n", " ").replace("```", "").strip()

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "width_mode": width_mode,
            "wide_screen_mode": True,
            "update_multi": True,
            "locales": _LOCALES,
        },
    }
    if summary:
        card["config"]["summary"] = {"content": summary}
    # 通知区放在正文顶部：Hermes 原文（忙碌回执 / 重定向 / 后台回执）不另发纯文本（#43）。
    # 每条提示占一个元素（id 按序号派生），元素数计入 session.element_count。
    card["body"] = {"elements": build_notice_elements(notices) + elements}
    if header_enabled:
        header_status = "error" if is_error else "stopped" if is_aborted else "completed"
        card["header"] = _build_header(header_status)
    return card


def _format_run_time(run_time: str) -> str:
    """将 ISO 时间戳格式化为可读日期时间，失败则原样返回."""
    if not run_time:
        return ""
    try:
        dt = datetime.fromisoformat(run_time)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return run_time


def build_cron_card(
    content: str, *, task_name: str = "", run_time: str = ""
) -> dict[str, Any]:
    """Cron 推送用的极简静态卡片 — schema 2.0，可选 header + markdown 内容."""
    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "locales": _LOCALES},
        "body": {"elements": []},
    }
    header_parts = [p for p in (task_name, _format_run_time(run_time)) if p]
    if header_parts:
        card["header"] = {
            "title": {"tag": "lark_md", "content": ":Alarm: " + " · ".join(header_parts)},
            "template": "blue",
        }
    if not content.strip():
        return card
    summary = content[:120].replace("\n", " ").replace("```", "").strip()
    if summary:
        card["config"]["summary"] = {"content": summary}
    _append_content_elements(card["body"]["elements"], content)
    return card


def build_background_card(preview: str, content: str) -> dict[str, Any]:
    """Background 任务完成推送卡片 — schema 2.0，纯正文（无 header）.

    ``preview`` 仅保留参数兼容：卡片不再有标题，「后台任务」这四个字由正文自己带，
    卡片第一行就是内容本身。``config.summary`` 仍按正文首段生成，供飞书会话列表
    的摘要行显示。
    """
    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "locales": _LOCALES},
        "body": {"elements": []},
    }
    body = content if content.strip() else "(No response generated)"
    summary = body[:120].replace("\n", " ").replace("```", "").strip()
    if summary:
        card["config"]["summary"] = {"content": summary}
    _append_content_elements(card["body"]["elements"], body)
    return card


def count_elements_recursive(value: Any) -> int:
    """递归统计卡片树里的元素节点数（#56 诊断）.

    飞书 300305 校验的是全树递归节点总数，``len(body.elements)`` 只看顶层会严重
    低估——统一面板里嵌的工具组/思考面板每个都带一串子元素。含 ``tag`` 键的
    dict 记一个元素，容器内的嵌套元素全部计入。
    """
    if isinstance(value, dict):
        count = 1 if "tag" in value else 0
        for child in value.values():
            count += count_elements_recursive(child)
        return count
    if isinstance(value, list):
        return sum(count_elements_recursive(child) for child in value)
    return 0


def strip_unified_panel(card: dict[str, Any]) -> int:
    """去掉完成态卡片的统一面板（思考 + 工具过程），就地修改，返回剥离的元素数（#53）.

    封卡撞 300305 时的应急重发载荷：面板子元素在流式期已提交过一次，整卡重建
    会被服务端按「已有 + 本次」重复计数，是元素超限的最大来源。剥离后正文与
    状态优先入卡；仍超限才走 build_overflow_fallback_card 整卡降级。返回 0 表示
    卡上没有统一面板。
    """
    body = card.get("body")
    if not isinstance(body, dict):
        return 0
    elements = body.get("elements")
    if not isinstance(elements, list):
        return 0
    kept = [
        el for el in elements
        if not (isinstance(el, dict) and el.get("element_id") == UNIFIED_PANEL_ELEMENT_ID)
    ]
    removed = len(elements) - len(kept)
    if removed:
        body["elements"] = kept
    return removed


def build_overflow_fallback_card() -> dict[str, Any]:
    """封卡失败（300305 元素超限）的降级卡片：一句提示，正文由纯文本兜底投递（#22）."""
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "locales": _LOCALES},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": _T["status_error"][0],
                "i18n_content": _t("status_error"),
            },
            "template": "red",
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": _T["card_overflow_fallback"][0],
                    "i18n_content": _t("card_overflow_fallback"),
                }
            ]
        },
    }
