"""Knowledge-base-first lookup used by both manual food entry and expense sync."""

import json
import unittest
from unittest.mock import patch

import server


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FoodLookupTests(unittest.TestCase):
    def request(self, data):
        handler = object.__new__(server.BojiHandler)
        handler.session_username = lambda: "tester"
        handler.read_json = lambda: data
        responses = []
        handler.json_response = lambda payload, status=200, cookie=None: responses.append((status, payload))
        handler.handle_food_tool()
        return responses[-1]

    def test_knowledge_base_is_used_first(self):
        food = {"name": "馒头", "unit": "g", "kcal": 230, "carb": 48, "protein": 7, "fat": 1, "source": {"sheet": "食物", "cell": "A1"}}
        with patch.object(server, "find_knowledge_food", return_value=(food, 1.0)), \
             patch.object(server, "deepseek_api_key", side_effect=AssertionError("AI must not be called")):
            status, payload = self.request({"name": "馒头", "amount": 50})
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["kcal"], 115)
        self.assertEqual(payload["result"]["source"], "knowledge")
        self.assertEqual(payload["result"]["sourceRef"], {"sheet": "食物", "cell": "A1"})

    def test_unknown_food_uses_ai_fallback(self):
        model_payload = {"choices": [{"message": {"content": json.dumps({"name": "新食物", "kcal": 150,
            "carb": 20, "protein": 10, "fat": 4, "confidence": "low", "note": "按同类食物估算"})}}]}
        with patch.object(server, "find_knowledge_food", return_value=(None, .1)), \
             patch.object(server, "deepseek_api_key", return_value="test-key"), \
             patch.object(server, "urlopen", return_value=FakeResponse(model_payload)) as request:
            status, payload = self.request({"name": "新食物", "amount": 100})
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["source"], "ai_estimate")
        self.assertEqual(payload["result"]["kcal"], 150)
        request.assert_called_once()

    def test_unknown_food_without_ai_does_not_return_nutrition(self):
        with patch.object(server, "find_knowledge_food", return_value=(None, .1)), \
             patch.object(server, "deepseek_api_key", return_value=""):
            status, payload = self.request({"name": "新食物", "amount": 100})
        self.assertEqual(status, 503)
        self.assertNotIn("result", payload)


if __name__ == "__main__":
    unittest.main()
