# 🍟 hermes-fry-cards (薯条卡片)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/Hermes-%E2%89%A50.14.0-2463eb)](https://github.com/NousResearch/hermes-agent)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.11-blue)](https://www.python.org/)

[Hermes](https://github.com/NousResearch/hermes-agent) Gateway plugin for real-time streaming Feishu/Lark CardKit v2.0 cards.

> 🍟 **Fork of [hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming)**: adds merged tool-use panels and further refinements.

[中文文档](README.md) · [Installation Guide](INSTALL.md)

![](assets/cover.jpg)

---

## Features

| Capability | Description |
|------------|-------------|
| 🎴 Streaming card | Real-time typewriter effect; dynamically renders thinking / tool / answer elements in event arrival order |
| 🔧 Tool-use tracking | Live tool call status with standard icons, result/error blocks |
| 🧠 Reasoning display | Shows model thinking/reasoning via tags, prefixes, or native API blocks |
| 📊 Completion card | Final card with token usage, duration, and context window info |
| 🖼️ Image resolution | Auto-detects markdown image refs, downloads & re-uploads as Feishu img_key |
| 🛡️ Message guard | Auto-terminates updates when message is deleted/recalled |
| ⏱ Throttled updates | 100ms throttle balances real-time feel vs API rate limits |
| 📤 Auto card split | Splits into multiple cards near Feishu's 200-element limit |
| 🔔 Cron delivery | Delivers scheduled job results as Feishu cards with Markdown |
| 🔙 Background delivery | Delivers `/background` (`/btw`) task results as cards |
| 🌐 Bilingual | Card text auto-switches based on Feishu client language |
| 🎨 Customizable | Header/footer, text sizes, width mode, footer fields |

---

## Card Rendering

The plugin dynamically renders thinking, tool call, and answer elements in event arrival order. Multi-round content stays in its actual order.

When approaching Feishu's 200-element limit, it auto-splits: old card is sealed with complete data, new card continues, only the last card carries the footer.

![](assets/streaming.jpg)

---

## Requirements

- Hermes `>= 0.14.0` (2026.5.16) with Feishu/Lark platform configured
- `Python >= 3.11`
- `lark-oapi >= 1.4.0` — Feishu/Lark official Python SDK
- `PyYAML >= 6.0` — YAML parser
- Feishu app permissions: CardKit read/write, message send & reply, image upload

---

## Installation

👉 For the full installation procedure (manual + AI Agent paths), see **[INSTALL.md](INSTALL.md)**.

### AI Agent one-liner

```
curl https://raw.githubusercontent.com/techysy/hermes-fry-cards/main/INSTALL.md
```

Have your AI agent read and execute the guide.

---

## Configuration

Add to `~/.hermes/config.yaml`:

```yaml
streaming:
  enabled: true
```

### Credentials

| Priority | Source | Variables |
|----------|--------|-----------|
| 1 | Environment | `FEISHU_APP_ID` / `FEISHU_APP_SECRET` (or `LARK_APP_ID` / `LARK_APP_SECRET`) |
| 2 | Config file | `feishu` or `lark` section in `~/.hermes/config.yaml` |

### Card Style

```yaml
streaming:
  enabled: true
  width_mode: default
  header:
    enabled: true
  body:
    text_size: normal_v2
  footer:
    enabled: true
    text_size: notation
    fields:
      - [status, elapsed, context, model]
    show_label: false
  panel_expanded: false
display:
  platforms:
    feishu:
      show_tool_use: true
      show_reasoning: true   # Show reasoning process (default: false, enable manually)
```

| Option | Description | Default |
|--------|-------------|---------|
| `header.enabled` | Status header bar (blue/green/red by state) | `false` |
| `footer.enabled` | Footer metadata bar | `true` |
| `body.text_size` | Answer body text size | `normal_v2` |
| `footer.text_size` | Footer text size | `notation` |
| `footer.fields` | Footer field layout (2D array) | `[status, elapsed, context, model]` |
| `footer.show_label` | Show field labels | `false` |
| `panel_expanded` | Keep completion panels expanded | `false` |
| `width_mode` | Card width mode (`default` / `compact` / `fill`) | `default` |
| `show_tool_use` | Show tool-use panels | `true` |
| `show_reasoning` | Show reasoning process (false drops reasoning, card may not update) | `false` |

---

## CLI Commands

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m hermes_fry_cards verify     # Verify compatibility (no file changes)
$HERMES_PYTHON -m hermes_fry_cards install    # Inject hooks
$HERMES_PYTHON -m hermes_fry_cards uninstall  # Remove hooks
$HERMES_PYTHON -m hermes_fry_cards restore    # Restore from backup
$HERMES_PYTHON -m hermes_fry_cards status     # Show status
```

---

## Update

```bash
cd hermes-fry-cards
git pull
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m pip install -e .
$HERMES_PYTHON -m hermes_fry_cards uninstall
$HERMES_PYTHON -m hermes_fry_cards verify
$HERMES_PYTHON -m hermes_fry_cards install
hermes gateway restart
```

---

## Uninstall

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
$HERMES_PYTHON -m hermes_fry_cards uninstall
$HERMES_PYTHON -m pip uninstall hermes-fry-cards
```

---

## How It Works

The plugin injects hook calls into `gateway/run.py` and `cron/scheduler.py` via AST patching. All business logic lives in the `hermes_fry_cards` package.

```
User sends message
  → Card session created
  → Streaming updates (tool status, text — throttled)
  → Image URL async resolution
  → Completion card (tokens, duration, context)
```

If a message is deleted/recalled, updates are auto-terminated.

**Interrupt handling:**
- `/stop` abort — card shows interrupted state:

![](assets/abort.jpg)

- Message interrupt — old card shows interrupted state, new streaming card auto-created:

![](assets/interrupt.jpg)

---

## Notes

- `install` modifies `~/.hermes/hermes-agent/gateway/run.py` and `cron/scheduler.py`; `.hermes_lark.bak` backups are created automatically
- Re-run `verify` + `install` after Hermes updates
- The plugin complements the built-in Feishu adapter: plugin handles streaming cards, built-in adapter handles message routing
- Only affects Feishu/Lark platform — other platforms are unaffected

---



---

## License

[MIT](LICENSE)
