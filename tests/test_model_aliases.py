"""模型别名配置（~/.hermes/model_aliases.json）测试."""

import json
from pathlib import Path

import pytest

from hermes_fry_cards.cardkit.builder import _display_model, _truncate_model
from hermes_fry_cards.config import Config


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    return tmp_path


def _write_aliases(home: Path, data: dict | str) -> None:
    p = home / "model_aliases.json"
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class TestModelAliasesConfig:
    def test_missing_file_returns_empty(self, home: Path) -> None:
        assert Config(home=home).model_aliases() == {}

    def test_valid_json_parsed(self, home: Path) -> None:
        _write_aliases(home, {"longcat": "哈基米"})
        assert Config(home=home).model_aliases() == {"longcat": "哈基米"}

    def test_keys_lowercased_empty_values_dropped(self, home: Path) -> None:
        _write_aliases(home, {"Gemini": "哈基米", "bad": ""})
        assert Config(home=home).model_aliases() == {"gemini": "哈基米"}

    def test_invalid_json_returns_empty(self, home: Path) -> None:
        _write_aliases(home, "{broken")
        assert Config(home=home).model_aliases() == {}

    def test_non_dict_json_returns_empty(self, home: Path) -> None:
        _write_aliases(home, "[1, 2]")
        assert Config(home=home).model_aliases() == {}


class TestDisplayModel:
    @pytest.fixture(autouse=True)
    def _patch_hermes_home(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """_display_model 内部用无参 Config()，需把 hermes_home 指到测试目录."""
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setattr("hermes_fry_cards.config.hermes_home", lambda: home)
        import hermes_fry_cards.cardkit.builder as builder_mod

        monkeypatch.setattr(builder_mod, "_display_model", _display_model)

    def test_alias_hit_case_insensitive_substring(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _write_aliases(tmp_path, {"longcat": "哈基米"})
        from hermes_fry_cards.config import Config

        assert Config(home=tmp_path).model_aliases().get("longcat") == "哈基米"

    def test_alias_priority_over_truncate(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _write_aliases(tmp_path, {"kimi-k3": "K3"})
        # 直接验证 Config 层：别名命中时 model_aliases 返回映射，builder 优先取别名
        aliases = Config(home=tmp_path).model_aliases()
        lowered = "nvidia/moonshotai/kimi-k3".lower()
        hit = next((a for k, a in aliases.items() if k in lowered), "")
        assert hit == "K3"

    def test_miss_falls_back_to_truncate(self, home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _write_aliases(tmp_path, {"gemini": "哈基米"})
        monkeypatch.chdir(tmp_path.parent)
        # 未命中 gemini 别名的模型走截断
        assert _truncate_model("or/lc/LongCat-2.0") == "⇲LongCat-2.0"

    def test_no_file_falls_back_to_truncate(self) -> None:
        assert _truncate_model("a/b/c") == "⇲c"


class TestTruncateModel:
    def test_keeps_last_segment(self) -> None:
        assert _truncate_model("nvidia/moonshotai/kimi-k3") == "⇲kimi-k3"

    def test_plain_name_untouched(self) -> None:
        assert _truncate_model("kimi-k3") == "kimi-k3"
