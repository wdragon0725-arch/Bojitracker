"""Bounded, account-scoped, read-only tools for the personal Agent."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from analytics import analyze
from metric_definitions import METRIC_BY_ID
from record_store import KINDS, get_record


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


AGENT_TOOLS = [
    {"type": "function", "function": {"name": "get_metric", "description": "查询当前用户在最近 7 或 30 天的一个已定义指标，包括值、覆盖天数、来源和证据记录 ID。",
        "parameters": _schema({"metric_id": {"type": "string", "enum": sorted(METRIC_BY_ID)}, "days": {"type": "integer", "enum": [7, 30]}}, ["metric_id", "days"]) }},
    {"type": "function", "function": {"name": "get_daily_trend", "description": "查询当前用户最近 7 或 30 天已记录的每日饮食营养或花销。缺记录的日子不补零。",
        "parameters": _schema({"kind": {"type": "string", "enum": ["food", "expense"]}, "days": {"type": "integer", "enum": [7, 30]}}, ["kind", "days"]) }},
    {"type": "function", "function": {"name": "get_workout_history", "description": "列出当前用户最近 7 或 30 天的力量训练记录，含日期、动作、重量、组数、次数、RPE、训练量和估算消耗。最多返回最近 30 次。",
        "parameters": _schema({"days": {"type": "integer", "enum": [7, 30]}}, ["days"]) }},
    {"type": "function", "function": {"name": "get_record", "description": "按记录 ID 查看当前用户的一条饮食、训练或花销记录；只能读取，不能修改。",
        "parameters": _schema({"kind": {"type": "string", "enum": list(KINDS)}, "record_id": {"type": "string", "maxLength": 120}}, ["kind", "record_id"]) }},
    {"type": "function", "function": {"name": "lookup_food", "description": "按名称和克数查询本地食物知识库。未命中时返回 not_found，不会把 AI 猜测伪装为知识库结果。",
        "parameters": _schema({"name": {"type": "string", "maxLength": 120}, "amount_grams": {"type": "number", "exclusiveMinimum": 0, "maximum": 5000}}, ["name", "amount_grams"]) }},
]

TOOL_CARDS = [
    {"id": "get_metric", "name": "个人指标查询", "description": "读取 7/30 天指标、覆盖天数和证据记录 ID；只限当前账户。"},
    {"id": "get_daily_trend", "name": "饮食与花销趋势", "description": "读取有记录日期的每日数值，不把未记录日填成零。"},
    {"id": "get_workout_history", "name": "力量训练记录查询", "description": "查看最近 7/30 天的训练日期、动作、重量、组数、次数和 RPE。"},
    {"id": "get_record", "name": "记录证据核对", "description": "按 ID 查看当前账户的一条原始记录，核对结论来源。"},
    {"id": "lookup_food", "name": "本地食物知识库", "description": "优先查询知识库食物营养；未命中会明确标记。"},
]


def _arguments(name: str, arguments: object) -> dict:
    if not isinstance(arguments, dict):
        raise ValueError("工具参数必须是对象")
    fields = {
        "get_metric": {"metric_id", "days"},
        "get_daily_trend": {"kind", "days"},
        "get_workout_history": {"days"},
        "get_record": {"kind", "record_id"},
        "lookup_food": {"name", "amount_grams"},
    }
    if name not in fields or set(arguments) != fields[name]:
        raise ValueError("未知工具或参数字段不符")
    if "days" in arguments and (type(arguments["days"]) is not int or arguments["days"] not in (7, 30)):
        raise ValueError("时间窗口仅允许 7 或 30 天")
    return arguments


def execute_tool(db: sqlite3.Connection, username: str, name: str, arguments: object,
                 *, find_food=None, calculate_food=None) -> dict:
    args = _arguments(name, arguments)
    if name == "get_metric":
        metric_id = args["metric_id"]
        if metric_id not in METRIC_BY_ID:
            raise ValueError("未知指标 ID")
        report = analyze(db, username, args["days"])
        metric = report["metrics"][metric_id]
        evidence_key = metric["evidenceKey"]
        return {"metricId": metric_id, "definition": METRIC_BY_ID[metric_id], "metric": metric,
                "window": {"from": report["from"], "to": report["to"], "days": args["days"]},
                "coverage": report["coverage"], "sourceBreakdown": report["sourceBreakdown"].get(evidence_key, {}),
                "recordIds": report["evidence"].get(evidence_key, [])}
    if name == "get_daily_trend":
        if args["kind"] not in ("food", "expense"):
            raise ValueError("趋势类型无效")
        report = analyze(db, username, args["days"])
        return {"kind": args["kind"], "window": {"from": report["from"], "to": report["to"], "days": args["days"]},
                "daily": report["summary"][args["kind"]]["daily"], "coverage": report["coverage"],
                "recordIds": report["evidence"][args["kind"]]}
    if name == "get_workout_history":
        end = date.today()
        start = end - timedelta(days=args["days"] - 1)
        params = (username, start.isoformat(), end.isoformat())
        total = db.execute("SELECT COUNT(*) FROM records WHERE username=? AND kind='workout' "
                           "AND occurred_on BETWEEN ? AND ? AND deleted_at IS NULL", params).fetchone()[0]
        rows = db.execute("SELECT r.id,r.occurred_on,w.training_type,w.rpe,w.volume_kg,w.calories "
                          "FROM records r JOIN workout_records w ON w.record_id=r.id "
                          "WHERE r.username=? AND r.kind='workout' AND r.occurred_on BETWEEN ? AND ? "
                          "AND r.deleted_at IS NULL ORDER BY r.occurred_on DESC,r.id DESC LIMIT 30", params).fetchall()
        sessions = []
        for row in rows:
            exercises = db.execute("SELECT name,weight_kg,sets_count,reps_per_set,volume_kg "
                                   "FROM exercise_sets WHERE workout_id=? ORDER BY position", (row["id"],)).fetchall()
            sessions.append({"id": row["id"], "date": row["occurred_on"], "type": row["training_type"],
                             "rpe": row["rpe"], "volumeKg": row["volume_kg"], "estimatedCalories": row["calories"],
                             "exercises": [{"name": item["name"], "weightKg": item["weight_kg"],
                                            "sets": item["sets_count"], "reps": item["reps_per_set"],
                                            "volumeKg": item["volume_kg"]} for item in exercises]})
        return {"window": {"from": start.isoformat(), "to": end.isoformat(), "days": args["days"]},
                "totalSessions": total, "returnedSessions": len(sessions), "truncated": total > len(sessions),
                "sessions": sessions, "recordIds": [item["id"] for item in sessions]}
    if name == "get_record":
        record_id, kind = args["record_id"], args["kind"]
        if kind not in KINDS or not isinstance(record_id, str) or not 1 <= len(record_id) <= 120:
            raise ValueError("记录类型或 ID 无效")
        record = get_record(db, username, kind, record_id)
        return {"status": "found", "record": record, "recordIds": [record_id]} if record else {"status": "not_found", "recordIds": []}
    if name == "lookup_food":
        food_name, grams = args["name"], args["amount_grams"]
        if not isinstance(food_name, str) or not 1 <= len(food_name.strip()) <= 120 or isinstance(grams, bool) or not isinstance(grams, (int, float)) or not 0 < grams <= 5000:
            raise ValueError("食物名称或克数无效")
        if find_food is None or calculate_food is None:
            raise ValueError("食物知识库暂不可用")
        food, score = find_food(food_name)
        return {"status": "found", "result": calculate_food(food, grams), "matchScore": score, "recordIds": []} if food else {"status": "not_found", "recordIds": []}
    raise ValueError("不支持的工具")
