"""Protect account data from stale browser tabs overwriting newer records."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class FakeRequest:
    def __init__(self, expected, data):
        self.body = {"data": data, "expectedUpdatedAt": expected}
        self.result = None

    def session_username(self):
        return "test-user"

    def read_json(self):
        return self.body

    def json_response(self, payload, status=200):
        self.result = (status, payload)


class DataRevisionTests(unittest.TestCase):
    def test_stale_save_is_rejected_and_new_data_preserved(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            with patch.object(server, "DB_PATH", Path(directory) / "test.db"):
                server.init_db()
                with server.connect() as db:
                    db.execute("INSERT INTO users VALUES (?, ?, ?, ?)", ("test-user", "hash", "salt", 1))
                    db.execute("INSERT INTO user_data VALUES (?, ?, ?)", ("test-user", json.dumps({"foods": [{"id": "new"}]}), 100))

                stale = FakeRequest(99, {"foods": []})
                server.BojiHandler.handle_save(stale)
                self.assertEqual(stale.result[0], 409)
                with server.connect() as db:
                    row = db.execute("SELECT data_json, updated_at FROM user_data WHERE username = 'test-user'").fetchone()
                    self.assertEqual(json.loads(row["data_json"])["foods"][0]["id"], "new")
                    self.assertEqual(row["updated_at"], 100)

                current = FakeRequest(100, {"foods": [{"id": "new"}, {"id": "later"}]})
                server.BojiHandler.handle_save(current)
                self.assertEqual(current.result[0], 200)
                self.assertGreater(current.result[1]["updatedAt"], 100)
                with server.connect() as db:
                    foods = json.loads(db.execute("SELECT data_json FROM user_data WHERE username = 'test-user'").fetchone()["data_json"])["foods"]
                    self.assertEqual([item["meta"]["ownerUsername"] for item in foods], ["test-user", "test-user"])
                    self.assertEqual(foods[0]["meta"]["version"], 1)

                missing = FakeRequest(None, {"foods": []})
                server.BojiHandler.handle_save(missing)
                self.assertEqual(missing.result[0], 400)


if __name__ == "__main__":
    unittest.main()
