"""Markdown 文本处理 — 标题降级、表格降级、图片 key 剥离、长文本分块与内容预算."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .i18n import _content_t

_logger = logging.getLogger("hermes_fry_cards")

_MAX_CARD_TABLES = 5
_MAX_CHUNK_CHARS = 2400
# 单个 answer 元素的 UTF-8 字节预算（≈6000 汉字）。飞书卡片 JSON ~30KB 上限，
# 元素级估算（2400 字符/chunk）对单段超长文本保护不足，字节级钳制兜底。
_ANSWER_MAX_BYTES = 18000

__all__ = [
    "_ANSWER_MAX_BYTES",
    "_downgrade_tables",
    "_find_tables_outside_code_blocks",
    "_split_long_text",
    "_strip_invalid_image_keys",
    "clamp_utf8",
    "optimize_markdown_style",
]


# ==========================================
# Markdown 块扫描器 — fence 感知的表格定位（取代纯正则方案）
# ==========================================


@dataclass
class _MarkdownBlock:
    kind: str  # "fence" | "table" | "plain"
    text: str
    start: int
    end: int


def _fence_opening(line: str) -> tuple[str, int] | None:
    stripped = line.lstrip(" \t")
    if not stripped.startswith(("`", "~")):
        return None
    char = stripped[0]
    count = 1
    while count < len(stripped) and stripped[count] == char:
        count += 1
    if count >= 3:
        return char, count
    return None


def _is_fence_closing(line: str, marker_char: str, marker_size: int) -> bool:
    stripped = line.lstrip(" \t").rstrip("\r\n")
    if not stripped.startswith(marker_char * marker_size):
        return False
    return not stripped[marker_size:].strip(marker_char).strip()


def _parse_markdown_row(row: str) -> list[str] | None:
    """解析表格行为单元格列表；无 `|` 分隔返回 None。跳过 inline code 与转义中的 `|`."""
    stripped = row.strip()
    if not stripped:
        return None
    cells: list[str] = []
    current: list[str] = []
    delimiter_count = 0
    inline_code_size = 0
    index = 0
    while index < len(stripped):
        char = stripped[index]
        if char == "\\" and index + 1 < len(stripped):
            current.append(char)
            current.append(stripped[index + 1])
            index += 2
            continue
        if char == "`":
            run_end = index + 1
            while run_end < len(stripped) and stripped[run_end] == "`":
                run_end += 1
            run_size = run_end - index
            current.append(stripped[index:run_end])
            if inline_code_size == 0:
                inline_code_size = run_size
            elif inline_code_size == run_size:
                inline_code_size = 0
            index = run_end
            continue
        if char == "|" and inline_code_size == 0:
            cells.append("".join(current).strip())
            current = []
            delimiter_count += 1
            index += 1
            continue
        current.append(char)
        index += 1
    cells.append("".join(current).strip())
    if delimiter_count == 0:
        return None
    if stripped.startswith("|"):
        cells = cells[1:]
    if stripped.endswith("|") and cells:
        cells = cells[:-1]
    return cells or None


def _parse_table_separator(row: str) -> list[str] | None:
    stripped = row.strip()
    if not stripped:
        return None
    if not re.match(r"^\|?[\s\-|:]+\|?$", stripped):
        return None
    cells = _parse_markdown_row(stripped)
    if not cells:
        return None
    for cell in cells:
        clean = cell.replace("-", "").replace(":", "").strip()
        if clean:
            return None
    return cells


def _scan_markdown_blocks(text: str) -> list[_MarkdownBlock]:
    """把文本切成 fence / table / plain 三类块，表格识别要求表头与分隔行列数一致."""
    if not text:
        return [_MarkdownBlock(kind="plain", text="", start=0, end=0)]

    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)

    blocks: list[_MarkdownBlock] = []
    paragraph: list[str] = []
    paragraph_start = 0

    def _flush_paragraph(end: int) -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(_MarkdownBlock(
                kind="plain", text="".join(paragraph), start=paragraph_start, end=end,
            ))
            paragraph = []

    index = 0
    while index < len(lines):
        line = lines[index]
        opening = _fence_opening(line)
        if opening is not None:
            _flush_paragraph(offsets[index])
            fence_start = offsets[index]
            fence_lines = [line]
            marker_char, marker_size = opening
            index += 1
            while index < len(lines):
                candidate = lines[index]
                fence_lines.append(candidate)
                index += 1
                if _is_fence_closing(candidate, marker_char, marker_size):
                    break
            fence_text = "".join(fence_lines)
            blocks.append(_MarkdownBlock(
                kind="fence", text=fence_text,
                start=fence_start, end=fence_start + len(fence_text),
            ))
            continue

        if index + 1 < len(lines):
            headers = _parse_markdown_row(line.rstrip("\r\n"))
            separator = _parse_table_separator(lines[index + 1].rstrip("\r\n"))
            if headers is not None and separator is not None and len(headers) == len(separator):
                _flush_paragraph(offsets[index])
                table_start = offsets[index]
                table_lines = [line, lines[index + 1]]
                index += 2
                while index < len(lines):
                    candidate = lines[index].rstrip("\r\n")
                    if not candidate:
                        break
                    row_cells = _parse_markdown_row(candidate)
                    if row_cells is None or len(row_cells) != len(headers):
                        break
                    table_lines.append(lines[index])
                    index += 1
                table_text = "".join(table_lines)
                blocks.append(_MarkdownBlock(
                    kind="table", text=table_text,
                    start=table_start, end=table_start + len(table_text),
                ))
                continue

        paragraph.append(line)
        index += 1
    _flush_paragraph(len(text))
    return blocks


def _find_tables_outside_code_blocks(text: str) -> list[tuple[int, int, str]]:
    """查找代码块外的 markdown 表格，返回 [(start, end, raw), ...]."""
    return [
        (block.start, block.end, block.text)
        for block in _scan_markdown_blocks(text)
        if block.kind == "table"
    ]


# ==========================================
# 表格无损压缩 — 超限表格转「Table N · Row M」字段列表
# ==========================================


def _compact_table(table_text: str, table_number: int) -> str:
    """单个表格 → 字段列表（内容完整保留，飞书不再渲染为表格元素）."""
    lines = table_text.splitlines()
    if len(lines) < 2:
        return table_text
    header = _parse_markdown_row(lines[0])
    if not header:
        return table_text
    compact_lines: list[str] = []
    row_count = 1
    for row in lines[2:]:
        if not row.strip():
            continue
        cells = _parse_markdown_row(row)
        if not cells:
            continue
        compact_lines.append(f"**Table {table_number} · Row {row_count}**")
        for i, cell in enumerate(cells):
            col_name = header[i] if i < len(header) else f"Column {i + 1}"
            compact_lines.append(f"- {col_name}: {cell.strip()}")
        compact_lines.append("")
        row_count += 1
    return "\n".join(compact_lines) + "\n"


def _transform_table_overflow(text: str, max_tables: int) -> str:
    """超过 max_tables 的表格压缩为字段列表，前面的表格原样保留."""
    blocks = _scan_markdown_blocks(text)
    table_seen = 0
    notice_added = False
    parts: list[str] = []
    for block in blocks:
        if block.kind == "table":
            table_seen += 1
            if table_seen > max_tables:
                if not notice_added:
                    parts.append(f"\n> {_content_t('table_overflow_notice')}\n\n")
                    notice_added = True
                parts.append(_compact_table(block.text, table_seen))
                continue
        parts.append(block.text)
    return "".join(parts)


def _downgrade_tables(text: str, limit: int = _MAX_CARD_TABLES) -> str:
    """超限表格无损压缩为字段列表（取代旧代码块包装 — 内容保留且可读性更好）."""
    if "|" not in text:
        return text
    try:
        return _transform_table_overflow(text, limit)
    except Exception:
        _logger.debug("table compaction failed, fallback to code-block wrap", exc_info=True)
    # 回退：包装为代码块（内容可见但飞书不渲染为表格元素）
    matches = _find_tables_outside_code_blocks(text)
    if len(matches) <= limit:
        return text
    result = text
    for start, end, raw in reversed(matches[limit:]):
        replacement = f"```\n{raw}\n```"
        result = result[:start] + replacement + result[end:]
    return result


# ==========================================
# 内容字节预算 — 防 ~30KB 卡片 JSON 溢出
# ==========================================


def clamp_utf8(text: str, max_bytes: int = _ANSWER_MAX_BYTES, preserve_tail: bool = True) -> str:
    """按 UTF-8 字节钳制长文.

    preserve_tail=True 用于完成态封卡（首 60% + 尾 40% 双保，末尾结论不丢）；
    preserve_tail=False 用于流式进行中（渐进截断）.
    """
    if len(text.encode("utf-8")) <= max_bytes:
        return text

    if not preserve_tail:
        suffix = f"\n\n{_content_t('clamp_truncated')}"
        budget = max_bytes - len(suffix.encode("utf-8"))
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(text[:mid].encode("utf-8")) <= budget:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + suffix

    omission_tag = f"\n\n{_content_t('clamp_middle_omitted')}\n\n"
    tag_bytes = len(omission_tag.encode("utf-8"))
    net_budget = max_bytes - tag_bytes
    if net_budget <= 0:
        return text[:100] + omission_tag

    head_budget = int(net_budget * 0.60)
    tail_budget = net_budget - head_budget

    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(text[:mid].encode("utf-8")) <= head_budget:
            lo = mid
        else:
            hi = mid - 1
    head_text = text[:lo]

    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(text[-mid:].encode("utf-8")) <= tail_budget:
            lo = mid
        else:
            hi = mid - 1
    tail_text = text[-lo:] if lo > 0 else ""

    return head_text + omission_tag + tail_text


def _strip_invalid_image_keys(text: str) -> str:
    """移除非 img_ 前缀的图片引用."""
    if "![" not in text:
        return text

    def _replace(m: re.Match) -> str:
        return m.group(0) if m.group(2).startswith("img_") else ""

    return re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", _replace, text)


def optimize_markdown_style(text: str) -> str:
    """优化流式 Markdown 以适配飞书 CardKit 渲染.

    1. 提取代码块用占位符保护
    2. 标题降级: H1 -> H4, H2-H6 -> H5
    3. 还原代码块
    4. 压缩多余空行
    5. 剥离无效图片 key（非 img_xxx 格式）
    """
    try:
        # 1. 提取代码块
        mark = "___CB_"
        code_blocks: list[str] = []

        def _extract(m: re.Match) -> str:
            prefix = m.group(1) or ""
            block = m.group(0)[len(prefix) :]
            idx = len(code_blocks)
            code_blocks.append(block)
            return f"{prefix}{mark}{idx}___"

        r = re.sub(r"(^|\n)(`{3,})([^\n]*)\n[\s\S]*?\n\2(?=\n|$)", _extract, text)

        # 2. 标题降级（仅当存在 H1-H3 时）
        if re.search(r"^#{1,3} ", text, re.MULTILINE):
            r = re.sub(r"^#{2,6} (.+)$", r"##### \1", r, flags=re.MULTILINE)
            r = re.sub(r"^# (.+)$", r"#### \1", r, flags=re.MULTILINE)

        # 3. 还原代码块
        for i, block in enumerate(code_blocks):
            r = r.replace(f"{mark}{i}___", block)

        # 4. 压缩多余空行
        r = re.sub(r"\n{3,}", "\n\n", r)

        # 5. 剥离无效图片 key
        r = _strip_invalid_image_keys(r)

        return r
    except Exception:
        _logger.debug("optimize_markdown_style failed", exc_info=True)
        return text


def _split_long_text(text: str, limit: int = _MAX_CHUNK_CHARS) -> list[str]:
    """将超长文本按段落/换行拆分为多个不超过 limit 字符的块."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks
