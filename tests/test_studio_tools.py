"""Studio 工具箱（/api/tools）与完结卡摘要配置项 — 校验、凭据隔离、错误人话化.

真实 HTTP 全程 mock（patch ``studio.tools.chat_completion``），测试不打真端点。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_studio import _get_json, _post_ok, _req  # 复用同一套 loopback 夹具（仓内既有口径）

import hermes_fry_cards.studio.server as srv
from hermes_fry_cards import llm
from hermes_fry_cards.config import Config
from hermes_fry_cards.studio import tools

_SECRET = "sk-STUDIO-SECRET-LEAKME"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(srv, "hermes_home", lambda: tmp_path)
    conf = {
        "streaming": {"enabled": True},
        "model": {"base_url": "http://10r.local:20127/v1", "default": "Hermes"},
        "custom_providers": [
            {
                "api_key": _SECRET,
                "base_url": "http://10r.local:20127/v1",
                "models": {"cbcn/glm-5.3-flash": {}, "cbai/hy4-flash": {}},
            }
        ],
    }
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(conf, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return tmp_path


@pytest.fixture()
def server(home: Path):
    import threading
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.StudioHandler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _cfg(home: Path) -> Config:
    return Config(home=home)


class TestToolsState:
    def test_state_exposes_endpoint_availability_without_secrets(self, home: Path, server: str) -> None:
        state = _get_json(server, "/api/state")
        section = state["tools"]
        assert section["available"] is True
        assert section["source"] == "custom_providers"
        assert "cbcn/glm-5.3-flash" in [m["id"] for m in section["models"]]
        assert section["default_model"] == "cbcn/glm-5.3-flash"  # flash 档优先
        assert section["max_input_chars"] == tools._MAX_INPUT_CHARS
        lang_ids = [item["id"] for item in section["target_langs"]]
        assert "zh" in lang_ids and "en" in lang_ids
        # 整份 state 里绝不允许出现 key 本体（前端拿不到凭据）
        assert _SECRET not in json.dumps(state, ensure_ascii=False)

    def test_state_reports_unavailable_without_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(srv, "hermes_home", lambda: tmp_path)
        (tmp_path / "config.yaml").write_text("streaming:\n  enabled: true\n", encoding="utf-8")
        section = tools.tools_state(Config(home=tmp_path))
        assert section["available"] is False
        assert section["source"] == ""
        assert section["models"] == []

    def test_state_reflects_configured_summary_model(self, home: Path) -> None:
        data = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        data["streaming"]["ai_summary"] = {"model": "cbai/hy4-flash"}
        (home / "config.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        assert tools.tools_state(_cfg(home))["default_model"] == "cbai/hy4-flash"


class TestRunToolValidation:
    def test_mode_is_required_and_enumerated(self, home: Path) -> None:
        with pytest.raises(ValueError, match="mode"):
            tools.run_tool({"mode": "poem", "text": "x"}, _cfg(home))
        with pytest.raises(ValueError, match="mode"):
            tools.run_tool({"text": "x"}, _cfg(home))

    def test_text_is_required_nonblank_and_capped(self, home: Path) -> None:
        with pytest.raises(ValueError, match="text"):
            tools.run_tool({"mode": "summary", "text": "   "}, _cfg(home))
        with pytest.raises(ValueError, match="超过"):
            tools.run_tool(
                {"mode": "summary", "text": "x" * (tools._MAX_INPUT_CHARS + 1)}, _cfg(home)
            )

    def test_unknown_fields_rejected(self, home: Path) -> None:
        with pytest.raises(ValueError, match="api_key"):
            tools.run_tool(
                {"mode": "summary", "text": "x", "api_key": "hack"}, _cfg(home)
            )

    def test_model_and_lang_length_and_control_chars(self, home: Path) -> None:
        with pytest.raises(ValueError, match="model"):
            tools.run_tool({"mode": "summary", "text": "x", "model": "m" * 200}, _cfg(home))
        with pytest.raises(ValueError, match="model"):
            tools.run_tool({"mode": "summary", "text": "x", "model": "a\nb"}, _cfg(home))
        with pytest.raises(ValueError, match="target_lang"):
            tools.run_tool({"mode": "translate", "text": "x", "target_lang": "l" * 99}, _cfg(home))

    def test_timeout_bounds(self, home: Path) -> None:
        assert tools._expect_timeout(None) == tools._DEFAULT_TIMEOUT_SEC
        assert tools._expect_timeout(0.1) == tools._TIMEOUT_MIN_SEC
        assert tools._expect_timeout(9999) == tools._TIMEOUT_MAX_SEC
        with pytest.raises(ValueError, match="timeout_sec"):
            tools._expect_timeout("fast")
        with pytest.raises(ValueError, match="timeout_sec"):
            tools._expect_timeout(True)

    def test_payload_must_be_object(self, home: Path) -> None:
        with pytest.raises(ValueError, match="对象"):
            tools.run_tool(["not", "a", "dict"], _cfg(home))


class TestRunToolCalls:
    def test_summary_call_passes_endpoint_model_and_budget(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        def fake(endpoint: Any, model: str, messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
            seen.update(endpoint=endpoint, model=model, messages=messages, kw=kw)
            usage = {"prompt_tokens": 40, "completion_tokens": 18}
            return {"text": "结论摘要", "usage": usage, "elapsed_ms": 2100, "model": model}

        monkeypatch.setattr(tools, "chat_completion", fake)
        out = tools.run_tool({"mode": "summary", "text": "正文" * 300}, _cfg(home))
        assert out == {
            "ok": True,
            "mode": "summary",
            "text": "结论摘要",
            "model": "cbcn/glm-5.3-flash",
            "usage": {"prompt_tokens": 40, "completion_tokens": 18},
            "elapsed_ms": 2100,
            "source": "custom_providers",
        }
        assert seen["endpoint"].api_key == _SECRET
        assert seen["kw"]["timeout_sec"] == tools._DEFAULT_TIMEOUT_SEC
        assert seen["kw"]["max_tokens"] == tools._SUMMARY_MAX_OUTPUT_TOKENS
        assert _SECRET not in json.dumps(out, ensure_ascii=False)

    def test_translate_uses_selected_language_and_input_scaled_budget(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def fake(endpoint: Any, model: str, messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
            seen.update(messages=messages, kw=kw)
            return {"text": "Done", "usage": {}, "elapsed_ms": 900, "model": model}

        monkeypatch.setattr(tools, "chat_completion", fake)
        out = tools.run_tool({"mode": "translate", "text": "完结卡摘要已就绪", "target_lang": "en"}, _cfg(home))
        assert out["ok"] is True and out["text"] == "Done"
        assert "翻译成en" in seen["messages"][0]["content"]
        assert seen["kw"]["max_tokens"] == 256  # 短句走下限
        long_text = "很长" * 4000
        tools.run_tool({"mode": "translate", "text": long_text}, _cfg(home))
        assert seen["kw"]["max_tokens"] == tools._TRANSLATE_MAX_OUTPUT_TOKENS

    def test_translate_without_language_picks_zh_or_en_by_content(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def fake(endpoint: Any, model: str, messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
            seen["content"] = messages[0]["content"]
            return {"text": "ok", "usage": {}, "elapsed_ms": 1, "model": model}

        monkeypatch.setattr(tools, "chat_completion", fake)
        zh = tools.run_tool({"mode": "translate", "text": "完结卡会自动生成摘要，短回复不打扰。"}, _cfg(home))
        assert "翻译成英文" in seen["content"] and zh["target_lang"] == "英文"
        en = tools.run_tool({"mode": "translate", "text": "The card adds a summary after sealing."}, _cfg(home))
        assert "翻译成中文" in seen["content"] and en["target_lang"] == "中文"

    def test_explicit_model_wins_over_auto(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        picked: dict[str, str] = {}

        def fake(endpoint: Any, model: str, messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
            picked["model"] = model
            return {"text": "t", "usage": {}, "elapsed_ms": 1, "model": model}

        monkeypatch.setattr(tools, "chat_completion", fake)
        tools.run_tool({"mode": "summary", "text": "x" * 10, "model": "cbai/hy4-flash"}, _cfg(home))
        assert picked["model"] == "cbai/hy4-flash"

    def test_missing_endpoint_gives_clear_message(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "config.yaml").write_text("streaming:\n  enabled: true\n", encoding="utf-8")
        with pytest.raises(llm.LLMError, match="没有可用的模型端点"):
            tools.run_tool({"mode": "summary", "text": "x"}, Config(home=tmp_path))


class TestHttpRoute:
    def test_summary_roundtrip(self, server: str, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            tools,
            "chat_completion",
            lambda endpoint, model, messages, **kw: {
                "text": "摘要结果",
                "usage": {"prompt_tokens": 12, "completion_tokens": 5},
                "elapsed_ms": 1500,
                "model": model,
            },
        )
        out = _post_ok(server, "/api/tools", {"mode": "summary", "text": "正文" * 200})
        assert out["ok"] is True and out["text"] == "摘要结果"
        assert out["usage"]["prompt_tokens"] == 12

    def test_endpoint_error_is_200_with_human_message(self, server: str, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(endpoint: Any, model: str, messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
            raise llm.LLMError(f"端点返回 HTTP 502：bad gateway，用的是 {endpoint.api_key}")

        monkeypatch.setattr(tools, "chat_completion", boom)
        code, body, _ = _req(server, "/api/tools", {"mode": "summary", "text": "x"})
        assert code == 200
        payload = json.loads(body)
        assert payload["ok"] is False and "HTTP 502" in payload["error"]
        assert _SECRET not in payload["error"]  # 掩码在 llm 层已生效，路由不得再漏

    def test_bad_input_is_400(self, server: str) -> None:
        code, body, _ = _req(server, "/api/tools", {"mode": "summary"})
        assert code == 400 and "text" in json.loads(body)["error"]
        code, _, _ = _req(server, "/api/tools", {"mode": "nope", "text": "x"})
        assert code == 400

    def test_route_never_writes_config(self, server: str, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        before = (home / "config.yaml").read_text(encoding="utf-8")
        monkeypatch.setattr(
            tools,
            "chat_completion",
            lambda *a, **k: {"text": "t", "usage": {}, "elapsed_ms": 1, "model": "m"},
        )
        _post_ok(server, "/api/tools", {"mode": "summary", "text": "x" * 20})
        assert (home / "config.yaml").read_text(encoding="utf-8") == before

    def test_host_gate_applies(self, server: str) -> None:
        """POST 路由同 GET 一样吃 Host 门（防 DNS rebinding 打到 tools 端点白烧 token）."""
        import http.client

        host, port = server.removeprefix("http://").split(":")
        conn = http.client.HTTPConnection(host, int(port), timeout=10)
        # putrequest(skip_host=True) 才真的只发我们指定的 Host：直接塞 headers 里
        # 会连默认 Host 一起发出去，服务端取到哪个不确定（既有 test_studio 同口径）
        conn.putrequest("POST", "/api/tools", skip_host=True)
        conn.putheader("Host", "evil.example.com")
        conn.putheader("Content-Type", "application/json")
        body = json.dumps({"mode": "summary", "text": "x"}).encode()
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        assert resp.status == 403
        resp.read()
        conn.close()


class TestAiSummaryConfigWhitelist:
    def test_validate_accepts_full_section(self) -> None:
        validated = srv.validate_payload(
            {"streaming": {"ai_summary": {
                "enabled": True,
                "min_chars": 1200,
                "model": "cbcn/glm-5.3-flash",
                "timeout_sec": 12,
                "max_output_chars": 200,
            }}}
        )
        assert validated["streaming"]["ai_summary"] == {
            "enabled": True,
            "min_chars": 1200,
            "model": "cbcn/glm-5.3-flash",
            "timeout_sec": 12.0,
            "max_output_chars": 200,
        }

    def test_rejects_unknown_keys_and_bad_types(self) -> None:
        with pytest.raises(ValueError, match="未知字段"):
            srv.validate_payload({"streaming": {"ai_summary": {"prompt": "hack"}}})
        with pytest.raises(ValueError, match="必须是布尔值"):
            srv.validate_payload({"streaming": {"ai_summary": {"enabled": "yes"}}})
        with pytest.raises(ValueError, match="model"):
            srv.validate_payload({"streaming": {"ai_summary": {"model": 42}}})
        with pytest.raises(ValueError, match="model"):
            srv.validate_payload({"streaming": {"ai_summary": {"model": "a b/c\ninject"}}})
        with pytest.raises(ValueError, match="1~120"):
            srv.validate_payload({"streaming": {"ai_summary": {"timeout_sec": 3600}}})
        with pytest.raises(ValueError, match="40~600"):
            srv.validate_payload({"streaming": {"ai_summary": {"max_output_chars": 99999}}})

    def test_merge_writes_only_managed_keys(self, home: Path) -> None:
        base = srv.read_full_config(home / "config.yaml")
        merged, changed = srv.merge_managed(
            base, srv.validate_payload({"streaming": {"ai_summary": {"enabled": True, "min_chars": 500}}})
        )
        assert changed == ["streaming.ai_summary.enabled", "streaming.ai_summary.min_chars"]
        assert merged["model"]["default"] == "Hermes"  # 非受管段原样保留
        assert merged["custom_providers"][0]["api_key"] == _SECRET  # 凭据不被改动
        assert merged["streaming"]["ai_summary"] == {"enabled": True, "min_chars": 500}

    def test_config_http_roundtrip_hot_read(self, server: str, home: Path) -> None:
        _post_ok(server, "/api/config", {"streaming": {"ai_summary": {"enabled": True, "min_chars": 1000}}})
        state = _get_json(server, "/api/state")
        assert state["streaming"]["ai_summary"]["enabled"] is True
        assert state["streaming"]["ai_summary"]["min_chars"] == 1000
        # 写盘后运行时读取必须立刻跟上（热重载口径）
        cfg = Config(home=home)
        assert cfg.ai_summary_enabled is True and cfg.ai_summary_min_chars == 1000
