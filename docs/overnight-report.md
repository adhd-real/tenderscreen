# Overnight report: 4-5 November 2026

All six tasks are done, plus the item you added to Task A this morning (a
`slow` test marker). Each was checked with ruff, mypy, pytest and pip-audit,
reviewed by a fresh-context reviewer, fixed, and committed on branch
**`claude/bold-maxwell-8t0jxb`**. Pull request to `main`:
[adhd-real/tenderscreen#1](https://github.com/adhd-real/tenderscreen/pull/1).

| | |
|---|---|
| Tests | 1,039, all passing on Python 3.12 and 3.13; none skipped (Tesseract was installed here); 13 marked `slow` |
| Coverage of `src/` | 100% (CI gate: 90%) |
| ruff, ruff format, mypy --strict, pip-audit, licence check | all pass |
| CI on GitHub | green on the latest commit (3.12, 3.13 and the secret scan), OCR tests included; one earlier run failed on a sandbox race (fixed, see Task C) and one was cancelled by a newer push |

Every choice I made without you is in [decisions.md](decisions.md), task by
task, including what each review found.

## Check these first

1. **The branch starts from the scaffold, not from `main`.** `main` holds only an
   empty commit; CLAUDE.md, CI and pyproject.toml were on the unmerged branch
   `claude/loving-galileo-7um5vl`, so the pull request also contains those four
   scaffold commits. Merge the scaffold first if you want the PR to show only
   tonight's work.
2. **The money parser is deliberately strict (Task B).** It refuses rather than
   guesses, so it rejects some real strings you might expect to pass:
   "Rs. 5,00,000/- (Rupees Five Lakh only)" (words are never cross-checked),
   "EMD - Rs. 500" (a label needs a colon: "EMD: Rs. 500"), a bare "25.00" (no
   currency or unit), "Rs. 500 per day" and "Rs 5 lakh + GST". Decide whether
   that trade-off is right before extraction relies on it. Full list:
   decisions.md, "Task B".
3. **The document sandbox (Task C)** is the most intricate code of the night:
   every PDF parse and OCR runs in memory-capped child processes with deadlines
   (`src/tenderscreen/documents/sandbox.py`, decisions.md "Task C"). It contains
   crashes, hangs and memory bombs; it is **not** a security boundary against a
   malicious PDF exploiting PDFium (same user, no seccomp or namespaces).
4. **CLAUDE.md vs. `data/manual/records.csv` (Task F): your decision.** CLAUDE.md
   says files in `data/manual/` are never modified, but the task asks the kit to
   fill in `data/manual/records.csv` and write `records-summary.md` there. The
   kit only reads the uploaded documents. If you agree, add an explicit exception
   for those two files to CLAUDE.md; I may not change its rules.
5. **Portal details in docs/data-check.md are unverified.** I had no internet
   access, so menu names for CPPP, Punjab eProcurement and GeM are what such
   portals usually show, and say so. Correct them as you go.
6. **Column and field names before you start recording.** Check the columns in
   `data/manual/records.csv` and the 12 fields in `data/labels/_template.json`
   (for example `pbg_deadline_text`, compared as text because PBG deadlines are
   usually relative: "within 21 days of LoA").
7. **Known gap: Hindi in legacy fonts.** PDFs typed in Kruti Dev and similar
   pre-Unicode fonts have a text layer of Latin "gibberish". `extract_pages`
   takes it at face value (it is not OCR'd), and the data check kit calls it
   `english`. The guide asks you to check the language by eye; fixing it
   properly (detect such text layers and OCR them) is future work.
8. **MPL-2.0 dev dependencies.** `certifi` (via pip-audit and requests) and
   `pathspec` (via mypy) are MPL-2.0: weak copyleft, not GPL-family, used only by
   dev tools. The licence gate allows them, but your list of acceptable licences
   does not name MPL, so decide whether that is acceptable.
9. **Small CLAUDE.md wording.** I may only add lines to CLAUDE.md, so "Run all
   five before every commit" now undercounts the commands above it (six checks
   plus a subset command). Consider "Run all of these".

## Setup-script line for the cloud environment

OCR needs system packages that the session-start hook (Python only) does not
install. Add this to the environment's setup script:

```bash
apt-get update && apt-get install -y tesseract-ocr tesseract-ocr-hin fonts-noto-core
```

Without it, OCR tests skip locally (with the reason shown); CI installs the same
packages and sets `TENDERSCREEN_REQUIRE_OCR=1`, so there they fail rather than
skip.

## Task by task

### A. Quality gates

- **Built:** pytest-cov with a 90% line-coverage gate on `src/`, enforced by every
  plain `pytest` run (configured in pyproject.toml, so CI's test step enforces
  it), and `python -m tenderscreen.devtools.licence_check`, a CI step that fails
  if any installed distribution declares a GPL, LGPL or AGPL licence, or none.
  Item 3 (added on 5 November): a `slow` marker. The post-edit hook now runs
  `pytest -m "not slow" --no-cov`; plain `pytest` and CI still run everything
  with the coverage gate.
- **Tests:** 63 (61 new, plus the 2 scaffold tests). Coverage 100%.
- **Decisions:** the check reads declared licence metadata, not licence-file
  text (pypdfium2's bundled ICU licence quotes the GPL inside an autoconf
  exception); a package with no declared licence fails; `--allow NAME` exists for
  reviewed exceptions and is unused.
- **Slow marker:** "OCR tests" are the 10 that run the real Tesseract (the ones
  that skip without it, as in your Task C wording); "large-file tests" are the 3
  that write a file of 1 MB or more. A guard in `tests/conftest.py` stops any
  run in which an OCR test lacks the marker. Measured: the hook's test step went
  from 64.8 s to 52.7 s. Most of what remains is tests that wait for timeouts
  on purpose (about 28 s); they are neither OCR nor large-file tests, so I left
  them unmarked. Marking them is the next step if the hook should be faster.
- **Found in review and fixed:** coverage rounded 89.5% up to a pass (now
  `precision = 2`); some GPL spellings slipped past ("GNU Public License");
  `License: UNKNOWN` counted as a licence.
- **Skipped or failing:** nothing.

### B. Indian money and date parsing

- **Built:** `parse_inr(text)` returns `AmountResult(paise, reason)`;
  `parse_date(text)` returns `DateResult(value, reason, has_time)` with an aware
  Asia/Kolkata datetime. All eight required money examples and all five date
  formats are tests, verbatim.
- **Tests:** 320 (required: 40). Coverage 100%.
- **Decisions:** a strict money grammar, `[label:] [currency] number
  [lakh|crore] [currency] [only|approx|मात्र]`; everything else is rejected with a
  reason (words, two numbers, rates, units such as MT, headers like "Rs. in
  lakhs", OCR letter/digit mix-ups, superscripts, other scripts). Exact
  `Decimal` arithmetic, never float. Dates must be the whole text, years
  1950-2100, day first; a date without a time is flagged `has_time=False`.
  `tzdata` is a dependency, and a test proves parsing works with no system
  timezone files.
- **Found in review and fixed:** the first version guessed on about twenty inputs
  ("Rs. 25 thousands" became ₹25, "Rs. 500 per day" passed, "₹ 5000²" became
  ₹50,002); year 9999 could crash date parsing.
- **Skipped or failing:** nothing; see the strictness note above.

### C. PDF text extraction and OCR

- **Built:** `extract_pages(path)` returns, per page, the page number (from 1),
  text, method (`text_layer` or `ocr`) and quality (`good`, `low` or `empty`,
  plus OCR confidence). Pages without a usable text layer are rendered with
  pypdfium2 and OCR'd by Tesseract (eng+hin) in parallel worker processes.
  Limits: 100 MB, 1,000 pages and 60 s per page by default, plus 1 GiB of memory
  per worker and 30 minutes per document, each failing with its own clear error.
- **Tests:** 240, generating their own PDFs: text layer, scanned text, mixed,
  empty, corrupt, oversized, Hindi (with Noto Devanagari), PDF bombs. All OCR
  tests ran here (Tesseract 5.3.4 installed without trouble). Coverage 100%.
- **Decisions:** a page's text layer counts if it has 40 letters and digits, or
  200 when images cover half the page (scans with a "Digitally signed by"
  overlay are still OCR'd); image coverage is measured in page coordinates,
  through form matrices. Every parse runs in child processes the parent manages
  directly (not `multiprocessing.Pool`) and answers in size-capped JSON, never
  pickle. The first failing page stops the document.
- **Found in two reviews and fixed:** a 2 KB PDF bomb could hang or abort the
  caller; crashes were reported as timeouts a minute later; a killed worker
  could deadlock shutdown; a worker that sent half a message could block the
  parent; OCR text was placed on whatever page the worker claimed (now paired
  with the task sent); workers survived a killed parent; no limit for a whole
  document; image coverage ignored offset page boxes and form coordinates.
- **Found by CI and fixed:** a race reported a worker killed mid-message as
  "message over 4 MB" (a dying process closes its pipe just before it can be
  reaped). It failed one CI run; the fix has a test that fails on the old code.
- **Measured (4-CPU container):** 12 scanned A4 pages in 7.9 s with one worker,
  2.5 s with four; 200 text-layer pages in 0.24 s.
- **Skipped or failing:** nothing. Known gap: legacy-font Hindi (above).

### D. Document store with dedupe and caching

- **Built:** `save_document(content, source_url, fetched_at)` writes the bytes
  once, atomically, to `data/raw/<sha256>` and records size, content type
  (sniffed from the bytes) and every source with its fetch time in IST, in
  `data/raw/store.sqlite3`. Saving identical bytes again keeps one file and adds
  the new source. A result cache keyed by (sha256, step, version) sits behind a
  seven-method `StoreBackend` protocol with a SQLite implementation (standard
  library `sqlite3`, SQL kept Postgres-compatible). `extract_pages(path,
  cache=...)` goes through it; its step version covers the code, pypdfium2 and
  PDFium versions, Tesseract's version and language files, and every
  output-affecting setting.
- **Tests:** 107 (94 in `tests/store/`, 13 for the `extract_pages` cache in
  `tests/documents/test_extract_cache.py`). Coverage 100%. They prove dedupe,
  cache hits (no recompute), invalidation on a version change, and that a
  corrupted entry (changed payload, wrong checksum, undecodable) is recomputed.
- **Decisions:** files have no extension (the name depends only on the content);
  `data/raw/` is git-ignored; every entry carries a checksum over its key and
  payload and is strictly decoded before use; storing one version keeps the
  others (`invalidate_step` removes old versions explicitly).
- **Found in review and fixed:** concurrent first opens could break a new
  database; a file changed during extraction could be cached under its old
  hash; storing one version evicted the others (callers with different settings
  thrashed); the checksum did not cover the key; some settings were missing from
  the step version; a document and its source were written in separate
  transactions; source URLs accepted `javascript:` and `data:`.
- **Skipped or failing:** nothing. Postgres itself is not built (the task asked
  for the interface).

### E. Evaluation harness

- **Built:** `python -m tenderscreen.evals <labels_dir> <predictions_dir>
  [--out eval-results]` writes `report.md` and `summary.json`: per field,
  accuracy, wrong, missed (said `not_found` where the label has a value),
  invented (a value where the label says `not_found`), invalid and source-page
  agreement. Money compares exactly in paise, dates exactly (including whether a
  time was stated), text after normalising Unicode, whitespace, case and
  invisible characters. A labels template (12 fields) and
  [labelling.md](labelling.md), a step-by-step guide.
- **Tests:** 170; the acceptance tests for perfect, wrong, invented and missed
  fields are in `tests/evals/test_run.py`. Coverage 100%.
- **Decisions:** how a field compares follows from its name (`_paise`, `_date`,
  `number_of_`, `_percentage`, otherwise text), so labels need no schema file.
  Labels are strict (any problem stops the run, since scores against bad labels
  mean nothing); predictions are scored field by field. Contact-like fields are
  never shown in reports or errors, and the JSON summary holds no values.
- **Found in review and fixed:** hostile JSON (deep nesting, `NaN`, repeated
  keys) crashed the run or slipped through; an unreadable predicted value was
  hidden as "invalid" (now invented or wrong); contact data could appear in
  error details; crafted names could inject Markdown into the report; typos in
  label field names went unnoticed (now checked against the template); money in
  labels written as a bare number would have been read as paise (now refused).
- **Found in a second review, of those fixes:** a lone surrogate (`"\ud800"`)
  still crashed the run and left an empty report; the repeated-key check was
  quadratic (a 5 MB file would have taken over half an hour); a correct value
  with a malformed citation was scored wrong instead of invalid; links and
  images in values still rendered (values are now shown as inline code).
- **Skipped or failing:** nothing.

### F. Data check kit for manual research

- **Built:** a `data/manual/records.csv` template (one row per record, every
  requested column plus `file`, `page_count` and `notes`), and `python -m
  tenderscreen.datacheck [--no-ocr]`, which scans `data/manual/` with Task C's
  code (OCR through Task D's cache) and fills only blank cells of
  `file_format`, `text_layer_or_scanned`, `page_count` and `language`
  (Devanagari vs Latin share), then writes `records-summary.md`: how many
  records contain each field, records by portal and notice type, fields per
  portal, and problems. [data-check.md](data-check.md) is the step-by-step guide
  for eprocure.gov.in, eproc.punjab.gov.in and gem.gov.in.
- **Tests:** 139, on synthetic files only; `test_rerun_never_overwrites_manual_entries`
  is the acceptance test. Coverage 100%.
- **Decisions:** a malformed CSV stops the kit with the line number and changes
  nothing; extra columns, row order, BOM and line endings are kept, and an
  unchanged file is not rewritten; images are not OCR'd (their language is left
  for you); HTML is parsed in the sandbox too.
- **Found in review and fixed:** a stray double quote made the CSV reader merge
  the following rows into one cell, which the kit would have written back;
  low-quality OCR could pre-fill a wrong language (now left blank, with the
  reason in the summary); HTML parsing had no time limit; rewriting records.csv
  made it owner-only; Excel's trailing empty columns stopped the kit; a
  timestamp made the summary change on every run.
- **Found in a second review, of those fixes:** edits saved to records.csv while
  the kit ran were overwritten (it now refuses and keeps your version); a stray
  quote closed by an inch mark on a later row still merged rows without an
  error (now caught); the file mode was set after the fsync; a test still read
  your records.csv.
- **Skipped or failing:** nothing. The portal details are unverified (above).

## Dependencies added

| Package | Licence | Why |
|---|---|---|
| pypdfium2 5.14.0 | Apache-2.0 / BSD-3-Clause, plus PDFium's bundled libraries | PDF text layers and page rendering (instead of PyMuPDF or poppler) |
| pillow 13.0.0 | MIT-CMU (HPND-style) | images for OCR |
| tzdata 2026.6 | Apache-2.0 | Asia/Kolkata without system timezone files |
| reportlab 5.1.0 (dev) | BSD | builds the synthetic test PDFs |
| pytest-cov 7.1.0 (dev) | MIT | the coverage gate |

Tesseract itself is a system program (Apache-2.0), called as a separate process.

## How the night went

- **Order:** setup, then A to F, each committed and pushed separately, then
  review fixes as follow-up commits (`git log` shows one commit per step).
- **Reviews:** a fresh-context reviewer checked every task's diff against
  CLAUDE.md and the acceptance criteria. Task C was reviewed twice; the E and F
  fix commits were reviewed once more and their findings fixed (above).
- **Committed before their reviews finished:** the session's stop hook requires
  committed and pushed work, so D, E and F were committed after every check had
  passed but while their reviews were still running. Their fixes are separate
  commits, which also makes them easy to read on their own.
- **CI:** one run was cancelled because the next push superseded it, and one
  failed on the sandbox race described under Task C, which is now fixed.
- **Housekeeping:** the container restarted once during a benchmark (nothing
  lost). Some `/tmp/tenderscreen-ocr-*` folders left by early test runs (before
  the sandbox rewrite cleaned up after itself) could not be deleted, because the
  permission check refused a sweep of the shared `/tmp`; they are harmless and
  vanish with the container.

## Pull request

[adhd-real/tenderscreen#1](https://github.com/adhd-real/tenderscreen/pull/1), from branch
`claude/bold-maxwell-8t0jxb` into `main`.
