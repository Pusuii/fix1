"""Akses database SQLite + helper query."""
import os
import sqlite3
from datetime import datetime

from flask import current_app, g

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE"], detect_types=0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        g.db = conn
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def q(sql, args=(), one=False):
    """SELECT: kembalikan satu baris (one=True) atau list baris."""
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def scalar(sql, args=(), default=0):
    row = q(sql, args, one=True)
    if row is None or row[0] is None:
        return default
    return row[0]


def run(sql, args=()):
    """INSERT/UPDATE/DELETE: commit & kembalikan lastrowid."""
    conn = get_db()
    cur = conn.execute(sql, args)
    conn.commit()
    last = cur.lastrowid
    cur.close()
    return last


def init_db(app):
    """Buat tabel jika belum ada."""
    conn = sqlite3.connect(app.config["DATABASE"])
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        conn.executescript(fh.read())
    conn.commit()
    conn.close()


def db_is_empty(app):
    conn = sqlite3.connect(app.config["DATABASE"])
    try:
        n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    except sqlite3.Error:
        n = 0
    conn.close()
    return n == 0
