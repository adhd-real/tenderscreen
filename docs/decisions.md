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
