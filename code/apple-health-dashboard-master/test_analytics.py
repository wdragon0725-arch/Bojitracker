"""Metric dictionary, missingness, account isolation, evidence and API tests."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import server
from analytics import agent_context, analyze, profile_targets
from metric_definitions import METRIC_BY_ID, catalog
from migrate_records import migrate
from record_store import insert_record


class AnalyticsTests(unittest.TestCase):
    def test_dictionary_is_unique_and_profile_formula(self):
        self.assertEqual(len(METRIC_BY_ID), len(catalog()["metrics"]))
        for definition in catalog()["metrics"]:
            self.assertIn("missingPolicy", definition)
            self.assertIn("formula", definition)
            self.assertIn("unit", definition)
        target = profile_targets({"weight": "70", "height": "175", "age": "30", "sex": "male",
                                  "goal": "lose", "trainingDays": "4"})
        self.assertGreater(target["calories"], 1000)
        self.assertEqual(target["protein"], 126)
        self.assertIsNone(profile_targets({"weight": "70"}))

    def test_reports_and_http_routes(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / "analytics.db"
            with patch.object(server, "DB_PATH", path):
                server.init_db()
                profile = {"weight": "70", "height": "175", "age": "30", "sex": "male",
                           "goal": "lose", "trainingDays": "4"}
                with server.connect() as db:
                    for username in ("alice", "bob"):
                        db.execute("INSERT INTO users VALUES (?,?,?,?)", (username, "hash", "salt", 1))
                        db.execute("INSERT INTO user_data VALUES (?,?,?)", (username, json.dumps({"profile": profile}), 100))
                migrate(path)
                with server.connect() as db:
                    empty = analyze(db, "alice", 7)
                    self.assertIsNone(empty["metrics"]["food.kcal_total"]["value"])
                    self.assertIsNone(empty["metrics"]["expense.total"]["value"])
                    self.assertEqual(empty["metrics"]["food.recorded_days"]["value"], 0)
                    today = date.today().isoformat()
                    insert_record(db, "alice", "food", {"id": "f1", "date": today, "meal": "午餐", "name": "米饭",
                        "amount": 100, "unit": "g", "kcal": 116, "carb": 26, "protein": 2.6, "fat": .3,
                        "nutritionSource": "knowledge_base"})
                    insert_record(db, "alice", "food", {"id": "f2", "date": today, "meal": "晚餐", "name": "鸡蛋",
                        "amount": 100, "unit": "g", "kcal": 140, "carb": 1, "protein": 12, "fat": 10,
                        "testData": True, "nutritionSource": "ai_estimate"})
                    insert_record(db, "alice", "expense", {"id": "e1", "date": today, "title": "午餐", "category": "餐饮", "amount": 20})
                    insert_record(db, "alice", "workout", {"id": "w1", "date": today, "type": "力量训练",
                        "rpe": 7, "volume": 960, "calories": 150, "exercises": [{"name": "深蹲", "weight": 40,
                        "sets": 3, "reps": 8, "volume": 960}]})
                with server.connect() as db:
                    report = analyze(db, "alice", 7)
                    m = report["metrics"]
                    self.assertEqual(m["food.kcal_total"]["value"], 256)
                    self.assertEqual(m["food.protein_avg_recorded_day"]["value"], 14.6)
                    self.assertEqual(m["training.sessions"]["value"], 1)
                    self.assertEqual(m["training.volume_kg"]["value"], 960)
                    self.assertEqual(m["expense.total"]["value"], 20)
                    self.assertEqual(set(report["evidence"]["food"]), {"f1", "f2"})
                    self.assertEqual(report["sourceBreakdown"]["food"]["ai_estimate"], 1)
                    self.assertNotIn("summary", agent_context(report))
                    filtered = analyze(db, "alice", 7, include_demo=False)
                    self.assertEqual(filtered["metrics"]["food.kcal_total"]["value"], 116)
                    self.assertEqual(filtered["evidence"]["food"], ["f1"])
                    bob = analyze(db, "bob", 7)
                    self.assertIsNone(bob["metrics"]["food.kcal_total"]["value"])

                handler = object.__new__(server.BojiHandler)
                handler.session_username = lambda: "alice"
                handler.path = "/api/analytics?days=7"
                responses = []
                handler.json_response = lambda payload, status=200, cookie=None: responses.append((status, payload))
                handler.handle_analytics("/api/analytics")
                self.assertEqual(responses[-1][1]["report"]["metrics"]["food.kcal_total"]["value"], 256)
                handler.handle_analytics("/api/analytics/metrics")
                self.assertEqual(responses[-1][1]["dictionaryVersion"], 1)


if __name__ == "__main__":
    unittest.main()
