"""Card session state shared by controller and card orchestration."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from concurrent.futures import Future as ConcurrentFuture
from enum import StrEnum
from threading import Lock
from typing import TYPE_CHECKING, Any

from .flush import CARDKIT_MS, FlushController
from .segments import Segment, SegmentState
from .tooluse import ToolUseTracker
from .unavailable_guard import UnavailableGuard

if TYPE_CHECKING:
    from .image import ImageResolver

_logger = logging.getLogger("hermes_fry_cards")


class SessionState(StrEnum):
    IDLE = "idle"
    CREATING = "creating"
    STREAMING = "streaming"
    CLARIFY_PAUSED = "clarify_paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"

    @property
    def is_terminal(self) -> bool:
        return self in {
            SessionState.COMPLETED,
            SessionState.FAILED,
            SessionState.ABORTED,
        }


class CardSession:
    """单条消息的卡片会话状态."""

    __slots__ = (
        "_loop",
        "anchor_id",
        "card_id",
        "card_msg_id",
        "chat_id",
        "clarify_pending_split",
        "create_task",
        "created_at",
        "deferred_background_review_closed",
        "deferred_background_review_lock",
        "deferred_background_reviews",
        "element_count",
        "flush",
        "footer",
        "guard",
        "image_resolver",
        "message_id",
        "segment_state",
        "sequence",
        "session_key",
        "split_disabled",
        "split_index",
        "state",
        "synthetic",
        "tool_panel_created",
        "tool_panel_estimate",
        "tool_use",
    )

    def __init__(
        self,
        message_id: str,
        chat_id: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.message_id = message_id
        self.anchor_id: str | None = None
        self.chat_id = chat_id
        # synthetic=True：无真实飞书 message_id 的轮次（后台通知/clarify 恢复等），
        # 卡片直接 send_card_to_chat 而非 reply，且完成时接管网关文本投递防止刷屏。
        self.synthetic: bool = False
        self.session_key: str | None = None
        self.create_task: asyncio.Future[Any] | ConcurrentFuture | None = None
        self.state = SessionState.IDLE
        self.card_msg_id: str | None = None
        self.card_id: str | None = None
        self.tool_use = ToolUseTracker()
        self.tool_panel_estimate: int = 0  # 合并面板模式下工具面板当前的总元素估算
        self.flush = FlushController(throttle_ms=CARDKIT_MS, loop=loop)
        self.footer: dict[str, Any] = {}
        self.sequence = 1
        self._loop = loop
        self.created_at = time.time()
        self.deferred_background_review_closed = False
        self.deferred_background_reviews: list[tuple[str, Callable[[str], Any]]] = []
        self.deferred_background_review_lock = Lock()

        self.guard = UnavailableGuard(
            reply_to_message_id=message_id,
            get_card_message_id=lambda: self.card_msg_id,
            on_terminate=self.mark_failed,
        )

        self.image_resolver: ImageResolver | None = None
        self.segment_state: SegmentState | None = SegmentState()
        self.element_count: int = 0
        self.split_disabled = False
        self.split_index: int = 0
        self.clarify_pending_split: bool = False
        self.tool_panel_created: bool = False

    @property
    def has_card(self) -> bool:
        return bool(self.card_id or self.card_msg_id)

    def set_card(self, *, card_id: str, card_msg_id: str) -> None:
        self.card_id = card_id
        self.card_msg_id = card_msg_id

    def mark_failed(self, reason: str = "") -> None:
        if self.state == SessionState.FAILED and not reason:
            return  # 已标记过且无新信息，保持首次原因
        if self.state == SessionState.FAILED:
            _logger.info("session already FAILED, updating reason: msg=%s reason=%s", self.message_id[:12], reason)
        elif reason:
            _logger.info("session marked FAILED: msg=%s state=%s reason=%s", self.message_id[:12], self.state, reason)
        else:
            _logger.info("session marked FAILED: msg=%s state=%s (no reason given)", self.message_id[:12], self.state)
        self.state = SessionState.FAILED

    def active_segments(self) -> list[Segment]:
        if self.segment_state is None:
            return []
        return self.segment_state.segments[self.split_index:]
