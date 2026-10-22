"""Contract checks for replies produced by the health assistant."""

import unittest

from agent_response import validate_agent_response


VALID_REPLY = {
    "category": "nutrition",
    "answer": "记录显示今天蛋白质摄入较低。",
    "evidence": ["2026-10-20 饮食记录：蛋白质 50 g"],
    "recordIds": ["f1"],
    "actions": ["下一餐加入一种富含蛋白质的食物。"],
    "uncertainty": "早餐未记录，不能推断全天摄入量。",
    "urgency": "routine",
}


class AgentResponseTests(unittest.TestCase):
    def test_accepts_complete_reply(self):
        self.assertEqual(validate_agent_response(VALID_REPLY), VALID_REPLY)

    def test_rejects_missing_and_unknown_fields(self):
        with self.assertRaises(ValueError):
            validate_agent_response({key: value for key, value in VALID_REPLY.items() if key != "urgency"})
        with self.assertRaises(ValueError):
            validate_agent_response({**VALID_REPLY, "extra": "not allowed"})

    def test_rejects_wrong_types_and_enums(self):
        with self.assertRaises(ValueError):
            validate_agent_response({**VALID_REPLY, "category": "diagnosis"})
        with self.assertRaises(ValueError):
            validate_agent_response({**VALID_REPLY, "actions": "eat more"})
        with self.assertRaises(ValueError):
            validate_agent_response({**VALID_REPLY, "evidence": [""]})


if __name__ == "__main__":
    unittest.main()
