# 🍟 hermes-fry-cards (薯条卡片)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/Hermes-%E2%89%A50.14.0-2463eb)](https://github.com/NousResearch/hermes-agent)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.11-blue)](https://www.python.org/)
[![当前版本](https://img.shields.io/badge/Release-v0.1.0--rc2-2463eb?logo=github&logoColor=white)](https://github.com/techysy/hermes-fry-cards/releases)

> 🍟 Hermes Gateway plugin for real-time streaming Feishu/Lark CardKit v2.0 cards

[中文文档](README.md) · [Installation Guide](INSTALL.md)

![cover](assets/cover.jpg)

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

---

## 🚀 Quick Install

### AI Agent one-liner

```
curl https://raw.githubusercontent.com/techysy/hermes-fry-cards/main/INSTALL.md
```

### Manual install

```bash
git clone https://github.com/techysy/hermes-fry-cards.git
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

## 📜 Attribution

Inspired by [hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming) (author Cheerwhy), licensed under MIT. This is an independent development with architecture rewrite and feature enhancements.

## 📄 License

[MIT](LICENSE)
