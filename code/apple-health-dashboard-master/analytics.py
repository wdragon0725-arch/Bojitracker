"""Single server-side metric calculation path for the homepage and Agent."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import date

from metric_definitions import DICTIONARY_VERSION, METRIC_BY_ID
from record_store import summaries


def _js_round(value: float) -> int:
    return math.floor(value + 0.5)


def profile_targets(profile: dict | None) -> dict | None:
    """Match the existing UI's v1 estimate; this is not a clinical target."""
    if not isinstance(profile, dict):
        return None
    try:
        weight, height, age = (float(profile[key]) for key in ("weight", "height", "age"))
        training_days = float(profile.get("trainingDays") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(x) for x in (weight, height, age, training_days)) or min(weight, height, age) <= 0 or not 0 <= training_days <= 7:
        return None
    sex = profile.get("sex")
    goal = profile.get("goal")
    if sex not in ("male", "female") or goal not in ("lose", "gain", "maintain"):
        return None
    bmr = 9.99 * weight + 6.25 * height - 4.92 * age + (5 if sex == "male" else -161)
    tdee = bmr / .7 + (200 if sex == "male" else 150) * training_days / 7
    calories = _js_round(tdee * ({"lose": .8, "gain": 1.05, "maintain": 1}[goal]))
    protein = _js_round(weight * (1.6 if goal == "maintain" else 1.8))
    fat = _js_round((80 if sex == "male" else 70) if goal == "gain" else (60 if sex == "male" else 50))
    carbs = max(80, _js_round((calories - protein * 4 - fat * 9) / 4))
    return {"formulaVersion": 1, "bmr": _js_round(bmr), "tdee": _js_round(tdee),
            "calories": calories, "protein": protein, "fat": fat, "carbs": carbs,
            "bmi": round(weight / (height / 100) ** 2, 2)}


def analyze(db: sqlite3.Connection, username: str, days: int, *, include_demo: bool = True) -> dict:
    if days not in (7, 30):
        raise ValueError("分析窗口仅支持 7 或 30 天")
    row = db.execute("SELECT data_json FROM user_data WHERE username=?", (username,)).fetchone()
    account = json.loads(row["data_json"] or "{}") if row else {}
    profile = account.get("profile")
    target_inputs = {key: profile.get(key) for key in ("weight", "height", "age", "sex", "goal", "trainingDays")} if isinstance(profile, dict) else None
    targets = profile_targets(profile)
    summary = summaries(db, username, days, include_demo=include_demo)
    evidence = {kind: [item["id"] for item in summary["evidence"] if item["kind"] == kind]
                for kind in ("food", "workout", "expense")}
    source_counts = db.execute("SELECT r.kind,r.value_source,COUNT(*) AS count FROM records r WHERE r.username=? AND r.occurred_on BETWEEN ? AND ? AND r.deleted_at IS NULL" +
                               ("" if include_demo else " AND r.is_demo=0") + " GROUP BY r.kind,r.value_source",
                               (username, summary["from"], summary["to"])).fetchall()
    source_breakdown = {kind: {} for kind in evidence}
    for source_row in source_counts:
        source_breakdown[source_row["kind"]][source_row["value_source"]] = source_row["count"]
    metrics = {}

    def add(identifier: str, value: int | float | None, record_count: int, *,
            recorded_days: int | None = None, missing: str | None = None) -> None:
        definition = METRIC_BY_ID[identifier]
        status = missing or ("observed" if record_count else "no_records")
        metrics[identifier] = {"value": value, "unit": definition["unit"], "status": status,
                               "recordCount": record_count, "recordedDays": recorded_days,
                               "evidenceKey": definition["source"] if definition["source"] in evidence else None,
                               "formulaVersion": definition["version"]}

    food = summary["food"]
    count, recorded_days = food["recordCount"], food["recordedDays"]
    for nutrient in ("kcal", "carb", "protein", "fat"):
        add(f"food.{nutrient}_total", food["totals"][nutrient] if count else None, count, recorded_days=recorded_days)
    add("food.recorded_days", recorded_days, count, recorded_days=recorded_days)
    for nutrient in ("kcal", "protein"):
        add(f"food.{nutrient}_avg_recorded_day", round(food["totals"][nutrient] / recorded_days, 2) if recorded_days else None,
            count, recorded_days=recorded_days)
    today_row = next((item for item in food["daily"] if item["day"] == date.today().isoformat()), None)
    today_count = today_row["records"] if today_row else 0
    for nutrient in ("kcal", "carb", "protein", "fat"):
        add(f"food.{nutrient}_today", today_row[nutrient] if today_row else None, today_count,
            recorded_days=1 if today_row else 0)
    add("food.target_gap_today", round(today_row["kcal"] - targets["calories"], 2) if today_row and targets else None,
        today_count, recorded_days=1 if today_row else 0,
        missing="target_unavailable" if today_row and not targets else None)
    training = summary["training"]
    sessions = training["sessions"]
    add("training.sessions", sessions, sessions)
    add("training.volume_kg", training["volumeKg"] if sessions else None, sessions)
    add("training.estimated_calories_total", training["estimatedCalories"] if sessions else None, sessions)
    expense = summary["expense"]
    expense_count = expense["recordCount"]
    add("expense.total", expense["total"] if expense_count else None, expense_count, recorded_days=expense["recordedDays"])
    add("expense.recorded_days", expense["recordedDays"], expense_count, recorded_days=expense["recordedDays"])
    add("target.calories_daily", targets["calories"] if targets else None, 0,
        missing="estimate" if targets else "profile_incomplete")
    add("target.protein_daily", targets["protein"] if targets else None, 0,
        missing="estimate" if targets else "profile_incomplete")
    return {"dictionaryVersion": DICTIONARY_VERSION, "days": days, "from": summary["from"], "to": summary["to"],
            "includeDemo": include_demo, "targetInputs": target_inputs, "targets": targets,
            "coverage": {"foodRecordedDays": recorded_days, "expenseRecordedDays": expense["recordedDays"],
                         "windowDays": days, "note": "有记录不代表当天记录完整；无记录不代表实际为零"},
            "sourceBreakdown": source_breakdown, "evidence": evidence, "metrics": metrics,
            "summary": summary}


def agent_context(report: dict) -> dict:
    """Send metrics and traceable IDs, but not legacy JSON or record payloads."""
    return {key: report[key] for key in ("dictionaryVersion", "days", "from", "to", "includeDemo",
                                           "targets", "coverage", "sourceBreakdown", "evidence", "metrics")}
