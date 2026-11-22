"""厂商路由 logo（PNG）→ 飞书 image_key。

header title 是纯文本贴不了图；厂商 PNG logo 渲染在统一面板展开区顶部
（column_set：logo 并排本轮路由模型名）。资产来自 @lobehub/icons-static-png
（dark/-color），随包分发（``hermes_fry_cards/assets/vendor-logos/``）；
首次渲染读取本地文件经 ``FeishuClient.upload_image_bytes`` 换取 img_key，
进程内缓存。上传失败静默降级为纯文本模型名（模型字段回落 🦐 品牌图标），
不影响出卡。
"""

from __future__ import annotations

import asyncio
import logging
from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..feishu import FeishuClient

_logger = logging.getLogger(__name__)

# 模型名子串（大小写不敏感）→ 打包内 logo 资产；先命中先得。
# 匹配维度是**模型名本身**（served_model 的模型家族词，如 deepseek-v4.1-flash →
# deepseek、hy4-flash → codebuddy），渠道前缀（cbcn/ tencent/ 等）不参与判定——
# 子串匹配天然同时覆盖「裸模型名」与「前缀/模型」两种形态，但规则表只按模型家族登记。
VENDOR_LOGO_RULES: tuple[tuple[str, str], ...] = (
    ("deepseek", "deepseek.png"),
    ("qwen", "qwen.png"),
    ("zhipu", "zhipu.png"),
    ("glm", "zhipu.png"),
    ("zcode", "zhipu.png"),
    ("chatglm", "zhipu.png"),
    ("claude", "claude.png"),
    ("anthropic", "claude.png"),
    ("gemini", "gemini.png"),
    ("kimi", "kimi.png"),
    ("moonshot", "moonshot.png"),
    ("minimax", "minimax.png"),
    ("doubao", "doubao.png"),
    ("hunyuan", "hunyuan.png"),
    ("gpt", "openai.png"),
    ("openai", "openai.png"),
    ("step", "stepfun.png"),
    # 10R fleet 实际在用的模型家族（按模型名登记，与渠道前缀无关）
    ("hy4", "codebuddy.png"),  # CodeBuddy 渠道的 hy4 家族（hy4-preview / hy4-flash）
    ("codebuddy", "codebuddy.png"),
    ("grok", "grok.png"),
    ("longcat", "longcat.png"),
    ("mimo", "xiaomimimo.png"),  # Xiaomi MiMo（lobehub 资产名 xiaomimimo）
    ("dots", "dotsstudio.png"),  # dots3-note-prev（lobehub 资产名 dotsstudio）
    ("muse", "muse.png"),  # muse-spark-*
)


def resolve_vendor_logo_asset(model: str) -> str | None:
    """模型名 → 打包内 logo 文件名；未命中返回 None（渲染端降级纯文本）."""
    lower = model.lower()
    for needle, file in VENDOR_LOGO_RULES:
        if needle in lower:
            return file
    return None


# file → img_key（None = 上传失败，进程内不再重试）
_memo: dict[str, str | None] = {}
# file → 上传中的 Future（并发去重：同文件只发起一次上传）
_inflight: dict[str, asyncio.Future[str | None]] = {}


async def resolve_vendor_logo_image_key(client: FeishuClient, model: str) -> str | None:
    """取厂商 logo 的 img_key（懒上传 + 进程内缓存；失败返回 None）."""
    file = resolve_vendor_logo_asset(model)
    if not file:
        return None
    if file in _memo:
        return _memo[file]
    if file in _inflight:
        return await _inflight[file]

    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str | None] = loop.create_future()
    _inflight[file] = fut
    try:
        data = resources.files("hermes_fry_cards").joinpath("assets/vendor-logos", file).read_bytes()
        key = await client.upload_image_bytes(data)
    except Exception:
        _logger.debug("vendor logo upload failed for %s", file, exc_info=True)
        key = None
    _memo[file] = key
    _inflight.pop(file, None)
    fut.set_result(key)
    return key
