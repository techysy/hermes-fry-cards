"""Integration coverage for the Hermes 0.21+ split gateway layout."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from hermes_fry_cards.patcher import (
    MARKERS,
    MK_CRON_DELIVER,
    CronPatcher,
    Patcher,
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
    """,
    "busy": """
        async def stop(self, session_key):
            await self._interrupt_and_clear_session(
                session_key,
                invalidation_reason="stop_command",
            )
    """,
}

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
    for marker in ("START", "COMPLETE", "FOLLOWUP_COMPLETE", "FOLLOWUP_RESULT", "ABORT", "INTERRUPT", "BG_DELIVER"):
        assert f"HERMES_LARK_{marker}_BEGIN" in contents["turn"]
    for marker in ("TOOL", "ANSWER", "THINKING", "REASONING", "BACKGROUND_REVIEW", "CLARIFY"):
        assert f"HERMES_LARK_{marker}_BEGIN" in contents["runner"]
    assert "HERMES_LARK_STOP_BEGIN" in contents["busy"]


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
