"""
app.py
------
Web interface for the Engineering Datasheet Extraction & Validation tool.

Run with:
    pip install -r requirements.txt
    python app.py

Then open http://127.0.0.1:5000 in a browser.

This is a thin Flask layer over the existing core modules (extractor.py,
dedup.py, db.py) -- no extraction/storage logic was duplicated here.
"""

import os
from io import BytesIO

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, send_file, jsonify
)
import pandas as pd

import db
import dedup
import extractor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = "datasheet-extractor-office-tool"  
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024 

db.init_db()


def get_db():
    return db.get_connection()


@app.route("/")
def index():
    conn = get_db()
    documents = db.list_documents(conn)
    stats = {
        "total_documents": len(documents),
        "total_parameters": sum(
            len(db.get_parameters(conn, d["id"])) for d in documents
        ) if documents else 0,
    }
    conn.close()
    return render_template("index.html", documents=documents, stats=stats,
                           ocr_available=extractor.OCR_AVAILABLE)


@app.route("/upload", methods=["POST"])
def upload():
    files = request.files.getlist("pdf_files")
    force = request.form.get("force") == "on"

    if not files or files[0].filename == "":
        flash("No file selected.", "error")
        return redirect(url_for("index"))

    conn = get_db()
    results = []

    for file in files:
        filename = file.filename
        if not filename.lower().endswith(".pdf"):
            results.append({"file": filename, "status": "error",
                             "message": "Not a PDF file."})
            continue

        save_path = os.path.join(UPLOAD_DIR, filename)
        file.save(save_path)

        
        try:
            extracted = extractor.extract_from_pdf(save_path)
        except Exception as e:
            
            err_msg = str(e)
            if "tesseract" in err_msg.lower():
                err_msg = (
                    "Tesseract OCR engine is not installed or not on PATH. "
                    "Install it from https://github.com/UB-Mannheim/tesseract/wiki "
                    "(Windows) or via 'brew install tesseract' / "
                    "'sudo apt-get install tesseract-ocr', then restart the app. "
                    "Normal text/table extraction will work without it."
                )
            results.append({"file": filename, "status": "error", "message": err_msg})
            continue

        dup_info = dedup.check_duplicate(
            conn, save_path, filename, extracted["datasheet_no"]
        )

        if dup_info["is_duplicate"] and not force:
            existing = dup_info["existing_document"]
            results.append({
                "file": filename, "status": "duplicate",
                "message": f"Matches existing document #{existing['id']} "
                           f"('{existing['file_name']}') via {dup_info['reason']}.",
                "document_id": existing["id"],
            })
            continue

        warning = None
        if dup_info["reason"] == "filename" and not dup_info["is_duplicate"]:
            existing = dup_info["existing_document"]
            warning = f"Same file name as document #{existing['id']}, but content differs."

        if extracted.get("ocr_used_on_pages"):
            pages = ", ".join(str(p) for p in extracted["ocr_used_on_pages"])
            note = f"OCR was used on page(s) {pages} (scanned/image-only)."
            warning = (warning + " " if warning else "") + note

        if extracted.get("checkboxes_found"):
            note = f"{extracted['checkboxes_found']} checkbox(es) extracted."
            warning = (warning + " " if warning else "") + note

        if extracted.get("signatures"):
            names = ", ".join(f"\"{s['text']}\" (p.{s['page']})" for s in extracted["signatures"])
            note = f"Signature detected: {names}."
            warning = (warning + " " if warning else "") + note

        if extracted.get("image_texts"):
            note = f"{len(extracted['image_texts'])} image region(s) OCR'd."
            warning = (warning + " " if warning else "") + note

        if not extracted["key_values"]:
            if not extractor.OCR_AVAILABLE:
                warning = (warning + " " if warning else "") + \
                    "No parameters extracted, and OCR is not installed -- if this is a " \
                    "scanned PDF, install pytesseract/pymupdf and the Tesseract OCR engine " \
                    "(see README) to enable scanned-document support."
            else:
                warning = (warning + " " if warning else "") + \
                    "No parameters could be extracted (unsupported layout or empty page)."

        document_id = db.insert_document(
            conn,
            file_name=filename,
            file_path=os.path.abspath(save_path),
            file_hash=dup_info["file_hash"],
            datasheet_no=extracted["datasheet_no"],
            page_count=extracted["page_count"],
            status="processed",
        )
        db.insert_parameters(conn, document_id, extracted["key_values"])

        results.append({
            "file": filename,
            "status": "warning" if warning else "ok",
            "message": warning or f"Extracted {len(extracted['key_values'])} parameters.",
            "document_id": document_id,
        })

    conn.close()

    for r in results:
        category = "error" if r["status"] == "error" else \
                   "duplicate" if r["status"] == "duplicate" else \
                   "warning" if r["status"] == "warning" else "success"
        flash(f"{r['file']}: {r['message']}", category)

    return redirect(url_for("index"))


@app.route("/document/<int:document_id>")
def view_document(document_id):
    conn = get_db()
    document = db.get_document(conn, document_id)
    if not document:
        conn.close()
        flash(f"Document #{document_id} not found.", "error")
        return redirect(url_for("index"))
    parameters = db.get_parameters(conn, document_id)
    conn.close()
    return render_template("document.html", document=document, parameters=parameters)


@app.route("/document/<int:document_id>/delete", methods=["POST"])
def delete_document(document_id):
    conn = get_db()
    conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    conn.commit()
    conn.close()
    flash(f"Document #{document_id} deleted.", "success")
    return redirect(url_for("index"))


@app.route("/document/<int:document_id>/export")
def export_document(document_id):
    conn = get_db()
    document = db.get_document(conn, document_id)
    if not document:
        conn.close()
        flash(f"Document #{document_id} not found.", "error")
        return redirect(url_for("index"))
    parameters = db.get_parameters(conn, document_id)
    conn.close()

    df = pd.DataFrame([{
        "Parameter": p["param_key"],
        "Value": p["param_value"],
        "Unit": p["unit"] or "",
        "Source": p["source"],
    } for p in parameters])

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Extracted Parameters")
    buffer.seek(0)

    safe_name = os.path.splitext(document["file_name"])[0]
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"{safe_name}_extracted.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/export-all")
def export_all():
    conn = get_db()
    documents = db.list_documents(conn)

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        summary_rows = []
        for d in documents:
            params = db.get_parameters(conn, d["id"])
            summary_rows.append({
                "ID": d["id"], "File Name": d["file_name"],
                "Datasheet No": d["datasheet_no"] or "",
                "Pages": d["page_count"], "Parameters": len(params),
                "Processed At": d["processed_at"],
            })
            df = pd.DataFrame([{
                "Parameter": p["param_key"], "Value": p["param_value"],
                "Unit": p["unit"] or "", "Source": p["source"],
            } for p in params])
            sheet_name = f"{d['id']}_{d['file_name']}"[:31].replace("/", "-")
            if df.empty:
                df = pd.DataFrame([{"Parameter": "(none extracted)", "Value": "", "Unit": "", "Source": ""}])
            df.to_excel(writer, index=False, sheet_name=sheet_name)

        pd.DataFrame(summary_rows).to_excel(writer, index=False, sheet_name="Summary")

    conn.close()
    buffer.seek(0)
    return send_file(
        buffer, as_attachment=True, download_name="all_datasheets.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    conn = get_db()
    results = db.search_parameters(conn, query) if query else []
    conn.close()
    return render_template("search.html", query=query, results=results)


@app.route("/api/documents/<int:document_id>/parameters")
def api_parameters(document_id):
    conn = get_db()
    parameters = db.get_parameters(conn, document_id)
    conn.close()
    return jsonify([dict(p) for p in parameters])


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
