"""
extractor.py  –  Universal PDF Engineering Datasheet Extractor
==============================================================

Extraction paths (all run automatically on every PDF):

TEXT & TABLES
  1. Text lines     – "Pressure : 10 Bar", "Material = SS304"
  2. Tables         – pdfplumber 2-col or 3-col parameter tables

CHECKBOXES  (all six sub-modes merged automatically)
  3a. Unicode glyphs     – ■/□/☑/☐ etc. embedded as text characters
  3b. AcroForm checkbox  – PDF form widget, field_type = CHECKBOX
  3c. AcroForm radio     – PDF form widget, field_type = RADIOBUTTON
  3d. Vector rectangle   – small filled/unfilled square drawn on page
  3e. Vector circle      – small filled/unfilled circle/oval drawn on page
  3f. Image checkbox     – tiny raster image classified by brightness/OCR

IMAGE TEXT  (OCR on every image on every page)
  4.  Embedded images    – OCR'd regardless of page text density;
                           parsed for both key-values AND checkboxes
  5.  Annotations        – FreeText / signature annotation regions
  6.  Scanned pages      – full-page OCR fallback when page has no text

OCR dependencies (optional – all other paths work without OCR):
  pip install pytesseract pymupdf Pillow
  + Tesseract engine:
      Windows  → https://github.com/UB-Mannheim/tesseract/wiki
                 then set TESSERACT_CMD below
      macOS    → brew install tesseract
      Linux    → sudo apt-get install tesseract-ocr
"""

import re
import pdfplumber

try:
    import pytesseract
    import fitz
    from PIL import Image, ImageStat
   
    pytesseract.get_tesseract_version()
    OCR_AVAILABLE = True
except Exception:
   
    OCR_AVAILABLE = False



OCR_ZOOM_FULLPAGE  = 2.5   
OCR_ZOOM_IMAGE     = 3.0  
OCR_ZOOM_ANNOT     = 4.0  
OCR_ZOOM_CBIMAGE   = 5.0  

MIN_PAGE_TEXT      = 20

CB_MIN             = 6   
CB_MAX             = 70    
CB_ASPECT          = 2.0  
CB_Y_TOL           = 16    
CB_LABEL_RIGHT     = 160   
CB_OVERLAP_TOL     = 6    

IMG_CB_DARK_THRESH = 160
IMG_CB_MIN_PT      = 6
IMG_CB_MAX_PT      = 60

MIN_OCR_TEXT       = 3

UNITS = [
    "mm","cm","m","km","in","inch","ft",
    "kg","g","lb","lbs","ton",
    "bar","bara","barg","psi","psig","kpa","mpa","pa","atm",
    "°c","°f","degc","degf",
    "w","kw","mw","hp","v","kv","mv","a","ma","hz","khz",
    "rpm","nm","n","kn",
    "l","ml","lpm","gpm","m3/h","m3","cfm",
    "%","ppm","sec","s","min","hr","hrs","years","yr",
]
_UNIT_RE = r"(?:" + "|".join(sorted((re.escape(u) for u in UNITS), key=len, reverse=True)) + r")"

KV_PATTERNS = [
    re.compile(r"^\s*(?P<key>[A-Za-z][A-Za-z0-9 /().\-]{1,60}?)\s*[:=]\s*(?P<value>.+?)\s*$"),
    re.compile(r"^\s*(?P<key>[A-Za-z][A-Za-z0-9 /().\-]{1,60}?)\s{2,}(?P<value>\S.*?)\s*$"),
]
VU_SPLIT = re.compile(
    r"^\s*(?P<value>-?\d[\d.,]*)\s*(?P<unit>" + _UNIT_RE + r")\b\.?\s*$", re.IGNORECASE
)
DS_NO_LABELS = [
    "datasheet no","datasheet number","data sheet no","doc no",
    "document no","document number","drawing no","dwg no",
    "part no","model no","model number","spec no","specification no","ds no","tag no",
]
DS_NO_RE = re.compile(
    r"(?i)\b(?:" + "|".join(re.escape(l) for l in DS_NO_LABELS)
    + r")\.?\s*[:#]?\s*([A-Za-z0-9\-_/.]{3,40})"
)

SKIP_LINES = [
    re.compile(r"^\s*$"),
    re.compile(r"^\s*page\s+\d+", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*$"),
]

CHECKED_CHARS   = set("■▪●☑☒✓✔✗✘▶◆◉")
UNCHECKED_CHARS = set("□▫○☐◯◌")
ALL_CB_CHARS    = CHECKED_CHARS | UNCHECKED_CHARS
_CB_SPLIT_RE    = re.compile(r"([" + re.escape("".join(ALL_CB_CHARS)) + r"])")

_CB_TYPE  = 2   
_RB_TYPE  = 5  
ACRO_CHECKED_VALUES = {"yes","on","true","checked","1","x"}


def _split_vu(raw):
    raw = raw.strip().strip(".")
    m = VU_SPLIT.match(raw)
    return (m.group("value"), m.group("unit")) if m else (raw, None)


def _kv(key, value, unit=None, source="text"):
    return {"key": key, "value": value, "unit": unit, "source": source}


def _ocr(fitz_page, rect=None, zoom=OCR_ZOOM_IMAGE):
    """Render a page region (or full page) and return pytesseract text."""
    if not OCR_AVAILABLE:
        return ""
    try:
        mat = fitz.Matrix(zoom, zoom)
        pix = fitz_page.get_pixmap(matrix=mat, clip=rect) if rect else fitz_page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        return (pytesseract.image_to_string(img) or "").strip()
    except Exception:
        return ""


def _img_brightness(pil_img):
    """Mean pixel brightness 0–255 (lower = darker = more likely checked)."""
    try:
        stat = ImageStat.Stat(pil_img.convert("L"))
        return stat.mean[0]
    except Exception:
        return 255.0


def _rects_overlap(r1, r2, tol=CB_OVERLAP_TOL):
    return abs(r1.x0 - r2.x0) < tol and abs(r1.y0 - r2.y0) < tol


def _plumb_words(pdf_path, page_index):
    """Return pdfplumber word dicts for one page."""
    try:
        with pdfplumber.open(pdf_path) as p:
            return [
                {"text": w["text"], "x0": float(w["x0"]), "x1": float(w["x1"]),
                 "top": float(w["top"]), "bot": float(w["bottom"])}
                for w in p.pages[page_index].extract_words()
            ]
    except Exception:
        return []


def _label_right(rect, words, x_margin=CB_LABEL_RIGHT, y_tol=CB_Y_TOL):
    """Words to the RIGHT of rect at roughly the same vertical centre."""
    cy = (rect.y0 + rect.y1) / 2
    near = [w for w in words
            if rect.x1 - 2 <= w["x0"] <= rect.x1 + x_margin
            and abs((w["top"] + w["bot"]) / 2 - cy) <= y_tol]
    near.sort(key=lambda w: (round(w["top"] / 4) * 4, w["x0"]))
    return " ".join(w["text"] for w in near)



def parse_text_lines(text):
    results = []
    if not text:
        return results
    for line in text.splitlines():
        if any(p.match(line) for p in SKIP_LINES):
            continue
        if any(ch in ALL_CB_CHARS for ch in line):
            continue   
        for pat in KV_PATTERNS:
            m = pat.match(line)
            if not m:
                continue
            key = m.group("key").strip(" -")
            val = m.group("value").strip()
            if not key or not val or len(key) < 2 or key.isdigit():
                continue
            v, u = _split_vu(val)
            results.append(_kv(key, v, u, "text"))
            break
    return results



def parse_table(table):
    results = []
    if not table:
        return results
    for row in table:
        cells = [c.strip() if c else "" for c in row]
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue
        key = cells[0]
        if not key or not re.search(r"[A-Za-z]", key):
            continue
        if key.lower() in {"parameter","field","description","item","property"}:
            continue
        if len(cells) >= 3:
            val, unit = cells[1], cells[2]
        else:
            val, unit = _split_vu(cells[1])
        results.append(_kv(key, val, unit, "table"))
    return results



def parse_unicode_checkboxes(text):
    """
    Finds ■/□/☑/☐ etc. in text and converts each to a checkbox kv.
    Lines like "Winding Temp. Monitoring:  □ Yes  ■ No" →
        "Winding Temp. Monitoring: Yes" = Unchecked
        "Winding Temp. Monitoring: No"  = Checked
    """
    results = []
    if not text:
        return results
    for line in text.splitlines():
        if not any(ch in ALL_CB_CHARS for ch in line):
            continue
        first = next((i for i, ch in enumerate(line) if ch in ALL_CB_CHARS), len(line))
        prefix_raw = line[:first].strip().rstrip(":-* ").strip()
        prefix = prefix_raw if re.search(r"[A-Za-z]", prefix_raw) else ""

        parts = _CB_SPLIT_RE.split(line)
        i = 0
        while i < len(parts):
            part = parts[i]
            if part in ALL_CB_CHARS:
                symbol    = part
                label_raw = parts[i + 1].strip() if i + 1 < len(parts) else ""
                label     = re.sub(r"[\s\*·,\-]+$", "", label_raw).strip()
                if label and re.search(r"[A-Za-z0-9]", label) and not label.isdigit():
                    full = f"{prefix}: {label}" if prefix and prefix.lower() not in label.lower() else label
                    results.append(_kv(full, "Checked" if symbol in CHECKED_CHARS else "Unchecked",
                                       source="checkbox"))
            i += 1
    return results



def _extract_acroform(fitz_page, page_idx, words):
    results = []
    try:
        widgets = list(fitz_page.widgets() or [])
    except Exception:
        return results

    radio_groups = {}
    for w in widgets:
        if w.field_type == _RB_TYPE:
            radio_groups.setdefault(w.field_name, []).append(w)

    for w in widgets:
        if w.field_type == _CB_TYPE:
            raw = (w.field_value or "").strip().lower()
            is_checked = raw in ACRO_CHECKED_VALUES and raw not in {"no","off","false","unchecked","0",""}
            label = _label_right(w.rect, words) or w.field_name.replace("_"," ").strip() or f"Checkbox p.{page_idx+1}"
            results.append((_kv(label, "Checked" if is_checked else "Unchecked", source="checkbox"), w.rect))

        elif w.field_type == _RB_TYPE:
            raw = (w.field_value or "").strip().lower()
            is_selected = raw not in {"", "off", "no", "false", "0"}
            label = _label_right(w.rect, words)
            group = w.field_name.replace("_"," ")
            full_label = f"{group}: {label}" if label and group.lower() not in label.lower() else (label or group)
            if not full_label:
                full_label = f"Radio p.{page_idx+1}"
            results.append((_kv(full_label, "Selected" if is_selected else "Unselected", source="checkbox"), w.rect))

    return results



def _is_square_ish(rect):
    w, h = rect.width, rect.height
    if w <= 0 or h <= 0:
        return False
    return CB_MIN < w < CB_MAX and CB_MIN < h < CB_MAX and max(w,h)/min(w,h) < CB_ASPECT


def _extract_vector_shapes(fitz_page, page_idx, words, skip_rects):
    """
    Detects filled/unfilled vector rectangles (squares) and
    circles/ovals as checkbox indicators.
    """
    results = []
    try:
        drawings = fitz_page.get_drawings()
    except Exception:
        return results

    outlines = [d for d in drawings if _is_square_ish(d.get("rect") or fitz.Rect())]
    filled   = [d for d in drawings if d.get("fill") is not None and
                _is_square_ish(d.get("rect") or fitz.Rect())]

    outer = []
    for d in outlines:
        dominated = any(_rects_overlap(d["rect"], o["rect"]) and
                        d["rect"].width <= o["rect"].width for o in outer)
        if not dominated:
            outer.append(d)

    for d in outer:
        rect = d["rect"]
      
        if any(_rects_overlap(rect, sr) for sr in skip_rects):
            continue
        items = d.get("items", [])
        item_types = {it[0] for it in items}

        if "re" in item_types:
            shape = "square"
        elif "c" in item_types:
            shape = "circle"
        else:
            continue

        is_checked = any(_rects_overlap(rect, fd["rect"]) for fd in filled
                         if fd.get("rect") is not None)

        label = _label_right(rect, words)
        if not label:
            label = f"{shape.capitalize()} checkbox p.{page_idx+1}"

        results.append((_kv(label, "Checked" if is_checked else "Unchecked", source="checkbox"), rect))

    return results



_CB_OCR_CHARS = re.compile(r"[■□☑☐✓✔✗✘xX×]")

def _extract_image_checkboxes(fitz_page, page_idx, words, skip_rects):
    """
    Finds small raster images on the page (could be checkbox icons,
    tick marks, bullet graphics) and classifies them as checked/unchecked
    using two methods:
      1. OCR – if the image contains a checkmark or cross character
      2. Brightness – if image is predominantly dark (filled = checked)
    Only fires on images in the CB_MIN–CB_MAX pt size range.
    """
    results = []
    if not OCR_AVAILABLE:
        return results
    try:
        images = fitz_page.get_images(full=True)
    except Exception:
        return results

    for img in images:
        xref = img[0]
        try:
            rects = fitz_page.get_image_rects(xref)
        except Exception:
            continue
        for rect in rects:
            w, h = rect.width, rect.height
            if not (CB_MIN < w < CB_MAX and CB_MIN < h < CB_MAX):
                continue
            if any(_rects_overlap(rect, sr) for sr in skip_rects):
                continue
            try:
                mat = fitz.Matrix(OCR_ZOOM_CBIMAGE, OCR_ZOOM_CBIMAGE)
                pix = fitz_page.get_pixmap(matrix=mat, clip=rect)
                pil_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            except Exception:
                continue

            ocr_text = (pytesseract.image_to_string(
                pil_img, config="--psm 10 -c tessedit_char_whitelist=■□☑☐✓✔✗✘xX×01") or "").strip()
            has_checkmark = bool(_CB_OCR_CHARS.search(ocr_text))
            checked_by_ocr = has_checkmark and any(c in CHECKED_CHARS or c in "✓✔xX×1" for c in ocr_text)

            brightness = _img_brightness(pil_img)
            checked_by_brightness = brightness < IMG_CB_DARK_THRESH

            is_checked = checked_by_ocr or checked_by_brightness

            label = _label_right(rect, words)
            if not label:
                continue 

            results.append((_kv(label, "Checked" if is_checked else "Unchecked", source="checkbox"), rect))

    return results


def extract_checkboxes(pdf_path):
    """
    Runs all six checkbox sub-modes on every page and returns a
    deduplicated flat list of {key, value, unit, source} dicts.
    """
    if not OCR_AVAILABLE:
        return []
    results = []
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return []

    for page_idx in range(len(doc)):
        fitz_page = doc[page_idx]
        words     = _plumb_words(pdf_path, page_idx)

        acro_items  = _extract_acroform(fitz_page, page_idx, words)
        acro_rects  = [item[1] for item in acro_items]
        results.extend(item[0] for item in acro_items)

        vec_items   = _extract_vector_shapes(fitz_page, page_idx, words, acro_rects)
        vec_rects   = [item[1] for item in vec_items]
        results.extend(item[0] for item in vec_items)

        img_items   = _extract_image_checkboxes(fitz_page, page_idx, words,
                                                 acro_rects + vec_rects)
        results.extend(item[0] for item in img_items)

    doc.close()
    return results


def extract_all_image_text(pdf_path):
    """
    OCRs EVERY embedded image on every page (regardless of page text density)
    and every non-widget annotation (FreeText, stamps, signatures).

    Returns (image_results, annotation_results) where each is a list of:
        {"page": int, "text": str, "kv": [...], "cb": [...]}

    'kv'  = key-value pairs parsed from the OCR text
    'cb'  = checkbox entries parsed from the OCR text
    'text' = raw OCR string
    """
    img_results  = []
    ann_results  = []
    if not OCR_AVAILABLE:
        return img_results, ann_results
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return img_results, ann_results

    for page_idx in range(len(doc)):
        fitz_page  = doc[page_idx]
        seen_rects = []

        try:
            images = fitz_page.get_images(full=True)
        except Exception:
            images = []

        for img in images:
            xref = img[0]
            try:
                rects = fitz_page.get_image_rects(xref)
            except Exception:
                continue
            for rect in rects:
                if rect.is_empty or rect.width < 4 or rect.height < 4:
                    continue
                text = _ocr(fitz_page, rect=rect, zoom=OCR_ZOOM_IMAGE)
                seen_rects.append(rect)
                if len(text) >= MIN_OCR_TEXT and re.search(r"[A-Za-z0-9]", text):
                    img_results.append({
                        "page": page_idx + 1,
                        "text": " ".join(text.split()),
                        "kv":   parse_text_lines(text),
                        "cb":   parse_unicode_checkboxes(text),
                    })

        try:
            annots = list(fitz_page.annots() or [])
        except Exception:
            annots = []

        for annot in annots:
            rect = annot.rect
            if rect.is_empty or rect.width < 4 or rect.height < 4:
                continue
            if any(rect.intersects(r) for r in seen_rects):
                continue
            text = _ocr(fitz_page, rect=rect, zoom=OCR_ZOOM_ANNOT)
            if len(text) >= MIN_OCR_TEXT and re.search(r"[A-Za-z]", text):
                ann_results.append({
                    "page": page_idx + 1,
                    "text": " ".join(text.split()),
                    "kv":   parse_text_lines(text),
                    "cb":   parse_unicode_checkboxes(text),
                })

    doc.close()
    return img_results, ann_results


def find_datasheet_no(full_text):
    if not full_text:
        return None
    m = DS_NO_RE.search(full_text)
    return m.group(1).strip() if m else None



def extract_from_pdf(pdf_path):
    """
    Runs all extraction paths. Returns:
    {
        "page_count"       : int,
        "full_text"        : str,
        "datasheet_no"     : str | None,
        "key_values"       : [{key, value, unit, source}, ...],
        "ocr_used_on_pages": [int, ...],
        "signatures"       : [{page, text}, ...],
        "image_texts"      : [{page, text, kv, cb}, ...],
        "checkboxes_found" : int,
    }
    """
    all_kv            = []
    full_text_parts   = []
    ocr_used_on_pages = []


    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)

        for page_idx, page in enumerate(pdf.pages):
            page_text = page.extract_text() or ""

            tables = page.extract_tables()
            table_cell_text = set()
            for table in tables:
                all_kv.extend(parse_table(table))
                for row in table:
                    for cell in row:
                        if cell:
                            table_cell_text.add(cell.strip())

            if len(page_text.strip()) < MIN_PAGE_TEXT and not tables and OCR_AVAILABLE:
                try:
                    doc_tmp = fitz.open(pdf_path)
                    ocr_text = _ocr(doc_tmp[page_idx], zoom=OCR_ZOOM_FULLPAGE)
                    doc_tmp.close()
                except Exception:
                    ocr_text = ""
                if ocr_text.strip():
                    page_text = ocr_text
                    ocr_used_on_pages.append(page_idx + 1)
                    all_kv.extend(parse_text_lines(ocr_text))
                    all_kv.extend(parse_unicode_checkboxes(ocr_text))
                    full_text_parts.append(ocr_text)
                    continue

            full_text_parts.append(page_text)
            remaining = [l for l in page_text.splitlines()
                         if l.strip() not in table_cell_text]
            text_only = "\n".join(l for l in remaining
                                  if not any(ch in ALL_CB_CHARS for ch in l))
            cb_text   = "\n".join(remaining)
            all_kv.extend(parse_text_lines(text_only))
            all_kv.extend(parse_unicode_checkboxes(cb_text))

    full_text = "\n".join(full_text_parts)

    all_kv.extend(extract_checkboxes(pdf_path))

    image_results, ann_results = extract_all_image_text(pdf_path)

    for ir in image_results:
        if ir["kv"]:
            all_kv.extend(ir["kv"])  
        if ir["cb"]:
            all_kv.extend(ir["cb"])
        if not ir["kv"] and not ir["cb"]:
            all_kv.append(_kv(f"Image Text (p.{ir['page']})", ir["text"][:200], source="image"))

    signatures = []
    for ar in ann_results:
        signatures.append({"page": ar["page"], "text": ar["text"]})
        all_kv.append(_kv("Signed By", ar["text"], source="signature"))

    seen, deduped = set(), []
    for item in all_kv:
        sig = (item["key"].lower(),
               str(item.get("value","")).lower(),
               str(item.get("unit") or "").lower())
        if sig not in seen:
            seen.add(sig)
            deduped.append(item)

    return {
        "page_count"       : page_count,
        "full_text"        : full_text,
        "datasheet_no"     : find_datasheet_no(full_text),
        "key_values"       : deduped,
        "ocr_used_on_pages": ocr_used_on_pages,
        "signatures"       : signatures,
        "image_texts"      : image_results,
        "checkboxes_found" : sum(1 for kv in deduped if kv.get("source") == "checkbox"),
    }
