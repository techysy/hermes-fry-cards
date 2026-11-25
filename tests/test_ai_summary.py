"""完结卡 AI 摘要 — 开关/阈值判定、元素构造、落卡 action、journal 收尾、不阻塞主链路.

真实 HTTP 一律 mock：``llm.chat_completion`` 是唯一的出网点，本套件不打真端点。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_fry_cards import llm
from hermes_fry_cards.cardkit.builder import (
    AI_SUMMARY_ELEMENT_ID,
    UNIFIED_PANEL_ELEMENT_ID,
    build_ai_summary_element,
)
from hermes_fry_cards.config import Config
from hermes_fry_cards.feishu import FeishuAPIError
from hermes_fry_cards.streaming import summary
from hermes_fry_cards.streaming.segments import Segment, SegmentState, SegmentType
from hermes_fry_cards.streaming.session import CardSession, SessionState


def _cfg(raw: dict[str, Any]) -> Config:
    cfg = Config()
    cfg._raw = raw
    return cfg


def _summary_cfg(**kwargs: Any) -> Config:
    sec: dict[str, Any] = {"enabled": True}
    sec.update(kwargs)
    return _cfg({"streaming": {"enabled": True, "ai_summary": sec}})


def _answer_seg(text: str) -> Segment:
    seg = Segment(SegmentType.ANSWER, "streaming_content")
    seg.text = text
    seg.created = True
    return seg


class TestConfigSwitches:
    def test_default_is_off(self) -> None:
        cfg = _cfg({"streaming": {"enabled": True}})
        assert cfg.ai_summary_enabled is False
        assert cfg.ai_summary_min_chars == 800
        assert cfg.ai_summary_model == ""
        assert cfg.ai_summary_timeout_sec == 20.0
        assert cfg.ai_summary_max_output_chars == 160

    def test_hot_reload_picks_up_edits(self, tmp_path: Path) -> None:
        conf = tmp_path / "config.yaml"
        conf.write_text("streaming:\n  ai_summary:\n    enabled: false\n", encoding="utf-8")
        cfg = Config(home=tmp_path)
        assert cfg.ai_summary_enabled is False
        conf.write_text("streaming:\n  ai_summary:\n    enabled: true\n    min_chars: 1200\n", encoding="utf-8")
        assert cfg.ai_summary_enabled is True
        assert cfg.ai_summary_min_chars == 1200

    def test_malformed_values_fall_back_to_defaults(self) -> None:
        cfg = _cfg(
            {"streaming": {"ai_summary": {"min_chars": "abc", "timeout_sec": None, "max_output_chars": [], "model": 5}}}
        )
        assert cfg.ai_summary_min_chars == 800
        assert cfg.ai_summary_timeout_sec == 20.0
        assert cfg.ai_summary_max_output_chars == 160
        assert cfg.ai_summary_model == ""

    def test_out_of_range_values_are_clamped(self) -> None:
        cfg = _cfg({"streaming": {"ai_summary": {"min_chars": -5, "timeout_sec": 9999, "max_output_chars": 1}}})
        assert cfg.ai_summary_min_chars == 0
        assert cfg.ai_summary_timeout_sec == 120.0
        assert cfg.ai_summary_max_output_chars == 40

    def test_non_dict_section_is_tolerated(self) -> None:
        cfg = _cfg({"streaming": {"ai_summary": "yes"}})
        assert cfg.ai_summary_enabled is False
        assert cfg.ai_summary_min_chars == 800


class TestTriggerRules:
    def test_off_switch_never_summarizes(self) -> None:
        assert summary.should_summarize(_cfg({"streaming": {}}), "x" * 5000) is False

    def test_short_reply_is_not_summarized(self) -> None:
        cfg = _summary_cfg(min_chars=800)
        assert summary.should_summarize(cfg, "短回复。") is False
        assert summary.should_summarize(cfg, "长" * 800) is True

    def test_zero_threshold_summarizes_anything(self) -> None:
        assert summary.should_summarize(_summary_cfg(min_chars=0), "一点点") is True

    def test_empty_answer_never_triggers(self) -> None:
        assert summary.should_summarize(_summary_cfg(min_chars=0), "") is False

    def test_answer_text_joins_only_answer_segments(self) -> None:
        state = SegmentState()
        reasoning = Segment(SegmentType.REASONING, "reasoning_content")
        reasoning.text = "内部思考不该进摘要素材"
        state.segments.append(reasoning)
        state.segments.append(_answer_seg("第一段结论"))
        tool = Segment(SegmentType.TOOL, "tool_panel_1")
        state.segments.append(tool)
        state.segments.append(_answer_seg("第二段结论"))
        assert summary.answer_text_of(state.segments) == "第一段结论\n\n第二段结论"

    def test_answer_text_survives_odd_objects(self) -> None:
        assert summary.answer_text_of([None, object(), _answer_seg("ok")]) == "ok"
        assert summary.answer_text_of([]) == ""


class TestSummaryElement:
    def test_label_is_bilingual_and_body_shared(self) -> None:
        el = build_ai_summary_element("本轮改了三处，测试全绿。")
        assert el["tag"] == "markdown"
        assert el["element_id"] == AI_SUMMARY_ELEMENT_ID
        assert el["content"].startswith("🔍 **AI summary**")
        assert el["i18n_content"]["zh_cn"].startswith("🔍 **AI 摘要**")
        assert "本轮改了三处" in el["content"]

    def test_overlong_summary_is_clamped_not_dropped(self) -> None:
        el = build_ai_summary_element("摘要" * 5000)
        assert len(el["content"].encode("utf-8")) <= 2000
        assert "摘要" in el["content"]

    def test_whitespace_only_input_still_yields_label(self) -> None:
        assert AI_SUMMARY_ELEMENT_ID in json.dumps(build_ai_summary_element("   "))


class TestApplyAction:
    def test_before_target_inserts_before_panel(self) -> None:
        action = summary.insert_action({"tag": "markdown"}, UNIFIED_PANEL_ELEMENT_ID)
        assert action["params"]["type"] == "insert_before"
        assert action["params"]["target_element_id"] == UNIFIED_PANEL_ELEMENT_ID

    def test_no_target_appends(self) -> None:
        assert summary.insert_action({"tag": "markdown"}, None)["params"]["type"] == "append"

    def test_has_unified_panel_detects_card_tree(self) -> None:
        card = {"body": {"elements": [{"tag": "collapsible_panel", "element_id": UNIFIED_PANEL_ELEMENT_ID}]}}
        assert summary.has_unified_panel(card) is True
        assert summary.has_unified_panel({"body": {"elements": [{"tag": "markdown"}]}}) is False
        assert summary.has_unified_panel({}) is False
        assert summary.has_unified_panel({"body": "not-a-dict"}) is False

    @pytest.mark.asyncio
    async def test_insert_failure_falls_back_to_append_with_next_sequence(self) -> None:
        client = MagicMock()
        client.cardkit_batch_update = AsyncMock(
            side_effect=[FeishuAPIError("code=300315 not find elementID", 300315), None]
        )
        ok = await summary.apply_summary_element(
            client, "card_1", {"tag": "markdown"}, sequence=9, before_element_id=UNIFIED_PANEL_ELEMENT_ID
        )
        assert ok is True
        first, second = client.cardkit_batch_update.await_args_list
        assert first.kwargs["sequence"] == 9
        assert second.kwargs["sequence"] == 10
        assert second.args[1][0]["params"]["type"] == "append"

    @pytest.mark.asyncio
    async def test_both_paths_failing_returns_false_without_raising(self) -> None:
        client = MagicMock()
        client.cardkit_batch_update = AsyncMock(side_effect=FeishuAPIError("boom", 300317))
        assert await summary.apply_summary_element(client, "card_1", {}, sequence=3, before_element_id=None) is False
        assert client.cardkit_batch_update.await_count == 1

    @pytest.mark.asyncio
    async def test_append_only_path_does_not_double_request(self) -> None:
        client = MagicMock()
        client.cardkit_batch_update = AsyncMock()
        assert await summary.apply_summary_element(client, "card_1", {}, sequence=5, before_element_id=None) is True
        assert client.cardkit_batch_update.await_count == 1


class TestRegistry:
    def test_add_then_drop_lifecycle(self, tmp_path: Path) -> None:
        el = {"tag": "markdown", "element_id": AI_SUMMARY_ELEMENT_ID, "content": "x"}
        summary.add_pending(tmp_path, "card_1", el, sequence=4, before_element_id=UNIFIED_PANEL_ELEMENT_ID)
        data = summary.read_pending(tmp_path)
        assert data["card_1"]["sequence"] == 4
        assert data["card_1"]["before"] == UNIFIED_PANEL_ELEMENT_ID
        summary.drop_pending(tmp_path, "card_1")
        assert summary.read_pending(tmp_path) == {}
        assert not summary.pending_path(tmp_path).exists()

    def test_registry_is_a_separate_file_from_live_cards(self, tmp_path: Path) -> None:
        assert summary.pending_path(tmp_path).name == "fry-cards-ai-summary.json"
        assert summary.pending_path(tmp_path).name != "fry-cards-live.json"

    def test_fifo_cap_bounds_the_file(self, tmp_path: Path) -> None:
        el = {"tag": "markdown", "element_id": AI_SUMMARY_ELEMENT_ID}
        for i in range(summary._PENDING_KEEP + 7):
            summary.add_pending(tmp_path, f"card_{i}", el, sequence=i + 1)
        assert len(summary.read_pending(tmp_path)) == summary._PENDING_KEEP

    def test_corrupt_registry_is_treated_as_empty(self, tmp_path: Path) -> None:
        summary.pending_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        summary.pending_path(tmp_path).write_text("{not json", encoding="utf-8")
        assert summary.read_pending(tmp_path) == {}

    def test_missing_card_id_is_ignored(self, tmp_path: Path) -> None:
        summary.add_pending(tmp_path, "", {"element_id": AI_SUMMARY_ELEMENT_ID}, sequence=1)
        assert summary.read_pending(tmp_path) == {}


class TestRunCompletionSummary:
    @pytest.mark.asyncio
    async def test_success_writes_element_and_clears_registry(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.cardkit_batch_update = AsyncMock()
        cfg = _summary_cfg()
        with patch.object(summary, "generate_summary", AsyncMock(return_value="结论：改动已验证。")) as gen:
            ok = await summary.run_completion_summary(
                client, cfg, tmp_path, "card_ok", sequence=8, answer_text="长" * 900, has_unified_panel=True
            )
        assert ok is True
        gen.assert_awaited_once()
        call = client.cardkit_batch_update.await_args
        assert call.kwargs["sequence"] == 8  # 直接用调用方给的下一个序号
        assert call.args[1][0]["params"]["target_element_id"] == UNIFIED_PANEL_ELEMENT_ID
        assert summary.read_pending(tmp_path) == {}

    @pytest.mark.asyncio
    async def test_generation_failure_leaves_card_registry_and_client_untouched(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.cardkit_batch_update = AsyncMock()
        with patch.object(summary, "generate_summary", AsyncMock(side_effect=llm.LLMError("端点不可用"))):
            ok = await summary.run_completion_summary(
                client, _summary_cfg(), tmp_path, "card_no", sequence=8, answer_text="长" * 900
            )
        assert ok is False
        client.cardkit_batch_update.assert_not_awaited()
        assert summary.read_pending(tmp_path) == {}  # 没生成就不该留条目

    @pytest.mark.asyncio
    async def test_timeout_is_swallowed(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.cardkit_batch_update = AsyncMock()
        with patch.object(summary, "generate_summary", AsyncMock(side_effect=TimeoutError())):
            assert await summary.run_completion_summary(
                client, _summary_cfg(), tmp_path, "card_to", sequence=2, answer_text="x" * 900
            ) is False
        client.cardkit_batch_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unexpected_exception_never_escapes(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.cardkit_batch_update = AsyncMock()
        with patch.object(summary, "generate_summary", AsyncMock(side_effect=RuntimeError("意外"))):
            assert await summary.run_completion_summary(
                client, _summary_cfg(), tmp_path, "card_boom", sequence=2, answer_text="x" * 900
            ) is False

    @pytest.mark.asyncio
    async def test_apply_failure_keeps_entry_for_recovery(self, tmp_path: Path) -> None:
        client = MagicMock()
        # 面板路径与末尾路径都失败（序号复用 300317）：条目必须留着等启动补投
        client.cardkit_batch_update = AsyncMock(side_effect=FeishuAPIError("300317", 300317))
        with patch.object(summary, "generate_summary", AsyncMock(return_value="摘要")):
            ok = await summary.run_completion_summary(
                client, _summary_cfg(), tmp_path, "card_retry", sequence=4, answer_text="x" * 900,
                has_unified_panel=True,
            )
        assert ok is False
        assert client.cardkit_batch_update.await_count == 2  # insert_before 失败后回落 append
        pending = summary.read_pending(tmp_path)
        assert pending["card_retry"]["element"]["element_id"] == AI_SUMMARY_ELEMENT_ID
        assert pending["card_retry"]["sequence"] == 4
        assert pending["card_retry"]["before"] == UNIFIED_PANEL_ELEMENT_ID  # 登记的仍是首选落点

    @pytest.mark.asyncio
    async def test_no_card_id_short_circuits(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.cardkit_batch_update = AsyncMock()
        assert await summary.run_completion_summary(
            client, _summary_cfg(), tmp_path, "", sequence=1, answer_text="x"
        ) is False
        client.cardkit_batch_update.assert_not_awaited()


class TestGenerateSummary:
    @pytest.mark.asyncio
    async def test_happy_path_uses_resolved_endpoint_and_flash_model(self) -> None:
        cfg = _summary_cfg()
        endpoint = llm.LLMEndpoint(base_url="http://e/v1", api_key="k", source="model")
        captured: dict[str, Any] = {}

        def fake(endpoint_arg: Any, model: str, messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
            captured.update(model=model, kw=kw, messages=messages, endpoint=endpoint_arg)
            usage = {"prompt_tokens": 30, "completion_tokens": 12}
            return {"text": "摘要", "usage": usage, "elapsed_ms": 2000, "model": model}

        with patch.object(summary, "resolve_llm_endpoint", return_value=endpoint), patch.object(
            summary, "default_small_model", return_value="cbcn/glm-5.3-flash"
        ), patch.object(summary, "chat_completion", fake):
            assert await summary.generate_summary(cfg, "正文" * 500) == "摘要"
        assert captured["endpoint"] is endpoint
        assert captured["model"] == "cbcn/glm-5.3-flash"
        assert captured["kw"]["timeout_sec"] == 20.0
        assert captured["kw"]["max_tokens"] == cfg.ai_summary_max_output_chars * 2 + 64
        assert "正文" in captured["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_configured_model_wins_over_auto_pick(self) -> None:
        cfg = _summary_cfg(model="big/opus-5")
        endpoint = llm.LLMEndpoint(base_url="http://e/v1", api_key="k")
        seen: dict[str, Any] = {}

        def fake(endpoint_arg: Any, model: str, messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
            seen["model"] = model
            return {"text": "ok", "usage": {}, "elapsed_ms": 1, "model": model}

        with patch.object(summary, "resolve_llm_endpoint", return_value=endpoint), patch.object(
            summary, "chat_completion", fake
        ):
            await summary.generate_summary(cfg, "x" * 900)
        assert seen["model"] == "big/opus-5"

    @pytest.mark.asyncio
    async def test_missing_endpoint_raises_human_error(self) -> None:
        with patch.object(summary, "resolve_llm_endpoint", return_value=None), pytest.raises(
            llm.LLMError, match="没有可用的模型端点"
        ):
            await summary.generate_summary(_summary_cfg(), "x" * 900)

    @pytest.mark.asyncio
    async def test_no_candidate_model_raises(self) -> None:
        endpoint = llm.LLMEndpoint(base_url="http://e/v1", api_key="k")
        with patch.object(summary, "resolve_llm_endpoint", return_value=endpoint), patch.object(
            summary, "default_small_model", return_value=""
        ), pytest.raises(llm.LLMError, match="没有可用的小模型"):
            await summary.generate_summary(_summary_cfg(), "x" * 900)

    @pytest.mark.asyncio
    async def test_timeout_sec_reaches_the_http_layer(self) -> None:
        """``streaming.ai_summary.timeout_sec`` 必须真的传到请求上（socket 超时才是硬边界）。"""
        cfg = _summary_cfg(timeout_sec=3)
        endpoint = llm.LLMEndpoint(base_url="http://e/v1", api_key="k")
        seen: dict[str, Any] = {}

        def fake(endpoint_arg: Any, model: str, messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
            seen["timeout_sec"] = kw["timeout_sec"]
            return {"text": "ok", "usage": {}, "elapsed_ms": 1, "model": model}

        with patch.object(summary, "resolve_llm_endpoint", return_value=endpoint), patch.object(
            summary, "default_small_model", return_value="m"
        ), patch.object(summary, "chat_completion", fake):
            assert await summary.generate_summary(cfg, "x" * 900) == "ok"
        assert seen["timeout_sec"] == 3

    @pytest.mark.asyncio
    async def test_wait_for_is_the_outer_hard_boundary(self) -> None:
        """请求线程卡住时（极端：驱动不遵守 socket timeout），外层 wait_for 也得收得住。

        不等 wait_for 的话，收尾任务会被一个永不返回的线程吊死。
        """
        endpoint = llm.LLMEndpoint(base_url="http://e/v1", api_key="k")

        def hang(endpoint_arg: Any, model: str, messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
            time.sleep(5.0)
            return {"text": "迟到的摘要", "usage": {}, "elapsed_ms": 5000, "model": model}

        # timeout_sec 配置下限是 1s；把外层余量压到 0，验证的就是「1s 整」这道闸
        with patch.object(summary, "resolve_llm_endpoint", return_value=endpoint), patch.object(
            summary, "default_small_model", return_value="m"
        ), patch.object(summary, "chat_completion", hang), patch.object(
            summary, "_TIMEOUT_MARGIN_SEC", 0.0
        ):
            started = time.monotonic()
            with pytest.raises(asyncio.TimeoutError):
                await summary.generate_summary(_summary_cfg(timeout_sec=1), "x" * 900)
            elapsed = time.monotonic() - started
        assert 0.9 <= elapsed < 3.0  # 到点就放弃，不陪线程睡满 5s

    @pytest.mark.asyncio
    async def test_blank_model_text_is_an_error_not_empty_summary(self) -> None:
        endpoint = llm.LLMEndpoint(base_url="http://e/v1", api_key="k")
        with patch.object(summary, "resolve_llm_endpoint", return_value=endpoint), patch.object(
            summary, "default_small_model", return_value="m"
        ), patch.object(summary, "chat_completion", lambda *a, **k: {"text": "   "}), pytest.raises(
            llm.LLMError, match="摘要为空"
        ):
            await summary.generate_summary(_summary_cfg(), "x" * 900)


class TestRecovery:
    @pytest.mark.asyncio
    async def test_pending_entry_is_reapplied_with_jump(self, tmp_path: Path) -> None:
        el = build_ai_summary_element("补投摘要")
        summary.add_pending(tmp_path, "card_a", el, sequence=5)
        client = MagicMock()
        client.cardkit_batch_update = AsyncMock()
        assert await summary.recover_pending_summaries(client, tmp_path) == 1
        call = client.cardkit_batch_update.await_args
        assert call.kwargs["sequence"] == 5 + summary._RECOVERY_SEQ_JUMP
        assert summary.read_pending(tmp_path) == {}

    @pytest.mark.asyncio
    async def test_failures_are_counted_and_dropped_after_limit(self, tmp_path: Path) -> None:
        el = build_ai_summary_element("摘要")
        summary.add_pending(tmp_path, "card_b", el, sequence=2)
        client = MagicMock()
        client.cardkit_batch_update = AsyncMock(side_effect=FeishuAPIError("gone", 300318))
        for expected in (1, 2):
            assert await summary.recover_pending_summaries(client, tmp_path) == 0
            assert summary.read_pending(tmp_path)["card_b"]["attempts"] == expected
        assert await summary.recover_pending_summaries(client, tmp_path) == 0
        assert summary.read_pending(tmp_path) == {}  # 第三败出列，不再无限重试

    @pytest.mark.asyncio
    async def test_garbage_entries_are_dropped_without_api_calls(self, tmp_path: Path) -> None:
        summary.pending_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        summary.pending_path(tmp_path).write_text(
            json.dumps({
                "card_c": {"element": {"tag": "markdown"}, "sequence": 3},
                "card_d": "junk",
                "card_e": {"sequence": 0},
            }),
            encoding="utf-8",
        )
        client = MagicMock()
        client.cardkit_batch_update = AsyncMock()
        assert await summary.recover_pending_summaries(client, tmp_path) == 0
        client.cardkit_batch_update.assert_not_awaited()
        assert summary.read_pending(tmp_path) == {}

    @pytest.mark.asyncio
    async def test_empty_registry_makes_no_calls(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.cardkit_batch_update = AsyncMock()
        assert await summary.recover_pending_summaries(client, tmp_path) == 0
        client.cardkit_batch_update.assert_not_awaited()


# ---------------------------------------------------------------------------
# 主链路集成：摘要只在封口成功后排队，且绝不拖慢/改变收尾
# ---------------------------------------------------------------------------

from hermes_fry_cards.controller import StreamCardController  # noqa: E402


def _ctrl_with_client(raw: dict[str, Any]) -> StreamCardController:
    ctrl = StreamCardController()
    ctrl._cfg = _cfg(raw)
    ctrl._initialized = True
    names = ["cardkit_batch_update", "cardkit_update", "cardkit_close_streaming", "cardkit_create", "reply_card_by_id"]
    client = MagicMock(spec=names)
    client.cardkit_close_streaming = AsyncMock()
    client.cardkit_update = AsyncMock()
    client.cardkit_batch_update = AsyncMock()
    ctrl._client = client
    return ctrl


def _raw(on: bool = True, min_chars: int = 100) -> dict[str, Any]:
    return {
        "streaming": {
            "enabled": True,
            "header": {"enabled": True},
            "ai_summary": {"enabled": on, "min_chars": min_chars},
        },
        "feishu": {"app_id": "app", "app_secret": "secret"},
    }


def _session(msg_id: str, answer: str, *, state: SessionState = SessionState.STREAMING) -> CardSession:
    session = CardSession(msg_id, "chat_1", asyncio.get_running_loop())
    session.state = state
    session.card_id = f"card_{msg_id}"
    session.card_msg_id = f"cardmsg_{msg_id}"
    session.segment_state = SegmentState()
    session.segment_state.on_answer_delta(answer)
    return session


class TestCompletionHook:
    """封口成功之后才排队摘要；判定必须用重建前捕获的 is_error / is_aborted.

    断言看的是「调度决策」（``run_completion_summary`` 有没有被调用、``_fire_and_forget``
    有没有被调用），不依赖后台任务何时跑完——后者会让「不该摘要」的用例因为任务
    尚未执行而假通过（本套件初版就踩过这个坑）。
    """

    @staticmethod
    def _no_fire(ctrl: StreamCardController) -> MagicMock:
        fire = MagicMock()
        ctrl._fire_and_forget = fire  # type: ignore[assignment]
        return fire

    @pytest.mark.asyncio
    async def test_long_answer_schedules_summary_after_sealing(self) -> None:
        ctrl = _ctrl_with_client(_raw())
        session = _session("msg_on", "结" * 200)
        ctrl._sessions["msg_on"] = session
        fire = self._no_fire(ctrl)
        with patch.object(summary, "run_completion_summary", MagicMock(return_value="coro")) as make:
            assert await ctrl._do_complete_card(session) is True
        kwargs = make.call_args.kwargs
        # 不变量：摘要序号必须严格大于服务端最后一次落卡用过的序号（否则 300317）
        sealed_seq = ctrl._client.cardkit_update.await_args.kwargs["sequence"]
        assert kwargs["sequence"] == sealed_seq + 1
        assert "结" * 200 in kwargs["answer_text"]
        assert kwargs["has_unified_panel"] is False
        fire.assert_called_once()
        assert fire.call_args.args[0] == "coro"

    @pytest.mark.asyncio
    async def test_switch_off_makes_no_model_call_at_all(self) -> None:
        ctrl = _ctrl_with_client(_raw(on=False))
        session = _session("msg_off", "结" * 5000)
        ctrl._sessions["msg_off"] = session
        fire = self._no_fire(ctrl)
        with patch.object(summary, "run_completion_summary", MagicMock()) as make:
            assert await ctrl._do_complete_card(session) is True
        make.assert_not_called()
        fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_short_reply_below_threshold_is_not_summarized(self) -> None:
        ctrl = _ctrl_with_client(_raw(min_chars=800))
        session = _session("msg_short", "短回复。")
        ctrl._sessions["msg_short"] = session
        self._no_fire(ctrl)
        with patch.object(summary, "run_completion_summary", MagicMock()) as make:
            assert await ctrl._do_complete_card(session) is True
        make.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_outcome_is_not_summarized(self) -> None:
        """错误轮次不摘要 —— state 会被落卡成功覆写成 COMPLETED，判定必须看 is_error."""
        ctrl = _ctrl_with_client(_raw())
        session = _session("msg_err", "结" * 500)
        session.mark_failed(reason="agent_returned_error")
        ctrl._sessions["msg_err"] = session
        fire = self._no_fire(ctrl)
        with patch.object(summary, "run_completion_summary", MagicMock()) as make:
            assert await ctrl._do_complete_card(session) is True
            assert session.state == SessionState.COMPLETED  # 前提：state 确实被覆写
        make.assert_not_called()
        fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_aborted_outcome_is_not_summarized(self) -> None:
        """被 /stop 掐断的残缺卡不摘要（同样不能靠 state 判定）."""
        ctrl = _ctrl_with_client(_raw())
        session = _session("msg_abort", "结" * 500)
        session.state = SessionState.ABORTED
        ctrl._sessions["msg_abort"] = session
        fire = self._no_fire(ctrl)
        with patch.object(summary, "run_completion_summary", MagicMock()) as make:
            assert await ctrl._do_complete_card(session) is True
            assert session.state == SessionState.COMPLETED
        make.assert_not_called()
        fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_seal_does_not_summarize(self) -> None:
        ctrl = _ctrl_with_client(_raw())
        ctrl._client.cardkit_close_streaming = AsyncMock(side_effect=FeishuAPIError("gone", 300318))
        ctrl._client.cardkit_update = AsyncMock(side_effect=FeishuAPIError("gone", 300318))
        ctrl._client.cardkit_batch_update = AsyncMock(side_effect=FeishuAPIError("gone", 300318))
        session = _session("msg_noseal", "结" * 500)
        ctrl._sessions["msg_noseal"] = session
        fire = self._no_fire(ctrl)
        with patch.object(summary, "run_completion_summary", MagicMock()) as make, patch.object(
            asyncio, "sleep", AsyncMock()
        ):  # 重试退避不真等
            assert await ctrl._do_complete_card(session) is False
        make.assert_not_called()
        fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_slow_summary_does_not_delay_completion(self) -> None:
        """模型再慢也不拖收尾：任务只排队，完成调用立刻返回."""
        ctrl = _ctrl_with_client(_raw())
        session = _session("msg_slow", "结" * 300)
        ctrl._sessions["msg_slow"] = session
        self._no_fire(ctrl)
        started_at = time.monotonic()
        with patch.object(summary, "run_completion_summary", MagicMock(return_value="coro")):
            assert await ctrl._do_complete_card(session) is True
        assert time.monotonic() - started_at < 1.0

    @pytest.mark.asyncio
    async def test_summary_failure_does_not_affect_completion_result(self) -> None:
        """摘要任务在后台炸掉：完成结果、卡片状态、卡片更新次数都不受影响."""
        ctrl = _ctrl_with_client(_raw())
        session = _session("msg_badsum", "结" * 300)
        ctrl._sessions["msg_badsum"] = session
        with patch.object(summary, "run_completion_summary", AsyncMock(side_effect=llm.LLMError("端点炸了"))):
            assert await ctrl._do_complete_card(session) is True
            for _ in range(5):
                await asyncio.sleep(0)  # 让后台任务真的跑完
        assert session.state == SessionState.COMPLETED
        assert ctrl._client.cardkit_update.await_count == 1  # 没有整卡重发
        assert ctrl._client.cardkit_batch_update.await_count == 0

    @pytest.mark.asyncio
    async def test_completion_still_reports_text_fallback_when_seal_fails(self) -> None:
        """封卡失败 → 走原有文本回退，与摘要无关（摘要不得抢这条道）."""
        ctrl = _ctrl_with_client(_raw())
        session = _session("msg_fb", "结" * 300)
        ctrl._sessions["msg_fb"] = session
        with patch.object(summary, "run_completion_summary", MagicMock()) as make, patch.object(
            ctrl, "_do_complete_card_inner", AsyncMock(return_value=False)
        ):
            assert await ctrl._complete_session_wait(session) is False
        make.assert_not_called()

    @pytest.mark.asyncio
    async def test_element_budget_gate_skips_model_call_entirely(self) -> None:
        """卡片元素数逼近 200 上限（#56 口径的递归计数）时：补元素注定撞 300305，
        连模型都不该调——省一次白烧。"""
        ctrl = _ctrl_with_client(_raw())
        session = _session("msg_budget", "结" * 300)
        ctrl._sessions["msg_budget"] = session
        self._no_fire(ctrl)
        with patch.object(summary, "run_completion_summary", MagicMock()) as make, patch(
            "hermes_fry_cards.streaming.controller.ELEMENT_THRESHOLD", 1
        ):  # 阈值压到 1：这张小卡的递归元素数已 ≥1，补第 2 个元素注定撞 300305
            assert await ctrl._do_complete_card(session) is True
        make.assert_not_called()

    @pytest.mark.asyncio
    async def test_panel_present_routes_summary_before_panel(self) -> None:
        ctrl = _ctrl_with_client(_raw())
        session = _session("msg_panel", "结" * 300)
        reasoning = Segment(SegmentType.REASONING, "reasoning_content")
        reasoning.text = "先看基线再改 builder。"
        reasoning.text_el_id = "reasoning_0_text"
        reasoning.created = True
        session.segment_state.segments.insert(0, reasoning)
        session.footer = {"duration": 30.0}  # 超过面板最小耗时阈值 → 统一面板渲染
        ctrl._sessions["msg_panel"] = session
        self._no_fire(ctrl)
        with patch.object(summary, "run_completion_summary", MagicMock(return_value="coro")) as make:
            assert await ctrl._do_complete_card(session) is True
        assert make.call_args.kwargs["has_unified_panel"] is True

class TestStartupRecoveryWiring:
    """启动对账必须接进已有的「一进程一次」清扫，且失败不拖累封卡清扫."""

    @pytest.mark.asyncio
    async def test_recovery_runs_before_stale_card_sweep(self) -> None:
        ctrl = _ctrl_with_client(_raw())
        order: list[str] = []

        async def fake_recover(client: Any, home: Path) -> int:
            order.append("summary")
            return 0

        def snapshot() -> dict[str, Any]:
            order.append("live-cards")
            return {}

        with patch.object(summary, "recover_pending_summaries", fake_recover), patch.object(
            ctrl, "_read_live_cards", side_effect=snapshot
        ):
            await ctrl._sweep_stale_cards()
        assert order == ["summary", "live-cards"]  # 摘要先补，封卡清扫照常走

    @pytest.mark.asyncio
    async def test_recovery_failure_does_not_break_card_sweep(self) -> None:
        ctrl = _ctrl_with_client(_raw())
        with patch.object(
            summary, "recover_pending_summaries", AsyncMock(side_effect=RuntimeError("意外"))
        ), patch.object(ctrl, "_read_live_cards", return_value={}) as read_live:
            await ctrl._sweep_stale_cards()  # 不抛
        read_live.assert_called()

    @pytest.mark.asyncio
    async def test_recovered_count_is_logged_not_raised(self, caplog: pytest.LogCaptureFixture) -> None:
        ctrl = _ctrl_with_client(_raw())
        with caplog.at_level("INFO"), patch.object(
            summary, "recover_pending_summaries", AsyncMock(return_value=2)
        ):
            await ctrl._recover_ai_summaries()
        assert "re-applied 2 card(s)" in caplog.text

    @pytest.mark.asyncio
    async def test_recovery_skipped_without_client(self) -> None:
        ctrl = StreamCardController()
        ctrl._client = None
        with patch.object(summary, "recover_pending_summaries", AsyncMock()) as rec:
            await ctrl._recover_ai_summaries()
        rec.assert_not_awaited()

    def test_recovery_uses_its_own_registry_file(self, tmp_path: Path) -> None:
        """摘要补投绝不能碰网关重启封卡的登记表（否则完成的卡会被扫成「已中断」）."""
        assert summary.pending_path(tmp_path).name == "fry-cards-ai-summary.json"
        assert (tmp_path / "state" / "fry-cards-live.json") != summary.pending_path(tmp_path)
        summary.add_pending(tmp_path, "card_x", build_ai_summary_element("摘要"), sequence=3)
        ctrl = StreamCardController(profile_home=tmp_path)
        assert ctrl._read_live_cards() == {}
