"""Common, additive metadata for food, strength-training and expense records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5


RECORD_COLLECTIONS = {"foods": "food", "workouts": "workout", "expenses": "expense"}
METADATA_SCHEMA_VERSION = 1
VALUE_SOURCES = {
    "knowledge": "knowledge_base",
    "knowledge_base": "knowledge_base",
    "ai_estimate": "ai_estimate",
    "estimated": "formula_estimate",
    "formula_estimate": "formula_estimate",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _value_source(record: dict, kind: str) -> str:
    if kind == "expense":
        return "user_reported"
    raw = record.get("nutritionSource") if kind == "food" else record.get("calorieSource")
    return VALUE_SOURCES.get(raw, "unknown")


def _source_ref(record: dict, previous_meta: dict, value_source: str) -> dict | None:
    if value_source != "knowledge_base":
        return None
    source = record.get("nutritionRef")
    if isinstance(source, dict) and isinstance(source.get("sheet"), str) and isinstance(source.get("cell"), str):
        return {"sheet": source["sheet"][:120], "cell": source["cell"][:24]}
    old = previous_meta.get("sourceRef")
    return old if isinstance(old, dict) else None


def _entry_method(record: dict, previous_meta: dict) -> str:
    old = previous_meta.get("entryMethod")
    if old in ("manual", "expense_sync", "generated_test", "legacy"):
        return old
    if record.get("testData") is True:
        return "generated_test"
    if record.get("linkedExpenseId"):
        return "expense_sync"
    return "manual"


def _confidence(record: dict, previous_meta: dict, value_source: str) -> str | None:
    if value_source not in ("knowledge_base", "ai_estimate", "formula_estimate"):
        return None
    raw = record.get("nutritionConfidence") or previous_meta.get("confidence")
    return raw if raw in ("high", "medium", "low") else None


def normalize_state_records(
    data: dict[str, Any], username: str, *, previous: dict[str, Any] | None = None,
    timestamp: str | None = None, migration: bool = False,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Add/maintain record metadata without changing values or excluding demo data.

    `previous` is the server's saved state: matching unchanged records keep their
    metadata, while changed records receive a new per-record version. During a
    migration, unknown historical creation times remain null instead of being
    invented.
    """
    if not isinstance(data, dict):
        raise ValueError("账号数据必须是对象")
    now = timestamp or utc_now()
    counts = {name: 0 for name in RECORD_COLLECTIONS}
    for collection, kind in RECORD_COLLECTIONS.items():
        records = data.get(collection, [])
        if not isinstance(records, list):
            raise ValueError(f"{collection} 必须是数组")
        old_records = (previous or {}).get(collection, [])
        old_by_id = {item.get("id"): item for item in old_records if isinstance(item, dict) and item.get("id")}
        seen_ids: set[str] = set()
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise ValueError(f"{collection}[{index}] 必须是对象")
            record_id = record.get("id")
            if not isinstance(record_id, str) or not record_id:
                record_id = str(uuid5(NAMESPACE_URL, f"boji:{username}:{collection}:{index}"))
                record["id"] = record_id
            if record_id in seen_ids:
                raise ValueError(f"{collection} 中存在重复记录 ID：{record_id}")
            seen_ids.add(record_id)
            saved = old_by_id.get(record_id)
            saved_meta = saved.get("meta") if isinstance(saved, dict) and isinstance(saved.get("meta"), dict) else {}
            incoming_meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
            base_meta = saved_meta or incoming_meta
            payload_changed = bool(saved and {k: v for k, v in record.items() if k != "meta"} != {k: v for k, v in saved.items() if k != "meta"})
            source = _value_source(record, kind)
            original_time = base_meta.get("createdAt") or record.get("createdAt")
            created_at = original_time if isinstance(original_time, str) and original_time else (None if migration or saved else now)
            version = base_meta.get("version") if isinstance(base_meta.get("version"), int) and base_meta["version"] > 0 else 1
            if payload_changed:
                version += 1
            updated_at = now if payload_changed or (not saved and not migration) else base_meta.get("updatedAt") or created_at
            meta = {
                "schemaVersion": METADATA_SCHEMA_VERSION,
                "recordType": kind,
                "ownerUsername": username,
                "entryMethod": _entry_method(record, base_meta),
                "valueSource": source,
                "sourceRef": _source_ref(record, base_meta, source),
                "confidence": _confidence(record, base_meta, source),
                "createdAt": created_at,
                "updatedAt": updated_at,
                "version": version,
            }
            if record.get("meta") != meta:
                record["meta"] = meta
                counts[collection] += 1
    return data, counts
