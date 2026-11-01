# AGENTS.md

## Project

Hermes Gateway plugin that injects hooks into `~/.hermes/hermes-agent/gateway/run.py` and `cron/scheduler.py` via AST patching to provide real-time streaming Feishu/Lark CardKit v2.0 cards with typewriter effect.

## Commands

```bash
# All commands must use Hermes's venv Python
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3

$HERMES_PYTHON -m hermes_fry_cards verify     # Check compatibility (safe, no file changes)
$HERMES_PYTHON -m hermes_fry_cards install    # Inject hooks into run.py and cron/scheduler.py
$HERMES_PYTHON -m hermes_fry_cards uninstall  # Remove hooks
$HERMES_PYTHON -m hermes_fry_cards restore    # Restore from .hermes_lark.bak backup
$HERMES_PYTHON -m hermes_fry_cards status     # Show patch status
$HERMES_PYTHON -m hermes_fry_cards studio     # Visual config studio (0.0.0.0:8765, local 127.0.0.1:8765)

# Install for development
$HERMES_PYTHON -m pip install -e .
$HERMES_PYTHON -m pip install -e ".[dev]"  # test dependencies

# Lint
$HERMES_PYTHON -m ruff check hermes_fry_cards tests
$HERMES_PYTHON -m mypy hermes_fry_cards/

# Run tests (local run.py first, CI auto-downloads from GitHub)
$HERMES_PYTHON -m pytest tests/ -q
```

## Architecture

```
gateway/run.py (Hermes)
  └─ AST-injected hooks (patcher.py defines markers + injection logic)
       │
       ├─ on_feishu_normalize   → patch.on_feishu_normalize() (inline, fixes false thread_id)
       ├─ on_message_started    → controller.on_message_started()
       ├─ on_tool_updated       → controller.on_tool_update()
       ├─ on_answer_delta       → controller.on_answer()
       ├─ on_thinking_delta     → controller.on_thinking()
       ├─ on_reasoning_delta    → controller.on_reasoning()
       ├─ on_background_review_message → controller.defer_background_review()
       ├─ on_message_interrupted → controller.on_interrupted()
       ├─ on_queued_followup_boundary → patch.on_queued_followup_boundary() (finalize card before drain, set response_previewed/already_sent)
       ├─ on_queued_followup_card_ready → patch.on_queued_followup_card_ready() (pre-create/reuse the follow-up turn's card before the recursion, #37)
       ├─ on_queued_followup_completed → patch.on_queued_followup_completed() (seal the follow-up turn's own card, suppress its plain-text send, #35)
       ├─ on_queued_followup_result   → patch.on_queued_followup_result() (carry deepest completion ID through recursive merge)
       ├─ on_message_completed_wait → controller.on_completed_wait()
       ├─ on_message_aborted    → controller.on_aborted()
       ├─ on_session_aborted    → controller.on_session_aborted() (busy-session /stop)
       ├─ on_background_deliver → controller.on_background_deliver()
       ├─ on_busy_ack           → controller.on_busy_ack() (busy receipt into notice slot, #43)
       ├─ on_turn_redirected    → patch.on_turn_redirected() (redirect: switch cards; else merge notice, #43)
       ├─ on_turn_redirected (REDIRECT_RESOLVED, optional) → same, injected after the _steer unpack BEFORE the 30s busy-ack debounce so a second consecutive interrupt still switches cards (#43)
       ├─ on_background_completion (BG_COMPLETION, optional) → controller.on_background_completion() in
       │    gateway/run_notifications.py's _inject_watch_notification — watch/completion notices merge into
       │    the live card (session_key resolution) or go out as a standalone static card; True skips the
       │    plain-text synthetic injection (#47). Delegation-type completion events carry no
       │    command/started_at fields, so background_completion_text's fallback must not render the
       │    gateway's internal marker line ([ASYNC DELEGATION BATCH COMPLETE …], #54) — bracketed
       │    uppercase-marker first lines fall back to the generic ✅ 后台任务已完成
       └─ on_session_compressed → controller.on_compression() (optional, seal card on context compaction)

cron/scheduler.py (Hermes)
  └─ CronPatcher (patcher.py) injects on_cron_deliver into _deliver_result
       └─ intercepts feishu/lark targets → build_cron_card → send_card_to_chat

plugins/platforms/feishu/adapter.py (Hermes)
  └─ ClarifyPatcher (patcher.py) appends a marker block at module scope
       └─ clarify.apply_patch() monkey-patches send_clarify + wraps
          _on_card_action_trigger → interactive option buttons, multi-select
          toggles, "其他" inline input (form container) + toast feedback.
          Also wraps _handle_approval_card_action: Hermes gates approval
          clicks with _allow_group_message (group admission policy), which
          rejects every human click on deployments without per-chat rules;
          the wrapper re-gates with _is_interactive_operator_authorized via
          a thread-local bypass (async resolver's second check preserved)
          and adds resolved/expired/unauthorized/deny toast feedback.
          Runtime patch only (no AST surgery); uninstall = block delete.
          State lives in clarify._CLARIFY_STATE (FIFO-capped).

StreamCardController (singleton, controller.py)
  ├─ CardSession per message (state machine: IDLE→CREATING→STREAMING→COMPLETED/FAILED/ABORTED)
  │   └─ stream segments: CardSession.segment_state (SegmentState)
  ├─ _session_keys — Hermes session_key → active CardSession mapping for precise /stop handling
  ├─ _interrupt_map — old_message_id → new_message_id mapping for interrupt redirect
  ├─ FlushController (streaming/flush.py) — throttles CardKit updates (500ms)
  ├─ ToolUseTracker (streaming/tooluse.py) — tracks tool call lifecycle with icon/status mapping
  ├─ UnavailableGuard (streaming/unavailable_guard.py) — auto-terminates on message delete/recall
  └─ ImageResolver (streaming/image.py) — async download + re-upload markdown images as Feishu img_key

Streaming card runtime (streaming/)
  ├─ controller.py — StreamingController: create card, flush, split/rollover, and cron delivery orchestration
  ├─ session.py — CardSession per message (state machine: IDLE→CREATING→STREAMING→COMPLETED/FAILED/ABORTED)
  ├─ segments.py — SegmentState: flat segment list (reasoning / answer / tool), same-type appends, cross-type creates new
  ├─ segment_helper.py — CardKit action builders, element estimates, and tool split point selection
  ├─ text.py — reasoning tag parsing and final answer text cleanup
  ├─ flush.py — FlushController: throttles CardKit updates (base = streaming.flush_interval_ms, default 500ms;
  │              adaptive down-throttle: single stream_element full text >8KB → ≥1s, >16KB → ≥2s (#52, judged by
  │              the largest single element, not the flush cumulative sum); flush payload >16KB → ≥500ms,
  │              >40KB → ≥800ms (#46); element tiers take precedence; never below base)
  ├─ tooluse.py — ToolUseTracker: tool call lifecycle tracking with icon/status mapping
  ├─ image.py — ImageResolver: async download + re-upload markdown images as Feishu img_key
  └─ unavailable_guard.py — UnavailableGuard: auto-terminates on message delete/recall

FeishuClient (feishu.py) — lark-oapi SDK wrapper
  ├─ CardKit streaming API — update single elements at 100ms intervals

Card templates (cardkit/)
  ├─ builder.py — builds Feishu card JSON
  │   ├─ _build_header — card-level header with status-based theming (blue/green/red)
  │   ├─ build_streaming_card_v2 — initial streaming CardKit v2 card (header_enabled, text_size)
  │   ├─ build_complete_card — final card, renders segments in order (header_enabled, body_text_size, footer_enabled, footer_text_size, panel_fields)
  │   │   └─ unified panel header + footer share one field pool (status/elapsed/speed/cache/tokens/context/model);
  │   │      panel adds exclusive reasoning/tools counts; driven by streaming.footer.fields / display.platforms.feishu.panel_fields
  │   ├─ build_cron_card — static card for cron delivery
  │   └─ build_background_card — static card for background task delivery
  ├─ markdown.py — CardKit markdown normalization and table/image helpers
  └─ i18n.py — localized CardKit labels

Studio (studio/ — visual config workbench, stdlib http.server + native JS only)
  ├─ server.py — whitelist validation / refuse-write (409) / backup rotation / atomic merge;
  │              real-builder preview (4 scenarios × streaming/completed/error); structured status collect
  └─ web/ — index.html + style.css + preview.js (CardKit JSON→DOM renderer) + app.js (form/status/save)
```

## Key Constraints

- Hermes `>= 0.14.0` (2026.5.16) required. `patcher.py` targets specific function names in Hermes's `gateway/run.py` (`_handle_message_with_agent`, `progress_callback`, `_stream_delta_cb`, `_interim_assistant_cb`) and `cron/scheduler.py` (`_deliver_result`). If Hermes changes these, `verify` will catch it.
- The interrupt hook is injected at the `"Restart typing indicator"` comment in `_run_agent`. It fires when `was_interrupted and next_message_id` are both truthy. The `_interrupt_map` redirects completion from `old_id` to the new session, handling nested interrupts (A→B→C).
- The completion hook installed into `gateway/run.py` is async: `on_message_completed_wait` awaits queued CardKit creation/finalization before setting `already_sent`. Upgrades must rerun `uninstall` + `install` so older sync completion hooks are removed from Hermes gateway.
- The `_thinking_hook` has a `not already_streamed` guard (patcher.py:103) — thinking deltas are skipped once answer streaming has begun.
- Tool panel renders only the last `MAX_TOOL_PANEL_STEPS` (30) steps (#46): header keeps the total count, older steps collapse into a one-line summary. `estimate_tool_elements` applies the same cap so split/rollover accounting stays consistent — light-step panels become bounded and stop splitting; heavy-step panels still split via the (uncapped-below-30) threshold math.
- Reasoning text is clamped to `_REASONING_MAX_BYTES` (10KB, `cardkit/markdown.py`) with a head-summary + tail window (#52) in both full-text resend paths: streaming `cardkit_stream_element` (unbounded reasoning reached 28.8KB per push / 1.3MB per turn, saturating Feishu client rendering) and the completion card's unified reasoning panels (would otherwise approach the ~30KB card JSON limit stacked on the clamped answer). `FlushController.adapt_payload_size` also gains element-size tiers (>8KB → ≥1s, >16KB → ≥2s) keyed on the largest single element sent this flush — the payload cumulative sum would mis-bucket many small elements.
- Completion cards render tool groups compact (`_build_tool_panel(compact=True)`: title rows only, no detail/output; #53) because the full-card rebuild resubmits panel sub-elements the server already counted from streaming history, crossing the 200-element hard limit → 300305. When completion still hits 300305, `_do_complete_card_inner` strips the unified panel (`strip_unified_panel`, matched by `UNIFIED_PANEL_ELEMENT_ID`) and retries once — body and status take priority — before the overflow fallback. The fallback replace advances `session.sequence += 2`: the loading-icon delete already consumed N+1, reusing it returns 300317 and the fallback card never lands.
- The NORMALIZE hook (`on_feishu_normalize`) is injected at `source = event.source` in `_handle_message`, before any other processing. It detects Feishu quoted messages with a false `thread_id` (set by the Feishu adapter but absent in raw event) and clears it, preventing `_reply_anchor_for_event` from returning the wrong ID.
- The `anchor_id` mechanism: for Feishu quoted messages, `_reply_anchor_for_event(event)` returns `reply_to_message_id` instead of `event.message_id`. The START hook passes both — `message_id` for session identity and streaming callback lookup, `anchor_id` for card delivery (reply target). Sessions are registered under both keys.
- Reasoning display depends on upstream providing `<thinking>`/`<thought>`/`<antthinking>` tags or `Reasoning:\n` prefix in text. Native API reasoning blocks (Anthropic extended thinking, DeepSeek reasoning_content) are available via `on_reasoning_delta` hook when `display.platforms.feishu.show_reasoning` is enabled.
- CardKit v2.0 elements (collapsible_panel, streaming_mode) only work with `"schema": "2.0"` cards.
- Streaming cards use a single CardKit card for the message lifecycle: elements are dynamically created in event arrival order. When CardKit creation fails, the plugin yields to the Hermes Gateway default reply.
- The follow-up drain hooks manage card lifecycle for Hermes's queued follow-up messages (triggered when `busy_text_mode: queue` or `busy_input_mode: queue`). `on_queued_followup_boundary` is injected at `was_interrupted = result.get("interrupted")` in `_run_agent` — it finalizes the current card and sets `response_previewed`/`already_sent` on the result dict before the drain loop processes the queued message. `on_queued_followup_result` is injected at `return _preserve_queued_followup_history_offset(...)` and uses `setdefault` to carry the deepest `_hermes_lark_completion_id` back through the recursive merge chain.
- The COMPLETE hook uses `_lark_completion_id = agent_result.get('_hermes_lark_completion_id') or event.message_id` — in follow-up scenarios the deepest message_id propagates up via `on_queued_followup_result`, ensuring the correct card session is finalized. Non-follow-up scenarios fall back to `event.message_id`.
- The background deliver hook (`on_background_deliver`) is injected in `_run_background_task` after `adapter.extract_images(response)`. It uses `ReplyMessage` API with `event_message_id` as anchor, so cards land in the correct topic. On success, `text_content` is cleared to avoid duplicate text delivery, while images and media files continue through the original Hermes loops. On failure, the original Hermes delivery logic runs as fallback.
- Notices (#43) render Hermes's text raw — no `💡 提示` label. Zones: in-progress notices (kind `busy`/`redirect`) go to the card BOTTOM (streaming: before loading; completed: before footer), post-turn receipts (kind `background`) stay at the TOP. `on_interrupted` clears the old card's progress notices on redirect so the sentence is not rendered twice.
- Synthetic turn ids (background completion receipts, e.g. `["completion", <task>, <ts>]`) are detected in `on_message_started` (`_is_synthetic_turn_id`) and treated like `syn-*` turns: the card is sent via `send_card_to_chat` instead of `reply_card_by_id` (which would fail 99992354); the id is kept as the session key.
- The answer hook marks Hermes's stream consumer via `patch.mark_stream_delivered` when a delta is consumed: it installs `has_durably_delivered_text` (the duck-typed query point in `_run_agent_stream_confirmed_final_delivery`) backed by `controller.stream_card_healthy`, so `_run_agent_mark_streamed_delivery` takes the suppression branch instead of logging the "Normal final-send NOT suppressed" duplicate-risk warning. Synthetic turns carry no `event_message_id` — the mark and the health query both fall back to `session_key` (#48). `on_completed_wait` marks the text fallback when sealing fails so the gateway's `already_sent` is undone and the final text still delivers.
- Group tool gating (#55) is injected before the `return enabled, disabled` in `_resolve_turn_toolsets` (gateway/run_turn.py; MODULAR-ONLY, OPTIONAL anchor). When `gateway.group_security_boundary.enabled`, non-admin group turns drop the built-in write/execute toolsets (`execute_code`/`terminal`/`file`/`computer_use` — query-class toolsets stay available); `non_admin_allowed_toolsets` is a strict allowlist, `non_admin_disabled_toolsets` extends the default deny, and the denylist always wins over the allowlist so a config typo cannot reopen the boundary. Admin resolution reuses Hermes's slash-command policy (`gateway.slash_access.policy_for_source(self.config, source).is_admin`); when it cannot resolve (module missing / `allow_admin_from` unset for the scope / exception) the gate fails OPEN — never lock out the owner. Cached agents keep build-time toolsets: restart the gateway (or `/new` the group session) after enabling.
- Completion-failure diagnostics log `recursive=` (full-tree element count, `count_elements_recursive`, #56) next to `card_elements` — Feishu's 300305 counts the whole card tree, so the top-level `len(body.elements)` undercounts badly when the unified panel nests tool/reasoning panels.
- Commit messages: body should use bullet list format (unnumbered `- item`).
