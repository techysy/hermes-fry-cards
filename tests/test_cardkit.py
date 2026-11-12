"""cardkit 包测试 — markdown 优化、表格处理、卡片构建."""

from __future__ import annotations

import pytest

from hermes_fry_cards.cardkit.builder import (
    REASONING_ELEMENT_ID,
    REASONING_TEXT_ELEMENT_ID,
    TOOL_PANEL_ELEMENT_ID,
    _build_footer_elements,
    _build_header,
    _build_reasoning_panel,
    _build_tool_panel,
    _compact,
    _escape_md,
    _format_elapsed,
    _longest_backtick_run,
    build_complete_card,
    build_streaming_card_v2,
    strip_unified_panel,
)
from hermes_fry_cards.cardkit.markdown import (
    _downgrade_tables,
    _find_tables_outside_code_blocks,
    _split_long_text,
    _strip_invalid_image_keys,
    optimize_markdown_style,
    parse_table_block,
    split_markdown_tables,
)
from hermes_fry_cards.streaming.segments import Segment

# --- Markdown 优化 ---


class TestOptimizeMarkdownStyle:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("# Title", "#### Title"),
            ("## Sub", "##### Sub"),
            ("### Deep", "##### Deep"),
        ],
    )
    def test_headings_are_downgraded(self, source: str, expected: str) -> None:
        assert expected in optimize_markdown_style(source)

    def test_h4_h5_h6_unchanged(self) -> None:
        text = "#### H4\n##### H5\n###### H6"
        result = optimize_markdown_style(text)
        assert "#### H4" in result
        assert "##### H5" in result

    def test_heading_in_code_block_preserved(self) -> None:
        text = "```\n# Should not change\n```"
        assert "# Should not change" in optimize_markdown_style(text)

    def test_blank_line_compression(self) -> None:
        result = optimize_markdown_style("a\n\n\n\n\nb")
        assert "\n\n\n" not in result

    def test_invalid_image_key_removed(self) -> None:
        text = "![alt](not_img_key)"
        assert "not_img_key" not in optimize_markdown_style(text)

    def test_valid_img_key_preserved(self) -> None:
        text = "![alt](img_v3_abc123)"
        assert "img_v3_abc123" in optimize_markdown_style(text)

    def test_no_headings_unchanged(self) -> None:
        text = "plain text\nanother line"
        assert optimize_markdown_style(text) == text

    def test_mixed_headings_and_code(self) -> None:
        text = "# Title\n```\n# Code heading\n```\n## Sub"
        result = optimize_markdown_style(text)
        assert "#### Title" in result
        assert "# Code heading" in result


class TestStripInvalidImageKeys:
    def test_no_images_unchanged(self) -> None:
        assert _strip_invalid_image_keys("no images") == "no images"

    def test_img_prefix_kept(self) -> None:
        assert "img_v3_test" in _strip_invalid_image_keys("![a](img_v3_test)")

    def test_non_img_removed(self) -> None:
        assert "http://example.com/img.png" not in _strip_invalid_image_keys("![a](http://example.com/img.png)")


# --- 表格处理 ---


class TestFindTablesOutsideCodeBlocks:
    def test_no_tables(self) -> None:
        assert _find_tables_outside_code_blocks("no tables here") == []

    def test_single_table(self) -> None:
        text = "| A | B |\n|---|---|\n| 1 | 2 |"
        results = _find_tables_outside_code_blocks(text)
        assert len(results) == 1

    def test_table_inside_code_block_ignored(self) -> None:
        text = "```\n| A | B |\n|---|---|\n| 1 | 2 |\n```"
        assert _find_tables_outside_code_blocks(text) == []

    def test_mixed(self) -> None:
        table = "| A | B |\n|---|---|\n| 1 | 2 |"
        text = f"{table}\n\n```\n{table}\n```"
        results = _find_tables_outside_code_blocks(text)
        assert len(results) == 1


class TestDowngradeTables:
    def test_within_limit_unchanged(self) -> None:
        table = "| A | B |\n|---|---|\n| 1 | 2 |"
        text = f"{table}\n\n{table}\n\n{table}"
        assert _downgrade_tables(text) == text

    def test_over_limit_compacted_to_field_list(self) -> None:
        table = "| A | B |\n|---|---|\n| 1 | 2 |"
        text = "\n\n".join([table] * 6)
        result = _downgrade_tables(text)
        # 前 5 张表原样保留
        assert result.count("| A | B |") == 5
        # 第 6 张表无损压缩为字段列表（内容保留，不再渲染为表格）
        assert "Table 6 · Row 1" in result
        assert "- A: 1" in result
        assert "- B: 2" in result

    def test_compaction_preserves_all_cell_content(self) -> None:
        header = "| 名称 | 数值 |"
        row = "| 关键指标 | 42 |"
        table = f"{header}\n|---|---|\n{row}"
        text = "\n\n".join([table] * 7)
        result = _downgrade_tables(text)
        assert "关键指标" in result
        assert "42" in result
        # 压缩产物中不再含 markdown 表格管道行
        assert "|---|" not in result.split("Table 7")[1]


# --- 原生 table 元素（#18） ---


class TestParseTableBlock:
    def test_parses_header_and_rows(self) -> None:
        parsed = parse_table_block("| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |")
        assert parsed
        header, rows = parsed
        assert [h.strip() for h in header] == ["A", "B"]
        assert rows == [["1", "2"], ["3", "4"]]

    def test_preserves_inline_markup(self) -> None:
        parsed = parse_table_block("| A |\n|---|\n| **bold** `code` |")
        assert parsed
        assert parsed[1][0][0].strip() == "**bold** `code`"

    def test_rejects_non_table(self) -> None:
        assert parse_table_block("just text") is None


class TestSplitMarkdownTables:
    def test_table_and_text_ordered(self) -> None:
        parts = split_markdown_tables("intro\n\n| A |\n|---|\n| 1 |\n\noutro")
        kinds = [kind for kind, _ in parts]
        assert kinds == ["md", "table", "md"]
        assert parts[0][1].strip() == "intro"
        assert parts[2][1].strip() == "outro"

    def test_table_inside_code_block_stays_md(self) -> None:
        parts = split_markdown_tables("```\n| A |\n|---|\n| 1 |\n```")
        assert [kind for kind, _ in parts] == ["md"]

    def test_adjacent_md_blocks_merged(self) -> None:
        parts = split_markdown_tables("a\n\nb")
        assert parts == [("md", "a\n\nb")]


class TestNativeTableCards:
    def test_complete_card_answer_table_becomes_native(self) -> None:
        seg = _seg("answer", "| A | B |\n|---|---|\n| x**粗** | y |")
        card = build_complete_card(segments=[seg], all_tool_steps=[])
        tables = [el for el in card["body"]["elements"] if el.get("tag") == "table"]
        assert len(tables) == 1
        table = tables[0]
        assert [col["display_name"] for col in table["columns"]] == ["A", "B"]
        assert all(col["width"] == "auto" for col in table["columns"])
        assert table["row_height"] == "auto"
        assert table["rows"] == [{"c0": "x**粗**", "c1": "y"}]

    def test_complete_card_text_around_table_preserved(self) -> None:
        seg = _seg("answer", "前文\n\n| A |\n|---|\n| 1 |\n\n后文")
        card = build_complete_card(segments=[seg], all_tool_steps=[])
        texts = [
            el["content"] for el in card["body"]["elements"]
            if el.get("tag") == "markdown"
        ]
        assert any("前文" in t for t in texts)
        assert any("后文" in t for t in texts)

    def test_table_element_pads_short_rows(self) -> None:
        from hermes_fry_cards.cardkit.builder import _build_table_element

        element = _build_table_element(["A", "B", "C"], [["1", "2"]])
        assert element["rows"] == [{"c0": "1", "c1": "2", "c2": ""}]

    def test_overflow_tables_still_downgraded(self) -> None:
        table = "| A | B |\n|---|---|\n| 1 | 2 |"
        seg = _seg("answer", "\n\n".join([table] * 6))
        card = build_complete_card(segments=[seg], all_tool_steps=[])
        tables = [el for el in card["body"]["elements"] if el.get("tag") == "table"]
        assert len(tables) == 5
        texts = [
            el["content"] for el in card["body"]["elements"]
            if el.get("tag") == "markdown"
        ]
        assert any("Table 6 · Row 1" in t for t in texts)

    def test_background_card_table_becomes_native(self) -> None:
        from hermes_fry_cards.cardkit.builder import build_background_card

        card = build_background_card("p", "| A | B |\n|---|---|\n| 1 | 2 |")
        tables = [el for el in card["body"]["elements"] if el.get("tag") == "table"]
        assert len(tables) == 1
        assert tables[0]["rows"] == [{"c0": "1", "c1": "2"}]

    def test_background_card_has_no_header(self) -> None:
        """后台回执卡不带标题：正文第一行就是内容本身（preview 只留给列表摘要）."""
        from hermes_fry_cards.cardkit.builder import build_background_card

        card = build_background_card("ls -la", "✅ 后台任务已完成 — `ls -la` (3s)")

        assert "header" not in card
        assert card["body"]["elements"][0]["content"] == "✅ 后台任务已完成 — `ls -la` (3s)"
        assert card["config"]["summary"]["content"] == "✅ 后台任务已完成 — `ls -la` (3s)"
        assert "Background" not in str(card["config"]["summary"])


# --- 文本拆分 ---


class TestSplitLongText:
    def test_short_text_not_split(self) -> None:
        assert _split_long_text("short") == ["short"]

    def test_long_text_split_at_paragraph(self) -> None:
        chunk = "x" * 1200
        text = f"{chunk}\n\n{chunk}\n\n{chunk}"
        parts = _split_long_text(text, limit=2000)
        assert len(parts) > 1

    def test_no_paragraph_break_falls_back_to_newline(self) -> None:
        lines = ["word " * 100 for _ in range(30)]
        text = "\n".join(lines)
        parts = _split_long_text(text, limit=500)
        assert len(parts) > 1

    def test_exact_limit_not_split(self) -> None:
        text = "a" * 2400
        assert len(_split_long_text(text)) == 1


# --- 工具面板 ---

_STEP_RUNNING = {
    "name": "read",
    "title": "Read",
    "status": "running",
    "detail": "",
    "output": "",
    "error": "",
    "icon": "icon",
    "elapsed_ms": 0,
    "result_block": None,
    "error_block": None,
}
_STEP_SUCCESS = {**_STEP_RUNNING, "status": "success", "output": "ok", "elapsed_ms": 100}


class TestBuildToolPanel:
    def test_empty_steps(self) -> None:
        panel = _build_tool_panel([])
        assert panel["element_id"] == TOOL_PANEL_ELEMENT_ID
        assert "Tool use" in panel["header"]["title"]["content"]
        assert "工具执行" in panel["header"]["title"]["i18n_content"]["zh_cn"]

    def test_tool_running_vs_completed_title(self) -> None:
        running_panel = _build_tool_panel([_STEP_RUNNING], running=True)
        assert "Tool running" in running_panel["header"]["title"]["content"]
        assert "工具执行中" in running_panel["header"]["title"]["i18n_content"]["zh_cn"]

        completed_panel = _build_tool_panel([_STEP_SUCCESS], running=False)
        assert "Tool use" in completed_panel["header"]["title"]["content"]
        assert "工具执行" in completed_panel["header"]["title"]["i18n_content"]["zh_cn"]

    def test_with_steps(self) -> None:
        panel = _build_tool_panel([_STEP_SUCCESS], elapsed_ms=500)
        assert panel["element_id"] == TOOL_PANEL_ELEMENT_ID

    def test_with_elapsed(self) -> None:
        panel = _build_tool_panel([_STEP_RUNNING], elapsed_ms=3000)
        title = panel["header"]["title"]["content"]
        assert "3.0s" in title


class TestToolPanelStepCap:
    """工具面板只渲染最近 N 步（#46）：长任务卡片体积必须有上界."""

    @staticmethod
    def _steps(count: int) -> list[dict]:
        return [
            {**_STEP_SUCCESS, "name": f"tool_{idx}", "title": f"Tool {idx}", "detail": "", "output": ""}
            for idx in range(count)
        ]

    def test_under_cap_renders_every_step(self) -> None:
        panel = _build_tool_panel(self._steps(20))
        assert len(panel["elements"]) == 20
        assert all("另有" not in str(el.get("content", "")) for el in panel["elements"])

    def test_over_cap_keeps_last_30_with_summary(self) -> None:
        from hermes_fry_cards.cardkit.builder import MAX_TOOL_PANEL_STEPS

        panel = _build_tool_panel(self._steps(50))
        # 摘要 1 行 + 最近 30 步各 1 个标题元素（无 detail/output）
        assert len(panel["elements"]) == MAX_TOOL_PANEL_STEPS + 1
        summary = panel["elements"][0]
        assert "另有 20 步工具调用" in summary["i18n_content"]["zh_cn"]
        assert "20 earlier tool steps" in summary["content"]
        # 保留的是最近的步骤（最后一步是 Tool 49）
        assert "Tool 49" in str(panel["elements"][-1])

    def test_header_still_shows_total_steps(self) -> None:
        panel = _build_tool_panel(self._steps(50))
        # header 计总步数（"50 steps"），面板体积有界但总数不丢
        assert "50" in panel["header"]["title"]["content"]

    def test_complete_card_panel_header_honours_panel_text_size(self) -> None:
        """统一面板 header 字号走 display.panel_text_size（Studio 可设，默认 notation）."""
        seg = _seg("reasoning", "thinking text")
        card = build_complete_card(
            segments=[seg], all_tool_steps=[_STEP_SUCCESS],
            footer_data={"duration": 10.0}, panel_text_size="heading",
        )
        panel = next(
            el for el in card["body"]["elements"] if el.get("tag") == "collapsible_panel"
        )
        assert panel["header"]["text_size"] == "heading"

    def test_complete_card_panel_header_defaults_to_notation(self) -> None:
        seg = _seg("reasoning", "thinking text")
        card = build_complete_card(
            segments=[seg], all_tool_steps=[_STEP_SUCCESS], footer_data={"duration": 10.0},
        )
        panel = next(
            el for el in card["body"]["elements"] if el.get("tag") == "collapsible_panel"
        )
        assert panel["header"]["text_size"] == "notation"

    def test_estimation_matches_capped_rendering(self) -> None:
        """估算端与渲染端按同一上限取值，长任务里估算不再无界增长."""
        from hermes_fry_cards.streaming.segment_helper import estimate_tool_elements

        steps = self._steps(50)
        # 两个都超过 30 步的区间：估算收敛到同一上限值（含摘要行）
        assert estimate_tool_elements(0, 50, steps) == estimate_tool_elements(5, 50, steps)


class TestCompactToolPanel:
    """#53 封卡紧凑面板：每步只渲染标题行（工具名 · 状态），不渲染入参与输出."""

    @staticmethod
    def _steps(count: int) -> list[dict]:
        return [
            {
                **_STEP_SUCCESS,
                "name": f"tool_{idx}",
                "title": f"Tool {idx}",
                "detail": f"detail of {idx}",
                "output": f"output of {idx}",
            }
            for idx in range(count)
        ]

    def test_compact_renders_title_only(self) -> None:
        panel = _build_tool_panel(self._steps(3), compact=True)
        # 每步 1 个标题元素，detail/output 不入卡
        assert len(panel["elements"]) == 3
        for el in panel["elements"]:
            assert el["text"]["tag"] == "lark_md"
            assert "font" in el["text"]["content"]  # 状态色标在标题行内

    def test_non_compact_keeps_detail_and_output(self) -> None:
        panel = _build_tool_panel(self._steps(1), compact=False)
        # 标题 + detail（output 走 result_block，本 step 未带 → 2 个元素）
        assert len(panel["elements"]) == 2
        assert any("detail of 0" in str(el) for el in panel["elements"])
        # 紧凑模式下 detail 不出现
        compact_panel = _build_tool_panel(self._steps(1), compact=True)
        assert all("detail of 0" not in str(el) for el in compact_panel["elements"])

    def test_compact_keeps_panel_header_and_summary(self) -> None:
        from hermes_fry_cards.cardkit.builder import MAX_TOOL_PANEL_STEPS

        panel = _build_tool_panel(self._steps(50), elapsed_ms=5000, compact=True)
        assert "50" in panel["header"]["title"]["content"]
        # 摘要 1 行 + 最近 30 步各 1 个标题元素
        assert len(panel["elements"]) == MAX_TOOL_PANEL_STEPS + 1

    def test_complete_card_tool_groups_are_compact(self) -> None:
        """统一面板里的嵌套工具组按紧凑渲染，长工具轮次完成态不再撞 300305（#53）."""
        seg = _seg("tool", "", tool_offset=0, tool_end_offset=5)
        card = build_complete_card(
            segments=[seg], all_tool_steps=self._steps(5), footer_data={"duration": 10.0},
        )
        unified = next(
            el for el in card["body"]["elements"]
            if el.get("tag") == "collapsible_panel"
        )
        tool_panel = unified["elements"][0]
        # 5 步 → 5 个标题元素，无 detail/output 元素
        assert len(tool_panel["elements"]) == 5
        assert all("detail of" not in str(el) for el in tool_panel["elements"])

    def test_unified_panel_has_stable_element_id(self) -> None:
        seg = _seg("reasoning", "thinking text")
        card = build_complete_card(segments=[seg], all_tool_steps=[], footer_data={"duration": 10.0})
        unified = next(
            el for el in card["body"]["elements"] if el.get("tag") == "collapsible_panel"
        )
        assert unified["element_id"] == "unified_panel"

    def test_complete_card_reasoning_text_clamped_to_budget(self) -> None:
        """完成态统一面板的 reasoning 全文按同一预算钳制（#52）：28KB 级思考全文
        叠加正文会越过 ~30KB 卡片 JSON 上限，且与流式期钳制后的观感保持一致."""
        from hermes_fry_cards.cardkit.markdown import _REASONING_MAX_BYTES

        seg = _seg("reasoning", "思" * 20_000)  # ~60KB 全文
        card = build_complete_card(segments=[seg], all_tool_steps=[], footer_data={"duration": 10.0})
        unified = next(
            el for el in card["body"]["elements"] if el.get("tag") == "collapsible_panel"
        )
        reasoning_panel = unified["elements"][0]
        content = reasoning_panel["elements"][0]["content"]
        assert len(content.encode("utf-8")) <= _REASONING_MAX_BYTES

    def test_complete_card_short_reasoning_text_untouched(self) -> None:
        seg = _seg("reasoning", "短思考")
        card = build_complete_card(segments=[seg], all_tool_steps=[], footer_data={"duration": 10.0})
        unified = next(
            el for el in card["body"]["elements"] if el.get("tag") == "collapsible_panel"
        )
        assert "短思考" in unified["elements"][0]["elements"][0]["content"]


class TestStripUnifiedPanel:
    """#53 封卡撞 300305 时的应急剥离：正文与状态优先入卡."""

    def test_strips_only_unified_panel(self) -> None:
        seg = _seg("reasoning", "thinking text")
        card = build_complete_card(segments=[seg], all_tool_steps=[], footer_data={"duration": 10.0})
        before = len(card["body"]["elements"])
        removed = strip_unified_panel(card)
        assert removed == 1
        assert len(card["body"]["elements"]) == before - 1
        # 正文仍在
        assert any(el.get("tag") == "markdown" for el in card["body"]["elements"])

    def test_returns_zero_when_absent(self) -> None:
        card = {"body": {"elements": [{"tag": "markdown", "content": "x"}]}}
        assert strip_unified_panel(card) == 0
        assert len(card["body"]["elements"]) == 1

    def test_malformed_body_is_noop(self) -> None:
        assert strip_unified_panel({}) == 0
        assert strip_unified_panel({"body": "broken"}) == 0
        assert strip_unified_panel({"body": {"elements": "broken"}}) == 0


# --- Footer ---


class TestBuildFooterElements:
    def test_empty_data_renders_default_status(self) -> None:
        # 默认字段包含 "status"，总是会渲染
        result = _build_footer_elements({})
        assert len(result) >= 1
        assert "Completed" in result[0]["content"]

    def test_status_error(self) -> None:
        result = _build_footer_elements({}, is_error=True)
        assert "red" in result[0]["content"]

    def test_status_aborted(self) -> None:
        result = _build_footer_elements({}, is_aborted=True)
        assert "Stopped" in result[0]["content"]

    def test_elapsed_displayed(self) -> None:
        result = _build_footer_elements({"duration": 12.5}, fields=[["elapsed"]])
        assert "12.5s" in result[0]["content"]

    def test_model_displayed(self) -> None:
        result = _build_footer_elements({"model": "claude-3"}, fields=[["model"]])
        assert "claude-3" in result[0]["content"]

    def test_context_displayed(self) -> None:
        result = _build_footer_elements(
            {"context_used": 50000, "context_max": 200000},
            fields=[["context"]],
        )
        assert "50.0K" in result[0]["content"]
        assert "25%" in result[0]["content"]

    def test_tokens_displayed(self) -> None:
        result = _build_footer_elements(
            {"input_tokens": 1000, "output_tokens": 500},
            fields=[["tokens"]],
        )
        assert "↑" in result[0]["content"]
        assert "↓" in result[0]["content"]

    def test_show_label(self) -> None:
        result = _build_footer_elements(
            {"duration": 5},
            fields=[["elapsed"]],
            show_label=True,
        )
        assert "Elapsed" in result[0]["content"]

    def test_multi_row_fields(self) -> None:
        result = _build_footer_elements(
            {"duration": 5, "model": "gpt"},
            fields=[["elapsed"], ["model"]],
        )
        assert "\n" in result[0]["content"]

    def test_no_matching_fields(self) -> None:
        assert _build_footer_elements({}, fields=[["tokens"]]) == []

    def test_speed_displayed(self) -> None:
        result = _build_footer_elements({"tokens_per_sec": 207.6}, fields=[["speed"]])
        assert "208 tok/s" in result[0]["content"]

    def test_speed_hidden_without_data(self) -> None:
        assert _build_footer_elements({}, fields=[["speed"]]) == []
        assert _build_footer_elements({"tokens_per_sec": 0}, fields=[["speed"]]) == []

    def test_cache_displayed(self) -> None:
        # 命中率 = cache_read / input_tokens（prompt 已含缓存，口径同 Hermes）
        result = _build_footer_elements(
            {"cache_read_tokens": 8300, "input_tokens": 15420},
            fields=[["cache"]],
        )
        assert "Cache hit 54%" in result[0]["content"]
        assert "缓存命中 54%" in result[0]["i18n_content"]["zh_cn"]

    def test_cache_hidden_without_data(self) -> None:
        # 无缓存读取或无 prompt 分母时隐藏（零读取 ≠ 0%）
        assert _build_footer_elements({}, fields=[["cache"]]) == []
        assert _build_footer_elements({"cache_read_tokens": 100}, fields=[["cache"]]) == []
        assert _build_footer_elements({"input_tokens": 100}, fields=[["cache"]]) == []
        assert _build_footer_elements(
            {"cache_read_tokens": 0, "input_tokens": 100}, fields=[["cache"]]
        ) == []

    def test_cache_pct_capped_at_100(self) -> None:
        result = _build_footer_elements(
            {"cache_read_tokens": 2000, "input_tokens": 1000},
            fields=[["cache"]],
        )
        assert "100%" in result[0]["content"]

    def test_default_fields_include_speed_and_cache(self) -> None:
        result = _build_footer_elements(
            {
                "tokens_per_sec": 208,
                "cache_read_tokens": 8300,
                "input_tokens": 15420,
                "context_used": 50000,
                "context_max": 200000,
                "model": "deepseek-v4",
            },
        )
        content = result[0]["content"]
        for piece in ("208 tok/s", "Cache hit 54%", "50.0K", "deepseek-v4"):
            assert piece in content


# --- 推理面板 ---


class TestBuildReasoningPanel:
    def test_without_elapsed(self) -> None:
        panel = _build_reasoning_panel("thinking content")
        assert "Thought" in panel["header"]["title"]["content"]
        assert not panel["expanded"]

    def test_with_elapsed(self) -> None:
        panel = _build_reasoning_panel("thoughts", elapsed_ms=5000)
        title = panel["header"]["title"]["content"]
        assert "5.0s" in title

    def test_expanded_true(self) -> None:
        panel = _build_reasoning_panel("text", expanded=True)
        assert panel["expanded"] is True

    def test_element_id_default_none(self) -> None:
        panel = _build_reasoning_panel("text")
        assert "element_id" not in panel

    def test_inner_markdown_has_element_id(self) -> None:
        panel = _build_reasoning_panel("text")
        inner = panel["elements"][0]
        assert inner["element_id"] == REASONING_TEXT_ELEMENT_ID

    def test_title_is_plain_text_grey(self) -> None:
        panel = _build_reasoning_panel("text")
        title = panel["header"]["title"]
        assert title["tag"] == "plain_text"
        assert title["text_color"] == "grey"
        assert title["text_size"] == "notation"

    def test_empty_text_shows_thinking_title(self) -> None:
        panel = _build_reasoning_panel(" ")
        assert "Thinking" in panel["header"]["title"]["content"]

    def test_with_content_shows_thought_title(self) -> None:
        panel = _build_reasoning_panel("reasoning here")
        assert "Thought" in panel["header"]["title"]["content"]
        assert "Thinking" not in panel["header"]["title"]["content"]


# --- 数字格式化 ---


class TestCompact:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (42, "42"),
            (1_500, "1.5K"),
            (2_500_000, "2.5M"),
            (1_000, "1.0K"),
            (250_000_000, "250M"),
        ],
    )
    def test_compacts_numbers(self, value: int, expected: str) -> None:
        assert _compact(value) == expected


class TestFormatElapsed:
    @pytest.mark.parametrize(
        ("milliseconds", "expected"),
        [(3_500, "3.5s"), (125_000, "2m 5s"), (60_000, "1m 0s")],
    )
    def test_formats_elapsed_time(self, milliseconds: float, expected: str) -> None:
        assert _format_elapsed(milliseconds) == expected


# --- 工具函数 ---


class TestEscapeMd:
    def test_escapes_special_chars(self) -> None:
        result = _escape_md("a`b*c{d}e[f]g<h>i")
        assert "\\" in result

    def test_plain_text_unchanged(self) -> None:
        assert _escape_md("hello world") == "hello world"


class TestLongestBacktickRun:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("no backticks", 0), ("a `b` c", 1), ("```code```", 3)],
    )
    def test_finds_longest_run(self, text: str, expected: int) -> None:
        assert _longest_backtick_run(text) == expected


# --- 完整卡片构建 ---


class TestBuildStreamingCardV2:
    def test_structure(self) -> None:
        card = build_streaming_card_v2()
        assert card["schema"] == "2.0"
        assert card["config"]["streaming_mode"] is True
        assert card["body"]["elements"]

    def test_with_tool_steps(self) -> None:
        card = build_streaming_card_v2(tool_steps=[_STEP_RUNNING], elapsed_ms=100)
        assert any(e.get("element_id") == TOOL_PANEL_ELEMENT_ID for e in card["body"]["elements"])

    def test_no_tool_use(self) -> None:
        card = build_streaming_card_v2(show_tool_use=False)
        assert not any(e.get("element_id") == TOOL_PANEL_ELEMENT_ID for e in card["body"]["elements"])

    def test_show_reasoning_adds_panel(self) -> None:
        card = build_streaming_card_v2(show_reasoning=True)
        assert any(e.get("element_id") == REASONING_ELEMENT_ID for e in card["body"]["elements"])

    def test_show_reasoning_default_no_panel(self) -> None:
        card = build_streaming_card_v2()
        assert not any(e.get("element_id") == REASONING_ELEMENT_ID for e in card["body"]["elements"])

    def test_reasoning_before_tool_before_answer(self) -> None:
        card = build_streaming_card_v2(
            show_reasoning=True,
            tool_steps=[_STEP_RUNNING],
            elapsed_ms=100,
            show_tool_use=True,
        )
        ids = [e.get("element_id") for e in card["body"]["elements"]]
        reasoning_idx = ids.index(REASONING_ELEMENT_ID)
        tool_idx = ids.index(TOOL_PANEL_ELEMENT_ID)
        assert reasoning_idx < tool_idx

    def test_width_mode_default(self) -> None:
        card = build_streaming_card_v2()
        assert card["config"]["width_mode"] == "default"

    def test_width_mode_custom(self) -> None:
        card = build_streaming_card_v2(width_mode="compact")
        assert card["config"]["width_mode"] == "compact"

    def test_header_template_override_default(self) -> None:
        """#71: 支持 header_template 设为 default 关掉彩色标题栏."""
        card = build_streaming_card_v2(header_enabled=True, header_template="default")
        assert card["header"]["template"] == "default"


# --- 分段完成态卡片 ---


def _seg(seg_type: str, text: str = "", **kwargs: int | float) -> Segment:
    """创建测试用 Segment mock."""
    seg = Segment(seg_type, f"{seg_type}_0")
    seg.text = text
    if seg_type == "reasoning":
        seg.text_el_id = f"{seg_type}_0_text"
    seg.tool_offset = int(kwargs.get("tool_offset", 0))
    seg.tool_end_offset = int(kwargs.get("tool_end_offset", 0))
    seg.elapsed_ms = float(kwargs.get("elapsed_ms", 0.0))
    seg.start_time = float(kwargs.get("start_time", 0.0))
    seg.created = True
    seg.dirty = False
    return seg


def _unified_panel(card: dict) -> dict:
    """取完成态卡片的统一面板（顶层唯一 collapsible_panel，思考/工具面板嵌套其内）."""
    return next(
        e for e in card["body"]["elements"]
        if e.get("tag") == "collapsible_panel"
    )


class TestBuildSegmentCompleteCard:
    def test_empty_segments_and_skipped_reasoning(self) -> None:
        """空 segments 渲染 Done；空 reasoning 被跳过."""
        card = build_complete_card(segments=[], all_tool_steps=[])
        assert card["schema"] == "2.0"
        assert any("Done" in str(e) or "完成" in str(e) for e in card["body"]["elements"])

        card2 = build_complete_card(segments=[_seg("reasoning", "")], all_tool_steps=[])
        assert any("Done" in str(e) or "完成" in str(e) for e in card2["body"]["elements"])

    def test_answer_only_no_done(self) -> None:
        card = build_complete_card(
            segments=[_seg("answer", "hello world")],
            all_tool_steps=[],
        )
        elements = card["body"]["elements"]
        assert any("hello world" in str(e) for e in elements)
        assert not any("Done" in str(e) for e in elements)

    def test_reasoning_before_answer(self) -> None:
        card = build_complete_card(
            segments=[_seg("reasoning", "think"), _seg("answer", "reply")],
            all_tool_steps=[],
            footer_data={"duration": 8.0},  # 思考较久（>5s），应展示统一面板
        )
        contents = [str(e) for e in card["body"]["elements"]]
        r_idx = next(i for i, c in enumerate(contents) if "think" in c)
        a_idx = next(i for i, c in enumerate(contents) if "reply" in c)
        # 答案在上面，推理+工具统一面板在底部（二次开发特性）
        assert a_idx < r_idx

    def test_tool_segment_uses_steps_slice(self) -> None:
        steps = [_STEP_RUNNING, _STEP_SUCCESS, _STEP_RUNNING]
        card = build_complete_card(
            segments=[_seg("tool", tool_offset=1, tool_end_offset=3)],
            all_tool_steps=steps,
        )
        # 统一面板（header 带 💭）内含一个嵌套 🔧 工具面板，覆盖 steps[1:3]
        unified = _unified_panel(card)
        tool_panels = [e for e in unified["elements"] if e.get("tag") == "collapsible_panel"]
        assert len(tool_panels) == 1
        assert len(tool_panels[0].get("elements", [])) == 2  # steps[1:3]

    def test_multiple_tool_segments_merged_into_single_bottom_panel(self) -> None:
        """推理+工具合并成底部统一面板（二次开发特性）."""
        steps = [_STEP_SUCCESS, _STEP_RUNNING, _STEP_SUCCESS]
        card = build_complete_card(
            segments=[
                _seg("reasoning", "r1"),
                _seg("tool", tool_offset=0, tool_end_offset=2),  # 前两个工具
                _seg("answer", "a1"),
                _seg("tool", tool_offset=2, tool_end_offset=3),  # 最后一个工具
            ],
            all_tool_steps=steps,
            show_tool_use=True,
        )
        elements = card["body"]["elements"]
        # 统一面板是顶层 collapsible_panel（推理+工具合并于其内）
        unified = [_unified_panel(card)]
        # 恰好一个统一面板，且包含推理内容
        assert len(unified) == 1
        # 统一面板在卡片底部（answer 之后）
        contents = [str(e) for e in elements]
        a_idx = next(i for i, c in enumerate(contents) if "a1" in c)
        panel_idx = next(
            i for i, e in enumerate(elements)
            if e.get("tag") == "collapsible_panel"
        )
        assert a_idx < panel_idx

    def test_three_round_ordering(self) -> None:
        card = build_complete_card(
            segments=[
                _seg("reasoning", "r1"),
                _seg("answer", "a1"),
                _seg("tool", tool_offset=0, tool_end_offset=2),
                _seg("reasoning", "r2"),
                _seg("answer", "a2"),
            ],
            all_tool_steps=[_STEP_SUCCESS, _STEP_RUNNING],
        )
        contents = [str(e) for e in card["body"]["elements"]]
        # 答案在上面，推理+工具统一面板在底部
        a1 = next(i for i, c in enumerate(contents) if "a1" in c)
        a2 = next(i for i, c in enumerate(contents) if "a2" in c)
        # 统一面板包含 r1, r2 和工具
        unified = [i for i, c in enumerate(contents) if "Agent" in c]
        assert a1 < a2  # 两个答案都在统一面板之前
        if unified:
            assert a2 < unified[0]  # 第二个答案也在统一面板之前

    def test_workflow_interleaving_reasoning_and_tools(self) -> None:
        """统一面板内按真实发生顺序交错：💭思考 → 🔧工具组 → 💭思考 → 🔧工具组."""
        steps = [_STEP_SUCCESS, _STEP_RUNNING, _STEP_SUCCESS]
        card = build_complete_card(
            segments=[
                _seg("reasoning", "think-A", elapsed_ms=3200),
                _seg("tool", tool_offset=0, tool_end_offset=2, elapsed_ms=1000),
                _seg("answer", "answer"),
                _seg("reasoning", "think-B", elapsed_ms=5000),
                _seg("tool", tool_offset=2, tool_end_offset=3, elapsed_ms=800),
            ],
            all_tool_steps=steps,
        )
        unified = _unified_panel(card)
        kinds = [
            "reasoning" if "💭" in str(e.get("header", {})) else "tools"
            for e in unified["elements"]
            if e.get("tag") == "collapsible_panel"
        ]
        # 交错顺序：思考1 → 工具组1 → 思考2 → 工具组2（不再是「所有思考→所有工具」）
        assert kinds == ["reasoning", "tools", "reasoning", "tools"]
        # 两个思考面板文本按序
        r_texts = [
            e["elements"][0]["content"]
            for e in unified["elements"]
            if e.get("tag") == "collapsible_panel" and "💭" in str(e.get("header", {}))
        ]
        assert r_texts == ["think-A", "think-B"]
        # 工具组各自覆盖自己的步骤区间
        t_panels = [
            e for e in unified["elements"]
            if e.get("tag") == "collapsible_panel" and "🔧" in str(e.get("header", {}))
        ]
        assert len(t_panels[0]["elements"]) == 2  # steps[0:2]
        assert len(t_panels[1]["elements"]) == 1  # steps[2:3]

    def test_complete_card_header_template_override_default(self) -> None:
        """#71: 完成态卡片同样支持 header_template 设为 default."""
        card = build_complete_card(
            segments=[_seg("answer", "done")],
            all_tool_steps=[],
            header_enabled=True,
            header_template="default",
        )
        assert card["header"]["template"] == "default"

    def test_workflow_interleaving_tool_only_after_reasoning(self) -> None:
        """只有工具、无思考时，统一面板内仍含一个 🔧 面板（不回归平铺）."""
        card = build_complete_card(
            segments=[_seg("tool", tool_offset=0, tool_end_offset=1)],
            all_tool_steps=[_STEP_SUCCESS],
        )
        unified = _unified_panel(card)
        assert [e.get("tag") for e in unified["elements"]] == ["collapsible_panel"]
        assert "🔧" in str(unified["elements"][0].get("header", {}))

    def test_tool_end_offset_zero_uses_all_steps(self) -> None:
        steps = [_STEP_SUCCESS, _STEP_RUNNING]
        card = build_complete_card(
            segments=[_seg("tool", tool_offset=0, tool_end_offset=0)],
            all_tool_steps=steps,
        )
        unified = _unified_panel(card)
        tool_panel = next(e for e in unified["elements"] if e.get("tag") == "collapsible_panel")
        assert len(tool_panel["elements"]) == 2

    def test_complete_card_width_mode_default(self) -> None:
        card = build_complete_card(
            segments=[_seg("answer", "hi")],
            all_tool_steps=[],
        )
        assert card["config"]["width_mode"] == "default"

    def test_complete_card_width_mode_custom(self) -> None:
        card = build_complete_card(
            segments=[_seg("answer", "hi")],
            all_tool_steps=[],
            width_mode="fill",
        )
        assert card["config"]["width_mode"] == "fill"

    def test_tool_empty_steps_skipped(self) -> None:
        card = build_complete_card(
            segments=[_seg("tool", tool_offset=5, tool_end_offset=5)],
            all_tool_steps=[_STEP_SUCCESS],
        )
        assert not any(e.get("tag") == "collapsible_panel" for e in card["body"]["elements"])

    def test_show_tool_use_false_hides_tool_panel(self) -> None:
        """show_tool_use=False → TOOL segment rendered as nothing (无工具面板)."""
        steps = [_STEP_RUNNING, _STEP_SUCCESS]
        card = build_complete_card(
            segments=[_seg("tool", tool_offset=0, tool_end_offset=2), _seg("answer", "hello")],
            all_tool_steps=steps,
            show_tool_use=False,
        )
        # 无 collapsible_panel（工具面板）
        assert not any(e.get("tag") == "collapsible_panel" for e in card["body"]["elements"])
        # 但 answer 依然在
        assert any(e.get("tag") == "markdown" and "hello" in str(e.get("content", ""))
                   for e in card["body"]["elements"])

    def test_show_tool_use_true_default_shows_panel(self) -> None:
        """show_tool_use 默认 True → 工具面板保留（向后兼容）."""
        steps = [_STEP_RUNNING, _STEP_SUCCESS]
        card = build_complete_card(
            segments=[_seg("tool", tool_offset=0, tool_end_offset=2)],
            all_tool_steps=steps,
        )
        assert any(e.get("tag") == "collapsible_panel" for e in card["body"]["elements"])

    def test_summary_truncated_from_last_answer(self) -> None:
        card = build_complete_card(
            segments=[_seg("answer", "short"), _seg("answer", "x" * 200)],
            all_tool_steps=[],
        )
        summary = card["config"].get("summary", {}).get("content", "")
        assert len(summary) <= 120


class TestPanelHeaderFields:
    """统一面板 header 字段组合 — 与 footer 共用字段池 + 面板专属计数."""

    def _card(self, *, panel_fields=None, footer_data=None) -> dict:
        return build_complete_card(
            segments=[
                _seg("reasoning", "think"),
                _seg("tool", tool_offset=0, tool_end_offset=1),
                _seg("answer", "reply"),
            ],
            all_tool_steps=[_STEP_SUCCESS],
            footer_data=footer_data or {"duration": 8.0},
            panel_fields=panel_fields,
        )

    @staticmethod
    def _panel(card: dict) -> dict:
        # 顶层唯一的 collapsible_panel 即统一面板（工具面板是其嵌套子元素）
        return next(e for e in card["body"]["elements"] if e.get("tag") == "collapsible_panel")

    def test_default_composition(self) -> None:
        panel = self._panel(self._card(footer_data={"duration": 8.0, "model": "deepseek-v4"}))
        title = panel["header"]["title"]["content"]
        assert "🍟 deepseek-v4" in title
        assert "💭1" in title
        assert "🔧1" in title
        assert "⏱️ 8.0s" in title

    def test_custom_fields_speed_cache(self) -> None:
        panel = self._panel(self._card(
            panel_fields=["cache", "speed"],
            footer_data={
                "duration": 8.0,
                "input_tokens": 15420,
                "cache_read_tokens": 8300,
                "tokens_per_sec": 208,
            },
        ))
        title = panel["header"]["title"]["content"]
        assert "Cache hit 54%" in title
        assert "208 tok/s" in title
        assert "💭" not in title  # 未选思考计数

    def test_header_i18n_zh(self) -> None:
        panel = self._panel(self._card(
            panel_fields=["cache"],
            footer_data={"duration": 8.0, "input_tokens": 15420, "cache_read_tokens": 8300},
        ))
        assert "缓存命中 54%" in panel["header"]["title"]["i18n_content"]["zh_cn"]

    def test_unknown_fields_ignored(self) -> None:
        panel = self._panel(self._card(
            panel_fields=["model", "bogus_field"],
            footer_data={"duration": 8.0, "model": "m1"},
        ))
        assert "🍟 m1" in panel["header"]["title"]["content"]

    def test_all_hidden_falls_back_to_placeholder(self) -> None:
        panel = self._panel(self._card(panel_fields=["model"], footer_data={"duration": 8.0}))
        assert panel["header"]["title"]["content"] == "🍟"

    def test_context_hidden_when_no_data(self) -> None:
        panel = self._panel(self._card(
            panel_fields=["context"],
            footer_data={"duration": 8.0},
        ))
        assert panel["header"]["title"]["content"] == "🍟"


class TestBuildCronCard:
    def test_basic_card_structure(self) -> None:
        from hermes_fry_cards.cardkit.builder import build_cron_card

        card = build_cron_card("Hello **world**")
        assert card["schema"] == "2.0"
        assert card["body"]["elements"][0]["tag"] == "markdown"
        assert "Hello **world**" in card["body"]["elements"][0]["content"]

    def test_summary_from_content(self) -> None:
        from hermes_fry_cards.cardkit.builder import build_cron_card

        card = build_cron_card("Line 1\nLine 2\n" + "x" * 200)
        summary = card["config"]["summary"]["content"]
        assert summary.startswith("Line 1 Line 2")
        assert len(summary) <= 120

    def test_empty_content(self) -> None:
        from hermes_fry_cards.cardkit.builder import build_cron_card

        card = build_cron_card("")
        assert card["body"]["elements"] == []

    def test_table_content_preserved(self) -> None:
        from hermes_fry_cards.cardkit.builder import build_cron_card

        content = "| A | B |\n|---|---|\n| 1 | 2 |"
        card = build_cron_card(content)
        tables = [el for el in card["body"]["elements"] if el.get("tag") == "table"]
        assert len(tables) == 1
        assert [col["display_name"] for col in tables[0]["columns"]] == ["A", "B"]
        assert tables[0]["rows"] == [{"c0": "1", "c1": "2"}]

    def test_header_with_task_name(self) -> None:
        from hermes_fry_cards.cardkit.builder import build_cron_card

        card = build_cron_card("Hello", task_name="daily-digest")
        assert card["header"]["title"]["content"] == ":Alarm: daily-digest"
        assert card["header"]["title"]["tag"] == "lark_md"
        assert card["header"]["template"] == "blue"

    def test_no_header_without_task_name(self) -> None:
        from hermes_fry_cards.cardkit.builder import build_cron_card

        card = build_cron_card("Hello")
        assert "header" not in card

    def test_header_with_task_name_and_run_time(self) -> None:
        from hermes_fry_cards.cardkit.builder import build_cron_card

        card = build_cron_card(
            "Hello",
            task_name="daily-digest",
            run_time="2026-06-10T14:30:00+08:00",
        )
        assert card["header"]["title"]["content"] == ":Alarm: daily-digest · 2026-06-10 14:30"

    def test_header_with_run_time_only(self) -> None:
        from hermes_fry_cards.cardkit.builder import build_cron_card

        card = build_cron_card("Hello", run_time="2026-06-10T14:30:00+08:00")
        assert card["header"]["title"]["content"] == ":Alarm: 2026-06-10 14:30"

    def test_header_invalid_run_time_falls_back_to_raw(self) -> None:
        from hermes_fry_cards.cardkit.builder import build_cron_card

        card = build_cron_card("Hello", run_time="not-a-date")
        assert card["header"]["title"]["content"] == ":Alarm: not-a-date"

    def test_header_run_time_without_timezone(self) -> None:
        from hermes_fry_cards.cardkit.builder import build_cron_card

        card = build_cron_card("Hello", run_time="2026-06-10T14:30:00")
        assert card["header"]["title"]["content"] == ":Alarm: 2026-06-10 14:30"


# --- Header ---


class TestBuildHeader:
    @pytest.mark.parametrize(
        ("status", "template", "title"),
        [
            ("streaming", "blue", "Processing"),
            ("completed", "green", "Completed"),
            ("error", "red", "Error"),
            ("stopped", "red", "Stopped"),
            ("unknown", "green", "Completed"),
        ],
    )
    def test_status_header(self, status: str, template: str, title: str) -> None:
        header = _build_header(status)
        assert header is not None
        assert header["template"] == template
        assert title in header["title"]["content"]

    def test_title_has_i18n(self) -> None:
        header = _build_header("streaming")
        assert "i18n_content" in header["title"]
        assert "zh_cn" in header["title"]["i18n_content"]
        assert "en_us" in header["title"]["i18n_content"]

class TestStreamingCardHeader:
    def test_header_absent_by_default(self) -> None:
        card = build_streaming_card_v2()
        assert "header" not in card

    def test_header_present_when_enabled(self) -> None:
        card = build_streaming_card_v2(header_enabled=True)
        assert "header" in card
        assert card["header"]["template"] == "blue"


class TestCompleteCardHeader:
    def test_completed_has_green_header(self) -> None:
        card = build_complete_card(
            segments=[_seg("answer", "hi")],
            all_tool_steps=[],
            header_enabled=True,
        )
        assert "header" in card
        assert card["header"]["template"] == "green"

    def test_aborted_has_red_header(self) -> None:
        card = build_complete_card(
            segments=[_seg("answer", "hi")],
            all_tool_steps=[],
            is_aborted=True,
            header_enabled=True,
        )
        assert "header" in card
        assert card["header"]["template"] == "red"

    def test_error_has_red_header(self) -> None:
        card = build_complete_card(
            segments=[_seg("answer", "hi")],
            all_tool_steps=[],
            is_error=True,
            header_enabled=True,
        )
        assert "header" in card
        assert card["header"]["template"] == "red"
        assert "Error" in card["header"]["title"]["content"]

    def test_header_disabled(self) -> None:
        card = build_complete_card(
            segments=[_seg("answer", "hi")],
            all_tool_steps=[],
            header_enabled=False,
        )
        assert "header" not in card


class TestCompleteCardFooter:
    def test_footer_present_by_default(self) -> None:
        """footer_enabled 默认 True → body 末尾追加 footer 文本元素."""
        card = build_complete_card(
            segments=[_seg("answer", "hi")],
            all_tool_steps=[],
        )
        # footer 以 markdown 文本元素呈现（含状态字段），非分隔线 hr
        footer_els = [
            e for e in card["body"]["elements"]
            if e.get("tag") == "markdown" and "✅" in str(e.get("content", ""))
        ]
        assert footer_els

    def test_footer_disabled(self) -> None:
        """footer_enabled=False → 不追加 footer 文本元素."""
        card = build_complete_card(
            segments=[_seg("answer", "hi")],
            all_tool_steps=[],
            footer_enabled=False,
        )
        contents = [str(e.get("content", "")) for e in card["body"]["elements"]]
        # 仅保留 answer，无任何状态/统计 footer
        assert all("✅" not in c and "💭" not in c and "🔧" not in c for c in contents)
        assert any("hi" in c for c in contents)


class TestOverflowFallbackCard:
    """封卡失败（300305）时的降级卡（#22 请求 3）：一句提示替代「半封卡片 + 纯文本重发」."""

    def test_shape_and_i18n(self) -> None:
        from hermes_fry_cards.cardkit.builder import build_overflow_fallback_card

        card = build_overflow_fallback_card()
        assert card["schema"] == "2.0"
        assert card["header"]["template"] == "red"
        elements = card["body"]["elements"]
        assert len(elements) == 1
        content = elements[0]["content"]
        assert "plain text" in content
        assert "纯文本" in elements[0]["i18n_content"]["zh_cn"]
