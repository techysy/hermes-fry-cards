"""Injector compatibility against the real Hermes v0.23.0 sources.

Hermes v0.23.0 refactored background delivery logic into `gateway/run_background.py`
(GatewayBackgroundMixin) and separated progress and persistence mixins.
These tests verify that Patcher correctly identifies and injects all required and optional
hooks across the modular layout of Hermes v0.23.0.
"""
from __future__ import annotations

import ast
import shutil
import urllib.request
from pathlib import Path

import pytest

from hermes_fry_cards.patcher import (
    MARKERS,
    MK_BG_DELIVER,
    OPTIONAL_HOOKS,
    Patcher,
)


def _marker_name(begin: str) -> str:
    """``# HERMES_LARK_BUSY_ACK_BEGIN`` → ``busy_ack``（与 OPTIONAL_HOOKS 同键）."""
    return begin.removeprefix("# HERMES_LARK_").removesuffix("_BEGIN").lower()


OPTIONAL_MARKERS = OPTIONAL_HOOKS

_HERMES_TAG = "v0.23.0"
_BASE_URL = f"https://raw.githubusercontent.com/NousResearch/hermes-agent/{_HERMES_TAG}/gateway"

_HERMES_HOME = Path.home() / ".hermes" / "hermes-agent" / "gateway"
SAMPLES_DIR = Path(__file__).parent / "samples" / f"modular_{_HERMES_TAG}"


_REQUIRED_SYMBOL = {
    "run_turn.py": "_run_agent",
    "run_turn_runner.py": "_run_agent",
    "run_busy.py": "_handle_active_session_busy_message",
    "run_background.py": "_run_background_task",
    "run_turn_progress.py": "progress_callback",
    "run_turn_persistence.py": "hooks.emit",
    "run_notifications.py": "_inject_watch_notification",
}


def _is_pristine(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    required = _REQUIRED_SYMBOL.get(path.name)
    if required is None:
        return "HERMES_LARK" not in source
    return "HERMES_LARK" not in source and required in source


def _ensure_sample(name: str) -> Path:
    local = _HERMES_HOME / name
    if _is_pristine(local):
        return local

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    dest = SAMPLES_DIR / name
    if _is_pristine(dest):
        return dest
    dest.unlink(missing_ok=True)
    dest.with_name(dest.name + ".hermes_lark.bak").unlink(missing_ok=True)

    try:
        urllib.request.urlretrieve(f"{_BASE_URL}/{name}", dest)
    except Exception as exc:
        pytest.skip(f"{name} not available locally and download failed: {exc}")
    if not _is_pristine(dest):
        dest.unlink(missing_ok=True)
        pytest.skip(f"{name} download returned empty or already-patched file")
    return dest


@pytest.fixture()
def real_v023(tmp_path: Path) -> dict[str, Path]:
    turn_src = _ensure_sample("run_turn.py")
    runner_src = _ensure_sample("run_turn_runner.py")
    busy_src = _ensure_sample("run_busy.py")
    bg_src = _ensure_sample("run_background.py")
    prog_src = _ensure_sample("run_turn_progress.py")
    pers_src = _ensure_sample("run_turn_persistence.py")
    notif_src = _ensure_sample("run_notifications.py")

    paths = {
        "inbound": tmp_path / "run_inbound.py",
        "turn": tmp_path / "run_turn.py",
        "runner": tmp_path / "run_turn_runner.py",
        "busy": tmp_path / "run_busy.py",
        "background": tmp_path / "run_background.py",
        "progress": tmp_path / "run_turn_progress.py",
        "persistence": tmp_path / "run_turn_persistence.py",
        "notifications": tmp_path / "run_notifications.py",
    }
    paths["inbound"].write_text(
        "async def _handle_message(self, event):\n"
        "    _admitted = await self._admit(event)\n"
        "    source = event.source\n",
        encoding="utf-8",
    )
    shutil.copy2(turn_src, paths["turn"])
    shutil.copy2(runner_src, paths["runner"])
    shutil.copy2(busy_src, paths["busy"])
    shutil.copy2(bg_src, paths["background"])
    shutil.copy2(prog_src, paths["progress"])
    shutil.copy2(pers_src, paths["persistence"])
    shutil.copy2(notif_src, paths["notifications"])
    return paths


def test_every_injection_anchor_exists_in_v023(real_v023: dict[str, Path]) -> None:
    """All injection targets must be discovered and verified in Hermes v0.23.0."""
    Patcher(modular_paths=real_v023).verify_target()


def test_apply_is_valid_python_and_installs_each_marker_once_in_v023(real_v023: dict[str, Path]) -> None:
    patcher = Patcher(modular_paths=real_v023)
    patcher.apply()

    assert patcher.is_fully_patched()
    for path in real_v023.values():
        ast.parse(path.read_text(encoding="utf-8"))

    combined = "\n".join(p.read_text(encoding="utf-8") for p in real_v023.values())
    for begin, end in MARKERS:
        if _marker_name(begin) in OPTIONAL_MARKERS and combined.count(begin) == 0:
            continue
        assert combined.count(begin) == 1, f"{begin} installed {combined.count(begin)}x"
        assert combined.count(end) == 1, f"{end} installed {combined.count(end)}x"


def test_bg_deliver_injected_into_background_module_in_v023(real_v023: dict[str, Path]) -> None:
    """In Hermes 0.23, bg_deliver hook must land in run_background.py."""
    patcher = Patcher(modular_paths=real_v023)
    patcher.apply()

    turn_text = real_v023["turn"].read_text(encoding="utf-8")
    bg_text = real_v023["background"].read_text(encoding="utf-8")

    assert MK_BG_DELIVER not in turn_text, "bg_deliver should not be in run_turn.py for 0.23"
    assert MK_BG_DELIVER in bg_text, "bg_deliver must land in run_background.py for 0.23"
    assert "on_background_deliver" in bg_text


def test_apply_is_idempotent_and_remove_restores_in_v023(real_v023: dict[str, Path]) -> None:
    originals = {k: p.read_text(encoding="utf-8") for k, p in real_v023.items()}
    patcher = Patcher(modular_paths=real_v023)

    patcher.apply()
    installed = {k: p.read_text(encoding="utf-8") for k, p in real_v023.items()}
    patcher.apply()
    assert installed == {k: p.read_text(encoding="utf-8") for k, p in real_v023.items()}

    patcher.remove()
    assert originals == {k: p.read_text(encoding="utf-8") for k, p in real_v023.items()}
