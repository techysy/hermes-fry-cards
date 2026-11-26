"""模型 logo（厂商 / 智能体兜底）测试.

覆盖：模型名 → 资产映射、打包资产存在性与**白底可见性**、未识别模型的智能体兜底、
上传缓存按内容失效、model 字段 🦐 回落 / logo 纯名切换、面板 header 图标渲染与省略。
"""

from __future__ import annotations

import struct
import zlib
from importlib import resources

import pytest

from hermes_fry_cards.cardkit.builder import _model_with_served, build_complete_card
from hermes_fry_cards.cardkit.vendor_logos import (
    AGENT_LOGO_WORDS,
    HOST_BRAND_LOGO,
    VENDOR_LOGO_RULES,
    logo_asset_bytes,
    resolve_agent_logo_asset,
    resolve_logo_asset,
    resolve_vendor_logo_asset,
)
from hermes_fry_cards.streaming.segments import Segment


def _asset_dir(name: str):
    """打包内资产目录（vendor-logos / agent-logos）."""
    return resources.files("hermes_fry_cards").joinpath("assets", name)


def logo_pixels(data: bytes) -> list[tuple[int, int, int]]:
    """logo PNG 里可见像素的 RGB 列表——只用标准库，本仓测试不许拉 Pillow.

    只支持资产实际形态（8 位调色板 + 行过滤 None/Sub/Up），换了编码方式明确报错
    而不是静默放行。alpha <= 40 视为不可见（圆角外透明像素不计入）。
    """
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "不是 PNG"
    pos, idat, ihdr, plte, trns = 8, bytearray(), b"", b"", b""
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            ihdr = body
        elif kind == b"PLTE":
            plte = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        pos += 12 + length
    width, height, depth, color = struct.unpack(">IIBB", ihdr[:10])
    assert (depth, color) == (8, 3), f"需 8 位调色板 PNG, depth={depth} color={color}"
    raw = zlib.decompress(bytes(idat))
    stride, out, at = width, [], 0
    prev = bytearray(stride)
    for _ in range(height):
        ft = raw[at]
        at += 1
        row = bytearray(raw[at:at + stride])
        at += stride
        if ft == 1:
            for x in range(1, stride):
                row[x] = (row[x] + row[x - 1]) & 0xFF
        elif ft == 2:
            for x in range(stride):
                row[x] = (row[x] + prev[x]) & 0xFF
        else:
            assert ft == 0, f"未支持的行过滤类型 {ft}"
        prev = row
        for idx in row:
            if (trns[idx] if idx < len(trns) else 255) > 40:
                out.append(tuple(plte[3 * idx:3 * idx + 3]))
    return out


def png_size(data: bytes) -> tuple[int, int]:
    """logo PNG 的 IHDR 宽高——同样只用标准库."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "不是 PNG"
    assert data[12:16] == b"IHDR", "首块不是 IHDR"
    return struct.unpack(">II", data[16:24])


def dark_ratio(data: bytes) -> float:
    """可见像素里「深色」的占比（亮度 < 200，亮度 = 0.2126R+0.7152G+0.0722B）.

    飞书面板头是浅底：整片接近白色的图标（此前 7 张取的是 lobehub dark 变体＝纯白）
    画上去等于没画，也就是用户看到的「模型那头空着」。
    """
    lums = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in logo_pixels(data)]
    return sum(1 for v in lums if v < 200) / len(lums) if lums else 0.0


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


class TestAgentFallback:
    """未识别模型 → 智能体图标兜底（本仓宿主是 Hermes Gateway）."""

    def test_agent_words_resolve(self) -> None:
        assert resolve_agent_logo_asset("combo/Hermes") == "hermes.png"
        assert resolve_agent_logo_asset("10router/openclaw") == "openclaw.png"
        assert resolve_agent_logo_asset("claude-code-runner") == "claude-code.png"
        assert resolve_agent_logo_asset("kimi-code · 10r-free") == "kimi-code.png"
        assert resolve_agent_logo_asset("codex-pool") == "codex.png"
        assert resolve_agent_logo_asset("zcode-agent") == "zcode.png"
        assert resolve_agent_logo_asset("agy-1") == "antigravity.png"
        assert resolve_agent_logo_asset("10r-free") is None

    def test_vendor_wins_over_agent(self) -> None:
        """厂商命中优先：kimi 家族仍出厂商图，不被 kimi-code 智能体词抢走."""
        assert resolve_logo_asset("kimi-k5") == ("vendor-logos", "kimi.png")
        assert resolve_logo_asset("tencent/hy4-flash:free") == ("vendor-logos", "hunyuan.png")
        assert resolve_logo_asset("claude-opus-5.5") == ("vendor-logos", "claude.png")

    def test_unknown_model_falls_back_to_host_brand(self) -> None:
        """连智能体也认不出 → 本宿主品牌图兜底，绝不空着（用户原始诉求）."""
        for model in ("qd/qfmodel", "10router/10r-free", "chat-mode-pool", "space-bunny-free"):
            assert resolve_logo_asset(model) == ("agent-logos", HOST_BRAND_LOGO), model

    def test_empty_model_has_no_logo(self) -> None:
        assert resolve_logo_asset("") is None
        assert resolve_logo_asset("   ") is None

    def test_agent_assets_bundled_and_visible(self) -> None:
        """兜底资产随包分发，且在白底面板头上看得见（此前 7 张纯白图一路绿灯的根因）."""
        for _, file in AGENT_LOGO_WORDS:
            data = resources.files("hermes_fry_cards").joinpath("assets/agent-logos", file).read_bytes()
            assert data[:8] == b"\x89PNG\r\n\x1a\n", file
            assert dark_ratio(data) > 0.5, f"{file} 深色像素不足: 白底上等于没有图标"

    def test_every_vendor_logo_visible_on_light_card(self) -> None:
        for path in sorted(_asset_dir("vendor-logos").iterdir()):
            if path.name.endswith(".png"):
                assert dark_ratio(path.read_bytes()) > 0.2, f"{path.name} 整片近白: 白底上看不见"

    def test_all_logos_are_256_square(self) -> None:
        """源图统一 256×256：卡片上图标最大渲染尺寸远小于此，640 只是上传负担.

        v0.7.5 把两目录 27 张全部从 640×640 等比重采样到 256×256（体积 −49.8%）。
        锁住尺寸口径，避免以后混进大图把上传体积悄悄涨回去。
        """
        for name in ("vendor-logos", "agent-logos"):
            for path in sorted(_asset_dir(name).iterdir()):
                if path.name.endswith(".png"):
                    assert png_size(path.read_bytes()) == (256, 256), f"{name}/{path.name} 不是 256×256"

    def test_asset_dirs_have_no_name_overlap(self) -> None:
        """两目录文件名不得重复：重复会让按文件名派生的缓存键串图."""
        vendors = {p.name for p in _asset_dir("vendor-logos").iterdir()}
        agents = {p.name for p in _asset_dir("agent-logos").iterdir()}
        assert not (vendors & agents), vendors & agents

    def test_asset_bytes_rejects_traversal(self) -> None:
        for bad in ("../../config.py", "assets/x.png", "..\\x.png", "", "  "):
            assert logo_asset_bytes("vendor-logos", bad) is None, bad
        assert logo_asset_bytes("nope", "kimi.png") is None
        assert logo_asset_bytes("agent-logos", "kimi-code.png")


class TestModelFieldServed:
    def test_served_model_official_name_when_logo_present(self) -> None:
        """logo 命中厂商：显示官方模型名（家族官方拼写 + 变体尾巴，原生体验）."""
        data = {"model": "openclaw", "served_model": "qwen4-max", "logo_image_key": "img_v2_logo"}
        assert _model_with_served(data) == "Qwen 4-max"

    def test_hy4_official_name(self) -> None:
        """混元官方口径：tencent/hy4-flash:free → Hy4 flash."""
        data = {"model": "Hermes", "served_model": "tencent/hy4-flash:free",
                "logo_image_key": "img_v2_logo"}
        assert _model_with_served(data) == "Hy4 flash"

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
        # 模型字段显示官方名（家族官方拼写 + 变体尾巴），无 🍟（logo 已承载）
        assert "Qwen 4-max" in header["title"]["content"]
        assert "🍟" not in header["title"]["content"]
        assert "openclaw" not in header["title"]["content"]

    def test_fallback_fries_without_logo_key(self) -> None:
        panel = self._panel({"duration": 60.0, "model": "openclaw", "served_model": "qwen4-max"})
        header = panel["header"]
        assert "icon" not in header
        assert "🍟" in header["title"]["content"]
        # 未命中厂商：回落代理原样名 + 🍟 品牌位（官方名仅在 logo 命中时启用）
        assert "qwen4-max" in header["title"]["content"]
        assert "openclaw" not in header["title"]["content"]


class TestLogoImageKeyCache:
    """上传缓存键含资产内容摘要：换过像素的图必须重传，否则修好的图标在
    早已发过卡的进程里永远显不出来（同姊妹仓 feige 的缓存口径）。"""

    @pytest.mark.asyncio
    async def test_uploads_agent_logo_and_caches_by_content(self, monkeypatch) -> None:
        from unittest.mock import AsyncMock

        from hermes_fry_cards.cardkit import vendor_logos

        vendor_logos._memo.clear()
        vendor_logos._inflight.clear()
        client = AsyncMock()
        client.config.app_id = "cli_test"
        client.upload_image_bytes = AsyncMock(return_value="img_v2_agent")
        key = await vendor_logos.resolve_vendor_logo_image_key(client, "10router/10r-free")
        assert key == "img_v2_agent"
        assert client.upload_image_bytes.await_count == 1
        # 缓存命中：同一份字节不再上传
        assert await vendor_logos.resolve_vendor_logo_image_key(client, "10router/10r-free") == key
        assert client.upload_image_bytes.await_count == 1
        # 内容摘要变了 → 缓存键变 → 重新上传
        real = vendor_logos.logo_asset_bytes

        def swapped(dir_name: str, file: str):
            data = real(dir_name, file)
            return data + b"\x00" if data else data

        monkeypatch.setattr(vendor_logos, "logo_asset_bytes", swapped)
        assert await vendor_logos.resolve_vendor_logo_image_key(client, "10router/10r-free") == key
        assert client.upload_image_bytes.await_count == 2
        vendor_logos._memo.clear()
        vendor_logos._inflight.clear()

    @pytest.mark.asyncio
    async def test_switch_off_yields_no_logo_anywhere(self) -> None:
        """开关语义：vendor_logo 关掉时封卡路径根本不调取图函数（厂商图与兜底图都不出）.

        判定在调用点（streaming/controller.py），这里锁住 builder 侧：footer 没有
        logo_image_key 时面板头不带 icon、模型字段回落 🍟，与「关掉」的观感一致。
        """
        card = build_complete_card(
            segments=[_seg("reasoning", "推理内容"), _seg("answer", "答案正文")],
            all_tool_steps=[],
            footer_data={"duration": 60.0, "model": "10router/10r-free"},
        )
        panels = [el for el in card["body"]["elements"] if el.get("tag") == "collapsible_panel"]
        assert "icon" not in panels[0]["header"]
        assert "🍟" in panels[0]["header"]["title"]["content"]
