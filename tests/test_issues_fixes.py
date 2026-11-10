"""Tests verifying fixes for Issues #58, #59, #62, #63, #64, #65, #66, #67, #68, #69."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_fry_cards.config import Config
from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.patcher import _complete_hook
from hermes_fry_cards.streaming.flush import FlushController
from hermes_fry_cards.streaming.segments import SegmentType
from hermes_fry_cards.streaming.session import CardSession, SessionState


@pytest.mark.asyncio
async def test_issue_64_remove_loading_icon_uses_delete_elements_action() -> None:
    """#64: CardKit loading 图标删除必须使用 CardKit 标准动作 delete_elements."""
    ctrl = StreamCardController()
    ctrl._client = MagicMock()
    ctrl._client.cardkit_batch_update = AsyncMock()

    await ctrl._remove_loading_icon("card_123", sequence=2)

    ctrl._client.cardkit_batch_update.assert_awaited_once_with(
        "card_123",
        [{"action": "delete_elements", "params": {"element_ids": ["loading_icon"]}}],
        sequence=2,
    )


@pytest.mark.asyncio
async def test_issue_63_incremental_text_arriving_during_stream_element_keeps_dirty() -> None:
    """#63: 在 cardkit_stream_element await 网络调用期间到达的增量必须保留 dirty 状态."""
    ctrl = StreamCardController()
    ctrl._client = MagicMock()
    ctrl._client.cardkit_batch_update = AsyncMock()
    session = CardSession("msg_test", "chat_test", asyncio.get_running_loop())
    session.card_id = "card_test"
    session.state = SessionState.STREAMING
    ctrl._sessions["msg_test"] = session

    # 模拟在 stream_element 协程执行期间有新的 delta 到来
    async def fake_stream_element(*args, **kwargs):
        session.segment_state.on_answer_delta(" late arrival")

    ctrl._client.cardkit_stream_element = AsyncMock(side_effect=fake_stream_element)

    session.segment_state.on_answer_delta("initial")
    seg = session.segment_state.segments[-1]
    assert seg.type == SegmentType.ANSWER
    assert seg.dirty is True

    await ctrl._do_flush(session)

    # 写完后 seg.text 为 "initial late arrival"，因等待期间来了新文本，seg.dirty 仍应为 True
    assert seg.text == "initial late arrival"
    assert seg.dirty is True


@pytest.mark.asyncio
async def test_issue_68_flush_wait_for_flush_wakes_up_when_completed_before_task_starts() -> None:
    """#68: 排队未启动的 flush 任务在 mark_completed 之后被执行，必须唤醒 wait_for_flush 等待者."""
    loop = asyncio.get_running_loop()
    flush = FlushController(throttle_ms=50.0, loop=loop)
    flush.set_card_message_ready(True)

    do_flush = AsyncMock()
    # 安排一次延时 flush
    flush.schedule_update(do_flush)
    assert flush._pending_timer is not None

    # 此时标记完成，并取消待执行 timer
    flush.mark_completed()

    # 立即等待 flush，必须立刻或在任务被清理后正常返回，绝不能永久超时
    await asyncio.wait_for(flush.wait_for_flush(), timeout=1.0)


def test_issue_66_config_reload_preserves_valid_config_on_malformed_yaml(tmp_path: Path) -> None:
    """#66: 当 config.yaml 在保存过程中为残缺/损坏的 YAML 时，_reload 捕获异常并保留上次有效配置."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("streaming:\n  enabled: true\n  content_lang: en\n", encoding="utf-8")

    cfg = Config(home=tmp_path)
    assert cfg.content_lang == "en"

    # 写入损坏的 YAML（如编辑器正在清空重写阶段）
    cfg_file.write_text("streaming:\n  content_lang: [unclosed\n", encoding="utf-8")
    # 清理 stat 缓存以触发重解析
    cfg._reload_key = None

    # 不抛出 ParserError，而是回退保留上次有效配置
    assert cfg.content_lang == "en"


@pytest.mark.asyncio
async def test_issue_65_interrupted_turn_seals_card_as_aborted_status() -> None:
    """#65: 发生打断时，on_completed_wait 传入 is_interrupted=True 应将卡片保留为 ABORTED 终态."""
    ctrl = StreamCardController()
    ctrl._client = MagicMock()
    session = CardSession("msg_int", "chat_int", asyncio.get_running_loop())
    session.card_id = "card_int"
    session.card_msg_id = "msg_id_int"
    session.state = SessionState.STREAMING
    ctrl._sessions["msg_int"] = session

    with patch.object(ctrl, "_complete_session_wait", new_callable=AsyncMock, return_value=True):
        res = await ctrl.on_completed_wait(
            message_id="msg_int",
            answer="Stopped before this turn started",
            is_error=False,
            is_interrupted=True,
        )
        assert res is True
        assert session.state == SessionState.ABORTED


def test_issue_65_complete_hook_passes_is_interrupted() -> None:
    """#65: _complete_hook 代码块中必须透传 is_interrupted=bool(agent_result.get('interrupted'))."""
    hook = _complete_hook("    ")
    assert "is_interrupted=bool(agent_result.get('interrupted'))" in hook


@pytest.mark.asyncio
async def test_issue_65_interrupted_tail_discarded_does_not_pollute_new_turn_card() -> None:
    """#65: 普通打断丢弃的旧回合，其迟到的完成事件绝不回落到 session_key 污染新卡."""
    ctrl = StreamCardController()
    old_session = CardSession("old_msg", "chat_int", asyncio.get_running_loop())
    old_session.session_key = "user_sk"
    ctrl._sessions["old_msg"] = old_session
    ctrl._session_keys["user_sk"] = old_session

    # 用户发送新消息打断旧消息（map_completion=False）
    ctrl.on_interrupted(
        old_message_id="old_msg",
        new_message_id="new_msg",
        chat_id="chat_int",
        session_key="user_sk",
        map_completion=False,
    )

    new_session = ctrl._sessions["new_msg"]
    assert new_session is not old_session
    assert ctrl._session_keys["user_sk"] is new_session

    # 旧回合迟到的完成事件到达，查询 session 时由于在 _interrupted_tails 中，必须返回 None 而不能拿到 new_session
    completed_session = ctrl._completion_session("old_msg", session_key="user_sk")
    assert completed_session is None
