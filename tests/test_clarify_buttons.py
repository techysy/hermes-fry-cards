"""Tests for the Feishu clarify button card module (hermes_fry_cards.clarify)."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from hermes_fry_cards import clarify
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    CallBackCard,
    CallBackToast,
    P2CardActionTriggerResponse,
)
from tools import clarify_gateway as cg


@pytest.fixture(autouse=True)
def _clean_state():
    clarify._CLARIFY_STATE.clear()
    clarify._CLARIFY_SELECTIONS.clear()
    yield
    clarify._CLARIFY_STATE.clear()
    clarify._CLARIFY_SELECTIONS.clear()


class FakeAdapter:
    """Minimal stand-in carrying only what the patched handlers touch."""

    _hl_P2CardActionTriggerResponse = P2CardActionTriggerResponse
    _hl_CallBackCard = CallBackCard
    _hl_CallBackToast = CallBackToast
    _hl_SendResult = None
    # plain function on the class → instance method; ``self`` binds to FakeAdapter
    _hl_handle_clarify = clarify.handle_clarify_card_action

    def __init__(self):
        self.resumed: list[str] = []

    def _is_interactive_operator_authorized(self, open_id: str) -> bool:
        return True

    def _get_cached_sender_name(self, open_id: str) -> str:
        return "tester"

    def resume_typing_for_chat(self, chat_id: str) -> None:
        self.resumed.append(chat_id)


def _event(chat_id: str = "oc_test") -> SimpleNamespace:
    return SimpleNamespace(
        operator=SimpleNamespace(open_id="ou_boss", user_id=""),
        context=SimpleNamespace(open_chat_id=chat_id),
    )


def _register(clarify_id: str, choices: list[str], *, multi: bool = False):
    cg.register(
        clarify_id=clarify_id,
        session_key=f"sk-{clarify_id}",
        question="Q?",
        choices=choices,
        multi_select=multi,
    )
    clarify._CLARIFY_STATE[clarify_id] = {
        "session_key": f"sk-{clarify_id}",
        "chat_id": "oc_test",
        "message_id": "",
        "question": "Q?",
        "choices": choices,
        "multi_select": multi,
    }


def _wait_in_thread(clarify_id: str, box: dict) -> threading.Thread:
    t = threading.Thread(
        target=lambda: box.setdefault("r", cg.wait_for_response(clarify_id, timeout=8))
    )
    t.start()
    time.sleep(0.15)
    return t


# ---------------------------------------------------------------------------
# Card builders
# ---------------------------------------------------------------------------

class TestCardBuilders:
    def test_single_select_buttons(self):
        card = clarify.build_clarify_card(
            question="选", choices=["A", "B"], clarify_id="c1", multi_select=False
        )
        buttons = [
            b for e in card["elements"] if e.get("tag") == "action" for b in e["actions"]
        ]
        # 2 choose + 1 other
        assert [b["value"]["hermes_clarify_action"] for b in buttons] == [
            "choose",
            "choose",
            "other",
        ]
        assert card["header"]["template"] == "blue"

    def test_multi_select_toggle_and_submit(self):
        card = clarify.build_clarify_card(
            question="选",
            choices=["A", "B", "C"],
            clarify_id="c2",
            multi_select=True,
            selected=[1],
        )
        buttons = [
            b for e in card["elements"] if e.get("tag") == "action" for b in e["actions"]
        ]
        acts = [b["value"]["hermes_clarify_action"] for b in buttons]
        assert acts == ["toggle", "toggle", "toggle", "submit", "other"]
        # selected option highlighted
        assert buttons[1]["type"] == "primary"
        assert buttons[0]["type"] == "default"

    def test_flatten_choice_variants(self):
        assert clarify.flatten_choice(" plain ") == "plain"
        assert clarify.flatten_choice({"label": "L"}) == "L"
        assert clarify.flatten_choice({"description": "D"}) == "D"
        assert clarify.flatten_choice(None) == ""
        assert clarify.flatten_choice({"unrelated": 1}) == ""

    def test_resolved_card_green(self):
        card = clarify.build_resolved_clarify_card(
            question="Q", response_text="A", user_name="U"
        )
        assert card["header"]["template"] == "green"
        assert "A" in card["elements"][0]["content"]


# ---------------------------------------------------------------------------
# Click handling (end-to-end through the clarify_gateway wait)
# ---------------------------------------------------------------------------

class TestClickHandling:
    def test_single_choose_resolves_agent_wait(self):
        inst = FakeAdapter()
        _register("t-single", ["甲", "乙", "丙"])
        box: dict = {}
        t = _wait_in_thread("t-single", box)

        inst._hl_handle_clarify(
            event=_event(),
            action_value={"hermes_clarify_action": "choose", "clarify_id": "t-single", "idx": 2},
            loop=None,
        )
        t.join(timeout=10)
        assert box.get("r") == "丙"
        assert "t-single" not in clarify._CLARIFY_STATE
        assert inst.resumed == ["oc_test"]

    def test_multi_toggle_submit(self):
        inst = FakeAdapter()
        _register("t-multi", ["X", "Y", "Z"], multi=True)
        box: dict = {}
        t = _wait_in_thread("t-multi", box)

        inst._hl_handle_clarify(
            event=_event(),
            action_value={"hermes_clarify_action": "toggle", "clarify_id": "t-multi", "idx": 0},
            loop=None,
        )
        assert t.is_alive(), "toggle must not resolve the wait"
        inst._hl_handle_clarify(
            event=_event(),
            action_value={"hermes_clarify_action": "toggle", "clarify_id": "t-multi", "idx": 2},
            loop=None,
        )
        inst._hl_handle_clarify(
            event=_event(),
            action_value={"hermes_clarify_action": "submit", "clarify_id": "t-multi"},
            loop=None,
        )
        t.join(timeout=10)
        assert box.get("r") == "X, Z"

    def test_other_flips_to_text_capture(self):
        inst = FakeAdapter()

        entry = cg.register(
            clarify_id="t-other", session_key="sk-t-other", question="Q", choices=["P", "Q2"]
        )
        clarify._CLARIFY_STATE["t-other"] = {
            "session_key": "sk-t-other",
            "chat_id": "oc_test",
            "message_id": "",
            "question": "Q",
            "choices": ["P", "Q2"],
            "multi_select": False,
        }
        resp = inst._hl_handle_clarify(
            event=_event(),
            action_value={"hermes_clarify_action": "other", "clarify_id": "t-other"},
            loop=None,
        )
        assert entry.awaiting_text is True
        # state kept so the text-intercept path still works
        assert "t-other" in clarify._CLARIFY_STATE
        # click feedback: toast popup + card flipped to inline-input mode
        assert resp is not None
        assert resp.toast is not None and "自定义输入" in resp.toast.content
        assert resp.card is not None
        form = [e for e in resp.card.data["elements"] if e.get("tag") == "form"]
        assert form and any(
            e.get("tag") == "input" for e in form[0]["elements"]
        ), "输入态卡片必须内嵌输入框"
        ok = cg.resolve_text_response_for_session("sk-t-other", "freeform answer")
        assert ok
        assert cg.wait_for_response("t-other", timeout=2) == "freeform answer"

    def test_text_submit_resolves_with_input_value(self):
        inst = FakeAdapter()
        _register("t-text", ["A", "B"])
        box: dict = {}
        t = _wait_in_thread("t-text", box)
        event = _event()
        event.action = SimpleNamespace(
            form_value={"hermes_clarify_input": "  我的自定义答案  "}
        )
        resp = inst._hl_handle_clarify(
            event=event,
            action_value={"hermes_clarify_action": "text_submit", "clarify_id": "t-text"},
            loop=None,
        )
        t.join(timeout=10)
        assert box.get("r") == "我的自定义答案"  # stripped
        assert "t-text" not in clarify._CLARIFY_STATE
        assert resp is not None and resp.toast is not None
        assert resp.toast.type == "success"
        assert resp.card is not None
        assert resp.card.data["header"]["template"] == "green"

    def test_text_submit_empty_warns(self):
        inst = FakeAdapter()
        _register("t-empty", ["A"])
        event = _event()
        event.action = SimpleNamespace(form_value={"hermes_clarify_input": "   "})
        resp = inst._hl_handle_clarify(
            event=event,
            action_value={"hermes_clarify_action": "text_submit", "clarify_id": "t-empty"},
            loop=None,
        )
        assert resp is not None and resp.toast is not None
        assert resp.toast.type == "warning"
        # still waiting — not resolved
        assert "t-empty" in clarify._CLARIFY_STATE
        cg.resolve_gateway_clarify("t-empty", "cleanup")

    def test_unknown_clarify_id_is_noop(self):
        inst = FakeAdapter()

        result = inst._hl_handle_clarify(
            event=_event(),
            action_value={"hermes_clarify_action": "choose", "clarify_id": "ghost", "idx": 0},
            loop=None,
        )
        assert result is not None  # empty response (no card, no toast)
        assert getattr(result, "card", None) is None
        assert getattr(result, "toast", None) is None

    def test_chat_mismatch_rejected(self):
        inst = FakeAdapter()

        _register("t-chat", ["A"])
        box: dict = {}
        t = _wait_in_thread("t-chat", box)
        inst._hl_handle_clarify(
            event=_event("oc_other_chat"),
            action_value={"hermes_clarify_action": "choose", "clarify_id": "t-chat", "idx": 0},
            loop=None,
        )
        assert t.is_alive(), "mismatched chat must not resolve"
        assert "t-chat" in clarify._CLARIFY_STATE
        # cleanup the live entry
        cg.resolve_gateway_clarify("t-chat", "cleanup")
        t.join(timeout=5)

    def test_unauthorized_operator_rejected(self):
        inst = FakeAdapter()
        inst._is_interactive_operator_authorized = lambda oid: False

        _register("t-auth", ["A"])
        inst._hl_handle_clarify(
            event=_event(),
            action_value={"hermes_clarify_action": "choose", "clarify_id": "t-auth", "idx": 0},
            loop=None,
        )
        assert "t-auth" in clarify._CLARIFY_STATE  # untouched

    def test_expired_entry_click_cleans_state(self):
        inst = FakeAdapter()

        # state exists but gateway entry was never registered (expired)
        clarify._CLARIFY_STATE["t-expired"] = {
            "session_key": "sk",
            "chat_id": "oc_test",
            "message_id": "",
            "question": "Q",
            "choices": ["A"],
            "multi_select": False,
        }
        inst._hl_handle_clarify(
            event=_event(),
            action_value={"hermes_clarify_action": "choose", "clarify_id": "t-expired", "idx": 0},
            loop=None,
        )
        assert "t-expired" not in clarify._CLARIFY_STATE


# ---------------------------------------------------------------------------
# apply_patch
# ---------------------------------------------------------------------------

class TestApplyPatch:
    def test_idempotent_and_wires_methods(self):
        class Target:
            @staticmethod
            def _on_card_action_trigger(data):  # pragma: no cover - sentinel
                return "orig"

        assert clarify.apply_patch(
            Target, SendResult=object, CallBackCard=None, P2CardActionTriggerResponse=None
        )
        first_router = Target._on_card_action_trigger
        # second call is a no-op
        assert clarify.apply_patch(Target, SendResult=object)
        assert Target._on_card_action_trigger is first_router
        assert Target.send_clarify is clarify.send_clarify
        assert Target._hl_handle_clarify is clarify.handle_clarify_card_action
        assert Target._hl_orig_card_action_trigger(None) == "orig"

    def test_none_class_returns_false(self):
        assert clarify.apply_patch(None, SendResult=object) is False


# ---------------------------------------------------------------------------
# Approval gate fix + toast feedback
# ---------------------------------------------------------------------------

class FakeApprovalAdapter:
    """Stand-in for the approval path: mirrors the real adapter's shape."""

    _hl_P2CardActionTriggerResponse = P2CardActionTriggerResponse
    _hl_CallBackCard = CallBackCard
    _hl_CallBackToast = CallBackToast
    _hl_handle_clarify = clarify.handle_clarify_card_action
    _handle_approval_card_action = clarify.handle_approval_card_action_patched
    _allow_group_message = clarify._allow_group_message_patched

    def __init__(self, *, group_gate_opens: bool, operator_ok: bool = True):
        self._approval_state: dict = {}
        self._group_gate_opens = group_gate_opens  # simulates _allow_group_message policy
        self._operator_ok = operator_ok
        self.submitted: list = []

    # the wrapped-original we delegate to, mimicking Hermes's real handler
    def _hl_orig_approval_handler(self, *, event, action_value, loop):
        sender = SimpleNamespace(open_id="ou_x", user_id="")
        if not self._allow_group_message_patched_gate(sender):
            return P2CardActionTriggerResponse()  # Hermes: silent reject
        resp = P2CardActionTriggerResponse()
        card = CallBackCard()
        card.type = "raw"
        card.data = {"header": {"template": "green"}}
        resp.card = card
        return resp

    def _allow_group_message_patched_gate(self, sender_id) -> bool:
        # simulate the patched gate inside the "original" handler
        if getattr(clarify._APPROVAL_BYPASS, "active", False):
            return True
        return self._group_gate_opens

    def _is_interactive_operator_authorized(self, open_id: str) -> bool:
        return self._operator_ok

    def _get_cached_sender_name(self, open_id: str) -> str:
        return "tester"


def _approval_event():
    return SimpleNamespace(
        operator=SimpleNamespace(open_id="ou_boss", user_id=""),
        context=SimpleNamespace(open_chat_id="oc_test"),
    )


class TestApprovalFeedback:
    def _register_approval(self, inst):
        inst._approval_state["ap1"] = {"chat_id": "oc_test", "session_key": "sk"}

    def test_click_resolves_despite_closed_group_gate(self):
        # Core bug scenario: group admission closed (no rules) but operator
        # is authorized → click must still work (bypass) and carry a toast.
        inst = FakeApprovalAdapter(group_gate_opens=False)
        self._register_approval(inst)
        resp = inst._handle_approval_card_action(
            event=_approval_event(),
            action_value={"hermes_action": "approve_once", "approval_id": "ap1"},
            loop=None,
        )
        assert resp.card is not None, "审批必须被原处理器受理（变绿卡）"
        assert resp.toast is not None and resp.toast.type == "success"
        assert "已允许本次执行" in resp.toast.content
        # bypass flag must not leak after the call
        assert not getattr(clarify._APPROVAL_BYPASS, "active", False)

    def test_stale_card_gets_expiry_toast(self):
        inst = FakeApprovalAdapter(group_gate_opens=True)
        resp = inst._handle_approval_card_action(
            event=_approval_event(),
            action_value={"hermes_action": "approve_once", "approval_id": "ghost"},
            loop=None,
        )
        assert resp.toast is not None and resp.toast.type == "warning"
        assert "过期" in resp.toast.content
        assert resp.card is None

    def test_unauthorized_operator_gets_error_toast(self):
        inst = FakeApprovalAdapter(group_gate_opens=False, operator_ok=False)
        self._register_approval(inst)
        resp = inst._handle_approval_card_action(
            event=_approval_event(),
            action_value={"hermes_action": "approve_once", "approval_id": "ap1"},
            loop=None,
        )
        assert resp.toast is not None and resp.toast.type == "error"
        assert "权限" in resp.toast.content
        # state untouched — nothing resolved
        assert "ap1" in inst._approval_state

    def test_gate_exception_fails_closed(self):
        # A raising gate must DENY, never silently grant the bypass.
        inst = FakeApprovalAdapter(group_gate_opens=False)
        self._register_approval(inst)

        def _boom(open_id: str) -> bool:
            raise RuntimeError("gate exploded")

        inst._is_interactive_operator_authorized = _boom
        resp = inst._handle_approval_card_action(
            event=_approval_event(),
            action_value={"hermes_action": "approve_once", "approval_id": "ap1"},
            loop=None,
        )
        assert resp.toast is not None and resp.toast.type == "error"
        # nothing resolved, and the bypass never latched
        assert "ap1" in inst._approval_state
        assert not getattr(clarify._APPROVAL_BYPASS, "active", False)

    def test_deny_choice_gets_warning_toast(self):
        inst = FakeApprovalAdapter(group_gate_opens=True)
        self._register_approval(inst)
        resp = inst._handle_approval_card_action(
            event=_approval_event(),
            action_value={"hermes_action": "deny", "approval_id": "ap1"},
            loop=None,
        )
        assert resp.toast is not None and resp.toast.type == "warning"
        assert "拒绝" in resp.toast.content

    def test_allow_group_message_passthrough_when_inactive(self):
        # Outside an approval click, the patched gate must behave exactly
        # like the original (no accidental open door).
        calls = []

        class Stub:
            _hl_orig_allow_group_message = staticmethod(
                lambda sender_id, chat_id="", *, is_bot=False: calls.append(1) or False
            )
            _allow_group_message = clarify._allow_group_message_patched

        assert Stub()._allow_group_message(SimpleNamespace(open_id="ou", user_id="")) is False
        assert len(calls) == 1
