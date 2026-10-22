#!/usr/bin/env python3
"""Add clearly labelled, repeatable 14-day demo records to an existing account."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from server import DB_PATH, knowledge_foods
from record_metadata import normalize_state_records


BREAKFASTS = [
    [(6, 60), (12, 1), (11, 2), (16, 150)],
    [(5, 100), (13, 1), (11, 2), (17, 100)],
    [(0, 120), (12, 1), (11, 2), (16, 150)],
]
LUNCHES = [
    [(1, 250), (8, 180), (18, 200)],
    [(1, 230), (10, 160), (18, 200)],
    [(7, 280), (9, 180), (14, 180)],
]
DINNERS = [
    [(3, 300), (8, 160), (18, 200), (19, 20)],
    [(1, 220), (14, 250), (15, 150), (18, 150)],
    [(4, 300), (10, 170), (18, 200), (19, 20)],
]
WORKOUTS = [
    ("下肢", 7, [("深蹲", 45, 4, 8), ("罗马尼亚硬拉", 50, 3, 10), ("腿举", 100, 3, 12)]),
    ("上肢推", 7, [("卧推", 35, 4, 8), ("哑铃推举", 14, 3, 10), ("绳索夹胸", 15, 3, 12)]),
    ("上肢拉", 8, [("高位下拉", 40, 4, 10), ("坐姿划船", 40, 3, 10), ("杠铃弯举", 20, 3, 12)]),
    ("下肢轻量", 6, [("前蹲", 35, 3, 8), ("腿弯举", 25, 3, 12), ("站姿提踵", 40, 3, 15)]),
]
OTHER_EXPENSES = [
    ("交通", "公交", 3),
    ("购物", "日用品", 28),
    ("运动健康", "健身房饮用水", 6),
    ("学习", "电子书", 12),
    ("娱乐", "电影", 38),
]


def record_id(username: str, batch: str, kind: str, day: str, index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"boji-health:{username}:{batch}:{kind}:{day}:{index}"))


def calories(exercises: list[dict], rpe: int, body_weight: float) -> int:
    total_sets = sum(item["sets"] for item in exercises)
    total_reps = sum(item["sets"] * item["reps"] for item in exercises)
    volume = sum(item["volume"] for item in exercises)
    average_load = volume / total_reps if total_reps else 0
    minutes = max(8, total_sets * 2.4 + len(exercises) * 2)
    met = 3.5 + max(0, rpe - 4) * .45 + min(1.5, average_load / body_weight * 1.5)
    return math.floor(met * 3.5 * body_weight / 200 * minutes + .5)


def add_demo_data(data: dict, username: str, today: date) -> dict[str, int]:
    foods = knowledge_foods()
    if len(foods) < 21:
        raise ValueError("食物知识库不完整，停止写入")
    for field in ("foods", "workouts", "expenses"):
        if not isinstance(data.get(field), list):
            raise ValueError(f"{field} 不是数组，停止写入以免覆盖原数据")

    previous = json.loads(json.dumps(data, ensure_ascii=False))
    batch = f"demo-{today.isoformat()}-14d"
    existing_meals = {(x.get("date"), x.get("meal")) for x in data["foods"] if isinstance(x, dict)}
    existing_workouts = {x.get("date") for x in data["workouts"] if isinstance(x, dict)}
    existing_expenses = {x.get("date") for x in data["expenses"] if isinstance(x, dict)}
    counts = {"foods": 0, "workouts": 0, "expenses": 0}
    start = today - timedelta(days=13)
    body_weight = float((data.get("profile") or {}).get("weight") or 70)

    for offset in range(14):
        day = (start + timedelta(days=offset)).isoformat()
        meal_plans = [("早餐", "08:10", BREAKFASTS[offset % 3]), ("午餐", "12:20", LUNCHES[offset % 3])]
        if day != today.isoformat():
            meal_plans.append(("晚餐", "18:40", DINNERS[offset % 3]))
        for meal, when, plan in meal_plans:
            if (day, meal) in existing_meals:
                continue
            for index, (food_index, amount) in enumerate(plan):
                food = foods[food_index]
                factor = amount if food["unit"] == "serving" else amount / 100
                data["foods"].append({
                    "id": record_id(username, batch, f"food:{meal}", day, index),
                    "date": day, "time": when, "meal": meal,
                    "name": f"{food['name']}（测试）", "amount": amount, "unit": food["unit"],
                    **{key: round(food[key] * factor, 2) for key in ("kcal", "carb", "protein", "fat")},
                    "nutritionSource": "knowledge_base", "nutritionRef": food.get("source"), "nutritionConfidence": "high",
                    "nutritionNote": "【测试数据】按本地食物知识库估算",
                    "testData": True, "testBatch": batch,
                })
                counts["foods"] += 1

        if offset % 2 == 0 and day not in existing_workouts and day != today.isoformat():
            title, rpe, exercise_plan = WORKOUTS[(offset // 2) % len(WORKOUTS)]
            exercises = [{"name": name, "weight": weight, "sets": sets, "reps": reps, "volume": weight * sets * reps}
                         for name, weight, sets, reps in exercise_plan]
            data["workouts"].append({
                "id": record_id(username, batch, "workout", day, 0),
                "date": day, "type": "力量训练", "note": f"【测试数据】{title}示例训练",
                "rpe": rpe, "exercises": exercises,
                "volume": sum(item["volume"] for item in exercises),
                "calories": calories(exercises, rpe, body_weight), "calorieSource": "estimated",
                "testData": True, "testBatch": batch,
            })
            counts["workouts"] += 1

        if day not in existing_expenses:
            category, title, amount = OTHER_EXPENSES[offset % len(OTHER_EXPENSES)]
            for index, (expense_category, expense_title, expense_amount) in enumerate([
                ("餐饮", "测试·工作日午餐", 22 + offset % 5 * 2),
                (category, f"测试·{title}", amount),
            ]):
                data["expenses"].append({
                    "id": record_id(username, batch, "expense", day, index),
                    "createdAt": f"{day}T12:30:00+08:00", "date": day,
                    "amount": expense_amount, "category": expense_category,
                    "payment": "微信", "title": expense_title,
                    "note": "【测试数据】可按测试标记识别", "testData": True, "testBatch": batch,
                })
                counts["expenses"] += 1
    normalize_state_records(data, username, previous=previous)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True, help="已存在的用户名，不会创建新账号")
    args = parser.parse_args()
    if not DB_PATH.is_file():
        raise SystemExit(f"数据库不存在：{DB_PATH}")
    today = date.today()
    backup = DB_PATH.with_name(f"{DB_PATH.name}.pre-demo-{args.username}-{datetime.now():%Y%m%d-%H%M%S}.bak")
    with sqlite3.connect(DB_PATH, timeout=10) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT username FROM users WHERE username = ? COLLATE NOCASE", (args.username,)).fetchone()
        if row is None:
            raise SystemExit(f"账号不存在：{args.username}")
        username = row["username"]
        with sqlite3.connect(backup) as backup_db:
            db.backup(backup_db)
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data_json, updated_at FROM user_data WHERE username = ?", (username,)).fetchone()
            if row is None:
                raise ValueError("账号数据行不存在")
            data = json.loads(row["data_json"] or "{}")
            if not isinstance(data, dict):
                raise ValueError("账号数据不是对象")
            counts = add_demo_data(data, username, today)
            if any(counts.values()):
                revision = max(int(time.time() * 1000), row["updated_at"] + 1)
                db.execute("UPDATE user_data SET data_json = ?, updated_at = ? WHERE username = ?",
                           (json.dumps(data, ensure_ascii=False, separators=(",", ":")), revision, username))
            db.commit()
        except Exception:
            db.rollback()
            raise
    print(json.dumps({"username": username, "start": (today - timedelta(days=13)).isoformat(),
                      "end": today.isoformat(), "added": counts, "backup": str(backup)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
