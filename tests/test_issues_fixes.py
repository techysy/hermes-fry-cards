"""Tests verifying fixes for Issues #58, #59, #62, #63, #64, #65, #66, #67, #68, #69."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_fry_cards.config import Config
from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.feishu import FeishuAPIError
from hermes_fry_cards.patcher import _complete_hook
from hermes_fry_cards.streaming.flush import FlushController
from hermes_fry_cards.streaming.segments import SegmentType
from hermes_fry_cards.streaming.session import CardSession, SessionState

# ── 健壮性复查：配置热重载生产语义 ──────────────────────────────────────────


def test_robustness_reload_works_after_load_populated_raw(tmp_path: Path) -> None:
    """生产回归：_load() 读过一次凭据后，streaming 样式配置仍必须热重载（#71）.

    _platform_cfg 的磁盘回退会调 _load() 并固化 _raw；_reload() 若无条件短路到
    _raw，热重载（含既有的 show_reasoning #17）在生产上永久失效。
    """
    path = tmp_path / "config.yaml"
    path.write_text(
        "streaming:\n  width_mode: fill\nfeishu:\n  app_id: a\n  app_secret: b\n",
        encoding="utf-8",
    )
    cfg = Config(home=tmp_path)
    assert cfg.width_mode == "fill"
    # _platform_cfg 走磁盘回退触发 _load()（屏蔽环境变量里的凭据，避免 env 优先）
    with patch.dict(
        os.environ,
        {"FEISHU_APP_ID": "", "FEISHU_APP_SECRET": "", "LARK_APP_ID": "", "LARK_APP_SECRET": ""},
    ):
        assert cfg.feishu_app_id == "a"
    # 此后改配置必须仍然生效
    path.write_text(
        "streaming:\n  width_mode: compact\nfeishu:\n  app_id: a\n  app_secret: b\n",
        encoding="utf-8",
    )
    assert cfg.width_mode == "compact"


def test_robustness_reload_non_dict_yaml_returns_empty(tmp_path: Path) -> None:
    """顶层是非 dict YAML（列表/标量）时必须返回 {}，不得把 None 抛给调用方."""
    (tmp_path / "config.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    cfg = Config(home=tmp_path)
    assert cfg._reload() == {}
    (tmp_path / "config.yaml").write_text("just a scalar\n", encoding="utf-8")
    assert cfg._reload() == {}


def test_robustness_reload_corrupt_yaml_keeps_last_good_and_marks_key(tmp_path: Path) -> None:
    """坏 YAML：保留上次有效配置 + 记录 key，不逐 delta 重解析刷日志（#66 加固）."""
    path = tmp_path / "config.yaml"
    path.write_text("streaming:\n  content_lang: en\n", encoding="utf-8")
    cfg = Config(home=tmp_path)
    assert cfg.content_lang == "en"

    path.write_text("streaming:\n  content_lang: [broken\n", encoding="utf-8")
    assert cfg.content_lang == "en"

    # key 已记录：稳态读取不再触碰 safe_load
    with patch("hermes_fry_cards.config.yaml.safe_load", side_effect=AssertionError("reparsed")):
        assert cfg.content_lang == "en"

    # 用户保存完成后 mtime/size 变化 → 重新解析生效
    path.write_text("streaming:\n  content_lang: zh\n", encoding="utf-8")
    assert cfg.content_lang == "zh"


def test_robustness_reload_missing_file_keeps_last_good(tmp_path: Path) -> None:
    """stat 失败（编辑器原子替换窗口）时回落上次有效配置，而非清空为默认."""
    path = tmp_path / "config.yaml"
    path.write_text("streaming:\n  content_lang: en\n", encoding="utf-8")
    cfg = Config(home=tmp_path)
    assert cfg.content_lang == "en"

    with patch("hermes_fry_cards.config._config_path", return_value=tmp_path / "missing.yaml"):
        assert cfg.content_lang == "en"


# ── 健壮性复查：换卡边界与流式降级 ──────────────────────────────────────────


def test_robustness_set_card_resets_stream_closed_on_new_card() -> None:
    """换卡（拆卡/切卡）时 stream_closed 必须重置：新卡是新的流式会话（#69 换卡边界）."""
    loop = asyncio.new_event_loop()
    try:
        session = CardSession("msg_rollover", "chat", loop)
        session.set_card(card_id="card_a", card_msg_id="msg_a")
        session.stream_closed = True

        session.set_card(card_id="card_b", card_msg_id="msg_b")
        assert session.stream_closed is False

        # 同卡重复登记（幂等重注册）不清标记
        session.stream_closed = True
        session.set_card(card_id="card_b", card_msg_id="msg_b")
        assert session.stream_closed is True
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_robustness_stream_or_update_downgrades_on_300309() -> None:
    """#69: 命中 300309 置位 stream_closed 并降级 partial_update_element，后续不再试流式."""
    ctrl = StreamCardController()
    ctrl._cfg._raw = {"streaming": {"enabled": True}}
    ctrl._client = MagicMock()
    ctrl._client.cardkit_stream_element = AsyncMock(
        side_effect=FeishuAPIError("streaming mode is closed", 300309)
    )
    ctrl._client.cardkit_batch_update = AsyncMock()
    session = CardSession("msg_309", "chat", asyncio.get_running_loop())
    session.card_id = "card_309"

    await ctrl._stream_or_update(session, "answer_0", "text", kind="answer")
    assert session.stream_closed is True
    ctrl._client.cardkit_batch_update.assert_awaited_once()

    # 后续写入直接走批量更新，不再尝试流式
    await ctrl._stream_or_update(session, "answer_0", "text2", kind="answer")
    assert ctrl._client.cardkit_stream_element.await_count == 1
    assert ctrl._client.cardkit_batch_update.await_count == 2


@pytest.mark.asyncio
async def test_robustness_300313_missing_reasoning_body_restored() -> None:
    """#67: 300313 且缺失元素正好是该段正文 id 时，补建正文元素并写回内容."""
    ctrl = StreamCardController()
    ctrl._cfg._raw = {"streaming": {"enabled": True}}
    ctrl._client = MagicMock()
    session = CardSession("msg_313", "chat", asyncio.get_running_loop())
    session.card_id = "card_313"
    session.state = SessionState.STREAMING
    ctrl._sessions["msg_313"] = session
    session.segment_state.on_reasoning_delta("thought text")
    seg = session.segment_state.segments[-1]
    seg.created = True

    # 第一次流式写入报 300313 缺正文；补建后的写入成功
    ctrl._client.cardkit_stream_element = AsyncMock(
        side_effect=[
            FeishuAPIError(f"300313 not find elementID : {seg.text_el_id}", 300313),
            None,
        ]
    )
    ctrl._client.cardkit_batch_update = AsyncMock()

    await ctrl._do_flush(session)

    # 补建动作：partial_update_element 挂到面板 el_id
    action = ctrl._client.cardkit_batch_update.await_args.args[1][0]
    assert action["action"] == "partial_update_element"
    assert action["params"]["element_id"] == seg.el_id
    assert action["params"]["partial_element"]["elements"][0]["element_id"] == seg.text_el_id
    assert "thought text" in action["params"]["partial_element"]["elements"][0]["content"]
    # 写入成功后 dirty 清零
    assert seg.dirty is False


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
