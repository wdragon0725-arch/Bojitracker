"""Versioned contracts for descriptive, record-backed Boji Health metrics."""

DICTIONARY_VERSION = 1


def _metric(identifier: str, label: str, unit: str, formula: str, source: str,
            missing: str = "null_when_no_records", estimate: bool = False) -> dict:
    return {"id": identifier, "version": 1, "label": label, "unit": unit,
            "formula": formula, "source": source, "missingPolicy": missing,
            "isEstimate": estimate}


METRICS = (
    _metric("food.kcal_total", "已记录热量合计", "kcal", "SUM(food_records.kcal)", "food"),
    _metric("food.carb_total", "已记录碳水合计", "g", "SUM(food_records.carb)", "food"),
    _metric("food.protein_total", "已记录蛋白质合计", "g", "SUM(food_records.protein)", "food"),
    _metric("food.fat_total", "已记录脂肪合计", "g", "SUM(food_records.fat)", "food"),
    _metric("food.recorded_days", "饮食有记录天数", "day", "COUNT(DISTINCT records.occurred_on)", "food", "zero_means_no_records"),
    _metric("food.kcal_avg_recorded_day", "有记录日平均热量", "kcal/day", "food.kcal_total / food.recorded_days", "food"),
    _metric("food.protein_avg_recorded_day", "有记录日平均蛋白质", "g/day", "food.protein_total / food.recorded_days", "food"),
    _metric("food.kcal_today", "今日已记录热量", "kcal", "SUM(today's food_records.kcal)", "food"),
    _metric("food.carb_today", "今日已记录碳水", "g", "SUM(today's food_records.carb)", "food"),
    _metric("food.protein_today", "今日已记录蛋白质", "g", "SUM(today's food_records.protein)", "food"),
    _metric("food.fat_today", "今日已记录脂肪", "g", "SUM(today's food_records.fat)", "food"),
    _metric("food.target_gap_today", "今日已记录热量与建议目标差", "kcal", "food.kcal_today - target.calories_daily", "food"),
    _metric("training.sessions", "已记录力量训练次数", "session", "COUNT(workout_records)", "workout", "zero_means_no_records"),
    _metric("training.volume_kg", "已记录训练容量", "kg", "SUM(workout_records.volume_kg)", "workout"),
    _metric("training.estimated_calories_total", "训练估算消耗", "kcal", "SUM(workout_records.calories)", "workout", estimate=True),
    _metric("expense.total", "已记录花销", "CNY", "SUM(expense_records.amount_cents) / 100", "expense"),
    _metric("expense.recorded_days", "花销有记录天数", "day", "COUNT(DISTINCT records.occurred_on)", "expense", "zero_means_no_records"),
    _metric("target.calories_daily", "每日建议热量", "kcal/day", "profile_formula_v1", "profile", estimate=True),
    _metric("target.protein_daily", "每日建议蛋白质", "g/day", "profile_formula_v1", "profile", estimate=True),
)

METRIC_BY_ID = {item["id"]: item for item in METRICS}


def catalog() -> dict:
    return {"dictionaryVersion": DICTIONARY_VERSION, "metrics": [dict(item) for item in METRICS]}
