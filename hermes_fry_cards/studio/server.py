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

if TYPE_CHECKING:
    from ..streaming.segments import Segment
    from ..streaming.tooluse import ToolBlock, ToolDisplayStep

_logger = logging.getLogger("hermes_fry_cards")

_WEB_ROOT = (Path(__file__).parent / "web").resolve()
_MAX_BODY_BYTES = 1_000_000
_MAX_DRAIN_BYTES = 4_000_000  # 413 时有界排空上限（防未读数据触发客户端写端 RST）
_BACKUP_KEEP = 20
_HOSTS_OK = ("127.0.0.1", "localhost", "::1")

_FOOTER_FIELDS = ("status", "elapsed", "model", "tokens", "context")
_WIDTH_MODES = ("default", "compact", "fill")
_CONTEXT_MODES = ("text", "bar", "text_bar", "block", "block_text")
_LANGS = ("zh", "en")

_SCENARIOS = ("short", "workflow", "tables", "longtext")
_OUTCOMES = ("completed", "error", "aborted")
_STATES = ("streaming", "completed")

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


_STREAMING_SCALARS: dict[str, str] = {
    "enabled": "bool",
    "content_lang": "lang",
    "chat_types": "chat_types",
    "panel_expanded": "bool",
    "width_mode": "width",
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
    "model_aliases_enabled": "bool",
    "max_reasoning_panels": "panels",
    "unified_panel_min_duration": "dur600",
    "context_display_mode": "context",
}


def _validate_scalar(v: Any, name: str, kind: str) -> Any:
    if kind == "bool":
        return _expect_bool(v, name)
    if kind == "lang":
        return _expect_choice(v, name, _LANGS)
    if kind == "width":
        return _expect_choice(v, name, _WIDTH_MODES)
    if kind == "context":
        return _expect_choice(v, name, _CONTEXT_MODES)
    if kind == "chat_types":
        return _validate_chat_types(v, name)
    if kind == "fields":
        return _validate_fields(v, name)
    if kind == "textsize":
        return _expect_token_str(v, name)
    if kind == "dur":
        return _expect_range(v, name, 0, 86400)
    if kind == "dur600":
        return _expect_range(v, name, 0, 600)
    if kind == "panels":
        _expect_range(v, name, 1, 50)
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
    unknown = set(payload) - {"streaming", "display"}
    if unknown:
        raise ValueError(f"未知顶层字段: {', '.join(sorted(unknown))}")

    streaming_spec: dict[str, Any] = dict(_STREAMING_SCALARS)
    streaming_spec.update({"header": _HEADER_KEYS, "footer": _FOOTER_KEYS, "body": _BODY_KEYS})

    result: dict[str, Any] = {}
    streaming = _validate_section(payload, "streaming", streaming_spec)
    if streaming:
        result["streaming"] = streaming
    display = _validate_section(payload, "display", _DISPLAY_KEYS)
    if display:
        result["display"] = display
    if not result:
        raise ValueError("payload 至少要包含 streaming 或 display 段")
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


def _collect_status(home: Path) -> dict[str, Any]:
    st: dict[str, Any] = {
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
        "width_mode": cfg.width_mode,
        "header": {"enabled": cfg.header_enabled, "min_duration": cfg.header_min_duration},
        "footer": {
            "enabled": cfg.footer_enabled,
            "show_label": cfg.footer_show_label,
            "fields": cfg.footer_fields,
            "text_size": cfg.footer_text_size,
        },
        "body": {"text_size": cfg.body_text_size},
    }
    display = {
        "show_reasoning": cfg.show_reasoning,
        "show_tool_use": cfg.show_tool_use,
        "show_context": cfg.show_context,
        "truncate_model_name": cfg.truncate_model_name,
        "model_aliases_enabled": cfg.model_aliases_enabled,
        "max_reasoning_panels": cfg.max_reasoning_panels,
        "unified_panel_min_duration": cfg.unified_panel_min_duration,
        "context_display_mode": cfg.context_display_mode,
    }
    return {"streaming": streaming, "display": display, "status": _collect_status(home)}


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


def _scenario_data(scenario: str) -> tuple[list[Segment], list[ToolDisplayStep], dict[str, Any], float]:
    """返回 (segments, all_tool_steps, footer_data, streaming_elapsed_ms)."""
    base_footer = {
        "model": "mimo/mimo-x-flash",
        "input_tokens": 15420,
        "output_tokens": 986,
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


def build_preview(payload: Any, home: Path | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是对象")
    unknown = set(payload) - {"scenario", "state", "outcome", "overrides"}
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
    vals = _apply_overrides(
        {"streaming": base["streaming"], "display": base["display"]},
        payload.get("overrides"),
    )
    sv, dv = vals["streaming"], vals["display"]
    sv_header = sv.get("header") or {}
    sv_footer = sv.get("footer") or {}
    sv_body = sv.get("body") or {}

    segments, steps, footer_data, elapsed_ms = _scenario_data(scenario)

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
            panel_expanded=bool(sv.get("panel_expanded", False)),
            header_enabled=common["header_enabled"],
            body_text_size=common["body_text_size"],
            show_tool_use=common["show_tool_use"],
            width_mode=common["width_mode"],
        )
    return {"ok": True, "card": card, "meta": {"scenario": scenario, "state": state, "outcome": outcome}}


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------


def _host_ok(handler: BaseHTTPRequestHandler) -> bool:
    host = (handler.headers.get("Host") or "").strip().lower()
    if not host:
        return False
    for base in _HOSTS_OK:
        if host == base or host.startswith(base + ":"):
            return True
        if base != "::1" and host == f"[{base}]":
            return True
        if host.startswith(f"[{base}]:"):
            return True
    return host == "[::1]"


def run_studio_server(host: str = "127.0.0.1", port: int = 8765, *, open_browser: bool = True) -> int:
    httpd = ThreadingHTTPServer((host, port), StudioHandler)
    httpd.daemon_threads = True
    real_port = httpd.server_address[1]
    url = f"http://{host}:{real_port}/"
    print(f"🍟 fry-cards Studio — {url}")
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
