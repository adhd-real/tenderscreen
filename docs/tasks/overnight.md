# Overnight task list

Saved verbatim from the user's prompt on 2026-11-04. Tick each task when done.
Re-read this file before starting each task. Item 3 of TASK A was added by the
user on 2026-11-05.

## Progress

- [x] Setup: branch, environment, docs/decisions.md
- [ ] TASK A: Quality gates (item 3 added 2026-11-05)
- [x] TASK B: Indian money and date parsing
- [x] TASK C: PDF text extraction and OCR
- [x] TASK D: Document store with dedupe and caching
- [x] TASK E: Evaluation harness
- [x] TASK F: Data check kit for manual research
- [ ] FINAL STEP: Morning report, push, pull request

## Working notes (for resuming after a restart or compaction)

- Branch `claude/bold-maxwell-8t0jxb`, built on the scaffold branch (see
  docs/decisions.md). A to F are committed and pushed, each followed by the
  fixes its fresh-context review found (C was reviewed twice); a sandbox race
  found by CI is fixed too.
- The morning report is docs/overnight-report.md; every unattended decision is
  in docs/decisions.md.

## Original prompt (verbatim)

You are working unattended overnight. I am asleep and cannot answer.

HOW TO WORK TONIGHT
- Never ask me questions or wait for approval. When something is
  unclear, choose the safest reasonable option, record it in
  docs/decisions.md (what you chose and why), and continue.
- First, save this entire prompt to docs/tasks/overnight.md. Tick off
  each task there when done, and re-read the file before starting each
  task, so nothing is lost if the conversation is compacted.
- Work on one new branch. After each task: run ruff, mypy, pytest and
  pip-audit and fix all failures; then have a reviewer subagent with
  fresh context check that task's diff against CLAUDE.md and the
  task's acceptance criteria; fix what it finds; commit; push.
- If a task still fails after a few honest attempts, record exactly
  what failed and what you tried, and move to the next task. Never
  weaken a test, lint rule, coverage threshold or CLAUDE.md rule to
  make something pass. Never edit the rules in CLAUDE.md; you may only
  add commands or layout lines.
- You have no internet access except package installs. Never try to
  reach government portals.
- Dependencies must have permissive licences only (MIT, BSD,
  Apache-2.0, ISC, PSF, HPND). No GPL, LGPL or AGPL; for example, do
  not use PyMuPDF. If only a non-permissive option exists, skip it and
  note it in the report.
- All test data is synthetic and generated inside the tests. Never
  commit real personal data.

TASK A: QUALITY GATES
1. Add pytest-cov; CI fails below 90% line coverage for src/.
2. Add a CI step that fails if any installed dependency has a GPL,
   LGPL or AGPL licence.
3. Add a "slow" pytest marker. The post-edit hook runs only fast tests
   (-m "not slow"); the per-task check and CI always run the full suite.
   Mark OCR and large-file tests as slow.
Acceptance: the same commands pass in this session.

TASK B: INDIAN MONEY AND DATE PARSING (src/tenderscreen/parsing/)
Tests first, then implementation. Use Decimal, never float.
parse_inr(text) returns integer paise, or None with a reason. Never
guess. Required cases:
  "Rs. 25.00 Lacs"   -> 250000000
  "2.5 Cr"           -> 2500000000
  "₹ 2,50,00,000"    -> 2500000000
  "INR 3.2 crore"    -> 3200000000
  "₹ 20441"          -> 2044100
  "₹ 1,234.50"       -> 123450
  "25 लाख"           -> 250000000
  "1 करोड़"           -> 1000000000
Also handle lakh, lakhs, lac, crores, Rs, Rs., INR and ₹ variants.
Return None with a reason for: empty text, negative amounts, more than
one amount in the string, amounts written in words, and anything
ambiguous.
parse_date(text) returns a timezone-aware datetime in Asia/Kolkata,
always day-first (Indian format). Handle "15-Oct-2026 05:00 PM",
"15-Oct-2026", "15/10/2026", "15.10.2026" and "2026-10-15". Reject
impossible dates such as 31/02/2026. It must work without system
timezone files (add tzdata).
Acceptance: at least 40 test cases, including every example above.

TASK C: PDF TEXT EXTRACTION AND OCR (src/tenderscreen/documents/)
- extract_pages(path) returns for each page: page number (from 1),
  text, method ("text_layer" or "ocr"), and a quality flag.
- A page has a text layer only if it yields meaningful text (use a
  sensible character threshold); otherwise OCR it with Tesseract,
  English plus Hindi.
- Render pages for OCR with a permissively licensed library such as
  pypdfium2, not poppler.
- Run OCR in worker processes, never one page at a time in sequence.
- Configurable limits with defaults of 100 MB and 1,000 pages per file
  and 60 seconds per page, each failing with a clear error.
- Tesseract is not preinstalled here. Try: apt-get update && apt-get
  install -y tesseract-ocr tesseract-ocr-hin fonts-noto-core. If that
  fails, still build the module, make OCR tests skip with a clear
  reason when Tesseract is missing, and state this in the report.
- Update ci.yml to install the same packages, so OCR tests always run
  in CI and are never skipped there.
- Tests generate their own PDFs (for example with reportlab): text
  layer, scanned image of text, mixed pages, empty, corrupt, oversized,
  and Hindi when a Devanagari font is available.
Acceptance: a mixed document gets the right method on each page;
corrupt and oversized files fail cleanly.

TASK D: DOCUMENT STORE WITH DEDUPE AND CACHING (src/tenderscreen/store/)
- save_document(content, source_url, fetched_at) stores raw bytes under
  data/raw/ named by their sha256, plus metadata (source URL, fetch
  time as timezone-aware IST, size, content type). Saving identical
  bytes again keeps one file and adds the new source to its metadata.
- A result cache keyed by (sha256, step name, step version), so no step
  ever redoes work. Changing a step's version invalidates its entries.
- Use SQLite from the standard library behind a small interface, so we
  can switch to Postgres later.
- Route Task C's extract_pages through this cache.
Acceptance: tests prove dedupe, cache hits, invalidation on version
change, and that a corrupted cache entry is recomputed, not trusted.

TASK E: EVALUATION HARNESS (src/tenderscreen/evals/)
- Compare predicted extraction results (JSON) against my hand-labelled
  answers (JSON) across a set of documents. Fields follow CLAUDE.md:
  {value, source_page, quote} or {status: "not_found"}.
- Report per field: accuracy; misses (said not_found when an answer
  existed); inventions (gave a value when the answer was not_found).
  Money compares exactly in paise, dates exactly, text after
  normalising whitespace and case.
- Output a markdown report and a JSON summary, via:
  python -m tenderscreen.evals <labels_dir> <predictions_dir>
- Add a labels template and docs/labelling.md explaining step by step
  how I label a document.
Acceptance: synthetic tests cover perfect, wrong, invented and missed
fields.

TASK F: DATA CHECK KIT FOR MY MANUAL RESEARCH
- data/manual/records.csv template, one row per record: portal, URL,
  date found, notice type (award or tender), captcha needed (y/n),
  robots.txt notes, language, file format, text layer or scanned, and
  which fields are present: award date, successful bidder name,
  contract value, number of bids, LoA date, PBG percentage or deadline,
  contact details.
- A command that scans data/manual/ using Task C's code, pre-fills what
  it can (file format, text layer or scanned, page count, language by
  Devanagari vs Latin share) without ever overwriting what I typed,
  and writes a summary of how many records contain each field.
- docs/data-check.md: a step-by-step guide for me covering
  eprocure.gov.in, eproc.punjab.gov.in and gem.gov.in: what to save,
  how to check robots.txt in a browser, how to upload files through
  GitHub, and how to run the kit.
Acceptance: works on synthetic files; re-running never overwrites
manual entries.

FINAL STEP: MORNING REPORT
Write docs/overnight-report.md. For each task: what was built, test
count, coverage, decisions made, anything skipped or failing and why.
Then list exactly what I should check first, and any setup-script line
I should add to the cloud environment. Push, then open a pull request
to main if you can; otherwise write the branch name in the report.
