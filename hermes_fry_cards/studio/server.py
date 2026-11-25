"""Studio 可视化配置工作坊 — 纯 stdlib HTTP 服务 + 原生前端（零第三方依赖）.

写回安全五件套（参考 aiduPOP studio 实践）：
1. 服务端结构校验 — 不信任前端，类型/枚举/范围/未知键全部拒绝（400）；
2. 拒写保护 — config.yaml 读不出或解析失败时拒绝写入（409），绝不覆盖无法解析的配置（保护飞书凭证）；
3. 写前备份 — ~/.hermes/backups/fry_studio/config.yaml.bak_<ns>，轮转保留 20 份；
4. 白名单深合并 — 只写受管键，用户手写的其他配置原样保留；
5. 原子写 — tmp + fsync + os.replace。

网络面：仅监听 loopback、Host 门防 DNS rebinding（非本机 Host 直接 403）、
不发 CORS 头、POST body ≤1MB、响应带 X-Content-Type-Options: nosniff。

注意：保存会重写整个 config.yaml（YAML 注释会丢失），UI 已显著提示。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import yaml

from ..config import Config, hermes_home
from ..llm import LLMError
from .tools import run_tool, tools_state

if TYPE_CHECKING:
    from ..streaming.segments import Segment
    from ..streaming.tooluse import ToolBlock, ToolDisplayStep

_logger = logging.getLogger("hermes_fry_cards")

_WEB_ROOT = (Path(__file__).parent / "web").resolve()
_MAX_BODY_BYTES = 1_000_000
_MAX_DRAIN_BYTES = 4_000_000  # 413 时有界排空上限（防未读数据触发客户端写端 RST）
_BACKUP_KEEP = 20
_BASE_HOSTS_OK = ("127.0.0.1", "localhost", "::1")
_ALLOWED_HOSTS_CACHE: list[str] | None = None  # loopback + studio.allowed_hosts（启动后缓存，重启生效）


def _allowed_hosts() -> list[str]:
    """Host 门白名单：内置 loopback + ``studio.allowed_hosts`` 配置附加（网段前缀或完整主机）."""
    global _ALLOWED_HOSTS_CACHE
    if _ALLOWED_HOSTS_CACHE is None:
        try:
            extra = Config().studio_allowed_hosts
        except Exception:
            extra = []
        _ALLOWED_HOSTS_CACHE = list(_BASE_HOSTS_OK) + [h for h in extra if h not in _BASE_HOSTS_OK]
    return _ALLOWED_HOSTS_CACHE

_FOOTER_FIELDS = ("status", "elapsed", "speed", "cache", "model", "tokens", "context")
_PANEL_FIELDS = ("model", "reasoning", "tools", "context", "elapsed", "speed", "cache", "tokens", "status")
_WIDTH_MODES = ("default", "compact", "fill")
_CONTEXT_MODES = ("text", "bar", "text_bar", "block", "block_text")
_LANGS = ("zh", "en")
_TEXT_SIZES = ("normal_v2", "normal", "heading", "notation")

_SCENARIOS = ("short", "workflow", "tables", "longtext")
_OUTCOMES = ("completed", "error", "aborted")
_STATES = ("streaming", "completed")

# 预览「模型」下拉：既接受已配置/已登记的模型，也允许手填任意 id（配置改了不必重启 Studio）
_PREVIEW_MODEL_MAX_LEN = 120
_PREVIEW_MODEL_MAX_OPTIONS = 200  # 候选上限：已配置模型优先占位，防止超大 provider 清单刷爆下拉
# 无任何配置可读时的兜底展示模型（与厂商家族样本池同口径，保证下拉非空）
_PREVIEW_FALLBACK_MODEL = "mimo/mimo-x-flash"
# 内置样本池：覆盖打包内厂商 logo 家族（口径同线上 10R fleet），
# 让「没配 model/custom_providers」的机器也能预览到 logo + 官方名映射效果
_PREVIEW_MODEL_SAMPLES = (
    "tencent/hy4-flash:free",
    "deepseek-v4.1-flash",
    "qwen4-max",
    "glm-5.3-flashx",
    "kimi-k5",
    "MiniMax-M3.1-Flash",
    "longcat/LongCat-2.5-Preview",
    "grok-4.7",
    "claude-opus-5.5",
    "mimo-v2.6-flash-free",
    "oc/ling-3.1-flash",
    "space-bunny-free",  # 未命中厂商家族 → 回落别名/⇲ 截断形态
)
_VENDOR_LOGO_URL_PREFIX = "/assets/vendor-logos/"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class ConfigReadError(Exception):
    """config.yaml 读不出/解析失败/结构异常 — 拒绝写入以保护凭证."""


class _Responded:
    """body 解析已自行响应的哨兵."""


_RESPONDED = _Responded()


# ---------------------------------------------------------------------------
# 服务端校验（不信任前端）
# ---------------------------------------------------------------------------


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _expect_bool(v: Any, name: str) -> bool:
    if not isinstance(v, bool):
        raise ValueError(f"{name} 必须是布尔值")
    return v


def _expect_choice(v: Any, name: str, allowed: tuple[str, ...]) -> str:
    if not isinstance(v, str) or v not in allowed:
        raise ValueError(f"{name} 必须是 {'/'.join(allowed)} 之一")
    return v


def _expect_token_str(v: Any, name: str, max_len: int = 32) -> str:
    valid = (
        isinstance(v, str)
        and 0 < len(v) <= max_len
        and v.replace("_", "").replace("-", "").isalnum()
        and v[0] in "abcdefghijklmnopqrstuvwxyz"
    )
    if not valid:
        raise ValueError(f"{name} 必须是小写字母数字连字符下划线组成的短串")
    return str(v)


def _expect_notice_text(v: Any, name: str) -> str:
    if not isinstance(v, str) or not 1 <= len(v.strip()) <= 80:
        raise ValueError(f"{name} 必须是 1~80 字符的非空字符串")
    if any(ord(char) < 32 for char in v):
        raise ValueError(f"{name} 不能包含控制字符")
    return v.strip()


def _expect_range(v: Any, name: str, lo: float, hi: float) -> float:
    if not _is_number(v) or not lo <= float(v) <= hi:
        raise ValueError(f"{name} 必须是 {lo}~{hi} 之间的数字")
    return float(v)


def _validate_fields(v: Any, name: str) -> list[list[str]]:
    if not isinstance(v, list):
        raise ValueError(f"{name} 必须是二维数组")
    if len(v) > 4:
        raise ValueError(f"{name} 最多 4 行")
    rows: list[list[str]] = []
    for row in v:
        if not isinstance(row, list) or not row:
            raise ValueError(f"{name} 每行必须是非空数组")
        if len(row) > len(_FOOTER_FIELDS):
            raise ValueError(f"{name} 每行最多 {len(_FOOTER_FIELDS)} 个字段")
        seen: set[str] = set()
        for cell in row:
            if not isinstance(cell, str) or cell not in _FOOTER_FIELDS:
                raise ValueError(f"{name} 字段必须是 {'/'.join(_FOOTER_FIELDS)} 之一")
            if cell in seen:
                raise ValueError(f"{name} 同一行字段不能重复")
            seen.add(cell)
        rows.append(list(row))
    return rows


def _validate_panel_fields(v: Any, name: str) -> list[str]:
    """统一面板 header 字段（有序一维列表，可空 = 回落默认布局）."""
    if not isinstance(v, list):
        raise ValueError(f"{name} 必须是数组")
    if len(v) > len(_PANEL_FIELDS):
        raise ValueError(f"{name} 最多 {len(_PANEL_FIELDS)} 个字段")
    seen: set[str] = set()
    for cell in v:
        if not isinstance(cell, str) or cell not in _PANEL_FIELDS:
            raise ValueError(f"{name} 字段必须是 {'/'.join(_PANEL_FIELDS)} 之一")
        if cell in seen:
            raise ValueError(f"{name} 字段不能重复")
        seen.add(cell)
    return list(v)


def _validate_chat_types(v: Any, name: str) -> list[str] | None:
    if v is None:
        return None
    if not isinstance(v, list):
        raise ValueError(f"{name} 必须是数组或 null")
    result: list[str] = []
    for item in v:
        token = _expect_token_str(item, name)
        if token not in result:
            result.append(token)
    return result


def _validate_chat_list(v: Any, name: str) -> list[str]:
    """群 ID 白名单（如 allow_chats）：非空字符串数组，去重保序，单个 ≤64 且无空白/控制字符."""
    if not isinstance(v, list):
        raise ValueError(f"{name} 必须是数组（一行一个群 chat_id）")
    if len(v) > 200:
        raise ValueError(f"{name} 最多 200 个")
    out: list[str] = []
    for item in v:
        if not isinstance(item, str) or not item or len(item) > 64:
            raise ValueError(f"{name} 每项必须是 1~64 字符的字符串 chat_id")
        if item != item.strip() or any(c.isspace() or ord(c) < 32 for c in item):
            raise ValueError(f"{name} 含空白或控制字符: {item!r}")
        if item not in out:
            out.append(item)
    return out


def _expect_model_str(v: Any, name: str) -> str:
    """模型 id：非空短串，允许斜杠/点/冒号/减号/下划线（``cbcn/hy4-flash:free``）."""
    if not isinstance(v, str):
        raise ValueError(f"{name} 必须是字符串")
    model = v.strip()
    if len(model) > 120:
        raise ValueError(f"{name} 最长 120 字符")
    if any(ord(char) < 32 for char in model):
        raise ValueError(f"{name} 不能包含控制字符")
    if model and not re.fullmatch(r"[\w./:@+\[\]-]+", model):
        raise ValueError(f"{name} 只允许字母数字与 . / : @ + _ - [ ] 等模型 id 常用字符")
    return model


def _validate_str_list(v: Any, name: str) -> list[str]:
    """通用字符串名单(如 #55 的工具集白/黑名单): 非空字符串数组，去重保序."""
    if not isinstance(v, list):
        raise ValueError(f"{name} 必须是数组(一行一个名称)")
    if len(v) > 50:
        raise ValueError(f"{name} 最多 50 个")
    out: list[str] = []
    for item in v:
        if not isinstance(item, str) or not item or len(item) > 48:
            raise ValueError(f"{name} 每项必须是 1~48 字符的字符串")
        if item != item.strip() or any(c.isspace() or ord(c) < 32 for c in item):
            raise ValueError(f"{name} 含空白或控制字符: {item!r}")
        if item not in out:
            out.append(item)
    return out


_STREAMING_SCALARS: dict[str, str] = {
    "enabled": "bool",
    "completion_notice": "bool",
    "completion_notice_text": "notice_text",
    "content_lang": "lang",
    "chat_types": "chat_types",
    "panel_expanded": "bool",
    "width_mode": "width",
    "flush_interval_ms": "flushms",
}
_HEADER_KEYS = {"enabled": "bool", "min_duration": "dur"}
_FOOTER_KEYS = {
    "enabled": "bool",
    "show_label": "bool",
    "fields": "fields",
    "text_size": "textsize",
}
_BODY_KEYS = {"text_size": "textsize"}
_DISPLAY_KEYS: dict[str, str] = {
    "show_reasoning": "bool",
    "show_tool_use": "bool",
    "show_context": "bool",
    "truncate_model_name": "bool",
    "vendor_logo": "bool",
    "model_aliases_enabled": "bool",
    "max_reasoning_panels": "panels",
    "unified_panel_min_duration": "dur600",
    "context_display_mode": "context",
    "panel_fields": "panel_fields",
    "panel_text_size": "textsize",
}
# 完结卡 AI 摘要（streaming.ai_summary）：默认关；阈值/模型/超时/长度全部可调
_AI_SUMMARY_KEYS = {
    "enabled": "bool",
    "min_chars": "chars",
    "model": "model",
    "timeout_sec": "dur120",
    "max_output_chars": "outchars",
}
_GATEWAY_KEYS = {
    "enabled": "bool",
    "allow_chats": "chatlist",
    "non_admin_allowed_toolsets": "strlist",
    "non_admin_disabled_toolsets": "strlist",
}


def _validate_scalar(v: Any, name: str, kind: str) -> Any:
    if kind == "bool":
        return _expect_bool(v, name)
    if kind == "notice_text":
        return _expect_notice_text(v, name)
    if kind == "lang":
        return _expect_choice(v, name, _LANGS)
    if kind == "width":
        return _expect_choice(v, name, _WIDTH_MODES)
    if kind == "context":
        return _expect_choice(v, name, _CONTEXT_MODES)
    if kind == "chat_types":
        return _validate_chat_types(v, name)
    if kind == "chatlist":
        return _validate_chat_list(v, name)
    if kind == "strlist":
        return _validate_str_list(v, name)
    if kind == "fields":
        return _validate_fields(v, name)
    if kind == "panel_fields":
        return _validate_panel_fields(v, name)
    if kind == "textsize":
        return _expect_choice(v, name, _TEXT_SIZES)
    if kind == "dur":
        return _expect_range(v, name, 0, 86400)
    if kind == "dur600":
        return _expect_range(v, name, 0, 600)
    if kind == "dur120":
        return _expect_range(v, name, 1, 120)
    if kind == "chars":  # 摘要触发阈值：0 = 任何回复都摘要
        _expect_range(v, name, 0, 100_000)
        return int(v)
    if kind == "outchars":  # 摘要目标字数
        _expect_range(v, name, 40, 600)
        return int(v)
    if kind == "model":
        return _expect_model_str(v, name)
    if kind == "panels":
        _expect_range(v, name, 1, 50)
        return int(v)
    if kind == "flushms":
        _expect_range(v, name, 50, 2000)
        return int(v)
    raise ValueError(f"未知校验类型: {kind}")


def _validate_section(payload: dict, section: str, spec: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get(section)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{section} 必须是对象")
    unknown = set(raw) - set(spec)
    if unknown:
        raise ValueError(f"{section} 含未知字段: {', '.join(sorted(unknown))}")
    result: dict[str, Any] = {}
    for key, val in raw.items():
        kind = spec[key]
        if isinstance(kind, dict):
            if not isinstance(val, dict):
                raise ValueError(f"{section}.{key} 必须是对象")
            sub_unknown = set(val) - set(kind)
            if sub_unknown:
                raise ValueError(f"{section}.{key} 含未知字段: {', '.join(sorted(sub_unknown))}")
            result[key] = {k: _validate_scalar(v, f"{section}.{key}.{k}", kind[k]) for k, v in val.items()}
        else:
            result[key] = _validate_scalar(val, f"{section}.{key}", kind)
    return result


def validate_payload(payload: Any) -> dict[str, Any]:
    """校验 POST /api/config 或预览 overrides — 返回仅含受管键的规范化结构."""
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是对象")
    unknown = set(payload) - {"streaming", "display", "gateway"}
    if unknown:
        raise ValueError(f"未知顶层字段: {', '.join(sorted(unknown))}")

    streaming_spec: dict[str, Any] = dict(_STREAMING_SCALARS)
    streaming_spec.update(
        {"header": _HEADER_KEYS, "footer": _FOOTER_KEYS, "body": _BODY_KEYS, "ai_summary": _AI_SUMMARY_KEYS}
    )

    result: dict[str, Any] = {}
    streaming = _validate_section(payload, "streaming", streaming_spec)
    if streaming:
        result["streaming"] = streaming
    display = _validate_section(payload, "display", _DISPLAY_KEYS)
    if display:
        result["display"] = display
    gateway = _validate_section(payload, "gateway", {"group_security_boundary": _GATEWAY_KEYS})
    if gateway:
        result["gateway"] = gateway
    if not result:
        raise ValueError("payload 至少要包含 streaming、display 或 gateway 段")
    return result


# ---------------------------------------------------------------------------
# 配置读写（拒写 / 备份 / 白名单合并 / 原子写）
# ---------------------------------------------------------------------------


def read_full_config(conf_path: Path) -> dict[str, Any]:
    if not conf_path.exists():
        return {}
    try:
        raw = yaml.safe_load(conf_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise ConfigReadError(f"config.yaml 读取/解析失败，拒绝写入以保护凭证: {e}") from e
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigReadError("config.yaml 顶层不是映射，拒绝写入")
    return raw


def read_full_json(path: Path) -> dict[str, Any]:
    """读取 JSON 配置（model_aliases.json）；读不出/解析失败 → 拒写."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ConfigReadError(f"{path.name} 读取/解析失败，拒绝写入: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigReadError(f"{path.name} 顶层不是映射，拒绝写入")
    return raw


def backup_config(conf_path: Path) -> Path | None:
    if not conf_path.exists():
        return None
    bk_dir = conf_path.parent / "backups" / "fry_studio"
    bk_dir.mkdir(parents=True, exist_ok=True)
    target = bk_dir / f"{conf_path.name}.bak_{time.time_ns()}"
    shutil.copy2(conf_path, target)
    backups = sorted(bk_dir.glob(f"{conf_path.name}.bak_*"), key=lambda p: p.name)
    for old in backups[:-_BACKUP_KEEP]:
        with contextlib.suppress(OSError):
            old.unlink()
    return target


def merge_managed(base: dict[str, Any], validated: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """白名单深合并 — 只写受管键；返回 (新配置, 变更键列表). 结构异常 → ConfigReadError."""
    out = dict(base)
    changed: list[str] = []

    def _track(path: str, old: Any, has: bool, value: Any) -> bool:
        if not has or old != value:
            changed.append(path)
            return True
        return False

    if "streaming" in validated:
        sec_raw = out.get("streaming")
        if sec_raw is None:
            sec: dict[str, Any] = {}
            out["streaming"] = sec
        elif isinstance(sec_raw, dict):
            sec = sec_raw
        else:
            raise ConfigReadError("streaming 段不是映射，拒绝写入")
        for key, value in validated["streaming"].items():
            if isinstance(value, dict):
                sub_raw = sec.get(key)
                if sub_raw is None:
                    sub: dict[str, Any] = {}
                    sec[key] = sub
                elif isinstance(sub_raw, dict):
                    sub = sub_raw
                else:
                    raise ConfigReadError(f"streaming.{key} 不是映射，拒绝写入")
                for k2, v2 in value.items():
                    path = f"streaming.{key}.{k2}"
                    if _track(path, sub.get(k2, _MISSING), k2 in sub, v2):
                        sub[k2] = v2
            else:
                path = f"streaming.{key}"
                if _track(path, sec.get(key, _MISSING), key in sec, value):
                    sec[key] = value

    if "display" in validated:
        disp_raw = out.get("display")
        if disp_raw is None:
            disp: dict[str, Any] = {}
            out["display"] = disp
        elif isinstance(disp_raw, dict):
            disp = disp_raw
        else:
            raise ConfigReadError("display 段不是映射，拒绝写入")
        plat_raw = disp.get("platforms")
        if plat_raw is None:
            plat: dict[str, Any] = {}
            disp["platforms"] = plat
        elif isinstance(plat_raw, dict):
            plat = plat_raw
        else:
            raise ConfigReadError("display.platforms 不是映射，拒绝写入")
        feishu_raw = plat.get("feishu")
        if feishu_raw is None:
            feishu: dict[str, Any] = {}
            plat["feishu"] = feishu
        elif isinstance(feishu_raw, dict):
            feishu = feishu_raw
        else:
            raise ConfigReadError("display.platforms.feishu 不是映射，拒绝写入")
        for key, value in validated["display"].items():
            path = f"display.platforms.feishu.{key}"
            if _track(path, feishu.get(key, _MISSING), key in feishu, value):
                feishu[key] = value

    if "gateway" in validated:
        gw_raw = out.get("gateway")
        if gw_raw is None:
            gw: dict[str, Any] = {}
            out["gateway"] = gw
        elif isinstance(gw_raw, dict):
            gw = gw_raw
        else:
            raise ConfigReadError("gateway 段不是映射，拒绝写入")
        for section, sub in validated["gateway"].items():
            sub_raw = gw.get(section)
            if sub_raw is None:
                subd: dict[str, Any] = {}
                gw[section] = subd
            elif isinstance(sub_raw, dict):
                subd = sub_raw
            else:
                raise ConfigReadError(f"gateway.{section} 不是映射，拒绝写入")
            for k2, v2 in sub.items():
                path = f"gateway.{section}.{k2}"
                if _track(path, subd.get(k2, _MISSING), k2 in subd, v2):
                    subd[k2] = v2

    return out, changed


class _Missing:
    pass


_MISSING = _Missing()


def write_atomic(conf_path: Path, data: dict[str, Any]) -> None:
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    tmp = conf_path.parent / (conf_path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, conf_path)


# ---------------------------------------------------------------------------
# 模型别名（model_aliases.json）— 校验 / 读写（时段人设格式兼容 claw-fry-cards）
# ---------------------------------------------------------------------------

_ALIAS_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_MAX_ALIAS_ENTRIES = 200
_MAX_TIME_RULES = 16


def _validate_alias_key(v: Any, idx: int) -> str:
    if not isinstance(v, str) or not v or v != v.strip() or len(v) > 64:
        raise ValueError(f"别名第 {idx + 1} 行的键必须是 1~64 字符且不含首尾空白")
    if any(ord(c) < 32 for c in v):
        raise ValueError(f"别名第 {idx + 1} 行的键含控制字符")
    return v


def _validate_alias_name(v: Any, where: str) -> str:
    if not isinstance(v, str) or not v or len(v) > 64:
        raise ValueError(f"{where} 名称必须是 1~64 字符字符串")
    return v


def _validate_alias_days(v: Any, where: str) -> Any:
    if isinstance(v, list):
        if not v or len(v) > 7:
            raise ValueError(f"{where} days 数组需 1~7 个星期值")
        out: list[int] = []
        for d in v:
            if not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 6:
                raise ValueError(f"{where} days 数组每项必须是 0~6 的整数（0=周日）")
            if d not in out:
                out.append(d)
        return out
    if isinstance(v, str):
        for part in v.split(","):
            part = part.strip()
            if "-" in part:
                a, _, z = part.partition("-")
                if not (a.isdigit() and z.isdigit() and 0 <= int(a) <= 6 and 0 <= int(z) <= 6):
                    raise ValueError(f'{where} days 字符串需形如 "1-5"、"0,6"、"1-5,0"')
            elif not (part.isdigit() and 0 <= int(part) <= 6):
                raise ValueError(f'{where} days 字符串需形如 "1-5"、"0,6"、"1-5,0"')
        return v
    raise ValueError(f"{where} days 必须是数组或字符串")


def _validate_alias_entry_value(value: Any, idx: int) -> Any:
    if isinstance(value, str):
        return _validate_alias_name(value, f"别名第 {idx + 1} 行")
    if not isinstance(value, dict):
        raise ValueError(f"别名第 {idx + 1} 行的值必须是字符串或时段人设对象")
    unknown = set(value) - {"name", "timeAliases"}
    if unknown:
        raise ValueError(f"别名第 {idx + 1} 行对象含未知字段: {', '.join(sorted(unknown))}")
    name = value.get("name")
    if name is not None:
        _validate_alias_name(name, f"别名第 {idx + 1} 行默认名")
    rules = value.get("timeAliases")
    if rules is not None and not isinstance(rules, list):
        raise ValueError(f"别名第 {idx + 1} 行 timeAliases 必须是数组")
    if rules:
        if len(rules) > _MAX_TIME_RULES:
            raise ValueError(f"别名第 {idx + 1} 行时段规则最多 {_MAX_TIME_RULES} 条")
        cleaned_rules: list[dict[str, Any]] = []
        for ri, rule in enumerate(rules):
            where = f"别名第 {idx + 1} 行第 {ri + 1} 条时段规则"
            if not isinstance(rule, dict):
                raise ValueError(f"{where} 必须是对象")
            r_unknown = set(rule) - {"days", "start", "end", "name"}
            if r_unknown:
                raise ValueError(f"{where} 含未知字段: {', '.join(sorted(r_unknown))}")
            r_name = rule.get("name")
            if not r_name:
                raise ValueError(f"{where} 缺少 name")
            _validate_alias_name(r_name, where)
            cleaned: dict[str, Any] = {"name": r_name}
            if "days" in rule:
                cleaned["days"] = _validate_alias_days(rule["days"], where)
            for tm in ("start", "end"):
                if tm in rule:
                    tv = rule[tm]
                    if not isinstance(tv, str) or not _ALIAS_HHMM_RE.match(tv):
                        raise ValueError(f"{where} 的 {tm} 必须是 HH:MM（00:00~23:59）")
                    cleaned[tm] = tv
            cleaned_rules.append(cleaned)
        result: dict[str, Any] = dict(value)
        result["timeAliases"] = cleaned_rules
        if not result.get("name") and not cleaned_rules:
            raise ValueError(f"别名第 {idx + 1} 行对象至少需要 name 或 timeAliases")
        return result
    if not name:
        raise ValueError(f"别名第 {idx + 1} 行对象至少需要 name 或 timeAliases")
    return {"name": name}


def validate_alias_entries(payload: Any) -> list[tuple[str, Any]]:
    """校验 POST /api/aliases — 返回有序 [(key, value), ...]；非法 → ValueError(400)."""
    if not isinstance(payload, dict) or set(payload) != {"entries"}:
        raise ValueError("payload 必须是 {\"entries\": [...]}")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError("entries 必须是数组")
    if len(entries) > _MAX_ALIAS_ENTRIES:
        raise ValueError(f"别名最多 {_MAX_ALIAS_ENTRIES} 条")
    out: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(entries):
        if not isinstance(item, dict) or set(item) != {"key", "value"}:
            raise ValueError(f"别名第 {idx + 1} 行必须是 {{key, value}} 对象")
        key = _validate_alias_key(item.get("key"), idx)
        lk = key.lower()
        if lk in seen:
            raise ValueError(f"别名键重复: {key}")
        seen.add(lk)
        out.append((key, _validate_alias_entry_value(item.get("value"), idx)))
    return out


# ---------------------------------------------------------------------------
# 状态采集（Tab 3 — 结构化复用 status/verify 的底层 API）
# ---------------------------------------------------------------------------


def _load_env_file(home: Path) -> None:
    """对齐网关：先加载 $HERMES_HOME/.env 再读凭据（与 __main__._cmd_status 一致）."""
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
    except ImportError:
        return
    with contextlib.suppress(OSError):
        load_dotenv(home / ".env", override=False)


def _marker_label(begin: str) -> str:
    return begin.replace("# HERMES_LARK_", "").replace("_BEGIN", "").lower()


def _fry_version() -> str | None:
    """插件版本：pip 元数据（pyproject）为准，__version__ 兜底。"""
    try:
        from importlib.metadata import version as _md_version

        return _md_version("hermes-fry-cards")
    except Exception:
        pass
    try:
        from . import __version__

        return __version__
    except Exception:
        return None


def _hermes_cli_version() -> str | None:
    """Hermes Agent 版本（hermes --version 首行），尽力而为。"""
    cli = shutil.which("hermes")
    if not cli:
        return None
    try:
        proc = subprocess.run(
            [cli, "--version"], capture_output=True, text=True, timeout=10, check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    out = (proc.stdout or proc.stderr or "").strip().splitlines()
    return out[0].strip() if out else None


def _collect_status(home: Path) -> dict[str, Any]:
    st: dict[str, Any] = {
        "fry_version": _fry_version(),
        "hermes_version": _hermes_cli_version(),
        "patched": None,
        "target": None,
        "markers": {},
        "cron_hook": "unavailable",
        "clarify_hook": "unavailable",
        "verify": {"gateway": "unavailable", "cron": "unavailable", "clarify": "unavailable"},
        "credentials": False,
        "streaming_enabled": None,
        "content_lang": None,
        "hermes_python": None,
        "install_dir": None,
    }
    try:
        from ..patcher import ClarifyPatcher, CronPatcher, Patcher, PatcherError, hermes_install_dir, hermes_python
    except Exception:
        return st

    try:
        patcher = Patcher()
        st["target"] = str(patcher.run_path)
        st["patched"] = bool(patcher.is_patched())
        if st["patched"]:
            st["markers"] = {_marker_label(b): bool(f) for b, f in patcher.marker_status().items()}
        try:
            patcher.verify_target()
            st["verify"]["gateway"] = "compatible"
        except Exception:
            st["verify"]["gateway"] = "incompatible"
    except PatcherError:
        pass

    try:
        cron = CronPatcher()
        st["cron_hook"] = "installed" if cron.is_patched() else "not_installed"
        try:
            cron.verify_target()
            st["verify"]["cron"] = "compatible"
        except Exception:
            st["verify"]["cron"] = "incompatible"
    except PatcherError:
        pass

    try:
        clarify = ClarifyPatcher()
        st["clarify_hook"] = "installed" if clarify.is_patched() else "not_installed"
        try:
            clarify.verify_target()
            st["verify"]["clarify"] = "compatible"
        except Exception:
            st["verify"]["clarify"] = "incompatible"
    except PatcherError:
        pass

    _load_env_file(home)
    cfg = Config(home=home)
    st["credentials"] = bool(cfg.env_app_id or cfg.feishu_app_id)
    st["streaming_enabled"] = cfg.enabled
    st["content_lang"] = cfg.content_lang
    try:
        py = hermes_python()
        st["hermes_python"] = str(py) if py is not None else None
        d = hermes_install_dir()
        st["install_dir"] = str(d) if d is not None else None
    except Exception:
        pass
    return st


def collect_state(home: Path | None = None) -> dict[str, Any]:
    """GET /api/state — 当前受管配置值（经 Config 属性，含平台覆盖优先级）+ 状态诊断."""
    home = home or hermes_home()
    cfg = Config(home=home)
    chat = cfg.chat_types
    streaming = {
        "enabled": cfg.enabled,
        "content_lang": cfg.content_lang,
        "chat_types": sorted(chat) if chat is not None else None,
        "panel_expanded": cfg.panel_expanded,
        "completion_notice": cfg.completion_notice,
        "completion_notice_text": cfg.completion_notice_text,
        "width_mode": cfg.width_mode,
        "flush_interval_ms": cfg.flush_interval_ms,
        "header": {"enabled": cfg.header_enabled, "min_duration": cfg.header_min_duration},
        "footer": {
            "enabled": cfg.footer_enabled,
            "show_label": cfg.footer_show_label,
            "fields": cfg.footer_fields,
            "text_size": cfg.footer_text_size,
        },
        "body": {"text_size": cfg.body_text_size},
        # 完结卡 AI 摘要（默认关；Studio「配置」页可开，阈值/模型/超时可调）
        "ai_summary": {
            "enabled": cfg.ai_summary_enabled,
            "min_chars": cfg.ai_summary_min_chars,
            "model": cfg.ai_summary_model,
            "timeout_sec": cfg.ai_summary_timeout_sec,
            "max_output_chars": cfg.ai_summary_max_output_chars,
        },
    }
    display = {
        "show_reasoning": cfg.show_reasoning,
        "show_tool_use": cfg.show_tool_use,
        "show_context": cfg.show_context,
        "truncate_model_name": cfg.truncate_model_name,
        "vendor_logo": cfg.vendor_logo,
        "model_aliases_enabled": cfg.model_aliases_enabled,
        "max_reasoning_panels": cfg.max_reasoning_panels,
        "unified_panel_min_duration": cfg.unified_panel_min_duration,
        "context_display_mode": cfg.context_display_mode,
        "panel_fields": cfg.panel_fields,
        "panel_text_size": cfg.panel_text_size,
    }
    gateway = {"group_security_boundary": cfg.group_security_boundary}
    return {
        "streaming": streaming,
        "display": display,
        "gateway": gateway,
        "preview": preview_models(cfg),
        "tools": tools_state(cfg),
        "status": _collect_status(home),
    }


# ---------------------------------------------------------------------------
# 预览 — 服务端真实 builder 渲染（与线上卡片同一代码路径）
# ---------------------------------------------------------------------------


def _mk_step(
    name: str,
    title: str,
    status: str,
    detail: str,
    icon: str,
    elapsed_ms: float,
    *,
    result_block: ToolBlock | None = None,
    error_block: ToolBlock | None = None,
) -> ToolDisplayStep:
    return {
        "name": name,
        "title": title,
        "status": status,
        "detail": detail,
        "output": "",
        "error": "",
        "icon": icon,
        "elapsed_ms": elapsed_ms,
        "result_block": result_block,
        "error_block": error_block,
    }


def _mk_seg(
    seg_type: str,
    el_id: str,
    *,
    text: str = "",
    text_el: str = "",
    elapsed_ms: float = 0.0,
    tool_offset: int = 0,
    tool_end: int = 0,
) -> Segment:
    from ..streaming.segments import Segment

    s = Segment(seg_type, el_id)
    s.text = text
    s.text_el_id = text_el
    s.elapsed_ms = elapsed_ms
    s.tool_offset = tool_offset
    s.tool_end_offset = tool_end
    s.created = True
    s.dirty = False
    return s


def _workflow_steps() -> list[ToolDisplayStep]:
    return [
        _mk_step(
            "read", "Read AGENTS.md", "success", "512 lines", "file-link-text_outlined", 310,
            result_block={"language": "text", "content": "# AGENTS.md\n## Commands\n...", "fenced": ""},
        ),
        _mk_step("grep", "Grep hook anchors", "success", "18 matches", "doc-search_outlined", 460),
        _mk_step("bash", "Run pytest -q", "running", "collecting tests...", "setting_outlined", 1500),
        _mk_step("edit", "Edit builder.py", "success", "+12 −3", "edit_outlined", 240),
        _mk_step(
            "glob", "Find sample files", "error", "permission denied", "folder_outlined", 90,
            error_block={"language": "text", "content": "PermissionError: [Errno 13]", "fenced": ""},
        ),
    ]


def _preview_default_model(cfg: Config) -> str:
    """已配置模型（config.yaml ``model.default`` / ``model.model``，兼容字符串形态）.

    与 Hermes ``_resolve_gateway_model`` 同口径；读不出（无配置/结构异常）返回空串。
    """
    raw = cfg.raw_section("model")
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        for key in ("default", "model"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _configured_models(cfg: Config) -> list[str]:
    """配置里出现过的模型 id（已配置模型 + custom_providers / providers 名单），保序去重.

    结构完全按「尽力而为」读取：任何一段形态不符就跳过该段，绝不因此报错——
    Studio 预览只是给个人挑一个名字渲染，配置写得再怎么稀奇也不该把预览页打死。
    """
    out: list[str] = []

    def _add(value: Any) -> None:
        if isinstance(value, str):
            name = value.strip()
            if name and name not in out:
                out.append(name)

    def _walk_models(value: Any) -> None:
        if isinstance(value, str):
            _add(value)
        elif isinstance(value, dict):
            for key in value:  # custom_providers[].models 是 {模型名: {...}} 映射
                _add(key)
        elif isinstance(value, (list, tuple)):
            for item in value:  # 也兼容 ["model-a", "model-b"] 形态
                _add(item)

    _add(_preview_default_model(cfg))
    for section in ("custom_providers", "providers", "fallback_providers", "fallback_model"):
        raw = cfg.raw_section(section)
        if isinstance(raw, dict):  # providers 也可能写成 {slug: {...}} 映射
            entries = list(raw.values())
        elif isinstance(raw, (list, tuple)):
            entries = list(raw)
        else:
            entries = [raw]
        for entry in entries:
            if not isinstance(entry, dict):
                _add(entry)
                continue
            _add(entry.get("model"))
            _walk_models(entry.get("models"))
    return out


def preview_models(cfg: Config | None = None) -> dict[str, Any]:
    """GET /api/state 的 preview 段 — 渲染预览「模型」下拉的候选与默认值.

    候选 = 已配置模型 + 配置内出现过的模型 + 内置厂商家族样本池（覆盖打包内 logo
    家族，没配 model/custom_providers 的机器也能预览 logo/官方名映射效果）。
    默认选中已配置模型，读不到时回落兜底样本；总量有界（已配置项优先占位）。
    """
    from ..cardkit.vendor_logos import resolve_vendor_display_name

    cfg = cfg or Config()
    ids: list[str] = []
    # 已配置模型/配置内模型在前（用户最可能挑它们），样本池补足家族覆盖；总量有界
    for name in [*_configured_models(cfg), *_PREVIEW_MODEL_SAMPLES, _PREVIEW_FALLBACK_MODEL]:
        if name and name not in ids:
            ids.append(name)
        if len(ids) >= _PREVIEW_MODEL_MAX_OPTIONS:
            break
    if _PREVIEW_FALLBACK_MODEL not in ids:  # 截断兜底：默认值必须在候选里
        ids.append(_PREVIEW_FALLBACK_MODEL)

    models: list[dict[str, str]] = []
    for name in ids:
        display = resolve_vendor_display_name(name) or ""
        models.append({"id": name, "label": f"{name} → {display}" if display and display != name else name})
    default = _preview_default_model(cfg) or _PREVIEW_FALLBACK_MODEL
    return {"models": models, "default_model": default}


def _preview_logo_key(model: str) -> str:
    """预览用 logo 引用：命中厂商家族 → Studio 本地静态 PNG 路径；未命中 → 空串.

    线上 logo 走「上传飞书换 img_key」（需 FeishuClient），Studio 不碰凭据，
    改由 ``/assets/vendor-logos/<file>`` 直接提供打包内同一份 PNG——builder 只把
    这个值当字符串透传（footer ``![](key)`` 与面板 header ``custom_icon.img_key``），
    渲染判定链路（官方名映射、logo 位、回落 🍟）与线上完全一致。
    """
    from ..cardkit.vendor_logos import resolve_vendor_logo_asset

    asset = resolve_vendor_logo_asset(model)
    return f"{_VENDOR_LOGO_URL_PREFIX}{asset}" if asset else ""


def _scenario_data(
    scenario: str, model: str = _PREVIEW_FALLBACK_MODEL,
) -> tuple[list[Segment], list[ToolDisplayStep], dict[str, Any], float]:
    """返回 (segments, all_tool_steps, footer_data, streaming_elapsed_ms).

    ``model`` 为本轮预览要渲染的模型 id（footer/统一面板的 model 字段数据源，
    口径同线上 ``session.footer["model"]``）。
    """
    base_footer = {
        "model": model,
        "input_tokens": 15420,
        "output_tokens": 986,
        "cache_read_tokens": 8300,  # 8300/15420 ≈ 54%，展示 speed/cache 字段效果
        "tokens_per_sec": 208,
        "context_used": 41200,
        "context_max": 1000000,
    }
    if scenario == "short":
        segs = [_mk_seg("answer", "answer_1", text="快捷回复测试：短耗时应按阈值隐藏 header 与统一面板。")]
        return segs, [], {**base_footer, "duration": 0.6}, 600

    steps = _workflow_steps()
    if scenario == "tables":
        rows = "\n\n".join(
            f"| 项目 | 值 |\n|---|---|\n| 指标{i} | {i * 11} |" for i in range(1, 7)
        )
        segs = [_mk_seg("answer", "answer_1", text=f"多表格压力测试（第 6 张起应压缩为字段列表）：\n\n{rows}")]
        return segs, steps, {**base_footer, "duration": 8.2}, 8200

    if scenario == "longtext":
        para = "这是模拟的长篇分析内容，用于验证字节预算与中段省略的展示效果，段落会持续增长直到触发 18KB 上限。"
        head = "## 长文本压力测试\n\n开头背景：本次任务需要对大量上下文进行归纳。\n\n"
        text = head + para * 220 + "\n\n**结论**：末尾核心结论应当保留。"
        segs = [_mk_seg("answer", "answer_1", text=text)]
        return segs, steps, {**base_footer, "duration": 15.4}, 15400

    # workflow（默认）— 思考1 → 工具组1 → 思考2 → 工具组2 → 答案
    answer = (
        "## 结论\n\n已完成注入验证：\n\n- gateway hook 全部就位\n- cron / clarify 兼容\n\n"
        "| 目标 | 结果 |\n|---|---|\n| gateway | ✅ |\n| cron | ✅ |\n\n"
        "```bash\nhermes gateway restart\n```"
    )
    segs = [
        _mk_seg(
            "reasoning", "reasoning_1", text="先确认注入锚点在 0.21.1 的形态是否变化……",
            text_el="reasoning_1_text", elapsed_ms=4200,
        ),
        _mk_seg("tool", "tool_1", elapsed_ms=780, tool_offset=0, tool_end=2),
        _mk_seg(
            "reasoning", "reasoning_2", text="return 已改为赋值形态，需要兼容两种锚点。",
            text_el="reasoning_2_text", elapsed_ms=3100,
        ),
        _mk_seg("tool", "tool_2", elapsed_ms=1400, tool_offset=2, tool_end=5),
        _mk_seg("answer", "answer_3", text=answer),
    ]
    return segs, steps, {**base_footer, "duration": 14.6}, 14600


def _apply_overrides(vals: dict[str, Any], overrides_raw: Any) -> dict[str, Any]:
    if overrides_raw is None:
        return vals
    validated = validate_payload(overrides_raw)
    out = {
        "streaming": dict(vals.get("streaming", {})),
        "display": dict(vals.get("display", {})),
    }
    for sec in ("streaming", "display"):
        if sec not in validated:
            continue
        for key, value in validated[sec].items():
            if isinstance(value, dict):
                cur = out[sec].get(key)
                merged = dict(cur) if isinstance(cur, dict) else {}
                merged.update(value)
                out[sec][key] = merged
            else:
                out[sec][key] = value
    return out


def _preview_model_value(payload: dict[str, Any], cfg: Config) -> str:
    """校验并归一化预览「模型」入参：手填任意 id 也接受（只限长度/控制字符）.

    缺省（未传/空串）→ 已配置模型 → 兜底样本，与下拉默认项同一口径。
    """
    if "model" not in payload:
        return _preview_default_model(cfg) or _PREVIEW_FALLBACK_MODEL
    raw = payload["model"]
    if not isinstance(raw, str):
        raise ValueError("model 必须是字符串")
    value = raw.strip()
    if not value:
        return _preview_default_model(cfg) or _PREVIEW_FALLBACK_MODEL
    if len(value) > _PREVIEW_MODEL_MAX_LEN or any(ord(char) < 32 for char in value):
        raise ValueError(f"model 必须是 1~{_PREVIEW_MODEL_MAX_LEN} 字符、不含控制字符的字符串")
    return value


def build_preview(payload: Any, home: Path | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是对象")
    unknown = set(payload) - {"scenario", "state", "outcome", "model", "overrides"}
    if unknown:
        raise ValueError(f"未知字段: {', '.join(sorted(unknown))}")

    scenario = payload.get("scenario", "workflow")
    if scenario not in _SCENARIOS:
        raise ValueError(f"scenario 必须是 {'/'.join(_SCENARIOS)} 之一")
    state = payload.get("state", "completed")
    if state not in _STATES:
        raise ValueError(f"state 必须是 {'/'.join(_STATES)} 之一")
    outcome = payload.get("outcome", "completed")
    if outcome not in _OUTCOMES:
        raise ValueError(f"outcome 必须是 {'/'.join(_OUTCOMES)} 之一")

    home = home or hermes_home()
    base = collect_state(home)
    model = _preview_model_value(payload, Config(home=home))
    vals = _apply_overrides(
        {"streaming": base["streaming"], "display": base["display"]},
        payload.get("overrides"),
    )
    sv, dv = vals["streaming"], vals["display"]
    sv_header = sv.get("header") or {}
    sv_footer = sv.get("footer") or {}
    sv_body = sv.get("body") or {}

    segments, steps, footer_data, elapsed_ms = _scenario_data(scenario, model)
    # 厂商 logo：口径同线上封卡（display.vendor_logo 开启且命中家族才带 key），
    # Studio 用本地静态 PNG 路径替代飞书 img_key（见 _preview_logo_key）
    footer_data.pop("logo_image_key", None)
    if dv.get("vendor_logo", True):
        _logo_key = _preview_logo_key(model)
        if _logo_key:
            footer_data["logo_image_key"] = _logo_key

    from ..cardkit.builder import build_complete_card, build_streaming_card_v2

    common = {
        "show_tool_use": dv.get("show_tool_use", True),
        "header_enabled": sv_header.get("enabled", True),
        "body_text_size": sv_body.get("text_size", "normal_v2"),
        "width_mode": sv.get("width_mode", "default"),
    }
    if state == "streaming":
        card = build_streaming_card_v2(
            tool_steps=(steps or None) if common["show_tool_use"] else None,
            elapsed_ms=elapsed_ms,
            show_tool_use=common["show_tool_use"],
            show_reasoning=dv.get("show_reasoning", False),
            show_streaming_element=True,
            header_enabled=common["header_enabled"],
            text_size=common["body_text_size"],
            width_mode=common["width_mode"],
        )
    else:
        card = build_complete_card(
            segments=segments,
            all_tool_steps=steps,
            footer_data=footer_data,
            is_error=outcome == "error",
            is_aborted=outcome == "aborted",
            footer_fields=sv_footer.get("fields"),
            footer_show_label=bool(sv_footer.get("show_label", False)),
            footer_enabled=sv_footer.get("enabled", True),
            footer_text_size=sv_footer.get("text_size", "notation"),
            panel_fields=dv.get("panel_fields"),
            panel_expanded=bool(sv.get("panel_expanded", False)),
            panel_text_size=str(dv.get("panel_text_size") or "notation"),
            header_enabled=common["header_enabled"],
            body_text_size=common["body_text_size"],
            show_tool_use=common["show_tool_use"],
            width_mode=common["width_mode"],
        )
    return {
        "ok": True,
        "card": card,
        "meta": {"scenario": scenario, "state": state, "outcome": outcome, "model": model},
    }


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------


def _host_ok(handler: BaseHTTPRequestHandler) -> bool:
    host = (handler.headers.get("Host") or "").strip().lower()
    if not host:
        return False
    for base in _allowed_hosts():
        base_l = base.lower()
        if host == base_l or host.startswith(base_l + ":"):
            return True
        if base_l != "::1" and host == f"[{base_l}]":
            return True
        if host.startswith(f"[{base_l}]:"):
            return True
        # prefix entries (e.g. "192.168.31.") match any host starting with them
        if base_l.endswith(".") and host.startswith(base_l):
            return True
    return host == "[::1]"


def _display_host(host: str) -> str:
    """0.0.0.0 / :: 是通配地址，不能拿来开浏览器或拼可点 URL — 统一回落 127.0.0.1."""
    return "127.0.0.1" if host in ("0.0.0.0", "::") else host


def run_studio_server(host: str = "0.0.0.0", port: int = 8765, *, open_browser: bool = True) -> int:
    httpd = ThreadingHTTPServer((host, port), StudioHandler)
    httpd.daemon_threads = True
    real_port = httpd.server_address[1]
    # 0.0.0.0 / :: 是通配地址，不能拿来开浏览器：本机一律用 127.0.0.1 拼 URL
    local_host = _display_host(host)
    url = f"http://{local_host}:{real_port}/"
    print(f"🍟 fry-cards Studio — {url}")
    if local_host != host:
        print(f"  LAN  : http://<本机IP>:{real_port}/  （非 loopback 来源需在 config.yaml 配 studio.allowed_hosts）")
    print("  Ctrl+C 停止")
    if open_browser:
        timer = threading.Timer(0.3, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStudio 已停止")
    finally:
        httpd.server_close()
    return 0


class StudioHandler(BaseHTTPRequestHandler):
    server_version = "FryCardsStudio"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        _logger.debug("studio http: " + fmt, *args)

    # -- helpers -----------------------------------------------------------

    def _respond(
        self, code: int, body: bytes, content_type: str, extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def _json(self, code: int, obj: Any, extra: dict[str, str] | None = None) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._respond(code, body, "application/json; charset=utf-8", extra)

    def _err(self, code: int, message: str, extra: dict[str, str] | None = None) -> None:
        self._json(code, {"ok": False, "error": message}, extra)

    def _gate(self) -> bool:
        if _host_ok(self):
            return True
        self._err(403, "forbidden host")
        return False

    def _static(self, rel: str) -> None:
        try:
            target = (_WEB_ROOT / rel).resolve()
            target.relative_to(_WEB_ROOT)
        except (OSError, ValueError):
            self._err(404, "not found")
            return
        self._send_file(target)

    def _vendor_logo(self, name: str) -> None:
        """预览用厂商 logo PNG：只按打包内资产文件名白名单取文件（不接任意路径）."""
        from ..cardkit.vendor_logos import VENDOR_LOGO_RULES

        allowed = {file for _, file in VENDOR_LOGO_RULES}
        if name not in allowed:
            self._err(404, "not found")
            return
        try:
            from importlib import resources

            data = resources.files("hermes_fry_cards").joinpath("assets/vendor-logos", name).read_bytes()
        except Exception:
            self._err(404, "not found")
            return
        self._respond(200, data, "image/png", {"Cache-Control": "no-cache"})

    def _send_file(self, target: Path) -> None:
        if not target.is_file():
            self._err(404, "not found")
            return
        ctype = _CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        try:
            data = target.read_bytes()
        except OSError:
            self._err(404, "not found")
            return
        self._respond(200, data, ctype, {"Cache-Control": "no-cache"})

    # -- GET ---------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._gate():
            return
        path = urlsplit(self.path).path
        try:
            if path in ("/", "/index.html"):
                self._static("index.html")
            elif path.startswith("/css/") or path.startswith("/js/"):
                self._static(path.lstrip("/"))
            elif path.startswith(_VENDOR_LOGO_URL_PREFIX):
                self._vendor_logo(path[len(_VENDOR_LOGO_URL_PREFIX):])
            elif path == "/api/state":
                self._json(200, {"ok": True, **collect_state()})
            elif path == "/api/aliases":
                home = hermes_home()
                raw = read_full_json(home / "model_aliases.json")  # 解析失败 → 409
                entries = [{"key": str(k), "value": v} for k, v in raw.items()]
                self._json(200, {"ok": True, "entries": entries})
            else:
                self._err(404, "not found")
        except ConfigReadError as e:
            self._err(409, str(e))
        except Exception as e:
            _logger.exception("studio GET failed")
            self._err(500, f"internal error: {e}")

    # -- POST --------------------------------------------------------------

    def _read_json_body(self) -> Any:
        """读取并解析 JSON body。出错时已自行响应并返回 _RESPONDED 哨兵。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self._err(400, "bad content-length")
            return _RESPONDED
        if length > _MAX_BODY_BYTES:
            self.close_connection = True
            self._err(413, "body too large", extra={"Connection": "close"})
            # 有界排空请求体再收连接：带未读数据关闭会在客户端写端触发 RST
            #（Windows 下 urllib 直接 ConnectionAbortedError，413 响应读不到）。
            try:
                self.connection.settimeout(5.0)
                self.rfile.read(min(length, _MAX_DRAIN_BYTES))
            except (OSError, ValueError):
                pass
            return _RESPONDED
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._err(400, "body must be UTF-8 JSON")
            return _RESPONDED

    def do_POST(self) -> None:
        if not self._gate():
            return
        path = urlsplit(self.path).path
        payload = self._read_json_body()
        if payload is _RESPONDED:
            return
        try:
            if path == "/api/config":
                self._handle_config(payload)
            elif path == "/api/preview":
                self._handle_preview(payload)
            elif path == "/api/aliases":
                self._handle_aliases(payload)
            elif path == "/api/restart":
                self._handle_restart(payload)
            elif path == "/api/tools":
                self._handle_tools(payload)
            else:
                self._err(404, "not found")
        except ConfigReadError as e:
            self._err(409, str(e))
        except ValueError as e:
            self._err(400, str(e))
        except Exception as e:
            _logger.exception("studio POST failed")
            self._err(500, f"internal error: {e}")

    def _handle_config(self, payload: Any) -> None:
        validated = validate_payload(payload)
        home = hermes_home()
        conf_path = home / "config.yaml"
        base = read_full_config(conf_path)
        merged, changed = merge_managed(base, validated)
        backup_config(conf_path)
        write_atomic(conf_path, merged)
        _logger.info("studio config written: %s", ", ".join(changed) or "(no change)")
        self._json(200, {"ok": True, "changed": changed})

    def _handle_aliases(self, payload: Any) -> None:
        validated = validate_alias_entries(payload)
        home = hermes_home()
        path = home / "model_aliases.json"
        read_full_json(path)  # 解析失败 → ConfigReadError(409) 拒写
        backup_config(path)
        data = {k: v for k, v in validated}
        tmp = path.parent / (path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _logger.info("studio aliases written: %d entries", len(data))
        self._json(200, {"ok": True, "count": len(data), "keys": list(data)})

    def _handle_preview(self, payload: Any) -> None:
        self._json(200, build_preview(payload))

    def _handle_tools(self, payload: Any) -> None:
        """POST /api/tools — 摘要/翻译一次小模型调用（凭据只在服务端，不回传）.

        端点侧失败（无凭据、超时、返回异常）统一 200 + ``ok:false`` + 人话 error：
        这类失败对用户是「工具没跑成」，不是 Studio 出错，不值得 5xx；
        error 文本已由 llm 层掩掉 api_key 并截断。入参非法仍是 400（do_POST 统一处理）。
        """
        try:
            self._json(200, run_tool(payload, Config(home=hermes_home())))
        except LLMError as e:
            _logger.info("studio tools call failed: %s", e)
            self._json(200, {"ok": False, "error": str(e)})

    def _handle_restart(self, payload: Any) -> None:
        cli = shutil.which("hermes")
        if not cli:
            self._json(200, {"ok": False, "error": "未找到 hermes CLI（PATH 中不存在）"})
            return
        try:
            proc = subprocess.run(
                [cli, "gateway", "restart"], capture_output=True, text=True, timeout=60, check=False,
            )
        except (subprocess.SubprocessError, OSError) as e:
            self._json(200, {"ok": False, "error": f"重启失败: {e}"})
            return
        tail = (proc.stdout + proc.stderr)[-2000:]
        self._json(200, {"ok": proc.returncode == 0, "output": tail})
