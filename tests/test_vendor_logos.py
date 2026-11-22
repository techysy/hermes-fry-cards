"""厂商路由 logo（vendor_logos）测试.

覆盖：模型名 → 资产映射、打包资产存在性、model 字段 🦐 回落 / logo 纯名切换、
面板展开区顶部 logo 条（column_set）渲染与省略。
"""

from __future__ import annotations

from importlib import resources

import pytest

from hermes_fry_cards.cardkit.builder import _model_with_served, build_complete_card
from hermes_fry_cards.cardkit.vendor_logos import VENDOR_LOGO_RULES, resolve_vendor_logo_asset
from hermes_fry_cards.streaming.segments import Segment


def _seg(seg_type: str, text: str = "") -> Segment:
    seg = Segment(seg_type, f"{seg_type}_0")
    seg.text = text
    if seg_type == "reasoning":
        seg.text_el_id = f"{seg_type}_0_text"
    seg.elapsed_ms = 6000.0
    seg.created = True
    return seg


class TestResolveVendorLogoAsset:
    def test_maps_known_vendors(self) -> None:
        assert resolve_vendor_logo_asset("qwen4-max") == "qwen.png"
        assert resolve_vendor_logo_asset("MiniMax-M3.1-Flash") == "minimax.png"
        assert resolve_vendor_logo_asset("deepseek-v4.1-flash") == "deepseek.png"
        assert resolve_vendor_logo_asset("glm-5.3-flashx") == "zhipu.png"
        assert resolve_vendor_logo_asset("ZCode 熬夜套餐") == "zhipu.png"
        assert resolve_vendor_logo_asset("claude-opus-5.5") == "claude.png"
        assert resolve_vendor_logo_asset("kimi-k5") == "kimi.png"

    def test_maps_by_model_name_not_channel_prefix(self) -> None:
        """匹配维度是模型家族词，渠道前缀（tencent/ cbai/ cbcn/ 等）不参与判定."""
        assert resolve_vendor_logo_asset("hy4-preview") == "codebuddy.png"
        assert resolve_vendor_logo_asset("tencent/hy4-flash:free") == "codebuddy.png"
        assert resolve_vendor_logo_asset("cbai/hy4-preview") == "codebuddy.png"
        assert resolve_vendor_logo_asset("grok-4.7") == "grok.png"
        assert resolve_vendor_logo_asset("ocg/grok-4.7") == "grok.png"
        assert resolve_vendor_logo_asset("longcat/LongCat-2.5-Preview") == "longcat.png"
        assert resolve_vendor_logo_asset("mimo-v2.6-flash-free") == "xiaomimimo.png"
        assert resolve_vendor_logo_asset("dots3-note-prev") == "dotsstudio.png"
        assert resolve_vendor_logo_asset("oc/muse-spark-1.3-contributor-free") == "muse.png"
        assert resolve_vendor_logo_asset("oc/ling-3.0-flash-fin-free") == "ling.png"
        assert resolve_vendor_logo_asset("qwc/ling-3.1-flash") == "ling.png"
        assert resolve_vendor_logo_asset("nous/upstage/solar-mini4:free") == "upstage.png"

    def test_unknown_vendor_returns_none(self) -> None:
        assert resolve_vendor_logo_asset("space-bunny-free") is None
        assert resolve_vendor_logo_asset("fledge-alpha-free") is None
        assert resolve_vendor_logo_asset("qdc/qfmodel") is None
        assert resolve_vendor_logo_asset("") is None

    def test_bundled_assets_exist(self) -> None:
        for _, file in VENDOR_LOGO_RULES:
            data = resources.files("hermes_fry_cards").joinpath("assets/vendor-logos", file).read_bytes()
            assert data, file
            assert data[:8] == b"\x89PNG\r\n\x1a\n", file


class TestModelFieldServed:
    def test_served_model_plain_when_logo_present(self) -> None:
        data = {"model": "openclaw", "served_model": "qwen4-max", "logo_image_key": "img_v2_logo"}
        assert _model_with_served(data) == "qwen4-max"

    def test_served_model_direct_without_logo(self) -> None:
        data = {"model": "openclaw", "served_model": "qwen4-max"}
        assert _model_with_served(data) == "qwen4-max"

    def test_falls_back_to_config_model(self) -> None:
        data = {"model": "claude-x"}
        assert _model_with_served(data) == "claude-x"

    def test_empty_model_returns_none(self) -> None:
        assert _model_with_served({}) is None


class TestPanelTitleLogo:
    """命中厂商时 title 内联 PNG logo（markdown tag，原 🍟 位置）；箭头与默认图标保留."""

    @pytest.fixture()
    def _panel(self):
        card = build_complete_card(
            segments=[_seg("reasoning", "推理内容"), _seg("answer", "答案正文")],
            all_tool_steps=[],
            footer_data={
                "duration": 60.0,
                "model": "openclaw",
                "served_model": "qwen4-max",
                "logo_image_key": "img_v2_01test",
            },
        )
        panels = [el for el in card["body"]["elements"] if el.get("tag") == "collapsible_panel"]
        assert len(panels) == 1
        return panels[0]

    def test_title_is_markdown_with_inline_logo(self, _panel) -> None:
        title = _panel["header"]["title"]
        assert title["tag"] == "markdown"
        assert title["content"].startswith("![](img_v2_01test) ")
        assert "qwen4-max" in title["content"]

    def test_logo_replaces_fries_position(self, _panel) -> None:
        """logo 内联在原 🍟 的位置（行首），模型名原生直显不走截断."""
        assert "🍟" not in _panel["header"]["title"]["content"]

    def test_arrow_icon_slot_untouched(self, _panel) -> None:
        """图标槽位不占用：默认展开/收起旋转箭头保留."""
        assert "icon" not in _panel["header"]

    def test_no_strip_element(self) -> None:
        card = build_complete_card(
            segments=[_seg("reasoning", "推理内容"), _seg("answer", "答案正文")],
            all_tool_steps=[],
            footer_data={"duration": 60.0, "model": "openclaw", "served_model": "qwen4-max"},
        )
        panels = [el for el in card["body"]["elements"] if el.get("tag") == "collapsible_panel"]
        children = panels[0]["elements"]
        assert all(el["tag"] != "column_set" for el in children)

    def test_fallback_fries_without_logo_key(self) -> None:
        """未命中厂商（无 logo key）：回落原 🍟 品牌位 + 默认形态."""
        card = build_complete_card(
            segments=[_seg("reasoning", "推理内容"), _seg("answer", "答案正文")],
            all_tool_steps=[],
            footer_data={"duration": 60.0, "model": "openclaw", "served_model": "qwen4-max"},
        )
        panels = [el for el in card["body"]["elements"] if el.get("tag") == "collapsible_panel"]
        title = panels[0]["header"]["title"]
        assert title["tag"] == "plain_text"
        assert "🍟" in title["content"]
        assert "qwen4-max" in title["content"]
        assert "openclaw" not in title["content"]
