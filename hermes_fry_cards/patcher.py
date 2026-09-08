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
]
MARKERS: list[tuple[str, str]] = [(f"# {PREFIX}_{n}_BEGIN", f"# {PREFIX}_{n}_END") for n in _HOOK_NAMES]

MK_NORMALIZE, MK_NORMALIZE_END = MARKERS[0]
MK_START, MK_START_END = MARKERS[1]
MK_COMPLETE, MK_COMPLETE_END = MARKERS[2]
MK_FOLLOWUP_COMPLETE, MK_FOLLOWUP_COMPLETE_END = MARKERS[3]
MK_FOLLOWUP_RESULT, MK_FOLLOWUP_RESULT_END = MARKERS[4]
MK_TOOL, MK_TOOL_END = MARKERS[5]
MK_ANSWER, MK_ANSWER_END = MARKERS[6]
MK_THINKING, MK_THINKING_END = MARKERS[7]
MK_REASONING, MK_REASONING_END = MARKERS[8]
MK_BACKGROUND_REVIEW, MK_BACKGROUND_REVIEW_END = MARKERS[9]
MK_ABORT, MK_ABORT_END = MARKERS[10]
MK_STOP, MK_STOP_END = MARKERS[11]
MK_INTERRUPT, MK_INTERRUPT_END = MARKERS[12]
MK_BG_DELIVER, MK_BG_DELIVER_END = MARKERS[13]
MK_CLARIFY, MK_CLARIFY_END = MARKERS[14]

# Group-security boundary is MODULAR-ONLY (targets ``_combined_ephemeral_prompt`` in
# gateway/run_turn_runner.py, which exists only in the Hermes 0.21+ modular layout). Kept out of
# the shared MARKERS list so the legacy monolithic apply/verify paths (which never host it) and
# their tests are unaffected.
MK_GROUP_BOUNDARY = f"# {PREFIX}_GROUP_BOUNDARY_BEGIN"
MK_GROUP_BOUNDARY_END = f"# {PREFIX}_GROUP_BOUNDARY_END"

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
def _code_roots() -> list[Path]:
    return [hermes_home() / "hermes-agent", Path("/usr/local/lib/hermes-agent")]


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
    """Hermes 0.21+ split the former gateway/run.py implementation into mixins."""
    roots = _code_roots()
    return {
        "inbound": _resolve_module_path("gateway.run_inbound", roots),
        "turn": _resolve_module_path("gateway.run_turn", roots),
        "runner": _resolve_module_path("gateway.run_turn_runner", roots),
        "busy": _resolve_module_path("gateway.run_busy", roots),
    }


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
    ("return _preserve_queued_followup_history_offset(result, followup_result)", (), "queued follow-up return"),
    ("agent.reasoning_config = reasoning_config", (), "reasoning_config"),
    ("agent.background_review_callback = _bg_review_send", (), "background_review_callback"),
    ("images, text_content = adapter.extract_images(response)", (), "background deliver"),
    ("_already_sent = bool(", (), "complete"),
    ("Discarding stale agent result", (), "abort"),
    ("agent.clarify_callback = _clarify_callback_sync", (), "clarify_callback"),
]


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
            "    from hermes_fry_cards.patch import on_message_completed_wait, on_message_needs_text_fallback",
            "    _lark_completion_id = agent_result.get('_hermes_lark_completion_id') or event.message_id",
            "    _lark_card_sent = await on_message_completed_wait(",
            "        message_id=_lark_completion_id,",
            "        answer=response,",
            "        is_error=bool(agent_result.get('failed')),",
            "        duration=locals().get('_response_time', locals().get('_turn_seconds')),",
            "        model=agent_result.get('model', ''),",
            "        tokens={",
            "            'input_tokens': agent_result.get('input_tokens', 0),",
            "            'output_tokens': agent_result.get('output_tokens', 0),",
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
            "    from hermes_fry_cards.patch import on_queued_followup_boundary",
            "    _lark_delivery_result = response if isinstance(response, dict) else result",
            "    _lark_event_message_id = locals().get('event_message_id')",
            "    if not _lark_event_message_id and locals().get('turn_ctx') is not None:",
            "        _lark_event_message_id = turn_ctx.event_message_id",
            "    _lark_followup_sent = await on_queued_followup_boundary(",
            "        message_id=_lark_event_message_id, result=_lark_delivery_result",
            "    )",
            "    if _lark_followup_sent and _lark_delivery_result is not result:",
            "        result['response_previewed'] = True",
            "        result['already_sent'] = True",
            "        result['final_response'] = ''",
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
            "    else:",
            "        _lark_message_id = event_message_id",
            "        _lark_run_current = _run_still_current",
            "    if _lark_run_current() and event_type in ('tool.started', 'tool.completed'):",
            "        if on_tool_updated(",
            "            message_id=_lark_message_id,",
            "            tool_name=tool_name or '',",
            "            status='started' if event_type == 'tool.started' else 'completed',",
            "            detail=preview or '',",
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
            "    except NameError:",
            "        _lark_message_id = event_message_id",
            "        _lark_run_current = _run_still_current",
            "    if text and _lark_run_current() and on_answer_delta(message_id=_lark_message_id, text=text):",
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
            "    except NameError:",
            "        _lark_message_id = event_message_id",
            "        _lark_run_current = _run_still_current",
            "    if (text and not already_streamed and _lark_run_current()",
            "            and on_thinking_delta(message_id=_lark_message_id, text=text)):",
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
            "        except NameError:",
            "            _lark_message_id = event_message_id",
            "            _lark_run_current = _run_still_current",
            "        if text and _lark_run_current():",
            "            from hermes_fry_cards.patch import on_reasoning_delta",
            "            on_reasoning_delta(message_id=_lark_message_id, text=text)",
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
            "                anchor_id=_lark_next_anchor_id,",
            "                session_key=locals().get('next_session_key') or locals().get('session_key'),",
            "            )",
            "        elif _lark_was_interrupted:",
            "            on_message_aborted(message_id=_lark_event_message_id)",
            "        elif pending_event is not None and _lark_next_message_id:",
            "            on_message_started(",
            "                message_id=_lark_next_message_id,",
            "                chat_id=getattr(next_source, 'chat_id', source.chat_id),",
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
            "    _lark_platform_name = (",
            "        locals().get('platform_name')",
            "        or getattr(_lark_delivery, 'platform_name', '')",
            "        or _lark_target.get('platform', '')",
            "    )",
            "    _lark_chat_id = (",
            "        locals().get('chat_id')",
            "        or getattr(_lark_delivery, 'chat_id', '')",
            "        or _lark_target.get('chat_id', '')",
            "    )",
            "    _lark_transport = locals().get('transport') or getattr(_lark_delivery, 'transport', None)",
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
    "group_boundary": _group_boundary_hook,
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
            if set(modular_paths) != required:
                raise PatcherError(
                    "modular_paths must contain exactly: " + ", ".join(sorted(required))
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
            self.MARKERS = list(MARKERS) + [(MK_GROUP_BOUNDARY, MK_GROUP_BOUNDARY_END)]

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
        turn_content, turn_tree, turn_lines = parsed["turn"]
        _, runner_tree, runner_lines = parsed["runner"]
        _, busy_tree, busy_lines = parsed["busy"]
        checks = [
            (_find_handle_message_source_site(inbound_tree, inbound_lines), "normalize"),
            (_find_func_body(turn_tree, turn_lines, "_handle_message_with_agent"), "start"),
            (_find_handler_return(turn_tree, turn_lines), "complete"),
            (_find_followup_complete_site(turn_tree, turn_lines), "queued follow-up boundary"),
            (_find_followup_result_site(turn_tree, turn_lines), "queued follow-up return"),
            (_find_handler_abort(turn_tree, turn_lines), "abort"),
            (_find_interrupt_site(turn_tree, turn_lines), "interrupt"),
            (_find_bg_deliver_site(turn_tree, turn_lines), "background deliver"),
            (_find_stop_site(busy_tree, busy_lines), "stop"),
            (_find_func_body(runner_tree, runner_lines, "progress_callback"), "tool"),
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
        if 'hooks.emit("agent:end"' not in turn_content:
            raise PatcherError("Cannot find hooks.emit('agent:end', ...) in gateway/run_turn.py")

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
        specs: dict[str, list[tuple[str, str, object]]] = {
            "inbound": [("normalize", "normalize", _find_handle_message_source_site)],
            "turn": [
                (
                    "start",
                    "start",
                    lambda tree, lines: _find_func_body(tree, lines, "_handle_message_with_agent"),
                ),
                ("complete", "complete", _find_handler_return),
                ("followup_complete", "followup_complete", _find_followup_complete_site),
                ("followup_result", "followup_result", _find_followup_result_site),
                ("abort", "abort", _find_handler_abort),
                ("interrupt", "interrupt", _find_interrupt_site),
                ("bg_deliver", "bg_deliver", _find_bg_deliver_site),
            ],
            "runner": [
                (
                    "tool",
                    "tool",
                    lambda tree, lines: _find_func_body(tree, lines, "progress_callback"),
                ),
                (
                    "thinking",
                    "thinking",
                    lambda tree, lines: _find_func_body(tree, lines, "interim_assistant_cb"),
                ),
                ("reasoning", "reasoning", _find_reasoning_site),
                ("background_review", "background_review", _find_background_review_site),
                ("clarify", "clarify", _find_clarify_site),
                ("group_boundary", "group_boundary", _find_ephemeral_prompt_return),
            ],
            "busy": [("stop", "stop", _find_stop_site)],
        }
        result: dict[str, str] = {}
        for name, content in contents.items():
            tree = ast.parse(content)
            lines = content.splitlines(keepends=True)
            hook_defs = [(hook, label, finder(tree, lines)) for hook, label, finder in specs[name]]
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
            ("abort", "abort", _find_handler_abort(tree, lines)),
            ("stop", "stop", _find_stop_site(tree, lines)),
            ("interrupt", "interrupt", _find_interrupt_site(tree, lines)),
            ("tool", "tool", _find_func_body(tree, lines, "progress_callback")),
            ("thinking", "thinking", _find_func_body(tree, lines, "_interim_assistant_cb")),
            ("reasoning", "reasoning", _find_reasoning_site(tree, lines)),
            ("background_review", "background_review", _find_background_review_site(tree, lines)),
            ("bg_deliver", "bg_deliver", _find_bg_deliver_site(tree, lines)),
            ("clarify", "clarify", _find_clarify_site(tree, lines)),
        ]
        hook_defs.extend(
            ("answer", f"answer callback {index}", loc)
            for index, loc in enumerate(_find_func_bodies(tree, lines, "_stream_delta_cb"), start=1)
        )

        sites: list[tuple[int, str, str]] = []
        for hook_fn_name, name, loc in hook_defs:
            if loc is None:
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
    for i, line in enumerate(lines):
        if line.strip() == "return _preserve_queued_followup_history_offset(result, followup_result)":
            return i, _safe_indent(lines, i)
    return None


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
