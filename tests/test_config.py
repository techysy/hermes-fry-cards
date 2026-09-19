"""config.py 测试 — 配置加载、footer 字段容错、平台配置优先级."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hermes_fry_cards.config import Config, _in_time_range, _resolve_model_alias


def _make_config(raw: dict[str, Any]) -> Config:
    """Create a Config pre-loaded with given raw dict."""
    cfg = Config()
    cfg._raw = raw
    return cfg


class TestEnabled:
    def test_enabled_true(self) -> None:
        cfg = _make_config({"streaming": {"enabled": True}})
        assert cfg.enabled is True

    def test_enabled_false(self) -> None:
        cfg = _make_config({"streaming": {"enabled": False}})
        assert cfg.enabled is False

    @pytest.mark.parametrize("raw", [{"streaming": {}}, {}], ids=["missing-key", "missing-section"])
    def test_enabled_defaults_true_when_missing(self, raw: dict[str, Any]) -> None:
        cfg = _make_config(raw)
        assert cfg.enabled is True

    def test_streaming_section_not_dict(self) -> None:
        cfg = _make_config({"streaming": "invalid"})
        assert cfg.enabled is True


class TestChatTypes:
    def test_defaults_none_when_missing(self) -> None:
        cfg = _make_config({"streaming": {}})
        assert cfg.chat_types is None

    def test_defaults_none_when_section_missing(self) -> None:
        cfg = _make_config({})
        assert cfg.chat_types is None

    def test_list_of_chat_types(self) -> None:
        cfg = _make_config({"streaming": {"chat_types": ["dm", "group"]}})
        assert cfg.chat_types == {"dm", "group"}

    def test_single_string_coerced(self) -> None:
        cfg = _make_config({"streaming": {"chat_types": "dm"}})
        assert cfg.chat_types == {"dm"}

    def test_empty_list_means_disable_all(self) -> None:
        cfg = _make_config({"streaming": {"chat_types": []}})
        assert cfg.chat_types == set()

    def test_case_and_whitespace_normalized(self) -> None:
        cfg = _make_config({"streaming": {"chat_types": [" DM ", "Group"]}})
        assert cfg.chat_types == {"dm", "group"}

    def test_non_list_value_returns_none(self) -> None:
        cfg = _make_config({"streaming": {"chat_types": {"dm": True}}})
        assert cfg.chat_types is None


class TestFooterFields:
    def test_normal_2d_fields(self) -> None:
        cfg = _make_config({"streaming": {"footer": {"fields": [["a", "b"], ["c"]]}}})
        assert cfg.footer_fields == [["a", "b"], ["c"]]

    def test_1d_auto_wrapped(self) -> None:
        cfg = _make_config({"streaming": {"footer": {"fields": ["status", "elapsed"]}}})
        assert cfg.footer_fields == [["status", "elapsed"]]

    @pytest.mark.parametrize(
        "raw",
        [{"streaming": {"footer": {"fields": []}}}, {"streaming": {}}],
        ids=["empty-fields", "missing-footer"],
    )
    def test_empty_footer_configuration_returns_default(self, raw: dict[str, Any]) -> None:
        cfg = _make_config(raw)
        assert cfg.footer_fields == [["status", "elapsed", "model", "context"]]

    def test_footer_not_dict_returns_default(self) -> None:
        cfg = _make_config({"streaming": {"footer": "invalid"}})
        assert cfg.footer_fields == [["status", "elapsed", "model", "context"]]

    def test_fields_non_list_returns_default(self) -> None:
        cfg = _make_config({"streaming": {"footer": {"fields": "status"}}})
        assert cfg.footer_fields == [["status", "elapsed", "model", "context"]]


class TestHeaderEnabled:
    def test_enabled_true(self) -> None:
        cfg = _make_config({"streaming": {"header": {"enabled": True}}})
        assert cfg.header_enabled is True

    def test_enabled_false(self) -> None:
        cfg = _make_config({"streaming": {"header": {"enabled": False}}})
        assert cfg.header_enabled is False

    @pytest.mark.parametrize(
        "raw",
        [{"streaming": {"header": {}}}, {"streaming": {}}],
        ids=["missing-key", "missing-section"],
    )
    def test_header_enabled_defaults_true_when_missing(self, raw: dict[str, Any]) -> None:
        cfg = _make_config(raw)
        assert cfg.header_enabled is True

    def test_header_not_dict_defaults_true(self) -> None:
        cfg = _make_config({"streaming": {"header": "invalid"}})
        assert cfg.header_enabled is True


class TestFooterEnabled:
    def test_enabled_true(self) -> None:
        cfg = _make_config({"streaming": {"footer": {"enabled": True}}})
        assert cfg.footer_enabled is True

    def test_enabled_false(self) -> None:
        cfg = _make_config({"streaming": {"footer": {"enabled": False}}})
        assert cfg.footer_enabled is False

    @pytest.mark.parametrize(
        "raw",
        [{"streaming": {"footer": {}}}, {"streaming": {}}],
        ids=["missing-key", "missing-section"],
    )
    def test_footer_enabled_defaults_false_when_missing(self, raw: dict[str, Any]) -> None:
        cfg = _make_config(raw)
        assert cfg.footer_enabled is False

    def test_footer_not_dict_defaults_false(self) -> None:
        cfg = _make_config({"streaming": {"footer": "invalid"}})
        assert cfg.footer_enabled is False


class TestFooterShowLabel:
    @pytest.mark.parametrize("value", [True, False])
    def test_reads_boolean_value(self, value: bool) -> None:
        cfg = _make_config({"streaming": {"footer": {"show_label": value}}})
        assert cfg.footer_show_label is value

    def test_missing_defaults_false(self) -> None:
        cfg = _make_config({"streaming": {"footer": {}}})
        assert cfg.footer_show_label is False


class TestCardDurationSec:
    def test_custom(self) -> None:
        cfg = _make_config({"streaming": {"card_ttl_sec": 300}})
        assert cfg.card_duration_sec == 300

    def test_default(self) -> None:
        cfg = _make_config({"streaming": {}})
        assert cfg.card_duration_sec == 600


class TestWidthMode:
    def test_default_when_missing(self) -> None:
        cfg = _make_config({"streaming": {}})
        assert cfg.width_mode == "default"

    def test_reads_valid_value(self) -> None:
        cfg = _make_config({"streaming": {"width_mode": "compact"}})
        assert cfg.width_mode == "compact"

    def test_reads_case_insensitive(self) -> None:
        cfg = _make_config({"streaming": {"width_mode": "FILL"}})
        assert cfg.width_mode == "fill"

    def test_invalid_falls_back_to_default(self) -> None:
        cfg = _make_config({"streaming": {"width_mode": "wide"}})
        assert cfg.width_mode == "default"


class TestFeishuAppId:
    def test_from_env(self) -> None:
        cfg = _make_config({})
        with patch.dict(os.environ, {"FEISHU_APP_ID": "env_id", "FEISHU_APP_SECRET": "env_secret"}):
            assert cfg.feishu_app_id == "env_id"

    def test_from_config(self) -> None:
        cfg = _make_config({"feishu": {"app_id": "cfg_id", "app_secret": "cfg_secret"}})
        with patch.dict(os.environ, {}, clear=True):
            assert cfg.feishu_app_id == "cfg_id"

    def test_empty_when_missing(self) -> None:
        cfg = _make_config({})
        with patch.dict(os.environ, {}, clear=True):
            assert cfg.feishu_app_id == ""


class TestFeishuBaseURL:
    def test_default_url(self) -> None:
        cfg = _make_config({"feishu": {"app_id": "id", "app_secret": "s"}})
        with patch.dict(os.environ, {}, clear=True):
            assert cfg.feishu_base_url == "https://open.feishu.cn"

    def test_custom_url_from_config(self) -> None:
        cfg = _make_config({"feishu": {"app_id": "id", "app_secret": "s", "base_url": "https://custom.com"}})
        with patch.dict(os.environ, {}, clear=True):
            assert cfg.feishu_base_url == "https://custom.com"

    def test_from_env(self) -> None:
        cfg = _make_config({})
        with patch.dict(
            os.environ, {"FEISHU_APP_ID": "id", "FEISHU_APP_SECRET": "s", "FEISHU_BASE_URL": "https://env.com"}
        ):
            assert cfg.feishu_base_url == "https://env.com"


class TestShowReasoning:
    def _make_reasoning_config(self, raw: dict[str, Any]) -> Config:
        """Create a Config with _reload mocked to return given raw dict."""
        cfg = Config()
        cfg._reload = lambda: raw  # type: ignore[assignment]
        return cfg

    def test_platform_level_true(self) -> None:
        cfg = self._make_reasoning_config({"display": {"platforms": {"feishu": {"show_reasoning": True}}}})
        assert cfg.show_reasoning is True

    def test_platform_level_false(self) -> None:
        cfg = self._make_reasoning_config({"display": {"platforms": {"feishu": {"show_reasoning": False}}}})
        assert cfg.show_reasoning is False

    def test_global_fallback_true(self) -> None:
        cfg = self._make_reasoning_config({"display": {"show_reasoning": True}})
        assert cfg.show_reasoning is True

    def test_global_fallback_false(self) -> None:
        cfg = self._make_reasoning_config({"display": {"show_reasoning": False}})
        assert cfg.show_reasoning is False

    def test_default_true(self) -> None:
        cfg = self._make_reasoning_config({})
        assert cfg.show_reasoning is True

    def test_display_not_dict(self) -> None:
        cfg = self._make_reasoning_config({"display": "invalid"})
        assert cfg.show_reasoning is True

    def test_platforms_not_dict(self) -> None:
        cfg = self._make_reasoning_config({"display": {"platforms": "invalid"}})
        assert cfg.show_reasoning is True

    def test_feishu_section_missing_key(self) -> None:
        cfg = self._make_reasoning_config({"display": {"platforms": {"feishu": {"other": True}}}})
        assert cfg.show_reasoning is True

    def test_platform_takes_priority_over_global(self) -> None:
        cfg = self._make_reasoning_config({
            "display": {
                "platforms": {"feishu": {"show_reasoning": False}},
                "show_reasoning": True,
            }
        })
        assert cfg.show_reasoning is False

    def test_no_display_section(self) -> None:
        cfg = self._make_reasoning_config({"streaming": {"enabled": True}})
        assert cfg.show_reasoning is True


class TestShowToolUse:
    def _make_config(self, raw: dict[str, Any]) -> Config:
        cfg = Config()
        cfg._reload = lambda: raw  # type: ignore[assignment]
        return cfg

    def test_platform_level_true(self) -> None:
        cfg = self._make_config({"display": {"platforms": {"feishu": {"show_tool_use": True}}}})
        assert cfg.show_tool_use is True

    def test_platform_level_false(self) -> None:
        cfg = self._make_config({"display": {"platforms": {"feishu": {"show_tool_use": False}}}})
        assert cfg.show_tool_use is False

    def test_global_fallback_true(self) -> None:
        cfg = self._make_config({"display": {"show_tool_use": True}})
        assert cfg.show_tool_use is True

    def test_global_fallback_false(self) -> None:
        cfg = self._make_config({"display": {"show_tool_use": False}})
        assert cfg.show_tool_use is False

    def test_default_true(self) -> None:
        """Missing config → default True (backward compatible)."""
        cfg = self._make_config({})
        assert cfg.show_tool_use is True

    def test_display_not_dict(self) -> None:
        cfg = self._make_config({"display": "invalid"})
        assert cfg.show_tool_use is True

    def test_platforms_not_dict(self) -> None:
        cfg = self._make_config({"display": {"platforms": "invalid"}})
        assert cfg.show_tool_use is True

    def test_feishu_section_missing_key(self) -> None:
        cfg = self._make_config({"display": {"platforms": {"feishu": {"other": True}}}})
        assert cfg.show_tool_use is True

    def test_platform_takes_priority_over_global(self) -> None:
        cfg = self._make_config({
            "display": {
                "platforms": {"feishu": {"show_tool_use": False}},
                "show_tool_use": True,
            }
        })
        assert cfg.show_tool_use is False


class TestPlatformCfg:
    def test_env_takes_priority(self) -> None:
        cfg = _make_config({"feishu": {"app_id": "config_id", "app_secret": "config_secret"}})
        with patch.dict(os.environ, {"FEISHU_APP_ID": "env_id", "FEISHU_APP_SECRET": "env_secret"}):
            result = cfg._platform_cfg()
            assert result["app_id"] == "env_id"

    def test_lark_section_fallback(self) -> None:
        cfg = _make_config({"lark": {"app_id": "lark_id", "app_secret": "lark_secret"}})
        with patch.dict(os.environ, {}, clear=True):
            result = cfg._platform_cfg()
            assert result["app_id"] == "lark_id"

    def test_feishu_before_lark(self) -> None:
        cfg = _make_config(
            {
                "feishu": {"app_id": "feishu_id", "app_secret": "fs"},
                "lark": {"app_id": "lark_id", "app_secret": "ls"},
            }
        )
        with patch.dict(os.environ, {}, clear=True):
            result = cfg._platform_cfg()
            assert result["app_id"] == "feishu_id"

    def test_empty_when_nothing(self) -> None:
        cfg = _make_config({})
        with patch.dict(os.environ, {}, clear=True):
            assert cfg._platform_cfg() == {}


def test_bound_profile_homes_resolve_distinct_gateway_platform_credentials(tmp_path) -> None:
    home_a = tmp_path / "profile-a"
    home_b = tmp_path / "profile-b"
    home_a.mkdir()
    home_b.mkdir()
    (home_a / "config.yaml").write_text(
        (
            "streaming:\n  enabled: true\ngateway:\n  platforms:\n    feishu:\n"
            "      extra:\n        app_id: app-a\n        app_secret: secret-a\n"
        ),
        encoding="utf-8",
    )
    (home_b / "config.yaml").write_text(
        (
            "streaming:\n  enabled: true\ngateway:\n  platforms:\n    feishu:\n"
            "      extra:\n        app_id: app-b\n        app_secret: secret-b\n"
        ),
        encoding="utf-8",
    )

    with patch.dict(os.environ, {}, clear=True):
        cfg_a = Config(home_a)
        cfg_b = Config(home_b)
        assert (cfg_a.enabled, cfg_a.feishu_app_id, cfg_a.feishu_app_secret) == (True, "app-a", "secret-a")
        assert (cfg_b.enabled, cfg_b.feishu_app_id, cfg_b.feishu_app_secret) == (True, "app-b", "secret-b")


def test_nested_lark_domain_uses_larksuite_url() -> None:
    cfg = _make_config(
        {
            "gateway": {
                "platforms": {
                    "lark": {
                        "extra": {
                            "app_id": "lark-id",
                            "app_secret": "lark-secret",
                            "domain": "lark",
                        }
                    }
                }
            }
        }
    )

    with patch.dict(os.environ, {}, clear=True):
        assert cfg.feishu_base_url == "https://open.larksuite.com"


class TestContentLang:
    def test_default_zh(self) -> None:
        cfg = _make_config({"streaming": {}})
        assert cfg.content_lang == "zh"

    def test_explicit_en(self) -> None:
        cfg = _make_config({"streaming": {"content_lang": "en"}})
        assert cfg.content_lang == "en"

    def test_case_and_whitespace_normalized(self) -> None:
        cfg = _make_config({"streaming": {"content_lang": " EN "}})
        assert cfg.content_lang == "en"

    def test_invalid_falls_back_zh(self) -> None:
        cfg = _make_config({"streaming": {"content_lang": "fr"}})
        assert cfg.content_lang == "zh"

    def test_streaming_section_not_dict(self) -> None:
        cfg = _make_config({"streaming": "invalid"})
        assert cfg.content_lang == "zh"


class TestModelAliasResolution:
    """时段人设解析 — 语义与 openclaw/claw-fry-cards 逐字对齐（claw 测试同款时间锚点）."""

    # 2026-09-14T04:00Z = 北京时间周一 12:00
    MONDAY_NOON = datetime(2026, 9, 14, 4, 0, tzinfo=UTC)

    def test_string_entry_passthrough(self) -> None:
        assert _resolve_model_alias("小虾米", self.MONDAY_NOON) == "小虾米"

    def test_peak_valley_half_open_interval(self) -> None:
        entry = {
            "name": "梁文谷⚡️",
            "timeAliases": [
                {"days": [1, 2, 3, 4, 5], "start": "09:00", "end": "12:00", "name": "梁文锋⚡️"},
                {"days": [1, 2, 3, 4, 5], "start": "14:00", "end": "18:00", "name": "梁文锋⚡️"},
            ],
        }
        # 北京 12:00 恰好是 [09:00,12:00) 右开边界 → 回落默认（谷段）
        assert _resolve_model_alias(entry, self.MONDAY_NOON) == "梁文谷⚡️"
        # 北京 15:00（UTC 07:00）命中下午峰段
        now_15 = datetime(2026, 9, 14, 7, 0, tzinfo=UTC)
        assert _resolve_model_alias(entry, now_15) == "梁文锋⚡️"

    def test_days_array_match_and_miss(self) -> None:
        entry = {"name": "默认", "timeAliases": [{"days": [1], "name": "周一"}]}
        assert _resolve_model_alias(entry, self.MONDAY_NOON) == "周一"
        entry_tue = {"name": "默认", "timeAliases": [{"days": [2], "name": "周二"}]}
        assert _resolve_model_alias(entry_tue, self.MONDAY_NOON) == "默认"

    def test_legacy_string_range_days(self) -> None:
        entry = {"name": "默认", "timeAliases": [{"days": "1-5", "start": "11:00", "end": "13:00", "name": "工作日"}]}
        assert _resolve_model_alias(entry, self.MONDAY_NOON) == "工作日"

    def test_legacy_comma_days(self) -> None:
        # 北京周一 = JS getDay 1
        entry = {"name": "默认", "timeAliases": [{"days": "0,6", "name": "周末"}]}
        assert _resolve_model_alias(entry, self.MONDAY_NOON) == "默认"
        entry2 = {"name": "默认", "timeAliases": [{"days": "1-5,0", "name": "带周日"}]}
        assert _resolve_model_alias(entry2, self.MONDAY_NOON) == "带周日"

    def test_cross_midnight_window(self) -> None:
        entry = {"name": "日", "timeAliases": [{"days": [1], "start": "18:00", "end": "09:00", "name": "夜"}]}
        # 北京周一 12:00 不在 18:00–09:00 → 回落
        assert _resolve_model_alias(entry, self.MONDAY_NOON) == "日"
        # 北京周一 20:00（UTC 12:00）在窗内
        now_20 = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
        assert _resolve_model_alias(entry, now_20) == "夜"

    def test_rule_without_name_skipped(self) -> None:
        entry = {"name": "默认", "timeAliases": [{"days": [1], "name": ""}, {"days": [1], "name": "有效"}]}
        assert _resolve_model_alias(entry, self.MONDAY_NOON) == "有效"

    def test_default_missing_returns_none(self) -> None:
        assert _resolve_model_alias({"timeAliases": [{"days": [0], "name": "周日"}]}, self.MONDAY_NOON) is None

    def test_junk_entry_returns_none(self) -> None:
        assert _resolve_model_alias(123, self.MONDAY_NOON) is None
        assert _resolve_model_alias(["x"], self.MONDAY_NOON) is None

    def test_beijing_fixed_regardless_of_host_tz(self) -> None:
        # 北京周日 10:00 = UTC 周日 02:00；固定 +8 计算，与宿主机时区无关
        sunday_10_bj = datetime(2026, 9, 13, 2, 0, tzinfo=UTC)
        entry = {"name": "x", "timeAliases": [{"days": [0], "start": "09:00", "end": "11:00", "name": "周日上午"}]}
        assert _resolve_model_alias(entry, sunday_10_bj) == "周日上午"

    def test_in_time_range_boundaries(self) -> None:
        assert _in_time_range("09:00", "09:00", "12:00") is True
        assert _in_time_range("12:00", "09:00", "12:00") is False
        assert _in_time_range("12:00", "12:00", "12:00") is True  # 起止相等 = 全天
        assert _in_time_range("03:00", "18:00", "09:00") is True  # 跨午夜
        assert _in_time_range("12:00", "18:00", "09:00") is False
        assert _in_time_range("03:00", "", "") is True

    def test_model_aliases_enabled_default_and_override(self, tmp_path: Path) -> None:
        # _reload() 每次读磁盘（热更新），故用真实临时 config.yaml 而非内存 _raw
        assert Config(home=tmp_path).model_aliases_enabled is True
        conf = tmp_path / "config.yaml"
        conf.write_text(
            "display:\n  platforms:\n    feishu:\n      model_aliases_enabled: false\n",
            encoding="utf-8",
        )
        assert Config(home=tmp_path).model_aliases_enabled is False
        conf.write_text(
            "display:\n  model_aliases_enabled: false\n"
            "  platforms:\n    feishu:\n      model_aliases_enabled: true\n",
            encoding="utf-8",
        )
        assert Config(home=tmp_path).model_aliases_enabled is True  # feishu 覆盖优先
        conf.write_text("display:\n  model_aliases_enabled: false\n", encoding="utf-8")
        assert Config(home=tmp_path).model_aliases_enabled is False  # 根键回落


class TestModelAliasesFile:
    def test_string_and_object_entries_resolved(self, tmp_path: Path) -> None:
        cfg = Config(home=tmp_path)
        (tmp_path / "model_aliases.json").write_text(
            json.dumps(
                {
                    "mimo": "小虾米",
                    "deepseek": {
                        "name": "梁文谷⚡️",
                        "timeAliases": [
                        {"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "23:59", "name": "梁文锋⚡️"}
                    ],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        aliases = cfg.model_aliases()
        assert aliases["mimo"] == "小虾米"
        # 全天规则命中（除 23:59–24:00 尾分钟外）
        assert aliases["deepseek"] in ("梁文锋⚡️", "梁文谷⚡️")

    def test_unparsable_file_returns_empty(self, tmp_path: Path) -> None:
        cfg = Config(home=tmp_path)
        (tmp_path / "model_aliases.json").write_text("{broken", encoding="utf-8")
        assert cfg.model_aliases() == {}

    def test_key_lowercased(self, tmp_path: Path) -> None:
        cfg = Config(home=tmp_path)
        (tmp_path / "model_aliases.json").write_text(json.dumps({"MIMO": "小虾米"}), encoding="utf-8")
        assert cfg.model_aliases() == {"mimo": "小虾米"}


class TestGroupSecurityBoundaryProperty:
    def test_defaults(self, tmp_path: Path) -> None:
        assert Config(home=tmp_path).group_security_boundary == {"enabled": False, "allow_chats": []}

    def test_reads_and_cleans(self, tmp_path: Path) -> None:
        conf = tmp_path / "config.yaml"
        conf.write_text(
            "gateway:\n"
            "  group_security_boundary:\n"
            "    enabled: true\n"
            "    allow_chats: ['oc_ok', 123, '', '  oc_pad  ', null]\n"
            "    text: CUSTOM\n",
            encoding="utf-8",
        )
        gsb = Config(home=tmp_path).group_security_boundary
        assert gsb["enabled"] is True
        assert gsb["allow_chats"] == ["oc_ok", "oc_pad"]  # 非法项剔除、空白裁剪
        # text 不在返回结构里（Studio 白名单不写它，但文件里保留）
        assert "text" not in gsb
        assert "text" in conf.read_text(encoding="utf-8")

    def test_gateway_not_dict_falls_back(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("gateway: broken\n", encoding="utf-8")
        assert Config(home=tmp_path).group_security_boundary == {"enabled": False, "allow_chats": []}

    def test_non_list_allow_chats_falls_back(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text(
            "gateway:\n  group_security_boundary:\n    enabled: true\n    allow_chats: oops\n",
            encoding="utf-8",
        )
        gsb = Config(home=tmp_path).group_security_boundary
        assert gsb == {"enabled": True, "allow_chats": []}
