"""Studio「工具箱」后端 — AI 摘要 / 文字翻译的 LLM 代理路由.

安全边界（与 Studio 其余路由同一口径，另外三条是本模块特有的）：

* **凭据只出本机、只进端点**：api_key 从 ``config.yaml`` 现成的 ``model`` /
  ``custom_providers`` 段读，绝不回传前端、绝不进日志（``llm`` 层已对异常消息做掩码）；
* **只监听 loopback**：沿用 ``StudioHandler`` 的 Host 门，本模块不额外开口；
* **输入有上限**：粘贴超大文本直接 400，避免误把整份日志发给端点白花 token；
* **不写配置**：本路由只调模型，任何写操作仍走 ``/api/config`` 的白名单校验。

响应只含：文本、模型名、耗时、token 计数（``usage``）与来源标签，便于判断花了多少。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..llm import (
    LLMError,
    build_summary_messages,
    build_translate_messages,
    chat_completion,
    default_small_model,
    endpoint_model_ids,
    resolve_llm_endpoint,
)

if TYPE_CHECKING:
    from ..config import Config

_logger = logging.getLogger("hermes_fry_cards")

MODES = ("summary", "translate")
_MAX_INPUT_CHARS = 20_000  # 与 llm 层输入上限一致；超了直接拒，不发请求
_MAX_MODEL_LEN = 120
_MAX_LANG_LEN = 40
_DEFAULT_TIMEOUT_SEC = 20.0
_TIMEOUT_MIN_SEC = 1.0
_TIMEOUT_MAX_SEC = 120.0
# 摘要请求的输出 token 上限（CJK 1 字 ≤2 token，再留余量）
_SUMMARY_MAX_OUTPUT_TOKENS = 464
# 翻译按输入规模给输出预算（译文通常与原文同量级）
_TRANSLATE_TOKENS_PER_INPUT_CHAR = 2
_TRANSLATE_MAX_OUTPUT_TOKENS = 4096

# 常用目标语言（前端下拉；也可手填任意语言名）
TARGET_LANGS = (
    ("zh", "中文"),
    ("en", "English"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("ru", "Русский"),
    ("pt", "Português"),
    ("vi", "Tiếng Việt"),
    ("ar", "العربية"),
    ("th", "ไทย"),
)


def tools_state(cfg: Config) -> dict[str, Any]:
    """GET /api/state 的 tools 段 — 端点是否可用、候选模型与默认值（不含任何凭据）."""
    endpoint = resolve_llm_endpoint(cfg)
    ids = endpoint_model_ids(cfg, endpoint) if endpoint is not None else []
    default_model = cfg.ai_summary_model or (default_small_model(cfg, endpoint) if endpoint else "")
    return {
        "available": endpoint is not None,
        # 凭据来自哪一段（"model" / "custom_providers" / "providers"），只给标签不给值
        "source": endpoint.source if endpoint else "",
        "models": [
            {"id": name, "label": name}
            for name in ids[:200]
        ],
        "default_model": default_model,
        "target_langs": [{"id": code, "label": label} for code, label in TARGET_LANGS],
        "max_input_chars": _MAX_INPUT_CHARS,
        "timeout_sec": _DEFAULT_TIMEOUT_SEC,
    }


def _expect_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("text 必须是非空字符串")
    text = value.strip()
    if len(text) > _MAX_INPUT_CHARS:
        raise ValueError(f"text 超过 {_MAX_INPUT_CHARS} 字符上限（当前 {len(text)}）")
    return text


def _expect_mode(value: Any) -> str:
    if value not in MODES:
        raise ValueError(f"mode 必须是 {'/'.join(MODES)} 之一")
    return str(value)


def _expect_model(value: Any) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("model 必须是非空字符串")
    model = value.strip()
    if len(model) > _MAX_MODEL_LEN:
        raise ValueError(f"model 名称过长（>{_MAX_MODEL_LEN}）")
    if any(ord(c) < 32 for c in model):
        raise ValueError("model 不能包含控制字符")
    return model


def _expect_lang(value: Any) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("target_lang 必须是非空字符串")
    lang = value.strip()
    if len(lang) > _MAX_LANG_LEN:
        raise ValueError(f"target_lang 过长（>{_MAX_LANG_LEN}）")
    if any(ord(c) < 32 for c in lang):
        raise ValueError("target_lang 不能包含控制字符")
    return lang


def _expect_timeout(value: Any) -> float:
    if value is None:
        return _DEFAULT_TIMEOUT_SEC
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout_sec 必须是数字")
    return max(_TIMEOUT_MIN_SEC, min(_TIMEOUT_MAX_SEC, float(value)))


def build_messages(mode: str, text: str, target_lang: str) -> tuple[list[dict[str, str]], str]:
    """按模式构造消息，返回 (messages, 语言标签)."""
    if mode == "summary":
        return build_summary_messages(text, max_input_chars=_MAX_INPUT_CHARS), ""
    lang = target_lang or _auto_target_lang(text)
    return build_translate_messages(text, lang, max_input_chars=_MAX_INPUT_CHARS), lang


def _auto_target_lang(text: str) -> str:
    """没指定目标语言时的默认：中英互译（中文原文 → 英文，其余 → 中文）."""
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    return "英文" if cjk > max(4, len(text) // 20) else "中文"


def max_output_tokens(mode: str, text_len: int) -> int:
    if mode == "summary":
        return _SUMMARY_MAX_OUTPUT_TOKENS
    return min(_TRANSLATE_MAX_OUTPUT_TOKENS, max(256, text_len * _TRANSLATE_TOKENS_PER_INPUT_CHAR))


def run_tool(payload: Any, cfg: Config) -> dict[str, Any]:
    """POST /api/tools — 校验入参并调端点一次，返回 {ok, text, model, usage, elapsed_ms}.

    同步阻塞（stdlib urllib）：Studio 是 ``ThreadingHTTPServer``，每个请求自带一根线程，
    这里阻塞只挡住点按钮的那个人，不影响配置页/预览页；超时由入参 ``timeout_sec`` 兜。
    """
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是对象")
    unknown = set(payload) - {"mode", "text", "target_lang", "model", "timeout_sec"}
    if unknown:
        raise ValueError(f"未知字段: {', '.join(sorted(unknown))}")

    mode = _expect_mode(payload.get("mode"))
    text = _expect_text(payload.get("text"))
    target_lang = _expect_lang(payload.get("target_lang"))
    timeout_sec = _expect_timeout(payload.get("timeout_sec"))

    endpoint = resolve_llm_endpoint(cfg)
    if endpoint is None:
        raise LLMError(
            "config.yaml 里没有可用的模型端点：需要 model.base_url + model.api_key，"
            "或 custom_providers / providers 里带 api_key 的条目"
        )
    model = _expect_model(payload.get("model")) or cfg.ai_summary_model or default_small_model(cfg, endpoint)
    messages, lang = build_messages(mode, text, target_lang)

    try:
        result = chat_completion(
            endpoint,
            model,
            messages,
            timeout_sec=timeout_sec,
            max_tokens=max_output_tokens(mode, len(text)),
            temperature=0.2,
        )
    except LLMError as e:
        # 纵深防御：llm 层已对自家消息掩码，这里再兜一次——任何一条通往浏览器的
        # 错误文本都不许带 api_key（响应会被前端显示，日志同理）。
        raise LLMError(endpoint._mask(str(e))) from e
    out: dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "text": result.get("text"),
        "model": result.get("model") or model,
        "usage": result.get("usage") or {},
        "elapsed_ms": result.get("elapsed_ms"),
        "source": endpoint.source,
    }
    if lang:
        out["target_lang"] = lang
    return out
