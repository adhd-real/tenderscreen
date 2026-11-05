# Data check: what do the portals really publish?

Before we build any fetcher, we need to know, from real documents, what each
portal publishes, in what form, and whether we may collect it automatically.
This guide is for doing that check by hand, in your browser, for three portals:

| Portal | Address | What it is |
|---|---|---|
| CPPP | `https://eprocure.gov.in` | Central Public Procurement Portal: central ministries, PSUs |
| Punjab eProcurement | `https://eproc.punjab.gov.in` | Punjab government departments |
| GeM | `https://gem.gov.in` | Government e-Marketplace: bids and orders |

You save documents into `data/manual/`, describe each one in
`data/manual/records.csv`, and run the kit, which pre-fills what it can and
counts which fields the documents actually contain.

> I (Claude) could not open these portals while writing this: the session had
> no internet access. Menu names below are what such portals usually show;
> portals change, so record what **you** see.

## Ground rules

- **Public pages only.** Do not log in to collect documents for this check.
  If something is visible only after logging in, write that in `notes`.
- **Never get around a captcha.** If a page asks for one, you may solve it
  yourself as a normal visitor, but record `captcha_needed = y`: it tells us
  that page cannot be fetched automatically.
- **Go at a human pace.** No download managers, browser extensions that bulk
  download, or scripts.
- **No personal data in records.csv.** For contact details write only `y` or
  `n` in `has_contact_details`, never the names or phone numbers themselves.

## Step 1: check robots.txt (5 minutes per portal)

`robots.txt` is the file where a site says which parts automated programs may
visit. TenderScreen obeys it (CLAUDE.md rule 5), so we need to know what it says.

1. In the address bar, type the site address followed by `/robots.txt`, for
   example `https://eprocure.gov.in/robots.txt`, and press Enter. Repeat for
   `https://eproc.punjab.gov.in/robots.txt`, `https://gem.gov.in/robots.txt`
   and any other host the documents come from (for example a separate host
   for bid listings or document downloads; the address bar shows it).
2. Read what you see:
   - `User-agent: *` starts the rules for all programs; rules under a named
     agent (e.g. `User-agent: Googlebot`) apply only to that one.
   - `Disallow: /path` means programs must not visit addresses starting with
     `/path`. `Disallow: /` means the whole site. A `Disallow:` with nothing
     after the colon disallows nothing.
   - `Allow: /path` makes an exception to a Disallow.
   - `Crawl-delay: 10` asks programs to wait 10 seconds between requests.
   - A "Page not found" (404) page means the site has no robots.txt: it states
     no rules.
   - If you get anything else instead of a short plain-text file (a server
     error, "access denied", a login page, a captcha, or the address bar
     changes to another page), write down exactly what happened. Until that is
     cleared up, TenderScreen treats the site as **not** allowing automated
     access.
3. For every record from that host, write a short note in the
   `robots_txt_notes` column, for example
   `2026-11-05: User-agent * Disallow /cppp/ ; no crawl-delay`,
   `2026-11-05: no robots.txt (404)` or
   `2026-11-05: robots.txt redirects to home page`. Include the date:
   robots.txt changes.

## Step 2: find and save documents (aim for 10 to 15 per portal)

Try to get a mix: award notices *and* tender notices, several departments,
English and Hindi, recent and a year old. Award notices matter most: they are
where award date, winning bidder and contract value appear.

### CPPP (eprocure.gov.in)

- Look for menus such as **Active Tenders**, **Tenders by Organisation**,
  **Tender Status** or **Results of Tenders** / **Award of Contract (AOC)**.
- A tender's page usually links its documents: the notice inviting tender
  (NIT, usually PDF), and often a bill of quantities (BOQ, spreadsheet).
- Award details are often an HTML page rather than a PDF. Save it with
  Ctrl+S (Cmd+S on Mac) as **Webpage, HTML only**.

### Punjab eProcurement (eproc.punjab.gov.in)

- Expect similar menus (**Tenders by Organisation**, **Tender Status**,
  **Results of Tenders**). Search pages on such portals often ask for a captcha;
  record it.
- Documents may be in English, Hindi or Punjabi. The kit recognises Hindi
  (Devanagari) and English; Punjabi in Gurmukhi script shows as
  `other script`.

### GeM (gem.gov.in)

- Bids are usually listed on a separate bids site linked from the GeM home page
  (look for **Bids** or **All Bids**). Each bid usually has a bid document PDF.
- Look for bid results or award / contract information on the bid's page, and
  note whether it is visible without logging in.

### Saving, for every portal

- **Save the original file.** If the portal offers a PDF, download that PDF; do
  not print the page to PDF instead. If the information is only an HTML page,
  save the page (HTML only).
- **Name files** `<portal>_<organisation>_<tender id>.<ext>`, lower case, no
  spaces, for example `cppp_nhai_2026_123456.pdf` or
  `punjab_pwd_2026_ab12.html`. Subfolders per portal are fine
  (`data/manual/gem/...`).
- **Keep the address.** Copy the page address (URL) where you found the
  document into the `url` column.

## Step 3: describe each document in records.csv

`data/manual/records.csv` has one row per record (usually one per file). You
can leave the four kit columns empty; the kit fills them.

| Column | What to write |
|---|---|
| `file` | the file name inside `data/manual/` (with subfolder if any), e.g. `gem/gem_moe_2026_77.pdf`. Leave empty if you could not save a file. |
| `portal` | `CPPP`, `Punjab` or `GeM` |
| `url` | the page address where you found it |
| `date_found` | the day you found it, as year-month-day: `2026-11-05` (the same form as in `robots_txt_notes`; it sorts correctly and cannot be read with day and month swapped) |
| `notice_type` | `award` or `tender` |
| `captcha_needed` | `y` or `n`: did any step to reach the document ask for a captcha? |
| `robots_txt_notes` | from Step 1 |
| `language` | kit fills for PDFs and web pages: `english`, `hindi`, `hindi+english` or `other script`. It leaves the cell empty for images, for scans whose OCR quality is low, and when there is too little text: fill those in by eye. |
| `file_format` | kit fills: `pdf`, `html`, `jpeg`, `png`, ... |
| `text_layer_or_scanned` | kit fills: `text layer`, `scanned` or `mixed` (PDF pages differ) for PDFs, `text (html)` for web pages, `scanned (image)` for image files |
| `page_count` | kit fills (PDFs) |
| `has_award_date` ... `has_contact_details` | `y` if the document states it, `n` if it does not. `has_pbg_percentage_or_deadline` is `y` if either the PBG percentage or its deadline is given. |
| `notes` | anything else: "login needed for result", "amount only in words", ... |

If you type something in a kit column yourself, the kit leaves it alone.

**Check the `language` the kit filled in by eye**, at least once per portal. It
counts Devanagari against Latin letters, so a Hindi document typed in an old
non-Unicode font (such as Kruti Dev, common in government offices) has a text
layer of Latin gibberish and comes out as `english`. Correct the cell; the kit
will not change it back.

## Step 4: upload through GitHub

1. Open the repository on github.com and go into the `data/manual` folder
   (on the branch you work on; `main` is fine).
2. Click **Add file → Upload files**, drag in the documents (and your edited
   `records.csv`, which replaces the old one), and click **Commit changes**.
   GitHub's web upload takes files up to 25 MB each.
3. To edit `records.csv` without downloading it: open the file, click the pencil
   icon, edit the text, and commit. Each line is one record; keep the commas.
4. If you prefer a spreadsheet: download the file, edit it in Excel or Google
   Sheets, and save it as CSV: Excel **CSV UTF-8 (Comma delimited)**, Google
   Sheets **File → Download → Comma-separated values**. Then upload it as in
   step 2. (UTF-8 keeps Hindi text intact.)
   - **Google Sheets is the safer choice** (**File → Import → Upload**).
     Opening the CSV by double-clicking it in Excel can garble Hindi text and
     turn dates and long numbers into something else, which is then saved back.
     In Excel, use **Data → From Text/CSV** instead, choose the file origin
     **65001: Unicode (UTF-8)**, and load the columns as text. (Menu names vary
     a little between Excel versions.)
   - Empty columns that Excel sometimes adds at the end are fine: the kit drops
     them.

## Step 5: run the kit

In a Claude Code cloud session, ask: "run the data check kit and commit the
results". Or run it yourself in the project folder:

```bash
python -m tenderscreen.datacheck
```

It will:

1. List every file in `data/manual/` (subfolders too; not `README.md`,
   `records.csv` or `records-summary.md`).
2. Add a row to `records.csv` for any file that has none (and create
   `records.csv` from the template if it is missing).
3. Fill **only empty cells** in `file_format`, `text_layer_or_scanned`,
   `page_count` and `language`. Anything you typed is never changed; running it
   again changes nothing you entered. Files whose four cells are all filled are
   not opened again.
4. Write `data/manual/records-summary.md`: how many records contain each field
   (`y` / `n` / blank), records by portal and notice type, which fields each
   portal's records contain, counts by captcha, language, format and
   scanned/text, and a list of problems (unreadable files, rows naming files
   that do not exist, files whose language it could not check). Running it
   again on unchanged records leaves the summary unchanged.

Scanned PDFs are OCR'd (English and Hindi) to detect their language; this needs
Tesseract and can take a minute per long document. Results are cached in
`data/raw/`, so a second run is quick. To skip OCR:

```bash
python -m tenderscreen.datacheck --no-ocr
```

If `records.csv` has a broken row (one cell too many because of a stray comma,
or a stray double quote that would run several rows together), the kit stops,
names the line, and changes nothing.

## What to look for in the summary

- **Fields present**: if "contract value" or "successful bidder name" is
  missing from most award notices on a portal, that portal cannot feed those
  features.
- **Scanned share**: many scanned documents mean OCR quality matters most.
- **Captcha and robots.txt**: a captcha on the path, or a `Disallow` covering
  the pages we need, means that portal's documents must come through manual
  upload, not a fetcher.
