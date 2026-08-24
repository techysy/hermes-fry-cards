"""token 被飞书侧吊销（99991663）时的运行时恢复测试."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from hermes_fry_cards.feishu import (
    FEISHU_TOKEN_INVALID_CODE,
    FeishuAPIError,
    FeishuClient,
    FeishuClientConfig,
)


class _FakeCache:
    """模仿 lark_oapi LocalCache 的最小 dict 接口."""

    def __init__(self) -> None:
        self.cache: dict[str, Any] = {}


def _client() -> FeishuClient:
    return FeishuClient(
        FeishuClientConfig(app_id="cli_test123", app_secret="sec", base_url="https://open.feishu.cn")
    )


def test_invalidates_sdk_cache_on_99991663(monkeypatch: pytest.MonkeyPatch) -> None:
    """遇到 99991663 时清 SDK token 缓存并重试一次成功."""
    from lark_oapi.core.token import manager as token_manager_mod

    fake = _FakeCache()
    fake.cache["self_tenant_token:cli_test123"] = "stale-token"
    monkeypatch.setattr(token_manager_mod.TokenManager, "cache", fake)

    client = _client()
    calls = {"n": 0}

    async def flaky_call() -> MagicMock:
        calls["n"] += 1
        if calls["n"] == 1:
            resp = MagicMock()
            resp.success.return_value = False
            resp.code = FEISHU_TOKEN_INVALID_CODE
            resp.msg = "Invalid access token for authorization."
            return resp
        ok = MagicMock()
        ok.success.return_value = True
        return ok

    import asyncio

    result = asyncio.run(client._checked_call("cardkit_create", flaky_call))
    assert result.success() is True
    assert calls["n"] == 2, "should retry exactly once after cache invalidation"
    assert "self_tenant_token:cli_test123" not in fake.cache, "stale token must be evicted"


def test_non_token_error_not_retried_via_invalidation(monkeypatch: pytest.MonkeyPatch) -> None:
    """其他错误码不走 token 恢复路径."""
    from lark_oapi.core.token import manager as token_manager_mod

    fake = _FakeCache()
    fake.cache["self_tenant_token:cli_test123"] = "token"
    monkeypatch.setattr(token_manager_mod.TokenManager, "cache", fake)

    client = _client()

    async def failing_call() -> MagicMock:
        resp = MagicMock()
        resp.success.return_value = False
        resp.code = 230001
        resp.msg = "some other error"
        return resp

    import asyncio

    with pytest.raises(FeishuAPIError):
        asyncio.run(client._checked_call("cardkit_create", failing_call))
    assert fake.cache.get("self_tenant_token:cli_test123") == "token", "cache untouched"
