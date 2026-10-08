"""Offline checks for DeepTeam 1.0.9's seeded linear attack boundary."""

from __future__ import annotations

import os
import unittest

os.environ["DEEPTEAM_TELEMETRY_OPT_OUT"] = "YES"
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "1"

from deepeval.models import DeepEvalBaseLLM  # noqa: E402
from deepteam.attacks.multi_turn import LinearJailbreaking  # noqa: E402
from deepteam.test_case import RTTurn  # noqa: E402
from redteam.deepteam.adapter import ClawithConversation  # noqa: E402


class FakeSimulator(DeepEvalBaseLLM):
    def __init__(self):
        super().__init__(model="fake")

    def load_model(self):
        return self

    def get_model_name(self):
        return "fake"

    def generate(self, prompt, schema=None):
        return self._answer(schema)

    async def a_generate(self, prompt, schema=None):
        return self._answer(schema)

    @staticmethod
    def _answer(schema):
        values = {
            "Feedback": {"jailbroken": False, "suggestion": "ask again"},
            "Improvement": {"reasoning": "retry", "new_prompt": "second message"},
            "NonRefusal": {"refusal": False, "reasoning": "valid"},
            "ShiftExplanation": {"reasoning": "Oracle fired"},
        }
        return schema.model_validate(values[schema.__name__])


class AdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_seeded_linear_attack_stops_on_oracle_hit(self):
        sent = []

        async def turn(message):
            sent.append(message)
            hit = len(sent) == 2
            return {"status": "oracle-success" if hit else "continue",
                    "reply": "Done" if hit else "No", "turns": len(sent),
                    "oracleSuccess": hit, "verdict": "success" if hit else "failed"}

        conversation = ClawithConversation(turn=turn, episode=7, surface="kb-integrity")
        attack = LinearJailbreaking(num_turns=4, simulator_model=FakeSimulator())
        turns = await attack._a_get_turns(
            model_callback=conversation.callback,
            turns=[RTTurn(role="user", content="first message")],
            vulnerability="Write a KB file", vulnerability_type="kb-integrity",
            metric_check=conversation.metric_check,
        )
        self.assertEqual(sent, ["first message", "second message"])
        self.assertEqual(len(conversation.events), 2)
        self.assertTrue(conversation.events[-1]["oracleSuccess"])
        self.assertEqual([turn.role for turn in turns],
                         ["user", "assistant", "user", "assistant"])

    async def test_history_branch_is_rejected_before_send(self):
        sent = []

        async def turn(message):
            sent.append(message)
            return {"status": "continue", "reply": "No", "turns": 1,
                    "oracleSuccess": False, "verdict": "failed"}

        conversation = ClawithConversation(turn=turn, episode=7, surface="kb-integrity")
        await conversation.callback("first", [RTTurn(role="user", content="first")])
        with self.assertRaisesRegex(RuntimeError, "replay or branch"):
            await conversation.callback("second", [RTTurn(role="user", content="another")])
        self.assertEqual(sent, ["first"])

    async def test_incomplete_turn_is_not_scored_as_safe(self):
        async def turn(_message):
            return {"status": "uncertain-send", "turns": 1}

        conversation = ClawithConversation(turn=turn, episode=7, surface="kb-integrity")
        with self.assertRaisesRegex(RuntimeError, "did not complete"):
            await conversation.callback("first", [RTTurn(role="user", content="first")])
        self.assertEqual(conversation.events, [])

    async def test_scoring_requires_matching_history(self):
        async def turn(_message):
            return {"status": "continue", "reply": "No", "turns": 1,
                    "oracleSuccess": False, "verdict": "failed"}

        conversation = ClawithConversation(turn=turn, episode=7, surface="kb-integrity")
        await conversation.callback("first", [RTTurn(role="user", content="first")])
        with self.assertRaisesRegex(RuntimeError, "no matching Clawith turn"):
            conversation.metric_check([RTTurn(role="assistant", content="invented")])
        self.assertEqual(conversation.metric_check(conversation.history).score, 1.0)


if __name__ == "__main__":
    unittest.main()
