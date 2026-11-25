"""完结卡 AI 摘要 — 卡片封口之后异步补一段摘要（不进流式主路径）.

设计前提（arch 实测，v0.7.2 期，只用 CardKit 卡实体、不投递到任何聊天）：

* 已 ``close_streaming`` + 全量重建封口的卡片，CardKit **仍接受**后续
  ``cardkit_batch_update``：``add_elements`` + ``insert_before``（目标 ``unified_panel``，
  即落在正文之后、面板之前）与 ``append`` 均成功；因此摘要只做「补一个元素」，
  **绝不整卡重发**——整卡重发会把面板子元素重复计数，正是 #53 撞 300305 的老路。
* ``sequence`` 必须严格递增：复用已用过的序号（含 0）一律 300317
  ``sequence number compare failed``；跳号（实测 +95）被接受 —— 重启补投靠跳号避让。
* 指向不存在的 element_id 会 300315 ``not find elementID``，所以
  ``insert_before unified_panel`` 失败（面板被 #53 剥离 / 本轮没有面板）时回落 ``append``。

失败纪律（这是全部设计的核心）：无凭据 / 超时 / 端点报错 / 落卡失败 → 只是「没有摘要」，
卡片停留在已封口的正常终态即正确兜底。**不碰** ``already_sent``、不碰文本回退、
不改卡片状态机、不重新登记 ``fry-cards-live.json``（那会让一张已完成的卡在下次重启
被扫成「已中断」）。

收尾纪律（对齐 claw 侧 journal：终态更新真正落卡才移除条目）：摘要元素先生成、
再写 ``<home>/state/fry-cards-ai-summary.json``，确认落卡才出列；进程重启或飞书瞬断后
由启动扫描补投（重试有上限，超限出列）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..cardkit.builder import AI_SUMMARY_ELEMENT_ID, UNIFIED_PANEL_ELEMENT_ID, build_ai_summary_element
from ..llm import LLMError, build_summary_messages, chat_completion, default_small_model, resolve_llm_endpoint

if TYPE_CHECKING:
    from ..config import Config
    from ..feishu import FeishuClient

_logger = logging.getLogger("hermes_fry_cards")

_PENDING_KEEP = 20  # 待补投条目上限（FIFO 截断，防长期离线堆积）
_MAX_ATTEMPTS = 3  # 单条摘要补投上限：卡片已删/权限变更这类错误重试也不会成
# 登记表读-改-写的互斥：摘要任务由 fire-and-forget 并发跑，多个会话可能同时写同一份
# 文件；口径同 controller 的 _live_cards_lock（RLock，绝不跨 await 持有）。
_registry_lock = threading.RLock()
# 重启补投的序号跳号幅度：卡片上次用过的序号未知（瞬断时可能已被服务端消费），
# 跳一段避让；实测卡 Kit 接受远大于当前值的序号（300317 只在「不比上次大」时出现）。
_RECOVERY_SEQ_JUMP = 1000
# wait_for 在 socket 超时之外再给的余量：覆盖线程池排队，避免「已发出的请求」
# 与「等待它的协程」生命周期差太远；真正的硬边界仍是 urllib 的 timeout。
_TIMEOUT_MARGIN_SEC = 2.0
# 摘要输入上限：与 llm 层同一口径（超长按尾部截断，避免把整轮长文塞进 prompt）
_MAX_SUMMARY_INPUT_CHARS = 20_000


# ---------------------------------------------------------------------------
# 待补投登记（与 fry-cards-live.json 完全分离：只补摘要，不参与重启封卡）
# ---------------------------------------------------------------------------

def pending_path(home: Path) -> Path:
    return home / "state" / "fry-cards-ai-summary.json"


def read_pending(home: Path) -> dict[str, Any]:
    try:
        data = json.loads(pending_path(home).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        _logger.debug("ai summary registry unreadable, treating as empty", exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def _write_pending(home: Path, data: dict[str, Any]) -> None:
    path = pending_path(home)
    if not data:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def add_pending(
    home: Path,
    card_id: str,
    element: dict[str, Any],
    *,
    sequence: int,
    before_element_id: str | None = None,
) -> None:
    """记下「摘要元素已生成、尚未确认落卡」（同 card_id 覆盖）."""
    if not card_id:
        return
    with _registry_lock:
        data = read_pending(home)
        data[card_id] = {
            "element": element,
            "sequence": sequence,
            "before": before_element_id or "",
            "at": time.time(),
            "attempts": 0,
        }
        if len(data) > _PENDING_KEEP:  # FIFO：最旧的先丢（丢了也只是那条不补）
            for key in sorted(data, key=lambda k: float(data[k].get("at") or 0))[: len(data) - _PENDING_KEEP]:
                data.pop(key, None)
        try:
            _write_pending(home, data)
        except OSError:
            _logger.warning("ai summary registry write failed", exc_info=True)


def drop_pending(home: Path, card_id: str) -> None:
    """摘要确认落卡（或彻底放弃）后出列."""
    if not card_id:
        return
    with _registry_lock:
        data = read_pending(home)
        if data.pop(card_id, None) is not None:
            with contextlib.suppress(OSError):
                _write_pending(home, data)


# ---------------------------------------------------------------------------
# 落卡
# ---------------------------------------------------------------------------

def insert_action(element: dict[str, Any], before_element_id: str | None) -> dict[str, Any]:
    """补一个元素到卡片的 action：命中目标则插在它之前，否则落到卡片末尾."""
    params: dict[str, Any] = {"elements": [element]}
    if before_element_id:
        params["type"] = "insert_before"
        params["target_element_id"] = before_element_id
    else:
        params["type"] = "append"
    return {"action": "add_elements", "params": params}


async def _try_apply(
    client: FeishuClient,
    card_id: str,
    element: dict[str, Any],
    *,
    sequence: int,
    before_element_id: str | None,
) -> bool:
    try:
        await client.cardkit_batch_update(
            card_id, [insert_action(element, before_element_id)], sequence=sequence
        )
        return True
    except Exception as e:
        _logger.warning(
            "ai summary update rejected: card=%s seq=%d target=%s err=%s",
            card_id[:12],
            sequence,
            before_element_id or "(append)",
            e,
        )
        return False


def has_unified_panel(card: dict[str, Any]) -> bool:
    """本轮终态卡上是否有统一面板（决定摘要插在面板之前还是落在末尾）.

    #53 的应急重发会剥掉面板，那类卡上 ``insert_before unified_panel`` 必然
    300315；本地判定省掉一次注定失败的请求。
    """
    if not isinstance(card, dict):
        return False
    body = card.get("body")
    elements = body.get("elements") if isinstance(body, dict) else None
    if not isinstance(elements, list):
        return False
    return any(
        isinstance(el, dict) and el.get("element_id") == UNIFIED_PANEL_ELEMENT_ID for el in elements
    )


async def apply_summary_element(
    client: FeishuClient,
    card_id: str,
    element: dict[str, Any],
    *,
    sequence: int,
    before_element_id: str | None,
) -> bool:
    """把摘要元素补进已封口的卡片；成功 True，失败 False（只记日志，不抛）.

    ``before_element_id`` 命中时落在正文之后、统一面板之前（观感最好）；该路径失败
    （面板不存在 / 被 #53 剥离 → 300315）回落卡片末尾，并顺延一位序号——序号复用会
    撞 300317，只有递增才解。
    """
    if before_element_id and await _try_apply(
        client, card_id, element, sequence=sequence, before_element_id=before_element_id
    ):
        return True
    start = sequence if not before_element_id else sequence + 1
    return await _try_apply(client, card_id, element, sequence=start, before_element_id=None)


# ---------------------------------------------------------------------------
# 触发判定与生成
# ---------------------------------------------------------------------------

def answer_text_of(segments: Any) -> str:
    """本轮最终答案文本（各 answer 段按顺序拼接）——摘要的唯一素材."""
    parts: list[str] = []
    for seg in segments or []:
        text = getattr(seg, "text", "") or ""
        seg_type = getattr(getattr(seg, "type", None), "value", getattr(seg, "type", ""))
        if seg_type == "answer" and text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def should_summarize(cfg: Config, answer_text: str) -> bool:
    """开关 + 阈值判定：默认关，短回复不打扰（一个 token 都不花）."""
    if not cfg.ai_summary_enabled or not answer_text:
        return False
    return len(answer_text) >= cfg.ai_summary_min_chars


def summary_max_tokens(cfg: Config) -> int:
    """输出 token 硬上限：按目标字数折算（CJK 1 字 ≤2 token）再留余量，防模型不听长度指令."""
    return cfg.ai_summary_max_output_chars * 2 + 64


async def generate_summary(cfg: Config, answer_text: str) -> str:
    """调小模型生成摘要；任何失败抛 ``LLMError``（调用方降级为「不摘要」）.

    阻塞的 urllib 调用放进线程池，事件循环不受影响；外层 ``wait_for`` 是硬超时兜底
    （socket 超时本身已是硬边界，多给的 5s 只为覆盖线程池排队）。
    """
    endpoint = resolve_llm_endpoint(cfg)
    if endpoint is None:
        raise LLMError("config.yaml 里没有可用的模型端点（model.base_url + api_key 或 custom_providers）")
    model = cfg.ai_summary_model or default_small_model(cfg, endpoint)
    if not model:
        raise LLMError("没有可用的小模型：请设 streaming.ai_summary.model")
    messages = build_summary_messages(
        answer_text[:_MAX_SUMMARY_INPUT_CHARS],
        max_input_chars=_MAX_SUMMARY_INPUT_CHARS,
        max_output_chars=cfg.ai_summary_max_output_chars,
    )
    timeout = cfg.ai_summary_timeout_sec
    result = await asyncio.wait_for(
        asyncio.to_thread(
            chat_completion,
            endpoint,
            model,
            messages,
            timeout_sec=timeout,
            max_tokens=summary_max_tokens(cfg),
            temperature=0.2,
        ),
        timeout=timeout + _TIMEOUT_MARGIN_SEC,
    )
    text = str(result.get("text") or "").strip()
    if not text:
        raise LLMError("摘要为空")
    usage = result.get("usage") or {}
    _logger.info(
        "ai_summary generated: model=%s elapsed=%dms tokens=%s→%s chars=%d",
        result.get("model") or model,
        int(result.get("elapsed_ms") or 0),
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        len(text),
    )
    return text


async def run_completion_summary(
    client: FeishuClient,
    cfg: Config,
    home: Path,
    card_id: str,
    *,
    sequence: int,
    answer_text: str,
    has_unified_panel: bool = False,
) -> bool:
    """生成摘要并补进已封口的卡片：成功 True；一切失败 False（不抛、不影响卡片）."""
    if not card_id:
        return False
    try:
        text = await generate_summary(cfg, answer_text)
    except (TimeoutError, LLMError) as e:
        _logger.warning("ai summary skipped (card=%s): %s", card_id[:12], e)
        return False
    except Exception as e:  # 兜住任何意外：摘要绝不能把收尾链路带崩
        _logger.warning("ai summary skipped (card=%s): %s: %s", card_id[:12], type(e).__name__, e)
        return False

    element = build_ai_summary_element(text)
    before = UNIFIED_PANEL_ELEMENT_ID if has_unified_panel else None
    add_pending(home, card_id, element, sequence=sequence, before_element_id=before)
    if await apply_summary_element(
        client, card_id, element, sequence=sequence, before_element_id=before
    ):
        drop_pending(home, card_id)
        _logger.info("ai summary appended: card=%s seq=%d chars=%d", card_id[:12], sequence, len(text))
        return True
    return False


async def recover_pending_summaries(client: FeishuClient, home: Path) -> int:
    """启动补投：把上次进程没来得及落卡的摘要补上（跳号避让序号，失败计数有上限）.

    与 ``_sweep_stale_cards``（网关重启把遗留卡封成「已中断」）互不相干：这里只给
    已封口的卡补一行摘要，绝不改写卡片状态。
    """
    data = read_pending(home)
    if not data:
        return 0
    recovered = 0
    for card_id, record in list(data.items()):
        element = record.get("element") if isinstance(record, dict) else None
        if not isinstance(element, dict) or element.get("element_id") != AI_SUMMARY_ELEMENT_ID:
            drop_pending(home, card_id)
            continue
        try:
            base_seq = int(record.get("sequence") or 0)
        except (TypeError, ValueError):
            base_seq = 0
        if base_seq <= 0:
            drop_pending(home, card_id)
            continue
        sequence = base_seq + _RECOVERY_SEQ_JUMP
        before = str(record.get("before") or "") or None
        ok = await apply_summary_element(
            client, card_id, element, sequence=sequence, before_element_id=before
        )
        if ok:
            drop_pending(home, card_id)
            recovered += 1
            _logger.info("ai summary recovery: card=%s seq=%d", card_id[:12], sequence)
            continue
        attempts = int(record.get("attempts") or 0) + 1
        if attempts >= _MAX_ATTEMPTS:
            _logger.warning("ai summary recovery dropped after %d attempts: card=%s", attempts, card_id[:12])
            drop_pending(home, card_id)
        else:
            # 读-改-写整段进锁（不含 await）：与并发的 add/drop 交错时会丢写或复活已出列的条目
            with _registry_lock:
                fresh = read_pending(home)
                current = fresh.get(card_id)
                if isinstance(current, dict):
                    fresh[card_id] = {**current, "attempts": attempts}
                    with contextlib.suppress(OSError):
                        _write_pending(home, fresh)
    return recovered
