"""StreamCardController — 流式卡片主控制器（单例）."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Coroutine, Iterator
from concurrent.futures import Future as ConcurrentFuture
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from .config import Config, hermes_home
from .feishu import (
    FeishuClient,
    FeishuClientConfig,
)
from .streaming.controller import StreamingController
from .streaming.session import CardSession, SessionState
from .streaming.text import strip_reasoning_tags
from .streaming.unavailable_guard import extract_api_code, is_terminal_api_code

_logger = logging.getLogger("hermes_fry_cards")
_CARD_CREATION_WAIT_SEC = 10.0
# 活跃卡登记（#43 网关重启封卡）：进程重启后把遗留卡封成「已中断」。
# 单条登记连续 N 个进程都封不掉就出列，避免无效消息 id 反复请求飞书。
_STALE_SWEEP_MAX_ATTEMPTS = 2
_LIVE_CARD_MAX_CLOSE_ATTEMPTS = 3
# 后台完成回执里命令的显示上限：超长命令行截断，卡片上只留一眼能认出的那一段。
_BACKGROUND_COMMAND_MAX_CHARS = 40
# 回执没有可用字段时的通用完成句：委派类完成事件（#54）四个字段全空，
# 兜底拿到的首行是 ``[ASYNC DELEGATION BATCH COMPLETE — …]`` 这类网关内部
# 标记，不能上屏，只能给一句通用提示。
_BACKGROUND_FALLBACK_TEXT = "✅ 后台任务已完成"
# 网关内部标记行的形态：方括号开头且首个标记词全大写（``[ASYNC …]``、
# ``[BG …]``；``[IMPORTANT: …]`` 在兜底前已被拆掉）。时间戳（``[2026-10-30 …]``）
# 以数字开头，不会误伤。
_INTERNAL_MARKER_RE = re.compile(r"^\[\s*[A-Z][A-Z0-9_]*[\] :,]")


def _unwrap_important_text(text: str) -> str:
    """去掉 ``[IMPORTANT: ...]`` 包裹后取首行（回执没有可用字段时的兜底文案）."""
    body = (text or "").strip()
    if body.startswith("[IMPORTANT:"):
        body = body[len("[IMPORTANT:"):].lstrip()
        if body.endswith("]"):
            body = body[:-1].rstrip()
    first = body.splitlines()[0].strip() if body.strip() else ""
    return first or body


def _fallback_receipt_text(text: str) -> str:
    """``command`` 缺失时的兜底文案；首行是网关内部标记就给通用完成句."""
    line = _unwrap_important_text(text)
    if not line:
        return _BACKGROUND_FALLBACK_TEXT
    if _INTERNAL_MARKER_RE.match(line) or "BATCH COMPLETE" in line:
        return _BACKGROUND_FALLBACK_TEXT
    return line


def background_completion_text(
    text: str,
    *,
    command: str | None = None,
    started_at: float | str | None = None,
    exit_code: int | str | None = None,
    now: float | None = None,
) -> str:
    """后台完成回执的用户文案（注入块的 ``text`` 是给模型看的内部文案）.

    ``text`` 形如 ``[IMPORTANT: Background process proc_x completed normally
    (exit code 0).\\nCommand: ...\\nOutput: ...]``，那是模型侧的合成消息，直接甩给
    用户既长又看不懂。有 ``command`` 时自己拼一句：``✅ 后台任务已完成 — `命令`
    (12s)``（命令超 40 字截断加省略号，时长 = now - started_at 取整秒）；字段缺失
    时回退到去包裹后的首行，但首行若是 ``[ASYNC DELEGATION BATCH COMPLETE …]``
    这类内部标记（#54）就换成通用完成句。``exit_code`` 只进日志，回执文案统一
    一句完成提示。
    """
    cmd = " ".join(str(command or "").split())
    if len(cmd) > _BACKGROUND_COMMAND_MAX_CHARS:
        cmd = cmd[:_BACKGROUND_COMMAND_MAX_CHARS] + "…"
    if not cmd:
        return _fallback_receipt_text(text)

    elapsed_s: int | None = None
    if started_at is not None:
        try:
            elapsed = float(now if now is not None else time.time()) - float(started_at)
        except (TypeError, ValueError):
            elapsed = -1.0
        if elapsed >= 0:
            elapsed_s = round(elapsed)

    line = f"✅ 后台任务已完成 — `{cmd}`"
    if elapsed_s is not None:
        line += f" ({elapsed_s}s)"
    _logger.debug(
        "background_completion_text: cmd=%r exit=%s elapsed=%s", cmd, exit_code, elapsed_s
    )
    return line


# ── 后台回执「已并入卡片」登记（#issue turn_busy 重复纯文本） ────────────────
# 回执并进卡片后，网关 _run_process_watcher 的 turn_busy 分支还会补发一条简洁纯文本，
# 用户看到卡外多一条。这里登记「这条完成回执已经由卡片接管」，注入点据此跳过那条纯文本。
# 键取进程会话 id（最可靠）、会话键与启动时刻三者之一；查不到就返回 False，照常发。
_BUSY_RECEIPT_SUPPRESS_TTL = 600.0  # 10 分钟窗口：完成回执的重复投递都发生在这段时间内
_merged_busy_receipts: dict[str, float] = {}  # 键 → 登记时刻（monotonic）
_merged_busy_receipts_lock = threading.Lock()


def _busy_receipt_keys(
    *,
    session_id: str | None = None,
    session_key: str | None = None,
    started_at: float | str | None = None,
) -> list[str]:
    """回执登记键：进程会话 id / 会话键 / 启动时刻，能拿到哪个用哪个."""
    keys: list[str] = []
    sid = str(session_id or "").strip()
    if sid:
        keys.append(f"sid:{sid}")
    skey = str(session_key or "").strip()
    if skey:
        keys.append(f"skey:{skey}")
    if started_at is not None:
        with suppress(TypeError, ValueError):
            keys.append(f"start:{float(started_at):.3f}")
    return keys


def remember_merged_busy_receipt(
    *,
    session_id: str | None = None,
    session_key: str | None = None,
    started_at: float | str | None = None,
) -> None:
    """登记「这条后台完成回执已经由卡片投递」，供注入点跳过网关的纯文本补发."""
    keys = _busy_receipt_keys(
        session_id=session_id, session_key=session_key, started_at=started_at
    )
    if not keys:
        return
    now = time.monotonic()
    with _merged_busy_receipts_lock:
        for key, stamp in list(_merged_busy_receipts.items()):
            if now - stamp > _BUSY_RECEIPT_SUPPRESS_TTL:
                _merged_busy_receipts.pop(key, None)
        for key in keys:
            _merged_busy_receipts[key] = now


def busy_receipt_suppressed(
    *,
    session_id: str | None = None,
    session_key: str | None = None,
    started_at: float | str | None = None,
    now: float | None = None,
) -> bool:
    """该后台完成回执是否已由卡片投递（True = 注入点跳过网关的纯文本补发）.

    拿不到任何可匹配的键、或登记已过期，一律返回 False：网关照常发纯文本，
    功能无损。
    """
    keys = _busy_receipt_keys(
        session_id=session_id, session_key=session_key, started_at=started_at
    )
    if not keys:
        return False
    current = time.monotonic() if now is None else now
    with _merged_busy_receipts_lock:
        for key in keys:
            stamp = _merged_busy_receipts.get(key)
            if stamp is not None and current - stamp <= _BUSY_RECEIPT_SUPPRESS_TTL:
                return True
    return False


def clear_merged_busy_receipts() -> None:
    """清空登记表（测试与配置重载用）."""
    with _merged_busy_receipts_lock:
        _merged_busy_receipts.clear()


def _interrupted_card() -> dict[str, Any]:
    """网关重启后给遗留卡用的静态「已中断」卡片（文案由插件提供，中英双语）."""
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "locales": ["zh_cn", "en_us"]},
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": _NOTICE_RESTART_EN,
                    "i18n_content": {"zh_cn": _NOTICE_RESTART_ZH, "en_us": _NOTICE_RESTART_EN},
                }
            ]
        },
    }


_NOTICE_RESTART_EN = "⚠️ The gateway restarted during this turn; this card stopped updating."
_NOTICE_RESTART_ZH = "⚠️ 网关在本轮处理中重启，卡片已停止更新。"


def _is_synthetic_turn_id(message_id: str) -> bool:
    """是否为网关合成轮次的消息 id（#43 反馈）.

    后台完成回执等合成事件用序列化序列当 id（``["completion", <task>, <ts>]``），
    不是飞书 open_message_id：拿它 reply 必然 99992354 target_message_gone。
    这类轮次的卡片改走 send_card_to_chat。
    """
    if not isinstance(message_id, str):
        return True
    cleaned = message_id.strip()
    return cleaned.startswith('["') or cleaned.startswith("['")


class StreamCardController(StreamingController):
    """流式卡片控制器 — 管理多条消息的卡片生命周期."""

    def __init__(self, profile_home: Path | None = None) -> None:
        self._profile_home = (profile_home or hermes_home()).resolve()
        self._cfg = Config(self._profile_home)
        self._client: FeishuClient | None = None
        self._sessions: dict[str, CardSession] = {}
        self._session_keys: dict[str, CardSession] = {}
        self._interrupt_map: dict[str, str] = {}
        # 丢弃的旧回合尾部：防止旧回合迟到的完成事件落到新卡上（#65）
        self._interrupted_tails: dict[str, float] = {}
        # 同一次打断的重复注入去重：(old_message_id, new_message_id) → monotonic 时刻。
        # REDIRECT 与 REDIRECT_RESOLVED 两个注入点对同一事件各调一次，间隔只有几毫秒；
        # 两次调用的 old_message_id 可能都指向刚建好的新会话（同 id），幂等判据分不出来。
        self._interrupt_dedup: dict[str, float] = {}
        # #43：重定向提示早于新卡登记时按 session_key 暂存，注册时补写
        self._pending_notices: dict[str, list[str]] = {}
        self._initialized = False
        self._init_lock = threading.Lock()
        self._session_ttl = self._cfg.card_duration_sec
        self._loop: asyncio.AbstractEventLoop | None = None
        self._text_fallback_needed: set[str] = set()
        self._text_fallback_aliases: dict[str, set[str]] = {}
        self._unscoped_enabled: bool | None = None
        # #43 网关重启封卡：活跃卡登记 + 启动清扫
        self._live_cards_lock = threading.RLock()
        self._swept_stale_cards = False
        self._stale_sweep_attempts = 0
        self._stale_sweep_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        unscoped = self._needs_fallback_scope()
        if unscoped and self._unscoped_enabled is not None:
            return self._unscoped_enabled
        with self._credential_scope():
            enabled = self._cfg.enabled and bool(self._cfg.feishu_app_id or self._cfg.env_app_id)
        if unscoped and enabled:
            self._unscoped_enabled = True
        return enabled

    @staticmethod
    def _needs_fallback_scope() -> bool:
        try:
            from agent.secret_scope import current_secret_scope, is_multiplex_active  # type: ignore[import-not-found]
        except ImportError:
            return False
        return is_multiplex_active() and current_secret_scope() is None

    @contextmanager
    def _credential_scope(self) -> Iterator[None]:
        try:
            from agent.secret_scope import (  # type: ignore[import-not-found]
                build_profile_secret_scope,
                current_secret_scope,
                is_multiplex_active,
                reset_secret_scope,
                set_secret_scope,
            )
        except ImportError:
            yield
            return
        if not is_multiplex_active() or current_secret_scope() is not None:
            yield
            return
        token = set_secret_scope(build_profile_secret_scope(self._profile_home))
        try:
            yield
        finally:
            reset_secret_scope(token)

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            with self._credential_scope():
                app_id = self._cfg.feishu_app_id or self._cfg.env_app_id
                app_secret = self._cfg.feishu_app_secret or self._cfg.env_app_secret
                if not app_id or not app_secret:
                    raise RuntimeError("feishu credentials not configured")
                self._client = FeishuClient(
                    FeishuClientConfig(
                        app_id=app_id,
                        app_secret=app_secret,
                        base_url=self._cfg.feishu_base_url,
                    )
                )
            self._initialized = True
        self._maybe_sweep_stale_cards()

    # ── 网关重启封卡（#43） ─────────────────────────────────────────────
    # 跨进程登记放在 profile 的 state 下：进程启动时读一次，凡是登记过、但本进程
    # 没有对应会话的卡，都说明上一轮进程没来得及收尾（重启/崩溃），统一封成
    # 「已中断」，否则用户会一直看到一张转圈的卡。

    def _live_cards_path(self) -> Path:
        return self._profile_home / "state" / "fry-cards-live.json"

    def _read_live_cards(self) -> dict[str, Any]:
        try:
            data = json.loads(self._live_cards_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            _logger.warning("live card registry unreadable, treating as empty", exc_info=True)
            return {}
        return data if isinstance(data, dict) else {}

    def _write_live_cards(self, data: dict[str, Any]) -> None:
        path = self._live_cards_path()
        if not data:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _track_live_card(self, session: CardSession) -> None:
        """登记活跃卡（幂等）：card_msg_id 为主键，附带 card_id 与 chat_id."""
        if not session.card_msg_id:
            return
        try:
            with self._live_cards_lock:
                data = self._read_live_cards()
                data[session.card_msg_id] = {
                    "card_id": session.card_id or "",
                    "chat_id": session.chat_id,
                    "at": time.time(),
                }
                self._write_live_cards(data)
        except OSError:
            _logger.warning("live card registry update failed", exc_info=True)

    def _untrack_live_card(self, session: CardSession) -> None:
        """注销（幂等）：卡已正常收尾，下次启动不该再封它."""
        if not session.card_msg_id:
            return
        try:
            with self._live_cards_lock:
                data = self._read_live_cards()
                if data.pop(session.card_msg_id, None) is not None:
                    self._write_live_cards(data)
        except OSError:
            _logger.warning("live card registry close failed", exc_info=True)

    def _maybe_sweep_stale_cards(self) -> None:
        """首次初始化后触发一次启动清扫（每进程最多尝试 N 次）.

        非阻塞：拿不到锁说明别的线程已在清扫，直接返回。
        """
        if self._swept_stale_cards or self._stale_sweep_attempts >= _STALE_SWEEP_MAX_ATTEMPTS:
            return
        if not self._stale_sweep_lock.acquire(blocking=False):
            return
        try:
            if self._swept_stale_cards:
                return
            self._stale_sweep_attempts += 1
            loop = self._get_loop()
            if loop is None or loop.is_closed():
                # 没有可用循环（例如同步上下文先触发初始化）：不记为已清扫，
                # 下次有循环时再试，最多 _STALE_SWEEP_MAX_ATTEMPTS 次。
                return
            self._swept_stale_cards = True
            self._fire_and_forget(self._sweep_stale_cards(), loop)
        except Exception:
            _logger.warning("stale card sweep scheduling failed", exc_info=True)
        finally:
            self._stale_sweep_lock.release()

    async def _sweep_stale_cards(self) -> None:
        """把上次进程遗留的活跃卡封成「已中断」."""
        try:
            with self._live_cards_lock:
                snapshot = self._read_live_cards()
        except OSError:
            _logger.warning("live card registry read failed", exc_info=True)
            return
        if not snapshot:
            return
        live_message_ids = {s.card_msg_id for s in self._sessions.values() if s.card_msg_id}
        recovered = 0
        for message_id, record in list(snapshot.items()):
            if message_id in live_message_ids:
                continue
            card_id = record.get("card_id") if isinstance(record, dict) else ""
            if not card_id:
                self._drop_live_card(message_id, record)
                continue
            try:
                assert self._client is not None
                await self._client.cardkit_update(card_id, _interrupted_card(), sequence=1)
            except Exception:
                attempts = int(record.get("attempts", 0)) + 1 if isinstance(record, dict) else 1
                _logger.warning(
                    "stale card close failed (attempt %d/%d): id=%s",
                    attempts,
                    _LIVE_CARD_MAX_CLOSE_ATTEMPTS,
                    message_id,
                )
                _logger.debug("stale card close error", exc_info=True)
                if attempts >= _LIVE_CARD_MAX_CLOSE_ATTEMPTS or not isinstance(record, dict):
                    self._drop_live_card(message_id, record)
                else:
                    self._update_live_card(message_id, record, {**record, "attempts": attempts})
                continue
            self._drop_live_card(message_id, record)
            recovered += 1
            _logger.info("delivery_closed: id=%s reason=gateway_restart", message_id)
        if recovered:
            _logger.info("gateway restart: closed %d stale card(s)", recovered)

    def _drop_live_card(self, message_id: str, record: Any) -> None:
        with self._live_cards_lock:
            data = self._read_live_cards()
            if data.get(message_id) is record or data.get(message_id) == record:
                data.pop(message_id, None)
                self._write_live_cards(data)

    def _update_live_card(self, message_id: str, record: Any, new_record: dict[str, Any]) -> None:
        with self._live_cards_lock:
            data = self._read_live_cards()
            if data.get(message_id) == record:
                data[message_id] = new_record
                self._write_live_cards(data)

    def _get_loop(self) -> asyncio.AbstractEventLoop | None:
        """获取事件循环，缓存以便跨线程复用."""
        try:
            loop = asyncio.get_running_loop()
            self._loop = loop
            return loop
        except RuntimeError:
            pass
        if self._loop is not None and not self._loop.is_closed():
            return self._loop
        return None

    def _get_active_session(self, message_id: str) -> CardSession | None:
        """获取非终态的活跃 session，不存在或已终态返回 None."""
        session = self._sessions.get(message_id)
        if session is None or session.state.is_terminal:
            return None
        return session

    def _resolve_session(
        self,
        message_id: str | None,
        session_key: str | None = None,
    ) -> CardSession | None:
        """按 message_id 解析会话；无 ID（合成轮次）时按 session_key 兜底."""
        if message_id:
            session = self._sessions.get(message_id)
            if session is not None and not session.state.is_terminal:
                return session
        if session_key:
            session = self._session_keys.get(session_key)
            if session is not None and not session.state.is_terminal:
                return session
        return None

    def resolve_session_id(
        self,
        message_id: str | None,
        session_key: str | None = None,
    ) -> str | None:
        """已存在会话的 message_id（按 message_id，其次 session_key 兜底）；无则 None.

        排队续轮的预建卡与收尾卡靠它对齐同一张卡：预建时按 session_key 注册，
        收尾时无消息 id 也据此复用，而不是另开一张（#37 重复投递修法）。
        """
        session = self._resolve_session(message_id, session_key)
        return session.message_id if session is not None else None

    def _notice_session(
        self,
        message_id: str | None,
        session_key: str | None = None,
    ) -> CardSession | None:
        """状态提示（忙碌回执 / 重定向 / 后台回执）的卡片归属.

        #43：Hermes 自己在回合进行中合成这些文案。提示属于「当前正在跑的那张卡」：
        - 有 message_id 且能解析到会话 → 就是它；
        - 只有 session_key → 按会话键取当前活跃卡；
        - 什么都没有 → 取唯一活跃卡（单用户 DM 的常见形态）。
        多张活跃卡且无标识时返回 None（宁可走 Hermes 原发送，也不误写别人的卡）。
        """
        session = self._resolve_session(message_id, session_key)
        if session is not None:
            return session
        if message_id or session_key:
            return None
        live = {id(s): s for s in list(self._sessions.values()) if not s.state.is_terminal}
        if len(live) == 1:
            return next(iter(live.values()))
        return None

    def on_busy_ack(
        self,
        *,
        text: str,
        message_id: str | None = None,
        session_key: str | None = None,
    ) -> bool:
        """忙碌回执 → 写进当前卡的通知槽并吞掉纯文本（#43）.

        返回 True 表示提示已合并进卡片，Hermes 不应再发纯文本。
        文案就是 Hermes 传进来的原文，插件不另写。
        """
        if not self.enabled or not text.strip():
            return False
        session = self._notice_session(message_id, session_key)
        if session is None or session.segment_state is None:
            return False
        session.segment_state.add_notice(text, kind="busy")
        self._schedule_flush(session)
        _logger.info(
            "busy_receipt_merged: incoming=%s owner=%s key=%s",
            message_id or "",
            session.message_id,
            session_key or "",
        )
        return True

    def on_redirected_notice(
        self,
        *,
        text: str,
        message_id: str | None = None,
        session_key: str | None = None,
    ) -> bool:
        """重定向提示 → 写进 redirect 后的新卡（#43）.

        ``on_interrupted`` 已把新消息建卡并按 ``_interrupt_map`` 接续完成；
        这里只负责把 Hermes 的重定向原文落到新卡通知槽，旧卡已收尾。
        新卡尚未登记时按 ``session_key`` 暂存，由 ``_register_session`` 补写。
        """
        if not self.enabled or not text.strip():
            return False
        session = self._notice_session(message_id, session_key)
        if session is None or session.segment_state is None:
            if session_key:
                self._pending_notices.setdefault(session_key, []).append(text.strip())
                _logger.info("redirect_notice_pending: key=%s", session_key)
                return True
            return False
        session.segment_state.add_notice(text, kind="redirect")
        self._schedule_flush(session)
        _logger.info(
            "redirect_notice_merged: incoming=%s owner=%s key=%s",
            message_id or "",
            session.message_id,
            session_key or "",
        )
        return True

    async def on_session_compressed(
        self,
        *,
        session_key: str | None = None,
        message_id: str | None = None,
    ) -> bool:
        """上下文压缩事件：保留当前轮活跃卡片，由正常完成钩子完成封卡（#59）."""
        return self.on_compression(session_key=session_key, message_id=message_id)

    def on_compression(
        self,
        *,
        session_key: str | None = None,
        message_id: str | None = None,
    ) -> bool:
        """同步入口：观测上下文压缩，不提前封卡（#59）.

        网关在压缩时检测到会话 ID 变更，但当前轮次的回复仍在流式输出或等待定稿。
        保留卡片处于流式活跃状态，避免提前封卡导致正常完成时找不到会话而重复发送纯文本。
        """
        if not self.enabled:
            return False
        session = self._resolve_session(message_id, session_key)
        if session is None or session.state.is_terminal:
            return False
        _logger.info(
            "session_compressed_retained: msg=%s key=%s card=%s state=%s",
            session.message_id[:12],
            session_key or "",
            (session.card_id or "")[:12],
            session.state,
        )
        return True

    def _fire_and_forget(
        self,
        coro: Coroutine[Any, Any, Any],
        loop: asyncio.AbstractEventLoop,
    ) -> asyncio.Future[Any] | ConcurrentFuture | None:
        try:
            task = loop.create_task(coro)
            task.add_done_callback(self._on_bg_task_done)
            return task
        except RuntimeError:
            try:
                fut = asyncio.run_coroutine_threadsafe(coro, loop)
                fut.add_done_callback(self._on_bg_task_done)
                return fut
            except Exception:
                _logger.debug("fire_and_forget failed", exc_info=True)
                return None

    def _register_session(
        self,
        session: CardSession,
        *,
        anchor_id: str | None = None,
        session_key: str | None = None,
    ) -> None:
        """统一 session 注册入口：维护 _sessions（message_id + anchor 别名）与 _session_keys.

        #43：同一 session_key 下出现新会话即「重定向换卡」——新卡接管该键，
        旧卡收尾；否则 session_key 仍指向旧卡，提示与完成回调都会落到已封的卡上。
        """
        previous = self._session_keys.get(session_key) if session_key else None
        if previous is not None and previous is not session and not previous.state.is_terminal:
            previous.state = SessionState.ABORTED
            previous.flush.mark_completed()
            _logger.info(
                "session_key_takeover: key=%s old=%s new=%s",
                session_key,
                previous.message_id[:12],
                session.message_id[:12],
            )
            self._complete_session(previous)
        self._sessions[session.message_id] = session
        self._track_live_card(session)  # #43：跨进程登记，网关重启时封卡（创建成功再覆盖）
        if anchor_id and anchor_id != session.message_id:
            session.anchor_id = anchor_id
            self._sessions[anchor_id] = session
        if session_key:
            session.session_key = session_key
            self._session_keys[session_key] = session
            # 提示先于新卡登记到达（重定向开口与建卡并发）时，注册成功这里补写
            pending = self._pending_notices.pop(session_key, [])
            if session.segment_state is not None:
                for text in pending:
                    session.segment_state.add_notice(text, kind="redirect")

    def _chat_type_allowed(self, chat_type: str) -> bool:
        """聊天类型是否允许发流式卡片.

        ``streaming.chat_types`` 缺省（Config.chat_types == None）→ 全部类型都允许；
        显式配置 → 仅命中列表内的聊天类型发卡片。
        空 chat_type（上游未提供）视为允许，保证向后兼容。
        """
        try:
            allowed = self._cfg.chat_types
        except Exception:
            return True
        if allowed is None or not chat_type:
            return True
        return chat_type.strip().lower() in allowed

    def on_message_started(
        self,
        *,
        message_id: str | None,
        chat_id: str,
        chat_type: str = "",
        anchor_id: str | None = None,
        session_key: str | None = None,
    ) -> str | None:
        """消息处理开始 — 创建会话 + 发占位卡片.

        message_id 缺失时（后台通知/clarify 恢复等合成轮次）降级为 synthetic
        会话：卡片直接 send_card_to_chat，不再放弃卡片回退纯文本。
        合成 id（如后台完成回执的 ``["completion", ...]`` 序列，#43 反馈）同样走
        synthetic 投递：它不是飞书 open_message_id，reply 必然 99992354。

        返回实际创建的 message_id（无 id 的合成轮次为 ``syn-*``），失败返回 None。
        排队续轮的收尾钩子需要它把卡片归属传给完成钩子（#35）。
        """
        if not self.enabled:
            return None
        synthetic = False
        if message_id and not isinstance(message_id, str):
            # 防御：合成事件可能直接传序列对象，_sessions 以它作键会 unhashable
            import uuid as _uuid

            _logger.info(
                "on_message_started: non-str message_id %r, degrading to syn-*",
                message_id,
            )
            message_id = "syn-" + _uuid.uuid4().hex[:16]
            synthetic = True
            anchor_id = None
        elif message_id and _is_synthetic_turn_id(message_id):
            # 合成 id 保留原值作会话键（完成回调按它找会话），但卡片不能 reply 它
            if not chat_id or not session_key:
                _logger.warning(
                    "on_message_started: synthetic turn id without chat/session_key, chat=%s",
                    chat_id[:12] if chat_id else "",
                )
                return None
            synthetic = True
            anchor_id = None
        elif not message_id:
            if not chat_id or not session_key:
                _logger.warning("on_message_started: missing message_id, chat=%s", chat_id[:12])
                return None
            import uuid as _uuid

            message_id = "syn-" + _uuid.uuid4().hex[:16]
            synthetic = True
            anchor_id = None
        if not self._chat_type_allowed(chat_type):
            _logger.info(
                "chat_type filtered: no card for chat_type=%r chat=%s msg=%s",
                chat_type,
                chat_id[:12],
                message_id[:12],
            )
            return None
        existing = self._sessions.get(message_id)
        if existing is not None and not existing.state.is_terminal:
            return message_id

        self._prune_stale_sessions()

        loop = self._get_loop()
        if loop is None:
            _logger.warning("no event loop available, skipping: msg=%s", message_id[:12])
            return None
        session = CardSession(message_id, chat_id, loop)
        session.synthetic = synthetic
        self._register_session(session, anchor_id=anchor_id, session_key=session_key)
        _logger.info(
            "session_created: msg=%s chat=%s anchor=%s key=%s%s",
            message_id[:12],
            chat_id[:12],
            (anchor_id or "")[:12],
            (session_key or "")[:12],
            " synthetic=1" if synthetic else "",
        )

        session.create_task = self._fire_and_forget(self._do_create_card(session), loop)
        return message_id

    def _mark_text_fallback_needed(self, session: CardSession) -> None:
        keys = {session.message_id}
        if session.anchor_id:
            keys.add(session.anchor_id)
        self._text_fallback_needed.update(keys)
        for key in keys:
            self._text_fallback_aliases[key] = set(keys)

    def consume_text_fallback(self, message_id: str) -> bool:
        """Return whether gateway should undo already_sent and deliver plain text."""
        if message_id not in self._text_fallback_needed:
            return False
        keys = self._text_fallback_aliases.pop(message_id, {message_id})
        for key in keys:
            self._text_fallback_needed.discard(key)
            self._text_fallback_aliases.pop(key, None)
        return True

    def stream_card_healthy(
        self,
        message_id: str | None = None,
        session_key: str | None = None,
    ) -> bool:
        """fry 卡片是否仍在健康承载这轮回复（网关最终投递抑制判定用）.

        #43 反馈：答案增量被插件接走后 Hermes 自己的 stream consumer 全程空转，
        ``_run_agent_mark_streamed_delivery`` 判 streamed=False 打重复投递警告。
        ``mark_stream_delivered`` 把本方法作为 ``has_durably_delivered_text`` 谓词
        挂到消费者上：卡活着（流式/收尾中且有卡）→ True 让网关走抑制分支；
        卡挂了 → False，最终文本仍由网关投递。

        ``session_key``（#48）：合成轮次没有 event_message_id，按 session_key
        兜底解析活跃卡（syn-* 会话注册时已同步进 ``_session_keys``）。
        """
        if not self.enabled or (not message_id and not session_key):
            return False
        session = self._resolve_session(message_id, session_key)
        if session is None or session.state.is_terminal:
            return False
        return session.has_card or session.state in (SessionState.IDLE, SessionState.CREATING)

    def on_thinking(self, *, message_id: str, text: str, session_key: str | None = None) -> bool:
        """思考内容增量."""
        if not self.enabled:
            return False
        session = self._resolve_session(message_id, session_key)
        if session is None or session.guard.should_skip("on_thinking"):
            return False

        if session.segment_state is None:
            return False
        return self._on_thinking_segment(session, text)

    def on_reasoning(self, *, message_id: str, text: str, session_key: str | None = None) -> bool:
        """Native model reasoning delta (incremental append)."""
        if not self.enabled:
            return False
        session = self._resolve_session(message_id, session_key)
        if session is None or session.guard.should_skip("on_reasoning"):
            return False
        if not self._cfg.show_reasoning:
            return False

        if session.segment_state is None:
            return False

        session.segment_state.on_reasoning_delta(text)
        self._schedule_flush(session)
        return True

    def on_tool_update(
        self,
        *,
        message_id: str,
        tool_name: str,
        status: str,
        detail: str = "",
        session_key: str | None = None,
    ) -> bool:
        """工具调用事件."""
        if not self.enabled:
            return False
        session = self._resolve_session(message_id, session_key)
        if session is None or session.guard.should_skip("on_tool_update"):
            return False
        if session.segment_state is None:
            return False

        if status in ("running", "started", "tool.started"):
            session.tool_use.record_start(tool_name, detail)
        else:
            is_error = status in ("error", "failed")
            session.tool_use.record_end(
                tool_name,
                error=detail if is_error else "",
                output="" if is_error else detail,
            )
            # clarify 工具 ended 且有待封卡标志 → 封旧卡 + 建新卡
            if session.clarify_pending_split:
                if tool_name and tool_name.strip().lower() == "clarify":
                    session.clarify_pending_split = False
                    self._schedule_clarify_split(session)
                else:
                    # pending 标志残留但工具名不匹配（如上游改名）——记录以便排查，
                    # 避免切卡静默失效。
                    _logger.warning(
                        "clarify_pending_split set but tool '%s' did not match, msg=%s",
                        tool_name,
                        session.message_id[:12],
                    )

        session.segment_state.on_tool_event(len(session.tool_use.build_display_steps()))
        self._schedule_flush(session)
        return True

    def _schedule_clarify_split(self, session: CardSession) -> None:
        """封旧卡 + 建新卡（clarify 工具 ended 后触发）。"""
        loop = self._get_loop()
        if loop is None:
            return
        future: ConcurrentFuture | None = None
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._do_clarify_split(session), loop,
            )
            # 预算 = 卡片创建等待 + seal/建卡余量
            future.result(timeout=_CARD_CREATION_WAIT_SEC * 2 + 30)
        except Exception:
            _logger.warning(
                "clarify_split failed or timed out, msg=%s",
                session.message_id[:12],
                exc_info=True,
            )
            # 取消仍在运行的切卡协程，避免与后续 flush 重叠。
            if future is not None and not future.done():
                future.cancel()

    def on_answer(self, *, message_id: str, text: str, session_key: str | None = None) -> bool:
        """答案文本增量（流式）."""
        if not self.enabled:
            return False
        session = self._resolve_session(message_id, session_key)
        if session is None or session.guard.should_skip("on_answer"):
            return False
        if session.segment_state is None:
            return False

        answer_text = strip_reasoning_tags(text)
        if not answer_text:
            return False

        session.segment_state.on_answer_delta(answer_text)
        self._schedule_flush(session)
        return True

    def on_aborted(self, *, message_id: str) -> None:
        """用户 /stop 导致消息被中断."""
        if not self.enabled:
            return
        session = self._get_active_session(message_id)
        if session is None:
            return

        session.state = SessionState.ABORTED
        session.flush.mark_completed()
        _logger.info("on_aborted: msg=%s state=ABORTED", message_id[:12])

        # 确保 abort 时 footer 有时间 + 模型显示
        if not session.footer.get("duration") and hasattr(session, "created_at"):
            import time as _t
            session.footer["duration"] = _t.time() - session.created_at
        if not session.footer.get("model"):
            session.footer["model"] = ""

        self._complete_session(session)

    async def on_session_aborted(self, *, session_key: str) -> bool:
        """Terminate the active card bound to a Hermes session key."""
        if not self.enabled or not session_key:
            return False
        session = self._session_keys.pop(session_key, None)
        if session is None or session.state.is_terminal:
            return False

        session.state = SessionState.ABORTED
        session.flush.mark_completed()
        _logger.info("on_session_aborted: msg=%s state=ABORTED", session.message_id[:12])

        # 确保 abort 时 footer 有时间显示
        if not session.footer.get("duration") and hasattr(session, "created_at"):
            import time as _t
            session.footer["duration"] = _t.time() - session.created_at
        if not session.footer.get("model"):
            session.footer["model"] = ""

        return await self._complete_session_after_creation(session)

    def on_interrupted(
        self,
        *,
        old_message_id: str,
        new_message_id: str,
        chat_id: str,
        chat_type: str = "",
        anchor_id: str | None = None,
        session_key: str | None = None,
        map_completion: bool = True,
    ) -> None:
        """用户发送新消息导致前一条消息被中断 — abort A + create B（幂等）.

        ``map_completion`` 仅在 Hermes 确定将正在跑的回合重定向到新消息时有效（run_busy
        的 ``redirected=True``）；普通打断（run_turn 的 was_interrupted）新消息自己成一轮，
        旧回合迟到的完成事件直接丢弃，不映射到新卡上（#65）。
        """
        if not self.enabled:
            return

        # ── 去重：同一次打断的两个注入点在毫秒级内先后到达 ──
        _now = time.monotonic()
        # 去重键只用 new_message_id：第二个注入点的 old_message_id 可能已经被换成
        # 刚建好的新会话（日志里 old==new），用 (old, new) 会漏判。
        _dedup_key = new_message_id
        _last = self._interrupt_dedup.get(_dedup_key)
        if _last is not None and _now - _last < 2.0:
            _logger.info(
                "interrupt_duplicate_skipped: old=%s new=%s dt=%.3fs",
                old_message_id, new_message_id, _now - _last,
            )
            return
        self._interrupt_dedup[_dedup_key] = _now
        if len(self._interrupt_dedup) > 64:
            _cut = _now - 10.0
            self._interrupt_dedup = {k: v for k, v in self._interrupt_dedup.items() if v >= _cut}

        old_session = self._get_active_session(old_message_id)
        session_key = session_key or (old_session.session_key if old_session is not None else None)

        # ── 幂等：同一次打断只处理一次（#issue 一次重定向两张卡） ──
        # 重定向有两个注入点（REDIRECT 回执前 / REDIRECT_RESOLVED 防抖前），同一次
        # 打断会各调一次。第二次若照旧按 old_message_id 解析，很容易拿到 anchor 别名
        # 指向的「新会话」并把它 abort 掉，于是同一个 new_message_id 又建出一张卡，
        # 提示写进随后被丢弃的那个会话。先认领已建的新卡：先按 new_message_id，
        # 再按 session_key，命中非终态会话就直接复用。
        # 同 id 中断（重试/重生成同一条消息）是「换掉这张卡」，不属于重复触发。
        existing = None
        if new_message_id != old_message_id:
            existing = self._sessions.get(new_message_id)
            if existing is not None and existing.state.is_terminal:
                existing = None
            if existing is None and session_key:
                key_session = self._session_keys.get(session_key)
                if (
                    key_session is not None
                    and not key_session.state.is_terminal
                    and key_session is not old_session
                ):
                    existing = key_session

        if old_session is not None and old_session is not existing:
            old_session.state = SessionState.ABORTED
            old_session.flush.mark_completed()
            # #43 反馈：进行中的提示（忙碌/重定向）随新卡走，旧卡正文不复述——
            # 否则同一句话在旧卡的完成态和新卡上各出现一遍。完成后的后台回执保留。
            old_session.clear_progress_notices()
            _logger.info(
                "interrupt_abort_old: msg=%s -> msg=%s",
                old_message_id[:12],
                new_message_id[:12],
            )
            self._complete_session(old_session)

        if existing is not None:
            self._reuse_interrupted_session(
                existing,
                new_message_id=new_message_id,
                anchor_id=anchor_id,
                session_key=session_key,
            )
            _logger.info(
                "interrupt_reused: msg=%s old=%s key=%s (duplicate interrupt, no new card)",
                new_message_id[:12],
                old_message_id[:12],
                (session_key or "")[:12],
            )
        else:
            # 若该聊天类型被过滤（如群聊不发卡片），新消息 B 不创建卡片会话，
            # 但中断映射仍记录，保证旧会话完成/回落逻辑一致。
            new_card_allowed = self._chat_type_allowed(chat_type)
            loop = self._get_loop()
            if loop is not None and new_card_allowed:
                reply_anchor_id = anchor_id if anchor_id and anchor_id != new_message_id else None
                session = CardSession(new_message_id, chat_id, loop)
                self._register_session(session, anchor_id=reply_anchor_id, session_key=session_key)
                _logger.info(
                    "session_created: msg=%s chat=%s anchor=%s key=%s (interrupt redirect)",
                    new_message_id[:12],
                    chat_id[:12],
                    (reply_anchor_id or "")[:12],
                    (session_key or "")[:12],
                )
                session.create_task = self._fire_and_forget(self._do_create_card(session), loop)
            elif not new_card_allowed:
                _logger.info(
                    "chat_type filtered: interrupt no card for chat_type=%r chat=%s msg=%s",
                    chat_type,
                    chat_id[:12],
                    new_message_id[:12],
                )

        if map_completion:
            self._interrupt_map[old_message_id] = new_message_id
            for key, val in list(self._interrupt_map.items()):
                if val == old_message_id and key != old_message_id:
                    self._interrupt_map[key] = new_message_id
        else:
            self._interrupted_tails[old_message_id] = time.monotonic()

    def _reuse_interrupted_session(
        self,
        session: CardSession,
        *,
        new_message_id: str,
        anchor_id: str | None,
        session_key: str | None,
    ) -> None:
        """把已建的新会话登记到这次打断的 message / anchor / session_key 键上.

        只补登记、不抢键：``anchor_id`` 已经指向别的活跃卡时保持原样，避免把一个
        正在跑的会话从它自己的键上顶掉（那正是重复建卡的成因）。
        """
        if self._sessions.get(new_message_id) is not session:
            self._sessions[new_message_id] = session
        if session_key and self._session_keys.get(session_key) is not session:
            session.session_key = session.session_key or session_key
            self._session_keys[session_key] = session
        reply_anchor_id = anchor_id if anchor_id and anchor_id != new_message_id else None
        if reply_anchor_id and reply_anchor_id != session.message_id:
            holder = self._sessions.get(reply_anchor_id)
            if holder is None or holder is session or holder.state.is_terminal:
                session.anchor_id = reply_anchor_id
                self._sessions[reply_anchor_id] = session

    def on_clarify_enter(
        self,
        *,
        message_id: str,
        chat_id: str | None = None,
        session_key: str | None = None,
    ) -> None:
        """clarify 进入：仅暂停 flush，等 tool.completed 再封卡（避免工具状态卡在 running）。"""
        if not self.enabled:
            return
        session = self._get_active_session(message_id)
        if session is None or session.state != SessionState.STREAMING:
            return
        session.state = SessionState.CLARIFY_PAUSED  # 暂停 flush，保留当前卡

    def on_clarify_exit(
        self,
        *,
        message_id: str,
        chat_id: str | None = None,
        session_key: str | None = None,
    ) -> None:
        """clarify 退出：标记待封卡，恢复 STREAMING，等 tool.completed 触发切卡。"""
        if not self.enabled:
            return
        session = self._sessions.get(message_id)
        if session is None or session.state != SessionState.CLARIFY_PAUSED:
            return  # 已被 interrupt 接管 / enter 未生效
        session.clarify_pending_split = True
        session.state = SessionState.STREAMING  # 让 tool.completed 能更新卡片

    def _yield_to_gateway(self, session: CardSession, *, reason: str) -> None:
        """统一 fallback 决策：卡片无法收尾时标记文本回退（无卡时）+ 清理，交还网关默认回复."""
        if not session.has_card:
            self._mark_text_fallback_needed(session)
        _logger.info(
            "fallback_to_text: msg=%s state=%s has_card=%s reason=%s",
            session.message_id[:12],
            session.state,
            session.has_card,
            reason,
        )
        self._cleanup_session(session)

    async def on_completed_wait(
        self,
        *,
        message_id: str,
        answer: str = "",
        is_error: bool = False,
        is_interrupted: bool = False,
        duration: float = 0.0,
        model: str = "",
        tokens: dict | None = None,
        context: dict | None = None,
        session_key: str | None = None,
        answer_rewritten: bool = False,
    ) -> bool:
        """消息处理完成，并等待卡片真正收尾后返回是否已发送."""
        if not self.enabled:
            return False
        requested_id = message_id
        session = self._completion_session(message_id, session_key)
        if session is None:
            return False
        message_id = session.message_id

        if not await self._wait_for_card_creation(session):
            self._yield_to_gateway(session, reason="card_creation_not_ready")
            return False

        if session.state == SessionState.FAILED:
            self._yield_to_gateway(session, reason="session_failed")
            return False

        if not session.has_card:
            self._yield_to_gateway(session, reason="no_card")
            return False

        _logger.info(
            "on_completed_wait: msg=%s has_card=%s state=%s interrupted=%s",
            message_id[:12],
            session.has_card,
            session.state,
            is_interrupted,
        )

        self._apply_completion_payload(
            session=session,
            answer=answer,
            duration=duration,
            model=model,
            tokens=tokens,
            context=context,
            answer_rewritten=answer_rewritten,
        )
        if is_error:
            session.mark_failed(reason="agent_returned_error")
        elif is_interrupted:
            session.state = SessionState.ABORTED
            _logger.info("completion interrupted: msg=%s", message_id[:12])

        completed = await self._complete_session_wait(session)
        if completed and is_interrupted and not is_error:
            session.state = SessionState.ABORTED
        _logger.info(
            "complete_session_wait: msg=%s completed=%s state=%s",
            message_id[:12], completed, session.state,
        )
        if completed and (is_error or not is_interrupted):
            self._maybe_send_completion_notice(session, is_error=is_error)
        elif not completed:
            # 封卡失败（重试 3 次后仍不成）：标记文本回退，撤销 already_sent，
            # 让网关把最终回复按纯文本补发——流式抑制标记可能已把 already_sent
            # 置位（见 mark_stream_delivered），不撤销用户就什么都收不到。
            self._mark_text_fallback_needed(session)
            if requested_id and requested_id != session.message_id:
                # 完成回调按原始 id 查询回退标志（重定向后二者可能不同）
                self._text_fallback_needed.add(requested_id)
                self._text_fallback_aliases[requested_id] = {
                    requested_id, session.message_id,
                }
        return completed

    def _maybe_send_completion_notice(self, session: CardSession, *, is_error: bool) -> None:
        if not self._cfg.completion_notice or not session.card_msg_id:
            return
        try:
            parts = [self._cfg.completion_notice_text]
            duration = float(session.footer.get("duration") or 0)
            if duration > 0:
                parts.append(f"{duration:.0f}s")
            if is_error:
                parts.append("出错")
            self._fire_and_forget(
                self._send_completion_notice(session, " · ".join(parts)),
                session._loop,
            )
        except Exception:
            _logger.debug("completion notice skipped", exc_info=True)

    async def _send_completion_notice(self, session: CardSession, text: str) -> None:
        try:
            await self._client.send_text_to_chat(
                session.chat_id,
                text,
                reply_to_message_id=session.card_msg_id,
            )
        except Exception:
            _logger.debug("completion notice send failed", exc_info=True)

    def on_cron_deliver(
        self,
        *,
        chat_id: str,
        content: str,
        loop: asyncio.AbstractEventLoop | None,
        task_name: str = "",
        run_time: str = "",
    ) -> bool:
        """Cron 推送 — 包装为静态卡片发送，成功返回 True."""
        if not self.enabled or not content or not chat_id:
            return False
        coroutine = self._do_cron_deliver(
            chat_id, content, task_name=task_name, run_time=run_time
        )
        try:
            if loop is not None and loop.is_running() and not loop.is_closed():
                try:
                    future = asyncio.run_coroutine_threadsafe(coroutine, loop)
                except Exception:
                    coroutine.close()
                    raise
                future.result(timeout=30)
            else:
                asyncio.run(coroutine)
            _logger.info("cron card delivered: chat=%s len=%d", chat_id[:12], len(content))
            return True
        except Exception:
            _logger.warning(
                "cron card delivery failed: chat=%s task=%s",
                str(chat_id)[:12],
                task_name or "<unnamed>",
                exc_info=True,
            )
            return False

    async def on_background_deliver(
        self,
        *,
        chat_id: str,
        preview: str,
        content: str,
        reply_to_message_id: str | None = None,
    ) -> bool:
        """Background 任务完成推送 — 包装为静态卡片发送，成功返回 True.

        #43：若该后台任务属于仍活跃的卡片会话（按任务自己的回复标识
        ``reply_to_message_id`` 解析），不再另发一张静态卡，改为把回执写进
        当前卡的通知槽，随完成态卡片一起呈现；否则保持原有静态卡投递。
        """
        if not self.enabled or not content or not chat_id:
            return False
        session = self._notice_session(reply_to_message_id)
        if session is not None and session.chat_id == chat_id and session.segment_state is not None:
            session.segment_state.add_notice(content, kind="background")
            self._schedule_flush(session)
            _logger.info("background_receipt_merged: id=%s chat=%s", session.message_id, chat_id[:12])
            return True
        return await self._deliver_background_static(chat_id, content, reply_to_message_id)

    async def on_background_completion(
        self,
        *,
        chat_id: str,
        session_key: str | None = None,
        text: str,
        command: str | None = None,
        started_at: float | str | None = None,
        exit_code: int | str | None = None,
        session_id: str | None = None,
    ) -> bool:
        """run_notifications 的 watch/completion 通知接管（#47）.

        该路径把完成通知合成内部消息注入，纯文本气泡直达，不经过卡片管线
        （无 START 钩子可拦）。接管规则：
        - 该会话此刻有活跃卡（按 session_key，其次唯一活跃卡）→ 按 #43 通知
          分区并入（kind=background，完成后通知置顶）
        - 无活跃卡 → 复用合成轮次的静态卡路径（send_card_to_chat，不 reply
          旧消息——evt 里的 message_id 是任务的触发消息，投递时刻已过期 #52694）
        都不行（禁用 / 无 chat_id / 投递失败）→ 返回 False，Hermes 走原注入
        路径发纯文本，功能无损降级。

        ``text`` 是模型侧的内部文案（``[IMPORTANT: ...]``），卡片上换成
        ``background_completion_text`` 拼的用户文案；``command`` / ``started_at`` /
        ``exit_code`` 由注入块从 evt 透传，缺字段时回退到去包裹后的首行。
        ``session_id`` 是网关的进程会话 id，只用于登记「回执已由卡片投递」，让
        turn_busy 分支不再补发一条纯文本（见 ``remember_merged_busy_receipt``）。
        """
        if not self.enabled or not text.strip() or not chat_id:
            return False
        display_text = background_completion_text(
            text, command=command, started_at=started_at, exit_code=exit_code
        )
        session = self._notice_session(None, session_key)
        if session is not None and session.chat_id == chat_id and session.segment_state is not None:
            session.segment_state.add_notice(display_text, kind="background")
            self._schedule_flush(session)
            remember_merged_busy_receipt(
                session_id=session_id, session_key=session_key, started_at=started_at
            )
            _logger.info(
                "background_completion_merged: id=%s chat=%s",
                session.message_id,
                chat_id[:12],
            )
            return True
        delivered = await self._deliver_background_static(chat_id, display_text)
        if delivered:
            # 独立静态卡本身就是回执：网关再补一条纯文本同样重复。
            remember_merged_busy_receipt(
                session_id=session_id, session_key=session_key, started_at=started_at
            )
        return delivered

    async def _deliver_background_static(
        self,
        chat_id: str,
        content: str,
        reply_to_message_id: str | None = None,
    ) -> bool:
        """无活跃卡时的静态卡投递；终端码只留一行 warning（#43/#47 共用）."""
        try:
            await self._do_background_deliver(
                chat_id,
                content[:80],
                content,
                reply_to_message_id=reply_to_message_id,
            )
            _logger.info("background card delivered: chat=%s len=%d", chat_id[:12], len(content))
            return True
        except Exception as exc:
            code = extract_api_code(exc)
            if is_terminal_api_code(code):
                # 目标消息已失效（删除/撤回/open_message_id 过期）—— 通知无处可投，属预期路径，
                # 只留一行 warning，不打堆栈（completion 通知一天可复现多次）。
                _logger.warning(
                    "background card delivery skipped: target message gone code=%s chat=%s",
                    code,
                    chat_id[:12],
                )
            else:
                _logger.warning("background card delivery failed", exc_info=True)
            return False

    def defer_background_review(
        self,
        *,
        message_id: str,
        text: str,
        sender: Callable[[str], Any],
    ) -> bool:
        """暂存 Hermes background review 通知，等卡片收尾后再发送."""
        if not self.enabled or not text or not callable(sender):
            return False
        session = self._get_active_session(message_id)
        if session is None:
            return False
        with session.deferred_background_review_lock:
            if session.deferred_background_review_closed:
                return False
            session.deferred_background_reviews.append((text, sender))
        return True

    def _flush_deferred_background_reviews(self, session: CardSession) -> None:
        lock = getattr(session, "deferred_background_review_lock", None)
        reviews = getattr(session, "deferred_background_reviews", None)
        if lock is None or reviews is None:
            return
        with lock:
            session.deferred_background_review_closed = True
            pending = list(reviews)
            reviews.clear()
        for text, sender in pending:
            try:
                sender(text)
            except Exception:
                _logger.debug("background review sender failed", exc_info=True)

    def _dispose_session(self, session: CardSession) -> None:
        """统一 session 清理入口（幂等）：只清理仍指向该 session 的索引."""
        removed_any = False
        keys = [session.message_id]
        if session.anchor_id:
            keys.append(session.anchor_id)
        for key in keys:
            if self._sessions.get(key) is session:
                del self._sessions[key]
                removed_any = True
        session_key = session.session_key
        if session_key and self._session_keys.get(session_key) is session:
            del self._session_keys[session_key]
            removed_any = True
        stale_keys = [key for key, value in self._interrupt_map.items() if value == session.message_id]
        for key in stale_keys:
            del self._interrupt_map[key]
            removed_any = True
        session.flush.mark_completed()
        if session.image_resolver:
            session.image_resolver.cancel_pending()
        self._untrack_live_card(session)  # #43：正常收尾后不再被重启清扫
        if removed_any:
            _logger.info(
                "session_disposed: msg=%s anchor=%s key=%s",
                session.message_id[:12],
                (session.anchor_id or "")[:12],
                (session.session_key or "")[:12],
            )
        else:
            _logger.debug("cleanup_idempotent: msg=%s already disposed", session.message_id[:12])

    def _cleanup(self, message_id: str) -> None:
        """兼容入口：按 message_id 解析 session 后走统一清理."""
        session = self._sessions.get(message_id)
        if session is None:
            _logger.debug("cleanup: no session registered for msg=%s", message_id[:12])
            return
        self._dispose_session(session)

    def _cleanup_session(self, session: CardSession) -> None:
        self._dispose_session(session)

    def _completion_session(
        self, message_id: str | None, session_key: str | None = None
    ) -> CardSession | None:
        session = self._sessions.get(message_id) if message_id else None
        # 允许 FAILED session 重新获取以便完成收尾，排除已 COMPLETED/ABORTED 的
        if session is not None and session.state != SessionState.COMPLETED and session.state != SessionState.ABORTED:
            return session

        redirected_id = self._interrupt_map.pop(message_id, None) if message_id else None
        if redirected_id is not None:
            _logger.info(
                "on_completed: redirect msg=%s -> msg=%s",
                (message_id or "")[:12],
                redirected_id[:12],
            )
            redirected = self._sessions.get(redirected_id)
            if redirected is not None and not redirected.state.is_terminal:
                return redirected

        # 普通打断的旧尾巴：直接丢弃，不回退到 session_key 避免污染新卡（#65）
        if message_id and message_id in self._interrupted_tails:
            _logger.info("on_completed: dropping discarded interrupted tail msg=%s", message_id[:12])
            self._interrupted_tails.pop(message_id, None)
            return None

        if session_key:
            fallback = self._session_keys.get(session_key)
            if fallback is not None and fallback.state not in (
                SessionState.COMPLETED, SessionState.ABORTED
            ):
                return fallback
        return None

    async def _wait_for_card_creation(self, session: CardSession) -> bool:
        task = session.create_task
        if task is None:
            return True
        try:
            if isinstance(task, asyncio.Future):
                await asyncio.wait_for(task, timeout=_CARD_CREATION_WAIT_SEC)
            else:
                await asyncio.wait_for(asyncio.wrap_future(task), timeout=_CARD_CREATION_WAIT_SEC)
            return True
        except TimeoutError:
            _logger.warning(
                "card creation timed out: msg=%s timeout=%.1fs",
                session.message_id[:12],
                _CARD_CREATION_WAIT_SEC,
            )
            task.cancel()
            session.mark_failed(reason="card_creation_timeout")
            return False
        except asyncio.CancelledError:
            session.mark_failed(reason="card_creation_cancelled")
            return False
        except Exception:
            _logger.debug("card creation task failed", exc_info=True)
            return False

    def _apply_completion_payload(
        self,
        *,
        session: CardSession,
        answer: str,
        duration: float,
        model: str,
        tokens: dict | None,
        context: dict | None,
        answer_rewritten: bool = False,
    ) -> None:
        if answer and session.segment_state:
            final_answer = strip_reasoning_tags(answer)
            if final_answer:
                session.segment_state.on_completion_answer(final_answer, replaced=answer_rewritten)

        # duration 为 0 时 fallback 到 session 已运行时间
        _duration = duration
        if not _duration and hasattr(session, "created_at"):
            import time as _t
            _duration = _t.time() - session.created_at
        prev_model = session.footer.get("model") or ""
        _cache_read = (tokens or {}).get("cache_read_tokens") or 0
        _tps = (tokens or {}).get("tokens_per_sec") or 0
        session.footer = {
            "duration": _duration,
            "model": model or prev_model,
            **({"input_tokens": tokens.get("input_tokens")} if tokens else {}),
            **({"output_tokens": tokens.get("output_tokens")} if tokens else {}),
            **({"cache_read_tokens": _cache_read} if _cache_read else {}),
            **({"tokens_per_sec": _tps} if _tps else {}),
            **({"context_used": context.get("used_tokens")} if context else {}),
            **({"context_max": context.get("max_tokens")} if context else {}),
        }

    def _complete_session(self, session: CardSession) -> None:
        """异步完成当前流式卡片."""
        session.flush.mark_completed()
        self._fire_and_forget(self._complete_session_after_creation(session), session._loop)

    async def _complete_session_after_creation(self, session: CardSession) -> bool:
        if not await self._wait_for_card_creation(session):
            self._cleanup_session(session)
            return False
        return await self._complete_session_wait(session)

    async def _complete_session_wait(self, session: CardSession) -> bool:
        """完成当前流式卡片，并等待最终 API 结果."""
        session.flush.mark_completed()
        return await self._do_complete_card(session)

    def _prune_stale_sessions(self) -> None:
        now = time.time()
        stale = [mid for mid, s in self._sessions.items() if mid is not None and now - s.created_at > self._session_ttl]
        for mid in stale:
            _logger.warning("stale_pruned: msg=%s ttl=%.0fs", mid[:12], self._session_ttl)
            self._cleanup(mid)

    @staticmethod
    def _on_bg_task_done(fut: asyncio.Future[Any] | ConcurrentFuture) -> None:
        try:
            fut.result()
        except asyncio.CancelledError:
            return
        except Exception:
            _logger.warning("background task failed: task=%s", getattr(fut, "get_name", lambda: "?")(), exc_info=True)


_controllers: dict[str, StreamCardController] = {}
_controller_lock = threading.Lock()


def get_controller() -> StreamCardController:
    profile_home = hermes_home().resolve()
    key = str(profile_home)
    with _controller_lock:
        controller = _controllers.get(key)
        if controller is None:
            controller = StreamCardController(profile_home)
            _controllers[key] = controller
        return controller
