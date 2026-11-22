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
        assert resolve_vendor_logo_asset("hy4-preview") == "hunyuan.png"  # hy = 腾讯混元（渠道是 CodeBuddy）
        assert resolve_vendor_logo_asset("tencent/hy4-flash:free") == "hunyuan.png"
        assert resolve_vendor_logo_asset("cbai/hy4-preview") == "hunyuan.png"
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


class TestPanelHeaderLogoIcon:
    """命中厂商：header 前缀图标槽位放家族 logo（icon_position=left，原 🍟 位置）；
    箭头不受影响（子面板各自独立）；未命中回落 🍟 品牌位。"""

    def _panel(self, footer_data):
        card = build_complete_card(
            segments=[_seg("reasoning", "推理内容"), _seg("answer", "答案正文")],
            all_tool_steps=[],
            footer_data=footer_data,
        )
        panels = [el for el in card["body"]["elements"] if el.get("tag") == "collapsible_panel"]
        assert len(panels) == 1
        return panels[0]

    def test_logo_as_left_prefix_icon(self) -> None:
        panel = self._panel({
            "duration": 60.0, "model": "openclaw", "served_model": "qwen4-max",
            "logo_image_key": "img_v2_01test",
        })
        header = panel["header"]
        assert header["icon"] == {"tag": "custom_icon", "img_key": "img_v2_01test", "size": "16px 16px"}
        # icon_position / icon_expanded_angle 是 header 层级字段（icon 的兄弟键）
        assert header["icon_position"] == "left"
        assert header["icon_expanded_angle"] == 0
        # 模型字段原生直显完整名（不走别名/⇲ 截断），无 🍟（logo 已承载）
        assert "qwen4-max" in header["title"]["content"]
        assert "🍟" not in header["title"]["content"]
        assert "openclaw" not in header["title"]["content"]

    def test_fallback_fries_without_logo_key(self) -> None:
        panel = self._panel({"duration": 60.0, "model": "openclaw", "served_model": "qwen4-max"})
        header = panel["header"]
        assert "icon" not in header
        assert "🍟" in header["title"]["content"]
        assert "qwen4-max" in header["title"]["content"]
        assert "openclaw" not in header["title"]["content"]
