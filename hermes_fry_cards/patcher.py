"""AST Patcher — 在 Hermes gateway/run.py 中注入 Hook 调用."""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from .config import hermes_home

_logger = logging.getLogger("hermes_fry_cards")


PREFIX = "HERMES_LARK"

_HOOK_NAMES = [
    "NORMALIZE",
    "START",
    "COMPLETE",
    "FOLLOWUP_COMPLETE",
    "FOLLOWUP_RESULT",
    "FOLLOWUP_DONE",
    "TOOL",
    "ANSWER",
    "THINKING",
    "REASONING",
    "BACKGROUND_REVIEW",
    "ABORT",
    "STOP",
    "INTERRUPT",
    "BG_DELIVER",
    "CLARIFY",
    "BUSY_ACK",
    "REDIRECT",
    "COMPRESSION",
    "REDIRECT_RESOLVED",
    "BG_COMPLETION",
    "BG_BUSY_SUPPRESS",
]
MARKERS: list[tuple[str, str]] = [(f"# {PREFIX}_{n}_BEGIN", f"# {PREFIX}_{n}_END") for n in _HOOK_NAMES]

MK_NORMALIZE, MK_NORMALIZE_END = MARKERS[0]
MK_START, MK_START_END = MARKERS[1]
MK_COMPLETE, MK_COMPLETE_END = MARKERS[2]
MK_FOLLOWUP_COMPLETE, MK_FOLLOWUP_COMPLETE_END = MARKERS[3]
MK_FOLLOWUP_RESULT, MK_FOLLOWUP_RESULT_END = MARKERS[4]
MK_FOLLOWUP_DONE, MK_FOLLOWUP_DONE_END = MARKERS[5]
MK_TOOL, MK_TOOL_END = MARKERS[6]
MK_ANSWER, MK_ANSWER_END = MARKERS[7]
MK_THINKING, MK_THINKING_END = MARKERS[8]
MK_REASONING, MK_REASONING_END = MARKERS[9]
MK_BACKGROUND_REVIEW, MK_BACKGROUND_REVIEW_END = MARKERS[10]
MK_ABORT, MK_ABORT_END = MARKERS[11]
MK_STOP, MK_STOP_END = MARKERS[12]
MK_INTERRUPT, MK_INTERRUPT_END = MARKERS[13]
MK_BG_DELIVER, MK_BG_DELIVER_END = MARKERS[14]
MK_CLARIFY, MK_CLARIFY_END = MARKERS[15]
MK_BUSY_ACK, MK_BUSY_ACK_END = MARKERS[16]
MK_REDIRECT, MK_REDIRECT_END = MARKERS[17]
MK_COMPRESSION, MK_COMPRESSION_END = MARKERS[18]
MK_REDIRECT_RESOLVED, MK_REDIRECT_RESOLVED_END = MARKERS[19]
MK_BG_COMPLETION, MK_BG_COMPLETION_END = MARKERS[20]
MK_BG_BUSY_SUPPRESS, MK_BG_BUSY_SUPPRESS_END = MARKERS[21]

# 可选注入点：锚点不存在时跳过而不让整次安装失败（#43 提示入卡与压缩封卡）。
# 旧版 Hermes 或精简布局可能缺失部分回调，缺失时插件退回既有行为。
OPTIONAL_HOOKS: frozenset[str] = frozenset(
    {"busy_ack", "redirect", "compression", "redirect_resolved", "bg_completion", "bg_busy_suppress", "tool_gating"}
)
_OPTIONAL_MARKERS: frozenset[str] = frozenset(
    f"# {PREFIX}_{name.upper()}_BEGIN" for name in OPTIONAL_HOOKS
)

# Group-security boundary is MODULAR-ONLY (targets ``_combined_ephemeral_prompt`` in
# gateway/run_turn_runner.py, which exists only in the Hermes 0.21+ modular layout). Kept out of
# the shared MARKERS list so the legacy monolithic apply/verify paths (which never host it) and
# their tests are unaffected.
MK_GROUP_BOUNDARY = f"# {PREFIX}_GROUP_BOUNDARY_BEGIN"
MK_GROUP_BOUNDARY_END = f"# {PREFIX}_GROUP_BOUNDARY_END"

# Group tool gating (#55) is MODULAR-ONLY the same way: it targets
# ``_resolve_turn_toolsets`` in gateway/run_turn.py. OPTIONAL (like busy_ack): older
# modular layouts without that method skip injection instead of failing install.
MK_TOOL_GATING = f"# {PREFIX}_TOOL_GATING_BEGIN"
MK_TOOL_GATING_END = f"# {PREFIX}_TOOL_GATING_END"

_BACKUP_SUFFIX = ".hermes_lark.bak"


def _valid_source(path: Path) -> Path | None:
    try:
        candidate = path.resolve()
        if candidate.is_file() and candidate.suffix == ".py":
            return candidate
    except (OSError, RuntimeError):
        _logger.debug("Invalid Hermes source candidate: %s", path, exc_info=True)
    return None


def _module_to_path(module_name: str) -> Path:
    """gateway.run → gateway/run.py."""
    return Path(*module_name.split(".")).with_suffix(".py")


# 候选代码根目录，按优先级排列（来源: Hermes 官方安装文档 Install Layout）。
# - per-user git installer: <HERMES_HOME>/hermes-agent/
# - root-mode (sudo curl|bash): /usr/local/lib/hermes-agent/
# - 官方 Docker 镜像等非常规布局: 从已安装 `gateway` 包位置反推（如 /opt/hermes）
def _code_roots() -> list[Path]:
    roots = [hermes_home() / "hermes-agent", Path("/usr/local/lib/hermes-agent")]
    try:
        spec = importlib.util.find_spec("gateway")
        for location in (spec.submodule_search_locations or []) if spec else []:
            root = Path(location).parent
            if root not in roots:
                roots.append(root)
    except Exception:
        _logger.debug("gateway root discovery failed", exc_info=True)
    return roots


# venv 内 python 解释器候选（覆盖 venv/.venv 命名变体）。
_VENV_PYTHONS: tuple[tuple[str, ...], ...] = (
    ("venv", "bin", "python3"),
    ("venv", "bin", "python"),
    (".venv", "bin", "python3"),
    (".venv", "bin", "python"),
)


def _python_from_hermes_cli() -> Path | None:
    """从 which hermes 反推 venv 内的 python3。"""
    cli = shutil.which("hermes")
    if cli is None:
        return None
    cli_path = Path(cli)
    # 读脚本内容，依次试 exec 行(bash wrapper) 和 shebang(console_scripts)
    try:
        text = cli_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    # 1. bash wrapper: exec "venv/bin/hermes" → 同目录 python3
    m = re.search(r'''exec\s+["']([^"']+)["']''', text)
    if m:
        venv_bin = Path(m.group(1)).parent  # venv/bin
        for name in ("python3", "python"):
            py = venv_bin / name
            if py.exists():
                return py
    # 2. console_scripts: shebang #!/path/to/python3 直接指向 python
    m = re.match(r'^#!\s*(\S+)', text)
    if m:
        py = Path(m.group(1))
        if py.exists() and "python" in py.name.lower():
            return py
    return None


def hermes_python() -> Path | None:
    """定位 Hermes 的 Python: which hermes 优先, _code_roots 兜底."""
    # 1. which hermes (覆盖所有官方安装方式，跨平台)
    if py := _python_from_hermes_cli():
        return py
    # 2. 兜底: 已知代码根下的 venv
    for root in _code_roots():
        for parts in _VENV_PYTHONS:
            py = root.joinpath(*parts)
            if py.exists():
                return py
    return None


def hermes_install_dir() -> Path | None:
    """定位 Hermes 安装目录 (含 gateway/run.py): hermes_constants 优先, _code_roots 兜底."""
    # 1. 用 Hermes Python 调用官方 API (single source of truth)
    py = hermes_python()
    if py is not None:
        try:
            result = subprocess.run(
                [str(py), "-c", "from hermes_constants import get_hermes_home; print(get_hermes_home())"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            _logger.debug("hermes_constants lookup failed", exc_info=True)
        else:
            if result.returncode == 0:
                home = Path(result.stdout.strip())
                install = home / "hermes-agent"
                if install.exists():
                    return install
    # 2. 兜底: _code_roots 里含 gateway/run.py 的那个
    rel = _module_to_path("gateway.run")
    for root in _code_roots():
        if (root / rel).exists():
            return root
    return None


def _resolve_module_path(module_name: str, roots: list[Path]) -> Path:
    """定位 Hermes 模块文件，候选代码根优先，importlib 兜底."""
    rel = _module_to_path(module_name)
    for root in roots:
        if candidate := _valid_source(root / rel):
            return candidate

    package = module_name.partition(".")[0]
    try:
        spec = importlib.util.find_spec(package)
        locations = spec.submodule_search_locations if spec else None
        # submodule_search_locations 指向包目录（如 .../gateway），
        # 因此需剥掉包名前缀，得到包内子路径。
        in_pkg = rel.relative_to(Path(package)) if rel.parts[0] == package else rel
        for location in locations or []:
            if candidate := _valid_source(Path(location) / in_pkg):
                return candidate
    except Exception:
        _logger.debug("Failed to resolve Hermes module %s", module_name, exc_info=True)
    return roots[0] / rel if roots else rel


def _default_run_path() -> Path:
    return _resolve_module_path("gateway.run", _code_roots())


def _default_modular_paths() -> dict[str, Path]:
    """Hermes 0.21+ split the former gateway/run.py implementation into mixins.

    0.22 微模块重构进一步拆分出 ``run_turn_progress``（``progress_callback`` / tool 锚点）
    与 ``run_turn_persistence``（``hooks.emit("agent:end")`` 锚点）。这两个模块按存在与否
    动态注册，缺失时对应锚点回退到 ``runner`` / ``turn``，保持对老版本的向后兼容。
    """
    roots = _code_roots()
    paths = {
        "inbound": _resolve_module_path("gateway.run_inbound", roots),
        "turn": _resolve_module_path("gateway.run_turn", roots),
        "runner": _resolve_module_path("gateway.run_turn_runner", roots),
        "busy": _resolve_module_path("gateway.run_busy", roots),
    }
    for optional in ("progress", "persistence"):
        resolved = _resolve_module_path(f"gateway.run_turn_{optional}", roots)
        if resolved.exists():
            paths[optional] = resolved
    notifications = _resolve_module_path("gateway.run_notifications", roots)
    if notifications.exists():
        # 0.22+ 的 watch/completion 通知宿主（#47 可选注入点 bg_completion）
        paths["notifications"] = notifications
    return paths


def _default_cron_path() -> Path:
    roots = _code_roots()
    legacy = _resolve_module_path("cron.scheduler", roots)
    if legacy.exists() and "delivered = False" in legacy.read_text(encoding="utf-8"):
        return legacy
    # Prefer a modular source in the selected Hermes roots.  Do not let
    # importlib escape an explicit HERMES_HOME and select another install.
    for root in roots:
        modular = _valid_source(root / _module_to_path("cron.scheduler_delivery"))
        if modular is not None:
            return modular
    return legacy


MK_CRON_DELIVER = f"# {PREFIX}_CRON_DELIVER_BEGIN"
MK_CRON_DELIVER_END = f"# {PREFIX}_CRON_DELIVER_END"

_ANCHOR_CHECKS: list[tuple[str, tuple[str, ...], str]] = [
    ("Restart typing indicator so the user sees activity", (), "interrupt"),
    ('was_interrupted = result.get("interrupted")', (), "queued follow-up boundary"),
    (
        "return _preserve_queued_followup_history_offset(result, followup_result)",
        (
            # Hermes 0.21.1+: return value assigned then augmented with queued_terminal_inbound_id
            "= _preserve_queued_followup_history_offset(result, followup_result)",
        ),
        "queued follow-up return",
    ),
    ("agent.reasoning_config = reasoning_config", (), "reasoning_config"),
    ("agent.background_review_callback = _bg_review_send", (), "background_review_callback"),
    ("images, text_content = adapter.extract_images(response)", (), "background deliver"),
    ("_already_sent = bool(", (), "complete"),
    ("Discarding stale agent result", (), "abort"),
    ("agent.clarify_callback = _clarify_callback_sync", (), "clarify_callback"),
    ("await adapter._send_with_retry(", (), "busy ack"),
]



def _busy_ack_hook(indent: str) -> str:
    """忙碌回执入卡：把 Hermes 合成的那条文案并进当前卡，并吞掉纯文本（#43）."""
    return _make_hook(
        indent,
        MK_BUSY_ACK,
        MK_BUSY_ACK_END,
        [
            "try:",
            "    if event.source.platform.value.lower() in ('feishu', 'lark'):",
            "        from hermes_fry_cards.patch import on_busy_ack",
            "        if on_busy_ack(",
            "            message_id=event.message_id,",
            "            session_key=session_key,",
            "            text=message,",
            "        ):",
            "            return True",
            *_hook_exception_lines("busy_ack"),
        ],
    )


def _redirect_hook(indent: str) -> str:
    """忙碌提示入卡：重定向时旧卡收尾、新卡接手，其余模式并入当前卡（#43）."""
    return _make_hook(
        indent,
        MK_REDIRECT,
        MK_REDIRECT_END,
        [
            "try:",
            "    if event.source.platform.value.lower() in ('feishu', 'lark'):",
            "        from hermes_fry_cards.patch import on_turn_redirected",
            "        on_turn_redirected(",
            "            new_message_id=event.message_id,",
            "            chat_id=event.source.chat_id,",
            "            chat_type=getattr(event.source, 'chat_type', '') or '',",
            "            session_key=session_key,",
            "            anchor_id=self._reply_anchor_for_event(event),",
            "            redirected=('redirected' in locals() and bool(redirected)),",
            "            text=message,",
            "        )",
            *_hook_exception_lines("redirect"),
        ],
    )


def _redirect_resolved_hook(indent: str) -> str:
    """重定向判定点换卡：赶在忙碌回执 30s 防抖之前（#43 反馈）.

    连续第二次打断时 Hermes 的回执被防抖吞掉（``busy_ack_ts`` 30s 内直接
    return），REDIRECT 锚点（回执发送前）永远走不到，旧卡停在半截、新消息
    没有卡。这个锚点挂在 ``_steer`` 解包之后、防抖之前：重定向判定一成立就
    换卡（旧卡收尾、新卡接手），与回执锚点幂等（都走 ``on_interrupted``，
    旧卡已终态/新卡已存在时均为 no-op）。非重定向模式（steer/queue）传空
    文案，插件侧 no-op。
    """
    return _make_hook(
        indent,
        MK_REDIRECT_RESOLVED,
        MK_REDIRECT_RESOLVED_END,
        [
            "try:",
            "    if event.source.platform.value.lower() in ('feishu', 'lark'):",
            "        from hermes_fry_cards.patch import on_turn_redirected",
            "        on_turn_redirected(",
            "            new_message_id=event.message_id,",
            "            chat_id=event.source.chat_id,",
            "            chat_type=getattr(event.source, 'chat_type', '') or '',",
            "            session_key=session_key,",
            "            anchor_id=self._reply_anchor_for_event(event),",
            "            redirected=bool(redirected),",
            "            text='',",
            "        )",
            *_hook_exception_lines("redirect_resolved"),
        ],
    )


def _bg_completion_hook(indent: str) -> str:
    """后台完成通知接管：活跃卡并入通知区，无卡走静态卡（#47）.

    ``_inject_watch_notification`` 把完成通知合成内部消息注入——纯文本气泡
    直达，不经过卡片管线（无 START 钩子可拦）。这个锚点挂在内部标记处理
    之前：fry 按会话活跃卡并入通知区，或独立静态卡直接投聊天；返回 True
    时注入点 ``return True`` 跳过 Hermes 注入（外层语义同「adapter 已接受」，
    不会重试/重排队）。False 时 Hermes 照常注入，无损降级。
    """
    return _make_hook(
        indent,
        MK_BG_COMPLETION,
        MK_BG_COMPLETION_END,
        [
            "try:",
            "    if platform_name in ('feishu', 'lark'):",
            "        from hermes_fry_cards.patch import on_background_completion",
            "        _lark_evt = evt if isinstance(evt, dict) else {}",
            "        _lark_evt_key = _lark_evt.get('session_key') if _lark_evt else None",
            "        if await on_background_completion(",
            "            chat_id=str(getattr(source, 'chat_id', '') or ''),",
            "            session_key=(str(_lark_evt_key) if _lark_evt_key else None),",
            "            text=synth_text,",
            "            command=_lark_evt.get('command'),",
            "            started_at=_lark_evt.get('started_at'),",
            "            exit_code=_lark_evt.get('exit_code'),",
            "            session_id=str(_lark_evt.get('session_id') or ''),",
            "        ):",
            "            return True",
            *_hook_exception_lines("bg_completion"),
        ],
    )


def _bg_busy_suppress_hook(indent: str) -> str:
    """回执已并入卡片时，跳过 turn_busy 分支的纯文本回执（#issue 卡片外多一条）.

    发起轮还在跑时 ``_run_process_watcher`` 在 ``_send_watcher_message`` 前补发一条
    concise 纯文本；插件已经把同一条回执并进那张活跃卡，这条纯文本就是卡外多出来的
    那一条。``suppress_busy_receipt`` 命中即 ``break``（该分支发送后本就 break，等价
    跳过本条发送）；拿不到键时返回 False，网关照常发，功能无损。

    注入块只用 ``_run_process_watcher`` 里已有的变量：``session_id`` / ``session``
    （取 ``started_at``）/ ``platform_name`` / ``chat_id`` / ``completion_evt``。
    """
    return _make_hook(
        indent,
        MK_BG_BUSY_SUPPRESS,
        MK_BG_BUSY_SUPPRESS_END,
        [
            "try:",
            "    if platform_name in ('feishu', 'lark'):",
            "        from hermes_fry_cards.patch import suppress_busy_receipt",
            "        _lark_busy_evt = completion_evt if isinstance(completion_evt, dict) else {}",
            "        if suppress_busy_receipt(",
            "            session_id=str(session_id or ''),",
            "            session_key=str(_lark_busy_evt.get('session_key') or ''),",
            "            started_at=getattr(session, 'started_at', None),",
            "            platform_name=platform_name,",
            "            chat_id=str(chat_id or ''),",
            "        ):",
            "            break",
            *_hook_exception_lines("bg_busy_suppress"),
        ],
    )


def _compression_hook(indent: str) -> str:
    """会话触发上下文压缩时封卡（#43 增强）."""
    return _make_hook(
        indent,
        MK_COMPRESSION,
        MK_COMPRESSION_END,
        [
            "try:",
            "    _lark_src = locals().get('source') or getattr(locals().get('ctx'), 'source', None)",
            "    _lark_sk = locals().get('session_key') or getattr(locals().get('ctx'), 'session_key', None)",
            "    _lark_plat = getattr(getattr(_lark_src, 'platform', None), 'value', '').lower() if _lark_src else ''",
            "    if _lark_plat in ('feishu', 'lark'):",
            "        from hermes_fry_cards.patch import on_session_compressed",
            "        on_session_compressed(session_key=_lark_sk)",
            *_hook_exception_lines("compression"),
        ],
    )


def _make_hook(indent: str, begin: str, end: str, body_lines: list[str]) -> str:
    return f"{indent}{begin}\n" + "".join(f"{indent}{line}\n" for line in body_lines) + f"{indent}{end}\n"


def _hook_exception_lines(hook_name: str, indent: str = "") -> list[str]:
    return [
        f"{indent}except Exception:",
        f"{indent}    import logging as _lark_logging",
        f'{indent}    _lark_logging.getLogger("hermes_fry_cards").exception('
        f'"injected hook failed: {hook_name}")',
    ]


def _feishu_normalize_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_NORMALIZE,
        MK_NORMALIZE_END,
        [
            "try:",
            "    from hermes_fry_cards.patch import on_feishu_normalize",
            "    on_feishu_normalize(",
            "        message_id=event.message_id,",
            "        source=source,",
            "        event=event,",
            "        reply_anchor_id=self._reply_anchor_for_event(event),",
            "    )",
            *_hook_exception_lines("normalize"),
        ],
    )


def _start_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_START,
        MK_START_END,
        [
            "try:",
            "    if source.platform.value.lower() in ('feishu', 'lark'):",
            "        from hermes_fry_cards.patch import on_message_started",
            "        _lark_anchor_id = self._reply_anchor_for_event(event)",
            "        on_message_started(",
            "            message_id=event.message_id,",
            "            chat_id=source.chat_id,",
            "            chat_type=getattr(source, 'chat_type', ''),",
            "            anchor_id=_lark_anchor_id,",
            "            session_key=locals().get('session_key') or locals().get('_quick_key'),",
            "        )",
            *_hook_exception_lines("start"),
        ],
    )


def _complete_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_COMPLETE,
        MK_COMPLETE_END,
        [
            "try:",
            "    from hermes_fry_cards.patch import (",
            "        on_message_completed_wait, on_message_needs_text_fallback, collect_stream_stats,",
            "    )",
            "    _lark_completion_id = agent_result.get('_hermes_lark_completion_id') or event.message_id",
            "    _lark_answer_rewritten = any(",
            "        str(_m.get('finish_reason') or '') == 'verify_hook_continue'",
            "        for _m in (locals().get('agent_messages') or []) if isinstance(_m, dict)",
            "    )",
            "    _lark_card_sent = await on_message_completed_wait(",
            "        message_id=_lark_completion_id,",
            "        answer=response,",
            "        is_error=bool(agent_result.get('failed')),",
            "        answer_rewritten=_lark_answer_rewritten,",
            "        duration=locals().get('_response_time', locals().get('_turn_seconds')),",
            "        model=agent_result.get('model', ''),",
            "        session_key=locals().get('session_key') or locals().get('_quick_key'),",
            "        tokens={",
            "            'input_tokens': agent_result.get('input_tokens', 0),",
            "            'output_tokens': agent_result.get('output_tokens', 0),",
            "            **collect_stream_stats(",
            "                locals().get('self'),",
            "                locals().get('session_key') or locals().get('_quick_key'),",
            "            ),",
            "        },",
            "        context={",
            "            'used_tokens': agent_result.get('last_prompt_tokens', 0),",
            "            'max_tokens': agent_result.get('context_length', 0),",
            "        },",
            "    )",
            "    if _lark_card_sent:",
            "        agent_result['already_sent'] = True",
            "        _footer_line = ''",
            "        if agent_result.get('failed'):",
            "            response = ''",
            "    elif on_message_needs_text_fallback(message_id=_lark_completion_id):",
            "        agent_result.pop('already_sent', None)",
            *_hook_exception_lines("complete"),
        ],
    )


def _followup_complete_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_FOLLOWUP_COMPLETE,
        MK_FOLLOWUP_COMPLETE_END,
        [
            "try:",
            "    from hermes_fry_cards.patch import on_queued_followup_boundary, collect_stream_stats",
            "    _lark_delivery_result = response if isinstance(response, dict) else result",
            "    _lark_event_message_id = locals().get('event_message_id')",
            "    if not _lark_event_message_id and locals().get('turn_ctx') is not None:",
            "        _lark_event_message_id = turn_ctx.event_message_id",
            "    _lark_followup_sent = await on_queued_followup_boundary(",
            "        message_id=_lark_event_message_id, result=_lark_delivery_result,",
            "        session_key=locals().get('session_key'),",
            "        stream_stats=collect_stream_stats(locals().get('self'), locals().get('session_key')),",
            "    )",
            "    if _lark_followup_sent and _lark_delivery_result is not result:",
            "        result['response_previewed'] = True",
            "        result['already_sent'] = True",
            "        result['final_response'] = ''",
            # 续轮开始前先把这一轮的卡建好：没有卡时 Hermes 会把续轮的回复按纯文本
            # 流式投出去，等续轮结束再补卡就重复了（#37）。预建失败只降级为原行为。
            # session_key 用与核心 next_session_key 同源的表达式，流式与收尾落在同一张卡。
            "    try:",
            "        from hermes_fry_cards.patch import on_queued_followup_card_ready",
            "        _lark_next_source = getattr(pending_event, 'source', None) or source",
            "        _lark_next_platform = str(",
            "            getattr(getattr(_lark_next_source, 'platform', None), 'value', '') or ''",
            "        ).lower()",
            "        if _lark_next_platform in ('feishu', 'lark'):",
            "            _lark_next_sk = locals().get('session_key')",
            "            try:",
            "                _lark_next_sk = self._session_key_for_source(_lark_next_source) or _lark_next_sk",
            "            except Exception:",
            "                pass",
            "            on_queued_followup_card_ready(",
            "                message_id=getattr(pending_event, 'message_id', None),",
            "                chat_id=getattr(_lark_next_source, 'chat_id', '') or '',",
            "                chat_type=getattr(_lark_next_source, 'chat_type', ''),",
            "                session_key=_lark_next_sk,",
            "            )",
            "    except Exception:",
            "        import logging as _lark_logging",
            "        _lark_logging.getLogger('hermes_fry_cards').debug(",
            "            'followup card pre-create skipped', exc_info=True)",
            *_hook_exception_lines("followup_complete"),
        ],
    )


def _followup_result_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_FOLLOWUP_RESULT,
        MK_FOLLOWUP_RESULT_END,
        [
            "try:",
            "    from hermes_fry_cards.patch import on_queued_followup_result",
            "    _lark_followup_completion_id = next_message_id or getattr(pending_event, 'message_id', None)",
            "    if _lark_followup_completion_id:",
            "        on_queued_followup_result(",
            "            message_id=_lark_followup_completion_id,",
            "            followup_result=followup_result,",
            "        )",
            *_hook_exception_lines("followup_result"),
        ],
    )


def _followup_done_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_FOLLOWUP_DONE,
        MK_FOLLOWUP_DONE_END,
        [
            "try:",
            "    from hermes_fry_cards.patch import (",
            "        on_queued_followup_completed, on_queued_followup_card_ready, collect_stream_stats,",
            "    )",
            "    if source.platform.value.lower() in ('feishu', 'lark'):",
            "        # 该续轮自己的 message_id。事件型续轮有 next_message_id；无事件续轮",
            "        # （后台通知 / steer）没有 id，按 session_key 复用边界处预建的卡（#37）。",
            "        _lark_fu_id = next_message_id or getattr(pending_event, 'message_id', None)",
            "        if not _lark_fu_id and turn_ctx.session_key:",
            "            _lark_fu_id = on_queued_followup_card_ready(",
            "                message_id=None,",
            "                chat_id=source.chat_id,",
            "                chat_type=getattr(source, 'chat_type', ''),",
            "                session_key=locals().get('next_session_key') or turn_ctx.session_key,",
            "            )",
            "        if _lark_fu_id:",
            "            _lark_fu_sent = await on_queued_followup_completed(",
            "                message_id=_lark_fu_id, result=followup_result,",
            "                session_key=locals().get('next_session_key') or turn_ctx.session_key,",
            "                stream_stats=collect_stream_stats(locals().get('self'), turn_ctx.session_key),",
            "            )",
            "            if _lark_fu_sent:",
            "                followup_result['response_previewed'] = True",
            "                followup_result['already_sent'] = True",
            "                followup_result['final_response'] = ''",
            "                followup_result['_hermes_lark_completion_id'] = _lark_fu_id",
            *_hook_exception_lines("followup_done"),
        ],
    )


def _tool_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_TOOL,
        MK_TOOL_END,
        [
            "try:",
            "    from hermes_fry_cards.patch import on_tool_updated",
            "    try:",
            "        _lark_ctx = ctx",
            "    except NameError:",
            "        try:",
            "            _lark_ctx = self._ctx",
            "        except (NameError, AttributeError):",
            "            _lark_ctx = None",
            "    if _lark_ctx is not None:",
            "        _lark_message_id = _lark_ctx.event_message_id",
            "        _lark_run_current = _lark_ctx._run_still_current",
            "        _lark_session_key = _lark_ctx.session_key",
            "    else:",
            "        _lark_message_id = event_message_id",
            "        _lark_run_current = _run_still_current",
            "        try:",
            "            _lark_session_key = session_key",
            "        except NameError:",
            "            _lark_session_key = None",
            "    if _lark_run_current() and event_type in ('tool.started', 'tool.completed'):",
            "        if on_tool_updated(",
            "            message_id=_lark_message_id,",
            "            tool_name=tool_name or '',",
            "            status='started' if event_type == 'tool.started' else 'completed',",
            "            detail=preview or '',",
            "            session_key=_lark_session_key,",
            "        ):",
            "            _lark_log_queue = getattr(_lark_ctx, 'log_queue', None) if _lark_ctx is not None else None",
            "            if _lark_ctx is None:",
            "                try:",
            "                    _lark_log_queue = log_queue",
            "                except NameError:",
            "                    pass",
            "            if _lark_log_queue is not None and event_type == 'tool.started' and tool_name != '_thinking':",
            "                from datetime import datetime as _lark_datetime",
            "                _lark_timestamp = _lark_datetime.now().strftime('%Y-%m-%d %H:%M:%S')",
            "                _lark_preview = f' \"{preview}\"' if preview else ''",
            "                _lark_log_queue.put(f'{_lark_timestamp}  {tool_name}:{_lark_preview}'.rstrip())",
            "            return",
            *_hook_exception_lines("tool"),
        ],
    )


def _answer_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_ANSWER,
        MK_ANSWER_END,
        [
            "try:",
            "    from hermes_fry_cards.patch import on_answer_delta",
            "    try:",
            "        _lark_message_id = ctx.event_message_id",
            "        _lark_run_current = ctx._run_still_current",
            "        _lark_session_key = ctx.session_key",
            "    except NameError:",
            "        _lark_message_id = event_message_id",
            "        _lark_run_current = _run_still_current",
            "        try:",
            "            _lark_session_key = session_key",
            "        except NameError:",
            "            _lark_session_key = None",
            "    if text and _lark_run_current() and on_answer_delta(message_id=_lark_message_id, text=text, session_key=_lark_session_key):",
            "        try:",
            "            # 增量由 fry 卡片接管: 把投递事实挂到网关流消费者上,",
            "            # 最终 send 的抑制判定按卡片健康状态走 True 分支(#43 反馈),",
            "            # 不再打 streamed=False 的重复投递警告. 合成轮次没有",
            "            # event_message_id(None), session_key 兜底(#48).",
            "            from hermes_fry_cards.patch import mark_stream_delivered",
            "            _lark_sc_holder = getattr(ctx, 'stream_consumer_holder', None)",
            "            if _lark_sc_holder is not None:",
            "                mark_stream_delivered(_lark_sc_holder[0], _lark_message_id, _lark_session_key)",
            "        except Exception:",
            "            pass",
            "        try:",
            "            _lark_stts_consumer = _stts_consumer_ref",
            "        except NameError:",
            "            _lark_stts_consumer = None",
            "        if _lark_stts_consumer is not None:",
            "            try:",
            "                _lark_stts_consumer.on_delta(text)",
            "            except Exception:",
            "                import logging as _lark_logging",
            "                _lark_logging.getLogger(\"hermes_fry_cards\").exception(",
            "                    \"injected hook failed: streaming_tts\"",
            "                )",
            "        return",
            *_hook_exception_lines("answer"),
        ],
    )


def _thinking_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_THINKING,
        MK_THINKING_END,
        [
            "try:",
            "    from hermes_fry_cards.patch import on_thinking_delta",
            "    try:",
            "        _lark_message_id = ctx.event_message_id",
            "        _lark_run_current = ctx._run_still_current",
            "        _lark_session_key = ctx.session_key",
            "    except NameError:",
            "        _lark_message_id = event_message_id",
            "        _lark_run_current = _run_still_current",
            "        try:",
            "            _lark_session_key = session_key",
            "        except NameError:",
            "            _lark_session_key = None",
            "    if (text and not already_streamed and _lark_run_current()",
            "            and on_thinking_delta(message_id=_lark_message_id, text=text, session_key=_lark_session_key)):",
            "        return",
            *_hook_exception_lines("thinking"),
        ],
    )


def _reasoning_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_REASONING,
        MK_REASONING_END,
        [
            "def _reasoning_cb(text):",
            "    try:",
            "        try:",
            "            _lark_message_id = ctx.event_message_id",
            "            _lark_run_current = ctx._run_still_current",
            "            _lark_session_key = ctx.session_key",
            "        except NameError:",
            "            _lark_message_id = event_message_id",
            "            _lark_run_current = _run_still_current",
            "            try:",
            "                _lark_session_key = session_key",
            "            except NameError:",
            "                _lark_session_key = None",
            "        if text and _lark_run_current():",
            "            from hermes_fry_cards.patch import on_reasoning_delta",
            "            on_reasoning_delta(message_id=_lark_message_id, text=text, session_key=_lark_session_key)",
            *_hook_exception_lines("reasoning", indent="    "),
            "agent.reasoning_callback = _reasoning_cb",
        ],
    )


def _background_review_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_BACKGROUND_REVIEW,
        MK_BACKGROUND_REVIEW_END,
        [
            "try:",
            "    from hermes_fry_cards.patch import on_background_review_message",
            "    _lark_bg_review_sender = agent.background_review_callback",
            "    def _lark_bg_review_callback(message):",
            "        try:",
            "            _lark_message_id = ctx.event_message_id",
            "        except NameError:",
            "            _lark_message_id = event_message_id",
            "        _lark_bg_review_deferred = on_background_review_message(",
            "            message_id=_lark_message_id,",
            "            text=message,",
            "            sender=_lark_bg_review_sender,",
            "        )",
            "        if not _lark_bg_review_deferred:",
            "            _lark_bg_review_sender(message)",
            "    agent.background_review_callback = _lark_bg_review_callback",
            *_hook_exception_lines("background_review"),
        ],
    )


def _abort_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_ABORT,
        MK_ABORT_END,
        [
            "try:",
            "    from hermes_fry_cards.patch import on_message_aborted",
            "    on_message_aborted(message_id=event.message_id)",
            *_hook_exception_lines("abort"),
        ],
    )


def _stop_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_STOP,
        MK_STOP_END,
        [
            "try:",
            "    if source.platform.value.lower() in ('feishu', 'lark'):",
            "        from hermes_fry_cards.patch import on_session_aborted",
            "        await on_session_aborted(",
            "            session_key=locals().get('quick_key') or locals().get('_quick_key') or '',",
            "        )",
            *_hook_exception_lines("stop"),
        ],
    )


def _interrupt_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_INTERRUPT,
        MK_INTERRUPT_END,
        [
            "try:",
            "    if source.platform.value.lower() in ('feishu', 'lark'):",
            "        from hermes_fry_cards.patch import (",
            "            on_message_aborted, on_message_interrupted, on_message_started,",
            "        )",
            "        _lark_was_interrupted = locals().get('was_interrupted')",
            "        if _lark_was_interrupted is None:",
            "            _lark_was_interrupted = bool(locals().get('result', {}).get('interrupted'))",
            "        _lark_event_message_id = locals().get('event_message_id')",
            "        if not _lark_event_message_id and locals().get('turn_ctx') is not None:",
            "            _lark_event_message_id = turn_ctx.event_message_id",
            "        _lark_next_message_id = getattr(pending_event, 'message_id', None) or next_message_id",
            "        _lark_next_anchor_id = next_message_id",
            "        if _lark_was_interrupted and _lark_next_message_id:",
            "            on_message_interrupted(",
            "                message_id=_lark_event_message_id,",
            "                new_message_id=_lark_next_message_id,",
            "                chat_id=source.chat_id,",
            "                chat_type=getattr(source, 'chat_type', ''),",
            "                anchor_id=_lark_next_anchor_id,",
            "                session_key=locals().get('next_session_key') or locals().get('session_key'),",
            "            )",
            "        elif _lark_was_interrupted:",
            "            on_message_aborted(message_id=_lark_event_message_id)",
            "        elif pending_event is not None and _lark_next_message_id:",
            "            on_message_started(",
            "                message_id=_lark_next_message_id,",
            "                chat_id=getattr(next_source, 'chat_id', source.chat_id),",
            "                chat_type=getattr(next_source, 'chat_type', getattr(source, 'chat_type', '')),",
            "                anchor_id=_lark_next_anchor_id,",
            "                session_key=locals().get('next_session_key') or locals().get('session_key'),",
            "            )",
            *_hook_exception_lines("interrupt"),
        ],
    )


def _cron_deliver_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_CRON_DELIVER,
        MK_CRON_DELIVER_END,
        [
            "try:",
            "    _lark_target = locals().get('target') or {}",
            "    _lark_delivery = locals().get('t')",
            "    _lark_delivery_platform = (",
            "        _lark_delivery.get('platform_name', _lark_delivery.get('platform', ''))",
            "        if isinstance(_lark_delivery, dict)",
            "        else getattr(_lark_delivery, 'platform_name', '')",
            "    )",
            "    _lark_delivery_chat_id = (",
            "        _lark_delivery.get('chat_id', '')",
            "        if isinstance(_lark_delivery, dict)",
            "        else getattr(_lark_delivery, 'chat_id', '')",
            "    )",
            "    _lark_platform_name = (",
            "        _lark_delivery_platform",
            "        or locals().get('platform_name')",
            "        or _lark_target.get('platform', '')",
            "    )",
            "    _lark_chat_id = (",
            "        _lark_delivery_chat_id",
            "        or locals().get('chat_id')",
            "        or _lark_target.get('chat_id', '')",
            "    )",
            "    _lark_transport = (",
            "        locals().get('transport')",
            "        or (_lark_delivery.get('transport') if isinstance(_lark_delivery, dict) else None)",
            "        or getattr(_lark_delivery, 'transport', None)",
            "    )",
            "    if (",
            "        str(_lark_platform_name).lower() in ('feishu', 'lark')",
            "        and not getattr(_lark_transport, 'is_relay', False)",
            "    ):",
            "        if '_hermes_lark_cron_seen' not in locals():",
            "            _hermes_lark_cron_seen = set()",
            "        _hermes_lark_cron_key = (str(_lark_chat_id), cleaned_delivery_content.strip())",
            "        if _hermes_lark_cron_key in _hermes_lark_cron_seen:",
            "            delivered = True",
            "            continue",
            "        from hermes_fry_cards.patch import on_cron_deliver",
            "        if on_cron_deliver(",
            "            chat_id=_lark_chat_id,",
            "            content=cleaned_delivery_content.strip(),",
            "            loop=loop,",
            "            task_name=job.get('name', ''),",
            "            run_time=job.get('next_run_at', ''),",
            "        ):",
            "            _hermes_lark_cron_seen.add(_hermes_lark_cron_key)",
            "            delivered = True",
            "            continue",
            *_hook_exception_lines("cron_deliver"),
        ],
    )


def _bg_deliver_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_BG_DELIVER,
        MK_BG_DELIVER_END,
        [
            "try:",
            "    if source.platform.value.lower() in ('feishu', 'lark') and response:",
            "        from hermes_fry_cards.patch import on_background_deliver",
            "        _bg_preview = prompt[:60] + ('...' if len(prompt) > 60 else '')",
            "        if await on_background_deliver(",
            "            chat_id=source.chat_id,",
            "            preview=_bg_preview,",
            "            content=text_content,",
            "            reply_to_message_id=event_message_id,",
            "        ):",
            "            text_content = ''",
            "            if not images and not media_files:",
            "                return",
            *_hook_exception_lines("background_deliver"),
        ],
    )


def _clarify_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_CLARIFY,
        MK_CLARIFY_END,
        [
            "try:",
            "    from hermes_fry_cards.patch import on_clarify_enter, on_clarify_exit",
            "    _lark_clarify_orig = agent.clarify_callback",
            "    def _lark_clarify_wrapper(question, choices, multi_select=False):",
            "        try:",
            "            _lark_clarify_msg_id = ctx.event_message_id",
            "            _lark_clarify_chat_id = ctx._status_chat_id",
            "            _lark_clarify_sk = ctx.session_key",
            "        except NameError:",
            "            _lark_clarify_msg_id = event_message_id",
            "            _lark_clarify_chat_id = None",
            "            _lark_clarify_sk = None",
            "        on_clarify_enter(",
            "            message_id=_lark_clarify_msg_id,",
            "            chat_id=_lark_clarify_chat_id,",
            "            session_key=_lark_clarify_sk,",
            "        )",
            "        try:",
            "            return _lark_clarify_orig(question, choices, multi_select)",
            "        finally:",
            "            on_clarify_exit(",
            "                message_id=_lark_clarify_msg_id,",
            "                chat_id=_lark_clarify_chat_id,",
            "                session_key=_lark_clarify_sk,",
            "            )",
            "    agent.clarify_callback = _lark_clarify_wrapper",
            *_hook_exception_lines("clarify"),
        ],
    )


def _group_boundary_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_GROUP_BOUNDARY,
        MK_GROUP_BOUNDARY_END,
        [
            "try:",
            "    from hermes_fry_cards.patch import apply_group_security_boundary",
            "    _lark_gb_cfg = getattr(self._ctx, 'user_config', None)",
            "    _lark_gb_src = getattr(self._ctx, 'source', None)",
            "    if _lark_gb_cfg is not None and _lark_gb_src is not None:",
            "        combined = apply_group_security_boundary(combined, _lark_gb_cfg, _lark_gb_src)",
            *_hook_exception_lines("group_boundary"),
        ],
    )


def _tool_gating_hook(indent: str) -> str:
    return _make_hook(
        indent,
        MK_TOOL_GATING,
        MK_TOOL_GATING_END,
        [
            "try:",
            "    from hermes_fry_cards.patch import apply_group_tool_gating",
            "    _lark_tg = apply_group_tool_gating(",
            "        enabled, disabled, getattr(self, 'config', None), user_config, source,",
            "    )",
            "    if _lark_tg is not None:",
            "        enabled, disabled = _lark_tg",
            *_hook_exception_lines("tool_gating"),
        ],
    )


_HOOK_FNS = {
    "normalize": _feishu_normalize_hook,
    "start": _start_hook,
    "complete": _complete_hook,
    "followup_complete": _followup_complete_hook,
    "followup_result": _followup_result_hook,
    "followup_done": _followup_done_hook,
    "abort": _abort_hook,
    "stop": _stop_hook,
    "interrupt": _interrupt_hook,
    "tool": _tool_hook,
    "answer": _answer_hook,
    "thinking": _thinking_hook,
    "reasoning": _reasoning_hook,
    "background_review": _background_review_hook,
    "bg_deliver": _bg_deliver_hook,
    "clarify": _clarify_hook,
    "busy_ack": _busy_ack_hook,
    "redirect": _redirect_hook,
    "redirect_resolved": _redirect_resolved_hook,
    "bg_completion": _bg_completion_hook,
    "bg_busy_suppress": _bg_busy_suppress_hook,
    "compression": _compression_hook,
    "group_boundary": _group_boundary_hook,
    "tool_gating": _tool_gating_hook,
}


def _remove_block(content: str, begin: str, end: str) -> str:
    lines = content.splitlines(keepends=True)
    result: list[str] = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped == begin:
            if in_block:
                return content
            in_block = True
            continue
        if stripped == end:
            if not in_block:
                return content
            in_block = False
            continue
        if not in_block:
            result.append(line)
    return content if in_block else "".join(result)


def _atomic_write(path: Path, content: str) -> None:
    """原子写入：先写临时文件再 rename，防止崩溃时文件损坏."""
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, dir=str(path.parent), prefix=".hermes_lark_", mode="w", encoding="utf-8"
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
        shutil.copymode(path, tmp_path)
        os.replace(str(tmp_path), str(path))
    except BaseException:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise


class PatcherError(RuntimeError):
    pass


def _remove_block_checked(content: str, begin: str, end: str) -> str:
    updated = _remove_block(content, begin, end)
    if any(line.strip() in (begin, end) for line in updated.splitlines()):
        raise PatcherError(f"Malformed injected marker block: {begin}")
    return updated


class Patcher:
    """管理 AST 注入的安装和移除."""

    MARKERS: list[tuple[str, str]] = MARKERS

    def __init__(
        self,
        run_path: Path | None = None,
        modular_paths: dict[str, Path] | None = None,
    ) -> None:
        self._modular_paths: dict[str, Path] | None = modular_paths
        if modular_paths is not None:
            required = {"inbound", "turn", "runner", "busy"}
            if not required.issubset(modular_paths):
                raise PatcherError(
                    "modular_paths must contain at least: " + ", ".join(sorted(required))
                )
            # 可选模块（0.22+ 的 progress / persistence / notifications）只接受已知名字，
            # 防止拼写错误让锚点静默回退到旧模块。
            unknown = set(modular_paths) - required - {"progress", "persistence", "notifications"}
            if unknown:
                raise PatcherError(
                    "modular_paths contains unknown modules: " + ", ".join(sorted(unknown))
                )
            self.run_path = modular_paths["turn"]
        else:
            self.run_path = run_path or _default_run_path()
        if run_path is None and modular_paths is None and self.run_path.exists():
            content = self.run_path.read_text(encoding="utf-8")
            if "def _handle_message_with_agent" not in content:
                modular = _default_modular_paths()
                if all(path.exists() for path in modular.values()):
                    self._modular_paths = modular
                    self.run_path = modular["turn"]
        if not self.run_path.exists():
            tried = ", ".join(str(r) for r in _code_roots())
            raise PatcherError(
                f"Hermes gateway source not found: {self.run_path} "
                f"(tried: {tried}). "
                f"Set HERMES_HOME to the dir containing hermes-agent/ and rerun."
            )
        # Modular layout additionally hosts the group-security-boundary hook (in
        # gateway/run_turn_runner.py), so removal / status must know its markers.
        if self._modular_paths is not None:
            self.MARKERS = list(MARKERS) + [
                (MK_GROUP_BOUNDARY, MK_GROUP_BOUNDARY_END),
                (MK_TOOL_GATING, MK_TOOL_GATING_END),
            ]

    def _source_paths(self) -> list[Path]:
        if self._modular_paths is not None:
            return list(self._modular_paths.values())
        return [self.run_path]

    def _combined_content(self) -> str:
        return "\n".join(path.read_text(encoding="utf-8") for path in self._source_paths())

    def is_patched(self) -> bool:
        return MK_START in self._combined_content()

    def is_fully_patched(self) -> bool:
        content = self._combined_content()
        answer_name = "stream_delta_cb" if self._modular_paths is not None else "_stream_delta_cb"
        answer_sites = 0
        for path in self._source_paths():
            source = path.read_text(encoding="utf-8")
            answer_sites += len(_find_func_bodies(ast.parse(source), source.splitlines(keepends=True), answer_name))
        for begin, end in self.MARKERS:
            if begin in _OPTIONAL_MARKERS and content.count(begin) == 0:
                # 可选锚点（#43）：该 layout 没有对应回调时本就不注入，不算未打全。
                continue
            expected = answer_sites if begin == MK_ANSWER else 1
            if content.count(begin) != expected or content.count(end) != expected:
                return False
        return True

    def marker_status(self) -> dict[str, bool]:
        """返回每个 marker 的安装状态（已安装 / 缺失），供 status 展示."""
        content = self._combined_content()
        return {begin: begin in content for begin, _end in self.MARKERS}

    def verify_target(self) -> None:
        if self._modular_paths is not None:
            self._verify_modular_target()
            return
        content = self.run_path.read_text(encoding="utf-8")
        tree = ast.parse(content)

        handler = _find_func_body(tree, content.splitlines(keepends=True), "_handle_message_with_agent")
        if handler is None:
            raise PatcherError("Cannot find _handle_message_with_agent in run.py — Hermes version may be incompatible")

        anchor_found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "emit":
                    hooks_obj = func.value
                    if (
                        isinstance(hooks_obj, ast.Attribute)
                        and hooks_obj.attr == "hooks"
                        and (node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "agent:end")
                    ):
                        anchor_found = True
                        break
        if not anchor_found:
            raise PatcherError(
                "Cannot find hooks.emit('agent:end', ...) anchor in run.py — Hermes version may be incompatible"
            )

        required_callbacks = {
            "progress_callback": False,
            "_stream_delta_cb": False,
            "_interim_assistant_cb": False,
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name in required_callbacks:
                required_callbacks[node.name] = True
        missing = [name for name, found in required_callbacks.items() if not found]
        if missing:
            raise PatcherError(
                f"Missing injection targets in run.py: {', '.join(missing)} — Hermes version may be incompatible"
            )

        # 字符串锚点检查：primary 命中或任一 fallback 命中即可。
        for primary, fallbacks, label in _ANCHOR_CHECKS:
            if primary in content or any(fb in content for fb in fallbacks):
                continue
            raise PatcherError(f"Cannot find {label} anchor in run.py — Hermes version may be incompatible")

        normalize_site = _find_handle_message_source_site(tree, content.splitlines(keepends=True))
        if normalize_site is None:
            raise PatcherError(
                "Cannot find _handle_message source anchor in run.py — Hermes version may be incompatible"
            )

    def _verify_modular_target(self) -> None:
        assert self._modular_paths is not None
        parsed = {}
        for name, path in self._modular_paths.items():
            content = path.read_text(encoding="utf-8")
            parsed[name] = (content, ast.parse(content), content.splitlines(keepends=True))

        _, inbound_tree, inbound_lines = parsed["inbound"]
        _, turn_tree, turn_lines = parsed["turn"]
        _, runner_tree, runner_lines = parsed["runner"]
        _, busy_tree, busy_lines = parsed["busy"]
        # 0.22+ 微模块重构：progress_callback 可能在独立 progress 模块（优先），
        # 否则回退 runner；两侧与 _inject_modular 的解析顺序保持一致。
        tool_tree, tool_lines = self._tool_anchor_source(parsed)
        checks = [
            (_find_handle_message_source_site(inbound_tree, inbound_lines), "normalize"),
            (_find_func_body(turn_tree, turn_lines, "_handle_message_with_agent"), "start"),
            (_find_handler_return(turn_tree, turn_lines), "complete"),
            (_find_followup_complete_site(turn_tree, turn_lines), "queued follow-up boundary"),
            (_find_followup_result_site(turn_tree, turn_lines), "queued follow-up return"),
            (_find_followup_done_site(turn_tree, turn_lines), "queued follow-up completion"),
            (_find_handler_abort(turn_tree, turn_lines), "abort"),
            (_find_interrupt_site(turn_tree, turn_lines), "interrupt"),
            (_find_bg_deliver_site(turn_tree, turn_lines), "background deliver"),
            (_find_stop_site(busy_tree, busy_lines), "stop"),
            (_find_func_body(tool_tree, tool_lines, "progress_callback"), "tool"),
            (_find_func_body(runner_tree, runner_lines, "interim_assistant_cb"), "thinking"),
            (_find_reasoning_site(runner_tree, runner_lines), "reasoning"),
            (_find_background_review_site(runner_tree, runner_lines), "background review"),
            (_find_clarify_site(runner_tree, runner_lines), "clarify"),
            (_find_ephemeral_prompt_return(runner_tree, runner_lines), "group boundary"),
        ]
        answer_sites = _find_func_bodies(runner_tree, runner_lines, "stream_delta_cb")
        if not answer_sites:
            checks.append((None, "answer callback"))
        missing = [label for site, label in checks if site is None]
        if missing:
            raise PatcherError(
                "Missing modular Hermes injection targets: " + ", ".join(missing)
            )
        # agent:end 在 0.22+ 迁移至独立 persistence 模块；turn 与 persistence 任一命中即可。
        agent_end_hosts = [name for name in ("turn", "persistence") if name in parsed]
        if not any('hooks.emit("agent:end"' in parsed[name][0] for name in agent_end_hosts):
            searched = " / ".join(f"gateway/run_turn_{n}.py" if n != "turn" else "gateway/run_turn.py"
                                  for n in agent_end_hosts)
            raise PatcherError(f"Cannot find hooks.emit('agent:end', ...) in {searched}")

    @staticmethod
    def _tool_anchor_source(
        parsed: dict[str, tuple[str, ast.Module, list[str]]],
    ) -> tuple[ast.Module, list[str]]:
        """tool 钩子的宿主模块：独立 progress 模块含 progress_callback 时优先，否则 runner."""
        for name in ("progress", "runner"):
            if name not in parsed:
                continue
            _, tree, lines = parsed[name]
            if _find_func_body(tree, lines, "progress_callback") is not None:
                return tree, lines
        # 两处都没有：交给 verify 的 tool 检查统一报缺失。
        return parsed["runner"][1], parsed["runner"][2]

    def apply(self) -> None:
        if self.is_fully_patched():
            return

        self.verify_target()
        if self._modular_paths is None:
            content = self.run_path.read_text(encoding="utf-8")
            if any(marker in content for pair in self.MARKERS for marker in pair):
                for begin, end in self.MARKERS:
                    content = _remove_block_checked(content, begin, end)
            else:
                self._backup()
            content = self._inject_all(content)
            _atomic_write(self.run_path, content)
            return

        contents = {name: path.read_text(encoding="utf-8") for name, path in self._modular_paths.items()}
        has_markers = any(
            marker in content for content in contents.values() for pair in self.MARKERS for marker in pair
        )
        if has_markers:
            for name, content in contents.items():
                for begin, end in self.MARKERS:
                    content = _remove_block_checked(content, begin, end)
                contents[name] = content
        else:
            self._backup()
        injected = self._inject_modular(contents)
        for name, content in injected.items():
            _atomic_write(self._modular_paths[name], content)

    def remove(self) -> None:
        for path in self._source_paths():
            content = path.read_text(encoding="utf-8")
            if not any(marker in content for pair in self.MARKERS for marker in pair):
                continue
            for begin, end in self.MARKERS:
                content = _remove_block_checked(content, begin, end)
            _atomic_write(path, content)

    def restore(self) -> None:
        missing = []
        for path in self._source_paths():
            backup = path.with_suffix(path.suffix + _BACKUP_SUFFIX)
            if not backup.exists():
                missing.append(str(backup))
                continue
            shutil.copy2(backup, path)
        if missing:
            raise PatcherError(f"No backup found: {', '.join(missing)}")

    def _backup(self) -> None:
        for path in self._source_paths():
            backup = path.with_suffix(path.suffix + _BACKUP_SUFFIX)
            if not backup.exists():
                shutil.copy2(path, backup)

    def _inject_modular(self, contents: dict[str, str]) -> dict[str, str]:
        specs: dict[str, list[tuple[str, str, Callable[[ast.Module, list[str]], tuple[int, str] | None]]]] = {
            "inbound": [("normalize", "normalize", _find_handle_message_source_site)],
            "turn": [
                # #55 可选锚点：旧 modular 布局没有 _resolve_turn_toolsets 时跳过注入，
                # 功能降级为无工具隔离（与 busy_ack 等可选锚点同一处理）。
                ("tool_gating", "turn toolsets", _find_turn_toolsets_return),
                (
                    "start",
                    "start",
                    lambda tree, lines: _find_func_body(tree, lines, "_handle_message_with_agent"),
                ),
                ("complete", "complete", _find_handler_return),
                ("followup_complete", "followup_complete", _find_followup_complete_site),
                ("followup_result", "followup_result", _find_followup_result_site),
                ("followup_done", "queued follow-up completion", _find_followup_done_site),
                ("abort", "abort", _find_handler_abort),
                ("interrupt", "interrupt", _find_interrupt_site),
                ("bg_deliver", "bg_deliver", _find_bg_deliver_site),
            ],
            "runner": [
                (
                    "thinking",
                    "thinking",
                    lambda tree, lines: _find_func_body(tree, lines, "interim_assistant_cb"),
                ),
                ("reasoning", "reasoning", _find_reasoning_site),
                ("background_review", "background_review", _find_background_review_site),
                ("clarify", "clarify", _find_clarify_site),
                ("compression", "compression", _find_compression_site),
                ("group_boundary", "group_boundary", _find_ephemeral_prompt_return),
            ],
            "busy": [
                ("stop", "stop", _find_stop_site),
                # #43 可选锚点：旧版 Hermes 缺任一回调时跳过注入（见 _inject_sites 的 optional 处理）
                ("busy_ack", "busy ack", _find_busy_ack_site),
                ("redirect", "redirect", _find_redirect_site),
                # 重定向判定点（回执防抖之前）：连续第二次打断的回执被防抖吞掉时
                # 仍能换卡（#43 反馈）
                ("redirect_resolved", "redirect resolved", _find_redirect_resolved_site),
            ],
            # 0.22+ 独立 progress 模块的宿主（tool 注入点按 progress_callback 归属动态挂载）。
            "progress": [],
            # persistence 只承载 agent:end 的 verify 探测，无注入点；留在 specs 里防 KeyError。
            "persistence": [],
            # 0.22+ 的 watch/completion 通知宿主（#47 可选注入点）。
            "notifications": [
                ("bg_completion", "background completion", _find_bg_completion_site),
                ("bg_busy_suppress", "background busy receipt suppress", _find_bg_busy_suppress_site),
            ],
        }
        # tool 锚点宿主：与 _verify_modular_target 的解析顺序一致（progress 优先，回退 runner）。
        tool_host = "runner"
        if "progress" in contents:
            progress_lines = contents["progress"].splitlines(keepends=True)
            if _find_func_body(ast.parse(contents["progress"]), progress_lines, "progress_callback"):
                tool_host = "progress"
        specs[tool_host] = [
            (
                "tool",
                "tool",
                lambda tree, lines: _find_func_body(tree, lines, "progress_callback"),
            ),
            *specs[tool_host],
        ]
        result: dict[str, str] = {}
        for name, content in contents.items():
            module_specs = specs.get(name)
            if not module_specs:
                # 无注入点的可选模块（persistence）原样跳过，不写回。
                continue
            tree = ast.parse(content)
            lines = content.splitlines(keepends=True)
            hook_defs: list[tuple[str, str, tuple[int, str] | None]] = [
                (hook, label, finder(tree, lines)) for hook, label, finder in module_specs
            ]
            if name == "runner":
                hook_defs.extend(
                    ("answer", f"answer callback {index}", loc)
                    for index, loc in enumerate(_find_func_bodies(tree, lines, "stream_delta_cb"), start=1)
                )
            result[name] = self._inject_sites(lines, hook_defs)
        return result

    @staticmethod
    def _inject_sites(lines: list[str], hook_defs: list[tuple[str, str, tuple[int, str] | None]]) -> str:
        sites: list[tuple[int, str, str]] = []
        for hook_fn_name, name, loc in hook_defs:
            if loc is None:
                if hook_fn_name in OPTIONAL_HOOKS:
                    # 可选锚点（#43 提示入卡）：旧版 Hermes 没有该回调时跳过，
                    # 不影响安装；对应功能降级为 Hermes 原有的纯文本提示。
                    _logger.info("optional injection target missing, skipped: %s", name)
                    continue
                raise PatcherError(f"Cannot locate {name} injection site — Hermes version may be incompatible")
            sites.append((loc[0], loc[1], hook_fn_name))
        sites.sort(key=lambda item: item[0], reverse=True)
        for idx, indent, fn_name in sites:
            lines[idx:idx] = _HOOK_FNS[fn_name](indent).splitlines(keepends=True)
        return "".join(lines)

    def _inject_all(self, content: str) -> str:
        tree = ast.parse(content)
        lines = content.splitlines(keepends=True)

        hook_defs: list[tuple[str, str, tuple[int, str] | None]] = [
            ("normalize", "normalize", _find_handle_message_source_site(tree, lines)),
            ("start", "start", _find_func_body(tree, lines, "_handle_message_with_agent")),
            ("complete", "complete", _find_handler_return(tree, lines)),
            ("followup_complete", "followup_complete", _find_followup_complete_site(tree, lines)),
            ("followup_result", "followup_result", _find_followup_result_site(tree, lines)),
            ("followup_done", "queued follow-up completion", _find_followup_done_site(tree, lines)),
            ("abort", "abort", _find_handler_abort(tree, lines)),
            ("stop", "stop", _find_stop_site(tree, lines)),
            ("interrupt", "interrupt", _find_interrupt_site(tree, lines)),
            ("tool", "tool", _find_func_body(tree, lines, "progress_callback")),
            ("thinking", "thinking", _find_func_body(tree, lines, "_interim_assistant_cb")),
            ("reasoning", "reasoning", _find_reasoning_site(tree, lines)),
            ("background_review", "background_review", _find_background_review_site(tree, lines)),
            ("bg_deliver", "bg_deliver", _find_bg_deliver_site(tree, lines)),
            ("clarify", "clarify", _find_clarify_site(tree, lines)),
            ("compression", "compression", _find_compression_site(tree, lines)),
        ]
        hook_defs.extend(
            ("answer", f"answer callback {index}", loc)
            for index, loc in enumerate(_find_func_bodies(tree, lines, "_stream_delta_cb"), start=1)
        )

        sites: list[tuple[int, str, str]] = []
        for hook_fn_name, name, loc in hook_defs:
            if loc is None:
                if hook_fn_name in OPTIONAL_HOOKS:
                    _logger.info("optional injection target missing in run.py, skipped: %s", name)
                    continue
                # 定位失败硬失败，不静默跳过（防位置漂移致重复消息）
                raise PatcherError(
                    f"Cannot locate {name} injection site — Hermes version may be incompatible"
                )
            sites.append((loc[0], loc[1], hook_fn_name))

        sites.sort(key=lambda x: x[0], reverse=True)
        _HOOK_FNS = {
            "normalize": _feishu_normalize_hook,
            "start": _start_hook,
            "complete": _complete_hook,
            "followup_complete": _followup_complete_hook,
            "followup_result": _followup_result_hook,
            "abort": _abort_hook,
            "stop": _stop_hook,
            "interrupt": _interrupt_hook,
            "tool": _tool_hook,
            "answer": _answer_hook,
            "thinking": _thinking_hook,
            "reasoning": _reasoning_hook,
            "background_review": _background_review_hook,
            "bg_deliver": _bg_deliver_hook,
            "clarify": _clarify_hook,
            "followup_done": _followup_done_hook,
            "busy_ack": _busy_ack_hook,
            "redirect": _redirect_hook,
            "compression": _compression_hook,
        }
        for idx, indent, fn_name in sites:
            hook = _HOOK_FNS[fn_name](indent)
            lines[idx:idx] = hook.splitlines(keepends=True)

        return "".join(lines)


def _find_func_body(tree: ast.Module, lines: list[str], name: str) -> tuple[int, str] | None:
    sites = _find_func_bodies(tree, lines, name)
    return sites[0] if sites else None


def _find_func_bodies(tree: ast.Module, lines: list[str], name: str) -> list[tuple[int, str]]:
    sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            body = node.body
            start = 0
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                start = 1
            if start < len(body):
                lineno = body[start].lineno - 1
                indent = _safe_indent(lines, lineno)
                sites.append((lineno, indent))
    return sites


def _find_handle_message_source_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_handle_message":
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and stmt.targets[0].id == "source"
                    and isinstance(stmt.value, ast.Attribute)
                    and stmt.value.attr == "source"
                    and isinstance(stmt.value.value, ast.Name)
                    and stmt.value.value.id == "event"
                ):
                    lineno = stmt.end_lineno or stmt.lineno
                    return lineno, _safe_indent(lines, stmt.lineno - 1)
                # Hermes 0.21+: admission returns ``event, source, is_internal``.
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], (ast.Tuple, ast.List))
                    and any(isinstance(elt, ast.Name) and elt.id == "source" for elt in stmt.targets[0].elts)
                    and isinstance(stmt.value, ast.Name)
                    and stmt.value.id == "_admitted"
                ):
                    lineno = stmt.end_lineno or stmt.lineno
                    return lineno, _safe_indent(lines, stmt.lineno - 1)
    return None


def _find_ephemeral_prompt_return(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    """Locate the ``return combined`` inside ``TurnRunner._combined_ephemeral_prompt``.

    The group-security-boundary hook is injected immediately before this return, so it can
    reassign ``combined`` in place before the method returns it.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "_combined_ephemeral_prompt":
            continue
        # Find the top-level ``return combined`` (last statement of the method body).
        target = None
        for stmt in node.body:
            if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name) and stmt.value.id == "combined":
                target = stmt
        if target is not None and target.lineno is not None:
            lineno = target.lineno - 1
            return lineno, _safe_indent(lines, lineno)
    return None


def _find_turn_toolsets_return(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    """Locate the ``return enabled, disabled`` inside ``_resolve_turn_toolsets``.

    The group tool gating hook (#55) is injected immediately before this return so it can
    reassign both values in place — foreground turns (_run_agent_display_settings →
    TurnContext) and background tasks funnel their toolsets through this one resolver.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "_resolve_turn_toolsets":
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Return) or not isinstance(stmt.value, ast.Tuple):
                continue
            elts = stmt.value.elts
            if len(elts) != 2 or not all(isinstance(e, ast.Name) for e in elts):
                continue
            names = {str(getattr(e, "id", "")) for e in elts}
            if names == {"enabled", "disabled"}:
                lineno = stmt.lineno - 1
                return lineno, _safe_indent(lines, lineno)
    return None


def _find_handler_return(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("_already_sent = bool("):
            indent = _safe_indent(lines, i)
            return i, indent
        # Hermes 0.21+: finish the card before the runtime footer/native-delivery decision.
        if stripped.startswith("_footer_line = self._hmwa_runtime_footer_line("):
            return i, _safe_indent(lines, i)

    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == "_handle_message_with_agent":
            returns = [
                n
                for n in ast.walk(node)
                if isinstance(n, ast.Return)
                and isinstance(n.value, ast.Name)
                and n.value.id == "response"
                and n.lineno is not None
            ]
            if returns:
                target = max(returns, key=lambda x: x.lineno)
                lineno = target.lineno - 1
                indent = _safe_indent(lines, lineno)
                return lineno, indent
    return None


def _find_handler_abort(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    for i, line in enumerate(lines):
        if "self._hmwa_discard_stale_result(" in line:
            return i, _safe_indent(lines, i)
    for i, line in enumerate(lines):
        if "Discarding stale agent result" in line:
            for j in range(i + 1, min(i + 20, len(lines))):
                if lines[j].strip() == "return None":
                    indent = _safe_indent(lines, j)
                    return j, indent
            break
    return None


def _find_stop_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Await):
            continue
        call = node.value.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not isinstance(func, ast.Attribute) or func.attr != "_interrupt_and_clear_session":
            continue
        is_stop = any(
            keyword.arg == "invalidation_reason"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "stop_command"
            for keyword in call.keywords
        )
        if is_stop:
            return node.end_lineno or node.lineno, _safe_indent(lines, node.lineno - 1)
    return None


def _find_interrupt_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    for i, line in enumerate(lines):
        if (
            "Restart typing indicator so the user sees activity" in line
            or "Restart the typing indicator; the outer typing task may be stale" in line
        ):
            indent = _safe_indent(lines, i)
            return i, indent
    return None


def _find_followup_complete_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    for i, line in enumerate(lines):
        if line.strip() == 'was_interrupted = result.get("interrupted")':
            return i, _safe_indent(lines, i)
        if line.strip() == 'if not result.get("interrupted"):' and any(
            "def _run_agent_queued_followup" in prior for prior in lines[max(0, i - 80) : i]
        ):
            return i, _safe_indent(lines, i)
    return None


def _find_followup_result_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    """Locate the queued follow-up return site.

    Two upstream shapes are supported (hook is inserted BEFORE the matched line; it only
    reads ``followup_result``, already bound by the ``await self._run_agent(...)`` above,
    so both placements are semantically equivalent):

    - Hermes <= 0.21.0: ``return _preserve_queued_followup_history_offset(result, followup_result)``
    - Hermes >= 0.21.1: the return value is first assigned (e.g. ``merged = _preserve_...(result,
      followup_result)``) and later augmented with ``queued_terminal_inbound_id`` before return
    """
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "return _preserve_queued_followup_history_offset(result, followup_result)":
            return i, _safe_indent(lines, i)
        # 0.21.1+ assignment form: <var> = _preserve_queued_followup_history_offset(result, followup_result)
        if stripped.endswith("= _preserve_queued_followup_history_offset(result, followup_result)") and not stripped.startswith("return"):
            return i, _safe_indent(lines, i)
    return None


def _find_followup_done_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    """Locate the queued follow-up's own completion site (#35).

    Injected right BEFORE the ``_preserve_queued_followup_history_offset`` line, i.e. after
    ``followup_result = await self._run_agent(...)`` has returned: the hook needs the finished
    ``followup_result`` to seal that turn's card and suppress its plain-text delivery.

    Same anchor as :func:`_find_followup_result_site` (both read the already-bound
    ``followup_result``); kept separate so each marker stays independently removable and so a
    ``verify`` failure names which of the two lost its anchor.
    """
    return _find_followup_result_site(tree, lines)


def _find_reasoning_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    for i, line in enumerate(lines):
        if (
            line.strip() == "agent.reasoning_config = reasoning_config"
            or line.strip().startswith("agent.reasoning_config, agent.service_tier = reasoning_config")
        ):
            return i + 1, _safe_indent(lines, i)
    return None


def _find_background_review_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    for i, line in enumerate(lines):
        if (
            line.strip() == "agent.background_review_callback = _bg_review_send"
            or line.strip().startswith("agent.background_review_callback, bg_release = ")
        ):
            return i + 1, _safe_indent(lines, i)
    return None


def _find_bg_deliver_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    for i, line in enumerate(lines):
        if line.strip() == "images, text_content = adapter.extract_images(response)":
            return i + 1, _safe_indent(lines, i)
    return None


def _find_clarify_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    for i, line in enumerate(lines):
        if line.strip() in {
            "agent.clarify_callback = _clarify_callback_sync",
            "agent.clarify_callback = self._clarify_callback_sync",
        }:
            return i + 1, _safe_indent(lines, i)
    return None


def _find_compression_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    """会话压缩检测点：在 `Session split detected` 语句之后."""
    for i, line in enumerate(lines):
        if "Session split detected" in line:
            end_idx = _statement_end_after(tree, i + 1)
            return end_idx, _safe_indent(lines, end_idx)
    return None



# 忙碌回执可能落在的宿主函数（Hermes 0.22.3 起把「中断 + 回执」拆进后者）。
_BUSY_ACK_HOSTS = ("_handle_active_session_busy_message", "_busy_interrupt_and_ack")


def _find_busy_ack_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    """忙碌回执发送点：落在回执宿主函数内、发送调用之前.

    回执经 ``_send_busy_ack_reply`` 发出（更早的版本直接 ``_send_with_retry``）。
    宿主函数随 Hermes 版本变化：0.22.0 在 ``_handle_active_session_busy_message``，
    0.22.3 起拆到 ``_busy_interrupt_and_ack``；按 ``_BUSY_ACK_HOSTS`` 顺序试，
    避免全局搜索串到同文件其它发送点。
    """
    return _find_busy_notice_site(tree, lines)


def _find_redirect_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    """忙碌提示入卡点：与忙碌回执同一点（「文案合成后、发送前」）.

    文案由 ``_compose_busy_ack_message`` 按模式合成，注入进去后由 ``redirected``
    分流（重定向才换卡，其余并入当前卡），所以两种形态共用一个锚点。
    """
    return _find_busy_notice_site(tree, lines)


_REDIRECT_RESOLVED_ANCHOR = "effective_mode, redirected = _steer.effective_mode, _steer.redirected"


def _find_bg_completion_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    """后台完成通知接管点：``_inject_watch_notification`` 内、内部标记之前（#47）.

    完成通知在 ``_mark_internal_notification(synth_text)`` 之后经
    ``admit_process_notice`` / ``admit_internal_event`` 注入为纯文本气泡；
    挂在标记处理之前，fry 接管（并入活跃卡或独立静态卡）时跳过整条注入。
    宿主是 0.22+ 拆出的 ``gateway/run_notifications.py``；旧布局没有该模块时
    由「模块不存在 → specs 缺失」自然跳过。
    """
    return _find_call_line_in_function(
        tree, lines, "_inject_watch_notification", "_mark_internal_notification(synth_text)",
    )


_BG_BUSY_RECEIPT_ANCHOR = 'self._format_process_final_message(session_id, session, "concise")'
_BG_BUSY_RECEIPT_HOSTS = ("_watch_process", "_run_process_watcher")


def _find_bg_busy_suppress_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    """turn_busy 纯文本回执的抑制点：concise 文案合成之后、``_send_watcher_message`` 之前.

    发起轮仍在运行时，``_run_process_watcher`` 会在这条 concise 文案之后补发一条
    纯文本回执（#112033 的「先给个回执」）；插件已把同一条回执并进活跃卡时这里
    要能拦住它。锚点取「concise 文案合成」这一行。
    在 Hermes 0.22.5+1713 中，_run_process_watcher 变为 wrapper，真正承载逻辑的
    是 _watch_process，因此按 _BG_BUSY_RECEIPT_HOSTS 顺序查找。
    """
    for host in _BG_BUSY_RECEIPT_HOSTS:
        hit = _find_call_line_in_function(tree, lines, host, _BG_BUSY_RECEIPT_ANCHOR)
        if hit is not None:
            end_idx = _statement_end_after(tree, hit[0] + 1)
            return end_idx, _safe_indent(lines, end_idx) or hit[1]
    return None


def _find_redirect_resolved_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    """重定向判定点：``_steer`` 解包语句之后、回执防抖之前（#43 反馈）.

    连续第二次打断的回执被 30s 防抖吞掉，REDIRECT 锚点（发送前）走不到；
    这里挂在解包后立即换卡。宿主随版本在两个忙碌函数间迁移（0.22.0 在
    ``_handle_active_session_busy_message``，0.22.3+ 拆进
    ``_busy_interrupt_and_ack``），按 ``_BUSY_ACK_HOSTS`` 顺序试。
    """
    for host in _BUSY_ACK_HOSTS:
        hit = _find_call_line_in_function(tree, lines, host, _REDIRECT_RESOLVED_ANCHOR)
        if hit is None:
            continue
        end_idx = _statement_end_after(tree, hit[0] + 1)
        return end_idx, _safe_indent(lines, end_idx) or hit[1]
    return None


def _find_busy_notice_site(tree: ast.Module, lines: list[str]) -> tuple[int, str] | None:
    for host in _BUSY_ACK_HOSTS:
        for needle in ("_send_busy_ack_reply(", "_send_with_retry("):
            hit = _find_call_line_in_function(tree, lines, host, needle)
            if hit is None:
                continue
            # 发送可能被 try 包着：插入点取「调用之前、try 之内」，
            # 落到 except 之后会插进异常处理块，语法直接坏掉。
            idx = _insert_before_in_try(tree, hit[0])
            return idx, _safe_indent(lines, idx) or hit[1]
    return None


def _find_call_line_in_function(
    tree: ast.Module,
    lines: list[str],
    func_name: str,
    needle: str,
) -> tuple[int, str] | None:
    """在指定函数的行范围内找一条命中行，返回 ``(行下标, 缩进)``."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != func_name:
            continue
        start = node.lineno - 1
        end = min(node.end_lineno or node.lineno, len(lines))
        for i in range(start, end):
            if needle in lines[i]:
                return i, _safe_indent(lines, i)
    return None


def _insert_before_in_try(tree: ast.Module, lineno: int) -> int:
    """行 ``lineno``（0-based）是 ``try`` 里一条语句的首行时，返回该语句在 ``try``
    体内的首行下标（即 ``try:`` 之后、``else``/``except`` 之前的插入点）；
    否则原样返回 ``lineno``.
    """
    best: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        first = node.body[0].lineno - 1
        last = (node.body[-1].end_lineno or node.body[-1].lineno) - 1
        if first <= lineno <= last:
            best.append(first)
    return min(best) if best else lineno


def _find_call_in_function(
    tree: ast.Module,
    lines: list[str],
    func_name: str,
    needle: str,
    *,
    after: bool = False,
) -> tuple[int, str] | None:
    """在指定函数（含嵌套函数）的行范围内找一处调用，返回其前后插入点.

    ``needle`` 按「行内包含」匹配（调用行常带 ``await``/赋值前缀）。
    ``after=True`` 时返回命中行之后一行（用于读取刚赋值的局部变量）；
    ``needle`` 以 ``(`` 结尾时返回整个调用语句之后（调用可能跨多行）。
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != func_name:
            continue
        start = node.lineno - 1
        end = node.end_lineno or node.lineno
        for i in range(start, min(end, len(lines))):
            # 用 contains 而非 startswith：调用前面可能还有 `await`、赋值等前缀。
            if needle not in lines[i]:
                continue
            if needle.endswith("("):
                # 调用可能跨多行（`await adapter._send_with_retry(` 后换行），
                # 插入点要落在整个调用语句之后，否则会把调用截成两半。
                call_end = _statement_end_after(tree, i + 1)
                return call_end, _safe_indent(lines, call_end)
            idx = i + 1 if after else i
            return idx, _safe_indent(lines, idx)
    return None


def _statement_end_after(tree: ast.Module, lineno: int) -> int:
    """返回第 ``lineno`` 行（1-based）所在**最内层**语句结束后的行下标（0-based 插入点）.

    只取最内层：外层 ``try`` / ``if`` / 函数体同样覆盖这一行，取最外层会把插入点
    推到函数末尾或更后面（实测 0.22.0 的 busy 处理里直接落到了函数外）。
    """
    best: ast.stmt | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        node_lineno = getattr(node, "lineno", 0)
        node_end = getattr(node, "end_lineno", 0) or node_lineno
        if not (node_lineno <= lineno <= node_end):
            continue
        if best is None:
            best = node
            continue
        best_end = getattr(best, "end_lineno", 0) or best.lineno
        best_span = best_end - best.lineno
        if (node_end - node_lineno) < best_span:
            best = node
    if best is None:
        return lineno
    return best.end_lineno or (best.lineno - 1)


def _safe_indent(lines: list[str], lineno: int) -> str:
    """获取缩进，跳过空行."""
    for i in range(lineno, -1, -1):
        if 0 <= i < len(lines) and lines[i].strip():
            return lines[i][: len(lines[i]) - len(lines[i].lstrip())]
    for i in range(lineno + 1, len(lines)):
        if lines[i].strip():
            return lines[i][: len(lines[i]) - len(lines[i].lstrip())]
    return ""


class CronPatcher:
    """注入 CRON_DELIVER hook 到 cron/scheduler.py 的 _deliver_result."""

    def __init__(self, cron_path: Path | None = None) -> None:
        self.cron_path = cron_path or _default_cron_path()
        if not self.cron_path.exists():
            tried = ", ".join(str(r) for r in _code_roots())
            raise PatcherError(
                f"cron/scheduler.py not found: {self.cron_path} "
                f"(tried: {tried}). "
                f"Set HERMES_HOME to the dir containing hermes-agent/ and rerun."
            )

    def is_patched(self) -> bool:
        return MK_CRON_DELIVER in self.cron_path.read_text(encoding="utf-8")

    def verify_target(self) -> None:
        content = self.cron_path.read_text(encoding="utf-8")
        if "cleaned_delivery_content" not in content:
            raise PatcherError("Cannot find 'cleaned_delivery_content' in scheduler.py")
        modular_anchor = "target_errors: list = []" in content and "for target in targets:" in content
        if "delivered = False" not in content and not modular_anchor:
            raise PatcherError("Cannot find 'delivered = False' or modular cron delivery loop anchor")

    def apply(self) -> None:
        content = self.cron_path.read_text(encoding="utf-8")
        has_markers = MK_CRON_DELIVER in content or MK_CRON_DELIVER_END in content
        if has_markers:
            cleaned = _remove_block_checked(content, MK_CRON_DELIVER, MK_CRON_DELIVER_END)
            if content.count(MK_CRON_DELIVER) == 1 and content.count(MK_CRON_DELIVER_END) == 1:
                return
            content = cleaned
        self.verify_target()
        if not has_markers:
            self._backup()
        lines = content.splitlines(keepends=True)

        inject_idx = None
        if "target_errors: list = []" in content and "for target in targets:" in content:
            for i, line in enumerate(lines):
                if line.strip() == "target_errors: list = []":
                    inject_idx = i
                    break
        else:
            for i, line in enumerate(lines):
                if line.strip() == "delivered = False":
                    inject_idx = i
                    break
        if inject_idx is None:
            raise PatcherError("Cannot find cron delivery loop anchor")

        indent = _safe_indent(lines, inject_idx)
        hook = _cron_deliver_hook(indent)
        target_idx = inject_idx + 1
        lines[target_idx:target_idx] = hook.splitlines(keepends=True)
        _atomic_write(self.cron_path, "".join(lines))

    def remove(self) -> None:
        content = self.cron_path.read_text(encoding="utf-8")
        if MK_CRON_DELIVER not in content and MK_CRON_DELIVER_END not in content:
            return
        content = _remove_block_checked(content, MK_CRON_DELIVER, MK_CRON_DELIVER_END)
        _atomic_write(self.cron_path, content)

    def restore(self) -> None:
        backup = self.cron_path.with_suffix(self.cron_path.suffix + _BACKUP_SUFFIX)
        if not backup.exists():
            raise PatcherError(f"No backup found: {backup}")
        shutil.copy2(backup, self.cron_path)

    def _backup(self) -> None:
        backup = self.cron_path.with_suffix(self.cron_path.suffix + _BACKUP_SUFFIX)
        if not backup.exists():
            shutil.copy2(self.cron_path, backup)


# ---------------------------------------------------------------------------
# Clarify button cards (Feishu adapter runtime patch)
# ---------------------------------------------------------------------------

MK_CLARIFY_CARD = f"# {PREFIX}_CLARIFY_CARD_BEGIN"
MK_CLARIFY_CARD_END = f"# {PREFIX}_CLARIFY_CARD_END"

_CLARIFY_BLOCK = f"""{MK_CLARIFY_CARD}
try:  # hermes-fry-cards: clarify option buttons for Feishu
    from hermes_fry_cards import clarify as _hl_clarify

    _hl_clarify.apply_patch(
        FeishuAdapter,
        SendResult=SendResult,
        CallBackCard=CallBackCard,
        P2CardActionTriggerResponse=P2CardActionTriggerResponse,
    )
except Exception:  # pragma: no cover - never break the adapter import
    import logging as _hl_logging

    _hl_logging.getLogger("hermes_fry_cards").warning(
        "[fry-cards] clarify button patch failed to apply", exc_info=True
    )
{MK_CLARIFY_CARD_END}
"""


def _default_feishu_adapter_path() -> Path:
    """Locate plugins/platforms/feishu/adapter.py under known Hermes roots."""
    rel = Path("plugins") / "platforms" / "feishu" / "adapter.py"
    for root in _code_roots():
        candidate = root / rel
        if candidate.is_file():
            return candidate
    tried = ", ".join(str(r) for r in _code_roots())
    raise PatcherError(
        f"Feishu adapter not found: {rel} (tried: {tried}). "
        f"Set HERMES_HOME to the dir containing hermes-agent/ and rerun."
    )


class ClarifyPatcher:
    """Append a runtime-patch block to the Feishu adapter (clarify buttons).

    Unlike the gateway hooks (AST injection into method bodies), this patch
    only appends a self-contained block at module scope; the actual behavior
    lives in ``hermes_fry_cards.clarify`` and is applied via monkey-patching
    when the adapter module is imported.  Removal is a plain block delete.
    """

    def __init__(self, adapter_path: Path | None = None) -> None:
        self.adapter_path = adapter_path or _default_feishu_adapter_path()
        if not self.adapter_path.exists():
            raise PatcherError(f"Feishu adapter not found: {self.adapter_path}")

    def is_patched(self) -> bool:
        return MK_CLARIFY_CARD in self.adapter_path.read_text(encoding="utf-8")

    def verify_target(self) -> None:
        content = self.adapter_path.read_text(encoding="utf-8")
        required = (
            "class FeishuAdapter",
            "def _on_card_action_trigger",
            "def _is_interactive_operator_authorized",
            "def _feishu_send_with_retry",
            "def _finalize_send_result",
            "SendResult",
        )
        missing = [name for name in required if name not in content]
        if missing:
            raise PatcherError(
                "Feishu adapter is missing expected anchors: "
                + ", ".join(missing)
                + " — Hermes version may be incompatible"
            )
        if "def send_clarify" in content:
            raise PatcherError(
                "Feishu adapter already defines send_clarify — "
                "Hermes shipped a native implementation; clarify patch not needed"
            )

    def apply(self) -> None:
        content = self.adapter_path.read_text(encoding="utf-8")
        has_markers = MK_CLARIFY_CARD in content or MK_CLARIFY_CARD_END in content
        if has_markers:
            cleaned = _remove_block_checked(content, MK_CLARIFY_CARD, MK_CLARIFY_CARD_END)
            if content.count(MK_CLARIFY_CARD) == 1 and content.count(MK_CLARIFY_CARD_END) == 1:
                return
            content = cleaned
        self.verify_target()
        if not has_markers:
            self._backup()
        if content and not content.endswith("\n"):
            content += "\n"
        content += _CLARIFY_BLOCK
        _atomic_write(self.adapter_path, content)

    def remove(self) -> None:
        content = self.adapter_path.read_text(encoding="utf-8")
        if MK_CLARIFY_CARD not in content and MK_CLARIFY_CARD_END not in content:
            return
        content = _remove_block_checked(content, MK_CLARIFY_CARD, MK_CLARIFY_CARD_END)
        _atomic_write(self.adapter_path, content)

    def restore(self) -> None:
        backup = self.adapter_path.with_suffix(self.adapter_path.suffix + _BACKUP_SUFFIX)
        if not backup.exists():
            raise PatcherError(f"No backup found: {backup}")
        shutil.copy2(backup, self.adapter_path)

    def _backup(self) -> None:
        backup = self.adapter_path.with_suffix(self.adapter_path.suffix + _BACKUP_SUFFIX)
        if not backup.exists():
            shutil.copy2(self.adapter_path, backup)
