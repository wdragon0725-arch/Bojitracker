"""Read-only tool orchestration, ownership and evidence validation."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import server
from agent_orchestrator import run_agent
from agent_tools import execute_tool
from migrate_records import migrate
from record_store import insert_record


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class AgentOrchestrationTests(unittest.TestCase):
    def test_tool_loop_and_cross_account_protection(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / "agent.db"
            with patch.object(server, "DB_PATH", path):
                server.init_db()
                with server.connect() as db:
                    for username in ("alice", "bob"):
                        db.execute("INSERT INTO users VALUES (?,?,?,?)", (username, "hash", "salt", 1))
                        db.execute("INSERT INTO user_data VALUES (?,?,?)", (username, "{}", 1))
                migrate(path)
                with server.connect() as db:
                    insert_record(db, "alice", "food", {"id": "f1", "date": date.today().isoformat(),
                        "name": "米饭", "meal": "午餐", "amount": 100, "unit": "g", "kcal": 116,
                        "carb": 26, "protein": 2.6, "fat": .3})
                    insert_record(db, "alice", "workout", {"id": "w1", "date": date.today().isoformat(),
                        "type": "力量训练", "rpe": 7, "volume": 960, "calories": 150,
                        "exercises": [{"name": "深蹲", "weight": 40, "sets": 3, "reps": 8, "volume": 960}]})
                with server.connect() as db:
                    self.assertEqual(execute_tool(db, "bob", "get_record", {"kind": "food", "record_id": "f1"})["status"], "not_found")
                    self.assertIsNone(execute_tool(db, "bob", "get_metric", {"metric_id": "food.protein_total", "days": 7})["metric"]["value"])
                    with self.assertRaises(ValueError):
                        execute_tool(db, "bob", "get_record", {"kind": "food", "record_id": "f1", "username": "alice"})
                    self.assertEqual(execute_tool(db, "bob", "get_workout_history", {"days": 7})["sessions"], [])
                    workouts = execute_tool(db, "alice", "get_workout_history", {"days": 7})
                    self.assertEqual(workouts["recordIds"], ["w1"])
                    self.assertEqual(workouts["sessions"][0]["exercises"][0]["name"], "深蹲")
                    self.assertEqual(workouts["sessions"][0]["exercises"][0]["sets"], 3)

                    tool_call = {"id": "call-1", "type": "function", "function": {"name": "get_metric",
                                 "arguments": json.dumps({"metric_id": "food.protein_total", "days": 7})}}
                    response = {"category": "nutrition", "answer": "有记录日的蛋白质为 2.6 g。",
                                "evidence": ["今日饮食记录显示 2.6 g 蛋白质"], "recordIds": ["f1"],
                                "actions": [], "uncertainty": "只记录了一餐，不能代表全天。", "urgency": "routine"}
                    outputs = [
                        {"choices": [{"message": {"content": None, "tool_calls": [tool_call]}, "finish_reason": "tool_calls"}]},
                        {"choices": [{"message": {"content": json.dumps(response)}, "finish_reason": "stop"}], "model": "mock-model"},
                    ]
                    requests = []

                    def fake_request(request, timeout):
                        requests.append(json.loads(request.data))
                        return FakeResponse(outputs.pop(0))

                    result = run_agent(db, "alice", [{"role": "user", "content": "我最近蛋白质怎么样？"}],
                                       api_key="test-key", base_url="https://example.invalid", model="mock-model",
                                       request_fn=fake_request)
                    self.assertEqual(result["response"]["recordIds"], ["f1"])
                    self.assertEqual(result["toolTrace"][0]["tool"], "get_metric")
                    self.assertEqual(requests[1]["messages"][-1]["role"], "tool")
                    self.assertIn("f1", requests[1]["messages"][-1]["content"])
                    self.assertFalse(any(message.get("role") == "tool" for message in requests[0]["messages"]))

    def test_rejects_unauthorized_tool_and_fabricated_id(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / "agent.db"
            with patch.object(server, "DB_PATH", path):
                server.init_db()
                with server.connect() as db:
                    db.execute("INSERT INTO users VALUES (?,?,?,?)", ("alice", "hash", "salt", 1))
                    db.execute("INSERT INTO user_data VALUES (?,?,?)", ("alice", "{}", 1))
                migrate(path)
                with server.connect() as db:
                    unauthorized = {"choices": [{"message": {"tool_calls": [{"id": "x", "function":
                        {"name": "delete_record", "arguments": "{}"}}]}}]}
                    with self.assertRaisesRegex(ValueError, "未授权"):
                        run_agent(db, "alice", [{"role": "user", "content": "删除"}], api_key="test",
                                  base_url="https://example.invalid", model="mock",
                                  request_fn=lambda *_args, **_kwargs: FakeResponse(unauthorized))
                    fabricated = {"category": "nutrition", "answer": "有记录", "evidence": [], "recordIds": ["not-real"],
                                  "actions": [], "uncertainty": "", "urgency": "routine"}
                    with self.assertRaisesRegex(ValueError, "未由本轮工具"):
                        run_agent(db, "alice", [{"role": "user", "content": "近况"}], api_key="test",
                                  base_url="https://example.invalid", model="mock",
                                  request_fn=lambda *_args, **_kwargs: FakeResponse({"choices": [{"message": {"content": json.dumps(fabricated)}}]}))
                    unsupported = {**fabricated, "recordIds": [], "evidence": ["某日摄入了 2000 千卡"]}
                    with self.assertRaisesRegex(ValueError, "未调用数据工具"):
                        run_agent(db, "alice", [{"role": "user", "content": "近况"}], api_key="test",
                                  base_url="https://example.invalid", model="mock",
                                  request_fn=lambda *_args, **_kwargs: FakeResponse({"choices": [{"message": {"content": json.dumps(unsupported)}}]}))


if __name__ == "__main__":
    unittest.main()
