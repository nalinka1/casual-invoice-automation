import json
import sqlite3
from contextlib import contextmanager


class Ledger:
    def __init__(self, path, baseline):
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, baseline INTEGER);
        CREATE TABLE IF NOT EXISTS runs (month TEXT PRIMARY KEY, number INTEGER UNIQUE,
          status TEXT, created TEXT, updated TEXT, draft_id TEXT, pdf TEXT, checksum TEXT,
          summary TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS events (key TEXT PRIMARY KEY, payload TEXT);
        CREATE TABLE IF NOT EXISTS drafts (month TEXT PRIMARY KEY, id TEXT UNIQUE, mime BLOB, status TEXT);
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (baseline,))
        if self.db.execute("SELECT baseline FROM settings").fetchone()[0] != baseline:
            raise ValueError("Ledger baseline differs: reconcile issued numbers before proceeding")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def get(self, key):
        row = self.db.execute("SELECT * FROM runs WHERE month=?", (key,)).fetchone()
        return dict(row) if row else None

    def reserve(self, key, now):
        with self.transaction():
            row = self.get(key)
            if row:
                return row
            number = self.db.execute("SELECT MAX(number) FROM runs").fetchone()[0]
            baseline = self.db.execute("SELECT baseline FROM settings").fetchone()[0]
            number = max(number or 0, baseline) + 1
            self.db.execute("INSERT INTO runs(month, number, status, created, updated) VALUES (?, ?, 'running', ?, ?)", (key, number, now, now))
            return self.get(key)

    def update(self, key, now, **values):
        allowed = {"status", "draft_id", "pdf", "checksum", "summary", "error"}
        if set(values) - allowed:
            raise ValueError("Invalid ledger fields")
        with self.db:
            self.db.execute(f"UPDATE runs SET {', '.join(k+'=?' for k in values)}, updated=? WHERE month=?", (*values.values(), now, key))

    def event(self, key, payload):
        with self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO events VALUES (?, ?)", (key, json.dumps(payload)))
        return bool(cursor.rowcount)
