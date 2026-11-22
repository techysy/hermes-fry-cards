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

    def test_unknown_vendor_returns_none(self) -> None:
        assert resolve_vendor_logo_asset("hy4-flash") is None
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


class TestRoutingLogoStrip:
    @pytest.fixture()
    def complete_panel_children(self):
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
        return panels[0]["elements"]

    def test_strip_is_first_element(self, complete_panel_children) -> None:
        strip = complete_panel_children[0]
        assert strip["tag"] == "column_set"

    def test_strip_has_logo_and_label(self, complete_panel_children) -> None:
        strip = complete_panel_children[0]
        logo_col, text_col = strip["columns"]
        assert logo_col["elements"][0]["tag"] == "img"
        assert logo_col["elements"][0]["img_key"] == "img_v2_01test"
        assert "qwen4-max" in text_col["elements"][0]["content"]

    def test_strip_omitted_without_logo_key(self) -> None:
        card = build_complete_card(
            segments=[_seg("reasoning", "推理内容"), _seg("answer", "答案正文")],
            all_tool_steps=[],
            footer_data={"duration": 60.0, "model": "openclaw", "served_model": "qwen4-max"},
        )
        panels = [el for el in card["body"]["elements"] if el.get("tag") == "collapsible_panel"]
        children = panels[0]["elements"]
        assert children[0]["tag"] != "column_set"
        # header model 段为家族 emoji + 实际模型（qwen → 🦆）
        header = panels[0]["header"]["title"]["content"]
        assert "🦆 qwen4-max" in header
        assert "openclaw" not in header


class TestModelFamilyEmoji:
    def test_maps_known_families(self) -> None:
        from hermes_fry_cards.cardkit.builder import _model_family_emoji

        assert _model_family_emoji("deepseek-v4.1-flash") == "🐳"
        assert _model_family_emoji("glm-5.3-flashx") == "🌀"
        assert _model_family_emoji("zcode-free/glm-5.3") == "🌀"
        assert _model_family_emoji("cmc/moonshotai/Kimi-K3") == "🌙"
        assert _model_family_emoji("gemini-3.8-flash") == "♊"
        assert _model_family_emoji("ocg/gpt-6-luna") == "🟢"
        assert _model_family_emoji("claude-opus-5.5") == "✳️"
        assert _model_family_emoji("MiniMax-M3.1-Flash") == "Ⓜ️"
        assert _model_family_emoji("stepp-cn/step-5-preview") == "🪜"

    def test_emoji_only_families(self) -> None:
        from hermes_fry_cards.cardkit.builder import _model_family_emoji

        assert _model_family_emoji("grok-4.7") == "🚀"
        assert _model_family_emoji("longcat-2.5-preview-free") == "🐱"
        assert _model_family_emoji("mimo-v2.6-flash-free") == "🐰"

    def test_unknown_falls_back_to_fries(self) -> None:
        from hermes_fry_cards.cardkit.builder import DEFAULT_FAMILY_EMOJI, _model_family_emoji

        assert _model_family_emoji("hy4-preview") == DEFAULT_FAMILY_EMOJI == "🍟"
        assert _model_family_emoji("") == "🍟"

    def test_case_insensitive(self) -> None:
        from hermes_fry_cards.cardkit.builder import _model_family_emoji

        assert _model_family_emoji("DeepSeek-Flash") == "🐳"
        assert _model_family_emoji("Kimi-K5") == "🌙"
