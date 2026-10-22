"""Record-level SQLite storage, migration, audit log and deterministic summaries."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from record_metadata import RECORD_COLLECTIONS, normalize_state_records, utc_now


KINDS = {"food": "foods", "workout": "workouts", "expense": "expenses"}
SCHEMA_VERSION = 2


def create_schema(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS records (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK(kind IN ('food','workout','expense')),
            occurred_on TEXT NOT NULL CHECK(length(occurred_on)=10),
            entry_method TEXT NOT NULL,
            value_source TEXT NOT NULL,
            source_ref_json TEXT,
            confidence TEXT,
            is_demo INTEGER NOT NULL DEFAULT 0 CHECK(is_demo IN (0,1)),
            version INTEGER NOT NULL CHECK(version>=1),
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_records_owner_date ON records(username,kind,occurred_on);
        CREATE INDEX IF NOT EXISTS idx_records_owner_demo ON records(username,is_demo,deleted_at);
        CREATE TABLE IF NOT EXISTS expense_records (
            record_id TEXT PRIMARY KEY REFERENCES records(id) ON DELETE CASCADE,
            amount_cents INTEGER NOT NULL CHECK(amount_cents>=0),
            category TEXT NOT NULL,
            payment TEXT,
            title TEXT NOT NULL,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS food_records (
            record_id TEXT PRIMARY KEY REFERENCES records(id) ON DELETE CASCADE,
            linked_expense_id TEXT REFERENCES expense_records(record_id) ON DELETE SET NULL,
            meal TEXT NOT NULL,
            name TEXT NOT NULL,
            amount REAL NOT NULL CHECK(amount>=0),
            unit TEXT NOT NULL,
            kcal REAL NOT NULL CHECK(kcal>=0),
            carb REAL NOT NULL CHECK(carb>=0),
            protein REAL NOT NULL CHECK(protein>=0),
            fat REAL NOT NULL CHECK(fat>=0)
        );
        CREATE INDEX IF NOT EXISTS idx_food_expense ON food_records(linked_expense_id);
        CREATE TABLE IF NOT EXISTS workout_records (
            record_id TEXT PRIMARY KEY REFERENCES records(id) ON DELETE CASCADE,
            training_type TEXT NOT NULL,
            note TEXT,
            rpe REAL CHECK(rpe IS NULL OR (rpe>=0 AND rpe<=10)),
            volume_kg REAL NOT NULL DEFAULT 0 CHECK(volume_kg>=0),
            calories REAL NOT NULL DEFAULT 0 CHECK(calories>=0)
        );
        CREATE TABLE IF NOT EXISTS exercise_sets (
            id INTEGER PRIMARY KEY,
            workout_id TEXT NOT NULL REFERENCES workout_records(record_id) ON DELETE CASCADE,
            position INTEGER NOT NULL CHECK(position>=0),
            name TEXT NOT NULL,
            weight_kg REAL NOT NULL CHECK(weight_kg>=0),
            sets_count INTEGER NOT NULL CHECK(sets_count>0),
            reps_per_set INTEGER NOT NULL CHECK(reps_per_set>0),
            volume_kg REAL NOT NULL CHECK(volume_kg>=0),
            UNIQUE(workout_id,position)
        );
        CREATE TABLE IF NOT EXISTS record_events (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
            record_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN ('create','update','delete')),
            actor TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            version INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_record_events_owner_record ON record_events(username,record_id,id);
    """)


def _number(value: object, field: str) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是数字") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} 不能为负数或无穷大")
    return number


def _date(record: dict) -> str:
    value = str(record.get("date") or "")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("记录日期必须为 YYYY-MM-DD") from exc


def _event(db: sqlite3.Connection, username: str, kind: str, event_type: str,
           actor: str, before: dict | None, after: dict | None, version: int) -> None:
    record = after or before or {}
    db.execute("INSERT INTO record_events(username,record_id,kind,event_type,actor,occurred_at,before_json,after_json,version) VALUES(?,?,?,?,?,?,?,?,?)",
               (username, record["id"], kind, event_type, actor, utc_now(),
                json.dumps(before, ensure_ascii=False) if before is not None else None,
                json.dumps(after, ensure_ascii=False) if after is not None else None, version))


def _details(db: sqlite3.Connection, kind: str, record: dict) -> None:
    record_id = record["id"]
    if kind == "food":
        linked = record.get("linkedExpenseId")
        if linked:
            exists = db.execute("SELECT 1 FROM expense_records e JOIN records r ON r.id=e.record_id WHERE e.record_id=? AND r.username=? AND r.deleted_at IS NULL",
                                (linked, record["meta"]["ownerUsername"])).fetchone()
            if not exists:
                raise ValueError("关联的支出记录不存在")
        db.execute("INSERT INTO food_records(record_id,linked_expense_id,meal,name,amount,unit,kcal,carb,protein,fat) VALUES(?,?,?,?,?,?,?,?,?,?) "
                   "ON CONFLICT(record_id) DO UPDATE SET linked_expense_id=excluded.linked_expense_id,meal=excluded.meal,name=excluded.name,amount=excluded.amount,unit=excluded.unit,kcal=excluded.kcal,carb=excluded.carb,protein=excluded.protein,fat=excluded.fat",
                   (record_id, linked, str(record.get("meal") or "未分类"), str(record.get("name") or "未命名食物"),
                    _number(record.get("amount"), "食物重量"), str(record.get("unit") or "g"),
                    *(_number(record.get(key), key) for key in ("kcal", "carb", "protein", "fat"))))
    elif kind == "expense":
        try:
            cents = int((Decimal(str(record.get("amount") or 0)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except (InvalidOperation, ValueError, OverflowError) as exc:
            raise ValueError("支出金额无效") from exc
        if cents < 0:
            raise ValueError("支出金额不能为负数")
        db.execute("INSERT INTO expense_records(record_id,amount_cents,category,payment,title,note) VALUES(?,?,?,?,?,?) "
                   "ON CONFLICT(record_id) DO UPDATE SET amount_cents=excluded.amount_cents,category=excluded.category,payment=excluded.payment,title=excluded.title,note=excluded.note",
                   (record_id, cents, str(record.get("category") or "其他"), str(record.get("payment") or ""),
                    str(record.get("title") or "未命名支出"), str(record.get("note") or "")))
    else:
        rpe = record.get("rpe")
        rpe = _number(rpe, "RPE") if rpe not in (None, "") else None
        db.execute("INSERT INTO workout_records(record_id,training_type,note,rpe,volume_kg,calories) VALUES(?,?,?,?,?,?) "
                   "ON CONFLICT(record_id) DO UPDATE SET training_type=excluded.training_type,note=excluded.note,rpe=excluded.rpe,volume_kg=excluded.volume_kg,calories=excluded.calories",
                   (record_id, str(record.get("type") or "力量训练"), str(record.get("note") or ""), rpe,
                    _number(record.get("volume"), "训练容量"), _number(record.get("calories"), "训练消耗")))
        db.execute("DELETE FROM exercise_sets WHERE workout_id=?", (record_id,))
        for position, item in enumerate(record.get("exercises") or []):
            db.execute("INSERT INTO exercise_sets(workout_id,position,name,weight_kg,sets_count,reps_per_set,volume_kg) VALUES(?,?,?,?,?,?,?)",
                       (record_id, position, str(item.get("name") or "未命名动作"), _number(item.get("weight"), "动作重量"),
                        int(item.get("sets") or 0), int(item.get("reps") or 0), _number(item.get("volume"), "动作容量")))


def insert_record(db: sqlite3.Connection, username: str, kind: str, record: dict, *, actor: str = "user", migration: bool = False) -> dict:
    if kind not in KINDS:
        raise ValueError("未知记录类型")
    record = dict(record)
    if not isinstance(record.get("id"), str) or not record["id"]:
        record["id"] = str(uuid4())
    normalize_state_records({KINDS[kind]: [record]}, username, migration=migration)
    occurred_on = _date(record)
    meta = record["meta"]
    db.execute("INSERT INTO records(id,username,kind,occurred_on,entry_method,value_source,source_ref_json,confidence,is_demo,version,created_at,updated_at,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (record["id"], username, kind, occurred_on, meta["entryMethod"], meta["valueSource"],
                json.dumps(meta["sourceRef"], ensure_ascii=False) if meta["sourceRef"] else None,
                meta["confidence"], int(record.get("testData") is True), meta["version"], meta["createdAt"], meta["updatedAt"],
                json.dumps(record, ensure_ascii=False, separators=(",", ":"))))
    _details(db, kind, record)
    _event(db, username, kind, "create", actor, None, record, meta["version"])
    return record


def get_record(db: sqlite3.Connection, username: str, kind: str, record_id: str) -> dict | None:
    row = db.execute("SELECT payload_json FROM records WHERE id=? AND username=? AND kind=? AND deleted_at IS NULL", (record_id, username, kind)).fetchone()
    return json.loads(row["payload_json"]) if row else None


def list_records(db: sqlite3.Connection, username: str, kind: str, *, include_demo: bool = True) -> list[dict]:
    where = "username=? AND kind=? AND deleted_at IS NULL" + ("" if include_demo else " AND is_demo=0")
    rows = db.execute(f"SELECT payload_json FROM records WHERE {where} ORDER BY occurred_on,created_at,id", (username, kind)).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def update_record(db: sqlite3.Connection, username: str, kind: str, record_id: str,
                  changes: dict, expected_version: int) -> dict | None:
    before = get_record(db, username, kind, record_id)
    if before is None:
        return None
    if before["meta"]["version"] != expected_version:
        raise RecordConflict(before["meta"]["version"])
    if not isinstance(changes, dict) or any(key in changes for key in ("id", "meta", "testData")):
        raise ValueError("不能修改记录 ID、元数据或测试标记")
    after = {**before, **changes}
    normalize_state_records({KINDS[kind]: [after]}, username, previous={KINDS[kind]: [before]})
    if after == before:
        return before
    meta = after["meta"]
    db.execute("UPDATE records SET occurred_on=?,entry_method=?,value_source=?,source_ref_json=?,confidence=?,version=?,updated_at=?,payload_json=? WHERE id=? AND username=?",
               (_date(after), meta["entryMethod"], meta["valueSource"], json.dumps(meta["sourceRef"], ensure_ascii=False) if meta["sourceRef"] else None,
                meta["confidence"], meta["version"], meta["updatedAt"], json.dumps(after, ensure_ascii=False, separators=(",", ":")), record_id, username))
    _details(db, kind, after)
    _event(db, username, kind, "update", "user", before, after, meta["version"])
    return after


def delete_record(db: sqlite3.Connection, username: str, kind: str, record_id: str, expected_version: int) -> bool | None:
    before = get_record(db, username, kind, record_id)
    if before is None:
        return None
    if before["meta"]["version"] != expected_version:
        raise RecordConflict(before["meta"]["version"])
    version = expected_version + 1
    db.execute("UPDATE records SET deleted_at=?,version=? WHERE id=? AND username=?", (utc_now(), version, record_id, username))
    _event(db, username, kind, "delete", "user", before, None, version)
    return True


class RecordConflict(Exception):
    def __init__(self, current_version: int):
        self.current_version = current_version
        super().__init__("记录已在其他位置更新，请刷新页面后重试")


def summaries(db: sqlite3.Connection, username: str, days: int, *, include_demo: bool = True) -> dict:
    end = date.today()
    start = end - timedelta(days=days - 1)
    demo_clause = "" if include_demo else " AND r.is_demo=0"
    params = (username, start.isoformat(), end.isoformat())
    result = {"days": days, "from": start.isoformat(), "to": end.isoformat(), "includeDemo": include_demo}
    daily_food = db.execute(f"SELECT r.occurred_on AS day,COUNT(*) AS records,ROUND(SUM(f.kcal),2) AS kcal,ROUND(SUM(f.carb),2) AS carb,ROUND(SUM(f.protein),2) AS protein,ROUND(SUM(f.fat),2) AS fat FROM records r JOIN food_records f ON f.record_id=r.id WHERE r.username=? AND r.occurred_on BETWEEN ? AND ? AND r.deleted_at IS NULL{demo_clause} GROUP BY r.occurred_on ORDER BY r.occurred_on", params).fetchall()
    daily_expense = db.execute(f"SELECT r.occurred_on AS day,COUNT(*) AS records,SUM(e.amount_cents) AS amount_cents FROM records r JOIN expense_records e ON e.record_id=r.id WHERE r.username=? AND r.occurred_on BETWEEN ? AND ? AND r.deleted_at IS NULL{demo_clause} GROUP BY r.occurred_on ORDER BY r.occurred_on", params).fetchall()
    workouts = db.execute(f"SELECT r.id,r.occurred_on,w.volume_kg,w.calories FROM records r JOIN workout_records w ON w.record_id=r.id WHERE r.username=? AND r.occurred_on BETWEEN ? AND ? AND r.deleted_at IS NULL{demo_clause} ORDER BY r.occurred_on", params).fetchall()
    categories = db.execute(f"SELECT e.category,SUM(e.amount_cents) AS amount_cents FROM records r JOIN expense_records e ON e.record_id=r.id WHERE r.username=? AND r.occurred_on BETWEEN ? AND ? AND r.deleted_at IS NULL{demo_clause} GROUP BY e.category ORDER BY amount_cents DESC", params).fetchall()
    food_rows = [dict(row) for row in daily_food]
    expense_rows = [{"day": row["day"], "records": row["records"], "amount": row["amount_cents"] / 100} for row in daily_expense]
    result["food"] = {"recordedDays": len(food_rows), "recordCount": sum(row["records"] for row in food_rows),
                      "totals": {key: round(sum(row[key] or 0 for row in food_rows), 2) for key in ("kcal", "carb", "protein", "fat")}, "daily": food_rows}
    result["training"] = {"sessions": len(workouts), "volumeKg": round(sum(row["volume_kg"] for row in workouts), 2),
                          "estimatedCalories": round(sum(row["calories"] for row in workouts), 2),
                          "evidence": [{"id": row["id"], "date": row["occurred_on"]} for row in workouts[-12:]]}
    result["expense"] = {"recordedDays": len(expense_rows), "recordCount": sum(row["records"] for row in expense_rows),
                         "total": round(sum(row["amount"] for row in expense_rows), 2), "daily": expense_rows,
                         "categories": [{"name": row["category"], "amount": row["amount_cents"] / 100} for row in categories]}
    evidence = db.execute(f"SELECT id,kind,occurred_on FROM records r WHERE username=? AND occurred_on BETWEEN ? AND ? AND deleted_at IS NULL{demo_clause} ORDER BY occurred_on DESC,id DESC", params).fetchall()
    result["evidence"] = [{"id": row["id"], "kind": row["kind"], "date": row["occurred_on"]} for row in evidence]
    return result
