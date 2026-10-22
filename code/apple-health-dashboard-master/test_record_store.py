"""Migration, per-record writes, versions, links, summaries and audit trail."""

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import server
from migrate_records import migrate
from record_store import RecordConflict, delete_record, get_record, insert_record, list_records, summaries, update_record


class RecordStoreTests(unittest.TestCase):
    def test_record_http_routes_and_conflict(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / "api.db"
            with patch.object(server, "DB_PATH", path):
                server.init_db()
                with server.connect() as db:
                    db.execute("INSERT INTO users VALUES (?,?,?,?)", ("tester", "hash", "salt", 1))
                    db.execute("INSERT INTO user_data VALUES (?,?,?)", ("tester", "{}", 100))
                migrate(path)

                def request(method, route, body=None):
                    handler = object.__new__(server.BojiHandler)
                    handler.command = method
                    handler.path = route
                    handler.session_username = lambda: "tester"
                    handler.read_json = lambda: body or {}
                    responses = []
                    handler.json_response = lambda payload, status=200, cookie=None: responses.append((status, payload))
                    handler.handle_records(route.split("?")[0])
                    return responses[-1]

                today = date.today().isoformat()
                status, payload = request("POST", "/api/records/food", {"record": {"id": "api-food", "date": today,
                    "meal": "午餐", "name": "米饭", "amount": 100, "unit": "g", "kcal": 116,
                    "carb": 26, "protein": 2.6, "fat": .3, "testData": True}})
                self.assertEqual(status, 201)
                self.assertEqual(payload["record"]["meta"]["version"], 1)
                status, payload = request("PATCH", "/api/records/food/api-food", {"expectedVersion": 1, "changes": {"kcal": 120}})
                self.assertEqual((status, payload["record"]["meta"]["version"]), (200, 2))
                status, payload = request("PATCH", "/api/records/food/api-food", {"expectedVersion": 1, "changes": {"kcal": 999}})
                self.assertEqual((status, payload["currentVersion"]), (409, 2))
                status, payload = request("GET", "/api/records/summary?days=7")
                self.assertEqual((status, payload["summary"]["food"]["totals"]["kcal"]), (200, 120))
                status, payload = request("GET", "/api/records/summary?days=7&includeDemo=0")
                self.assertEqual((status, payload["summary"]["food"]["recordCount"]), (200, 0))
                status, payload = request("GET", "/api/records/food/api-food/events")
                self.assertEqual((status, [event["event_type"] for event in payload["events"]]), (200, ["create", "update"]))
                status, _ = request("DELETE", "/api/records/food/api-food", {"expectedVersion": 2})
                self.assertEqual(status, 200)
                status, payload = request("GET", "/api/records/food/api-food")
                self.assertEqual(status, 404)

    def test_migration_and_record_lifecycle(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / "boji.db"
            with patch.object(server, "DB_PATH", path):
                server.init_db()
                old = {
                    "profile": {"name": "Tester"},
                    "foods": [{"id": "f1", "date": "2026-10-20", "meal": "午餐", "name": "米饭", "amount": 100,
                               "unit": "g", "kcal": 116, "carb": 26, "protein": 2.6, "fat": .3,
                               "nutritionSource": "knowledge_base", "linkedExpenseId": "e1", "testData": True}],
                    "workouts": [{"id": "w1", "date": "2026-10-20", "type": "力量训练", "rpe": 7,
                                  "volume": 960, "calories": 150, "calorieSource": "estimated",
                                  "exercises": [{"name": "深蹲", "weight": 40, "sets": 3, "reps": 8, "volume": 960}]}],
                    "expenses": [{"id": "e1", "date": "2026-10-20", "amount": 25, "category": "餐饮", "payment": "微信", "title": "午餐", "note": ""}],
                }
                with server.connect() as db:
                    db.execute("INSERT INTO users VALUES (?,?,?,?)", ("tester", "hash", "salt", 1))
                    db.execute("INSERT INTO user_data VALUES (?,?,?)", ("tester", json.dumps(old, ensure_ascii=False), 100))
                result = migrate(path)
                self.assertEqual(result["status"], "migrated")
                self.assertTrue(Path(result["backup"]).is_file())
                self.assertEqual(migrate(path)["status"], "already_current")
                with server.connect() as db:
                    self.assertEqual(json.loads(db.execute("SELECT data_json FROM user_data WHERE username='tester'").fetchone()[0]), old)
                    self.assertEqual([len(list_records(db, "tester", kind)) for kind in ("food", "workout", "expense")], [1, 1, 1])
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM exercise_sets").fetchone()[0], 1)
                    self.assertEqual(db.execute("SELECT linked_expense_id FROM food_records WHERE record_id='f1'").fetchone()[0], "e1")
                    self.assertEqual(len(list_records(db, "tester", "food", include_demo=False)), 0)
                    self.assertEqual(summaries(db, "tester", 7)["includeDemo"], True)
                    self.assertIsNone(get_record(db, "another", "food", "f1"))

                with server.connect() as db:
                    created = insert_record(db, "tester", "expense", {"id": "e2", "date": "2026-10-21", "amount": 12.5,
                                                                        "category": "交通", "title": "地铁", "payment": "微信"})
                    self.assertEqual(created["meta"]["version"], 1)
                    edited = update_record(db, "tester", "expense", "e2", {"amount": 15}, 1)
                    self.assertEqual(edited["meta"]["version"], 2)
                    with self.assertRaises(RecordConflict):
                        update_record(db, "tester", "expense", "e2", {"amount": 99}, 1)
                    with self.assertRaises(RecordConflict):
                        delete_record(db, "tester", "expense", "e2", 1)
                    self.assertTrue(delete_record(db, "tester", "expense", "e2", 2))
                    self.assertIsNone(get_record(db, "tester", "expense", "e2"))
                    events = db.execute("SELECT event_type FROM record_events WHERE record_id='e2' ORDER BY id").fetchall()
                    self.assertEqual([row[0] for row in events], ["create", "update", "delete"])
                    self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
