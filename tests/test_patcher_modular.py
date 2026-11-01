"""Integration coverage for the Hermes 0.21+ split gateway layout."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_fry_cards.patcher import (
    MARKERS,
    MK_BG_BUSY_SUPPRESS,
    MK_BG_BUSY_SUPPRESS_END,
    MK_CRON_DELIVER,
    MK_FOLLOWUP_COMPLETE,
    MK_FOLLOWUP_COMPLETE_END,
    MK_FOLLOWUP_DONE,
    MK_GROUP_BOUNDARY,
    MK_GROUP_BOUNDARY_END,
    MK_TOOL_GATING,
    MK_TOOL_GATING_END,
    OPTIONAL_HOOKS,
    CronPatcher,
    Patcher,
    PatcherError,
)

MODULAR_SOURCES = {
    "inbound": """
        async def _handle_message(self, event):
            _admitted = await self._admit(event)
            event, source, is_internal = _admitted
            return source
    """,
    "turn": """
        async def _handle_message_with_agent(self, source, event, hooks, adapter, response):
            agent_result = {"failed": False}
            self._hmwa_discard_stale_result(agent_result)
            # Restart the typing indicator; the outer typing task may be stale
            pending_event = None
            next_message_id = None
            next_source = source
            images, text_content = adapter.extract_images(response)
            hooks.emit("agent:end", agent_result)
            _footer_line = self._hmwa_runtime_footer_line(agent_result)
            return response

        async def _run_agent_queued_followup(self, turn_ctx, result, response):
            if not result.get("interrupted"):
                response = result
            followup_result = result
            return _preserve_queued_followup_history_offset(result, followup_result)

        async def _run_agent_queued_followup_0211(self, turn_ctx, result, response, next_inbound_id):
            followup_result = await self._run_agent(
                message=next_message, context_prompt=turn_ctx.context_prompt, history=updated_history,
            )
            merged = _preserve_queued_followup_history_offset(result, followup_result)
            # 0.21.1: terminal turn ledger identity
            if isinstance(merged, dict) and "queued_terminal_inbound_id" not in merged:
                merged = {**merged, "queued_terminal_inbound_id": next_inbound_id}
            return merged
    """,
    "runner": """
        def configure(self, agent, reasoning_config):
            agent.reasoning_config, agent.service_tier = reasoning_config, None
            agent.background_review_callback, bg_release = None, None
            agent.clarify_callback = self._clarify_callback_sync

        def progress_callback(event_type, tool_name=None, preview=None):
            return event_type

        def interim_assistant_cb(text):
            return text

        def stream_delta_cb(text):
            return text

        def _combined_ephemeral_prompt(self):
            ctx = self._ctx
            combined = ctx.context_prompt or ""
            for extra in ((ctx.channel_prompt or "").strip(),):
                if extra:
                    combined = (combined + "\\n\\n" + extra).strip()
            return combined

        def _sync_session_after_run(self, agent_history):
            if session_was_split:
                logger.info("Session split detected: %s → %s (compression)", ctx.session_id, agent_session_id)
            return compacted_in_place, agent_session_id, offset
    """,
    "busy": """
        async def stop(self, session_key):
            await self._interrupt_and_clear_session(
                session_key,
                invalidation_reason="stop_command",
            )

        async def _handle_active_session_busy_message(self, event, session_key):
            effective_mode, redirected = _steer.effective_mode, _steer.redirected
            message = "Queued for the next turn"
            try:
                await adapter._send_with_retry(
                    chat_id=event.source.chat_id,
                    content=message,
                )
            except Exception:
                pass
            return True
    """,
    "notifications": """
        def _mark_internal_notification(text):
            return text

        class NotificationsMixin:
            async def _run_process_watcher(self, watcher):
                while True:
                    session_id = str(watcher.get("session_id") or "")
                    session = self._process_registry.get(session_id)
                    if session is None:
                        break
                    if session.exited:
                        completion_evt = {"session_id": session_id}
                        synth_text = self._format_process_notification(completion_evt)
                        turn_busy = await self._launching_turn_active(platform_name, watcher)
                        delivered = await self._enqueue_process_completion_notification(synth_text, completion_evt)
                        if delivered is not False:
                            if turn_busy:
                                message_text = self._format_process_final_message(session_id, session, "concise")
                                await self._send_watcher_message(
                                    platform_name, chat_id, thread_id, message_text, watcher,
                                )
                            break

            async def _inject_watch_notification(self, synth_text, evt):
                source = await self._build_process_event_source(evt)
                if not source:
                    return None
                platform_name = source.platform.value
                try:
                    synth_text = _mark_internal_notification(synth_text)
                    metadata = {}
                    synth_event = MessageEvent(text=synth_text, source=source, internal=True)
                    await admit_internal_event(adapter, synth_event)
                    return True
                except Exception as e:
                    return False
    """,
}

# Hermes 0.22 微模块重构（#38）：progress_callback 从 run_turn_runner.py 迁移至独立的
# run_turn_progress.py，hooks.emit("agent:end") 从 run_turn.py 迁移至 run_turn_persistence.py。
# runner 里没有 progress_callback、turn 里没有 agent:end —— 锚点必须跟着迁到新模块。
MODULAR_022_OVERRIDES = {
    "turn": """
        async def _handle_message_with_agent(self, source, event, hooks, adapter, response):
            agent_result = {"failed": False}
            self._hmwa_discard_stale_result(agent_result)
            # Restart the typing indicator; the outer typing task may be stale
            pending_event = None
            next_message_id = None
            next_source = source
            images, text_content = adapter.extract_images(response)
            _footer_line = self._hmwa_runtime_footer_line(agent_result)
            return response

        def _resolve_turn_toolsets(self, user_config, source, platform_key):
            enabled = list(user_config.get("enabled_toolsets") or [])
            disabled = list(user_config.get("disabled_toolsets") or [])
            return enabled, disabled

        async def _run_agent_queued_followup(self, turn_ctx, result, response):
            if not result.get("interrupted"):
                response = result
            followup_result = result
            return _preserve_queued_followup_history_offset(result, followup_result)
    """,
    "runner": """
        def configure(self, agent, reasoning_config):
            agent.reasoning_config, agent.service_tier = reasoning_config, None
            agent.background_review_callback, bg_release = None, None
            agent.clarify_callback = self._clarify_callback_sync

        def interim_assistant_cb(text):
            return text

        def stream_delta_cb(text):
            return text

        def _combined_ephemeral_prompt(self):
            ctx = self._ctx
            combined = ctx.context_prompt or ""
            for extra in ((ctx.channel_prompt or "").strip(),):
                if extra:
                    combined = (combined + "\\n\\n" + extra).strip()
            return combined

        def _sync_session_after_run(self, agent_history):
            if session_was_split:
                logger.info("Session split detected: %s → %s (compression)", ctx.session_id, agent_session_id)
            return compacted_in_place, agent_session_id, offset
    """,
}

PROGRESS_SOURCE_022 = """
    class TurnProgressMixin:
        def progress_callback(event_type, tool_name=None, preview=None):
            return event_type
"""

PERSISTENCE_SOURCE_022 = """
    class TurnPersistenceMixin:
        async def persist_turn(self, hooks, agent_result):
            hooks.emit("agent:end", agent_result)
            return agent_result
"""


@pytest.fixture()
def modular_022_copy(tmp_path: Path) -> dict[str, Path]:
    """0.21 基线布局 + progress/persistence 微模块：tool 与 agent:end 已迁移。"""
    copied: dict[str, Path] = {}
    sources = {**MODULAR_SOURCES, **MODULAR_022_OVERRIDES}
    for name, content in sources.items():
        destination = tmp_path / f"run_{name}.py"
        destination.write_text(textwrap.dedent(content), encoding="utf-8")
        copied[name] = destination
    progress = tmp_path / "run_turn_progress.py"
    progress.write_text(textwrap.dedent(PROGRESS_SOURCE_022), encoding="utf-8")
    copied["progress"] = progress
    persistence = tmp_path / "run_turn_persistence.py"
    persistence.write_text(textwrap.dedent(PERSISTENCE_SOURCE_022), encoding="utf-8")
    copied["persistence"] = persistence
    return copied


def test_modular_022_verify_accepts_migrated_anchors(modular_022_copy: dict[str, Path]) -> None:
    """#38: tool 锚点在 progress、agent:end 在 persistence 时 verify 必须通过。"""
    Patcher(modular_paths=modular_022_copy).verify_target()


def test_modular_022_verify_fails_without_any_host(modular_copy: dict[str, Path]) -> None:
    """runner 缺 progress_callback 且无 progress 模块时，verify 必须点名 tool 缺失。

    0.22 布局若被错误地只传 4 个基础模块（发现逻辑失效），不能静默通过。
    """
    broken = dict(modular_copy)
    content = broken["runner"].read_text(encoding="utf-8")
    broken["runner"].write_text(
        "\n".join(
            line for line in content.splitlines()
            if "progress_callback" not in line and "return event_type" not in line
        ) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(PatcherError, match="tool"):
        Patcher(modular_paths=broken).verify_target()


def test_modular_022_apply_installs_tool_hook_in_progress_module(modular_022_copy: dict[str, Path]) -> None:
    """TOOL 钩子必须落在 progress 模块，persistence 不写回；幂等与可逆同样成立。"""
    originals = {name: path.read_text(encoding="utf-8") for name, path in modular_022_copy.items()}
    patcher = Patcher(modular_paths=modular_022_copy)

    patcher.apply()
    assert patcher.is_fully_patched()

    contents = {name: path.read_text(encoding="utf-8") for name, path in modular_022_copy.items()}
    assert "HERMES_LARK_TOOL_BEGIN" in contents["progress"]
    assert "HERMES_LARK_TOOL_BEGIN" not in contents["runner"]
    assert "HERMES_LARK_NORMALIZE_BEGIN" in contents["inbound"]
    assert "HERMES_LARK_STOP_BEGIN" in contents["busy"]
    # #43：忙碌回执入卡 / 重定向换卡（可选锚点）
    assert "HERMES_LARK_BUSY_ACK_BEGIN" in contents["busy"]
    assert "HERMES_LARK_REDIRECT_BEGIN" in contents["busy"]
    assert "HERMES_LARK_REDIRECT_RESOLVED_BEGIN" in contents["busy"]
    assert "HERMES_LARK_BG_COMPLETION_BEGIN" in contents["notifications"]
    assert "HERMES_LARK_TOOL_GATING_BEGIN" in contents["turn"]
    for begin, _end in [
        *MARKERS,
        (MK_GROUP_BOUNDARY, MK_GROUP_BOUNDARY_END),
        (MK_TOOL_GATING, MK_TOOL_GATING_END),
    ]:
        total = sum(content.count(begin) for content in contents.values())
        assert total == 1, f"{begin} installed {total}x"
    for path in modular_022_copy.values():
        ast.parse(path.read_text(encoding="utf-8"))

    # persistence 没有注入点：apply 不写回它（内容与 progress 的注入互相独立）。
    assert contents["persistence"] == originals["persistence"]

    patcher.apply()
    assert contents == {name: p.read_text(encoding="utf-8") for name, p in modular_022_copy.items()}

    patcher.remove()
    assert originals == {name: p.read_text(encoding="utf-8") for name, p in modular_022_copy.items()}


def test_modular_paths_accepts_optional_and_rejects_unknown(tmp_path: Path) -> None:
    """modular_paths 允许可选模块（progress/persistence），拒绝拼写错误的模块名。"""
    base = {
        name: tmp_path / f"run_{name}.py"
        for name in ("inbound", "turn", "runner", "busy")
    }
    for path in base.values():
        path.write_text("# stub\n", encoding="utf-8")
    # 缺 progress/persistence 的 0.21 布局仍合法
    Patcher(modular_paths=dict(base))
    with pytest.raises(PatcherError, match="unknown modules"):
        Patcher(modular_paths={**base, "presistense": tmp_path / "typo.py"})
    with pytest.raises(PatcherError, match="at least"):
        missing = {k: v for k, v in base.items() if k != "busy"}
        Patcher(modular_paths=missing)


CRON_SOURCE = """
    def deliver(job, targets, cleaned_delivery_content, loop, adapters, config):
        delivery_errors = []
        for target in targets:
            t = prepare(target)
            if t is None:
                continue
            target_errors: list = []
            delivered = send(t, cleaned_delivery_content)
            if not delivered:
                fallback(t)
        return delivery_errors
"""


@pytest.fixture()
def modular_copy(tmp_path: Path) -> dict[str, Path]:
    copied = {}
    for name, content in MODULAR_SOURCES.items():
        destination = tmp_path / f"run_{name}.py"
        destination.write_text(textwrap.dedent(content), encoding="utf-8")
        copied[name] = destination
    return copied


def test_modular_apply_is_complete_idempotent_and_reversible(modular_copy: dict[str, Path]) -> None:
    originals = {name: path.read_text(encoding="utf-8") for name, path in modular_copy.items()}
    patcher = Patcher(modular_paths=modular_copy)

    patcher.verify_target()
    patcher.apply()
    assert patcher.is_fully_patched()

    combined = "\n".join(path.read_text(encoding="utf-8") for path in modular_copy.values())
    for begin, end in MARKERS:
        assert combined.count(begin) == 1
        assert combined.count(end) == 1
    for path in modular_copy.values():
        ast.parse(path.read_text(encoding="utf-8"))
        assert path.with_suffix(path.suffix + ".hermes_lark.bak").exists()

    first = {name: path.read_text(encoding="utf-8") for name, path in modular_copy.items()}
    patcher.apply()
    assert first == {name: path.read_text(encoding="utf-8") for name, path in modular_copy.items()}

    patcher.remove()
    assert originals == {name: path.read_text(encoding="utf-8") for name, path in modular_copy.items()}


def test_modular_marker_distribution(modular_copy: dict[str, Path]) -> None:
    patcher = Patcher(modular_paths=modular_copy)
    patcher.apply()
    contents = {name: path.read_text(encoding="utf-8") for name, path in modular_copy.items()}

    assert "HERMES_LARK_NORMALIZE_BEGIN" in contents["inbound"]
    for marker in (
        "START", "COMPLETE", "FOLLOWUP_COMPLETE", "FOLLOWUP_RESULT", "FOLLOWUP_DONE",
        "ABORT", "INTERRUPT", "BG_DELIVER",
    ):
        assert f"HERMES_LARK_{marker}_BEGIN" in contents["turn"]
    for marker in ("TOOL", "ANSWER", "THINKING", "REASONING", "BACKGROUND_REVIEW", "CLARIFY", "COMPRESSION"):
        assert f"HERMES_LARK_{marker}_BEGIN" in contents["runner"]
    assert "HERMES_LARK_STOP_BEGIN" in contents["busy"]
    # #43：忙碌回执入卡 / 重定向换卡（可选锚点）
    assert "HERMES_LARK_BUSY_ACK_BEGIN" in contents["busy"]
    assert "HERMES_LARK_REDIRECT_BEGIN" in contents["busy"]
    # 重定向判定点（防抖前换卡，#43 反馈）
    assert "HERMES_LARK_REDIRECT_RESOLVED_BEGIN" in contents["busy"]
    # 后台完成通知接管（#47 可选锚点）
    assert "HERMES_LARK_BG_COMPLETION_BEGIN" in contents["notifications"]


def test_modular_followup_done_seals_card_before_merge(modular_copy: dict[str, Path]) -> None:
    """#35: the queued follow-up's own card is sealed before the chain merges its result.

    The follow-up turn runs a recursive ``_run_agent`` that never re-enters
    ``_handle_message_with_agent``, so the COMPLETE hook never fires for it. The new hook
    therefore anchors on the ``_preserve_queued_followup_history_offset`` line and must land
    BEFORE it: ``followup_result`` is already bound above, but the merged value handed back
    to the outer handler is what carries ``already_sent`` and suppresses the plain text.
    """
    patcher = Patcher(modular_paths=modular_copy)
    patcher.apply()
    lines = modular_copy["turn"].read_text(encoding="utf-8").splitlines()

    done = next(i for i, line in enumerate(lines) if MK_FOLLOWUP_DONE in line)
    merge = next(
        i for i, line in enumerate(lines)
        if "_preserve_queued_followup_history_offset(result, followup_result)" in line
        and not line.strip().startswith("# HERMES_LARK")
    )
    assert done < merge, "FOLLOWUP_DONE must be injected before the merge line"

    body = "\n".join(lines[done:merge])
    assert "on_queued_followup_completed" in body, "hook must seal the follow-up turn's card"
    assert "already_sent" in body and "final_response" in body, "must suppress the plain-text send"
    # An event-less follow-up carries no inbound id; it must reuse the card the boundary
    # pre-created for this turn (falling back to creating one when there is none). (#37)
    assert "on_queued_followup_card_ready" in body and "message_id=None" in body


def test_modular_boundary_precreates_followup_card_before_recursion(modular_copy: dict[str, Path]) -> None:
    """#37: the boundary hook pre-creates the follow-up turn's card before the recursion.

    Without a card at stream time Hermes streams that turn's reply as plain text, and the
    completion hook then adds a card carrying the same text — the reply lands twice. The
    pre-create must land AFTER sealing the previous card and BEFORE the recursive
    ``_run_agent`` call, keyed by the follow-up's own session.
    """
    patcher = Patcher(modular_paths=modular_copy)
    patcher.apply()
    lines = modular_copy["turn"].read_text(encoding="utf-8").splitlines()

    complete = next(i for i, line in enumerate(lines) if MK_FOLLOWUP_COMPLETE in line)
    complete_end = next(i for i, line in enumerate(lines) if MK_FOLLOWUP_COMPLETE_END in line)
    bound = next(i for i, line in enumerate(lines) if "followup_result = await self._run_agent(" in line)

    body = "\n".join(lines[complete:complete_end])
    assert "on_queued_followup_card_ready" in body, "boundary must pre-create the follow-up card"
    assert "on_queued_followup_boundary" in body, "boundary must still seal the previous card"
    assert body.index("on_queued_followup_boundary") < body.index("on_queued_followup_card_ready"), (
        "seal the previous card first, then pre-create this turn's"
    )
    assert complete < bound, "pre-create must land before the recursive _run_agent call"
    assert "self._session_key_for_source" in body, "pre-create must key the card by the follow-up's session"


def test_modular_cron_apply_is_idempotent_and_reversible(tmp_path: Path) -> None:
    copied = tmp_path / "scheduler_delivery.py"
    copied.write_text(textwrap.dedent(CRON_SOURCE), encoding="utf-8")
    original = copied.read_text(encoding="utf-8")
    patcher = CronPatcher(cron_path=copied)

    patcher.verify_target()
    patcher.apply()
    first = copied.read_text(encoding="utf-8")
    assert MK_CRON_DELIVER in first
    ast.parse(first)

    patcher.apply()
    assert copied.read_text(encoding="utf-8") == first
    patcher.remove()
    assert copied.read_text(encoding="utf-8") == original


def test_busy_ack_hook_merges_receipt_and_suppresses_plain_text(modular_copy: dict[str, Path]) -> None:
    """#43: 忙碌回执写入当前卡并吞掉 Hermes 纯文本（返回 True 即消费）。"""
    patcher = Patcher(modular_paths=modular_copy)
    patcher.apply()
    busy = modular_copy["busy"].read_text(encoding="utf-8")

    begin = busy.index("HERMES_LARK_BUSY_ACK_BEGIN")
    end = busy.index("HERMES_LARK_BUSY_ACK_END")
    block = busy[begin:end]
    assert "from hermes_fry_cards.patch import on_busy_ack" in block
    assert "text=message" in block
    assert "return True" in block
    assert "injected hook failed: busy_ack" in block
    # 注入点落在回执发送语句之前（占位回执前的逻辑），且仍在 try 体内
    ack = busy.index("HERMES_LARK_BUSY_ACK_BEGIN")
    assert ack < busy.index("_send_with_retry(")
    assert busy.index("try:", busy.index("async def _handle_active_session_busy_message")) < ack


def test_redirect_hook_rebuilds_card_before_notice(modular_copy: dict[str, Path]) -> None:
    """#43: 重定向先经 on_interrupted 换卡，再写提示。"""
    patcher = Patcher(modular_paths=modular_copy)
    patcher.apply()
    busy = modular_copy["busy"].read_text(encoding="utf-8")

    block = busy[busy.index("HERMES_LARK_REDIRECT_BEGIN"):busy.index("HERMES_LARK_REDIRECT_END")]
    assert "from hermes_fry_cards.patch import on_turn_redirected" in block
    assert "new_message_id=event.message_id" in block
    assert "injected hook failed: redirect" in block


def test_bg_completion_hook_intercepts_before_internal_marker(modular_copy: dict[str, Path]) -> None:
    """#47: 完成通知接管挂在内部标记处理之前——接管时跳过整条合成消息注入."""
    patcher = Patcher(modular_paths=modular_copy)
    patcher.apply()
    notifications = modular_copy["notifications"].read_text(encoding="utf-8")

    begin = notifications.index("HERMES_LARK_BG_COMPLETION_BEGIN")
    end = notifications.index("HERMES_LARK_BG_COMPLETION_END")
    marker_line = notifications.index("_mark_internal_notification(synth_text)")
    assert begin < end < marker_line, "hook must be injected before the internal-marker line"

    block = notifications[begin:end]
    assert "from hermes_fry_cards.patch import on_background_completion" in block
    assert "session_key=(str(_lark_evt_key) if _lark_evt_key else None)" in block
    assert "return True" in block
    assert "injected hook failed: bg_completion" in block


def test_bg_completion_hook_passes_evt_display_fields(modular_copy: dict[str, Path]) -> None:
    """回执文案要用 evt 的 command / started_at / exit_code 自己拼，不再甩内部文案."""
    patcher = Patcher(modular_paths=modular_copy)
    patcher.apply()
    notifications = modular_copy["notifications"].read_text(encoding="utf-8")

    block = notifications[
        notifications.index("HERMES_LARK_BG_COMPLETION_BEGIN"):
        notifications.index("HERMES_LARK_BG_COMPLETION_END")
    ]
    assert "command=_lark_evt.get('command')," in block
    assert "started_at=_lark_evt.get('started_at')," in block
    assert "exit_code=_lark_evt.get('exit_code')," in block
    # evt 不是 dict 时不能炸（_inject_watch_notification 也接受别的映射实现）
    assert "_lark_evt = evt if isinstance(evt, dict) else {}" in block


def test_bg_busy_suppress_hook_lands_before_concise_receipt(modular_copy: dict[str, Path]) -> None:
    """turn_busy 纯文本回执的抑制点：concise 文案之后、``_send_watcher_message`` 之前."""
    patcher = Patcher(modular_paths=modular_copy)
    patcher.apply()
    notifications = modular_copy["notifications"].read_text(encoding="utf-8")

    begin = notifications.index(MK_BG_BUSY_SUPPRESS)
    end = notifications.index(MK_BG_BUSY_SUPPRESS_END)
    concise = notifications.index('_format_process_final_message(session_id, session, "concise")')
    send = notifications.index("_send_watcher_message(")
    assert concise < begin < end < send, "钩子必须落在 concise 文案之后、纯文本发送之前"

    block = notifications[begin:end]
    assert "from hermes_fry_cards.patch import suppress_busy_receipt" in block
    assert "session_id=str(session_id or '')" in block
    assert "started_at=getattr(session, 'started_at', None)" in block
    assert "platform_name=platform_name" in block
    assert "break" in block
    assert "injected hook failed: bg_busy_suppress" in block
    ast.parse(notifications)


def test_bg_busy_suppress_hook_matches_watch_process_host(modular_copy: dict[str, Path]) -> None:
    """Hermes 0.22.5+1713 中 _run_process_watcher 改为 wrapper，逻辑移入 _watch_process，锚点仍要命中."""
    content = modular_copy["notifications"].read_text(encoding="utf-8")
    # 将函数名 _run_process_watcher 替换为 _watch_process 模拟最新 Hermes 源码
    content = content.replace("async def _run_process_watcher(", "async def _watch_process(")
    modular_copy["notifications"].write_text(content, encoding="utf-8")

    patcher = Patcher(modular_paths=modular_copy)
    patcher.apply()

    notifications = modular_copy["notifications"].read_text(encoding="utf-8")
    assert MK_BG_BUSY_SUPPRESS in notifications
    begin = notifications.index(MK_BG_BUSY_SUPPRESS_END)
    concise = notifications.index('_format_process_final_message(session_id, session, "concise")')
    assert concise < begin
    ast.parse(notifications)


def test_bg_busy_suppress_hook_is_optional(modular_copy: dict[str, Path]) -> None:
    """通知模块没有该锚点时跳过注入，不让整次安装失败（旧布局兼容）."""
    assert "bg_busy_suppress" in OPTIONAL_HOOKS
    stripped = modular_copy["notifications"].read_text(encoding="utf-8")
    stripped = "\n".join(
        line for line in stripped.splitlines()
        if '"_concise"' not in line and "concise" not in line
    ) + "\n"
    modular_copy["notifications"].write_text(stripped, encoding="utf-8")

    patcher = Patcher(modular_paths=modular_copy)
    patcher.apply()

    patched = modular_copy["notifications"].read_text(encoding="utf-8")
    assert MK_BG_BUSY_SUPPRESS not in patched
    assert patcher.is_fully_patched()


def test_optional_hooks_are_skipped_when_anchor_missing(tmp_path: Path) -> None:
    """#43: 旧版 Hermes 的 busy 模块没有回执回调时，跳过注入而不是让安装失败。"""
    paths = {}
    for name, content in MODULAR_SOURCES.items():
        if name == "busy":
            content = """
        async def stop(self, session_key):
            await self._interrupt_and_clear_session(
                session_key,
                invalidation_reason="stop_command",
            )
    """
        destination = tmp_path / f"run_{name}.py"
        destination.write_text(textwrap.dedent(content), encoding="utf-8")
        paths[name] = destination

    patcher = Patcher(modular_paths=paths)
    patcher.verify_target()
    patcher.apply()

    busy = paths["busy"].read_text(encoding="utf-8")
    assert "HERMES_LARK_STOP_BEGIN" in busy
    assert "HERMES_LARK_BUSY_ACK_BEGIN" not in busy
    assert "HERMES_LARK_REDIRECT_BEGIN" not in busy
    assert patcher.is_fully_patched()


def test_bg_busy_suppress_block_skips_plain_receipt_at_runtime(modular_copy: dict[str, Path]) -> None:
    """注入块的行为：命中 → 不发纯文本（break）；未命中 → 照常发一条."""
    import asyncio

    import hermes_fry_cards.patch as patch_module

    Patcher(modular_paths=modular_copy).apply()
    source = modular_copy["notifications"].read_text(encoding="utf-8")
    namespace: dict = {
        "platform_name": "feishu",
        "chat_id": "oc_chat",
        "thread_id": "",
        "logger": None,
    }
    exec(compile(source, "<bg-busy-suppress>", "exec"), namespace)
    watcher_cls = namespace["NotificationsMixin"]

    sent: list[tuple] = []

    class Session:
        exited = True
        started_at = 1000.0

    class Registry:
        @staticmethod
        def get(_sid):
            return Session()

    class Host(watcher_cls):
        _process_registry = Registry()

        async def _launching_turn_active(self, _platform, _watcher):
            return True

        async def _enqueue_process_completion_notification(self, _text, _evt):
            return True

        def _format_process_notification(self, _evt):
            return "internal"

        def _format_process_final_message(self, _sid, _session, _mode):
            return "concise receipt"

        async def _send_watcher_message(self, *args, **kwargs):
            sent.append(args)

    watcher = {"session_id": "proc_runtime"}

    original = patch_module.suppress_busy_receipt
    try:
        patch_module.suppress_busy_receipt = lambda **_kwargs: False
        asyncio.run(Host()._run_process_watcher(watcher))
        assert len(sent) == 1, "未命中时必须照常发纯文本回执"

        sent.clear()
        patch_module.suppress_busy_receipt = lambda **_kwargs: True
        asyncio.run(Host()._run_process_watcher(watcher))
        assert sent == [], "命中时必须跳过纯文本回执"
    finally:
        patch_module.suppress_busy_receipt = original


def test_tool_gating_block_filters_turn_toolsets_at_runtime(
    modular_022_copy: dict[str, Path], monkeypatch
) -> None:
    """注入块的端到端行为：非管理员群聊轮次的工具集被过滤，管理员/私聊原样（#55）."""
    import sys
    import types

    from hermes_fry_cards.patcher import Patcher as _P

    _P(modular_paths=modular_022_copy).apply()
    source = modular_022_copy["turn"].read_text(encoding="utf-8")
    assert "HERMES_LARK_TOOL_GATING_BEGIN" in source
    namespace: dict = {}
    exec(compile(source, "<tool-gating>", "exec"), namespace)
    resolve = namespace["_resolve_turn_toolsets"]

    module = types.ModuleType("gateway.slash_access")
    module.policy_for_source = lambda cfg, src: SimpleNamespace(
        enabled=True, is_admin=lambda uid: uid == "ou_admin"
    )
    parent = types.ModuleType("gateway")
    parent.slash_access = module
    monkeypatch.setitem(sys.modules, "gateway", parent)
    monkeypatch.setitem(sys.modules, "gateway.slash_access", module)

    gsb = {
        "enabled": True,
        "non_admin_allowed_toolsets": ["web", "terminal"],
        "non_admin_disabled_toolsets": ["terminal"],
    }
    user_config = {
        "gateway": {"group_security_boundary": gsb},
        "enabled_toolsets": ["terminal", "file", "web"],
        "disabled_toolsets": ["memory"],
    }

    class Runner:
        config = SimpleNamespace()

    def _src(chat_type="group", user_id="ou_user"):
        return SimpleNamespace(chat_type=chat_type, chat_id="oc_g", user_id=user_id)

    # 非管理员群聊：白名单交集 + 黑名单剔除，denied 并入 disabled
    enabled, disabled = resolve(Runner(), user_config, _src(), "feishu")
    assert enabled == ["web"]
    assert "terminal" in disabled and "memory" in disabled

    # 管理员：原样下发
    enabled, disabled = resolve(Runner(), user_config, _src(user_id="ou_admin"), "feishu")
    assert enabled == ["terminal", "file", "web"]
    assert disabled == ["memory"]

    # 私聊：原样下发
    enabled, disabled = resolve(Runner(), user_config, _src(chat_type="dm"), "feishu")
    assert enabled == ["terminal", "file", "web"]
