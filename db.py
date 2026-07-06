"""
db.py
-----
SQLite database layer for the Engineering Datasheet Extraction Tool.

Two main tables:
    documents   -> one row per processed PDF file (metadata + dedup keys)
    parameters  -> one row per extracted key/value/unit, linked to a document

Run this file directly to (re)create the schema:
    python db.py
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datasheets.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name       TEXT NOT NULL,
    file_path       TEXT,
    file_hash       TEXT UNIQUE,            -- sha256 of file bytes, used for exact-duplicate check
    datasheet_no    TEXT,                   -- vendor / internal datasheet number, if found
    page_count      INTEGER,
    processed_at    TEXT DEFAULT (datetime('now')),
    status          TEXT DEFAULT 'processed'   -- processed | duplicate | error
);

CREATE TABLE IF NOT EXISTS parameters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL,
    param_key       TEXT NOT NULL,
    param_value     TEXT,
    unit            TEXT,
    source           TEXT,                  -- 'text' or 'table'
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_param_doc ON parameters(document_id);
CREATE INDEX IF NOT EXISTS idx_param_key ON parameters(param_key);
CREATE INDEX IF NOT EXISTS idx_doc_hash ON documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_doc_dsno ON documents(datasheet_no);
"""


def get_connection(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path=DB_PATH):
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def find_document_by_hash(conn, file_hash):
    cur = conn.execute("SELECT * FROM documents WHERE file_hash = ?", (file_hash,))
    return cur.fetchone()


def find_document_by_datasheet_no(conn, datasheet_no):
    if not datasheet_no:
        return None
    cur = conn.execute(
        "SELECT * FROM documents WHERE datasheet_no = ? COLLATE NOCASE", (datasheet_no,)
    )
    return cur.fetchone()


def find_document_by_filename(conn, file_name):
    cur = conn.execute(
        "SELECT * FROM documents WHERE file_name = ? COLLATE NOCASE", (file_name,)
    )
    return cur.fetchone()


def insert_document(conn, file_name, file_path, file_hash, datasheet_no, page_count, status="processed"):
    cur = conn.execute(
        """INSERT INTO documents (file_name, file_path, file_hash, datasheet_no, page_count, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (file_name, file_path, file_hash, datasheet_no, page_count, status),
    )
    conn.commit()
    return cur.lastrowid


def insert_parameters(conn, document_id, kv_list):
    """kv_list: list of dicts with keys: key, value, unit, source"""
    conn.executemany(
        """INSERT INTO parameters (document_id, param_key, param_value, unit, source)
           VALUES (?, ?, ?, ?, ?)""",
        [
            (document_id, item["key"], item.get("value"), item.get("unit"), item.get("source"))
            for item in kv_list
        ],
    )
    conn.commit()


def get_document(conn, document_id):
    cur = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,))
    return cur.fetchone()


def get_parameters(conn, document_id):
    cur = conn.execute(
        "SELECT * FROM parameters WHERE document_id = ? ORDER BY id", (document_id,)
    )
    return cur.fetchall()


def list_documents(conn):
    cur = conn.execute("SELECT * FROM documents ORDER BY id DESC")
    return cur.fetchall()


def search_parameters(conn, key_substring):
    cur = conn.execute(
        """SELECT p.*, d.file_name FROM parameters p
           JOIN documents d ON d.id = p.document_id
           WHERE p.param_key LIKE ? COLLATE NOCASE
           ORDER BY d.id DESC""",
        (f"%{key_substring}%",),
    )
    return cur.fetchall()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at: {DB_PATH}")
