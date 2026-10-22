#!/usr/bin/env python3
"""Backfill common record metadata in the existing SQLite account snapshots."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime

from record_metadata import RECORD_COLLECTIONS, normalize_state_records
from server import DB_PATH


def main() -> None:
    if not DB_PATH.is_file():
        raise SystemExit(f"数据库不存在：{DB_PATH}")
    backup = DB_PATH.with_name(f"{DB_PATH.name}.pre-metadata-{datetime.now():%Y%m%d-%H%M%S-%f}.bak")
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        with closing(sqlite3.connect(backup)) as backup_db:
            db.backup(backup_db)
        try:
            db.execute("BEGIN IMMEDIATE")
            report = {}
            for row in db.execute("SELECT username, data_json, updated_at FROM user_data").fetchall():
                data = json.loads(row["data_json"] or "{}")
                data, counts = normalize_state_records(data, row["username"], migration=True)
                report[row["username"]] = {"records": {name: len(data.get(name, [])) for name in RECORD_COLLECTIONS}, "metadata_added": counts}
                if any(counts.values()):
                    revision = max(int(time.time() * 1000), row["updated_at"] + 1)
                    db.execute("UPDATE user_data SET data_json = ?, updated_at = ? WHERE username = ?",
                               (json.dumps(data, ensure_ascii=False, separators=(",", ":")), revision, row["username"]))
            db.commit()
        except Exception:
            db.rollback()
            raise
        integrity = db.execute("PRAGMA quick_check").fetchone()[0]
    print(json.dumps({"backup": str(backup), "integrity": integrity, "accounts": report}, ensure_ascii=False))


if __name__ == "__main__":
    main()
