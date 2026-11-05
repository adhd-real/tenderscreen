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
  and their sha256. On every read the checksum is verified and the payload must
  decode and validate; otherwise the entry is deleted, counted in
  `stats.discarded`, and the step runs again. Errors are never cached.
- Storing a result for a new version deletes that document's older versions of
  the step, so "changing a step's version invalidates its entries" holds per
  document as it is reprocessed; `invalidate_step(step, keep_version)` purges a
  step everywhere at once.
- `extract_pages(path, cache=...)` uses step `extract_pages` with a version
  string that includes the code version, the PDFium build, the Tesseract
  version and every setting that changes the output (languages, thresholds,
  DPI, ...). So upgrading Tesseract or changing a threshold recomputes, and
  settings that cannot change the output (workers, time limits) do not.
- Cached pages are JSON with a strict decoder (exact keys, types, enum values,
  pages numbered 1..n). Anything else is treated as corrupt and recomputed.
