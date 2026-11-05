# TenderScreen

Reads Indian government tender and award notices, extracts structured fields
(with the source page and quoted clause for every AI-extracted value), and will
later deliver results over WhatsApp.

## Development

Claude Code cloud sessions install everything automatically through
`.claude/hooks/session-start.sh`. To set up by hand:

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

OCR needs Tesseract with English and Hindi, and the Noto fonts used by the Hindi
tests (CI installs them; without them OCR tests are skipped locally):

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-hin fonts-noto-core
```

Run all checks (the same ones CI runs):

```bash
ruff check . && ruff format --check . && mypy && pytest && pip-audit --skip-editable \
  && python -m tenderscreen.devtools.licence_check
```

Project rules for contributors and for Claude live in [CLAUDE.md](CLAUDE.md).
