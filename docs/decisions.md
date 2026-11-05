# Decisions log

Choices made while working unattended, with the reason for each. Newest last.

## 2026-11-04: Setup

### Branch base: built on the unmerged scaffold branch

- **Situation:** `main` holds only an empty initial commit. The project scaffold
  (CLAUDE.md, `pyproject.toml`, `.github/workflows/ci.yml`, hooks) exists only on
  the remote branch `claude/loving-galileo-7um5vl`, which has no pull request.
  Every task refers to that scaffold (CLAUDE.md, `ci.yml`, `src/tenderscreen/`).
- **Chose:** fast-forwarded the working branch `claude/bold-maxwell-8t0jxb` onto
  `claude/loving-galileo-7um5vl` (no history rewritten) and built all tasks on top.
- **Why:** without the scaffold there is no CLAUDE.md to follow and no CI to
  extend. A pull request from this branch to `main` therefore also contains the
  four scaffold commits; if the scaffold branch is merged first, GitHub shows
  only the overnight work.

## Task A: Quality gates

### Coverage gate lives in pyproject.toml and applies to every plain `pytest`

- **Chose:** `--cov` in pytest `addopts`, `[tool.coverage.run] source_pkgs =
  ["tenderscreen"]` and `[tool.coverage.report] fail_under = 90` (line coverage,
  the coverage.py default). CI's existing `pytest` step therefore enforces it; no
  separate threshold is repeated in `ci.yml`.
- **Why:** one source of truth, and the same command passes or fails identically
  in a session and in CI. To run a subset of tests locally without the gate:
  `pytest --no-cov tests/<path>` (added to CLAUDE.md commands).
- **Found in review:** coverage.py rounds the total to `precision` digits before
  comparing with `fail_under`; at the default precision 0, 89.58% rounded to 90%
  and *passed*. Set `precision = 2`. Also set `include_namespace_packages = true`
  so a src/ folder missing `__init__.py` is counted instead of silently skipped.
- `tests/test_quality_gates.py` asks coverage.py for the values it will enforce
  (threshold, precision, source), checks `addopts` has `--cov` and no
  `--no-cov`/`--cov-fail-under`, and that no `.coveragerc`/`setup.cfg`/`tox.ini`
  overrides pyproject.

### Licence check is a small in-repo module, not pip-licenses

- **Chose:** `python -m tenderscreen.devtools.licence_check`, which reads each
  installed distribution's *declared* licence (PEP 639 `License-Expression`,
  `License` field, `License ::` classifiers) and fails on GPL, LGPL or AGPL.
- **Why:** no extra dependency to vet, exact control over matching, and fully
  tested. It deliberately does not grep bundled licence *files*: pypdfium2 ships
  ICU's licence file, which quotes the GPL in autoconf-script exception clauses,
  and LLVM's, which mentions the GPL in its exception text; neither makes
  pypdfium2 GPL (it declares BSD-3-Clause and Apache-2.0).
- **Stricter than asked, on purpose:** a package that declares *no* licence also
  fails (we cannot show it is permissive); so does the placeholder `UNKNOWN`
  that old setuptools writes. Dual licences that offer a GPL option
  (e.g. `MIT OR GPL-2.0`) are flagged too; a person should decide those.
- **Escape hatch for reviewed cases:** `--allow NAME` skips one package. CI passes
  none. Use it only after reading the licence, and record why here. (Possible
  false positive: a package that pastes the full PSF, HPND or MPL text into its
  `License` field, because those texts mention the GNU GPL.)
- Matching also catches misnomers found in review: "GNU Public License",
  "GNU Lesser/Library Public License", "GNUGPL".
- **Noticed, not failing:** `certifi` (via pip-audit → requests) and `pathspec`
  (via mypy) are MPL-2.0. MPL is weak copyleft, not GPL-family, and both are
  dev-tool dependencies only, so the gate passes; listed in the report for you.

### CLAUDE.md edited by adding lines only

- Added two command lines and two layout lines. The sentence "Run all five
  before every commit" was left as is (I may not edit rule text), so it now
  undercounts; you may want to change it to "all of these".

### Slow marker (item 3, added by you on 2026-11-05)

- `slow` is registered in pyproject.toml; `--strict-markers` was already on, so
  a misspelt marker is an error. The post-edit hook runs
  `pytest -x -q -m "not slow" --no-cov`; plain `pytest` (the check before every
  commit) and CI still run everything, with the coverage gate. The hook skips
  coverage because the 90% gate is meant for the full suite, and a subset
  could fail it for reasons unrelated to the edit.
- **"OCR tests" means tests that run the real Tesseract**: the ones that use
  the `tesseract` fixture, which skip locally without it (your wording in Task
  C). That is 10 tests. A guard in `tests/conftest.py` stops any run in which
  a test uses the fixture without `@pytest.mark.slow`, so a new OCR test cannot
  slip into the hook.
- **"Large-file tests" means tests that write a file of 1 MB or more**: three
  (a 2.4 MB HTML page, a 3 MB document for chunked hashing, a 5 MB labels
  file). The oversized-file tests elsewhere use tiny limits on tiny files, so
  they stay fast.
- **Measured:** the hook's test step took 64.8 s before (1,006 tests, with
  coverage) and 52.7 s now (999 tests, 13 deselected). Of what remains, about
  28 s is tests that wait for a timeout or a kill on purpose (sandbox, a fake
  Tesseract that hangs, PDF bombs), and most of the rest is starting worker
  processes. They are neither OCR nor large-file tests, so I left them
  unmarked; marking the timeout tests would be the next step if the hook
  should be faster.

## Task B: Indian money and date parsing

### Result types: a value or a reason, never both

- `parse_inr(text) -> AmountResult(paise, reason)` and
  `parse_date(text) -> DateResult(value, reason, has_time)`. Exactly one of
  value/reason is set (enforced in `__post_init__`). Reasons are `StrEnum`s whose
  value is a readable sentence, so code compares `reason is AmountRejection.WORDS`
  and people read `str(reason)`.

### Money: a strict shape, everything else rejected with a reason

The first version accepted any words around a single number. The fresh-context
review showed that this guesses: "Rs. 25 thousands" became ₹25, "Rs. 500 per
day" became ₹500, "(Rs. in Mn) 25" became ₹25, OCR's "Rs. l5,000" became ₹5,000,
and "₹ 5000²" (a footnote mark) became ₹50,002. So the accepted shape is now:

    [label:] [currency] number [lakh|crore] [currency] [only | approx | मात्र | लगभग]

- **Before the number:** nothing, currency markers, opening brackets, or a
  `label:` ending in a colon ("EMD: Rs. 25,000/-"). Words without a colon are
  rejected (`LABEL`): "EMD - Rs. 500", "The EMD is Rs. 500". A label may not
  contain a scale abbreviation ("(Rs. in L): 25") or a deduction word ("Less:").
- **After the number and unit:** only "only", "approx", "approximately",
  "मात्र", "लगभग", currency markers and punctuation `. , ; : ( ) [ ] *`.
  Anything else (`TRAILING_TEXT`) may change the meaning: rates ("per day",
  "/day", "प्रति दिन"), quantity units ("MT", "units"), "+ GST", misspelt or
  foreign-language units ("lacks", "core", "कोटी"). Known scale abbreviations
  ("L", "k", "Mn", "bn", ...) or letters glued to the number get `UNKNOWN_UNIT`.
- **Currency or unit required.** A bare number is `NO_CURRENCY`: tables often
  say "Rs. in lakhs" in a header, so "25.00" alone may mean 25 lakh. A lakh/crore
  amount *without* a currency marker must stand alone ("2.5 Cr"), because
  "Quantity: 25 lakh" is not money (`UNMARKED_WITH_LABEL`).
- **Words anywhere** (`WORDS`): number words in English or Hindi, including
  plural scales ("thousands", "millions"). "Rs. 5,00,000/- (Rupees Five Lakh
  only)" is rejected even though the figures are clear: checking that words match
  figures needs a words parser, which was not asked for. This is the most likely
  "false rejection" in real notices; see the report.
- **More than one number anywhere** (`MULTIPLE`), e.g. "Tender 2026/17 EMD Rs. 500".
- **Negatives** (`NEGATIVE`): any dash character (hyphen, en/em dash, minus sign,
  full-width forms...) between a label and the number, "(-)", "minus", a "Less:"
  label, or accounting brackets around the bare number ("₹ (1,234.50)").
- **Digits:** plain, Indian grouping (12,34,567) or Western grouping
  (1,234,567). Rejected: "25,00", "1.234.567", leading zeros, ".5", letters
  touching the digits, and superscript/subscript/circled/fraction digits (which
  NFKC would otherwise silently turn into ordinary digits). Devanagari and other
  Indian digits are read as digits.
- **Scripts:** letters outside Latin and Devanagari are rejected
  (`FOREIGN_SCRIPT`), because a scale word in, say, Bengali or Gurmukhi could not
  be recognised.
- **Precision:** more than 2 decimals without a unit is `FRACTIONAL_PAISE` ("₹
  1.234" may be European for 1234). With a unit, decimals are fine if the result
  is a whole number of paise. Arithmetic uses a private exact `decimal.Context`
  (330 digits, inexact results raise), so the caller's decimal settings cannot
  change the answer.
- Accepted markers: ₹, ₨, Rs, Rs., INR, Rupees, रु., रुपये (and spellings),
  and the Indian "/-" suffix. Units: lakh, lakhs, lac, lacs, crore, crores, cr,
  crs (any case, optional full stop), लाख, करोड़ (with or without nukta).
- Input longer than 300 characters is refused (security rule: bound every parse).

### Dates

- **Whole text must be a date** (plus optional time): no labels;
  "Last date: 15/10/2026" is rejected. Full matching is the safest way to avoid
  picking the wrong date out of a sentence.
- **Date without a time** is midnight IST with `has_time=False`, so a deadline of
  "15 Oct" is never silently treated as "15 Oct 00:00" by later code.
- **Explicit UTC offsets** ("…T11:30:00Z", "+05:30") are unambiguous, so they are
  converted to IST. "IST" together with a different offset is rejected
  (`CONFLICTING_TIME_ZONE`). Other zone names ("GMT") are rejected.
- **Years 1950-2100 only** (`YEAR_OUT_OF_RANGE`): other years are almost surely
  typos, extreme years made the UTC conversion overflow (found in review), and
  pre-1946 Kolkata times carry historical offsets such as +05:53.
- **Rejected:** two-digit years (century unknown), month-first ("Oct 15, 2026"),
  year-first with slashes ("2026/10/15"), mixed separators ("15/10-2026"), a time
  glued to the date ("2026-10-1517:00"), a bare hour with no minutes or AM/PM
  ("15/10/2026 5"), 13 PM / 00 AM / 24:00, superscript digits, letters in other
  scripts.
- **Extras accepted** (unambiguous): ordinals ("15th"), full month names, Hindi
  month names, times like "05.00 p.m.", "1700 hrs", "3 PM", an "IST" suffix.
  "12:00 AM" is midnight at the start of the day.
- `tzdata` is a pinned runtime dependency; a test starts a fresh interpreter with
  `PYTHONTZPATH` pointing at a non-existent folder and still gets +05:30.

## Task C: PDF text extraction and OCR

### Tesseract installed here; OCR tests skip locally without it, fail in CI

- `apt-get install -y tesseract-ocr tesseract-ocr-hin fonts-noto-core` worked in
  this session (Tesseract 5.3.4 with `eng`, `hin`, `osd`), so every OCR test ran.
- OCR tests use a `tesseract` fixture that skips with the reason when Tesseract
  (with eng+hin) or a Devanagari font is missing. `ci.yml` installs the same
  three packages and sets `TENDERSCREEN_REQUIRE_OCR=1`, which turns that skip
  into a failure, so OCR tests can never be silently skipped in CI. A guard test
  checks both lines stay in `ci.yml`.
- Most OCR *plumbing* (timeouts, crashes, failures, missing languages, minimal
  environment) is tested with a fake `tesseract` shell script, so those tests run
  everywhere and fast.

### When does a page "have a text layer"?

- At least `min_text_chars` = 40 letters/digits/vowel signs, **and**, if images
  cover at least half the page, at least `image_page_min_text_chars` = 200.
- **Why the second rule:** Indian government scans often carry a small real-text
  overlay such as "Digitally signed by … Date: 2026.10.15 17:30:45 IST" (about
  45-100 characters). A plain threshold would treat such a scan as text and never
  OCR the scanned body. A scan that already has a full OCR text layer (hundreds of
  characters) still uses it. Both thresholds are settings.
- Characters in the private-use area, unassigned code points, U+FFFD and control
  characters do not count as text; if they exceed 5% of a text layer, the page's
  quality is `low`.
- **Known gap:** PDFs typed in legacy Hindi fonts (Kruti Dev and similar) have a
  text layer of Latin "gibberish" that looks like real text; they are not
  detected and are not OCR'd. Detecting them needs a language check; noted in the
  report.

### Quality flag

- `good`, `low` or `empty`. OCR pages are `low` when Tesseract's mean word
  confidence is below 60 (`min_ocr_confidence`); `empty` means no letters or
  digits at all (blank page). `ocr_confidence` (0-100) is returned too.

### Every parse of the PDF runs in a sandboxed child process

- **First version** read text layers in the main process and ran OCR in a
  `multiprocessing.Pool`. The fresh-context review broke it three ways: a 2.4 KB
  "PDF bomb" of nested Form XObjects kept the main process busy for 19 s and
  1.8 GB, and a slightly larger one aborted the whole program; a worker crash
  (segfault) was reported 65 s later as a timeout; and a worker killed while
  idle (for example by the out-of-memory killer) made `Pool` shutdown hang
  forever. A first attempt to kill worker process groups also deadlocked `Pool`.
- **Now** (`documents/sandbox.py`): every parse of the file, text layers and
  OCR alike, happens in fresh "spawn" child processes that the parent manages
  directly (one pipe each). The parent waits on the pipe *and* the process
  sentinel, so a dead child is noticed at once ("killed by SIGSEGV"), and it
  kills exactly the child that overran. Each child gets a 1 GiB address-space cap
  (`ExtractionLimits.max_memory_bytes`, Linux `RLIMIT_AS`, inherited by
  Tesseract); normal pages need under 300 MB, and the review's bombs now fail
  in under a second with a clean `CorruptDocumentError`.
- **Deadlines:** a child first reports "ready" (starting Python is not the
  document's fault and has its own 60 s limit); then opening the file, each
  text-layer page and each OCR page get `page_timeout_s` (60 s). An OCR worker
  stops its own Tesseract at the limit and reports it; the parent kills a worker
  that has not answered 2 s later (a hung render).
- **Fail fast:** the first failed page (timeout, OCR error, crash) stops the
  whole document at once and kills every worker; previously all pages ran first.
- **Children answer in JSON, never pickle**, capped at 16 MB per message and
  validated by the parent: a child parses untrusted data, so if PDFium were ever
  exploited, its messages must not become code execution in the parent
  (CLAUDE.md: never unpickle anything derived from a document).
- **Tesseract dies with its worker:** started with the parent-death signal
  (`PR_SET_PDEATHSIG`, Linux), and it exits at once if the worker is already gone.
  A test checks no Tesseract outlives a killed worker.
- **Temporary files:** the parent creates one folder per document (a sub-folder
  per page) and always deletes it, even when workers are killed; before, a
  killed worker leaked its page image.
- **Calling scripts:** "spawn" re-imports the main program, so scripts must
  guard their top level with `if __name__ == "__main__":`. A script piped into
  `python -` cannot be re-imported at all; this is now detected up front with a
  clear error instead of workers crashing on start.
- Workers default to the CPUs this process may use (CPU affinity), never more
  than the pages to OCR. Each child costs about 0.3 s to start; measured before
  optimising, per CLAUDE.md.
- Rendering uses PDFium (pypdfium2, BSD-3/Apache-2.0) at 300 dpi grayscale,
  capped at 50 megapixels for posters; no poppler, no PyMuPDF. Pillow (MIT-CMU,
  HPND-style) writes the PNG. Tesseract runs as a program with
  `OMP_THREAD_LIMIT=1` and only `PATH` and `TESSDATA_PREFIX` in its environment.
- **Limits** (`ExtractionLimits`): 100 MB (checked with `stat` before anything
  opens the file), 1,000 pages, 60 s per page, 1 GiB memory per worker. Each
  failure has its own `DocumentError` subclass whose message names the file,
  page and setting. Corrupt, empty, non-PDF, password-protected, crashing and
  memory-exhausting files raise `CorruptDocumentError`; a missing file raises
  `FileNotFoundError`; a worker that cannot start raises `WorkerStartError`.

### Second review of the sandbox (fixed in a follow-up commit)

A second fresh-context review of the rewritten sandbox found, and these are now
fixed and tested:

- **A half-sent message could hang the parent forever** (the deadline covered
  only the wait for the first byte). Every read now has a watchdog that kills
  the child if the message does not finish arriving in time.
- **OCR text could be put on the wrong page**: the parent trusted the page
  number the worker reported. Results are now paired with the task the parent
  sent, and an answer naming another page is rejected. A wrong page would have
  meant wrong `source_page` citations (CLAUDE.md rule 3).
- **Workers outlived a killed parent.** Every child now has the parent-death
  signal, as Tesseract already did.
- **No limit for the whole document.** `ExtractionLimits.max_document_s`
  (default 30 minutes) now bounds all pages and stages together
  (`DocumentTimeoutError`); without it a crafted 1,000-page file could take
  over 16 hours at just under 60 s per page.
- **Stopping workers after a failure** gave each busy worker 0.5 s in turn;
  busy workers are now killed at once, and one worker failing to stop no
  longer leaves the others running.
- **Children inherited the full environment.** They now drop every variable
  except PATH, temp-folder, locale and TESSDATA_PREFIX. This keeps secrets away
  from Python code and programs in the worker, but native code could still read
  the process's original environment block: the sandbox limits resources, it
  is not a privilege boundary (stated in `sandbox.py`). For full isolation, run
  document processing in a process or container with no secrets.
- Smaller: answers are capped at 4 MB (decoding costs about 20x in memory) and
  deeply nested JSON is a protocol error; NaN or infinite limits are refused; a
  child that dies mid-message is reported as a crash, naming the page; text from
  children is shortened before it goes into error messages; the test-only
  `open_pdf` was removed; failing to start a process is a `WorkerStartError`.
- New tests: two-level nested forms (catching a wrong matrix order), a stalled
  message, a forged page number, the whole-document limit, workers dying with a
  SIGKILLed parent, the environment a worker sees, and stopping time.

### A race found by CI (fixed in a follow-up commit)

- CI failed once on Python 3.12: a child stalled mid-message was killed by the
  watchdog, but the parent reported "message over 4194304 bytes" instead of a
  timeout. A dying process closes its pipe a moment before its parent can reap
  it, so `is_alive()` could still say True. Now a cut-off message counts as a
  death when the watchdog fired, or when the child exits within
  `EXIT_GRACE_S` (1 s); only then is it treated as an oversized message. The
  same race could have misreported a child that crashed mid-message. A test
  forces `is_alive()` to say True and fails on the old code.

### Image coverage in page coordinates (found in review)

- Coverage was computed against (0, 0, width, height) and with Form XObject
  coordinates taken as page coordinates, so a scan on a page whose box starts
  at (1000, 1000), or a scan wrapped in a form (as merge and stamp tools do),
  measured 0% images and was wrongly treated as a text page. Now images are
  mapped through every enclosing form's matrix and clipped to the page's visible
  box (media box intersected with crop box). Both cases are tests. At most
  10,000 image/form objects are examined per page.

### Typing for untyped libraries: stubs, not `ignore_missing_imports`

- pypdfium2 and reportlab ship no type information. Instead of telling mypy to
  ignore them (which would weaken checking), `stubs/` declares just the calls we
  use, and `mypy_path = "stubs"` points mypy there.

### Test PDFs

- `tests/documents/pdf_factory.py` builds every PDF: text pages (reportlab),
  "scanned" pages (a Pillow image of text, placed full-page, so no text layer),
  scans with a signature overlay, blank pages, Hindi text layers (Noto Sans
  Devanagari embedded) and Hindi scans (shaped by Pillow's raqm). Corrupt,
  truncated, password-protected and oversized cases are derived from these.
  `reportlab` (BSD) is a dev-only dependency.

## Task D: Document store with dedupe and caching

### Layout on disk

- Raw bytes go to `data/raw/<sha256>` (64 lowercase hex characters, no
  extension), metadata and cached results to `data/raw/store.sqlite3`. Both are
  under the git-ignored `data/raw/`, so nothing downloaded is committed.
- **Why no file extension:** the name must depend only on the content; the
  content type is recorded in the metadata instead (`sniff_content_type` reads
  the bytes: PDF, HTML, PNG, JPEG, GIF, TIFF, ZIP/Office, XML, text).
- Writes are atomic (temporary file, `fsync`, rename). Saving bytes whose file
  exists but no longer matches its hash rewrites it; `read_bytes` re-checks the
  hash and raises `CorruptStoredFileError` rather than return damaged bytes.

### Dedupe and sources

- One `documents` row per sha256 (size, content type, first saved time) and one
  `document_sources` row per distinct (URL, fetch time). Saving identical bytes
  from a second URL keeps one file and adds a source; saving the same URL at the
  same time again changes nothing; the same URL fetched later is a new source
  (it records the re-fetch).
- `fetched_at` must be timezone-aware (naive times are refused, never assumed)
  and is stored converted to IST, e.g. `2026-10-15T17:00:00+05:30`.
- Source URLs need a scheme but any scheme is allowed (`file:` for manual
  uploads); the fetcher's domain allowlist is a separate, later concern.
- The store refuses documents over 100 MB, matching the extraction limit
  (security rule: bound every input).

### Interface for a later Postgres switch

- `StoreBackend` is a `typing.Protocol` with seven methods; `SQLiteBackend` is
  the only implementation. The SQL uses `ON CONFLICT ... DO NOTHING / DO UPDATE`
  and ISO text timestamps, both of which Postgres accepts, so a Postgres backend
  mostly swaps placeholders and connection handling. A schema-version row makes
  future migrations explicit; an unknown version is refused.

### Result cache

- Key: (sha256, step name, step version). Each entry stores the payload bytes
  and a sha256 over the key *and* the payload. On every read the checksum is
  verified and the payload must decode and validate; otherwise the entry is
  deleted, counted in `stats.discarded`, and the step runs again. Errors are
  never cached. The checksum catches damage and entries moved to another key;
  it has no secret, so it does not stop someone who can write the database.
- Changing a step's version invalidates its entries: they no longer match any
  lookup. Old versions are kept (not deleted on the next store), because the
  version includes settings and two callers with different settings would
  otherwise evict each other's results on every call (found in review);
  `invalidate_step(step, keep_version)` purges a step's other versions.
- `extract_pages(path, cache=...)` uses step `extract_pages` with a version
  string that includes the code version, the pypdfium2 and PDFium versions, the
  Tesseract version plus a fingerprint of its full `--version` output and its
  language data files, and every `ExtractionSettings` field except an explicit
  exclusion list (`workers`, `tesseract_binary`); a test fails if a new setting
  is added without deciding. Limits are not part of it, but a cached result is
  still checked against today's page limit.
- The file is hashed again after extraction; if it changed meanwhile, the pages
  are returned but not cached under the old hash (found in review).
- Cached pages are JSON with a strict decoder (exact keys, types, enum values,
  pages numbered 1..n). Anything else is treated as corrupt and recomputed.

### Found in the Task D review (fixed in a follow-up commit)

- **Concurrent first opens could corrupt a new store**: the schema check
  selected, then inserted, so several processes could write two version rows,
  after which every open failed. The version table now has a single fixed row
  (`id = 1`), setup runs in one `BEGIN IMMEDIATE` transaction, and "database is
  locked" during setup is retried. WAL mode is set only once.
- A document row and its source are now written in one transaction, and the
  folder is fsynced after the atomic rename.
- Every `sqlite3` error surfaces as `StoreError` (callers never depend on the
  database library). The `StoreBackend` protocol has seven methods; a Postgres
  version would use `BYTEA` instead of `BLOB`.
- Any decode failure (not only `ValueError`) discards a cache entry; a huge
  `ocr_confidence` no longer crashes the decoder.
- Source URLs must be `http:`, `https:` or `file:` with no spaces or invisible
  characters (`javascript:` and `data:` were accepted before).
- Content sniffing recognises HTML that starts with a comment ("saved from
  url"), with `<head>`, or as XHTML with an XML prolog.
- `**/data/raw/` is ignored by git wherever the store is opened from.

## Task E: Evaluation harness

### Files and pairing

- One JSON file per document in each folder, `<document id>.json`, paired by
  file name. Files starting with `_` (the template) are ignored. Shape:
  `{"document": "<source file>", "fields": {<name>: {value, source_page, quote}
  | {"status": "not_found"}}}`. Unknown or missing keys are errors.
- **Labels are strict:** any problem in any label file stops the run (exit 2)
  with every problem listed, because scores against bad labels mean nothing.
- **Predictions are lenient, field by field:** a missing prediction file, a
  missing field or a malformed field counts as `invalid` for that field (never
  as correct), and the rest is still scored. Predicted fields or files that have
  no label are listed and ignored.

### How values compare (by field name)

- `_paise`: money, exactly in paise; integer paise or text `parse_inr` accepts.
  JSON numbers with a decimal point are refused (`parse_float=Decimal`, so no
  float ever enters).
- `_date`, `_deadline`, `_at`: exactly, as IST datetimes from `parse_date`,
  including whether a time was stated ("15/10/2026" differs from
  "15/10/2026 00:00").
- `number_of_` / `_count`: whole numbers; `_percentage` / `_percent`: decimals
  (2.5 equals 2.50); everything else: text, after Unicode NFC, collapsing
  whitespace and ignoring case. Naming by convention was chosen over a separate
  schema file so labels need no extra configuration; docs/labelling.md has the
  table.
- Quotes are required in labels but not compared (the spec compares values);
  page agreement (same `source_page` for correct values) is reported extra.

### Outcomes and reports

- Per field: labelled, correct, accuracy, wrong, missed (label has a value,
  prediction said not_found), invented (label says not_found, prediction gave a
  value), invalid, page agreement.
- `report.md` and `summary.json` go to `--out` (default `eval-results/`,
  git-ignored). The JSON has counts and outcomes only, no field values. The
  Markdown shows values for mistakes, except fields whose names suggest contact
  data (contact, phone, mobile, email, address), which are hidden (CLAUDE.md:
  never log phone numbers).
- Money in the Markdown report is shown in rupees with Indian digit grouping
  ("₹ 25,00,000.00"): CLAUDE.md converts paise only when formatting for people.
- Money given as a bare integer is read as **paise** (the field is named
  `_paise`, and the pipeline will store int paise). Hand labels are safer
  written as text ("Rs. 25 lakh"); docs/labelling.md says so.
- The labels template marks every field `TODO`, which the validator rejects, so
  an unfinished label can never silently count as `not_found`.
- A prediction file that is not readable JSON makes every field of that
  document `invalid`, with the JSON error as the reason.

### Found in the Task E review (fixed in a follow-up commit)

- **Hostile JSON**: very deep nesting (`RecursionError`), huge numbers and
  `NaN`/`Infinity` crashed the run or slipped through; repeated keys silently
  kept the last value. All are now a clean "not valid UTF-8 JSON" problem for
  that file (a label stops the run; a prediction scores every field invalid). A
  byte-order mark is accepted (Notepad and Excel add one).
- **An unreadable predicted value is no longer "invalid"**: "within 21 days of
  LoA" for a date field claims a value exists, so it counts as **invented** when
  the label says `not_found` and **wrong** otherwise. Only a missing or
  malformed entry (wrong keys, no file) stays invalid.
- **Contact data**: error messages and the report's "invalid" details for
  contact-like fields never repeat the value, and every detail is shortened.
- **Markdown injection**: field names, document names and values are flattened
  to one line, with `|` and backticks replaced, and shown as inline code, so a
  crafted prediction cannot add rows, headings, links, images or HTML to the
  report. (The first fix only flattened them; links and `<img>` still rendered.
  Found in the review of the fixes.)
- **Labels write money as text**: a bare number in a label is refused, because
  it would be read as paise (`2500` would mean ₹ 25). Predictions may still give
  integer paise, since the pipeline stores int paise.
- **Field names are checked against `_template.json`** when it is in the labels
  folder, so a typo such as `award_dat` is an error rather than a new field. A
  template without a `"fields"` object stops the run.
- **`pbg_deadline` renamed `pbg_deadline_text`**: the deadline is usually
  relative ("within 21 days of LoA"), so it is compared as text; a `_deadline`
  name would have forced a calendar date. docs/labelling.md says the two PBG
  fields are separate facts.
- The template's `source_page` is `"TODO"` too, so an unfilled page number is
  caught. Only the placeholder counts as TODO: "TODO" or "TODO: ..."; a bidder
  named "Todo Enterprises" or a file "PHOTODOC.pdf" is not.
- Text comparison drops invisible characters (zero-width space, joiners, word
  joiner, byte-order mark, soft hyphen), which PDFs often contain.
- A prediction whose `"document"` names another file than its label is listed
  under Problems, since it was probably paired with the wrong label.
- `--out` pointing at a file (or an unwritable folder) is a clean error
  (exit 2).
- docs/labelling.md now covers JSON punctuation (commas, `\"`, joining
  multi-line quotes), the template field-name check, money as text, and that a
  cloud session sees labels only once they are on `main`.

### Found in the review of the Task E fixes (fixed in a follow-up commit)

- **A lone surrogate crashed the run**: JSON may spell half a character
  (`"\ud800"`), which cannot be written as UTF-8; the report was left empty and
  the run exited 1. Such a file is now invalid ("a \u escape spells half a
  character"), and the report writer escapes anything unwritable, such as
  undecodable bytes in a file name, instead of crashing.
- **The repeated-key check was quadratic**: 80,000 keys took a minute, and a
  file at the 5 MB cap would have taken over half an hour. It is one pass now.
- **A malformed citation is not a claim**: a correct value with
  `"source_page": "1"` or an empty quote was scored **wrong**. Only a value that
  cannot be read counts as claimed (invented or wrong); a bad page or quote
  makes the field invalid.
- Contact fields hide only error messages that repeat the input (the value or
  the page); "still says TODO" is shown for them too.
- Money in the report is shortened like other values.

## Task F: Data check kit

### records.csv

- Columns: `file` (added: links a row to a file in data/manual/, relative path),
  the requested ones (`portal`, `url`, `date_found`, `notice_type`,
  `captcha_needed`, `robots_txt_notes`, `language`, `file_format`,
  `text_layer_or_scanned`, `page_count`, seven `has_...` columns), and `notes`.
  `has_pbg_percentage_or_deadline` is one column, as the task lists "PBG
  percentage or deadline" as one field. `y`/`n` values; the summary also
  accepts yes/no/1/0/हाँ/नहीं.
- The kit writes only blank cells of four columns (`file_format`,
  `text_layer_or_scanned`, `page_count`, `language`) and the `file` cell of rows
  it adds. It never reorders or drops rows, keeps extra columns, keeps the BOM
  and line endings (Excel's "CSV UTF-8" writes both), and leaves the file
  untouched when nothing changed (so quoting style is not rewritten).
- A malformed CSV (repeated or blank header names, missing `file` column, a row
  with more cells than the header, not UTF-8) stops the kit with the line number,
  and nothing is written: guessing could lose typed data.

### What the kit fills

- `file_format` from the bytes; for PDFs `text_layer_or_scanned` is `text layer`,
  `scanned` or `mixed` using Task C's text-layer rule, and `page_count`.
  HTML is `text (html)`, images `scanned (image)`.
- `language` from the share of Devanagari vs Latin letters: >= 80% Devanagari
  "hindi", <= 20% "english", otherwise "hindi+english"; "other script" when most
  letters are neither (Gurmukhi on Punjab documents); blank under 20 letters.
  Scanned PDFs are OCR'd for this through the Task D cache (`--no-ocr` skips it
  and leaves the language blank). Images are not OCR'd: their language is left
  for a person.
- Problems (unreadable files, rows naming missing files, language not checked)
  go to the summary, not into the CSV.
- A missing `records.csv` is created from the template. A file whose four kit
  cells are all filled is not opened again (re-runs stay quick). A `records.csv`
  that is not UTF-8 (Excel's plain "CSV" on Windows) stops the kit with a
  message to save it as "CSV UTF-8".
- The OCR fixtures (`tesseract`, `devanagari_font`) moved from
  `tests/documents/conftest.py` to the root `tests/conftest.py`, since the kit's
  tests need them too.
- The summary is written to `data/manual/records-summary.md` (the kit skips it,
  `records.csv` and `README.md` when listing documents). Writing these two files
  into data/manual/ was asked for; the uploaded documents themselves are only
  ever read (CLAUDE.md layout rule).

### Found in the Task F review (fixed in a follow-up commit)

- **A stray double quote could merge rows**: Python's `csv` module, by default,
  reads an unterminated quote as one cell running to the end of the file, and
  writing that back would have merged the following rows into it. The reader is
  now strict, and broken quoting stops the kit with the line number. (A stray
  quote that a later inch mark closes still parses; see the next section.)
- **No language guess from doubtful OCR**: if any OCR'd page has low quality,
  the language cell stays blank and the summary says why. A misread script can
  look like another language, and a wrong pre-filled value is worse than a
  blank one. The guide asks for the language to be checked by eye; a Hindi
  document in a legacy non-Unicode font (Kruti Dev) has a Latin-looking text
  layer and is classed `english`: a known gap, stated in the guide.
- **HTML pages are parsed in the sandbox** with the per-page time limit and
  memory cap (CLAUDE.md: a size limit and a timeout on every parse), reading at
  most the first 5 MB; only three letter counts come back. A page saved in a
  legacy encoding, or starting with plain text, is still recognised as HTML by
  its `.htm`/`.html` name (unless it contains binary data).
- **records.csv writes are durable and keep permissions**: the file is fsynced,
  the folder synced after the rename, and the file keeps its mode (the temporary
  file used to make it owner-only); a new file gets the mode the umask allows.
- **Excel's trailing empty columns** (blank header names at the end) are
  dropped instead of stopping the kit; a value typed under such a column still
  stops it, as does a row wider than the header row.
- **File names compare stripped on both sides**, so a file named `a.pdf ` no
  longer gets a new row on every run.
- **The summary has no timestamp**, so re-running on unchanged records leaves
  it byte-for-byte unchanged (no git noise). It now also counts records by
  portal and notice type, and which fields each portal's records contain.
  Typed values and file names are flattened and escaped so they cannot break
  its tables.
- The committed `data/manual/records.csv` is no longer read by any test: its
  rows and columns are the user's data. The template itself is tested instead
  (written out and read back by the kit's reader).
- Tests moved out of `tests/datacheck/test_ocr_path.py` into `test_inspect.py`
  and `test_run.py`, so test files mirror modules (CLAUDE.md layout).
- The guide now covers robots.txt that errors, redirects or shows a captcha
  (treated as not allowing automated access), an empty `Disallow:`,
  year-month-day dates, safer spreadsheet use (Google Sheets, or Excel's
  UTF-8 import), and every value the kit writes.
- **Not changed (your decision)**: CLAUDE.md says files in `data/manual/` are
  never modified, while the task asks the kit to fill in
  `data/manual/records.csv` and write `records-summary.md` there. The kit only
  ever reads the uploaded documents; adding an explicit exception for these two
  files would change a CLAUDE.md rule, which I may not do.

### Found in the review of the Task F fixes (fixed in a follow-up commit)

- **Edits saved while the kit ran were overwritten**: records.csv was read at
  the start and replaced at the end, and OCR can take minutes. The kit now
  keeps the bytes it read and refuses to replace a file that changed, was
  removed or was created meanwhile; your version is kept and it asks you to run
  it again.
- **A stray quote closed by an inch mark** (`"Rs 5 lakh in words` on one row,
  `DI pipe 6"` on a later one) is valid CSV, so strict parsing could not see
  it, and the rows in between ended up inside one cell. A cell whose later
  lines carry at least half as many commas as the table has columns now stops
  the kit with the line where the cell starts. A note that spans lines and
  contains a comma or two is still accepted.
- The mode is set on the open temporary file before it is synced, so it is
  durable too, and a new file respects the umask (it was always `0644`).
- The test that HTML is read in a worker now fails if it is read in the parent.

