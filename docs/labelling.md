# How to label a document

Labels are your hand-checked answers for one document. The evaluation harness
compares the pipeline's predictions with them, field by field, and reports
accuracy, misses and inventions. Good labels are the yardstick for every later
change, so take your time: one carefully labelled document is worth more than
five rushed ones.

## What you need

- A public tender or award document in `data/manual/` (see
  [data-check.md](data-check.md) for how to save and upload one).
- A PDF viewer that shows page numbers (any browser works).
- The template: [`data/labels/_template.json`](../data/labels/_template.json).

## Step by step

### 1. Create the labels file

1. Note the document's file name, for example `pwd_award_2026_17.pdf`.
2. Copy `data/labels/_template.json` to `data/labels/pwd_award_2026_17.json`:
   the same name as the document, with `.json` instead of `.pdf`, and no
   leading `_`. The evaluator pairs labels and predictions by this name.
   - On GitHub: open `_template.json`, click the copy icon ("Copy raw file"),
     then go to `data/labels/`, choose **Add file → Create new file**, name it,
     and paste.
3. Replace the `"document"` value with the file name:
   `"document": "pwd_award_2026_17.pdf"`.

### 2. Fill in each field

Every field starts as

```json
"award_date": {"value": "TODO", "source_page": 1, "quote": "TODO"}
```

For each field, search the document for it, then do **one** of the following.

**If the document states it**, fill in all three parts:

```json
"award_date": {
  "value": "15/10/2026",
  "source_page": 2,
  "quote": "Date of award: 15.10.2026"
}
```

- `value`: the answer, written in a form from the table below.
- `source_page`: the page number **as your PDF viewer counts it**, starting at
  1 for the first page of the file. Not the number printed on the page: a cover
  sheet often makes those differ.
- `quote`: the exact text from that page that states the answer. Copy and paste
  it; do not retype or tidy it. Keep it short: just the clause or table row.

**If the document does not state it**, replace the whole entry with:

```json
"award_date": {"status": "not_found"}
```

Never guess, work it out from other facts, or copy it from another tender. If
you are not sure the document states it, it is `not_found`. This rule is the
same one the pipeline must follow (CLAUDE.md rule 3), so the labels must follow
it too.

### 3. Write values in a form the evaluator understands

The evaluator decides how to compare a field from its name:

| Field (name ends or starts with) | Write the value as | Examples | Compared |
|---|---|---|---|
| `…_paise` (money) | the amount as written, or whole paise | `"Rs. 25,00,000"`, `"2.5 Cr"`, `"₹ 25 lakh"`, `250000000` | exactly, in paise |
| `…_date`, `…_deadline`, `…_at` | a day-first date, time only if the document gives one | `"15/10/2026"`, `"15-Oct-2026 05:00 PM"` | exactly |
| `number_of_…`, `…_count` | a whole number | `4` or `"4"` | exactly |
| `…_percentage`, `…_percent` | a number, `%` optional | `"5"`, `"2.5%"` | as numbers (2.5 = 2.50) |
| anything else (text) | the text | `"Synthetic Builders Pvt Ltd"` | ignoring spacing and capitals |

Notes:

- **Money**: never write a decimal number such as `2500.50` without quotes; it
  is refused, because money must not pass through floating point. Write
  `"Rs. 2,500.50"` or `250050` (paise) instead. If the document gives the amount
  only in words ("Rupees five lakh only"), write the figure yourself as the value
  (`"Rs. 5,00,000"`) and paste the words as the quote.
- **Dates**: always day first, as Indian documents write them. Add a time only if
  the document states one: "15/10/2026" and "15/10/2026 00:00" count as
  different answers.
- **Hindi**: paste Devanagari text as it is. Hindi month names are understood in
  dates (`"15 अक्टूबर 2026"`), and लाख/करोड़ in money.
- **PBG**: the template has both `pbg_percentage` and `pbg_deadline`; fill in
  whichever the document states and mark the other `not_found`.

### 4. Check your labels

Run the evaluator with your labels as both the answers and the "predictions":

```bash
python -m tenderscreen.evals data/labels data/labels --out /tmp/label-check
```

- If a label is malformed (a `TODO` left over, a date that does not exist, an
  empty quote, a page number of 0), it stops and lists every problem with the
  file and field. Fix them and run it again.
- When every label is valid it prints a report with 100% accuracy, which is
  expected: the labels were compared with themselves.

You can also ask Claude in a cloud session: "check my labels in data/labels".

### 5. Commit

On GitHub, commit the new file (to a new branch, and open a pull request if you
want a second look). Cloud sessions then see it.

## Privacy

Labels are committed to the repository. Label public documents only, and copy
only what the public notice itself shows. `contact_details` often contains a
name and phone number: keep the repository private. The evaluation report
hides values of contact-like fields (contact, phone, mobile, email, address),
and the JSON summary contains no field values at all.

## Running an evaluation

When the pipeline produces predictions (one `<document>.json` per document, in
the same format, for example in `data/predictions/`):

```bash
python -m tenderscreen.evals data/labels data/predictions
```

It prints the report and writes `eval-results/report.md` and
`eval-results/summary.json` (change the folder with `--out`). For each field
the report shows:

- **Accuracy**: share of documents where the prediction matched the label
  (both `not_found` counts as a match).
- **Missed**: the label has an answer, but the prediction said `not_found`.
- **Invented**: the label says `not_found`, but the prediction gave a value.
  This is the worst kind of mistake for TenderScreen.
- **Wrong**: both gave a value, and the values differ.
- **Invalid**: the prediction file or field is missing or malformed.
- **Page agreement**: of the correct values, how many cite your `source_page`.
