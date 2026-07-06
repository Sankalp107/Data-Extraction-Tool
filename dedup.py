"""
dedup.py
--------
Duplicate-detection logic for the Engineering Datasheet Extraction Tool.

Three layers of checking, from strongest to weakest signal:

1. File hash (SHA-256)   -> catches byte-for-byte identical files,
                             even if renamed.
2. Datasheet number      -> catches the same vendor datasheet re-uploaded
                             under a different file name / revision, if a
                             recognizable "Datasheet No." / "Doc No." /
                             "Part No." field was found in the text.
3. File name             -> weakest signal (two different documents can
                             share a name), used only as a soft warning.
"""

import hashlib

import db


def compute_file_hash(file_path):
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def check_duplicate(conn, file_path, file_name, datasheet_no):
    """
    Returns a dict describing duplicate status:
        {
            "is_duplicate": bool,
            "reason": str | None,       # 'hash' | 'datasheet_no' | 'filename' | None
            "existing_document": Row | None,
            "file_hash": str,
        }

    'hash' and 'datasheet_no' matches are treated as hard duplicates
    (extraction is skipped). 'filename' match alone is only a soft
    warning -- the file is still processed, since two distinct
    datasheets can legitimately share a file name.
    """
    file_hash = compute_file_hash(file_path)

    existing = db.find_document_by_hash(conn, file_hash)
    if existing:
        return {
            "is_duplicate": True,
            "reason": "hash",
            "existing_document": existing,
            "file_hash": file_hash,
        }

    existing = db.find_document_by_datasheet_no(conn, datasheet_no)
    if existing:
        return {
            "is_duplicate": True,
            "reason": "datasheet_no",
            "existing_document": existing,
            "file_hash": file_hash,
        }

    existing = db.find_document_by_filename(conn, file_name)
    if existing:
        return {
            "is_duplicate": False,  # soft warning only
            "reason": "filename",
            "existing_document": existing,
            "file_hash": file_hash,
        }

    return {
        "is_duplicate": False,
        "reason": None,
        "existing_document": None,
        "file_hash": file_hash,
    }
