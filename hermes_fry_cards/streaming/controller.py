"""流式卡片的异步 API 编排 — 创建、刷新、拆卡、完成."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from ..cardkit.builder import (
    LOADING_ELEMENT_ID,
    NOTICE_ELEMENT_ID,
    TOOL_PANEL_ELEMENT_ID,
    build_background_card,
    build_complete_card,
    build_cron_card,
    build_overflow_fallback_card,
    build_streaming_card_v2,
    build_streaming_notice_element,
    count_elements_recursive,
    strip_unified_panel,
)
from ..cardkit.markdown import (
    _REASONING_MAX_BYTES,
    clamp_utf8,
    optimize_markdown_style,
    prepare_streaming_markdown,
)
from ..feishu import (
    CARDKIT_CONTENT_FAILED,
    CARDKIT_ELEMENT_LIMIT,
    CARDKIT_ELEMENT_LIMIT_TOTAL,
    CARDKIT_RATE_LIMITED,
    CARDKIT_STREAMING_CLOSED,
    FeishuAPIError,
)
from .diagnostics import (
    compact_ids,
    extract_duplicate_element_id,
    extract_missing_element_id,
    segment_state_for_log,
    summarize_actions,
)
from .image import ImageResolver
from .segment_helper import (
    ELEMENT_THRESHOLD,
    FOOTER_RESERVE,
    build_add_segment_action,
    build_reasoning_finalized_action,
    build_tool_update_action,
    estimate_answer_elements,
    estimate_segment_elements,
    estimate_tool_elements,
    find_tool_split_offset,
    panel_steps_start,
    tool_segment_end,
)
from .segments import Segment, SegmentState, SegmentType
from .session import SessionState
from .text import split_reasoning_text, strip_reasoning_tags
from .tooluse import ToolUseTracker
from .unavailable_guard import is_terminal_api_code

if TYPE_CHECKING:
    from ..config import Config
    from ..feishu import FeishuClient
    from .session import CardSession
    from .tooluse import ToolDisplayStep

_logger = logging.getLogger("hermes_fry_cards")


def _clean_delivered_text(content: str) -> str:
    """cron / background 静态卡片投递前的 reasoning 清理.

    与完成态卡片同一类模型输出，可能带 <think> 标签；剥空时保底用原文，
    空卡片比展示推理更糟。
    """
    stripped = strip_reasoning_tags(content)
    return stripped if stripped.strip() else content


def _latest_notice(session: CardSession) -> str | None:
    """流式态只展示最新一条提示（新卡建立时带上，避免重定向后新卡看不到提示）."""
    notices = _session_notices(session)
    return notices[-1] if notices else None


def _session_notices(session: CardSession) -> list[str]:
    """完成态卡片的通知区文案（Hermes 原文，按到达顺序，去重）."""
    return session.active_notices()


def _sync_notice_elements(session: CardSession) -> list[str]:
    """通知区文案同步进元素记账，返回文案列表.

    完成态每条提示占一个 markdown 元素（``fry_notice{_i}``），会在 loading 基线之外
    多出 N 个元素。这里把 N 记进 ``notice_element_count`` 并补进 ``element_count``，
    让拆卡阈值看到卡片真实元素数（#43 评审；接近 300305 时少算一格就会撞上限）。
    幂等：只按变化量调整。
    """
    notices = session.active_notices()
    delta = len(notices) - session.notice_element_count
    if delta:
        session.element_count = max(0, session.element_count + delta)
        session.notice_element_count = len(notices)
    return notices


def _split_notice_slots(session: CardSession) -> tuple[list[str], list[str]]:
    """通知区按展示位拆分（#43 反馈）：(顶部=完成后的回执, 底部=进行中提示)."""
    return session.split_notices()


async def _resolve_answer_images(
    segments: list[Segment],
    resolver: ImageResolver,
    *,
    log_prefix: str,
) -> None:
    """解析 answer segment 中的 markdown 图片，并原地更新文本."""
    for seg in segments:
        if seg.type != SegmentType.ANSWER or not seg.text:
            continue
        try:
            seg.text = await resolver.resolve_await(seg.text)
        except Exception:
            _logger.debug("%s image resolve failed: el=%s", log_prefix, seg.el_id, exc_info=True)


class StreamingController:
    """流式卡片专用方法 — 由 StreamCardController 继承."""

    _client: FeishuClient | None
    _cfg: Config
    _ensure_init: Callable[..., Coroutine[Any, Any, None]]
    _cleanup: Callable[[str], None]
    _cleanup_session: Callable[[CardSession], None]
    _flush_deferred_background_reviews: Callable[[CardSession], None]
    _wait_for_card_creation: Callable[[CardSession], Coroutine[Any, Any, bool]]

    def _schedule_flush(self, session: CardSession) -> None:
        if session.state == SessionState.IDLE or session.state.is_terminal:
            return
        if session.state == SessionState.CLARIFY_PAUSED:
            return
        if session.guard.should_skip("_schedule_flush"):
            return
        session.flush.schedule_update(lambda: self._do_flush(session))

    def _on_thinking_segment(self, session: CardSession, text: str) -> bool:
        segment_state = session.segment_state
        if segment_state is None:
            return False
        split = split_reasoning_text(text)
        reasoning = split.get("reasoning_text")
        answer = split.get("answer_text")

        if reasoning and self._cfg.show_reasoning:
            segment_state.on_reasoning_delta(reasoning)
        if answer:
            segment_state.on_answer_delta(answer)
        if not (reasoning and self._cfg.show_reasoning) and not answer:
            return False
        self._schedule_flush(session)
        return True

    async def _do_create_card(self, session: CardSession) -> None:
        """创建只有 loading 的流式占位卡片."""
        if session.state != SessionState.IDLE:
            return
        session.state = SessionState.CREATING
        if session.segment_state is None:
            session.segment_state = SegmentState(max_reasoning_panels=self._cfg.max_reasoning_panels)

        try:
            await self._ensure_init()
            assert self._client is not None

            reply_to_message_id = session.anchor_id or session.message_id
            notice_text = _latest_notice(session)
            card = build_streaming_card_v2(
                show_tool_use=self._cfg.show_tool_use,
                show_reasoning=False,
                show_streaming_element=False,
                header_enabled=self._cfg.header_enabled,
                text_size=self._cfg.body_text_size,
                width_mode=self._cfg.width_mode,
                notice=notice_text,
            )
            card_id = await self._client.cardkit_create(card)
            if session.synthetic:
                # 合成轮次（后台通知/clarify 恢复）无真实消息可回复，卡片直接投递到聊天
                card_msg_id = await self._client.send_card_to_chat(
                    chat_id=session.chat_id,
                    card={"type": "card", "data": {"card_id": card_id}},
                )
            else:
                try:
                    card_msg_id = await self._client.reply_card_by_id(
                        reply_to_message_id,
                        card_id,
                    )
                except FeishuAPIError as error:
                    if error.code != CARDKIT_CONTENT_FAILED:
                        raise
                    card_id = await self._client.cardkit_create(card)
                    try:
                        card_msg_id = await self._client.reply_card_by_id(
                            reply_to_message_id,
                            card_id,
                        )
                    except FeishuAPIError:
                        card_msg_id = await self._client.send_card_to_chat(
                            chat_id=session.chat_id,
                            card={"type": "card", "data": {"card_id": card_id}},
                        )
            session.set_card(card_id=card_id, card_msg_id=card_msg_id)
            self._track_live_card(session)  # #43：卡片建好后登记，网关重启时据此封卡
            session.element_count = 1  # loading element
            # 流式建卡时已带上最新一条提示（_latest_notice），记账同步这一格
            session.notice_element_count = 1 if notice_text else 0
            session.element_count += session.notice_element_count
            # 提示是卡上真实存在的元素：登记给段状态，首段正文到达时原地接管（#issue）
            if session.segment_state is not None:
                session.segment_state.bind_notice_element(
                    NOTICE_ELEMENT_ID if notice_text else None, notice_text
                )
            # 配置基准节流（毫秒→秒）；载荷自适应降频（#46）以它为下限
            session.flush.set_throttle(self._cfg.flush_interval_ms / 1000.0)

            if session.image_resolver is None and self._client:
                session.image_resolver = ImageResolver(
                    client=self._client,
                    on_image_resolved=lambda: self._schedule_flush(session),
                )

            session.flush.set_card_message_ready(True)
            if session.state == SessionState.CREATING:
                session.state = SessionState.STREAMING
            if session.segment_state and session.segment_state.has_dirty:
                self._schedule_flush(session)
            _logger.info(
                "card_created: msg=%s card=%s reply_to=%s",
                session.message_id[:12],
                (session.card_id or "")[:12],
                reply_to_message_id[:12],
            )
        except FeishuAPIError as e:
            if is_terminal_api_code(e.code):
                # 目标消息已删除/撤回/open_message_id 失效：卡片无处可投，属预期路径。
                # 该场景可一天复现多次，只留一行 warning，不打堆栈（避免 traceback 刷屏）。
                _logger.warning(
                    "card_reply_failed: msg=%s stage=create/reply code=%s target_message_gone err=%s",
                    session.message_id[:12],
                    e.code,
                    e,
                )
            else:
                _logger.warning(
                    "card_reply_failed: msg=%s stage=create/reply code=%s err=%s",
                    session.message_id[:12],
                    e.code,
                    e,
                    exc_info=True,
                )
            if hasattr(self, "_mark_text_fallback_needed"):
                self._mark_text_fallback_needed(session)
            session.mark_failed(reason="cardkit_create_api_error")
        except Exception as e:
            _logger.exception(
                "card_reply_failed: msg=%s stage=create unexpected_error=%s",
                session.message_id[:12],
                e,
            )
            session.mark_failed(reason="card_create_unexpected_error")

    async def _do_flush(self, session: CardSession) -> None:
        """幂等 flush：按 segment 顺序处理结构性变更，超阈值时拆卡."""
        if session.state.is_terminal or not session.card_id:
            return
        segment_state = session.segment_state
        if segment_state is None:
            return

        assert self._client is not None
        segments = segment_state.segments
        all_steps = session.tool_use.build_display_steps()
        # 本次 flush 的近似载荷（字节）：batch actions + 流式文本，供自适应降频分档（#46）
        payload_bytes = 0

        # ── 步骤 0: answer 段动态重估 ──
        # answer 元素在服务端会随内容膨胀（表格展开为单元格、完成态长文切块），
        # 创建时的一次性估算追不上文本增长。每次 flush 按当前文本重算，
        # 差值同步进 element_count，让阈值判断反映卡片真实元素数，
        # 主动在超阈值前拆卡，而不是撞 300305 后被动拆。
        for seg in segments[session.split_index:]:
            if seg.type == SegmentType.ANSWER and seg.created:
                new_est = estimate_answer_elements(seg.text)
                delta = new_est - seg.element_estimate
                if delta:
                    session.element_count += delta
                    seg.element_estimate = new_est

        # ── 步骤 1: batch_update — 按 segment 顺序处理结构性变更 ──
        actions: list[dict[str, Any]] = []
        new_el_ids: set[str] = set()
        new_el_estimates: dict[str, int] = {}
        updated_tool_segs: list[Segment] = []
        new_el_total = 0  # 同一 flush 内新 segment 估计 + dirty segment 增量的累计
        # 合并面板追踪：多个 tool segment 共享同一个底部面板
        tool_panel_element_id: str | None = None

        for i, seg in enumerate(segments):
            if i < session.split_index:
                continue

            # show_tool_use=False: 流式态跳过所有 TOOL segment 处理
            # （新建与 dirty 更新两条路径），只保留 reasoning/answer
            if seg.type == SegmentType.TOOL and not self._cfg.show_tool_use:
                if not seg.created:
                    seg.created = True  # 防止 next flush 再次进入 not created 分支
                seg.dirty = False
                continue

            if not seg.created:
                # 新卡首个 TOOL 段会整体替换共享面板（而不是在旧面板上叠加）：
                # 容量判断按「替换后的面板估算」算，并先扣掉本卡已入账的面板量，
                # 否则会把旧面板和新面板各算一遍，拆出来的卡只有 loading 图标（#41）。
                panel_replaces = (
                    seg.type == SegmentType.TOOL
                    and not session.tool_panel_created
                    and i == next(
                        (j for j in range(session.split_index, len(segments)) if segments[j].type == SegmentType.TOOL),
                        None,
                    )
                )
                if panel_replaces:
                    estimated = estimate_tool_elements(
                        panel_steps_start(segments, session.split_index, seg.tool_offset),
                        seg.tool_end_offset if seg.tool_end_offset else len(all_steps),
                        all_steps,
                    )
                    new_el_total -= session.tool_panel_estimate
                    if new_el_total < 0:
                        new_el_total = 0
                else:
                    estimated = estimate_segment_elements(seg, all_steps)
                if (
                    seg.type == SegmentType.TOOL
                    and session.element_count + new_el_total + estimated + FOOTER_RESERVE > ELEMENT_THRESHOLD
                    and not session.split_disabled
                ):
                    split_offset = find_tool_split_offset(
                        base_count=session.element_count + new_el_total,
                        seg=seg,
                        all_steps=all_steps,
                        panel_start=panel_steps_start(segments, session.split_index, seg.tool_offset),
                    )
                    if split_offset is not None:
                        segment_state.split_tool_segment(i, split_offset)
                        if panel_replaces:
                            estimated = estimate_tool_elements(
                                panel_steps_start(segments, session.split_index, seg.tool_offset),
                                seg.tool_end_offset if seg.tool_end_offset else len(all_steps),
                                all_steps,
                            )
                        else:
                            estimated = estimate_segment_elements(seg, all_steps)
                if (
                    session.element_count + new_el_total + estimated + FOOTER_RESERVE > ELEMENT_THRESHOLD
                    and session.element_count + new_el_total > 1
                    and not session.split_disabled
                ):
                    split_ok = await self._do_split_card(
                        session, i, actions, new_el_ids, new_el_estimates, updated_tool_segs,
                    )
                    if not split_ok:
                        return
                    actions = []
                    new_el_ids = set()
                    new_el_estimates = {}
                    updated_tool_segs = []
                    new_el_total = 0

                if seg.type == SegmentType.TOOL:
                    # 合并面板：所有 tool segment 共享同一个底部面板
                    if not session.tool_panel_created:
                        # 首次：更新流式卡片中已有的 pending 面板（element_id=TOOL_PANEL_ELEMENT_ID）
                        session.tool_panel_created = True
                        tool_panel_element_id = TOOL_PANEL_ELEMENT_ID
                        seg.created = True
                        # 本卡面板从本卡首个工具段开始（拆卡后不重发旧卡步骤）
                        panel_start = panel_steps_start(segments, session.split_index, seg.tool_offset)
                        panel_end = seg.tool_end_offset if seg.tool_end_offset else len(all_steps)
                        actions.append(build_tool_update_action(
                            element_id=TOOL_PANEL_ELEMENT_ID,
                            steps=all_steps[panel_start:panel_end],
                        ))
                        updated_tool_segs.append(seg)
                        # 面板子元素计入卡片元素总数。这里只登记「新面板估算」，
                        # session.tool_panel_estimate 由 _do_batch_update 成功后再推进 ——
                        # 提前赋值会让它算出的增量恒为 0（#41）。
                        estimate = estimate_tool_elements(panel_start, panel_end, all_steps)
                        new_el_estimates[TOOL_PANEL_ELEMENT_ID] = estimate
                        new_el_total += estimate
                    else:
                        # 面板已存在 → 跳过（后续 dirty 分支会更新）
                        seg.created = True
                        seg.dirty = False
                else:
                    new_el_ids.add(seg.el_id)
                    new_el_estimates[seg.el_id] = estimated
                    new_el_total += estimated
                    actions.append(build_add_segment_action(seg, all_steps, text_size=self._cfg.body_text_size))
                if (
                    seg.type == SegmentType.TOOL
                    and i + 1 < len(segments)
                    and segments[i + 1].type == SegmentType.TOOL
                    and segments[i + 1].tool_offset == seg.tool_end_offset
                    and not session.split_disabled
                ):
                    split_ok = await self._do_split_card(
                        session, i + 1, actions, new_el_ids, new_el_estimates, updated_tool_segs,
                    )
                    if not split_ok:
                        return
                    actions = []
                    new_el_ids = set()
                    new_el_estimates = {}
                    updated_tool_segs = []
                    new_el_total = 0
            elif seg.type == SegmentType.REASONING and seg.elapsed_ms > 0 and not seg.reasoning_finalized:
                _logger.info(
                    "CardKit reasoning finalized: msg=%s el=%s elapsed=%.0fms seq=%d",
                    session.message_id[:12],
                    seg.el_id,
                    seg.elapsed_ms,
                    session.sequence + 1,
                )
                actions.append(build_reasoning_finalized_action(seg))
            elif seg.type == SegmentType.TOOL and seg.dirty:
                # 合并面板：本卡所有工具段共用一个面板，渲染区间从本卡首个工具段起，
                # 拆卡后不把旧卡的步骤重发到新卡（否则新卡立刻再撞元素上限，#41）。
                start = panel_steps_start(segments, session.split_index, seg.tool_offset)
                end = len(all_steps)
                rollover = await self._maybe_rollover_tool_segment(
                    session=session,
                    segment_state=segment_state,
                    index=i,
                    seg=seg,
                    all_steps=all_steps,
                    actions=actions,
                    new_el_ids=new_el_ids,
                    new_el_estimates=new_el_estimates,
                    updated_tool_segs=updated_tool_segs,
                    pending_delta=new_el_total,
                )
                if rollover == "failed":
                    return
                if rollover == "split":
                    actions = []
                    new_el_ids = set()
                    new_el_estimates = {}
                    updated_tool_segs = []
                    new_el_total = 0
                    continue
                # 合并面板模式下，工具面板是同一个元素，元素估算按增量累计，
                # 避免每次把「全部工具步骤」重复计入 element_count 导致虚高拆卡。
                estimate = estimate_tool_elements(start, end, all_steps)
                # 合并面板的 element_id 恒为建卡骨架预置的 TOOL_PANEL_ELEMENT_ID：
                # 所有 tool segment 共享同一个底部面板，元素 id 与 segment 的 el_id
                # 无关。本地补丁（2026-10-14）：原实现回落到 seg.el_id，会让批更新去
                # 改一个卡片上不存在的 tools_N 元素 → 300313「not find elementID」→
                # 回滚重建 → 下一轮再回落，形成反复失败/恢复的抖动。
                shared_el_id = tool_panel_element_id or TOOL_PANEL_ELEMENT_ID
                actions.append(
                    build_tool_update_action(element_id=shared_el_id, steps=all_steps[start:end])
                )
                updated_tool_segs.append(seg)
                # 工具面板元素增量 = 当前总估算 - 上次记录的总估算（非当前段差值）。
                # session.tool_panel_estimate 只在批更新成功后推进（见 _do_batch_update），
                # 这里提前赋值会把它算出的增量清零（#41）。
                if shared_el_id not in new_el_estimates:
                    delta = estimate - session.tool_panel_estimate
                    new_el_estimates[shared_el_id] = estimate
                    new_el_total += delta
                else:
                    # 同一 flush 内多个 dirty 工具段共享面板：仅首次累加，后续不重复
                    seg.created = True
                    seg.dirty = False

        if actions:
            payload_bytes += len(json.dumps(actions, ensure_ascii=False, default=str))
            if not await self._do_batch_update(
                session, segments, actions, new_el_ids, new_el_estimates, updated_tool_segs,
            ):
                return

        # ── 步骤 1.5: 通知槽位同步 — 流式期到达的提示（含后台回执）当场可见 ──
        # 提示只进 notices 不会自己上屏：建卡之后才到的回执必须补一次元素更新，
        # 否则要等封卡重建整张卡才出现（#issue 流式期看不到回执）。
        payload_bytes += await self._sync_notice_slot(session, segment_state)

        # ── 步骤 2: stream_element 刷脏文本 ──
        max_element_bytes = 0
        for seg in segments[session.split_index:]:
            if not seg.created or not seg.dirty:
                continue
            try:
                if seg.type == SegmentType.REASONING:
                    content = optimize_markdown_style(seg.text) or " "
                    # 尾部窗口钳制（#52）：stream_element 是覆盖语义全文重发，
                    # reasoning 无上限时每秒重写 20–28KB、单轮累计 1.3MB，
                    # 会把飞书客户端渲染打满。保留头部摘要 + 尾部最新结论。
                    content = clamp_utf8(
                        content, max_bytes=_REASONING_MAX_BYTES, preserve_tail=True,
                    )
                    content_bytes = len(content.encode("utf-8"))
                    payload_bytes += content_bytes
                    max_element_bytes = max(max_element_bytes, content_bytes)
                    session.sequence += 1
                    _logger.info(
                        "CardKit stream element: msg=%s seq=%d type=reasoning len=%d",
                        session.message_id[:12],
                        session.sequence,
                        len(content),
                    )
                    await self._client.cardkit_stream_element(
                        session.card_id,
                        seg.text_el_id,
                        content,
                        sequence=session.sequence,
                    )
                    seg.dirty = False
                elif seg.type == SegmentType.ANSWER:
                    content = seg.text
                    if session.image_resolver:
                        content = session.image_resolver.resolve_images(content)
                    content = prepare_streaming_markdown(content) or " "
                    content = clamp_utf8(content, preserve_tail=False)
                    content_bytes = len(content.encode("utf-8"))
                    payload_bytes += content_bytes
                    max_element_bytes = max(max_element_bytes, content_bytes)
                    session.sequence += 1
                    _logger.info(
                        "CardKit stream element: msg=%s seq=%d type=answer len=%d",
                        session.message_id[:12],
                        session.sequence,
                        len(content),
                    )
                    await self._client.cardkit_stream_element(
                        session.card_id,
                        seg.el_id,
                        content,
                        sequence=session.sequence,
                    )
                    seg.dirty = False
            except Exception as e:
                _logger.debug("CardKit stream element failed: %s el=%s", e, seg.el_id, exc_info=True)

        # 载荷自适应降频（#46/#52）：按本 flush 实际推送量 + 最大单元素全文
        # 调整下一轮节流窗口；元素级档位优先（客户端重绘量由最大单元素决定）。
        session.flush.adapt_payload_size(payload_bytes, max_element_bytes=max_element_bytes)

    async def _sync_notice_slot(self, session: CardSession, segment_state: SegmentState) -> int:
        """把最新一条提示画到卡片通知槽，返回本次推送的载荷字节数.

        两条路径（#issue 流式期看不到回执）：
        - 本卡有可复用的提示元素（建卡时带过提示、且没被正文接管）→ 原地改文案
        - 没有（本卡从未带提示，或 ``fry_notice`` 已被正文接管）→ 在 loading 之前
          补一个流式提示元素；封卡整卡重建时它随旧卡一起消失，不会残留

        失败只记日志：提示是附带信息，画不上去不能让整轮 flush 或主流程失败。
        """
        if not segment_state.needs_notice_slot_paint():
            return 0
        target = segment_state.notice_slot_target()
        if target is None or not session.card_id:
            return 0
        assert self._client is not None
        element_id = segment_state.notice_element_id
        added = element_id is None
        try:
            if added:
                element_id = segment_state.next_notice_slot_element_id()
                action: dict[str, Any] = {
                    "action": "add_elements",
                    "params": {
                        "type": "insert_before",
                        "target_element_id": LOADING_ELEMENT_ID,
                        "elements": [
                            build_streaming_notice_element(
                                target,
                                element_id=element_id,
                                text_size=self._cfg.body_text_size,
                            )
                        ],
                    },
                }
            else:
                action = {
                    "action": "partial_update_element",
                    "params": {
                        "element_id": element_id,
                        "partial_element": {"content": target},
                    },
                }
            session.sequence += 1
            await self._client.cardkit_batch_update(
                session.card_id, [action], sequence=session.sequence
            )
        except Exception as exc:
            _logger.warning(
                "notice slot paint failed: msg=%s el=%s added=%s err=%s",
                session.message_id[:12],
                element_id,
                added,
                exc,
            )
            if not added:
                # 元素可能已随拆卡/重建消失：清掉登记，下一轮 flush 改走新增路径，
                # 而不是对着一个不存在的 id 反复失败。
                segment_state.bind_notice_element(None, None)
            return 0
        segment_state.bind_notice_element(element_id, target)
        if added:
            session.element_count += 1
            session.notice_element_count += 1
        _logger.info(
            "notice slot painted: msg=%s el=%s added=%s len=%d",
            session.message_id[:12],
            element_id,
            added,
            len(target),
        )
        return len(target.encode("utf-8"))

    async def _do_batch_update(
        self,
        session: CardSession,
        segments: list[Segment],
        actions: list[dict[str, Any]],
        new_el_ids: set[str],
        new_el_estimates: dict[str, int],
        updated_tool_segs: list[Segment],
    ) -> bool:
        """执行 batch_update 并处理快照/标记。返回 False 表示失败."""
        assert self._client is not None
        assert session.card_id is not None
        session.sequence += 1
        _logger.info(
            "CardKit batch update: msg=%s card=%s seq=%d actions=%d split=%d elements=%d",
            session.message_id[:12],
            session.card_id[:12],
            session.sequence,
            len(actions),
            session.split_index,
            session.element_count,
        )
        pre_flush_reasoning_elapsed = {
            seg.el_id: seg.elapsed_ms for seg in segments if seg.type == SegmentType.REASONING
        }
        pre_flush_tool_offsets = {
            seg.el_id: seg.tool_end_offset for seg in updated_tool_segs
        }
        pre_flush_tool_steps = session.tool_use.build_display_steps()
        pre_flush_tool_slices = {
            seg.el_id: pre_flush_tool_steps[seg.tool_offset:tool_segment_end(seg, pre_flush_tool_steps)]
            for seg in updated_tool_segs
        }
        pre_flush_tool_panel_estimate = session.tool_panel_estimate
        try:
            await self._client.cardkit_batch_update(
                session.card_id,
                actions,
                sequence=session.sequence,
            )
            for seg in segments:
                if seg.el_id in new_el_ids:
                    seg.created = True
                    estimate = new_el_estimates.get(seg.el_id, 0)
                    seg.element_estimate = estimate
                    session.element_count += estimate
            for seg in segments:
                if seg.type == SegmentType.REASONING and pre_flush_reasoning_elapsed.get(seg.el_id, 0) > 0:
                    seg.reasoning_finalized = True
            if new_el_ids:
                for seg in segments:
                    if seg.el_id in new_el_ids or not seg.created:
                        continue
                    if seg.type in (SegmentType.REASONING, SegmentType.ANSWER) and seg.text:
                        seg.dirty = True
            current_tool_steps = session.tool_use.build_display_steps()
            # 合并面板：工具面板是共享元素，element_count 按面板总估算的增量统一累加一次，
            # 避免多个工具段各自差值导致重复计数（element_count 虚高 → 过度拆卡）。
            if TOOL_PANEL_ELEMENT_ID in new_el_estimates:
                new_panel_estimate = new_el_estimates[TOOL_PANEL_ELEMENT_ID]
                session.element_count += new_panel_estimate - pre_flush_tool_panel_estimate
                session.tool_panel_estimate = new_panel_estimate
            for seg in updated_tool_segs:
                offset_ok = pre_flush_tool_offsets.get(seg.el_id, -1) == seg.tool_end_offset
                current_tool_slice = current_tool_steps[
                    seg.tool_offset:tool_segment_end(seg, current_tool_steps)
                ]
                tool_slice_ok = pre_flush_tool_slices.get(seg.el_id) == current_tool_slice
                if seg.el_id in new_el_estimates and seg.el_id != TOOL_PANEL_ELEMENT_ID:
                    estimate = new_el_estimates[seg.el_id]
                    session.element_count += estimate - seg.element_estimate
                    seg.element_estimate = estimate
                if seg.created and offset_ok and tool_slice_ok:
                    seg.dirty = False
        except FeishuAPIError as e:
            missing_el_id = extract_missing_element_id(e)
            action_summary = summarize_actions(actions)
            _logger.warning(
                "CardKit batch update failed: %s card=%s seq=%d split=%d elements=%d "
                "missing=%s missing_state=%s new=[%s] new_est=[%s] tool_updates=[%s] %s",
                e,
                session.card_id[:12],
                session.sequence,
                session.split_index,
                session.element_count,
                missing_el_id or "-",
                segment_state_for_log(segments, missing_el_id),
                compact_ids(new_el_ids),
                # 估算量一并打出（#22）：判假 300305 低估来源需要 elements 与逐项估算对照
                compact_ids([f"{el_id}~{est}" for el_id, est in new_el_estimates.items()]),
                compact_ids([seg.el_id for seg in updated_tool_segs]),
                action_summary,
                exc_info=True,
            )
            # 缺失元素（300313）时回滚 stale segment：本地 created=True 但卡片上不存在，
            # 下一轮 flush 会用 add_elements 重建该元素，避免反复 partial_update 死循环。
            if missing_el_id:
                for seg in segments[session.split_index:]:
                    if seg.el_id == missing_el_id and seg.created:
                        seg.created = False
                        seg.dirty = True
                        # issue #3: 工具面板是【经建卡骨架预置】的共享元素
                        # （tool_panel_created=True）。若只回滚 segment，下一轮 TOOL
                        # 分支会因 tool_panel_created 已为 True 落入 else 分支——仅翻
                        # 标志位、不发任何 action，缺失面板永不重建。这里把骨架标志一并
                        # 重置，让下一轮走“首次创建”分支重新 update 面板元素。
                        if seg.type == SegmentType.TOOL and session.tool_panel_created:
                            session.tool_panel_created = False
                            _logger.info(
                                "CardKit recovered stale tool panel skeleton "
                                "(tool_panel_created -> False) -> will rebuild on next flush",
                            )
                        # 同步扣减元素计数：该 segment 当初 add 成功时已累加进 element_count，
                        # 回滚为未创建后下一轮会重新 add 并再次累加，这里先扣除避免重复计数。
                        session.element_count -= seg.element_estimate
                        if session.element_count < 0:
                            session.element_count = 0
                        _logger.info(
                            "CardKit recovered stale segment %s -> will re-add on next flush",
                            seg.el_id,
                        )
                        break

            # 元素已存在（300301 Duplicate ID，#51）：飞书偶发内部瞬态错误（如 300308）重试后，
            # 元素在云端实际已成功落地但客户端保持 created=False。下次 flush 重发 add_elements
            # 会报 Duplicate ID，导致整批更新被原子拒绝并陷入死锁假死。
            # 这里标记其已创建并补齐元素计数，让下轮 flush 转为 partial_update 或静默态恢复正常更新。
            duplicate_el_id = extract_duplicate_element_id(e)
            if duplicate_el_id:
                for seg in segments[session.split_index:]:
                    if seg.el_id == duplicate_el_id:
                        if not seg.created:
                            seg.created = True
                            estimate = new_el_estimates.get(seg.el_id, 0)
                            seg.element_estimate = estimate
                            session.element_count += estimate
                            if seg.type == SegmentType.TOOL:
                                session.tool_panel_created = True
                            _logger.info(
                                "CardKit recovered duplicate segment %s (marked created=True, est=%d)",
                                seg.el_id,
                                estimate,
                            )
                        break
                if duplicate_el_id == TOOL_PANEL_ELEMENT_ID and not session.tool_panel_created:
                    session.tool_panel_created = True
                    _logger.info("CardKit recovered duplicate tool panel skeleton")

            self._handle_flush_error(e)
            # 卡片元素总数超限（300305）：element_count 追踪失真导致卡片实际元素数
            # 超过飞书硬上限，当前卡已无法写入任何新元素。强制拆卡到新卡继续流式。
            if e.code == CARDKIT_ELEMENT_LIMIT_TOTAL and not session.split_disabled:
                try:
                    await self._force_split_on_element_limit(session)
                except Exception:
                    _logger.warning(
                        "CardKit force-split on element limit failed, msg=%s",
                        session.message_id[:12],
                        exc_info=True,
                    )
            return False
        return True

    async def _force_split_on_element_limit(self, session: CardSession) -> None:
        """卡片元素总数已达飞书硬上限（300305）时强制拆卡。

        当 element_count 追踪失真（低于卡片实际元素数）导致卡片真实元素数
        超过飞书硬上限时，流式卡片已无法写入任何新元素。此方法封印旧卡、
        创建新卡，并把尚未创建的 segment 迁移到新卡，让后续 flush 在新卡上
        继续流式输出。
        """
        assert self._client is not None
        if session.split_disabled or not session.has_card:
            return
        segment_state = session.segment_state
        if segment_state is None:
            return
        segments = segment_state.segments
        seal_start_idx = session.split_index
        seal_segments = [s for s in segments[seal_start_idx:] if s.created]

        new_card = await self._create_streaming_card(session)
        if new_card is None:
            session.split_disabled = True  # 降级：继续写当前卡
            _logger.warning(
                "CardKit force-split: create new card failed, disabling split, msg=%s",
                session.message_id[:12],
            )
            return

        old_card_id = session.card_id
        assert old_card_id is not None
        await self._seal_current_card(
            session, seal_segments, card_id=old_card_id,
        )

        new_card_id, new_msg_id = new_card
        session.set_card(card_id=new_card_id, card_msg_id=new_msg_id)
        self._track_live_card(session)  # #43：新卡改登记（旧卡封完已注销）
        session.element_count = 1  # loading element
        # 新卡同样带最新一条提示（_latest_notice），记账同步这一格
        session.notice_element_count = 1 if _latest_notice(session) else 0
        session.element_count += session.notice_element_count
        session.sequence = 1  # 新卡从 1 重新计数
        session.tool_panel_created = False
        session.tool_panel_estimate = 0
        session.split_disabled = False
        # 已创建的 segment 留在封印后的旧卡上；新卡只承接未创建的那些。
        # split_index 必须前移：完成态按 active_segments()（segments[split_index:]）重建，
        # 不前移会把旧卡内容再渲染一遍，元素数再次超限（#41）。
        split_at = next(
            (i for i in range(seal_start_idx, len(segments)) if not segments[i].created),
            len(segments),
        )
        session.split_index = split_at
        # 未创建 segment 迁移到新卡，标记 dirty 以便下一轮 flush 重新 add。
        for seg in segments[split_at:]:
            # 新卡已重新用 fry_notice 承载提示时，接管过正文的段落要换 id 才能重新上卡
            if session.notice_element_count and seg.el_id == NOTICE_ELEMENT_ID:
                segment_state.reassign_element_id(seg)
            seg.created = False
            seg.dirty = True
        _logger.info(
            "CardKit force-split on element limit: msg=%s old_card=%s sealed=%d split_idx=%d new_card=%s",
            session.message_id[:12],
            old_card_id[:12],
            len(seal_segments),
            split_at,
            new_card_id[:12],
        )

    async def _maybe_rollover_tool_segment(
        self,
        *,
        session: CardSession,
        segment_state: SegmentState,
        index: int,
        seg: Segment,
        all_steps: list[ToolDisplayStep],
        actions: list[dict[str, Any]],
        new_el_ids: set[str],
        new_el_estimates: dict[str, int],
        updated_tool_segs: list[Segment],
        pending_delta: int = 0,
    ) -> str | None:
        """按 tool step 边界拆分过大的 dirty tool segment."""
        start = seg.tool_offset
        end = tool_segment_end(seg, all_steps)
        # 工具面板是共享元素，增量必须以「本卡已入账的面板总量」为基准。
        # 工具段的 element_estimate 恒为 0（共享面板的估算只记在 session 上），
        # 拿它当基准会把整块面板重复计入，约每 17 步就触发一次拆卡（#41）。
        panel_start = panel_steps_start(segment_state.segments, session.split_index, start)
        estimate = estimate_tool_elements(panel_start, end, all_steps)
        delta = estimate - session.tool_panel_estimate
        if (
            delta <= 0
            or session.element_count + pending_delta + delta + FOOTER_RESERVE <= ELEMENT_THRESHOLD
            or session.split_disabled
        ):
            return None

        split_offset = find_tool_split_offset(
            base_count=session.element_count + pending_delta - session.tool_panel_estimate,
            seg=seg,
            all_steps=all_steps,
            panel_start=panel_start,
        )
        if split_offset is None:
            # 面板已装不下哪怕 1 个新 step：按 step 边界强制拆，本卡至少保留 1 个 step，
            # 否则拆出来的首段是空面板，拆卡也降不下来。
            nxt = start + 1
            tail_estimate = estimate_tool_elements(nxt, end, all_steps)
            if session.element_count + pending_delta - session.tool_panel_estimate + tail_estimate <= ELEMENT_THRESHOLD:
                return None
            split_offset = nxt

        old_estimate = estimate_tool_elements(panel_start, split_offset, all_steps)
        actions.append(
            build_tool_update_action(
                element_id=seg.el_id,
                steps=all_steps[panel_start:split_offset],
            )
        )
        updated_tool_segs.append(seg)
        new_el_estimates[seg.el_id] = old_estimate
        segment_state.split_tool_segment(index, split_offset)
        split_ok = await self._do_split_card(
            session, index + 1, actions, new_el_ids, new_el_estimates, updated_tool_segs,
        )
        if not split_ok:
            return "failed"
        return "split"

    async def _seal_current_card(
        self,
        session: CardSession,
        seal_segments: list[Segment],
        *,
        card_id: str | None = None,
        sequence: int | None = None,
    ) -> None:
        """封印指定卡（默认 session.card_id）：close_streaming + 全量重建。失败仅记录日志。

        card_id 用于 session 已切到新卡、仍需封印旧卡的场景（clarify 切卡）；
        sequence 传入旧卡续用的递增序列（CardKit 要求单调递增，不能用新卡的计数）。
        """
        assert self._client is not None
        old_card_id = card_id or session.card_id
        if not old_card_id:
            return
        if session.image_resolver:
            await _resolve_answer_images(
                seal_segments,
                session.image_resolver,
                log_prefix="CardKit seal",
            )
        all_steps = session.tool_use.build_display_steps()
        seal_card = build_complete_card(
            segments=seal_segments,
            all_tool_steps=all_steps,
            footer_data=session.footer,
            footer_fields=[],
            footer_show_label=False,
            footer_enabled=False,
            panel_fields=self._cfg.panel_fields,
            panel_expanded=self._cfg.panel_expanded,
            panel_text_size=self._cfg.panel_text_size,
            header_enabled=False,
            body_text_size=self._cfg.body_text_size,
            show_tool_use=self._cfg.show_tool_use,
            width_mode=self._cfg.width_mode,
        )
        try:
            seq = (session.sequence if sequence is None else sequence) + 1
            await self._client.cardkit_close_streaming(old_card_id, sequence=seq)
            seq += 1
            await self._client.cardkit_update(old_card_id, seal_card, sequence=seq)
        except Exception:
            _logger.warning(
                "CardKit seal failed for old card %s, trying loading-icon cleanup",
                old_card_id[:12],
                exc_info=True,
            )
            # 全量重建失败（典型: 旧卡元素已超限 300305）时，卡片内容停留在
            # 流式末态且残留 loading 转圈图标，用户侧表现为消息永远「处理中」。
            # 降级补救：删除 loading 图标元素，让旧卡至少停止转圈。
            await self._remove_loading_icon(old_card_id, seq + 1)

    async def _remove_loading_icon(self, card_id: str, sequence: int) -> None:
        """从卡片删除 loading 转圈图标（seal/complete 失败时的降级补救）。"""
        assert self._client is not None
        try:
            await self._client.cardkit_batch_update(
                card_id,
                [{"type": "delete", "element_id": LOADING_ELEMENT_ID}],
                sequence=sequence,
            )
        except Exception:
            _logger.warning(
                "CardKit remove loading icon failed for card %s",
                card_id[:12],
                exc_info=True,
            )

    async def _create_streaming_card(self, session: CardSession) -> tuple[str, str] | None:
        """创建空白流式卡并挂到 anchor，返回 (card_id, msg_id)。失败返回 None。

        不修改 session.card_id —— 调用方负责 set_card。
        """
        assert self._client is not None
        try:
            # 拆卡后重建需要 show_tool_use=True 以包含 tool_panel 元素，
            # 否则后续 flush 会因找不到 element_id 而报 300313。
            need_tool_panel = session.tool_panel_created or any(
                s.type == SegmentType.TOOL for s in (session.segment_state.segments[session.split_index:] if session.segment_state else [])
            )
            notice_text = _latest_notice(session)
            card = build_streaming_card_v2(
                show_tool_use=need_tool_panel,
                show_reasoning=False,
                show_streaming_element=False,
                header_enabled=self._cfg.header_enabled,
                text_size=self._cfg.body_text_size,
                width_mode=self._cfg.width_mode,
                notice=notice_text,
            )
            new_card_id = await self._client.cardkit_create(card)
            if session.synthetic:
                # 合成轮次无真实消息可回复，拆卡后新卡直接投递到聊天
                new_msg_id = await self._client.send_card_to_chat(
                    chat_id=session.chat_id,
                    card={"type": "card", "data": {"card_id": new_card_id}},
                )
            else:
                new_msg_id = await self._client.reply_card_by_id(
                    session.anchor_id or session.message_id, new_card_id,
                )
        except Exception:
            _logger.warning(
                "CardKit create streaming card failed for msg=%s",
                session.message_id[:12],
                exc_info=True,
            )
            return None
        # 新卡同样带着最新一条提示：把该元素登记给段状态，首段正文可原地接管（#issue）
        if session.segment_state is not None:
            session.segment_state.bind_notice_element(
                NOTICE_ELEMENT_ID if notice_text else None, notice_text
            )
        return new_card_id, new_msg_id

    async def _do_split_card(
        self,
        session: CardSession,
        split_idx: int,
        actions: list[dict[str, Any]],
        new_el_ids: set[str],
        new_el_estimates: dict[str, int],
        updated_tool_segs: list[Segment],
    ) -> bool:
        """拆卡：先 flush pending actions，封旧卡，创建新卡。返回 False 表示失败需中断 flush."""
        assert self._client is not None
        old_card_id = session.card_id
        assert old_card_id is not None
        segment_state = session.segment_state
        assert segment_state is not None
        segments = segment_state.segments
        seal_start_idx = session.split_index

        if actions and not await self._do_batch_update(
            session, segments, actions, new_el_ids, new_el_estimates, updated_tool_segs,
        ):
            return False

        seal_segments = [s for s in segments[seal_start_idx:split_idx] if s.created]

        # create → seal → set_card（顺序与原实现一致）
        new_card = await self._create_streaming_card(session)
        if new_card is None:
            session.split_disabled = True  # 降级：继续写当前卡
            return True

        await self._seal_current_card(session, seal_segments)

        new_card_id, new_msg_id = new_card
        session.set_card(card_id=new_card_id, card_msg_id=new_msg_id)
        self._track_live_card(session)  # #43：强制拆卡后改登记新卡
        session.element_count = 1  # loading element
        # 新卡同样带最新一条提示（_latest_notice），记账同步这一格
        session.notice_element_count = 1 if _latest_notice(session) else 0
        session.element_count += session.notice_element_count
        session.sequence = 1  # 新卡从 1 重新计数
        session.tool_panel_created = False  # 新卡需要重建 tool_panel
        session.tool_panel_estimate = 0  # 新卡工具面板从 0 重新估算
        session.split_disabled = False
        session.split_index = split_idx
        for seg in segments[split_idx:]:
            # 新卡已重新用 fry_notice 承载提示时，接管过正文的段落要换 id 才能重新上卡
            if session.notice_element_count and seg.el_id == NOTICE_ELEMENT_ID:
                segment_state.reassign_element_id(seg)
            seg.created = False
        _logger.info(
            "CardKit split: msg=%s old_card=%s sealed=%d split_idx=%d new_card=%s",
            session.message_id[:12],
            old_card_id[:12],
            len(seal_segments),
            split_idx,
            new_card_id[:12],
        )
        return True

    async def _do_clarify_split(self, session: CardSession) -> bool:
        """clarify 工具结束后切卡：建新卡 + 封旧卡。

        返回 False 表示未切卡（无卡可封，或建卡失败降级继续写旧卡）。
        """
        await self._wait_for_card_creation(session)
        if not session.has_card or session.state == SessionState.FAILED:
            _logger.info(
                "clarify_split: no card to seal, msg=%s state=%s",
                session.message_id[:12], session.state,
            )
            return False

        # 先禁拆卡再等 flush：否则进行中的 flush 可能先拆卡，随后被本流程封印成空白卡。
        session.split_disabled = True
        try:
            await session.flush.wait_for_flush()
            try:
                await session.flush.flush_now(lambda: self._do_flush(session))
            except Exception:
                _logger.debug("clarify_split: final flush failed", exc_info=True)

            # 先建新卡后封旧卡（与拆卡一致）：建卡失败时旧卡未 close，可降级继续流式。
            new_card = await self._create_streaming_card(session)
            if new_card is None:
                _logger.warning(
                    "clarify_split: create new card failed, continuing on current card, msg=%s",
                    session.message_id[:12],
                )
                return False

            # 先切到新卡再封旧卡：任意时刻被取消（超时兜底）时，session 指向的都是
            # 未 close 的卡，后续 flush 不会写到已关闭的旧卡。
            # seal 必须显式传旧 card_id + 旧 sequence（session 已切新卡，
            # 且 CardKit sequence 要求单调递增，不能用新卡的计数）。
            old_card_id = session.card_id
            old_seq = session.sequence
            new_card_id, new_msg_id = new_card
            session.set_card(card_id=new_card_id, card_msg_id=new_msg_id)
            session.sequence = 1  # 新卡从 1 重新计数

            await self._seal_current_card(
                session,
                session.active_segments(),
                card_id=old_card_id,
                sequence=old_seq,
            )

            # 重置内容，新卡承载 clarify 后的输出
            session.segment_state = SegmentState(max_reasoning_panels=self._cfg.max_reasoning_panels)
            session.split_index = 0
            session.element_count = 1  # loading element（与拆卡一致）
            # 新卡同样带着最新一条提示：记账与元素登记一起迁到新段状态（#issue 原地接管）
            clarify_notice = _latest_notice(session)
            session.notice_element_count = 1 if clarify_notice else 0
            session.element_count += session.notice_element_count
            session.segment_state.bind_notice_element(
                NOTICE_ELEMENT_ID if clarify_notice else None, clarify_notice
            )
            session.tool_use = ToolUseTracker()
            session.tool_panel_created = False
            session.tool_panel_estimate = 0

            _logger.info(
                "clarify_split: sealed old + new card msg=%s card=%s",
                session.message_id[:12], new_card_id[:12],
            )
            return True
        finally:
            session.split_disabled = False  # 取消/异常/失败均恢复拆卡能力

    def _handle_flush_error(self, e: FeishuAPIError) -> None:
        if e.code == CARDKIT_RATE_LIMITED:
            return
        if e.code == CARDKIT_STREAMING_CLOSED:
            return
        if e.code == CARDKIT_ELEMENT_LIMIT_TOTAL:
            _logger.warning("CardKit card total element limit exceeded (code=300305)")
            return
        if e.code == CARDKIT_CONTENT_FAILED:
            sub_code = e.extract_sub_code()
            if sub_code == CARDKIT_ELEMENT_LIMIT:
                _logger.warning("CardKit card element limit exceeded")

    async def _do_complete_card(self, session: CardSession) -> bool:
        """完成流式卡片：close streaming + 全量重建卡片（保持 segments 顺序）."""
        try:
            return await self._do_complete_card_inner(session)
        finally:
            self._flush_deferred_background_reviews(session)
            self._cleanup_session(session)

    def _complete_header_enabled(self, session: "CardSession", all_tool_steps: list) -> bool:
        """完成态卡片是否显示顶部 header. 快回复(耗时 < header_min_duration 且无工具调用)时隐藏."""
        if not self._cfg.header_enabled:
            return False
        thresh = self._cfg.header_min_duration
        if thresh > 0 and not all_tool_steps:
            duration = session.footer.get("duration") if isinstance(session.footer, dict) else None
            try:
                elapsed = float(duration or 0)
            except (TypeError, ValueError):
                elapsed = 0.0
            if elapsed < thresh:
                return False
        return True

    async def _do_complete_card_inner(self, session: CardSession) -> bool:
        if session.guard.should_skip("_do_complete_card"):
            return False

        await session.flush.wait_for_flush()
        session.flush.mark_completed()

        segment_state = session.segment_state
        is_error = session.state == SessionState.FAILED
        is_aborted = session.state == SessionState.ABORTED
        all_tool_steps = session.tool_use.build_display_steps()

        if segment_state is not None:
            segment_state.finalize_segments(len(all_tool_steps))

        active_segments = session.active_segments()

        if session.image_resolver:
            await _resolve_answer_images(
                active_segments,
                session.image_resolver,
                log_prefix="CardKit",
            )

        # 封卡前摘掉「已经画在卡片提示元素上」的进行中提示（#issue：正文末尾残留）。
        # 被正文原地接管的提示早已从通知槽摘掉；没被接管的（本轮无正文）在这里摘掉。
        if session.segment_state is not None:
            session.segment_state.drop_rendered_progress_notices()
        # 完成态通知区每条提示占一个元素：先记账再判断，避免低估撞 300305
        _sync_notice_elements(session)
        # 展示位拆分（#43 反馈）：后台回执等完成后通知在顶部，忙碌/重定向等
        # 进行中提示落底部（footer 之前）。记账仍按总条数。
        top_notices, progress_notices = _split_notice_slots(session)
        card = build_complete_card(
            segments=active_segments,
            all_tool_steps=all_tool_steps,
            footer_data=session.footer,
            is_error=is_error,
            is_aborted=is_aborted,
            footer_fields=self._cfg.footer_fields,
            footer_show_label=self._cfg.footer_show_label,
            footer_enabled=self._cfg.footer_enabled,
            footer_text_size=self._cfg.footer_text_size,
            panel_fields=self._cfg.panel_fields,
            panel_expanded=self._cfg.panel_expanded,
            panel_text_size=self._cfg.panel_text_size,
            header_enabled=self._complete_header_enabled(session, all_tool_steps),
            body_text_size=self._cfg.body_text_size,
            show_tool_use=self._cfg.show_tool_use,
            width_mode=self._cfg.width_mode,
            notices=top_notices,
            progress_notices=progress_notices,
        )

        streaming_closed = False
        panel_dropped = False
        # #22：重建卡片的真实元素数（含完成态长文切块二次膨胀）与流式期估算
        # tracked 对照打出，用于定位 300305 的低谷来自流式估算还是完成态重建。
        card_elements = len(card.get("body", {}).get("elements", []))
        # #56：飞书 300305 校验的是全树递归节点数，顶层 len 看不出来——统一面板里
        # 嵌的工具组/思考面板每个都带一串子元素。诊断日志同时打两个数。
        card_elements_recursive = count_elements_recursive(card)
        for attempt in range(3):
            try:
                assert self._client is not None
                if session.card_id:
                    if not streaming_closed:
                        session.sequence += 1
                        await self._client.cardkit_close_streaming(
                            session.card_id,
                            sequence=session.sequence,
                        )
                        streaming_closed = True
                    session.sequence += 1
                    await self._client.cardkit_update(
                        session.card_id,
                        card,
                        sequence=session.sequence,
                    )
                session.state = SessionState.COMPLETED
                return True
            except FeishuAPIError as e:
                _logger.warning(
                    "CardKit complete attempt %d failed: code=%s msg=%s card_id=%s seq=%d "
                    "card_elements=%d recursive=%d tracked=%d",
                    attempt,
                    e.code,
                    e,
                    session.card_id,
                    session.sequence,
                    card_elements,
                    card_elements_recursive,
                    session.element_count,
                    exc_info=True,
                )
                if session.guard.terminate("_do_complete_card", e):
                    return False
                # 元素总数超限（#53）：面板子元素被流式期已提交的一份重复计数。
                # 剥离统一面板（思考+工具过程）重发一次，正文与状态优先入卡；
                # 仍超限再走重试/降级。
                if e.code == CARDKIT_ELEMENT_LIMIT_TOTAL and not panel_dropped:
                    panel_dropped = True
                    if strip_unified_panel(card):
                        card_elements = len(card.get("body", {}).get("elements", []))
                        card_elements_recursive = count_elements_recursive(card)
                        _logger.warning(
                            "CardKit complete retry without unified panel: msg=%s "
                            "card_elements=%d recursive=%d",
                            session.message_id[:12],
                            card_elements,
                            card_elements_recursive,
                        )
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                continue
            except Exception as e:
                _logger.warning(
                    "CardKit complete attempt %d failed: %s: %s card_id=%s seq=%d "
                    "card_elements=%d recursive=%d tracked=%d",
                    attempt,
                    type(e).__name__,
                    e,
                    session.card_id,
                    session.sequence,
                    card_elements,
                    card_elements_recursive,
                    session.element_count,
                    exc_info=True,
                )
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                continue

        _logger.error(
            "card_complete_failed: msg=%s card=%s after 3 attempts seq=%d",
            session.message_id[:12],
            session.card_id,
            session.sequence,
        )
        # 3 次全量重建均失败（典型: 卡片元素超限 300305）：降级删除 loading
        # 转圈图标并关闭流式模式，避免卡片永远停留在「处理中」状态。
        if session.card_id and self._client is not None:
            try:
                session.sequence += 1
                await self._client.cardkit_close_streaming(
                    session.card_id, sequence=session.sequence,
                )
            except Exception:
                _logger.warning(
                    "CardKit fallback close_streaming failed for card %s",
                    session.card_id[:12],
                    exc_info=True,
                )
            await self._remove_loading_icon(session.card_id, session.sequence + 1)
            # #22：封卡失败后失控的流式卡会留在聊天里、正文又经纯文本重发一遍——
            # 用一张小降级卡整卡替换，提示用户内容在下方，避免两张内容重叠的实体。
            try:
                # 上面删 loading 的调用已占用 N+1，本地序号要跳到 N+2：
                # 复用 N+1 会被服务端判 300317 sequence compare failed，降级卡换不上（#53）。
                session.sequence += 2
                await self._client.cardkit_update(
                    session.card_id,
                    build_overflow_fallback_card(),
                    sequence=session.sequence,
                )
            except Exception:
                _logger.warning(
                    "CardKit overflow fallback replace failed for card %s",
                    session.card_id[:12],
                    exc_info=True,
                )
        session.mark_failed(reason="complete_failed_after_retries")
        return False

    async def _do_cron_deliver(
        self, chat_id: str, content: str, *, task_name: str = "", run_time: str = ""
    ) -> None:
        await self._ensure_init()
        assert self._client is not None
        card = build_cron_card(_clean_delivered_text(content), task_name=task_name, run_time=run_time)
        await self._client.send_card_to_chat(chat_id, card)

    async def _do_background_deliver(
        self,
        chat_id: str,
        preview: str,
        content: str,
        *,
        reply_to_message_id: str | None = None,
    ) -> None:
        await self._ensure_init()
        assert self._client is not None
        card = build_background_card(preview, _clean_delivered_text(content))
        await self._client.send_card_to_chat(
            chat_id,
            card,
            reply_to_message_id=reply_to_message_id,
        )
