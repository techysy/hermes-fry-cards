"""Injector compatibility against the real Hermes v0.22.0 sources (#34 / #35).

The hand-written fixtures in ``test_patcher_modular.py`` are minimal and can drift from
upstream without anyone noticing. These tests run the real patcher over the actual
``gateway/run_turn.py`` and ``gateway/run_turn_runner.py`` from the ``v0.22.0`` release,
so a Hermes bump that moves an injection anchor fails here rather than at runtime on a
live gateway.

Source resolution follows the same convention as ``test_patcher.py``: prefer the local
install, else fetch a **pinned tag** (never a moving branch), else skip.

Both follow-up hooks anchor on the same ``_preserve_queued_followup_history_offset``
line, so ordering is load-bearing: ``FOLLOWUP_DONE`` must land first — it writes
``_hermes_lark_completion_id`` = the card it actually sealed, and ``FOLLOWUP_RESULT``'s
``setdefault`` must not overwrite that with the raw inbound id.
"""
from __future__ import annotations

import ast
import shutil
import urllib.request
from pathlib import Path

import pytest

from hermes_fry_cards.patcher import (
    MARKERS,
    MK_FOLLOWUP_COMPLETE,
    MK_FOLLOWUP_COMPLETE_END,
    MK_FOLLOWUP_DONE,
    MK_FOLLOWUP_RESULT,
    MK_TOOL_GATING,
    MK_TOOL_GATING_END,
    OPTIONAL_HOOKS,
    Patcher,
)


def _marker_name(begin: str) -> str:
    """``# HERMES_LARK_BUSY_ACK_BEGIN`` → ``busy_ack``（与 OPTIONAL_HOOKS 同键）."""
    return begin.removeprefix("# HERMES_LARK_").removesuffix("_BEGIN").lower()


OPTIONAL_MARKERS = OPTIONAL_HOOKS

# Pinned release, not a moving branch: a main-branch fixture would silently drift.
_HERMES_TAG = "v0.22.0"
_BASE_URL = f"https://raw.githubusercontent.com/NousResearch/hermes-agent/{_HERMES_TAG}/gateway"

_HERMES_HOME = Path.home() / ".hermes" / "hermes-agent" / "gateway"
SAMPLES_DIR = Path(__file__).parent / "samples" / f"modular_{_HERMES_TAG}"


_REQUIRED_SYMBOL = {
    "run_turn.py": "_run_agent",
    "run_turn_runner.py": "_run_agent",
    "run_busy.py": "_handle_active_session_busy_message",
}


def _is_pristine(path: Path) -> bool:
    """样本必须是上游原样：带注入 marker 或残缺的副本会让回归测试测到错的东西.

    曾经出现过样本缓存被某次 tests 运行写入了 marker，导致 apply→remove 的对比
    永远不相等（且把污染当成产品缺陷）。
    """
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
    """Local install first, then a pinned-tag download, else skip."""
    local = _HERMES_HOME / name
    if _is_pristine(local):
        return local

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    dest = SAMPLES_DIR / name
    if _is_pristine(dest):
        return dest
    # 缓存被污染（例如旧版本测试直接对样本原地打过补丁）：丢掉重新下载
    dest.unlink(missing_ok=True)
    dest.with_name(dest.name + ".hermes_lark.bak").unlink(missing_ok=True)

    try:
        urllib.request.urlretrieve(f"{_BASE_URL}/{name}", dest)
    except Exception as exc:  # offline / rate-limited / moved file
        pytest.skip(f"{name} not available locally and download failed: {exc}")
    if not _is_pristine(dest):
        dest.unlink(missing_ok=True)
        pytest.skip(f"{name} download returned empty or already-patched file")
    return dest


@pytest.fixture()
def real_v022(tmp_path: Path) -> dict[str, Path]:
    turn_src = _ensure_sample("run_turn.py")
    runner_src = _ensure_sample("run_turn_runner.py")
    busy_src = _ensure_sample("run_busy.py")

    # Only these two are stubbed — the patcher needs their entry points present, and
    # neither hosts an anchor under test.
    paths = {
        "inbound": tmp_path / "run_inbound.py",
        "turn": tmp_path / "run_turn.py",
        "runner": tmp_path / "run_turn_runner.py",
        "busy": tmp_path / "run_busy.py",
    }
    paths["inbound"].write_text(
        "async def _handle_message(self, event):\n"
        "    _admitted = await self._admit(event)\n"
        "    source = event.source\n",
        encoding="utf-8",
    )
    # 真实模块：它同时承载 stop / 忙碌回执 / 重定向三个锚点（#43），
    # 写成桩会让这些锚点漂移了也发现不了。
    shutil.copy2(busy_src, paths["busy"])
    shutil.copy2(turn_src, paths["turn"])
    shutil.copy2(runner_src, paths["runner"])
    return paths


def test_every_injection_anchor_exists_in_v022(real_v022: dict[str, Path]) -> None:
    """If a Hermes release moves an anchor, verify must fail loudly — never silently skip."""
    Patcher(modular_paths=real_v022).verify_target()


def test_apply_is_valid_python_and_installs_each_marker_once(real_v022: dict[str, Path]) -> None:
    patcher = Patcher(modular_paths=real_v022)
    patcher.apply()

    assert patcher.is_fully_patched()
    for path in real_v022.values():
        ast.parse(path.read_text(encoding="utf-8"))

    combined = "\n".join(p.read_text(encoding="utf-8") for p in real_v022.values())
    for begin, end in MARKERS:
        if _marker_name(begin) in OPTIONAL_MARKERS:
            # #43 可选锚点：0.22.0 样本的 busy 模块没有回执回调，跳过注入
            continue
        assert combined.count(begin) == 1, f"{begin} installed {combined.count(begin)}x"
        assert combined.count(end) == 1, f"{end} installed {combined.count(end)}x"


def test_followup_done_seals_card_before_merge(real_v022: dict[str, Path]) -> None:
    """#35: the follow-up's own card is sealed before the chain merges its result.

    Both follow-up hooks must land before the merge line, and only after
    ``followup_result`` is bound by the recursive ``_run_agent`` call.
    """
    Patcher(modular_paths=real_v022).apply()
    lines = real_v022["turn"].read_text(encoding="utf-8").splitlines()

    done = next(i for i, line in enumerate(lines) if MK_FOLLOWUP_DONE in line)
    result = next(i for i, line in enumerate(lines) if MK_FOLLOWUP_RESULT in line)
    merge = next(
        i for i, line in enumerate(lines)
        if "_preserve_queued_followup_history_offset(result, followup_result)" in line
        and not line.strip().startswith("# HERMES_LARK")
    )
    bound = next(i for i, line in enumerate(lines) if "followup_result = await self._run_agent(" in line)

    assert bound < done < merge, "hook must sit between the recursive call and the merge"
    assert result < merge
    assert done < result, "FOLLOWUP_DONE first, so FOLLOWUP_RESULT's setdefault can't clobber its id"

    body = "\n".join(lines[done:merge])
    assert "on_queued_followup_completed" in body, "must seal the follow-up turn's card"
    assert "already_sent" in body and "final_response" in body, "must suppress the plain-text send"
    # An event-less follow-up carries no inbound id; it must reuse the card the boundary
    # pre-created for this turn (falling back to creating one when there is none). (#37)
    assert "on_queued_followup_card_ready" in body and "message_id=None" in body


def test_followup_card_precreated_before_recursion(real_v022: dict[str, Path]) -> None:
    """#37 duplicate delivery: the follow-up turn's card exists BEFORE it starts running.

    Without a card at stream time Hermes streams that turn's reply as plain text, and the
    completion hook then adds a card carrying the same text — the reply lands twice.
    """
    Patcher(modular_paths=real_v022).apply()
    lines = real_v022["turn"].read_text(encoding="utf-8").splitlines()

    complete = next(i for i, line in enumerate(lines) if MK_FOLLOWUP_COMPLETE in line)
    complete_end = next(i for i, line in enumerate(lines) if MK_FOLLOWUP_COMPLETE_END in line)
    bound = next(i for i, line in enumerate(lines) if "followup_result = await self._run_agent(" in line)

    body = "\n".join(lines[complete:complete_end])
    assert "on_queued_followup_card_ready" in body, "boundary must pre-create the follow-up card"
    assert "on_queued_followup_boundary" in body, "boundary must still seal the previous card"
    assert body.index("on_queued_followup_boundary") < body.index("on_queued_followup_card_ready"), (
        "seal the previous card first, then pre-create this turn's"
    )
    assert complete < bound, "pre-create must land before the recursive _run_agent call"
    assert "self._session_key_for_source" in body, "pre-create must key the card by the follow-up's session"


def test_apply_is_idempotent_and_remove_restores(real_v022: dict[str, Path]) -> None:
    originals = {k: p.read_text(encoding="utf-8") for k, p in real_v022.items()}
    patcher = Patcher(modular_paths=real_v022)

    patcher.apply()
    installed = {k: p.read_text(encoding="utf-8") for k, p in real_v022.items()}
    patcher.apply()
    assert installed == {k: p.read_text(encoding="utf-8") for k, p in real_v022.items()}

    patcher.remove()
    assert originals == {k: p.read_text(encoding="utf-8") for k, p in real_v022.items()}


def test_busy_notice_anchors_on_real_busy_module(real_v022: dict[str, Path]) -> None:
    """#43: 真实 run_busy.py 上忙碌回执 / 重定向锚点落在文案合成之后、发送之前。"""
    patcher = Patcher(modular_paths=real_v022)
    patcher.apply()
    lines = real_v022["busy"].read_text(encoding="utf-8").splitlines()

    from hermes_fry_cards.patcher import MK_BUSY_ACK, MK_REDIRECT

    composed = next(
        i for i, line in enumerate(lines)
        if line.strip().startswith("message = self._compose_busy_ack_message(")
    )
    redirect = next(i for i, line in enumerate(lines) if MK_REDIRECT in line)
    ack = next(i for i, line in enumerate(lines) if MK_BUSY_ACK in line)
    send = next(i for i, line in enumerate(lines) if "_send_busy_ack_reply(event, adapter, message)" in line
                and not line.strip().startswith("#"))

    assert composed < redirect, "redirect hook must see the composed message"
    assert redirect < ack, "redirect takes precedence: it runs first and returns nothing"
    assert ack < send, "notice must land before the placeholder receipt is sent"
    body = "\n".join(lines[redirect:ack])
    assert "redirected=('redirected' in locals() and bool(redirected))" in body
    assert "injected hook failed: redirect" in body


# ── Hermes v0.22.3+：忙碌回执宿主拆分（#43 回归） ──────────────────────────────

_SAMPLE_225 = SAMPLES_DIR.parent / "modular_v0.22.5"
_MODULES_225 = ("run_turn.py", "run_turn_runner.py", "run_turn_progress.py",
                "run_turn_persistence.py", "run_busy.py", "run_notifications.py")


@pytest.fixture()
def real_v0225(tmp_path: Path) -> dict[str, Path]:
    """真实 v0.22.5 源码树：0.22.3 起忙碌回执拆进 ``_busy_interrupt_and_ack``."""
    if not all((_SAMPLE_225 / name).exists() for name in _MODULES_225):
        pytest.skip("v0.22.5 samples not available")
    paths = {}
    for key, name in zip(
        ("turn", "runner", "progress", "persistence", "busy", "notifications"),
        _MODULES_225, strict=True,
    ):
        destination = tmp_path / name
        shutil.copy2(_SAMPLE_225 / name, destination)
        paths[key] = destination
    paths["inbound"] = tmp_path / "run_inbound.py"
    paths["inbound"].write_text(
        "async def _handle_message(self, event):\n"
        "    _admitted = await self._admit(event)\n"
        "    source = event.source\n",
        encoding="utf-8",
    )
    return paths


def test_v0225_installs_busy_notice_hooks(real_v0225: dict[str, Path]) -> None:
    """0.22.5 把回执发送挪到 `_busy_interrupt_and_ack`，锚点仍要能找到（#43）."""
    from hermes_fry_cards.patcher import MK_BUSY_ACK, MK_REDIRECT

    patcher = Patcher(modular_paths=real_v0225)
    patcher.verify_target()
    patcher.apply()

    busy_lines = real_v0225["busy"].read_text(encoding="utf-8").splitlines()
    host = next(
        i for i, line in enumerate(busy_lines)
        if line.strip().startswith("async def _busy_interrupt_and_ack(")
    )
    send = next(
        i for i, line in enumerate(busy_lines)
        if "_send_busy_ack_reply(event, adapter, message)" in line
        and not line.strip().startswith("#")
    )
    redirect = next(i for i, line in enumerate(busy_lines) if MK_REDIRECT in line)
    ack = next(i for i, line in enumerate(busy_lines) if MK_BUSY_ACK in line)

    assert host < redirect < ack < send, "两个钩子必须落在新宿主函数内、发送之前"
    assert patcher.is_fully_patched()


def test_v0225_remove_restores_busy_module(real_v0225: dict[str, Path]) -> None:
    original = (_SAMPLE_225 / "run_busy.py").read_text(encoding="utf-8")
    patcher = Patcher(modular_paths=real_v0225)
    patcher.apply()
    patcher.remove()
    assert real_v0225["busy"].read_text(encoding="utf-8") == original


def _assert_redirect_resolved_before_debounce(busy_lines: list[str], busy_file: Path) -> None:
    """REDIRECT_RESOLVED 必须赶在 30s 防抖之前：连续第二次打断才有卡可换（#43 反馈）."""
    from hermes_fry_cards.patcher import MK_REDIRECT_RESOLVED

    resolved = next(
        i for i, line in enumerate(busy_lines) if MK_REDIRECT_RESOLVED in line
    )
    steer_unpack = next(
        i for i, line in enumerate(busy_lines)
        if "effective_mode, redirected = _steer.effective_mode" in line
    )
    debounce = next(
        i for i, line in enumerate(busy_lines)
        if "busy_ack_ts if _busy_state else 0" in line
    )
    assert steer_unpack < resolved < debounce, (
        f"{busy_file.name}: hook must sit between the _steer unpack and the ack debounce"
    )
    body = "\n".join(busy_lines[resolved:debounce])
    assert "redirected=bool(redirected)" in body
    assert "text=''" in body


def test_v022_redirect_resolved_anchors_before_debounce(real_v022: dict[str, Path]) -> None:
    Patcher(modular_paths=real_v022).apply()
    _assert_redirect_resolved_before_debounce(
        real_v022["busy"].read_text(encoding="utf-8").splitlines(),
        real_v022["busy"],
    )


def test_v0225_redirect_resolved_anchors_before_debounce(real_v0225: dict[str, Path]) -> None:
    """0.22.3+ 宿主拆进 `_busy_interrupt_and_ack`，锚点跟着搬，仍在防抖之前."""
    Patcher(modular_paths=real_v0225).apply()
    _assert_redirect_resolved_before_debounce(
        real_v0225["busy"].read_text(encoding="utf-8").splitlines(),
        real_v0225["busy"],
    )


def test_v0225_bg_completion_anchors_before_internal_marker(real_v0225: dict[str, Path]) -> None:
    """#47: 真实 run_notifications.py 上，通知接管挂在内部标记处理之前."""
    from hermes_fry_cards.patcher import MK_BG_COMPLETION, MK_BG_COMPLETION_END

    patcher = Patcher(modular_paths=real_v0225)
    patcher.apply()
    lines = real_v0225["notifications"].read_text(encoding="utf-8").splitlines()

    host = next(
        i for i, line in enumerate(lines)
        if line.strip().startswith("async def _inject_watch_notification(")
    )
    begin = next(i for i, line in enumerate(lines) if MK_BG_COMPLETION in line)
    end = next(i for i, line in enumerate(lines) if MK_BG_COMPLETION_END in line)
    # 注意：812 行的 lambda（_self_post_api_server）也含同样的 needle，取 hook 之后的第一个
    marker = next(
        i for i, line in enumerate(lines)
        if i > end and "_mark_internal_notification(synth_text)" in line and not line.strip().startswith("#")
    )
    assert host < begin < end < marker, "hook must live inside _inject_watch_notification, before the marker"

    body = "\n".join(lines[begin:end])
    assert "session_key=(str(_lark_evt_key) if _lark_evt_key else None)" in body
    assert "return True" in body
    assert patcher.is_fully_patched()


# ── 真实 run_notifications.py：turn_busy 纯文本回执抑制锚点（0.5.9.3） ──────

_LIVE_NOTIFICATIONS_CANDIDATES = (
    # 归档的干净样张（从真实 0.22.5 部署取回，未注入）——Windows 开发机也跑得动
    _SAMPLE_225 / "run_notifications.py",
    Path("/opt/hermes/gateway/run_notifications.py"),
    Path.home() / ".hermes" / "hermes-agent" / "gateway" / "run_notifications.py",
)


def _pristine_notifications(tmp_path: Path) -> Path:
    """本机安装的 ``run_notifications.py`` 去掉已注入块 → 上游原样副本（缺失则 skip）."""
    from hermes_fry_cards.patcher import (
        MARKERS,
        _remove_block,
    )

    for candidate in _LIVE_NOTIFICATIONS_CANDIDATES:
        if not candidate.exists():
            continue
        source = candidate.read_text(encoding="utf-8")
        for begin, end in MARKERS:
            source = _remove_block(source, begin, end)
        if "HERMES_LARK" in source:
            continue
        destination = tmp_path / "run_notifications.py"
        destination.write_text(source, encoding="utf-8")
        return destination
    pytest.skip("pristine run_notifications.py not available")


def test_bg_busy_suppress_anchors_on_real_notifications_module(tmp_path: Path) -> None:
    """真实源码上：抑制块落在 concise 文案之后、``_send_watcher_message`` 之前."""
    from hermes_fry_cards.patcher import (
        MK_BG_BUSY_SUPPRESS,
        MK_BG_BUSY_SUPPRESS_END,
        _bg_busy_suppress_hook,
        _find_bg_busy_suppress_site,
    )

    source_path = _pristine_notifications(tmp_path)
    source = source_path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    site = _find_bg_busy_suppress_site(ast.parse(source), lines)
    assert site is not None, "concise 回执锚点在真实源码上找不到"

    index, indent = site
    patched = "".join(lines[:index]) + _bg_busy_suppress_hook(indent) + "".join(lines[index:])
    ast.parse(patched)  # break 必须落在循环体内，缩进必须成立

    block = patched[patched.index(MK_BG_BUSY_SUPPRESS):patched.index(MK_BG_BUSY_SUPPRESS_END)]
    assert "suppress_busy_receipt" in block
    assert patched.index(MK_BG_BUSY_SUPPRESS) > patched.index(
        'self._format_process_final_message(session_id, session, "concise")'
    )
    assert patched.index(MK_BG_BUSY_SUPPRESS) < patched.index(
        "await self._send_watcher_message(platform_name, chat_id, thread_id, message_text, watcher)"
    )


# ── #55 群聊工具隔离：真实 run_turn.py 的 _resolve_turn_toolsets 锚点 ─────────

def test_tool_gating_anchors_before_toolsets_return_v022(real_v022: dict[str, Path]) -> None:
    patcher = Patcher(modular_paths=real_v022)
    patcher.verify_target()
    patcher.apply()
    lines = real_v022["turn"].read_text(encoding="utf-8").splitlines()

    host = next(
        i for i, line in enumerate(lines)
        if line.strip().startswith("def _resolve_turn_toolsets(")
    )
    begin = next(i for i, line in enumerate(lines) if MK_TOOL_GATING in line)
    end = next(i for i, line in enumerate(lines) if MK_TOOL_GATING_END in line)
    ret = next(
        i for i, line in enumerate(lines)
        if i > end and line.strip() == "return enabled, disabled"
    )
    assert host < begin < end < ret, "tool_gating hook must live inside _resolve_turn_toolsets, before the return"
    body = "\n".join(lines[begin:end])
    assert "apply_group_tool_gating(" in body
    assert "enabled, disabled = _lark_tg" in body
    assert patcher.is_fully_patched()


def test_tool_gating_anchors_before_toolsets_return_v0225(real_v0225: dict[str, Path]) -> None:
    patcher = Patcher(modular_paths=real_v0225)
    patcher.apply()
    lines = real_v0225["turn"].read_text(encoding="utf-8").splitlines()
    host = next(
        i for i, line in enumerate(lines)
        if line.strip().startswith("def _resolve_turn_toolsets(")
    )
    begin = next(i for i, line in enumerate(lines) if MK_TOOL_GATING in line)
    end = next(i for i, line in enumerate(lines) if MK_TOOL_GATING_END in line)
    assert host < begin < end, "tool_gating hook must land inside _resolve_turn_toolsets"
    assert patcher.is_fully_patched()
