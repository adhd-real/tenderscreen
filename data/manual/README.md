# data/manual

Public tender and award documents (PDFs, HTML saves, scans) that you upload
here through GitHub ("Add file" → "Upload files"). Cloud sessions start
fresh, so committed files in this folder are how they can read real documents.

- Only public documents. Nothing confidential, and no bidder's private financials.
- Claude reads these files but never edits, renames or deletes them.
- Tip: name files `<issuing-org>_<tender-id>.<ext>` so they are easy to refer to.

Scratch downloads go in `data/raw/` instead, which git ignores.

`records.csv` describes these files, one row per record, and
`records-summary.md` is written by the data check kit
(`python -m tenderscreen.datacheck`). The step-by-step guide is
[docs/data-check.md](../../docs/data-check.md).
