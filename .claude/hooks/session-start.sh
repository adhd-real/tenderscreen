#!/bin/bash
# SessionStart hook: every Claude Code cloud session starts in a fresh VM, so
# install TenderScreen's Python dependencies into .venv and put it on PATH.
set -euo pipefail

# Only run in Claude Code cloud sessions.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"
venv="$(pwd)/.venv"

# Create the venv, or recreate it if its interpreter no longer runs.
if ! "$venv/bin/python" -c 'import sys' >/dev/null 2>&1; then
  rm -rf "$venv"
  python3 -m venv "$venv"
fi

# Reinstall only when pyproject.toml or this script changed since the last
# successful install. The hash file lives inside .venv, so it vanishes with it.
hashes="$venv/.session-start.sha256"
if ! sha256sum --check --status "$hashes" 2>/dev/null; then
  rm -f "$hashes" # a failed install must run again next session
  "$venv/bin/python" -m pip install --quiet --disable-pip-version-check -e '.[dev]' >&2
  sha256sum pyproject.toml .claude/hooks/session-start.sh >"$hashes"
fi

# Every session: make Claude's shell commands use the venv's pinned tools.
if [ -n "${CLAUDE_ENV_FILE:-}" ] && ! grep -qsF "$venv/bin" "$CLAUDE_ENV_FILE"; then
  {
    echo "export VIRTUAL_ENV=\"$venv\""
    echo "export PATH=\"$venv/bin:\$PATH\""
  } >>"$CLAUDE_ENV_FILE"
fi

echo "TenderScreen: dev environment ready; .venv/bin (ruff, mypy, pytest, pip-audit) is on PATH."
