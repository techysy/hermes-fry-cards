#!/usr/bin/env bash
# 🍟 hermes-fry-cards — one-line installer
#
#   curl -fsSL https://raw.githubusercontent.com/techysy/hermes-fry-cards/main/install.sh | bash
#
# What it does:
#   1. Locates Hermes's own venv Python (the plugin MUST live there)
#   2. Installs this package into it (pip, editable off a temp clone by default)
#   3. Verifies compatibility against the running Hermes
#   4. Injects the hooks (uninstall + install, so upgrades re-patch cleanly)
#
# It does NOT restart the gateway — you must do that yourself, because the
# restart kills the process running this script if it was started from inside
# a gateway turn.
#
# Env overrides:
#   HERMES_PYTHON=/path/to/python3   skip auto-detection
#   FRY_REF=v0.3.3                   install a specific tag/branch (default: main)
#   FRY_SKIP_HOOKS=1                 install the package only, skip patch/reverify
set -euo pipefail

REPO="techysy/hermes-fry-cards"
REF="${FRY_REF:-main}"

say()  { printf '\033[1;36m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- Hermes venv
find_hermes_python() {
  if [ -n "${HERMES_PYTHON:-}" ]; then
    [ -x "$HERMES_PYTHON" ] || die "HERMES_PYTHON is not executable: $HERMES_PYTHON"
    printf '%s\n' "$HERMES_PYTHON"; return
  fi

  # 1. Ask the `hermes` launcher which interpreter it uses.
  if command -v hermes >/dev/null 2>&1; then
    local launcher py
    launcher="$(command -v hermes)"
    # `exec "/path/to/python3" ...` is how the launcher is written
    py="$(grep -oE 'exec "[^"]+"' "$launcher" 2>/dev/null | head -1 | sed 's/exec "//;s/"$//' || true)"
    if [ -n "$py" ] && [ -x "$py" ]; then printf '%s\n' "$py"; return; fi
  fi

  # 2. Per-user default install location.
  for cand in \
    "$HOME/.hermes/hermes-agent/venv/bin/python3" \
    "${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python3"
  do
    [ -x "$cand" ] && { printf '%s\n' "$cand"; return; }
  done

  # 3. Last resort: a python3 that can already import hermes_agent.
  if command -v python3 >/dev/null 2>&1; then
    if python3 -c 'import hermes_agent' >/dev/null 2>&1; then
      command -v python3; return
    fi
  fi

  return 1
}

say "Locating Hermes's Python..."
HERMES_PYTHON="$(find_hermes_python)" \
  || die "Could not find Hermes's venv. Set HERMES_PYTHON=/path/to/python3 and re-run."
ok "Hermes Python: $HERMES_PYTHON"

"$HERMES_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Hermes Python is older than 3.11 — upgrade Hermes first."
ok "Python: $("$HERMES_PYTHON" --version 2>&1)"

# ------------------------------------------------------------------- install
# Prefer an existing local checkout (developer machines); otherwise clone.
SRC_DIR=""
if [ -f "./pyproject.toml" ] && grep -q 'name = "hermes-fry-cards"' ./pyproject.toml 2>/dev/null; then
  SRC_DIR="$(pwd)"
  say "Using local checkout: $SRC_DIR"
elif [ -n "${FRY_SRC_DIR:-}" ] && [ -f "${FRY_SRC_DIR}/pyproject.toml" ]; then
  SRC_DIR="$FRY_SRC_DIR"
  say "Using provided FRY_SRC_DIR: $SRC_DIR"
else
  command -v git >/dev/null 2>&1 || die "git is required to auto-clone. Install git, or \`cd\` into a checkout first."
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  say "Cloning $REPO@$REF ..."
  git clone --depth 1 --branch "$REF" "https://github.com/$REPO.git" "$TMP/src" >/dev/null 2>&1 \
    || die "Clone failed (ref: $REF). Check your network / proxy."
  SRC_DIR="$TMP/src"
  ok "Cloned to a temp dir (removed on exit)"
fi

say "Installing into Hermes's venv..."
"$HERMES_PYTHON" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || warn "pip self-upgrade skipped"
"$HERMES_PYTHON" -m pip install --quiet "$SRC_DIR" \
  || "$HERMES_PYTHON" -m pip install --quiet -e "$SRC_DIR" \
  || die "pip install failed."
ok "Package installed: $("$HERMES_PYTHON" -m pip show hermes-fry-cards 2>/dev/null | awk '/^Version:/{print $2}')"

if [ "${FRY_SKIP_HOOKS:-0}" = "1" ]; then
  warn "FRY_SKIP_HOOKS=1 — skipping verify + hook injection"
  exit 0
fi

# ------------------------------------------------------- verify & inject hooks
say "Verifying compatibility with this Hermes build..."
if ! "$HERMES_PYTHON" -m hermes_fry_cards verify; then
  die "verify failed: this Hermes version is not supported. Nothing was patched.
   Please open an issue with the output above: https://github.com/$REPO/issues"
fi
ok "All injection targets compatible"

say "Injecting hooks (uninstall first, so upgrades re-patch cleanly)..."
"$HERMES_PYTHON" -m hermes_fry_cards uninstall >/dev/null 2>&1 || true
"$HERMES_PYTHON" -m hermes_fry_cards install || die "Hook injection failed."

say "Final status:"
"$HERMES_PYTHON" -m hermes_fry_cards status

cat <<'EOF'

──────────────────────────────────────────────────────────────
🍟 Installed. One last step — restart the gateway YOURSELF,
   from a terminal OUTSIDE this script (the restart would kill it):

       hermes gateway restart

   Then send a message in Feishu: the reply should stream into a card.
──────────────────────────────────────────────────────────────
EOF
