# 🍟 hermes-fry-cards (薯条卡片)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/Hermes-%E2%89%A50.14.0-2463eb)](https://github.com/NousResearch/hermes-agent)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.11-blue)](https://www.python.org/)
[![当前版本](https://img.shields.io/badge/Release-v0.1.0--rc2-2463eb?logo=github&logoColor=white)](https://github.com/techysy/hermes-fry-cards/releases)

> 🍟 Hermes Gateway plugin for real-time streaming Feishu/Lark CardKit v2.0 cards

[中文文档](README.md) · [Installation Guide](INSTALL.md)

![A compact Feishu card screenshot with a green completed status indicator, a small profile avatar, and a short chat message bubble. The header reads 回复 余师洋: 晚上好 and the status reads 已完成. The text in the message says 晚上好! 已切换到 nvidia/moonshot/kimi-k3, 有什么需要我处理的? The card sits on a light gray background with a clean, minimal interface and a calm, friendly tone.](assets/collapse.png)

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🎴 **Streaming card** | Real-time typewriter output; dynamically renders thinking / tools / answer in order |
| 🔧 **Merged tool-use panel** | Multiple tool calls combine into a single unified panel |
| 🧠 **Reasoning display** | Shows model thinking/reasoning via tags, prefixes, or native API blocks |
| 🎯 **Unified panel** | Reasoning + tools merged into one bottom panel with model name, round count, tool count, duration |
| 📊 **Completion card** | Final card with token usage, duration, context window |
| 🖼️ **Image resolution** | Auto-detects markdown image refs, downloads & re-uploads as Feishu img_key |
| 🛡️ **Message guard** | Auto-terminates updates when message is deleted/recalled |
| 📤 **Auto card split** | Splits into multiple cards near Feishu's 200-element limit |
| 🔔 **Cron delivery** | Delivers scheduled job results as Feishu cards with Markdown |
| 🔙 **Background delivery** | Delivers `/background` (`/btw`) task results as cards |
| 🌐 **Bilingual** | Card text auto-switches based on Feishu client language |
| 🎨 **Customizable** | Header/footer, text sizes, width mode, footer fields |
| 🎯 **Status border** | Header auto-colors by state: blue streaming, green completed, red error |

## 🏗️ Architecture

![hermes-fry-cards architecture: gateway events flow through the AST hook injection layer into StreamCardController, orchestrated by the streaming/ runtime with 100ms flush throttling; cardkit/ builds card JSON delivered via Feishu CardKit v2.0 API to streaming user cards; cron delivery and failure fallback paths are shown separately](assets/architecture.svg)

---

## 🚀 Quick Install

### AI Agent one-liner

```
curl https://raw.githubusercontent.com/techysy/hermes-fry-cards/main/INSTALL.md
```

### Manual install

```bash
# GitHub (primary)
git clone https://github.com/techysy/hermes-fry-cards.git
# or Gitee mirror (faster in CN)
git clone https://gitee.com/techysy/hermes-fry-cards.git

cd hermes-fry-cards
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -e .
$HERMES_PYTHON -m hermes_fry_cards verify
$HERMES_PYTHON -m hermes_fry_cards install
hermes gateway restart
```

---

## ⚙️ Configuration

```yaml
streaming:
  enabled: true
  header_enabled: true
  footer:
    enabled: true
    fields:
      - [status, elapsed, model, context]
display:
  platforms:
    feishu:
      show_tool_use: true
      show_reasoning: true   # Enable reasoning display
```

| Option | Description | Default |
|--------|-------------|---------|
| `header.enabled` | Status header bar | `false` |
| `footer.enabled` | Footer metadata bar | `true` |
| `panel_expanded` | Keep completion panels expanded | `false` |
| `width_mode` | Card width (`default` / `compact` / `fill`) | `default` |
| `show_tool_use` | Show tool-use panels | `true` |
| `show_reasoning` | Show reasoning process | `false` |

---

## 🖥️ CLI

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m hermes_fry_cards verify     # Verify compatibility
$HERMES_PYTHON -m hermes_fry_cards install    # Inject hooks
$HERMES_PYTHON -m hermes_fry_cards uninstall  # Remove hooks
$HERMES_PYTHON -m hermes_fry_cards status     # Show status
```

---

## 📝 Update / Uninstall

### Update

```bash
cd hermes-fry-cards && git pull
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -e .
$HERMES_PYTHON -m hermes_fry_cards uninstall
$HERMES_PYTHON -m hermes_fry_cards install
hermes gateway restart
```

### Uninstall

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m hermes_fry_cards uninstall
$HERMES_PYTHON -m pip uninstall hermes-fry-cards
```

---

## 🧠 How it Works

```
User sends message
  → Card session created
  → Streaming updates (tool status, text — throttled)
  → Image URL async resolution
  → Completion card (tokens, duration, context)
```

---

## 🔄 Differences from upstream hermes-lark-streaming

This project is independently developed based on [Cheerwhy/hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming) v0.12.0. The core streaming engine keeps the upstream AST hook injection architecture, enhanced in three layers:

### ✨ New features

| Feature | Description | Upstream |
|---------|-------------|----------|
| **Unified panel** | Merges reasoning + tool calls into a single bottom panel; `unified_panel_min_duration` auto-hides it when there are no tool calls and the reply is quick | ❌ Reasoning panels stack separately, verbose for multi-round chats |
| **Context progress bar** | Independent `show_context` switch + `context_display_mode` with three modes (`text` / gradient-shaded `bar` / `text_bar`), smart units (k below 1M) | ❌ Footer plain-text percentage only, no toggle |
| **Reasoning panel cap** | `max_reasoning_panels` (default 3); overflow merges into the last panel — supports models without segmented thinking (e.g. deepseek-v4-flash), prevents 300305 element overflow | ❌ Uncapped, long thinking always overflows |
| **Model name truncation** | `truncate_model_name`: `nvidia/moonshotai/kimi-k3` → `⇲kimi-k3`, fixes mobile line wrapping | ❌ Full name shown |
| **Model aliases** | Standalone JSON config at `~/.hermes/model_aliases.json`: `{"longcat": "哈基米", "gemini": "哈基米"}`; keys are case-insensitive substring-matched against the model name — a hit shows the alias (e.g. LongCat → 哈基米), a miss falls back to truncation; re-read on every render, effective immediately | ❌ None |

### Model alias config

Independent of `config.yaml`, aliases are written in `~/.hermes/model_aliases.json`:

```json
{
  "longcat": "哈基米",
  "gemini": "哈基米"
}
```

- **Match**: keys are case-insensitively substring-matched against the full model name (`longcat` → `or/lc/LongCat-2.0` hits)
- **Priority**: alias hit → show alias; miss → fall back to truncation
- **Hot reload**: re-read on every render, takes effect immediately on file change

> Full path: `~/.hermes/model_aliases.json`

Behavior default: `show_reasoning` defaults to **true** (upstream defaults to false).

### 🔧 Key fixes (upstream pitfalls)

- **300305 element-limit forced split recovery** — when total card elements exceed Feishu's hard limit, automatically seals the old card and migrates un-created segments to a new one to continue streaming; no longer stuck at "processing" forever
- **Remote image URL filtering** — works around CardKit rejecting remote URLs (`200570 invalid image keys`) by stripping un-uploadable references into code fences
- **Duplicate ID fix system for completion cards** — reasoning `text_el_id` reused end-to-end with indexed unique-ID fallback; upstream's fixed `reasoning_text` collides with streaming-phase elements and leaves cards stuck loading
- **Loading icon cleanup on seal/complete failure** — failed finalization no longer leaves a "processing" state behind

### 🏛️ Internal stability refactor (v0.1.1)

- Unified session lifecycle entry points (idempotent registration/cleanup), interrupt takeover no longer deletes new session mappings, terminated sessions can be rebuilt
- FlushController race protection: completion snapshot prevents redundant reflush, stale timer cancellation prevents double flush
- Traceable failure reasons via `mark_failed(reason=...)` + structured log events + `_yield_to_gateway` as the single fallback decision point
- 15 test files with 9 new stability regression tests; CONFIGURATION / TROUBLESHOOTING docs included

---

## 📄 Attribution

Inspired by [hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming) (author Cheerwhy), licensed under MIT. This is an independent development with architecture rewrite and feature enhancements.

## 📄 License

[MIT](LICENSE)
