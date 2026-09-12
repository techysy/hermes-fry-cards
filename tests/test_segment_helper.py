"""streaming.segment_helper 测试 — CardKit 元素容量估算与 tool 拆分点."""

from __future__ import annotations

from hermes_fry_cards.streaming.segment_helper import (
    ELEMENT_THRESHOLD,
    FOOTER_RESERVE,
    estimate_answer_elements,
    estimate_segment_elements,
    estimate_tool_elements,
    find_tool_split_offset,
    tool_segment_end,
)
from hermes_fry_cards.streaming.segments import Segment, SegmentType
from hermes_fry_cards.streaming.tooluse import ToolDisplayStep


def _step(*, detail: str = "", result: bool = False, error: bool = False) -> ToolDisplayStep:
    return {
        "name": "read",
        "title": "Read",
        "status": "running",
        "detail": detail,
        "output": "",
        "error": "",
        "icon": "tool",
        "elapsed_ms": 0,
        "result_block": {"language": "text", "content": "ok", "fenced": "ok"} if result else None,
        "error_block": {"language": "text", "content": "boom", "fenced": "boom"} if error else None,
    }


def test_estimate_segment_elements_for_basic_types() -> None:
    reasoning = Segment(SegmentType.REASONING, "reasoning")
    answer = Segment(SegmentType.ANSWER, "answer")
    tool = Segment(SegmentType.TOOL, "tool")
    steps = [_step()]

    assert estimate_segment_elements(reasoning, steps) == 4
    assert estimate_segment_elements(answer, steps) == 1
    assert estimate_segment_elements(tool, steps) == 6


def test_estimate_answer_elements_grows_with_tables_and_length() -> None:
    """answer 段估算必须随服务端膨胀源增长（表格单元格 / 长文切块）。

    回归：旧实现恒返回 1，导致 element_count 严重低估 → 撞 300305 被动拆卡。
    """
    # 空 / 纯文本：单元素
    assert estimate_answer_elements("") == 1
    assert estimate_answer_elements("   ") == 1
    assert estimate_answer_elements("hello world") == 1

    # 表格：每个数据单元格 + 表格容器都要计入（服务端展开为独立元素）
    table = "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |\n| 4 | 5 | 6 |"
    with_table = estimate_answer_elements(table)
    assert with_table > 1, "表格必须推高估算"
    assert estimate_answer_elements(table + "\n\n" + table) > with_table, "表格越多估算越大"

    # 长文：完成态按 _MAX_CHUNK_CHARS 切块，块数计入
    long_text = "x" * 3000
    assert estimate_answer_elements(long_text) >= 2, "超长文本必须按块数估算"
    assert estimate_answer_elements("x" * 6000) > estimate_answer_elements(long_text)

    # 代码块内的伪表格不算表格（服务端不展开）
    fenced = "```\n| A | B |\n|---|---|\n| 1 | 2 |\n```"
    assert estimate_answer_elements(fenced) == 1

    # segment 入口与文本估算一致
    seg = Segment(SegmentType.ANSWER, "answer")
    seg.text = table
    assert estimate_segment_elements(seg, []) == with_table


def test_estimate_tool_elements_counts_optional_detail_and_output_blocks() -> None:
    steps = [
        _step(),
        _step(detail="path"),
        _step(result=True),
        _step(error=True),
    ]

    assert estimate_tool_elements(0, len(steps), steps) == 21


def test_tool_segment_end_uses_open_segment_step_count() -> None:
    seg = Segment(SegmentType.TOOL, "tool")
    steps = [_step(), _step()]

    assert tool_segment_end(seg, steps) == 2

    seg.tool_end_offset = 1
    assert tool_segment_end(seg, steps) == 1


def test_find_tool_split_offset_keeps_largest_fitting_prefix() -> None:
    seg = Segment(SegmentType.TOOL, "tool")
    steps = [_step() for _ in range(4)]

    split_offset = find_tool_split_offset(
        base_count=ELEMENT_THRESHOLD - FOOTER_RESERVE - 7,
        seg=seg,
        all_steps=steps,
    )

    assert split_offset == 1


def test_find_tool_split_offset_returns_none_for_single_step() -> None:
    seg = Segment(SegmentType.TOOL, "tool")

    assert find_tool_split_offset(base_count=1, seg=seg, all_steps=[_step()]) is None
