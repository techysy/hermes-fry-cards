"""Feishu clarify button cards — runtime patch for the Hermes Feishu adapter.

Hermes's Feishu adapter ships no ``send_clarify`` override, so option
prompts degrade to a plain numbered text list (Telegram/Discord render
native buttons).  This module implements the interactive-card path and
monkey-patches it onto ``FeishuAdapter`` when the adapter module is
imported (injected by ``ClarifyPatcher`` at the end of adapter.py).

Capabilities:
  * single-select: one button per choice + "✏️ 其他" (flips into the
    gateway's text-capture so the next typed message becomes the answer)
  * multi-select: toggle buttons with an in-place checked state + submit
  * resolved clicks seal the card green with the chosen answer (sync
    callback response — renders on every client, same mechanism as the
    approval cards)
  * authorization via the adapter's own interactive-operator gate, chat
    mismatch protection, expired-entry cleanup, bounded state (FIFO cap)

The patch is idempotent and never raises into the adapter import path.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

_logger = logging.getLogger("hermes_fry_cards")

# Global (per-process) clarify card state. clarify_id is a uuid, so a
# module-level dict is collision-free and survives adapter restarts.
_CLARIFY_STATE: Dict[str, Dict[str, Any]] = {}
_CLARIFY_SELECTIONS: Dict[str, List[int]] = {}
_STATE_CAP = 100


# ---------------------------------------------------------------------------
# Card builders
# ---------------------------------------------------------------------------

def flatten_choice(choice: Any) -> str:
    """Unwrap a clarify choice to display text (LLMs emit str or dict)."""
    if choice is None:
        return ""
    if isinstance(choice, str):
        return choice.strip()
    if isinstance(choice, dict):
        for key in ("label", "description", "text", "title"):
            val = choice.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return ""
    return str(choice).strip()


def build_clarify_card(
    *,
    question: str,
    choices: List[str],
    clarify_id: str,
    multi_select: bool,
    selected: Optional[List[int]] = None,
    show_input: bool = False,
) -> Dict[str, Any]:
    """Build the interactive clarify card JSON (blue header, button rows).

    ``show_input`` flips the card into custom-answer mode: an inline input
    box + submit button (Feishu form container) replaces the "其他" button.
    """
    selected_set = set(selected or [])
    if multi_select:
        option_lines = "\n".join(
            f"{i + 1}. {'☑' if i in selected_set else '⬜'} {c}"
            for i, c in enumerate(choices)
        )
    else:
        option_lines = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(choices))
    body = f"**❓ {question}**"
    if option_lines:
        body += f"\n\n{option_lines}"
    if show_input:
        body += (
            "\n\n<font color='green'>✏️ **已切换自定义输入**：在下方输入框写下你的回答并点"
            "「📤 提交自定义回答」；也可以直接点上面的选项按钮，或在聊天里发消息。</font>"
        )
    elif multi_select:
        hint = "可多选，点选项切换勾选，再点「提交选择」"
        if selected_set:
            hint += f"（已选 {len(selected_set)} 项）"
        body += f"\n\n<font color='grey'>{hint}</font>"
    else:
        body += "\n\n<font color='grey'>点击选项按钮作答，或点「✏️ 其他」后直接回复文字</font>"

    def _btn(label: str, value: Dict[str, Any], btn_type: str = "default") -> dict:
        return {
            "tag": "button",
            "text": {"tag": "plain_text", "content": label},
            "type": btn_type,
            "value": value,
        }

    actions: List[dict] = []
    for idx in range(len(choices)):
        if multi_select:
            actions.append(_btn(
                f"{'☑' if idx in selected_set else '⬜'} {idx + 1}",
                {"hermes_clarify_action": "toggle", "clarify_id": clarify_id, "idx": idx},
                "primary" if idx in selected_set else "default",
            ))
        else:
            actions.append(_btn(
                str(idx + 1),
                {"hermes_clarify_action": "choose", "clarify_id": clarify_id, "idx": idx},
                "primary",
            ))
    if multi_select and selected_set:
        actions.append(_btn(
            f"提交选择({len(selected_set)})",
            {"hermes_clarify_action": "submit", "clarify_id": clarify_id},
            "primary",
        ))
    if not show_input:
        actions.append(_btn(
            "✏️ 其他",
            {"hermes_clarify_action": "other", "clarify_id": clarify_id},
        ))
    elements: List[dict] = [{"tag": "markdown", "content": body}]
    for i in range(0, len(actions), 5):
        elements.append({"tag": "action", "actions": actions[i:i + 5]})
    if show_input:
        elements.append({
            "tag": "form",
            "name": f"hermes_clarify_form_{clarify_id}",
            "elements": [
                {
                    "tag": "input",
                    "name": "hermes_clarify_input",
                    "input_type": "multiline_text",
                    "rows": 2,
                    "auto_resize": True,
                    "max_rows": 5,
                    "required": True,
                    "placeholder": {
                        "tag": "plain_text",
                        "content": "输入你的自定义回答…",
                    },
                    "fallback": {
                        "tag": "fallback_text",
                        "text": {
                            "tag": "plain_text",
                            "content": "当前客户端版本过低，请升级后使用输入框；可直接在聊天中回复文字。",
                        },
                    },
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "📤 提交自定义回答"},
                    "type": "primary",
                    "action_type": "form_submit",
                    "complex_interaction": True,
                    "name": "hermes_clarify_submit",
                    "value": {"hermes_clarify_action": "text_submit", "clarify_id": clarify_id},
                },
            ],
        })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"content": "❓ 需要你选择", "tag": "plain_text"},
            "template": "blue",
        },
        "elements": elements,
    }


def build_resolved_clarify_card(
    *, question: str, response_text: str, user_name: str
) -> Dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"content": "✅ 已收到你的选择", "tag": "plain_text"},
            "template": "green",
        },
        "elements": [
            {
                "tag": "markdown",
                "content": f"**❓ {question}**\n\n**{user_name}** 选择了：**{response_text}**",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Patched methods (plain functions; ``self`` is the FeishuAdapter instance)
# ---------------------------------------------------------------------------

async def send_clarify(
    self: Any,
    chat_id: str,
    question: str,
    choices: Optional[list],
    clarify_id: str,
    session_key: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Any:
    """Render a clarify prompt as an interactive card with buttons."""
    SendResult = self._hl_SendResult
    if not self._client:
        return SendResult(success=False, error="Not connected")

    flat = [c for c in (flatten_choice(x) for x in (choices or [])) if c]
    if not flat:
        # Open-ended prompt: plain text, gateway text-intercept captures reply.
        return await self.send(
            chat_id=chat_id, content=f"❓ {question}", reply_to=None, metadata=metadata
        )

    is_multi = False
    try:
        from tools import clarify_gateway as _cg
        with _cg._lock:
            _entry = _cg._entries.get(clarify_id)
        is_multi = bool(_entry and getattr(_entry, "multi_select", False))
    except Exception:
        _logger.debug("[fry-cards] multi_select lookup failed for %s", clarify_id, exc_info=True)

    try:
        card = build_clarify_card(
            question=str(question or ""),
            choices=flat,
            clarify_id=clarify_id,
            multi_select=is_multi,
        )
        response = await self._feishu_send_with_retry(
            chat_id=chat_id,
            msg_type="interactive",
            payload=json.dumps(card, ensure_ascii=False),
            reply_to=None,
            metadata=metadata,
        )
        result = self._finalize_send_result(response, "send_clarify failed")
        if result.success:
            while len(_CLARIFY_STATE) >= _STATE_CAP:
                _old_id = next(iter(_CLARIFY_STATE))
                _CLARIFY_STATE.pop(_old_id, None)
                _CLARIFY_SELECTIONS.pop(_old_id, None)
            _CLARIFY_STATE[clarify_id] = {
                "session_key": session_key,
                "chat_id": chat_id,
                "message_id": result.message_id or "",
                "question": str(question or ""),
                "choices": flat,
                "multi_select": is_multi,
            }
        return result
    except Exception as exc:
        _logger.warning("[fry-cards] send_clarify failed: %s", exc)
        return SendResult(success=False, error=str(exc))


def _empty_trigger_response(self: Any) -> Any:
    P2 = self._hl_P2CardActionTriggerResponse
    return P2() if P2 else None


def _callback_card(
    self: Any,
    card_data: Dict[str, Any],
    *,
    toast_type: str = "",
    toast_content: str = "",
) -> Any:
    """Wrap rebuilt card JSON into a sync callback response.

    The in-place card update renders on every client; ``toast`` additionally
    shows a transient popup for the clicking user (immediate click feedback).
    """
    response = _empty_trigger_response(self)
    CallBackCard = self._hl_CallBackCard
    if response is not None and CallBackCard is not None:
        card = CallBackCard()
        card.type = "raw"
        card.data = card_data
        response.card = card
    if toast_content and response is not None:
        CallBackToast = getattr(self, "_hl_CallBackToast", None)
        if CallBackToast is not None:
            toast = CallBackToast()
            toast.type = toast_type or "info"
            toast.content = toast_content
            response.toast = toast
    return response


def handle_clarify_card_action(
    self: Any, *, event: Any, action_value: Dict[str, Any], loop: Any
) -> Any:
    """Handle a clarify button click: resolve/toggle, return in-place card update."""
    from tools import clarify_gateway as _cg

    clarify_id = str(action_value.get("clarify_id") or "")
    state = _CLARIFY_STATE.get(clarify_id)
    if not state:
        _logger.debug("[fry-cards] Clarify %s already resolved or unknown", clarify_id)
        return _empty_trigger_response(self)

    operator = getattr(event, "operator", None)
    open_id = str(getattr(operator, "open_id", "") or "")
    # Same gate as update-prompt buttons: interactive callbacks authorize
    # against admins/allowlist, independent of group-message admission
    # policy (which rejects when no rule matches the chat).
    if not self._is_interactive_operator_authorized(open_id):
        _logger.warning("[fry-cards] Unauthorized clarify click by %s", open_id or "<unknown>")
        return _empty_trigger_response(self)
    callback_chat_id = str(getattr(getattr(event, "context", None), "open_chat_id", "") or "")
    expected_chat_id = str(state.get("chat_id", "") or "")
    if callback_chat_id and expected_chat_id and callback_chat_id != expected_chat_id:
        _logger.warning("[fry-cards] Clarify callback chat mismatch for %s", clarify_id)
        return _empty_trigger_response(self)

    user_name = self._get_cached_sender_name(open_id) or open_id
    action = str(action_value.get("hermes_clarify_action") or "")
    choices: List[str] = state.get("choices") or []
    question = str(state.get("question") or "")

    if action == "other":
        flipped = False
        try:
            flipped = _cg.mark_awaiting_text(clarify_id)
        except Exception:
            _logger.warning("[fry-cards] mark_awaiting_text failed for %s", clarify_id, exc_info=True)
        if not flipped:
            _CLARIFY_STATE.pop(clarify_id, None)
            _CLARIFY_SELECTIONS.pop(clarify_id, None)
            return _empty_trigger_response(self)
        # Visible click feedback: toast popup + card flips into inline
        # input mode (form container). Text-typing in chat still works.
        card = build_clarify_card(
            question=question, choices=choices, clarify_id=clarify_id,
            multi_select=bool(state.get("multi_select")),
            selected=_CLARIFY_SELECTIONS.get(clarify_id),
            show_input=True,
        )
        return _callback_card(
            self, card,
            toast_type="info",
            toast_content="已切换自定义输入：在卡片输入框填写，或直接在聊天里回复",
        )

    if action == "text_submit":
        text = ""
        try:
            form_value = getattr(getattr(event, "action", None), "form_value", None) or {}
            raw = form_value.get("hermes_clarify_input")
            if isinstance(raw, str):
                text = raw.strip()
        except Exception:
            _logger.debug("[fry-cards] text_submit form_value parse failed", exc_info=True)
        if not text:
            return _callback_card(
                self,
                build_clarify_card(
                    question=question, choices=choices, clarify_id=clarify_id,
                    multi_select=bool(state.get("multi_select")),
                    selected=_CLARIFY_SELECTIONS.get(clarify_id),
                    show_input=True,
                ),
                toast_type="warning",
                toast_content="请先在输入框里写点什么再提交",
            )
        try:
            resolved = _cg.resolve_gateway_clarify(clarify_id, text)
        except Exception:
            _logger.warning("[fry-cards] resolve failed for text_submit %s", clarify_id, exc_info=True)
            resolved = False
        _CLARIFY_STATE.pop(clarify_id, None)
        _CLARIFY_SELECTIONS.pop(clarify_id, None)
        if not resolved:
            return _empty_trigger_response(self)
        try:
            self.resume_typing_for_chat(expected_chat_id or callback_chat_id)
        except Exception:
            _logger.debug("[fry-cards] resume_typing after text_submit failed", exc_info=True)
        _logger.info(
            "clarify custom text resolved (id=%s, text=%r, user=%s)",
            clarify_id, text[:60], user_name,
        )
        return _callback_card(
            self,
            build_resolved_clarify_card(question=question, response_text=text, user_name=user_name),
            toast_type="success",
            toast_content="已提交你的自定义回答",
        )

    if action == "toggle" and state.get("multi_select"):
        try:
            idx = int(action_value.get("idx"))
        except (TypeError, ValueError):
            idx = -1
        if not (0 <= idx < len(choices)):
            return _empty_trigger_response(self)
        sel = _CLARIFY_SELECTIONS.setdefault(clarify_id, [])
        if idx in sel:
            sel.remove(idx)
        else:
            sel.append(idx)
            if len(sel) > 4:
                sel.pop(0)  # choices are capped at 4 by the tool schema
        return _callback_card(self, build_clarify_card(
            question=question, choices=choices, clarify_id=clarify_id,
            multi_select=True, selected=sel,
        ))

    # Single-select "choose" or multi-select "submit" → resolve the wait.
    resolved_text: Optional[str] = None
    if action == "choose":
        try:
            idx = int(action_value.get("idx"))
        except (TypeError, ValueError):
            idx = -1
        resolved_text = choices[idx] if 0 <= idx < len(choices) else f"choice {idx + 1}"
    elif action == "submit" and state.get("multi_select"):
        sel = sorted(_CLARIFY_SELECTIONS.get(clarify_id) or [])
        picked = [choices[i] for i in sel if 0 <= i < len(choices)]
        if not picked:
            return _empty_trigger_response(self)
        resolved_text = ", ".join(picked)
    if resolved_text is None:
        return _empty_trigger_response(self)

    try:
        resolved = _cg.resolve_gateway_clarify(clarify_id, resolved_text)
    except Exception:
        _logger.warning("[fry-cards] resolve_gateway_clarify failed for %s", clarify_id, exc_info=True)
        resolved = False
    _CLARIFY_STATE.pop(clarify_id, None)
    _CLARIFY_SELECTIONS.pop(clarify_id, None)
    if not resolved:
        _logger.info("[fry-cards] Clarify %s already expired/timed out; click ignored", clarify_id)
        return _empty_trigger_response(self)

    # Unblock the agent thread's typing pause (mirrors the gateway's text
    # intercept path, which resumes typing after resolving).
    try:
        self.resume_typing_for_chat(expected_chat_id or callback_chat_id)
    except Exception:
        _logger.debug("[fry-cards] resume_typing after clarify failed", exc_info=True)

    _logger.info(
        "clarify button resolved (id=%s, choice=%r, user=%s)",
        clarify_id, resolved_text[:60], user_name,
    )
    return _callback_card(self, build_resolved_clarify_card(
        question=question, response_text=resolved_text, user_name=user_name,
    ))


def _on_card_action_trigger_patched(self: Any, data: Any) -> Any:
    """Wrap the adapter's card-action router; intercept clarify buttons."""
    try:
        event = getattr(data, "event", None)
        action = getattr(event, "action", None)
        action_value = getattr(action, "value", {}) or {}
        if (
            isinstance(action_value, dict)
            and action_value.get("hermes_clarify_action")
            and action_value.get("clarify_id") in _CLARIFY_STATE
        ):
            return self._hl_handle_clarify(
                event=event, action_value=action_value, loop=self._loop
            )
    except Exception:
        _logger.warning("[fry-cards] clarify routing failed; falling through", exc_info=True)
    return self._hl_orig_card_action_trigger(data)


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------

def apply_patch(
    cls: Any,
    *,
    SendResult: Any,
    CallBackCard: Any = None,
    P2CardActionTriggerResponse: Any = None,
) -> bool:
    """Monkey-patch clarify card support onto FeishuAdapter.

    Called from the marker block injected at the end of adapter.py with the
    class and its module-level SDK names (resolved at import time, no
    sys.modules assumptions).  Idempotent; returns True when applied.
    """
    if cls is None or getattr(cls, "_hl_clarify_patched", False):
        return cls is not None and bool(getattr(cls, "_hl_clarify_patched", False))

    cls._hl_clarify_patched = True
    cls._hl_SendResult = SendResult
    cls._hl_CallBackCard = CallBackCard
    cls._hl_P2CardActionTriggerResponse = P2CardActionTriggerResponse
    try:  # toast needs its own class; absent on older SDKs (degrades silently)
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            CallBackToast as _Toast,
        )
    except Exception:
        _Toast = None
    cls._hl_CallBackToast = _Toast
    cls._hl_orig_card_action_trigger = cls._on_card_action_trigger
    cls.send_clarify = send_clarify
    cls._hl_handle_clarify = handle_clarify_card_action
    cls._on_card_action_trigger = _on_card_action_trigger_patched
    _logger.info("[fry-cards] clarify button patch applied to FeishuAdapter")
    return True
