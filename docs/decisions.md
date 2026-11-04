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
