"""Studio 可视化工作坊测试 — 校验 / 拒写 / 备份 / 白名单 / 原子写 / HTTP 冒烟 / 预览."""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import yaml

import hermes_fry_cards.studio.server as srv

_INITIAL_YAML = (
    "streaming:\n"
    "  enabled: true\n"
    "  card_ttl_sec: 600\n"  # 用户手写的非受管键 — 保存后必须存活
    "agents:\n"
    "  keep: me\n"
)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(srv, "hermes_home", lambda: tmp_path)
    (tmp_path / "config.yaml").write_text(_INITIAL_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def server(home: Path):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.StudioHandler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _req(
    base: str,
    path: str,
    payload: Any = None,
    *,
    raw: bytes | None = None,
) -> tuple[int, bytes, Any]:
    data = raw if raw is not None else (json.dumps(payload).encode("utf-8") if payload is not None else None)
    headers = {"Content-Type": "application/json"} if payload is not None and raw is None else {}
    req = Request(base + path, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status, resp.read(), resp.headers
    except HTTPError as e:
        return e.code, e.read(), e.headers


def _post_ok(base: str, path: str, payload: Any) -> dict[str, Any]:
    code, body, _ = _req(base, path, payload)
    assert code == 200, body
    return json.loads(body)


def _get_json(base: str, path: str) -> dict[str, Any]:
    code, body, _ = _req(base, path)
    assert code == 200, body
    return json.loads(body)


# ---------------------------------------------------------------------------
# 纯函数：校验
# ---------------------------------------------------------------------------


class TestValidation:
    def test_unknown_top_level_rejected(self) -> None:
        with pytest.raises(ValueError, match="未知顶层"):
            srv.validate_payload({"hacker": 1})

    def test_empty_payload_rejected(self) -> None:
        with pytest.raises(ValueError, match="至少"):
            srv.validate_payload({})

    def test_section_not_dict_rejected(self) -> None:
        with pytest.raises(ValueError, match="必须是对象"):
            srv.validate_payload({"streaming": "yes"})

    def test_unknown_nested_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="未知字段"):
            srv.validate_payload({"streaming": {"nonsense": True}})

    @pytest.mark.parametrize(
        ("bad", "match"),
        [
            ({"streaming": {"width_mode": "wide"}}, "width_mode"),
            ({"streaming": {"enabled": "yes"}}, "enabled"),
            ({"streaming": {"content_lang": "fr"}}, "content_lang"),
            ({"streaming": {"header": {"min_duration": -1}}}, "min_duration"),
            ({"streaming": {"footer": {"fields": [["status", "bogus"]]}}}, "字段"),
            ({"streaming": {"footer": {"fields": [["status", "status"]]}}}, "重复"),
            ({"streaming": {"footer": {"fields": "status"}}}, "二维数组"),
            ({"streaming": {"chat_types": "dm"}}, "数组"),
            ({"display": {"max_reasoning_panels": 0}}, "max_reasoning_panels"),
            ({"display": {"max_reasoning_panels": True}}, "max_reasoning_panels"),
            ({"display": {"context_display_mode": "huge"}}, "context_display_mode"),
            ({"display": {"unified_panel_min_duration": 601}}, "unified_panel_min_duration"),
        ],
    )
    def test_invalid_values_rejected(self, bad: dict, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            srv.validate_payload(bad)

    def test_valid_normalizes(self) -> None:
        out = srv.validate_payload(
            {
                "streaming": {
                    "width_mode": "fill",
                    "chat_types": ["dm", "dm"],
                    "footer": {"fields": [["status", "model"]]},
                },
                "display": {"max_reasoning_panels": 7},
            }
        )
        assert out["streaming"]["width_mode"] == "fill"
        assert out["streaming"]["chat_types"] == ["dm"]  # 去重
        assert out["display"]["max_reasoning_panels"] == 7

    def test_partial_sections_allowed(self) -> None:
        out = srv.validate_payload({"display": {"show_reasoning": False}})
        assert "streaming" not in out
        assert out["display"]["show_reasoning"] is False

    def test_textsize_accepts_normal_v2(self) -> None:
        out = srv.validate_payload({"streaming": {"body": {"text_size": "normal_v2"}}})
        assert out["streaming"]["body"]["text_size"] == "normal_v2"


# ---------------------------------------------------------------------------
# 纯函数：白名单合并 / 备份 / 原子写
# ---------------------------------------------------------------------------


class TestMergeAndWrite:
    def test_user_keys_survive(self) -> None:
        base = yaml.safe_load(_INITIAL_YAML)
        merged, changed = srv.merge_managed(base, srv.validate_payload({"streaming": {"width_mode": "compact"}}))
        assert merged["agents"] == {"keep": "me"}
        assert merged["streaming"]["card_ttl_sec"] == 600  # 非受管键存活
        assert merged["streaming"]["width_mode"] == "compact"
        assert "streaming.width_mode" in changed

    def test_display_written_to_platform_override(self) -> None:
        base = {"display": {"show_reasoning": True, "platforms": {"lark": {"x": 1}}}}
        merged, changed = srv.merge_managed(base, srv.validate_payload({"display": {"show_reasoning": False}}))
        assert merged["display"]["show_reasoning"] is True  # 根键不动
        assert merged["display"]["platforms"]["feishu"]["show_reasoning"] is False
        assert merged["display"]["platforms"]["lark"] == {"x": 1}  # 同级平台存活
        assert changed == ["display.platforms.feishu.show_reasoning"]

    def test_noop_save_reports_empty_changes(self) -> None:
        base = {"streaming": {"width_mode": "fill"}}
        _, changed = srv.merge_managed(base, srv.validate_payload({"streaming": {"width_mode": "fill"}}))
        assert changed == []

    def test_structural_shape_refused(self) -> None:
        with pytest.raises(srv.ConfigReadError):
            srv.merge_managed({"streaming": "broken"}, {"streaming": {"width_mode": "fill"}})

    def test_read_unparsable_refused(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text("streaming: [unclosed", encoding="utf-8")
        with pytest.raises(srv.ConfigReadError):
            srv.read_full_config(p)

    def test_read_missing_returns_empty(self, tmp_path: Path) -> None:
        assert srv.read_full_config(tmp_path / "nope.yaml") == {}

    def test_write_atomic_roundtrip(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        srv.write_atomic(p, {"streaming": {"enabled": True, "chat_types": None}})
        assert not (tmp_path / "config.yaml.tmp").exists()
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert data["streaming"]["enabled"] is True
        assert data["streaming"]["chat_types"] is None

    def test_backup_rotation(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text(_INITIAL_YAML, encoding="utf-8")
        for _ in range(25):
            srv.backup_config(p)
        backups = list((tmp_path / "backups" / "fry_studio").glob("config.yaml.bak_*"))
        assert len(backups) == srv._BACKUP_KEEP


# ---------------------------------------------------------------------------
# HTTP：静态 / 门禁 / 状态
# ---------------------------------------------------------------------------


class TestHttpBasics:
    def test_index_served(self, server: str) -> None:
        code, body, headers = _req(server, "/")
        assert code == 200
        assert "fry-cards Studio" in body.decode("utf-8")
        assert "text/html" in headers["Content-Type"]

    def test_js_served(self, server: str) -> None:
        code, body, headers = _req(server, "/js/app.js")
        assert code == 200
        assert "text/javascript" in headers["Content-Type"]
        assert len(body) > 500

    @pytest.mark.parametrize(
        "path",
        ["/", "/index.html", "/css/style.css", "/js/preview.js", "/js/app.js"],
    )
    def test_all_web_assets_served(self, server: str, path: str) -> None:
        """所有前端资源必须可取 — preview.js 曾静默缺失导致 app.js 整体挂掉."""
        code, body, _ = _req(server, path)
        assert code == 200, path
        assert len(body) > 500, path

    def test_path_traversal_blocked(self, server: str) -> None:
        host, port = server.removeprefix("http://").split(":")
        conn = http.client.HTTPConnection(host, int(port), timeout=10)
        conn.request("GET", "/css/../../pyproject.toml")
        resp = conn.getresponse()
        assert resp.status == 404
        resp.read()
        conn.close()

    def test_foreign_host_forbidden(self, server: str) -> None:
        host, port = server.removeprefix("http://").split(":")
        conn = http.client.HTTPConnection(host, int(port), timeout=10)
        conn.putrequest("GET", "/api/state", skip_host=True)
        conn.putheader("Host", "evil.example.com")
        conn.endheaders()
        resp = conn.getresponse()
        assert resp.status == 403
        resp.read()
        conn.close()

    def test_no_cors_header(self, server: str) -> None:
        _, _, headers = _req(server, "/api/state")
        assert "Access-Control-Allow-Origin" not in headers

    def test_unknown_route_404(self, server: str) -> None:
        code, _, _ = _req(server, "/api/nope")
        assert code == 404

    def test_oversized_body_413(self, server: str) -> None:
        raw = b'{"x":"' + b"a" * (srv._MAX_BODY_BYTES + 100) + b'"}'
        code, _, _ = _req(server, "/api/config", raw=raw)
        assert code == 413

    def test_malformed_json_400(self, server: str) -> None:
        code, body, _ = _req(server, "/api/config", raw=b"{not json")
        assert code == 400
        assert json.loads(body)["ok"] is False

    def test_state_shape_and_no_secrets(self, server: str) -> None:
        data = _get_json(server, "/api/state")
        assert data["ok"] is True
        assert set(data["streaming"]) >= {"enabled", "width_mode", "header", "footer", "body"}
        assert set(data["display"]) >= {"show_reasoning", "max_reasoning_panels"}
        st = data["status"]
        assert set(st["verify"]) == {"gateway", "cron", "clarify"}
        assert isinstance(st["credentials"], bool)
        # 响应中绝不出现凭证键名与值
        raw = json.dumps(data, ensure_ascii=False)
        assert "app_secret" not in raw
        assert "app_id" not in raw

    def test_state_reflects_user_yaml(self, server: str) -> None:
        data = _get_json(server, "/api/state")
        assert data["streaming"]["enabled"] is True
        assert data["streaming"]["chat_types"] is None  # 未配置 = 全部


# ---------------------------------------------------------------------------
# HTTP：写回端点
# ---------------------------------------------------------------------------


class TestConfigEndpoint:
    def test_valid_save_writes_and_backups(self, server: str, home: Path) -> None:
        data = _post_ok(
            server,
            "/api/config",
            {"streaming": {"width_mode": "fill", "header": {"enabled": False, "min_duration": 2.5}}},
        )
        assert "streaming.width_mode" in data["changed"]
        conf = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        assert conf["streaming"]["width_mode"] == "fill"
        assert conf["streaming"]["header"] == {"enabled": False, "min_duration": 2.5}
        assert conf["agents"] == {"keep": "me"}
        assert conf["streaming"]["card_ttl_sec"] == 600
        backups = list((home / "backups" / "fry_studio").glob("config.yaml.bak_*"))
        assert len(backups) == 1
        assert not (home / "config.yaml.tmp").exists()

    def test_invalid_value_leaves_file_untouched(self, server: str, home: Path) -> None:
        before = (home / "config.yaml").read_bytes()
        code, body, _ = _req(server, "/api/config", {"streaming": {"width_mode": "wide"}})
        assert code == 400
        assert "width_mode" in json.loads(body)["error"]
        assert (home / "config.yaml").read_bytes() == before
        assert not list((home / "backups" / "fry_studio").glob("config.yaml.bak_*"))

    def test_unparsable_config_refused_409(self, server: str, home: Path) -> None:
        broken = "streaming: [unclosed"
        (home / "config.yaml").write_text(broken, encoding="utf-8")
        code, body, _ = _req(server, "/api/config", {"streaming": {"width_mode": "fill"}})
        assert code == 409
        assert json.loads(body)["ok"] is False
        assert (home / "config.yaml").read_text(encoding="utf-8") == broken  # 一字未动

    def test_display_override_written(self, server: str, home: Path) -> None:
        _post_ok(server, "/api/config", {"display": {"show_reasoning": False, "context_display_mode": "bar"}})
        conf = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        feishu = conf["display"]["platforms"]["feishu"]
        assert feishu["show_reasoning"] is False
        assert feishu["context_display_mode"] == "bar"

    def test_chat_types_null_roundtrip(self, server: str) -> None:
        _post_ok(server, "/api/config", {"streaming": {"chat_types": ["dm"]}})
        data = _get_json(server, "/api/state")
        assert data["streaming"]["chat_types"] == ["dm"]
        _post_ok(server, "/api/config", {"streaming": {"chat_types": None}})
        data = _get_json(server, "/api/state")
        assert data["streaming"]["chat_types"] is None

    def test_backup_capped_at_20(self, server: str, home: Path) -> None:
        for i in range(22):
            mode = "fill" if i % 2 == 0 else "default"
            _post_ok(server, "/api/config", {"streaming": {"width_mode": mode}})
        backups = list((home / "backups" / "fry_studio").glob("config.yaml.bak_*"))
        assert len(backups) == srv._BACKUP_KEEP


# ---------------------------------------------------------------------------
# HTTP：预览端点
# ---------------------------------------------------------------------------


class TestPreviewEndpoint:
    def test_completed_workflow(self, server: str) -> None:
        data = _post_ok(server, "/api/preview", {"scenario": "workflow", "state": "completed"})
        card = data["card"]
        assert card["schema"] == "2.0"
        assert card["header"]["template"] == "green"  # 完成态
        raw = json.dumps(card, ensure_ascii=False)
        assert "结论" in raw

    def test_streaming_state(self, server: str) -> None:
        data = _post_ok(server, "/api/preview", {"scenario": "workflow", "state": "streaming"})
        card = data["card"]
        assert card["config"]["streaming_mode"] is True
        assert card["header"]["template"] == "blue"

    def test_tables_compaction_visible(self, server: str) -> None:
        data = _post_ok(server, "/api/preview", {"scenario": "tables", "state": "completed"})
        raw = json.dumps(data["card"], ensure_ascii=False)
        assert "Table 6 · Row 1" in raw

    def test_longtext_clamped(self, server: str) -> None:
        data = _post_ok(server, "/api/preview", {"scenario": "longtext", "state": "completed"})
        raw = json.dumps(data["card"], ensure_ascii=False)
        assert "省略" in raw or "omitted" in raw

    def test_error_outcome_header_red(self, server: str) -> None:
        data = _post_ok(server, "/api/preview", {"scenario": "short", "state": "completed", "outcome": "error"})
        assert data["card"]["header"]["template"] == "red"

    def test_overrides_not_persisted(self, server: str) -> None:
        data = _post_ok(
            server,
            "/api/preview",
            {"scenario": "short", "state": "completed", "overrides": {"streaming": {"header": {"enabled": False}}}},
        )
        assert "header" not in data["card"]  # override 生效
        state = _get_json(server, "/api/state")
        assert state["streaming"]["header"]["enabled"] is True  # 未落盘

    @pytest.mark.parametrize(
        "payload",
        [
            {"scenario": "nope"},
            {"scenario": "short", "state": "weird"},
            {"scenario": "short", "state": "completed", "outcome": "explode"},
            {"scenario": "short", "bogus_field": 1},
            {"scenario": "short", "overrides": {"streaming": {"width_mode": "wide"}}},
        ],
    )
    def test_invalid_preview_400(self, server: str, payload: dict) -> None:
        code, body, _ = _req(server, "/api/preview", payload)
        assert code == 400
        assert json.loads(body)["ok"] is False


# ---------------------------------------------------------------------------
# HTTP：重启端点（hermes CLI 缺失时如实回报）
# ---------------------------------------------------------------------------


class TestRestartEndpoint:
    def test_missing_cli_reported(self, server: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(srv.shutil, "which", lambda _name: None)
        data = _post_ok(server, "/api/restart", {})
        assert data["ok"] is False
        assert "hermes" in data["error"]


# ---------------------------------------------------------------------------
# HTTP：模型别名（时段人设格式兼容 claw-fry-cards）
# ---------------------------------------------------------------------------


class TestAliasesEndpoint:
    def test_get_missing_file_returns_empty(self, server: str) -> None:
        data = _get_json(server, "/api/aliases")
        assert data == {"ok": True, "entries": []}

    def test_roundtrip_string_and_time_entries(self, server: str, home: Path) -> None:
        # 预置旧别名文件 → 保存后备份的应是原件（首写无文件则无可备份）
        (home / "model_aliases.json").write_text('{"old": "旧名"}', encoding="utf-8")
        entries = [
            {"key": "mimo", "value": "小虾米"},
            {
                "key": "deepseek",
                "value": {
                    "name": "梁文谷⚡️",
                    "timeAliases": [
                        {"days": [1, 2, 3, 4, 5], "start": "09:00", "end": "12:00", "name": "梁文锋⚡️"},
                        {"days": "1-5,0", "start": "14:00", "end": "18:00", "name": "梁文锋⚡️"},
                    ],
                },
            },
        ]
        data = _post_ok(server, "/api/aliases", {"entries": entries})
        assert data["count"] == 2
        assert data["keys"] == ["mimo", "deepseek"]
        raw = json.loads((home / "model_aliases.json").read_text(encoding="utf-8"))
        assert list(raw) == ["mimo", "deepseek"]  # 插入序保留
        assert raw["deepseek"]["name"] == "梁文谷⚡️"
        assert raw["deepseek"]["timeAliases"][1]["days"] == "1-5,0"  # legacy 形态原样保留
        backups = sorted((home / "backups" / "fry_studio").glob("model_aliases.json.bak_*"))
        assert len(backups) == 1
        assert json.loads(backups[0].read_text(encoding="utf-8")) == {"old": "旧名"}  # 备份 = 写前原件
        back = _get_json(server, "/api/aliases")
        assert back["entries"] == entries

    def test_clear_all_entries(self, server: str, home: Path) -> None:
        _post_ok(server, "/api/aliases", {"entries": [{"key": "mimo", "value": "小虾米"}]})
        data = _post_ok(server, "/api/aliases", {"entries": []})
        assert data["count"] == 0
        assert json.loads((home / "model_aliases.json").read_text(encoding="utf-8")) == {}

    @pytest.mark.parametrize(
        "payload",
        [
            {"entries": [{"key": "a", "value": "x"}, {"key": "A", "value": "y"}]},  # 大小写重复
            {"entries": [{"key": "", "value": "x"}]},  # 空键
            {"entries": [{"key": " k", "value": "x"}]},  # 首尾空白
            {"entries": [{"key": "k", "value": 123}]},  # 值类型
            {"entries": [{"key": "k"}]},  # 缺 value
            {"entries": [{"k": "k", "value": "x"}]},  # 键名错
            {"entries": [{"key": "k", "value": {"name": "n", "bogus": 1}}]},  # 对象未知字段
            {"entries": [{"key": "k", "value": {"timeAliases": [{"days": [1]}]}}]},  # 规则缺 name
            {"entries": [{"key": "k", "value": {"timeAliases": [{"name": "x", "start": "9:00"}]}}]},  # 非 HH:MM
            {"entries": [{"key": "k", "value": {"timeAliases": [{"name": "x", "end": "24:00"}]}}]},  # 超界
            {"entries": [{"key": "k", "value": {"timeAliases": [{"name": "x", "days": [7]}]}}]},  # days 越界
            {"entries": [{"key": "k", "value": {"timeAliases": [{"name": "x", "days": "8-9"}]}}]},  # legacy days 越界
            {"entries": "oops"},
            {"entries": [], "extra": 1},
        ],
    )
    def test_invalid_entries_rejected_untouched(self, server: str, home: Path, payload: dict) -> None:
        code, body, _ = _req(server, "/api/aliases", payload)
        assert code == 400, body
        assert json.loads(body)["ok"] is False
        assert not (home / "model_aliases.json").exists()  # 一字未写

    def test_unparsable_aliases_file_refused(self, server: str, home: Path) -> None:
        path = home / "model_aliases.json"
        path.write_text("{broken", encoding="utf-8")
        gcode, _gbody, _ = _req(server, "/api/aliases")
        assert gcode == 409
        pcode, pbody, _ = _req(server, "/api/aliases", {"entries": [{"key": "k", "value": "v"}]})
        assert pcode == 409
        assert json.loads(pbody)["ok"] is False
        assert path.read_text(encoding="utf-8") == "{broken"

    def test_enabled_switch_in_state_and_config_roundtrip(self, server: str, home: Path) -> None:
        state = _get_json(server, "/api/state")
        assert state["display"]["model_aliases_enabled"] is True  # 缺省开
        _post_ok(server, "/api/config", {"display": {"model_aliases_enabled": False}})
        state = _get_json(server, "/api/state")
        assert state["display"]["model_aliases_enabled"] is False
        conf = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        assert conf["display"]["platforms"]["feishu"]["model_aliases_enabled"] is False
        _post_ok(server, "/api/config", {"display": {"model_aliases_enabled": True}})
        assert _get_json(server, "/api/state")["display"]["model_aliases_enabled"] is True


# ---------------------------------------------------------------------------
# HTTP：群聊安全边界（gateway.group_security_boundary）
# ---------------------------------------------------------------------------


class TestGroupSecurityBoundary:
    def test_state_default(self, server: str) -> None:
        state = _get_json(server, "/api/state")
        gsb = state["gateway"]["group_security_boundary"]
        assert gsb == {"enabled": False, "allow_chats": []}

    def test_enable_and_roundtrip_with_dedupe(self, server: str, home: Path) -> None:
        payload = {
            "gateway": {
                "group_security_boundary": {"enabled": True, "allow_chats": ["oc_dev1", "oc_dev1", "oc_team2"]}
            }
        }
        data = _post_ok(server, "/api/config", payload)
        assert "gateway.group_security_boundary.enabled" in data["changed"]
        gsb = _get_json(server, "/api/state")["gateway"]["group_security_boundary"]
        assert gsb == {"enabled": True, "allow_chats": ["oc_dev1", "oc_team2"]}
        conf = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        assert conf["gateway"]["group_security_boundary"]["enabled"] is True

    def test_sibling_gateway_keys_and_custom_text_preserved(self, server: str, home: Path) -> None:
        # 预置 gateway 段：兄弟键 + 未纳管的自定义 text 必须在保存后存活
        (home / "config.yaml").write_text(
            "gateway:\n"
            "  other_gw_key: 42\n"
            "  group_security_boundary:\n"
            "    enabled: false\n"
            "    text: CUSTOM-BOUNDARY-TEXT\n",
            encoding="utf-8",
        )
        _post_ok(
            server,
            "/api/config",
            {"gateway": {"group_security_boundary": {"enabled": True, "allow_chats": ["oc_x"]}}},
        )
        conf = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        gw = conf["gateway"]
        assert gw["other_gw_key"] == 42  # 兄弟键存活
        assert gw["group_security_boundary"]["text"] == "CUSTOM-BOUNDARY-TEXT"  # 未纳管键存活
        assert gw["group_security_boundary"]["enabled"] is True
        assert gw["group_security_boundary"]["allow_chats"] == ["oc_x"]

    @pytest.mark.parametrize(
        "gsb",
        [
            {"allow_chats": "oc_x"},  # 非数组
            {"allow_chats": ["has space"]},  # 含空白
            {"allow_chats": [""]},  # 空串
            {"allow_chats": ["x" * 65]},  # 超长
            {"allow_chats": [123]},  # 非字符串
            {"bogus": True},  # 未知字段
            {"enabled": "yes"},  # 非布尔
        ],
    )
    def test_invalid_gsb_rejected_untouched(self, server: str, home: Path, gsb: dict) -> None:
        before = (home / "config.yaml").read_bytes()
        code, body, _ = _req(server, "/api/config", {"gateway": {"group_security_boundary": gsb}})
        assert code == 400, body
        assert (home / "config.yaml").read_bytes() == before

    def test_unknown_gateway_section_rejected(self, server: str) -> None:
        code, _, _ = _req(server, "/api/config", {"gateway": {"other_section": {}}})
        assert code == 400

    def test_gsb_shape_mismatch_refused_409(self, server: str, home: Path) -> None:
        (home / "config.yaml").write_text("gateway:\n  group_security_boundary: broken\n", encoding="utf-8")
        code, body, _ = _req(
            server, "/api/config", {"gateway": {"group_security_boundary": {"enabled": True}}}
        )
        assert code == 409
        assert json.loads(body)["ok"] is False
