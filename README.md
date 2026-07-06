# Engineering Datasheet — Data Extraction & Validation Tool

A tool that reads engineering / vendor PDF datasheets, converts their
content into a standard Key-Value structure, checks for duplicates, and
stores everything in a SQLite database. Available as both a **web app**
(for office/team use) and a **CLI** (for scripting/automation).

## 0. Web App (recommended for office use)

```bash
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000** in a browser. From there you can:
- Drag-and-drop or browse to upload one or more PDF datasheets
- See extraction results and duplicate/error alerts inline
- Browse the registry of all processed documents
- Click into any document to see its extracted Key/Value/Unit table
- Download a single document's data, or all documents, as `.xlsx`
- Search parameters by name across every processed document

To run it for your whole team rather than just your own machine, run it
on a shared office PC/server and have colleagues open
`http://<that-machine's-IP>:5000` on the same network (change
`app.run(host="127.0.0.1", ...)` to `host="0.0.0.0"` in `app.py` first).
This is a development server suitable for internal/office use; for
public internet exposure, front it with a production WSGI server
(gunicorn/waitress) instead.

## 1. CLI Setup (alternative to the web app)

```bash
pip install -r requirements.txt
python main.py init
```

This creates `datasheets.db` (SQLite file) in the project folder.

## 2. Usage

```bash
# Process one or more PDFs
python main.py process path/to/datasheet.pdf
python main.py process sheet1.pdf sheet2.pdf sheet3.pdf

# Force re-processing even if flagged as a duplicate
python main.py process sheet1.pdf --force

# List all processed documents
python main.py list

# View extracted key-value data for a specific document
python main.py view 1

# Search extracted parameters by key name (across all documents)
python main.py search pressure
```

Every run prints clear status tags so issues are visible immediately:
- `[OK]`        — successfully processed and stored
- `[DUPLICATE]` — skipped, matched an existing document (hash or datasheet no.)
- `[WARNING]`   — soft issue (e.g. same file name but different content,
                   or no parameters could be extracted)
- `[ERROR]`     — file missing, not a PDF, or extraction failed

## 3. Solution Architecture

```
                ┌──────────────┐
   PDF file --->│  extractor.py │--- text + tables (pdfplumber)
                └──────┬───────┘
                       │  key_values[], datasheet_no, page_count
                       v
                ┌──────────────┐
                │   dedup.py    │--- sha256 hash / datasheet no / filename check
                └──────┬───────┘
                       │ is_duplicate?
                       v
                ┌──────────────┐
                │     db.py     │--- SQLite read/write (documents, parameters)
                └──────┬───────┘
                       │
                       v
                ┌──────────────┐
                │   main.py     │--- CLI: process / list / view / search
                └──────────────┘
```

**Modules**
| File | Responsibility |
|---|---|
| `extractor.py` | Opens the PDF, pulls text + tables, parses both into a flat list of `{key, value, unit, source}`. Also tries to detect a vendor "Datasheet No." |
| `dedup.py` | Computes a SHA-256 file hash and checks it (and the datasheet number / file name) against the database to decide if a file was already processed. |
| `db.py` | SQLite schema + all CRUD helper functions. |
| `main.py` | Command-line interface tying the above together, with status/alert messages. |

This separation means each piece can be swapped later — e.g. `db.py`
could point at PostgreSQL instead of SQLite, or `main.py` could be
replaced with a Flask/Streamlit UI, without touching the extraction logic.

## 4. Extraction Approach

Two complementary techniques are used because vendor datasheets vary a lot
in layout:

1. **Tables** — `pdfplumber.page.extract_tables()` is tried first on every
   page. Rows are interpreted as:
   - `[Parameter, Value, Unit]` (3 columns), or
   - `[Parameter, "10 Bar"]` (2 columns, value+unit split with a regex).
   Header rows (e.g. "Parameter / Value / Unit") are auto-skipped.

2. **Free text lines** — Anything not already captured by a table is
   scanned line-by-line with regex patterns that match common
   `Key : Value`, `Key = Value`, or `Key   Value` (aligned with spaces)
   layouts. A trailing unit (Bar, °C, kW, RPM, kg, etc.) is split off the
   value automatically using a configurable unit list (`UNITS` in
   `extractor.py`).

3. **Datasheet number detection** — a regex looks for common vendor
   labels ("Datasheet No", "Doc No", "Drawing No", "Part No", "Model No",
   etc.) followed by an alphanumeric code, used later for duplicate
   detection.

**Why this generalizes across layouts:** nothing is hard-coded to a
specific datasheet's wording or position — it relies on generic
punctuation/spacing patterns and a unit dictionary, so it works whether
the source PDF uses a paragraph-style spec list or a multi-column
parameter table. The unit list and key-value regexes can be extended in
`extractor.py` as new datasheet formats are encountered.

**Known limitations (prototype stage):**
- Multi-column page layouts where two unrelated columns sit side-by-side
  can occasionally interleave text incorrectly; `pdfplumber`'s layout
  mode helps but isn't perfect for every vendor template.
- Free-text key detection is regex-based, so highly irregular phrasing
  ("Max. allowable working pressure is around 10 bar") won't be picked
  up as a clean key-value pair — only labelled spec lines/tables are.

### Scanned / image-based PDFs (OCR)

Some datasheets are scans or photos with no real text layer — pdfplumber
can't read those directly. The extractor automatically detects pages
like this (very little/no extractable text and no tables) and falls
back to OCR, reading the page as an image instead.

This needs two extra pieces installed:

```bash
pip install pytesseract pymupdf Pillow
```

...plus the **Tesseract OCR engine** itself (a separate non-Python
program, not just a pip package):

- **Windows**: download and run the installer from
  https://github.com/UB-Mannheim/tesseract/wiki — then, if `tesseract`
  isn't automatically on your PATH, open `extractor.py` and uncomment
  the line setting `pytesseract.pytesseract.tesseract_cmd` to your
  install path (typically `C:\Program Files\Tesseract-OCR\tesseract.exe`).
- **macOS**: `brew install tesseract`
- **Linux**: `sudo apt-get install tesseract-ocr`

If pytesseract/pymupdf aren't installed, or Tesseract isn't found, OCR
is silently skipped — normal PDFs with a real text layer keep working
exactly as before. When OCR does kick in on a document, the web UI
flashes a note ("OCR was used on page(s) ...") so you know that page's
data came from image recognition rather than the embedded text layer —
worth a manual double-check on accuracy, since OCR isn't 100% perfect,
especially on low-quality scans.

## 5. Database Design

SQLite, two tables (see `db.py` for the exact SQL):

### `documents` — one row per processed PDF
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| file_name | TEXT | original file name |
| file_path | TEXT | absolute path at time of processing |
| file_hash | TEXT, UNIQUE | SHA-256 of file bytes — primary duplicate signal |
| datasheet_no | TEXT | extracted vendor/document number, if found |
| page_count | INTEGER | |
| processed_at | TEXT | timestamp, defaults to now |
| status | TEXT | processed / duplicate / error |

### `parameters` — one row per extracted key/value
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| document_id | INTEGER FK → documents.id | cascade delete |
| param_key | TEXT | e.g. "Pressure" |
| param_value | TEXT | e.g. "10" |
| unit | TEXT | e.g. "Bar" (nullable, for non-numeric fields like Material) |
| source | TEXT | "text" or "table" — where it was extracted from |

```
documents (1) ────< (many) parameters
     id                     document_id (FK)
```

A simple one-to-many ER relationship: each datasheet (document) can have
any number of extracted parameters. Indexes are added on `file_hash`,
`datasheet_no`, and `parameters.param_key` to keep duplicate checks and
searches fast as the table grows.

## 6. Duplicate Detection Logic

Checked in this order, in `dedup.py::check_duplicate()`:

1. **File hash (SHA-256)** — strongest signal. Catches the exact same
   file even if renamed. → hard duplicate, processing skipped.
2. **Datasheet number** — if the PDF's extracted "Datasheet No. / Doc
   No." matches one already in the database, it's treated as the same
   datasheet (e.g. re-exported or re-saved with different bytes) →
   hard duplicate, processing skipped.
3. **File name only** — weakest signal, since two unrelated documents
   can share a name. This produces only a soft `[WARNING]`; the file is
   still processed and stored.

`--force` on the `process` command bypasses the hard-duplicate skip if
you intentionally want to re-extract and store a new record (e.g. after
fixing the extraction logic).

## 7. Extending Toward the Full Prototype

This CLI is the extraction/storage core. To reach the full "portal" in
the original task list later, you'd layer:
- A UI (Streamlit/Flask) that calls `extractor.extract_from_pdf()`,
  `dedup.check_duplicate()`, and the `db.py` functions directly — no
  changes needed to this core logic.
- File upload handling that saves the upload to disk, then calls the
  same `process` flow used by the CLI.
- The same `[OK]/[DUPLICATE]/[WARNING]/[ERROR]` messages, rendered as
  UI alerts instead of console output.
