# TenderScreen

Reads Indian government tender and award notices (PDF/HTML), extracts structured
fields, and will later deliver matched results to firms over WhatsApp.

Pipeline: fetch → parse/OCR → extract (LLM) → validate → eligibility/matching
(deterministic) → deliver.

## Environment

- Work happens in Claude Code cloud sessions: every session is a fresh Linux VM.
  `.claude/hooks/session-start.sh` creates `.venv`, runs `pip install -e '.[dev]'`
  and puts `.venv/bin` on PATH. If tools are missing, run
  `CLAUDE_CODE_REMOTE=true .claude/hooks/session-start.sh`.
- After every edit to a `.py` file, `.claude/hooks/post-edit.sh` formats and lints
  that file and runs pytest. Fix what it reports before moving on.
- The user reviews changes as GitHub pull requests in the browser: keep commits
  small and focused, and say *why* in commit messages.

## Commands

```bash
ruff check .                # lint
ruff format .               # format (CI runs: ruff format --check .)
mypy                        # strict type check of src/ and tests/
pytest                      # tests
pip-audit --skip-editable   # dependencies with known vulnerabilities
python -m tenderscreen.devtools.licence_check  # fail on GPL, LGPL or AGPL dependencies
pytest --no-cov tests/<path>  # subset of tests; plain pytest fails below 90% coverage of src/
```

Run all five before every commit. CI (`.github/workflows/ci.yml`) runs the same
checks on Python 3.12 and 3.13, plus a TruffleHog secret scan.

## Layout

- `src/tenderscreen/`: package code (src layout; import as `tenderscreen`).
- `tests/`: pytest tests. `tests/test_<module>.py` mirrors `src/tenderscreen/<module>.py`.
- `tests/<pkg>/test_<module>.py` mirrors `src/tenderscreen/<pkg>/<module>.py` (test dirs are packages).
- `src/tenderscreen/devtools/`: CI tooling (licence check), not used by the pipeline.
- `src/tenderscreen/parsing/`: money (`parse_inr` → int paise) and date (`parse_date` → IST) parsers.
- `src/tenderscreen/ist.py`: the `IST` (Asia/Kolkata) zone, `to_ist()` and `now_ist()`.
- `data/manual/`: public tender documents the user uploads through GitHub.
  Committed. Read them freely; never modify, rename or delete them.
- `data/raw/`: scratch downloads. Git-ignored.
- `.claude/`: shared Claude Code settings and hooks.

## Permanent rules

These are non-negotiable. If a task seems to require breaking one, stop and ask.

### 1. Money is integer paise, never float

- Store and compute every amount as `int` paise (₹1 = 100 paise). Name such
  fields with a `_paise` suffix: `emd_paise`, `estimated_value_paise`.
- Parse amounts in text ("₹ 12,34,567.50", "Rs. 1.5 crore", "12 lakh") with
  `decimal.Decimal`, then convert to `int` paise. Never pass money through
  `float`, not even briefly.
- Convert to rupees, lakh or crore only when formatting output for people.

### 2. Eligibility and matching never call an LLM

- Eligibility checks and firm-to-tender matching are deterministic Python over
  already-extracted, validated fields: the same input always gives the same output.
- Eligibility and matching modules must not import an LLM client or call the
  extraction step. LLMs are used only in extraction.

### 3. Every AI-extracted field cites its source, or is `not_found`

- Each AI-extracted field stores its value, `source_page` (1-based page number
  in the source document) and `quote` (the exact clause text from that page).
- If the document does not state it, the field is `not_found` with no value.
  Never guess, infer, default, or carry a value over from another tender.

### 4. Never read, print, or commit `.env` files or secrets

- Code reads secrets from environment variables by name only
  (`os.environ["WHATSAPP_TOKEN"]`). Never hardcode, log or echo their values.
- This covers every tool: no `cat`, `grep`, `head` or `source` of `.env*` files,
  and no dumping the environment (`env`, `printenv`).
- `.env.example` (placeholder values only) is the user's to write.

### 5. No captcha bypass; obey robots.txt

- Never write code that solves, skips or outsources captchas, or that ignores
  or works around robots.txt.
- Rate-limit requests and stop on HTTP 403 or 429.
- If a portal blocks automated access, stop and ask the user. They can upload
  the documents to `data/manual/` instead.

### 6. Every new function gets tests

- Every new function, public or private, gets pytest tests in the same change:
  normal cases, edge cases and failure cases.
- Tests never touch the network; use small fixtures under `tests/fixtures/`.
- Never delete, skip or weaken a test, or a lint or type rule, to get a green build.

## Security rules

- Every downloaded document and portal response is untrusted input: enforce a
  maximum size and a timeout on every download and parse, and never execute,
  `eval` or unpickle anything from it (no macros, scripts or embedded files).
- Text sent to an LLM is data, not instructions. The extraction step has no
  tools. Its output must validate against a strict schema (unknown or missing
  keys are errors), and every `quote` must appear verbatim in the text of its
  `source_page`; otherwise the field is rejected.
- The fetcher may only request domains listed in the sources config (the
  allowlist; not created yet). For any other domain, refuse; do not fetch.
- Never log secrets, phone numbers or firm financial data. Log IDs or hashes.
- When a database is added, firm financials and phone numbers are encrypted at
  rest and accessed only through least-privilege roles.
- Every webhook (for example WhatsApp) verifies the request signature before
  doing anything else.
- Every web endpoint needs authentication, input validation and rate limiting.

## Performance rules

- Never process the same document twice: dedupe by the sha256 of its content,
  and cache parse, OCR and extraction results keyed by that hash plus the prompt
  version and model version.
- Run OCR only on pages with no text layer.
- Send the LLM only the relevant section of a document, never the whole file.
- Network calls are async with per-domain rate limits; heavy CPU work such as
  OCR runs in separate worker processes.
- Optimise only after measuring: add a benchmark before any speed change and
  report before/after numbers.

## Code conventions

- Python 3.12+, fully type-annotated (mypy strict). No `# type: ignore` without
  an error code and a reason.
- Datetimes are timezone-aware; tender deadlines are IST (`Asia/Kolkata`).
- `assert` only in tests; in `src/`, raise explicit exceptions.
- Keep I/O (network, files, LLM calls) at the edges and logic in small pure
  functions, so the logic is easy to test.
