"""Feishu client transient-error behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hermes_fry_cards.feishu import FeishuAPIError, FeishuClient, is_content_overflow_error


class _Resp:
    def __init__(self, *, ok: bool, code: int = 0, msg: str = "", data: object | None = None) -> None:
        self._ok = ok
        self.code = code
        self.msg = msg
        self.data = data

    def success(self) -> bool:
        return self._ok


def _client_with(**methods: AsyncMock) -> FeishuClient:
    client = FeishuClient.__new__(FeishuClient)
    client._client = SimpleNamespace(  # type: ignore[attr-defined]
        cardkit=SimpleNamespace(
            v1=SimpleNamespace(
                card=SimpleNamespace(
                    acreate=methods.get("card_create", AsyncMock()),
                    aupdate=methods.get("card_update", AsyncMock()),
                    abatch_update=methods.get("batch_update", AsyncMock()),
                    asettings=methods.get("settings", AsyncMock()),
                ),
                card_element=SimpleNamespace(content=methods.get("card_element_content", AsyncMock())),
            ),
        ),
        im=SimpleNamespace(
            v1=SimpleNamespace(
                message=SimpleNamespace(
                    acreate=methods.get("create_message", AsyncMock()),
                    areply=methods.get("reply", AsyncMock()),
                ),
            ),
        ),
    )
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "message"),
    [
        (2200, "Gateway timeout. Please try again later."),
        (300000, "Server Internal Error"),
    ],
    ids=["gateway-timeout", "server-internal-error"],
)
async def test_cardkit_create_retries_transient_errors_once(code: int, message: str) -> None:
    create = AsyncMock(
        side_effect=[
            _Resp(ok=False, code=code, msg=message),
            _Resp(ok=True, data=SimpleNamespace(card_id="card-ok")),
        ]
    )
    client = _client_with(card_create=create)

    assert await client.cardkit_create({"schema": "2.0"}) == "card-ok"
    assert create.await_count == 2


@pytest.mark.asyncio
async def test_cardkit_batch_update_retries_internal_error() -> None:
    batch_update = AsyncMock(
        side_effect=[
            _Resp(ok=False, code=1663, msg="internal error"),
            _Resp(ok=True),
        ]
    )
    client = _client_with(batch_update=batch_update)

    await client.cardkit_batch_update("card", [{"action": "add"}], sequence=7)

    assert batch_update.await_count == 2
    first_request = batch_update.await_args_list[0].args[0]
    second_request = batch_update.await_args_list[1].args[0]
    assert second_request.card_id == first_request.card_id
    assert second_request.request_body.sequence == first_request.request_body.sequence


@pytest.mark.asyncio
async def test_reply_card_by_id_retries_gateway_timeout_once() -> None:
    reply = AsyncMock(
        side_effect=[
            _Resp(ok=False, code=2200, msg="Gateway timeout. Please try again later."),
            _Resp(ok=True, data=SimpleNamespace(message_id="msg-ok")),
        ]
    )
    client = _client_with(reply=reply)

    assert await client.reply_card_by_id("anchor", "card") == "msg-ok"
    assert reply.await_count == 2
    first_request = reply.await_args_list[0].args[0]
    second_request = reply.await_args_list[1].args[0]
    assert first_request.request_body.uuid
    assert second_request.request_body.uuid == first_request.request_body.uuid


@pytest.mark.asyncio
async def test_send_card_to_chat_reuses_uuid_across_retries() -> None:
    create = AsyncMock(
        side_effect=[
            _Resp(ok=False, code=2200, msg="Gateway timeout. Please try again later."),
            _Resp(ok=True, data=SimpleNamespace(message_id="msg-ok")),
        ]
    )
    client = _client_with(create_message=create)

    assert await client.send_card_to_chat("chat", {"schema": "2.0"}) == "msg-ok"
    assert create.await_count == 2
    first_request = create.await_args_list[0].args[0]
    second_request = create.await_args_list[1].args[0]
    assert first_request.request_body.uuid
    assert second_request.request_body.uuid == first_request.request_body.uuid


@pytest.mark.asyncio
async def test_send_text_to_chat_uses_chat_id_and_text_message() -> None:
    create = AsyncMock(
        return_value=_Resp(ok=True, data=SimpleNamespace(message_id="msg-text"))
    )
    client = _client_with(create_message=create)

    assert await client.send_text_to_chat("oc_test", "done") == "msg-text"

    request = create.await_args.args[0]
    assert request.receive_id_type == "chat_id"
    assert request.request_body.receive_id == "oc_test"
    assert request.request_body.msg_type == "text"
    assert request.request_body.content == '{"text": "done"}'


@pytest.mark.asyncio
async def test_send_text_to_chat_can_reply_to_card_message() -> None:
    reply = AsyncMock(
        return_value=_Resp(ok=True, data=SimpleNamespace(message_id="msg-reply"))
    )
    client = _client_with(reply=reply)

    assert await client.send_text_to_chat(
        "oc_test", "done", reply_to_message_id="om_card"
    ) == "msg-reply"

    request = reply.await_args.args[0]
    assert request.message_id == "om_card"
    assert request.request_body.msg_type == "text"
    assert request.request_body.content == '{"text": "done"}'


@pytest.mark.asyncio
async def test_cardkit_create_does_not_retry_non_transient_error() -> None:
    create = AsyncMock(side_effect=[_Resp(ok=False, code=230099, msg="content failed")])
    client = _client_with(card_create=create)

    with pytest.raises(FeishuAPIError):
        await client.cardkit_create({"schema": "2.0"})

    assert create.await_count == 1


def test_is_content_overflow_error() -> None:
    # 顶层错误码命中
    assert is_content_overflow_error(FeishuAPIError("element exceeds the limit", code=300305))
    assert is_content_overflow_error(FeishuAPIError("card over max size", code=200860))
    assert is_content_overflow_error(FeishuAPIError("content failed", code=230099))
    # msg 内嵌子码命中（ErrCode: 11310）
    assert is_content_overflow_error(
        FeishuAPIError("Failed to create card content, ext=ErrCode: 11310; detail", code=230099)
    )
    # 非内容错误不命中
    assert not is_content_overflow_error(FeishuAPIError("rate limited", code=230020))
    assert not is_content_overflow_error(FeishuAPIError("sequence number compare failed", code=300317))
