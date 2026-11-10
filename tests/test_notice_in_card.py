"""Issue #43 — 提示消息入卡（忙碌回执 / 重定向 / 后台回执）.

Hermes 在回合进行中会自己合成状态提示（"⏳ Queued for the next turn…" 等）。
本模块钉住重写后的行为：提示写进**当前活跃卡**的通知槽，随完成态卡片一起
呈现，插件不另写文案；卡片归属沿用现有 ``_sessions`` / ``_session_keys`` /
``_interrupt_map``，不做归属重构。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from test_controller import _make_session, _mock_client, _setup_ctrl

from hermes_fry_cards.cardkit.builder import build_complete_card, build_notice_element
from hermes_fry_cards.streaming.segments import NotificationSlot, SegmentState
from hermes_fry_cards.streaming.session import SessionState

BUSY_TEXT = "⏳ Queued for the next turn. I'll respond once the current task finishes."
REDIRECT_TEXT = "↪ Steered into current run"


def _streaming(ctrl, message_id: str, *, chat_id: str = "chat_1", key: str | None = None):
    session = _make_session(message_id)
    session.chat_id = chat_id
    session.state = SessionState.STREAMING
    session.card_id = f"card_{message_id}"
    session.card_msg_id = f"msg_{message_id}"
    ctrl._register_session(session, session_key=key)
    return session


# ── 通知槽（数据层） ────────────────────────────────────────────────────────


def test_add_notice_dedupes_and_ignores_blank() -> None:
    """单槽位：同文案重复调用是 no-op，空文案忽略，不同文案替换前一条."""
    state = SegmentState()
    state.add_notice(BUSY_TEXT)
    state.add_notice(f"  {BUSY_TEXT}  ")
    state.add_notice("   ")
    assert [slot.text for slot in state.notices] == [BUSY_TEXT]
    assert isinstance(state.notices[0], NotificationSlot)

    # 不同文案：后到的覆盖先到的，槽里始终只有一条
    state.add_notice(REDIRECT_TEXT)
    assert [slot.text for slot in state.notices] == [REDIRECT_TEXT]


def test_complete_card_renders_notices_above_body() -> None:
    card = build_complete_card(segments=[], all_tool_steps=[], notices=[BUSY_TEXT, REDIRECT_TEXT])
    elements = card["body"]["elements"]
    assert elements[0]["element_id"] == "fry_notice"
    assert elements[0]["content"] == BUSY_TEXT
    assert elements[1]["content"] == REDIRECT_TEXT


def test_complete_card_without_notices_has_no_notice_elements() -> None:
    card = build_complete_card(segments=[], all_tool_steps=[])
    assert not [el for el in card["body"]["elements"] if el.get("element_id") == "fry_notice"]


def test_notice_element_renders_raw_text_without_label() -> None:
    """#43 反馈：重定向/忙碌文案本身是完整一句话，不再套「💡 提示」标签."""
    element = build_notice_element(REDIRECT_TEXT)
    assert element["content"] == REDIRECT_TEXT
    assert "💡" not in element["content"]
    assert "提示" not in element["content"]


# ── 忙碌回执 ────────────────────────────────────────────────────────────────


def test_busy_ack_goes_into_current_card() -> None:
    ctrl = _setup_ctrl()
    session = _streaming(ctrl, "msg_busy", key="key_busy")

    assert ctrl.on_busy_ack(text=BUSY_TEXT, message_id="msg_busy", session_key="key_busy") is True
    assert [slot.text for slot in session.segment_state.notices] == [BUSY_TEXT]


def test_busy_ack_falls_back_by_session_key_without_message_id() -> None:
    ctrl = _setup_ctrl()
    session = _streaming(ctrl, "msg_busy_key", key="key_only")

    assert ctrl.on_busy_ack(text=BUSY_TEXT, session_key="key_only") is True
    assert session.segment_state.notices


def test_busy_ack_without_owner_is_not_consumed() -> None:
    ctrl = _setup_ctrl()
    assert ctrl.on_busy_ack(text=BUSY_TEXT, message_id="msg_unknown", session_key="key_unknown") is False


def test_busy_ack_ambiguous_without_owner_is_not_consumed() -> None:
    """多张活跃卡且无标识：宁可走 Hermes 原发送，也不误写别人的卡."""
    ctrl = _setup_ctrl()
    first = _streaming(ctrl, "msg_a")
    second = _streaming(ctrl, "msg_b")
    assert ctrl.on_busy_ack(text=BUSY_TEXT) is False
    assert not first.segment_state.notices
    assert not second.segment_state.notices


def test_busy_ack_single_live_card_is_used() -> None:
    ctrl = _setup_ctrl()
    session = _streaming(ctrl, "msg_solo")
    assert ctrl.on_busy_ack(text=BUSY_TEXT) is True
    assert session.segment_state.notices


def test_busy_ack_ignores_blank_text() -> None:
    ctrl = _setup_ctrl()
    _streaming(ctrl, "msg_blank")
    assert ctrl.on_busy_ack(text="   ") is False


# ── 重定向 ──────────────────────────────────────────────────────────────────


def test_redirect_closes_old_card_and_opens_new_one() -> None:
    ctrl = _setup_ctrl()
    old = _streaming(ctrl, "msg_old", key="key_turn")
    ctrl._complete_session = lambda s: s.__setattr__("state", SessionState.ABORTED)  # type: ignore[method-assign]

    ctrl.on_interrupted(
        old_message_id="msg_old",
        new_message_id="msg_new",
        chat_id="chat_1",
        session_key="key_turn",
    )
    new = ctrl._sessions["msg_new"]
    new.segment_state = SegmentState()
    ctrl.on_redirected_notice(text=REDIRECT_TEXT, message_id="msg_new", session_key="key_turn")

    assert old.state == SessionState.ABORTED
    assert [slot.text for slot in new.segment_state.notices] == [REDIRECT_TEXT]
    # 完成回调仍按 _interrupt_map 落到新卡
    assert ctrl._completion_session("msg_old") is new


def test_redirect_notice_before_new_card_arrives_is_applied_on_register() -> None:
    """提示先于新卡登记到达（重定向开口与建卡并发）：注册时补写."""
    ctrl = _setup_ctrl()
    running = _streaming(ctrl, "msg_running", key="key_pending")
    ctrl._complete_session = lambda s: s.__setattr__("state", SessionState.ABORTED)  # type: ignore[method-assign]
    # 模拟换卡开口：旧卡已收尾但 session_key 尚未指向新卡（新卡还没登记）
    running.state = SessionState.ABORTED
    ctrl._session_keys.pop("key_pending", None)

    assert ctrl.on_redirected_notice(text=REDIRECT_TEXT, session_key="key_pending") is True
    assert ctrl._pending_notices["key_pending"] == [REDIRECT_TEXT]

    new = _streaming(ctrl, "msg_pending_new", key="key_pending")
    assert [slot.text for slot in new.segment_state.notices] == [REDIRECT_TEXT]
    assert "key_pending" not in ctrl._pending_notices


def test_session_key_takeover_aborts_previous_card() -> None:
    """同一会话键下的新卡接管：旧卡必须收尾，否则完成回调落到已封的卡上."""
    ctrl = _setup_ctrl()
    closed: list[str] = []
    ctrl._complete_session = lambda s: closed.append(s.message_id)  # type: ignore[method-assign]
    _streaming(ctrl, "msg_prev", key="key_takeover")

    new = _streaming(ctrl, "msg_next", key="key_takeover")
    assert closed == ["msg_prev"]
    assert ctrl._session_keys["key_takeover"] is new


# ── 后台回执 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_background_receipt_merges_into_live_card() -> None:
    ctrl = _setup_ctrl()
    session = _streaming(ctrl, "msg_bg", chat_id="chat_bg")

    delivered = await ctrl.on_background_deliver(
        chat_id="chat_bg",
        preview="done",
        content="后台任务完成",
        reply_to_message_id="msg_bg",
    )
    assert delivered is True
    assert [slot.text for slot in session.segment_state.notices] == ["后台任务完成"]


@pytest.mark.asyncio
async def test_background_receipt_without_live_card_still_sends_card() -> None:
    ctrl = _setup_ctrl()
    ctrl._do_background_deliver = AsyncMock()  # type: ignore[method-assign]

    delivered = await ctrl.on_background_deliver(
        chat_id="chat_bg",
        preview="done",
        content="任务完成",
        reply_to_message_id="msg_gone",
    )
    assert delivered is True
    ctrl._do_background_deliver.assert_awaited_once()


@pytest.mark.asyncio
async def test_background_receipt_only_merges_into_same_chat() -> None:
    ctrl = _setup_ctrl()
    _streaming(ctrl, "msg_other_chat", chat_id="chat_other")
    ctrl._do_background_deliver = AsyncMock()  # type: ignore[method-assign]

    await ctrl.on_background_deliver(
        chat_id="chat_target",
        preview="done",
        content="任务完成",
        reply_to_message_id="msg_other_chat",
    )
    assert ctrl._do_background_deliver.await_count == 1


# ── 完成态卡片带上提示 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_completed_card_includes_notice_slot() -> None:
    """本轮没有正文：那条提示要出现在完成态卡片上，且只有一条（不会被吞掉）."""
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_done", key="key_done")
    session.segment_state.add_notice(BUSY_TEXT)
    session.create_task = None
    session.card_id = "card_done"
    session.state = SessionState.STREAMING

    ok = await ctrl._do_complete_card(session)
    assert ok is True
    card = ctrl._client.cardkit_update.call_args.args[1]
    bodies = card["body"]["elements"]
    notices = [el for el in bodies if str(el.get("element_id", "")).startswith("fry_notice")]
    assert len(notices) == 1, "通知槽单槽位: 卡上最多一个提示元素"
    assert notices[0]["content"] == BUSY_TEXT


def test_streaming_card_carries_latest_notice_after_redirect() -> None:
    """重定向后新卡建立时带上提示；提示落卡片底部（loading 之前，#43 反馈）."""
    from hermes_fry_cards.cardkit.builder import build_streaming_card_v2
    from hermes_fry_cards.streaming.controller import _latest_notice

    session = _make_session("msg_streaming_notice")
    session.segment_state.add_notice(REDIRECT_TEXT)
    assert _latest_notice(session) == REDIRECT_TEXT

    card = build_streaming_card_v2(notice=_latest_notice(session))
    elements = card["body"]["elements"]
    assert elements[-1]["element_id"] == "loading_icon"
    assert elements[-2]["element_id"] == "fry_notice"
    assert elements[-2]["content"] == REDIRECT_TEXT
    # 不在卡片顶部（工具输出之上）
    assert elements[0]["element_id"] != "fry_notice"


# ── 通知区元素记账（#43 评审） ──────────────────────────────────────────────


def test_complete_card_notice_ids_are_unique() -> None:
    """一张卡命中多条提示时，元素 id 必须唯一（固定 fry_notice 会撞 id）."""
    card = build_complete_card(segments=[], all_tool_steps=[], notices=[BUSY_TEXT, REDIRECT_TEXT])
    ids = [el["element_id"] for el in card["body"]["elements"] if el.get("element_id", "").startswith("fry_notice")]
    assert ids == ["fry_notice", "fry_notice_1"]
    assert len(ids) == len(set(ids))


def test_three_notices_have_three_distinct_ids() -> None:
    from hermes_fry_cards.cardkit.builder import build_notice_elements

    elements = build_notice_elements(["a", "b", "c"])
    assert [e["element_id"] for e in elements] == ["fry_notice", "fry_notice_1", "fry_notice_2"]


def test_notice_elements_are_counted_in_element_budget() -> None:
    """通知槽最多一条提示、只占一个元素，记账按这一格累加（不能按旧的多条累加）."""
    from hermes_fry_cards.streaming.controller import _sync_notice_elements

    session = _make_session("msg_notice_budget")
    # 已建卡：loading 元素已在计数器里
    session.element_count = 10
    session.notice_element_count = 0
    session.segment_state.add_notice(BUSY_TEXT)
    session.segment_state.add_notice(REDIRECT_TEXT)

    notices = _sync_notice_elements(session)
    assert notices == [REDIRECT_TEXT]
    assert session.notice_element_count == 1
    assert session.element_count == 11


def test_notice_element_sync_is_idempotent() -> None:
    from hermes_fry_cards.streaming.controller import _sync_notice_elements

    session = _make_session("msg_notice_idem")
    session.element_count = 5
    session.notice_element_count = 0
    session.segment_state.add_notice(BUSY_TEXT)

    _sync_notice_elements(session)
    _sync_notice_elements(session)
    assert session.element_count == 6
    assert session.notice_element_count == 1


@pytest.mark.asyncio
async def test_completed_card_element_count_matches_rendered_elements() -> None:
    """完成态：记账的元素数不小于卡片实际元素数（通知区那一条也要计入）.

    单槽位下通知区最多一个元素；本轮没有正文时它留在卡上，记账必须把它算进去。
    """
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_notice_card", key="key_notice_card")
    session.segment_state.add_notice(BUSY_TEXT)
    session.segment_state.add_notice(REDIRECT_TEXT)
    session.create_task = None
    session.card_id = "card_notice_card"
    # 已建卡：loading 元素已有基线，尚未给提示记账
    session.element_count = 1
    session.notice_element_count = 0

    assert await ctrl._do_complete_card(session) is True
    card = ctrl._client.cardkit_update.call_args.args[1]
    rendered = [
        el for el in card["body"]["elements"] if str(el.get("element_id", "")).startswith("fry_notice")
    ]
    assert len(rendered) == 1
    assert rendered[0]["content"] == REDIRECT_TEXT
    assert session.notice_element_count == 1
    assert session.element_count >= len(card["body"]["elements"])


# ── 压缩封卡与别名兼容（#43 增强） ──────────────────────────────────────


def test_on_turn_redirected_accepts_message_id_alias() -> None:
    """镜像烘入的模板调用 on_turn_redirected(session_key=..., message_id=...) 必须正常工作."""
    from unittest.mock import patch

    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import on_turn_redirected

    ctrl = _setup_ctrl()
    _streaming(ctrl, "msg_old", key="key_alias")
    with patch.object(patch_mod, "get_controller", return_value=ctrl):
        # 即使只有 session_key 和 message_id，也不应抛 TypeError
        on_turn_redirected(session_key="key_alias", message_id="msg_new")
    assert "msg_new" in ctrl._sessions


def test_on_busy_ack_accepts_ctrl_keyword() -> None:
    """构建模板传或不传 ctrl 都不应抛 TypeError."""
    from unittest.mock import patch

    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import on_busy_ack

    ctrl = _setup_ctrl()
    _streaming(ctrl, "msg_b1", key="key_b1")
    with patch.object(patch_mod, "get_controller", return_value=ctrl):
        assert on_busy_ack(ctrl=ctrl, text=BUSY_TEXT, session_key="key_b1") is True
        assert on_busy_ack(text=BUSY_TEXT, session_key="key_b1") is True


@pytest.mark.asyncio
async def test_session_compressed_seals_active_card() -> None:
    """触发压缩时不提前封卡：保留当前会话活跃卡片为流式态（#59）."""
    ctrl = _setup_ctrl()
    session = _streaming(ctrl, "msg_comp", key="key_comp")
    session.card_id = "card_comp"
    sealed = []
    ctrl._complete_session_after_creation = AsyncMock(side_effect=lambda s: sealed.append(s.card_id) or True)

    assert await ctrl.on_session_compressed(session_key="key_comp") is True
    assert session.state == SessionState.STREAMING
    assert sealed == []


def test_on_compression_sync_entry() -> None:
    """同步 entry 调用 on_compression（保留流式活跃，不提前终态，#59）."""
    ctrl = _setup_ctrl()
    session = _streaming(ctrl, "msg_sync", key="key_sync")
    session.card_id = "card_sync"
    closed = []
    ctrl._complete_session = lambda s: closed.append(s.card_id)

    assert ctrl.on_compression(session_key="key_sync") is True
    assert session.state == SessionState.STREAMING
    assert closed == []


def test_on_session_compressed_skips_when_no_active_session() -> None:
    ctrl = _setup_ctrl()
    from unittest.mock import patch

    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import on_session_compressed

    with patch.object(patch_mod, "get_controller", return_value=ctrl):
        assert on_session_compressed(session_key="non_existent") is False


# ── 提示分区：进行中提示落底部，后台回执在顶部（#43 反馈） ──────────────────


def test_notice_kinds_split_bottom_and_top() -> None:
    """单槽位：kind 只决定那一个元素落哪个展示位——后台回执在顶部，进行中提示在底部.

    槽里始终只有一条：后到的提示覆盖先到的，不会出现「顶部 + 底部」两条并存。
    """
    state = SegmentState()
    state.add_notice(BUSY_TEXT, kind="busy")
    assert state.split_notices() == ([], [BUSY_TEXT])

    state.add_notice(REDIRECT_TEXT, kind="redirect")
    assert [slot.text for slot in state.notices] == [REDIRECT_TEXT]
    assert state.split_notices() == ([], [REDIRECT_TEXT])

    state.add_notice("后台任务完成", kind="background")
    assert [slot.text for slot in state.notices] == ["后台任务完成"]
    assert state.split_notices() == (["后台任务完成"], [])


def test_later_background_receipt_replaces_redirect_notice() -> None:
    """反例：同一张卡先来重定向提示、后来后台回执 → 槽里只剩后台回执.

    单槽位跨 kind 覆盖：重定向提示被后台回执顶掉，卡上只画一条（顶部那一条）。
    """
    state = SegmentState()
    state.add_notice(REDIRECT_TEXT, kind="redirect")
    state.add_notice("后台任务完成", kind="background")

    assert [slot.text for slot in state.notices] == ["后台任务完成"]
    assert state.notices[0].kind == "background"
    top, bottom = state.split_notices()
    assert (top, bottom) == (["后台任务完成"], [])

    card = build_complete_card(
        segments=[],
        all_tool_steps=[],
        notices=top,
        progress_notices=bottom,
    )
    rendered = [
        el["content"]
        for el in card["body"]["elements"]
        if str(el.get("element_id", "")).startswith("fry_notice")
    ]
    assert rendered == ["后台任务完成"]


def test_complete_card_renders_progress_notices_at_bottom() -> None:
    card = build_complete_card(
        segments=[],
        all_tool_steps=[],
        notices=["后台任务完成"],
        progress_notices=[BUSY_TEXT, REDIRECT_TEXT],
    )
    elements = card["body"]["elements"]
    ids = [el.get("element_id") for el in elements]
    assert ids.index("fry_notice") < ids.index("fry_notice_1") < ids.index("fry_notice_2")
    assert elements[ids.index("fry_notice")]["content"] == "后台任务完成"
    assert elements[ids.index("fry_notice_1")]["content"] == BUSY_TEXT
    assert elements[ids.index("fry_notice_2")]["content"] == REDIRECT_TEXT
    # 底部通知位在顶部回执之后，且都在卡内（无 id 冲突；正文/表格元素无 id，不计）
    notice_ids = [i for i in ids if i]
    assert len(set(notice_ids)) == len(notice_ids)


@pytest.mark.asyncio
async def test_busy_receipt_lands_at_bottom_of_completed_card() -> None:
    """忙碌回执的 kind=busy：本轮没有正文时留卡，且落底部通知位（不在卡顶）.

    单槽位下「有正文」的卡封卡时清空提示，所以底部展示位只在本轮没有正文
    （只跑了工具 / 被打断）时才可见。
    """
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_kind_busy", key="key_kind_busy")
    session.tool_use.record_start("read", "f")
    session.segment_state.on_tool_event(1)
    ctrl.on_busy_ack(text=BUSY_TEXT, message_id="msg_kind_busy", session_key="key_kind_busy")
    session.create_task = None
    session.card_id = "card_kind_busy"
    session.state = SessionState.STREAMING

    assert await ctrl._do_complete_card(session) is True
    card = ctrl._client.cardkit_update.call_args.args[1]
    bodies = card["body"]["elements"]
    notice = next(el for el in bodies if el.get("element_id") == "fry_notice")
    assert notice["content"] == BUSY_TEXT
    # 不在卡顶（工具面板在前），底部通知位在工具输出之后
    assert bodies[0].get("element_id") != "fry_notice"


@pytest.mark.asyncio
async def test_background_receipt_stays_at_top_of_completed_card() -> None:
    """后台回执 kind=background：本轮没有正文时留卡，且在卡顶第一个元素."""
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_kind_bg", key="key_kind_bg")
    session.segment_state.add_notice("后台任务完成", kind="background")
    session.create_task = None
    session.card_id = "card_kind_bg"
    session.state = SessionState.STREAMING

    assert await ctrl._do_complete_card(session) is True
    card = ctrl._client.cardkit_update.call_args.args[1]
    assert card["body"]["elements"][0]["content"] == "后台任务完成"


# ── 重定向封卡清提示（#43 反馈：同一句话不在旧卡正文复述） ──────────────────


def test_interrupt_clears_progress_notices_on_old_card() -> None:
    ctrl = _setup_ctrl()
    old = _streaming(ctrl, "msg_old_notice", key="key_old_notice")
    old.segment_state.add_notice(BUSY_TEXT, kind="busy")
    old.segment_state.add_notice("后台任务完成", kind="background")
    ctrl._complete_session = lambda s: s.__setattr__("state", SessionState.ABORTED)  # type: ignore[method-assign]

    ctrl.on_interrupted(
        old_message_id="msg_old_notice",
        new_message_id="msg_new_notice",
        chat_id="chat_1",
        session_key="key_old_notice",
    )
    # 进行中提示随新卡走，旧卡只保留完成后的回执
    assert [slot.text for slot in old.segment_state.notices] == ["后台任务完成"]


def test_clear_progress_notices_keeps_background_receipts() -> None:
    session = _make_session("msg_clear")
    session.segment_state.add_notice(BUSY_TEXT, kind="busy")
    session.segment_state.add_notice("后台任务完成", kind="background")

    session.clear_progress_notices()
    assert [slot.text for slot in session.segment_state.notices] == ["后台任务完成"]


# ── 合成轮次 id（后台完成回执的 ["completion", ...] 序列，#43 反馈） ─────────


def test_synthetic_completion_id_creates_send_to_chat_session() -> None:
    """合成 id 不是飞书 open_message_id：建会话但卡片直接投聊天，不 reply."""
    ctrl = _setup_ctrl()
    synthetic_id = '["completion","proc_684099a22fe4",1792773079.3370965]'

    started = ctrl.on_message_started(
        message_id=synthetic_id,
        chat_id="chat_bg",
        session_key="key_syn",
    )
    assert started == synthetic_id
    session = ctrl._sessions[synthetic_id]
    assert session.synthetic is True
    assert session.anchor_id is None


def test_synthetic_completion_id_is_detected() -> None:
    from hermes_fry_cards.controller import _is_synthetic_turn_id

    assert _is_synthetic_turn_id('["completion","proc_x",1.0]') is True
    assert _is_synthetic_turn_id('["background","task",1.0]') is True
    assert _is_synthetic_turn_id("om_x100b628cf392e4a") is False
    assert _is_synthetic_turn_id("syn-abc123") is False


# ── 网关流消费者投递标记（#43 反馈：streamed=False 重复投递警告） ────────────


def test_mark_stream_delivered_reports_healthy_card() -> None:
    from unittest.mock import patch

    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import mark_stream_delivered

    ctrl = _setup_ctrl()
    _streaming(ctrl, "msg_mark", key="key_mark")

    class _Consumer:
        pass

    consumer = _Consumer()
    with patch.object(patch_mod, "get_controller", return_value=ctrl):
        mark_stream_delivered(consumer, "msg_mark", "key_mark")
        assert consumer.has_durably_delivered_text("final answer") is True

        session = ctrl._sessions["msg_mark"]
        session.state = SessionState.FAILED
        assert consumer.has_durably_delivered_text("final answer") is False


def test_mark_stream_delivered_synthetic_turn_resolves_by_session_key() -> None:
    """合成轮次没有 event_message_id：message_id=None 时谓词按 session_key 兜底（#48）."""
    from unittest.mock import patch

    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import mark_stream_delivered

    ctrl = _setup_ctrl()
    _streaming(ctrl, "syn-abc123def456", key="key_syn48")

    class _Consumer:
        pass

    consumer = _Consumer()
    with patch.object(patch_mod, "get_controller", return_value=ctrl):
        mark_stream_delivered(consumer, None, "key_syn48")
        assert consumer._fry_session_key == "key_syn48"
        assert consumer.has_durably_delivered_text("final answer") is True


def test_mark_stream_delivered_without_any_id_is_false() -> None:
    from unittest.mock import patch

    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import mark_stream_delivered

    ctrl = _setup_ctrl()

    class _Consumer:
        pass

    consumer = _Consumer()
    with patch.object(patch_mod, "get_controller", return_value=ctrl):
        mark_stream_delivered(consumer, None, None)
        assert consumer.has_durably_delivered_text("final answer") is False


def test_stream_card_healthy_resolves_by_session_key() -> None:
    ctrl = _setup_ctrl()
    _streaming(ctrl, "syn-abc123def456", key="key_healthy")
    assert ctrl.stream_card_healthy(None, "key_healthy") is True
    assert ctrl.stream_card_healthy(None, None) is False


def test_mark_stream_delivered_survives_card_loss() -> None:
    from unittest.mock import patch

    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import mark_stream_delivered

    ctrl = _setup_ctrl()

    class _Consumer:
        pass

    consumer = _Consumer()
    with patch.object(patch_mod, "get_controller", return_value=ctrl):
        mark_stream_delivered(consumer, "msg_gone")
        assert consumer.has_durably_delivered_text("final answer") is False


def test_mark_stream_delivered_ignores_missing_consumer() -> None:
    from hermes_fry_cards.patch import mark_stream_delivered

    mark_stream_delivered(None, "msg_any")  # 不抛即通过


# ── 连续第二次打断：REDIRECT_RESOLVED 锚点在回执防抖之前换卡（#43 反馈） ────


def test_on_turn_redirected_with_empty_text_still_switches_cards() -> None:
    """防抖场景没有提示文案：redirected=True 也要完成换卡."""
    from unittest.mock import patch

    import hermes_fry_cards.patch as patch_mod
    from hermes_fry_cards.patch import on_turn_redirected

    ctrl = _setup_ctrl()
    old = _streaming(ctrl, "msg_old_2nd", key="key_2nd")
    ctrl._complete_session = lambda s: s.__setattr__("state", SessionState.ABORTED)  # type: ignore[method-assign]

    with patch.object(patch_mod, "get_controller", return_value=ctrl):
        on_turn_redirected(
            new_message_id="msg_new_2nd",
            chat_id="chat_1",
            session_key="key_2nd",
            redirected=True,
            text="",
        )
    assert old.state == SessionState.ABORTED
    assert "msg_new_2nd" in ctrl._sessions


# ── 提示元素被正文原地接管（#issue：提示没被正文覆盖） ──────────────────────


def test_first_answer_adopts_notice_element_in_place() -> None:
    """提示写着卡上那个元素：首段正文复用同一个 element id，不另起一段."""
    state = SegmentState()
    state.add_notice(REDIRECT_TEXT, kind="redirect")
    state.bind_notice_element("fry_notice", REDIRECT_TEXT)

    state.on_answer_delta("Hello")

    assert [seg.el_id for seg in state.segments] == ["fry_notice"]
    assert state.segments[0].type == "answer"
    assert state.segments[0].created is True
    # 提示已被正文取代：通知槽里不再留它，完成态也就不会再复述一遍
    assert state.notices == []
    assert state.adoptable_notice_element_id() is None


def test_later_deltas_keep_streaming_into_adopted_element() -> None:
    state = SegmentState()
    state.add_notice(BUSY_TEXT, kind="busy")
    state.bind_notice_element("fry_notice", BUSY_TEXT)

    state.on_answer_delta("Hello")
    state.on_answer_delta(" world")

    assert len(state.segments) == 1
    assert state.segments[0].el_id == "fry_notice"
    assert state.segments[0].text == "Hello world"


def test_background_receipt_element_is_not_taken_over() -> None:
    """后台回执留在通知区顶部，不被正文接管."""
    state = SegmentState()
    state.add_notice("后台任务完成", kind="background")
    state.bind_notice_element("fry_notice", "后台任务完成")

    state.on_answer_delta("Hello")

    assert state.segments[0].el_id != "fry_notice"
    assert [slot.text for slot in state.notices] == ["后台任务完成"]


def test_answer_without_notice_element_keeps_own_segment() -> None:
    """卡上没有提示元素（如回合中途才到的忙碌回执）时不接管，正文照常新建."""
    state = SegmentState()
    state.add_notice(BUSY_TEXT, kind="busy")
    state.bind_notice_element(None, None)

    state.on_answer_delta("Hello")

    assert state.segments[0].el_id == "answer_0"
    assert [slot.text for slot in state.notices] == [BUSY_TEXT]


def test_adoption_only_applies_to_the_first_answer() -> None:
    state = SegmentState()
    state.on_answer_delta("first")
    state.add_notice(REDIRECT_TEXT, kind="redirect")
    state.bind_notice_element("fry_notice", REDIRECT_TEXT)

    state.on_tool_event(1)  # 工具调用把回答切成第二段
    state.on_answer_delta("second")

    assert state.segments[0].el_id == "answer_0"
    assert "fry_notice" not in [seg.el_id for seg in state.segments]


def test_drop_rendered_progress_notices_follows_body_presence() -> None:
    """封卡摘提示只看本轮有没有正文：有正文清空槽位，没有正文保留那一条.

    单槽位下「画过的那条剔除、没画过的那条保留」不再成立：槽里最多一条，
    有正文时完成态只显示正文，没有正文时那一条必须留给用户看。
    """
    # 有正文：画在提示元素上的后台回执也不复述，槽位清空
    with_body = SegmentState()
    with_body.add_notice("后台任务完成", kind="background")
    with_body.bind_notice_element("fry_notice_live_1", "后台任务完成")
    with_body.on_answer_delta("answer text")
    with_body.drop_rendered_progress_notices()
    assert with_body.notices == []
    assert with_body.notice_element_id is None
    assert with_body.notice_element_text is None

    # 没有正文：画在元素上的那一条保留（不能让它随封卡消失）
    painted_no_body = SegmentState()
    painted_no_body.add_notice(BUSY_TEXT, kind="busy")
    painted_no_body.bind_notice_element("fry_notice", BUSY_TEXT)
    painted_no_body.drop_rendered_progress_notices()
    assert [slot.text for slot in painted_no_body.notices] == [BUSY_TEXT]

    # 没有正文、提示从未上卡：同样保留（没有元素可替换，摘掉就等于丢掉）
    unpainted_no_body = SegmentState()
    unpainted_no_body.add_notice(BUSY_TEXT, kind="busy")
    unpainted_no_body.drop_rendered_progress_notices()
    assert [slot.text for slot in unpainted_no_body.notices] == [BUSY_TEXT]


@pytest.mark.asyncio
async def test_do_create_card_binds_notice_element_for_adoption() -> None:
    """建卡时提示已写进 fry_notice：控制器把该元素登记给段状态，正文才敢原地接管."""
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    ctrl._client.reply_card_by_id = AsyncMock(return_value="msg_bind_reply")
    session = _make_session("msg_bind")
    session.chat_id = "chat_1"
    # 提示先于建卡到达（重定向开口与建卡并发时的常见顺序）
    session.segment_state.add_notice(REDIRECT_TEXT, kind="redirect")
    ctrl._sessions["msg_bind"] = session

    await ctrl._do_create_card(session)

    assert session.segment_state.notice_element_id == "fry_notice"
    assert session.notice_element_count == 1
    # 卡片骨架上确实画着这条提示
    built = ctrl._client.cardkit_create.call_args.args[0]
    assert [el.get("element_id") for el in built["body"]["elements"]][-2] == "fry_notice"

    session.segment_state.on_answer_delta("Hello")
    await ctrl._do_flush(session)
    assert [c.args[1] for c in ctrl._client.cardkit_stream_element.call_args_list] == ["fry_notice"]


@pytest.mark.asyncio
async def test_flush_overwrites_notice_element_instead_of_adding_one() -> None:
    """整条 flush 路径：正文流式写进 fry_notice，不再新增一个 answer 元素."""
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_adopt_flush", key="key_adopt_flush")
    session.segment_state.add_notice(REDIRECT_TEXT, kind="redirect")
    session.segment_state.bind_notice_element("fry_notice", REDIRECT_TEXT)
    session.notice_element_count = 1
    session.element_count = 2

    session.segment_state.on_answer_delta("Hello")
    await ctrl._do_flush(session)

    streamed = [call.args[1] for call in ctrl._client.cardkit_stream_element.call_args_list]
    assert streamed == ["fry_notice"]
    added_ids = [
        element["element_id"]
        for call in ctrl._client.cardkit_batch_update.call_args_list
        for action in call.args[1]
        if action.get("action") == "add_elements"
        for element in action["params"]["elements"]
    ]
    assert added_ids == []


@pytest.mark.asyncio
async def test_completed_card_drops_adopted_notice_from_body() -> None:
    """封卡后正文末尾不再残留那条进行中提示."""
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_adopt_done", key="key_adopt_done")
    session.segment_state.add_notice(REDIRECT_TEXT, kind="redirect")
    session.segment_state.bind_notice_element("fry_notice", REDIRECT_TEXT)
    session.segment_state.on_answer_delta("answer text")
    session.create_task = None
    session.element_count = 2
    session.notice_element_count = 1

    assert await ctrl._do_complete_card(session) is True
    card = ctrl._client.cardkit_update.call_args.args[1]
    contents = [el.get("content", "") for el in card["body"]["elements"]]
    assert REDIRECT_TEXT not in contents
    assert any("answer text" in text for text in contents)


@pytest.mark.asyncio
async def test_completed_card_keeps_painted_notice_when_no_answer_came() -> None:
    """本轮没有正文：画在元素上的提示封卡后仍要留着（否则用户什么都看不到）."""
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_painted_done", key="key_painted_done")
    session.segment_state.add_notice(BUSY_TEXT, kind="busy")
    session.segment_state.bind_notice_element("fry_notice", BUSY_TEXT)
    session.create_task = None
    session.element_count = 2
    session.notice_element_count = 1

    assert await ctrl._do_complete_card(session) is True
    card = ctrl._client.cardkit_update.call_args.args[1]
    contents = [el.get("content", "") for el in card["body"]["elements"]]
    assert BUSY_TEXT in contents


# ── on_interrupted 幂等（#issue：一次重定向两张卡） ─────────────────────────


def test_duplicate_interrupt_keeps_one_new_session() -> None:
    """REDIRECT / REDIRECT_RESOLVED 两个注入点各调一次：只建一张新卡，且不被 abort."""
    ctrl = _setup_ctrl()
    old = _streaming(ctrl, "msg_dup_old", key="key_dup")
    ctrl._complete_session = lambda s: s.__setattr__("state", SessionState.ABORTED)  # type: ignore[method-assign]

    for _ in range(2):
        ctrl.on_interrupted(
            old_message_id="msg_dup_old",
            new_message_id="msg_dup_new",
            chat_id="chat_1",
            session_key="key_dup",
            anchor_id="msg_dup_old",  # 新卡挂在旧消息上 → 旧消息键也指向新会话
        )

    survivors = {id(s): s for s in ctrl._sessions.values() if s.message_id == "msg_dup_new"}
    assert len(survivors) == 1
    survivor = ctrl._sessions["msg_dup_new"]
    assert survivor is next(iter(survivors.values()))
    assert survivor.state is not SessionState.ABORTED
    assert old.state == SessionState.ABORTED
    # 提示落在留存的那张卡上
    ctrl.on_redirected_notice(text=REDIRECT_TEXT, message_id="msg_dup_new", session_key="key_dup")
    assert [slot.text for slot in survivor.segment_state.notices] == [REDIRECT_TEXT]


def test_duplicate_interrupt_reuses_session_found_by_key() -> None:
    """新会话只按 session_key 登记过时，第二次调用按 session_key 认领它，不再新建."""
    ctrl = _setup_ctrl()
    old = _streaming(ctrl, "msg_keydup_old", key="key_keydup")
    pending = _make_session("msg_keydup_new")
    pending.chat_id = "chat_1"
    pending.session_key = "key_keydup"
    pending.state = SessionState.CREATING
    ctrl._session_keys["key_keydup"] = pending
    ctrl._complete_session = lambda s: s.__setattr__("state", SessionState.ABORTED)  # type: ignore[method-assign]

    ctrl.on_interrupted(
        old_message_id="msg_keydup_old",
        new_message_id="msg_keydup_new",
        chat_id="chat_1",
        session_key="key_keydup",
    )

    assert ctrl._sessions["msg_keydup_new"] is pending
    assert pending.state is SessionState.CREATING
    assert old.state == SessionState.ABORTED
    assert {id(s) for s in ctrl._sessions.values() if s.message_id == "msg_keydup_new"} == {id(pending)}



# ── 流式期通知槽即时渲染（0.5.9.3） ────────────────────────────────────────


def test_notice_slot_target_follows_latest_notice() -> None:
    """目标文案 = 最新一条提示；画上去之后不再要求重画."""
    state = SegmentState()
    assert state.notice_slot_target() is None
    assert state.needs_notice_slot_paint() is False

    state.add_notice(BUSY_TEXT, kind="busy")
    assert state.notice_slot_target() == BUSY_TEXT
    assert state.needs_notice_slot_paint() is True

    state.bind_notice_element("fry_notice", BUSY_TEXT)
    assert state.needs_notice_slot_paint() is False

    state.add_notice("后台任务完成", kind="background")
    assert state.notice_slot_target() == "后台任务完成"
    assert state.needs_notice_slot_paint() is True


def test_next_notice_slot_element_id_avoids_body_element() -> None:
    """中途补画的通知元素 id 与 ``fry_notice`` 分属两个命名空间，不会冲掉正文."""
    state = SegmentState()
    first = state.next_notice_slot_element_id()
    second = state.next_notice_slot_element_id()
    assert first == "fry_notice_live_1"
    assert second == "fry_notice_live_2"
    assert first != "fry_notice"


def test_streaming_notice_element_matches_card_skeleton_shape() -> None:
    """补画的元素与建卡时的提示元素同构（流式元素 + 正文字号 + 固定 id）."""
    from hermes_fry_cards.cardkit.builder import (
        build_streaming_card_v2,
        build_streaming_notice_element,
    )

    card = build_streaming_card_v2(notice=BUSY_TEXT)
    painted = build_streaming_notice_element(BUSY_TEXT, element_id="fry_notice")
    skeleton = card["body"]["elements"][-2]
    assert skeleton["element_id"] == "fry_notice"
    assert skeleton["tag"] == painted["tag"]
    assert skeleton["content"] == painted["content"]


@pytest.mark.asyncio
async def test_flush_paints_receipt_into_live_card_without_notice_element() -> None:
    """流式期到达的后台回执：flush 当场在 loading 之前补一个通知元素（不新增正文段）."""
    from hermes_fry_cards.cardkit.builder import LOADING_ELEMENT_ID

    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_bg_paint", key="key_bg_paint")
    session.element_count = 1
    session.segment_state.on_answer_delta("answer text")
    await ctrl._do_flush(session)  # 先把正文元素建出来
    ctrl._client.cardkit_batch_update.reset_mock()

    ok = await ctrl.on_background_completion(
        chat_id="chat_1", session_key="key_bg_paint", text="后台任务完成", session_id="proc_paint",
    )
    assert ok is True
    assert session.segment_state.needs_notice_slot_paint() is True

    await ctrl._do_flush(session)

    actions = ctrl._client.cardkit_batch_update.call_args.args[1]
    assert len(actions) == 1
    action = actions[0]
    assert action["action"] == "add_elements"
    assert action["params"]["type"] == "insert_before"
    assert action["params"]["target_element_id"] == LOADING_ELEMENT_ID
    element = action["params"]["elements"][0]
    assert element["element_id"] == "fry_notice_live_1"
    assert element["content"] == "后台任务完成"
    # 槽位登记推进：下一轮不再重画，也不多建一个元素
    assert session.segment_state.notice_element_id == "fry_notice_live_1"
    assert session.segment_state.notice_element_text == "后台任务完成"
    assert session.segment_state.needs_notice_slot_paint() is False
    # 回执没有变成正文段
    assert [seg.el_id for seg in session.segment_state.segments] == ["answer_0"]


@pytest.mark.asyncio
async def test_flush_repaints_existing_notice_element_in_place() -> None:
    """本卡已有提示元素且未被正文接管 → 原地改文案，不再新增元素."""
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_bg_repaint", key="key_bg_repaint")
    session.segment_state.add_notice(BUSY_TEXT, kind="busy")
    session.segment_state.bind_notice_element("fry_notice", BUSY_TEXT)
    session.notice_element_count = 1
    session.element_count = 2

    await ctrl.on_background_completion(
        chat_id="chat_1", session_key="key_bg_repaint", text="后台任务完成",
    )
    await ctrl._do_flush(session)

    actions = ctrl._client.cardkit_batch_update.call_args.args[1]
    assert len(actions) == 1
    assert actions[0]["action"] == "partial_update_element"
    assert actions[0]["params"]["element_id"] == "fry_notice"
    assert actions[0]["params"]["partial_element"]["content"] == "后台任务完成"
    assert session.segment_state.notice_element_text == "后台任务完成"


@pytest.mark.asyncio
async def test_notice_slot_paint_failure_keeps_notice_and_clears_binding() -> None:
    """画不上去不能抛：回执留在通知区，槽位登记清空（下一轮改走新增）."""
    from hermes_fry_cards.feishu import FeishuAPIError

    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    ctrl._client.cardkit_batch_update = AsyncMock(
        side_effect=FeishuAPIError("batch_update: code=300313", code=300313)
    )
    session = _streaming(ctrl, "msg_bg_fail", key="key_bg_fail")
    session.segment_state.add_notice(BUSY_TEXT, kind="busy")
    session.segment_state.bind_notice_element("fry_notice", BUSY_TEXT)
    session.element_count = 2

    await ctrl.on_background_completion(
        chat_id="chat_1", session_key="key_bg_fail", text="后台任务完成",
    )
    await ctrl._do_flush(session)  # 不抛

    assert session.segment_state.notice_element_id is None
    assert session.segment_state.needs_notice_slot_paint() is True
    assert "后台任务完成" in [slot.text for slot in session.segment_state.notices]


@pytest.mark.asyncio
async def test_completed_card_drops_painted_background_receipt() -> None:
    """封卡时画在通知槽上的后台回执随正文一起消失（流式可见、封卡被正文覆盖）."""
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_bg_seal", key="key_bg_seal")
    session.segment_state.on_answer_delta("answer text")
    session.segment_state.add_notice("后台任务完成", kind="background")
    session.segment_state.bind_notice_element("fry_notice_live_1", "后台任务完成")
    session.create_task = None
    session.card_id = "card_bg_seal"
    session.state = SessionState.STREAMING

    ok = await ctrl._do_complete_card(session)

    assert ok is True
    card = ctrl._client.cardkit_update.call_args.args[1]
    contents = [el.get("content") for el in card["body"]["elements"] if el.get("content")]
    assert "后台任务完成" not in contents
    assert any("answer text" in (c or "") for c in contents)


@pytest.mark.asyncio
async def test_completed_card_keeps_background_receipt_never_painted() -> None:
    """没落到卡片元素上的回执：本轮没有正文时仍要在完成态通知位出现（否则用户永远看不到）."""
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_bg_unpainted", key="key_bg_unpainted")
    session.segment_state.add_notice("后台任务完成", kind="background")
    session.create_task = None
    session.card_id = "card_bg_unpainted"
    session.state = SessionState.STREAMING

    ok = await ctrl._do_complete_card(session)

    assert ok is True
    card = ctrl._client.cardkit_update.call_args.args[1]
    contents = [el.get("content") for el in card["body"]["elements"] if el.get("content")]
    assert "后台任务完成" in contents
    assert session.notice_element_count == 1


@pytest.mark.asyncio
async def test_completed_card_with_body_renders_no_notice() -> None:
    """反例：封卡后本轮有正文 → 一条提示都不渲染，完成态卡片只显示正文."""
    ctrl = _setup_ctrl()
    ctrl._client = _mock_client()
    session = _streaming(ctrl, "msg_body_only", key="key_body_only")
    session.segment_state.on_answer_delta("answer text")
    session.segment_state.add_notice(BUSY_TEXT, kind="busy")
    session.segment_state.add_notice("后台任务完成", kind="background")
    session.create_task = None
    session.card_id = "card_body_only"
    session.state = SessionState.STREAMING

    ok = await ctrl._do_complete_card(session)

    assert ok is True
    card = ctrl._client.cardkit_update.call_args.args[1]
    elements = card["body"]["elements"]
    notice_ids = [
        el.get("element_id") for el in elements if str(el.get("element_id", "")).startswith("fry_notice")
    ]
    contents = [el.get("content", "") for el in elements]
    assert notice_ids == []
    assert BUSY_TEXT not in contents
    assert "后台任务完成" not in contents
    assert any("answer text" in text for text in contents)
    # 记账也跟着归零，不再多算一格
    assert session.notice_element_count == 0
