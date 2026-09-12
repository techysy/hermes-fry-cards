"""Reproduce issue #3 — 300313 recovery is skipped when tool_panel_created=True.

Theory (from the issue):
  When the tool panel was pre-created by the card skeleton
  (build_streaming_card_v2(show_tool_use=True) → tool_panel_created=True) and
  it later goes stale on the server, the recovery path is skipped:

    - the next flush enters the `not seg.created` TOOL branch
    - because tool_panel_created is already True it falls into the `else`
      branch (controller ~L288-291), which only flips
      seg.created=True / dirty=False and emits NO rebuild action

  → the element is still missing on the card, but locally marked created, so
    no further recovery is attempted.

This module pins the CURRENT behaviour so the gap is visible and regression-
proof; flip the assertion when the fix lands.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from hermes_fry_cards.feishu import FeishuAPIError
from hermes_fry_cards.streaming.segment_helper import estimate_segment_elements
from hermes_fry_cards.streaming.session import CardSession, SessionState

from test_controller import _enable, _mock_client


def _setup_ctrl():
    from hermes_fry_cards.controller import StreamCardController

    ctrl = StreamCardController()
    _enable(ctrl)
    ctrl._initialized = True
    ctrl._client = _mock_client()
    return ctrl


@pytest.mark.asyncio
async def test_issue3_gap_tool_panel_skeleton_stale_recovery_skipped() -> None:
    """ISSUE #3: with tool_panel_created=True the re-add path is skipped."""
    ctrl = _setup_ctrl()
    client = ctrl._client

    session = CardSession("msg_issue3", "chat_x", asyncio.get_running_loop())
    session.state = SessionState.STREAMING
    session.card_id = "card_issue3"
    session.card_msg_id = "msg_issue3_card"
    # KEY PRECONDITION: skeleton pre-created the tool panel.
    session.tool_panel_created = True

    session.tool_use.record_start("read", "file0")
    session.segment_state.on_tool_event(1)
    tool_seg = session.segment_state.segments[0]
    tool_seg.created = True
    tool_seg.element_estimate = estimate_segment_elements(
        tool_seg, session.tool_use.build_display_steps()
    )
    session.element_count = 0  # skeleton panel is not counted
    ctrl._sessions["msg_issue3"] = session

    stale_el_id = tool_seg.el_id
    client.cardkit_batch_update = AsyncMock(
        side_effect=[
            FeishuAPIError(
                f"cardkit_batch_update: code=300313, msg=ErrMsg: not find elementID : {stale_el_id};",
                300313,
            ),
            None,
            None,
        ]
    )

    # Trigger dirty → flush takes the tool-update branch → 300313.
    session.tool_use.record_start("read", "file1")
    session.segment_state.on_tool_event(len(session.tool_use.build_display_steps()))

    await ctrl._do_flush(session)

    # 300313 rolled the segment back, as designed.
    assert tool_seg.created is False, "300313 must roll the stale segment back"
    assert tool_seg.dirty is True

    # ---- the recovery flush (this is where issue #3 lived) ----
    calls_before = client.cardkit_batch_update.await_count
    await ctrl._do_flush(session)
    calls_after = client.cardkit_batch_update.await_count

    assert tool_seg.created is True
    assert tool_seg.dirty is False

    # FIXED (issue #3): resetting tool_panel_created makes the next flush take
    # the "first create" branch and actually re-issue the panel update, instead
    # of silently flipping the flags while the card stays broken.
    assert calls_after == calls_before + 1, (
        "recovery flush must re-issue the panel rebuild "
        f"(got {calls_after - calls_before} batch_update call(s))"
    )
    actions = client.cardkit_batch_update.call_args_list[-1][0][1]
    panel_actions = [
        a for a in actions
        if "tool_panel" in str(a) or stale_el_id in str(a)
    ]
    assert panel_actions, (
        f"recovery flush emitted no action touching the stale panel: {actions}"
    )
