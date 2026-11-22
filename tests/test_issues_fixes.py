"""Tests verifying fixes for Issues #58, #59, #62, #63, #64, #65, #66, #67, #68, #69."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_fry_cards.config import Config
from hermes_fry_cards.controller import StreamCardController
from hermes_fry_cards.feishu import FeishuAPIError
from hermes_fry_cards.patcher import _answer_hook, _complete_hook, _start_hook, _tool_hook
from hermes_fry_cards.streaming.flush import FlushController
from hermes_fry_cards.streaming.segments import Segment, SegmentType
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


# ── 10R 路由可见性：实际服务模型上卡 ──────────────────────────────────────────


class _FakeAgent:
    def __init__(self, served: str | None) -> None:
        self.last_served_model = served


def test_collect_served_model_reads_agent_cache() -> None:
    """从 _agent_cache / _running_agents 读 agent.last_served_model；缺失返回 None."""
    from hermes_fry_cards.patch import collect_served_model

    gw_hit = MagicMock()
    gw_hit._agent_cache = {"sk": (_FakeAgent("gemini-4-argon"), 1)}
    assert collect_served_model(gw_hit, "sk") == "gemini-4-argon"

    gw_running = MagicMock(spec=[])
    gw_running._running_agents = {"sk": _FakeAgent("provider/b")}
    assert collect_served_model(gw_running, "sk") == "provider/b"

    gw_none = MagicMock(spec=[])
    gw_none._agent_cache = {"sk": (_FakeAgent(None), 1)}
    assert collect_served_model(gw_none, "sk") is None

    assert collect_served_model(MagicMock(spec=[]), "sk") is None
    assert collect_served_model(None, "sk") is None


@pytest.mark.asyncio
async def test_complete_hook_passes_served_model() -> None:
    """注入钩子优先取 agent_result['served_model']，否则回退 agent.last_served_model."""
    gateway = MagicMock(spec=[])
    gateway._agent_cache = {"fixture-session": (_FakeAgent("provider/gemini-4"), 1)}

    # 生产钩子注入在方法体内，self / session_key 是真局部变量：
    # wrapper 用同名形参复现这一点（locals().get('self') 才拿得到 gateway）。
    source = (
        "async def complete(agent_result, event, response, _response_time, _footer_line,"
        " self=None, session_key=None):\n"
        f"{_complete_hook('    ')}"
        "    return agent_result, response, _footer_line\n"
    )
    namespace: dict = {"_quick_key": None}
    exec(compile(source, "<complete-hook-served>", "exec"), namespace)

    with (
        patch(
            "hermes_fry_cards.patch.on_message_completed_wait",
            new_callable=AsyncMock,
            return_value=True,
        ) as on_completed,
        patch("hermes_fry_cards.patch.on_message_needs_text_fallback", return_value=False),
    ):
        await namespace["complete"](
            {"model": "combo-main"},
            SimpleNamespace(message_id="message"),
            "answer", 1.0, "",
            self=gateway,
            session_key="fixture-session",
        )

    assert on_completed.await_args.kwargs["served_model"] == "provider/gemini-4"


@pytest.mark.asyncio
async def test_complete_hook_prefers_result_served_model() -> None:
    """agent_result 已带 served_model（上游投影）时不回退 live 读取."""
    source = (
        "async def complete(agent_result, event, response, _response_time, _footer_line):\n"
        f"{_complete_hook('    ')}"
        "    return agent_result, response, _footer_line\n"
    )
    namespace = {"self": MagicMock(spec=[]), "session_key": "sk", "_quick_key": None}
    exec(compile(source, "<complete-hook-served-result>", "exec"), namespace)

    with (
        patch(
            "hermes_fry_cards.patch.on_message_completed_wait",
            new_callable=AsyncMock,
            return_value=True,
        ) as on_completed,
        patch("hermes_fry_cards.patch.on_message_needs_text_fallback", return_value=False),
    ):
        await namespace["complete"](
            {"model": "combo-main", "served_model": "provider/a"},
            SimpleNamespace(message_id="message"),
            "answer", 1.0, "",
        )

    assert on_completed.await_args.kwargs["served_model"] == "provider/a"


def test_model_with_served_rendering() -> None:
    """model 字段：served 与配置名不同时显示「别名 → 实际」，相同/缺失不加箭头."""
    from hermes_fry_cards.cardkit.builder import _model_with_served

    assert _model_with_served({"model": "combo-main"}) == "combo-main"
    assert (
        _model_with_served({"model": "combo-main", "served_model": "provider/gemini-4"})
        == "combo-main → provider/gemini-4"
    )
    # 相同值不加箭头（runtime_footer 同语义）
    assert _model_with_served({"model": "same", "served_model": "same"}) == "same"
    assert _model_with_served({}) is None


def _reasoning_segment(text: str) -> Segment:
    seg = Segment(SegmentType.REASONING, "reasoning_0_panel")
    seg.text_el_id = "reasoning_0_text"
    seg.text = text
    return seg


def test_complete_card_renders_served_model_in_panel_and_footer() -> None:
    """完成态统一面板与 footer 的 model 字段都带「→ 实际模型」."""
    from hermes_fry_cards.cardkit.builder import build_complete_card

    footer_data = {"duration": 10.0, "model": "combo-main", "served_model": "provider/gemini-4"}
    card = build_complete_card(
        segments=[_reasoning_segment("thought")],
        all_tool_steps=[],
        footer_data=footer_data,
        footer_enabled=True,
        footer_fields=[["model"]],
        panel_fields=["model"],
    )

    unified = next(
        el for el in card["body"]["elements"] if el.get("tag") == "collapsible_panel"
    )
    assert "🍟 combo-main → provider/gemini-4" in unified["header"]["title"]["content"]
    assert "🍟 combo-main → provider/gemini-4" in unified["header"]["title"]["i18n_content"]["zh_cn"]

    # footer 里 model 字段的 markdown 也带箭头
    footer_texts = [
        el["content"]
        for el in card["body"]["elements"]
        if el.get("tag") == "markdown"
    ]
    assert any("combo-main → provider/gemini-4" in t for t in footer_texts)


def test_complete_card_hides_arrow_when_served_missing_or_equal() -> None:
    """served 缺失或与配置名相同时，model 字段与旧版渲染完全一致."""
    from hermes_fry_cards.cardkit.builder import build_complete_card

    for footer_data in (
        {"duration": 10.0, "model": "combo-main"},
        {"duration": 10.0, "model": "same", "served_model": "same"},
    ):
        card = build_complete_card(
            segments=[_reasoning_segment("thought")],
            all_tool_steps=[],
            footer_data=footer_data,
            footer_enabled=True,
            footer_fields=[["model"]],
            panel_fields=["model"],
        )
        unified = next(
            el for el in card["body"]["elements"] if el.get("tag") == "collapsible_panel"
        )
        assert "→" not in unified["header"]["title"]["content"]


# ── 10R 路由可见性：body 模型捕获（纯插件侧补位） ─────────────────────────────


class _FakeCompletions:
    """模拟 OpenAI chat.completions 资源对象（实例可赋值）。"""

    def __init__(self, result_fn) -> None:
        self._result_fn = result_fn

    async def create(self, *args, **kwargs):
        return await self._result_fn()


class _FakeChat:
    def __init__(self, completions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions) -> None:
        self.chat = _FakeChat(completions)


class _FakeServedAgent:
    def __init__(self, client=None) -> None:
        self.client = client
        self.last_served_model = None


class _FakeAsyncStream:
    """模拟 OpenAI AsyncStream：__anext__ 产出 chunk，属性可读（.response 等）。"""

    def __init__(self, chunks) -> None:
        self._chunks = list(chunks)
        self.response = SimpleNamespace(status_code=200)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def _chunk(model: str) -> SimpleNamespace:
    return SimpleNamespace(model=model)


@pytest.mark.asyncio
async def test_body_capture_non_stream_writes_last_served_model() -> None:
    """非流式：create 返回带 .model 的响应对象 → agent.last_served_model 更新."""
    from hermes_fry_cards.patch import install_served_model_body_capture

    completions = _FakeCompletions(
        lambda: asyncio.sleep(0, result=SimpleNamespace(model="hy4-preview", choices=[1]))
    )
    agent = _FakeServedAgent(_FakeClient(completions))
    assert install_served_model_body_capture(agent) is True

    # 模拟 Hermes 头捕获每响应先跑（无头 → 置 None），body 捕获后写获胜
    agent.last_served_model = None
    await agent.client.chat.completions.create(model="cbcn/hy4-preview")
    assert agent.last_served_model == "hy4-preview"


@pytest.mark.asyncio
async def test_body_capture_stream_writes_last_served_model() -> None:
    """流式：包装层透传 chunk 且在首个 chunk 就捕获实际模型；.response 属性委托可读."""
    from hermes_fry_cards.patch import install_served_model_body_capture

    async def result_fn():
        return _FakeAsyncStream([_chunk("hy4-preview"), _chunk("hy4-preview")])

    completions = _FakeCompletions(result_fn)
    agent = _FakeServedAgent(_FakeClient(completions))
    install_served_model_body_capture(agent)
    agent.last_served_model = None  # 模拟头捕获先置 None

    stream = await agent.client.chat.completions.create()
    assert stream.response.status_code == 200  # 属性委托（_chat_stream_created 读它）
    chunks = [c async for c in stream]
    assert [c.model for c in chunks] == ["hy4-preview", "hy4-preview"]
    assert agent.last_served_model == "hy4-preview"


@pytest.mark.asyncio
async def test_body_capture_wraps_request_client_factory() -> None:
    """工厂路径：流式每请求 client 返回前被就地包裹."""
    from hermes_fry_cards.patch import install_served_model_body_capture

    completions = _FakeCompletions(
        lambda: asyncio.sleep(0, result=_FakeAsyncStream([_chunk("glm-5.3-flash")]))
    )
    agent = _FakeServedAgent(client=None)
    agent._create_request_openai_client = lambda **kwargs: _FakeClient(completions)
    install_served_model_body_capture(agent)

    client = agent._create_request_openai_client(reason="chat_completion_stream_request")
    stream = await client.chat.completions.create()
    chunks = [c async for c in stream]
    assert chunks[0].model == "glm-5.3-flash"
    assert agent.last_served_model == "glm-5.3-flash"


@pytest.mark.asyncio
async def test_body_capture_is_idempotent() -> None:
    """重复安装不二次包装：create 只被调用一次，结果只捕获一次."""
    from hermes_fry_cards.patch import install_served_model_body_capture

    calls = {"n": 0}

    async def result_fn():
        calls["n"] += 1
        return SimpleNamespace(model="same", choices=[1])

    completions = _FakeCompletions(result_fn)
    agent = _FakeServedAgent(_FakeClient(completions))
    install_served_model_body_capture(agent)
    install_served_model_body_capture(agent)
    install_served_model_body_capture(agent)

    await agent.client.chat.completions.create()
    assert calls["n"] == 1
    assert agent.last_served_model == "same"


def test_body_capture_fail_open_without_openai_surface() -> None:
    """anthropic api_mode 等（无 chat.completions 表面）安装不抛错、不包装."""
    from hermes_fry_cards.patch import install_served_model_body_capture

    agent = _FakeServedAgent(client=SimpleNamespace(messages=object()))
    assert install_served_model_body_capture(agent) is True
    assert install_served_model_body_capture(_FakeServedAgent(client=None)) is True
    assert install_served_model_body_capture(None) is False


def test_start_hook_installs_body_capture() -> None:
    """START 钩子在回合开始时调用 ensure_served_model_capture（幂等安装）."""
    hook = _start_hook("    ")
    assert "ensure_served_model_capture" in hook


@pytest.mark.asyncio
async def test_ensure_served_model_capture_installs_on_cached_agent() -> None:
    """ensure 入口经 _agent_cache 定位 agent 并完成安装."""
    from hermes_fry_cards.patch import ensure_served_model_capture

    completions = _FakeCompletions(
        lambda: asyncio.sleep(0, result=SimpleNamespace(model="m", choices=[1]))
    )
    agent = _FakeServedAgent(_FakeClient(completions))
    gateway = MagicMock(spec=[])
    gateway._agent_cache = {"sk": (agent, 1)}

    ensure_served_model_capture(gateway, "sk")
    await agent.client.chat.completions.create()
    assert agent.last_served_model == "m"

    # 找不到 agent 时静默
    ensure_served_model_capture(MagicMock(spec=[]), "missing")
    ensure_served_model_capture(None, "sk")


# ── body 捕获修复：SDK required_args 包装器 + ctx.agent_holder 安装点 ──────────


@pytest.mark.asyncio
async def test_body_capture_survives_required_args_sync_wrapper() -> None:
    """OpenAI SDK 的 create 被 required_args 同步包装器包过（iscoroutinefunction=False
    但调用返回 awaitable）—— 双模式包装必须照样捕获（v0.6.7 实机回归）。"""
    from hermes_fry_cards.patch import install_served_model_body_capture

    class _SDKCompletions:
        # 模拟真实 SDK：async 实现 + 同步 def 包装（required_args 风格，实例方法可绑定）
        async def _impl(self, *args, **kwargs):
            return SimpleNamespace(model="deepseek-v4.1-flash", choices=[1])

        def create(self, *args, **kwargs):
            return self._impl(*args, **kwargs)

    completions = _SDKCompletions()
    agent = _FakeServedAgent(_FakeClient(completions))
    install_served_model_body_capture(agent)

    result = await agent.client.chat.completions.create(model="cbcn/deepseek-v4.1-flash")
    assert result.model == "deepseek-v4.1-flash"
    assert agent.last_served_model == "deepseek-v4.1-flash"


def test_ensure_served_model_capture_ctx_uses_agent_holder() -> None:
    """ctx.agent_holder 直通本轮 agent：不依赖缓存键（START 处 session_key 未绑定）."""
    from hermes_fry_cards.patch import ensure_served_model_capture_ctx

    completions = _FakeCompletions(
        lambda: asyncio.sleep(0, result=SimpleNamespace(model="m", choices=[1]))
    )
    agent = _FakeServedAgent(_FakeClient(completions))
    ctx = SimpleNamespace(agent_holder=[agent])

    ensure_served_model_capture_ctx(ctx)
    assert getattr(agent.client.chat.completions, "_hl_body_model_wrapped", False) is True

    # holder 为空 / ctx 缺字段：静默
    ensure_served_model_capture_ctx(SimpleNamespace(agent_holder=[]))
    ensure_served_model_capture_ctx(SimpleNamespace())
    ensure_served_model_capture_ctx(None)


def test_tool_and_answer_hooks_install_body_capture() -> None:
    """tool/answer 钩子注入体经 ctx 兜底安装（多步回合最终响应必被覆盖）."""
    assert "ensure_served_model_capture_ctx" in _tool_hook("    ")
    assert "ensure_served_model_capture_ctx" in _answer_hook("    ")
