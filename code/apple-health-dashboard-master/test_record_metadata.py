"""Metadata backfill and record-level provenance contracts."""

import copy
import unittest

from record_metadata import normalize_state_records


class RecordMetadataTests(unittest.TestCase):
    def test_backfill_keeps_values_and_includes_generated_records(self):
        data = {
            "foods": [
                {"id": "food-1", "date": "2026-10-20", "name": "米饭", "kcal": 116, "nutritionSource": "knowledge"},
                {"id": "food-2", "date": "2026-10-20", "name": "馒头（测试）", "kcal": 223, "testData": True, "nutritionSource": "knowledge_base"},
            ],
            "workouts": [{"id": "workout-1", "date": "2026-10-19", "calories": 221, "calorieSource": "estimated"}],
            "expenses": [{"id": "expense-1", "date": "2026-10-20", "amount": 25, "createdAt": "2026-10-20T12:00:00Z"}],
        }
        original = copy.deepcopy(data)
        result, counts = normalize_state_records(data, "test-user", migration=True)
        self.assertEqual(counts, {"foods": 2, "workouts": 1, "expenses": 1})
        for collection in original:
            self.assertEqual(len(result[collection]), len(original[collection]))
            for before, after in zip(original[collection], result[collection]):
                self.assertEqual({k: v for k, v in after.items() if k != "meta"}, before)
                self.assertEqual(after["meta"]["ownerUsername"], "test-user")
                self.assertEqual(after["meta"]["version"], 1)
        self.assertEqual(result["foods"][1]["meta"]["entryMethod"], "generated_test")
        self.assertEqual(result["foods"][0]["meta"]["createdAt"], None)
        self.assertEqual(result["workouts"][0]["meta"]["valueSource"], "formula_estimate")
        self.assertEqual(result["expenses"][0]["meta"]["createdAt"], "2026-10-20T12:00:00Z")

    def test_unchanged_records_keep_metadata_and_edits_advance_version(self):
        previous = {"foods": [{"id": "food-1", "name": "豆腐", "kcal": 80, "nutritionSource": "knowledge_base",
                              "nutritionRef": {"sheet": "19-食物", "cell": "D1"}}]}
        normalize_state_records(previous, "test-user", timestamp="2026-10-20T10:00:00Z")
        unchanged = copy.deepcopy(previous)
        unchanged["foods"][0]["meta"]["ownerUsername"] = "other-user"
        normalize_state_records(unchanged, "test-user", previous=previous, timestamp="2026-10-21T10:00:00Z")
        self.assertEqual(unchanged["foods"][0]["meta"], previous["foods"][0]["meta"])
        self.assertEqual(unchanged["foods"][0]["meta"]["sourceRef"], {"sheet": "19-食物", "cell": "D1"})

        edited = copy.deepcopy(previous)
        edited["foods"][0]["kcal"] = 100
        normalize_state_records(edited, "test-user", previous=previous, timestamp="2026-10-21T10:00:00Z")
        self.assertEqual(edited["foods"][0]["meta"]["version"], 2)
        self.assertEqual(edited["foods"][0]["meta"]["createdAt"], previous["foods"][0]["meta"]["createdAt"])
        self.assertEqual(edited["foods"][0]["meta"]["updatedAt"], "2026-10-21T10:00:00Z")

    def test_backfill_is_idempotent_and_duplicate_ids_are_rejected(self):
        data = {"foods": [{"id": "a", "nutritionSource": "ai_estimate"}]}
        normalize_state_records(data, "test-user", migration=True)
        _, counts = normalize_state_records(data, "test-user", migration=True)
        self.assertEqual(counts["foods"], 0)
        with self.assertRaises(ValueError):
            normalize_state_records({"foods": [{"id": "a"}, {"id": "a"}]}, "test-user", migration=True)


if __name__ == "__main__":
    unittest.main()
