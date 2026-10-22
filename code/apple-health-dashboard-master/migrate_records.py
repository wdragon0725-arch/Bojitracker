#!/usr/bin/env python3
"""Versioned, backed-up migration from legacy user JSON to record-level tables."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from record_metadata import normalize_state_records
from record_store import KINDS, SCHEMA_VERSION, create_schema, insert_record


def _legacy_totals(data: dict) -> dict:
    return {
        "foodKcal": round(sum(float(x.get("kcal") or 0) for x in data.get("foods", [])), 2),
        "foodCarb": round(sum(float(x.get("carb") or 0) for x in data.get("foods", [])), 2),
        "foodProtein": round(sum(float(x.get("protein") or 0) for x in data.get("foods", [])), 2),
        "foodFat": round(sum(float(x.get("fat") or 0) for x in data.get("foods", [])), 2),
        "expenseAmount": round(sum(float(x.get("amount") or 0) for x in data.get("expenses", [])), 2),
    }


def migrate(db_path: Path) -> dict:
    db_path = Path(db_path).resolve()
    if not db_path.is_file():
        raise ValueError(f"数据库不存在：{db_path}")
    with closing(sqlite3.connect(db_path, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return {"status": "already_current", "schemaVersion": version, "backup": None}
        legacy_rows = db.execute("SELECT username,data_json FROM user_data").fetchall()
        has_records = any(any(json.loads(row["data_json"] or "{}").get(name, []) for name in ("foods", "workouts", "expenses")) for row in legacy_rows)
        backup = None
        if has_records:
            backup = db_path.with_name(f"{db_path.name}.pre-records-{datetime.now():%Y%m%d-%H%M%S-%f}.bak")
            with closing(sqlite3.connect(backup)) as backup_db:
                db.backup(backup_db)
        create_schema(db)
        report = {}
        try:
            db.execute("BEGIN IMMEDIATE")
            for row in legacy_rows:
                username = row["username"]
                data = json.loads(row["data_json"] or "{}")
                normalize_state_records(data, username, migration=True)
                expected = {name: len(data.get(name, [])) for name in ("foods", "workouts", "expenses")}
                for kind in ("expense", "workout", "food"):
                    for record in data.get(KINDS[kind], []):
                        insert_record(db, username, kind, record, actor="migration", migration=True)
                actual = {name: db.execute("SELECT COUNT(*) FROM records WHERE username=? AND kind=? AND deleted_at IS NULL", (username, kind)).fetchone()[0]
                          for kind, name in KINDS.items()}
                if expected != actual:
                    raise ValueError(f"{username} 迁移条数不匹配：{expected} != {actual}")
                food = db.execute("SELECT SUM(f.kcal),SUM(f.carb),SUM(f.protein),SUM(f.fat) FROM records r JOIN food_records f ON f.record_id=r.id WHERE r.username=?", (username,)).fetchone()
                expense = db.execute("SELECT SUM(e.amount_cents) FROM records r JOIN expense_records e ON e.record_id=r.id WHERE r.username=?", (username,)).fetchone()
                observed = {"foodKcal": round(food[0] or 0, 2), "foodCarb": round(food[1] or 0, 2),
                            "foodProtein": round(food[2] or 0, 2), "foodFat": round(food[3] or 0, 2),
                            "expenseAmount": round((expense[0] or 0) / 100, 2)}
                expected_totals = _legacy_totals(data)
                if any(abs(observed[key] - expected_totals[key]) > 0.01 for key in expected_totals):
                    raise ValueError(f"{username} 迁移合计不匹配：{expected_totals} != {observed}")
                report[username] = {"counts": actual, "totals": observed}
            integrity = db.execute("PRAGMA quick_check").fetchone()[0]
            foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
            if integrity != "ok" or foreign_keys:
                raise ValueError("迁移后数据库完整性检查失败")
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.commit()
        except Exception:
            db.rollback()
            raise
    return {"status": "migrated", "schemaVersion": SCHEMA_VERSION, "backup": str(backup) if backup else None, "accounts": report}


if __name__ == "__main__":
    from server import DB_PATH, init_db

    init_db()
    print(json.dumps(migrate(DB_PATH), ensure_ascii=False))
