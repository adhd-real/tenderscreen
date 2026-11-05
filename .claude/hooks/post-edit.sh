#!/bin/bash
# PostToolUse hook: after Claude edits a Python file, format and lint that file
# and run the fast tests. Exit code 2 sends the problems back to Claude to fix.
set -uo pipefail

project_dir="${CLAUDE_PROJECT_DIR:-$(pwd)}"
file_path="$(python3 -c 'import json, sys
print(json.load(sys.stdin).get("tool_input", {}).get("file_path", ""))' 2>/dev/null)"

# Only Python files inside this repository.
case "$file_path" in
  "$project_dir"/*.py) ;;
  *) exit 0 ;;
esac
[ -f "$file_path" ] || exit 0

bin="$project_dir/.venv/bin"
if [ ! -x "$bin/ruff" ] || [ ! -x "$bin/pytest" ]; then
  echo "post-edit hook: .venv is missing. Run: CLAUDE_CODE_REMOTE=true .claude/hooks/session-start.sh" >&2
  exit 2
fi
cd "$project_dir" || exit 2

report=""
add_failure() { report+=$'\n'"== $1 =="$'\n'"$2"$'\n'; }

# Formatting is safe to apply automatically.
if ! out="$("$bin/ruff" format --quiet "$file_path" 2>&1)"; then
  add_failure "ruff format $file_path" "$out"
fi

# Lint is report-only (no --fix), so an import added in one edit is not
# deleted before the next edit uses it.
if ! out="$("$bin/ruff" check --quiet "$file_path" 2>&1)"; then
  add_failure "ruff check $file_path" "$out"
fi

# Fast tests only: tests marked slow (real-Tesseract OCR, files of 1 MB or
# more) are left to plain `pytest`, which the check before every commit and CI
# run. --no-cov: the 90% coverage gate applies to the full suite, not a subset.
if ! out="$("$bin/pytest" -x -q --no-header -m "not slow" --no-cov 2>&1)"; then
  add_failure "pytest -x -m 'not slow' (last 60 lines)" "$(tail -n 60 <<<"$out")"
fi

if [ -n "$report" ]; then
  echo "post-edit checks failed after editing $file_path:$report" >&2
  exit 2
fi
exit 0
