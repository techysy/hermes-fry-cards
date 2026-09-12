# Installation Guide — 🍟 hermes-fry-cards

> Dual-path installation: choose **Manual** or **AI Agent auto-install**.

## Requirements

- Hermes Agent `>= 0.14.0` installed (`hermes --version` works)
- Feishu/Lark platform configured in Hermes
- Feishu app permissions: CardKit read/write, message send & reply, image upload

---

## Path A — One-line install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/techysy/hermes-fry-cards/main/install.sh | bash
```

The script does everything in Path C below — locates Hermes's venv Python, installs the package, runs `verify`, and injects the hooks — then tells you to restart the gateway.

Useful knobs:

```bash
# Pin a version (default: main)
curl -fsSL .../install.sh | FRY_REF=v0.3.3 bash

# Override the interpreter if auto-detection misses
curl -fsSL .../install.sh | HERMES_PYTHON=/path/to/python3 bash

# Install the package only, skip verify + hook injection
curl -fsSL .../install.sh | FRY_SKIP_HOOKS=1 bash
```

> The script never restarts the gateway for you — a restart would kill the process
> running it when invoked from inside a gateway turn. Run `hermes gateway restart`
> yourself afterwards.

---

## Path B — AI Agent auto-install

Have your Hermes-connected AI agent execute:

```
curl https://raw.githubusercontent.com/techysy/hermes-fry-cards/main/INSTALL.md
```

The agent will read this guide and perform the steps automatically. Review its actions before the hook-injection step if you prefer manual confirmation.

---

## Path C — Manual (step by step)

### Step 1 — Clone the repo

```bash
git clone https://github.com/techysy/hermes-fry-cards.git
cd hermes-fry-cards
```

### Step 2 — Locate Hermes's Python

The plugin MUST be installed into Hermes's own venv:

```bash
HERMES_PYTHON=$(grep -oE 'exec "[^"]+"' "$(which hermes)" | sed 's/exec "//;s/"//')
HERMES_PYTHON=$(dirname "$HERMES_PYTHON")/python3

# Fallback (per-user default)
[ -z "$HERMES_PYTHON" ] || [ ! -x "$HERMES_PYTHON" ] && HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3

# Verify
"$HERMES_PYTHON" --version
```

### Step 3 — Install the plugin

```bash
"$HERMES_PYTHON" -m pip install -e .
```

### Step 4 — Verify environment

```bash
"$HERMES_PYTHON" -m hermes_fry_cards status
"$HERMES_PYTHON" -m hermes_fry_cards verify
```

- `status` must show `Hermes Python:` pointing to `$HERMES_PYTHON` with no warnings
- `verify` must print `Compatible.` for both targets

If `verify` reports `Incompatible:`, the Hermes version is unsupported — stop here.

### Step 5 — Configure credentials

```bash
# Option A — ~/.hermes/.env (recommended)
cat >> ~/.hermes/.env <<'EOF'
FEISHU_APP_ID=cli_xxxxx
FEISHU_APP_SECRET=xxxxx
EOF
chmod 600 ~/.hermes/.env
```

```yaml
# Option B — ~/.hermes/config.yaml
feishu:
  app_id: cli_xxxxx
  app_secret: xxxxx
```

Enable streaming in the same config:

```yaml
streaming:
  enabled: true
```

### Step 6 — Install hooks

```bash
"$HERMES_PYTHON" -m hermes_fry_cards install
```

This patches `gateway/run.py` and `cron/scheduler.py` in place. Backups are created as `.hermes_lark.bak`.

### Step 7 — Restart gateway

```bash
hermes gateway restart
```

### Step 8 — Post-install check

```bash
"$HERMES_PYTHON" -m hermes_fry_cards status
```

All hooks should read `installed`, and `Feishu credentials:` should read `configured`.

---

## Uninstall

```bash
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
"$HERMES_PYTHON" -m hermes_fry_cards uninstall
"$HERMES_PYTHON" -m pip uninstall hermes-fry-cards
```

---

## Update

```bash
cd hermes-fry-cards
git pull
HERMES_PYTHON=~/.hermes/hermes-agent/venv/bin/python3
"$HERMES_PYTHON" -m pip install -e .
"$HERMES_PYTHON" -m hermes_fry_cards uninstall
"$HERMES_PYTHON" -m hermes_fry_cards verify
"$HERMES_PYTHON" -m hermes_fry_cards install
hermes gateway restart
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: hermes_fry_cards` | Plugin not in Hermes's Python | Reinstall using exact `HERMES_PYTHON` from Step 2 |
| `status` shows `warning: running under ...` | CLI invoked with wrong interpreter | Rerun with the `$HERMES_PYTHON` path shown |
| `verify` reports `Incompatible:` | Hermes version unsupported | Check Hermes `>= 0.14.0`; wait for plugin update |
| `gateway/run.py not found` | Hermes layout not recognized | Run `cat "$(which hermes)"` to find venv, set manually |
| Credentials `MISSING` in `status` | Env/config not set or not persisted | Complete Step 5; restart gateway |
| Gateway fails to load after restart | Plugin installed in wrong venv | Confirm `status` shows no warning before restarting |
| Hooks gone after Hermes update | Hermes update overwrote patched files | Re-run `verify` + `install`, then restart |
