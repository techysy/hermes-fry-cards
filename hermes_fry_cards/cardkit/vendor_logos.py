"""模型 logo（厂商 PNG / 智能体兜底 PNG）→ 飞书 image_key。

header title 是纯文本贴不了图；PNG logo 渲染在统一面板展开区顶部
（column_set：logo 并排本轮路由模型名）。资产来自 @lobehub/icons-static-png
（dark/-color），随包分发（``hermes_fry_cards/assets/vendor-logos/`` 与
``assets/agent-logos/``）；首次渲染读取本地文件经 ``FeishuClient.upload_image_bytes``
换取 img_key，进程内缓存。上传失败静默降级为纯文本模型名（模型字段回落 🦐 品牌图标），
不影响出卡。

取图口径（``resolve_logo_asset``）：**厂商命中优先**，未命中回退智能体图标——
代理池/自建网关的模型名（``qd/qfmodel``、``10router/10r-free`` 一类）名字里没有
厂商家族词，此前面板头左侧一片空白；现在按模型串里的智能体词（Hermes / OpenClaw /
Claude Code …）取 ``agent-logos/``，连智能体也认不出时用本宿主品牌图兜到最后一档，
保证「认不出模型」不再等于「没有图标」。
"""

from __future__ import annotations

import asyncio
import hashlib
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
    ("hy4", "hunyuan.png"),  # hy4-preview / hy4-flash：腾讯混元 Hy 家族（渠道是 CodeBuddy，模型是混元）
    ("codebuddy", "codebuddy.png"),
    ("grok", "grok.png"),
    ("longcat", "longcat.png"),
    ("mimo", "xiaomimimo.png"),  # Xiaomi MiMo（lobehub 资产名 xiaomimimo）
    ("dots", "dotsstudio.png"),  # dots3-note-prev（lobehub 资产名 dotsstudio）
    ("muse", "muse.png"),  # muse-spark-*
    ("ling", "ling.png"),  # ling-3.0 / ling-3.1（InclusionAI Ling）
    ("upstage", "upstage.png"),  # nous/upstage/solar-mini4
)


def resolve_vendor_logo_asset(model: str) -> str | None:
    """模型名 → 打包内厂商 logo 文件名；未命中返回 None（改走智能体兜底）."""
    lower = model.lower()
    for needle, file in VENDOR_LOGO_RULES:
        if needle in lower:
            return file
    return None


# 智能体词（大小写不敏感、按子串匹配）→ ``assets/agent-logos/`` 文件名。
# 本仓宿主是 Hermes Gateway（不是某个 CLI），卡片里唯一的「执行者」身份就落在这个
# 模型串/路由名上，所以兜底判据只能取自模型名本身。词表按长度降序排列，
# 保证 ``kimi-code`` 先于 ``kimi``、``claude-code`` 先于 ``claude`` 命中。
AGENT_LOGO_WORDS: tuple[tuple[str, str], ...] = (
    ("claude-code", "claude-code.png"),
    ("antigravity", "antigravity.png"),
    ("kimi-code", "kimi-code.png"),
    ("hermesagent", "hermes.png"),
    ("hermes", "hermes.png"),
    ("openclaw", "openclaw.png"),
    ("claude", "claude-code.png"),
    ("codex", "codex.png"),
    ("zcode", "zcode.png"),
    ("kimi", "kimi-code.png"),
    ("moonshot", "kimi-code.png"),
    ("agy", "antigravity.png"),
)

# 连智能体也认不出时的最后一档：本宿主品牌图（Hermes Gateway 自己的图标）。
# 用户诉求是「无法识别模型就显示智能体的图标作为兜底」，宁可显宿主也不留空白。
HOST_BRAND_LOGO = "hermes.png"


def resolve_agent_logo_asset(model: str) -> str | None:
    """模型名里的智能体词 → ``agent-logos/`` 文件名；认不出返回 None.

    与 ``resolve_vendor_logo_asset`` 平行，供「厂商未命中」时兜底使用。
    """
    lower = (model or "").lower()
    if not lower:
        return None
    for needle, file in AGENT_LOGO_WORDS:
        if needle in lower:
            return file
    return None


def resolve_logo_asset(model: str) -> tuple[str, str] | None:
    """面板头 logo 取图：厂商命中优先，未命中回退智能体图，再回退宿主品牌图.

    返回 ``(目录, 文件名)``；``model`` 为空时返回 None（本轮没有模型信息，
    没必要硬塞一个宿主图标冒充厂商）。最后一档恒有值，故未识别模型必有图标。
    """
    if not (model or "").strip():
        return None
    vendor = resolve_vendor_logo_asset(model)
    if vendor:
        return ("vendor-logos", vendor)
    agent = resolve_agent_logo_asset(model)
    if agent:
        return ("agent-logos", agent)
    return ("agent-logos", HOST_BRAND_LOGO)


def logo_asset_bytes(dir_name: str, file: str) -> bytes | None:
    """读取打包内 logo 资产字节；目录/文件名越界或非资产路径一律拒绝（返回 None）.

    ``file`` 只接受纯文件名（不含路径分隔符、不含 ``..``），避免调用方把外部
    片段拼进来读到包内其他文件。
    """
    name = (file or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    if dir_name not in ("vendor-logos", "agent-logos"):
        return None
    try:
        return resources.files("hermes_fry_cards").joinpath("assets", dir_name, name).read_bytes()
    except (FileNotFoundError, IsADirectoryError, OSError, ModuleNotFoundError):
        return None


# 家族 → 官方拼写（厂商官网口径，如混元官网的 "Hy4 flash"）。
# 展示名 = 官方家族拼写 + 变体尾巴（模型名去掉渠道前缀/：标签后的剩余部分）。
_FAMILY_OFFICIAL: dict[str, str] = {
    "hy4": "Hy4",
    "hunyuan": "Hunyuan",
    "deepseek": "DeepSeek",
    "qwen": "Qwen",
    "glm": "GLM",
    "chatglm": "ChatGLM",
    "zhipu": "Zhipu",
    "zcode": "ZCode",
    "claude": "Claude",
    "anthropic": "Claude",
    "gemini": "Gemini",
    "kimi": "Kimi",
    "moonshot": "Moonshot",
    "minimax": "MiniMax",
    "doubao": "Doubao",
    "gpt": "GPT",
    "openai": "OpenAI",
    "step": "Step",
    "codebuddy": "CodeBuddy",
    "grok": "Grok",
    "longcat": "LongCat",
    "mimo": "MiMo",
    "dots": "Dots",
    "muse": "Muse",
    "ling": "Ling",
    "upstage": "Upstage",
}


def resolve_vendor_display_name(model: str) -> str | None:
    """代理拼接名 → 官方模型展示名；未命中家族返回 None.

    ``tencent/hy4-flash:free`` → 剥渠道前缀与 ``:free`` 标签 → ``hy4-flash`` →
    官方家族拼写 ``Hy4`` + 变体尾巴 ``flash`` → ``Hy4 flash``（混元官网口径）。
    """
    file = resolve_vendor_logo_asset(model)
    if not file:
        return None
    lower = model.lower()
    needle = next((n for n, f in VENDOR_LOGO_RULES if f == file and n in lower), None)
    if needle is None:
        return None
    official = _FAMILY_OFFICIAL.get(needle, needle.capitalize())
    base = model.strip().split("/")[-1].split(":")[0].strip()
    idx = base.lower().find(needle)
    if idx < 0:
        return official
    tail = base[idx + len(needle):].lstrip("-_").strip()
    return f"{official} {tail}".strip() or official


# 缓存键 → img_key（None = 上传失败，进程内不再重试）
_memo: dict[str, str | None] = {}
# 缓存键 → 上传中的 Future（并发去重：同一份字节只发起一次上传）
_inflight: dict[str, asyncio.Future[str | None]] = {}


def _app_scope(client: FeishuClient) -> str:
    """缓存键里的应用维度（image_key 属于上传它的 app，跨 app 不可复用）."""
    app_id = str(getattr(getattr(client, "config", None), "app_id", "") or "").strip()
    return app_id or "unknown-app"


async def resolve_vendor_logo_image_key(client: FeishuClient, model: str) -> str | None:
    """取面板头 logo 的 img_key（厂商优先、智能体兜底；懒上传 + 进程内缓存）.

    缓存键含 **资产内容摘要**：资产像素换过后旧 img_key 必然失效重传，否则修好的
    图标在早已发过卡的进程里永远显不出来（同姊妹仓 feige 的 ``_logo_cache_path`` 口径）。
    """
    asset = resolve_logo_asset(model)
    if not asset:
        return None
    dir_name, file = asset
    data = logo_asset_bytes(dir_name, file)
    if not data:
        return None
    cache_key = f"{_app_scope(client)}|{dir_name}/{file}|{hashlib.sha256(data).hexdigest()[:12]}"
    if cache_key in _memo:
        return _memo[cache_key]
    if cache_key in _inflight:
        return await _inflight[cache_key]

    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str | None] = loop.create_future()
    _inflight[cache_key] = fut
    try:
        key = await client.upload_image_bytes(data)
    except Exception:
        _logger.debug("logo upload failed for %s/%s", dir_name, file, exc_info=True)
        key = None
    _memo[cache_key] = key
    _inflight.pop(cache_key, None)
    fut.set_result(key)
    return key
