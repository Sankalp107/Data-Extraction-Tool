"""
main.py
-------
Command-line tool for the Engineering Datasheet Extraction & Validation
prototype.

Usage
=====
    python main.py init
        Create / reset the SQLite schema.

    python main.py process <pdf_path> [pdf_path ...]
        Extract one or more PDF datasheets, check for duplicates, and
        (if not a duplicate) save the results into the database.

    python main.py process <pdf_path> --force
        Process even if it looks like a duplicate (re-extract and store
        as a new record).

    python main.py list
        List all processed documents.

    python main.py view <document_id>
        Show the extracted key-value parameters for one document.

    python main.py search <text>
        Search extracted parameters by key name across all documents.

Errors and duplicate warnings are printed to the console with clear
[ERROR] / [DUPLICATE] / [WARNING] / [OK] tags, acting as the "alert
messages" required by the task for this CLI prototype.
"""

import argparse
import os
import sys

import db
import dedup
import extractor


def cmd_init(args):
    db.init_db()
    print(f"[OK] Database ready at {db.DB_PATH}")


def cmd_process(args):
    db.init_db()
    conn = db.get_connection()

    for pdf_path in args.pdf_path:
        print(f"\n--- Processing: {pdf_path} ---")

        if not os.path.isfile(pdf_path):
            print(f"[ERROR] File not found: {pdf_path}")
            continue
        if not pdf_path.lower().endswith(".pdf"):
            print(f"[ERROR] Not a PDF file: {pdf_path}")
            continue

        file_name = os.path.basename(pdf_path)

        # --- Extraction first, so we can use the datasheet number (if any)
        # in the duplicate check below. ---
        try:
            result = extractor.extract_from_pdf(pdf_path)
        except Exception as e:
            print(f"[ERROR] Failed to extract '{file_name}': {e}")
            continue

        dup_info = dedup.check_duplicate(
            conn, pdf_path, file_name, result["datasheet_no"]
        )

        if dup_info["is_duplicate"] and not args.force:
            existing = dup_info["existing_document"]
            print(
                f"[DUPLICATE] Skipped. Matched existing document id={existing['id']} "
                f"('{existing['file_name']}') via {dup_info['reason']}. "
                f"Use --force to process anyway."
            )
            continue

        if dup_info["reason"] == "filename" and not dup_info["is_duplicate"]:
            existing = dup_info["existing_document"]
            print(
                f"[WARNING] A different document with the same file name already "
                f"exists (id={existing['id']}). Proceeding since content differs."
            )

        if not result["key_values"]:
            print(f"[WARNING] No key-value parameters could be extracted from '{file_name}'. "
                  f"The PDF may be scanned/image-only or use an unsupported layout.")

        document_id = db.insert_document(
            conn,
            file_name=file_name,
            file_path=os.path.abspath(pdf_path),
            file_hash=dup_info["file_hash"],
            datasheet_no=result["datasheet_no"],
            page_count=result["page_count"],
            status="processed",
        )
        db.insert_parameters(conn, document_id, result["key_values"])

        print(f"[OK] Stored document id={document_id} "
              f"(datasheet_no={result['datasheet_no'] or 'N/A'}, "
              f"pages={result['page_count']}, "
              f"parameters_extracted={len(result['key_values'])})")

    conn.close()


def cmd_list(args):
    db.init_db()
    conn = db.get_connection()
    docs = db.list_documents(conn)
    if not docs:
        print("No documents processed yet.")
        return
    print(f"{'ID':<5} {'File Name':<35} {'Datasheet No':<20} {'Pages':<6} {'Processed At'}")
    print("-" * 95)
    for d in docs:
        print(f"{d['id']:<5} {d['file_name'][:34]:<35} {(d['datasheet_no'] or '-'):<20} "
              f"{d['page_count']:<6} {d['processed_at']}")
    conn.close()


def cmd_view(args):
    db.init_db()
    conn = db.get_connection()
    doc = db.get_document(conn, args.document_id)
    if not doc:
        print(f"[ERROR] No document with id={args.document_id}")
        return
    params = db.get_parameters(conn, args.document_id)

    print(f"Document #{doc['id']}: {doc['file_name']}")
    print(f"  Path:         {doc['file_path']}")
    print(f"  Datasheet No: {doc['datasheet_no'] or '-'}")
    print(f"  Pages:        {doc['page_count']}")
    print(f"  Processed at: {doc['processed_at']}")
    print()
    if not params:
        print("  (no parameters extracted)")
    else:
        print(f"  {'Key':<30} {'Value':<20} {'Unit':<10} {'Source'}")
        print("  " + "-" * 70)
        for p in params:
            print(f"  {p['param_key'][:29]:<30} {(p['param_value'] or '')[:19]:<20} "
                  f"{(p['unit'] or '-'):<10} {p['source']}")
    conn.close()


def cmd_search(args):
    db.init_db()
    conn = db.get_connection()
    rows = db.search_parameters(conn, args.text)
    if not rows:
        print(f"No parameters matching '{args.text}'")
        return
    print(f"{'Doc ID':<8} {'File Name':<30} {'Key':<25} {'Value':<15} {'Unit'}")
    print("-" * 90)
    for r in rows:
        print(f"{r['document_id']:<8} {r['file_name'][:29]:<30} {r['param_key'][:24]:<25} "
              f"{(r['param_value'] or '')[:14]:<15} {r['unit'] or '-'}")
    conn.close()


def build_parser():
    parser = argparse.ArgumentParser(
        description="Engineering Datasheet Extraction & Validation tool"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize the database").set_defaults(func=cmd_init)

    p_process = sub.add_parser("process", help="Extract and store one or more PDF datasheets")
    p_process.add_argument("pdf_path", nargs="+", help="Path(s) to PDF file(s)")
    p_process.add_argument("--force", action="store_true",
                            help="Process even if detected as a duplicate")
    p_process.set_defaults(func=cmd_process)

    sub.add_parser("list", help="List all processed documents").set_defaults(func=cmd_list)

    p_view = sub.add_parser("view", help="View extracted parameters for one document")
    p_view.add_argument("document_id", type=int)
    p_view.set_defaults(func=cmd_view)

    p_search = sub.add_parser("search", help="Search parameters by key name")
    p_search.add_argument("text", help="Substring to search for in parameter keys")
    p_search.set_defaults(func=cmd_search)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as e:
        print(f"[ERROR] Unexpected failure: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
