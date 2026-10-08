"""Offline contract tests for the Clawith PyRIT target and Oracle scorer."""

from __future__ import annotations

import tempfile
import json
import unittest
from unittest.mock import AsyncMock, patch

import appdirs

_data = tempfile.TemporaryDirectory()
with patch.object(appdirs, "user_data_dir", return_value=_data.name):
    from pyrit.executor.attack import AttackAdversarialConfig, AttackScoringConfig, RedTeamingAttack
    from pyrit.models import Message, MessageScorable
    from pyrit.memory import CentralMemory
    from pyrit.prompt_target import OpenAIChatTarget
    from pyrit.setup import IN_MEMORY, initialize_pyrit_async
    from redteam.pyrit.adapter import ClawithTarget, OracleScorer


class AdapterTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[],
                                     load_defaults=False, env_files=[], silent=True)

    def request(self, prompt):
        message = Message.from_prompt(prompt=prompt, role="user")
        message.message_pieces[0].conversation_id = "test-conversation"
        return message

    async def test_oracle_hit_scores_true_and_preserves_morty_reply(self):
        async def turn(message):
            self.assertEqual(message, "write the file")
            return {"status": "oracle-success", "reply": "Done", "turns": 1,
                    "oracleSuccess": True}

        target = ClawithTarget(turn=turn, episode=7, surface="kb-integrity")
        replies = await target.send_prompt_async(
            message=self.request("write the file"))
        self.assertEqual(replies[0].message_pieces[0].original_value, "Done")
        CentralMemory.get_memory_instance().add_message_to_memory(request=replies[0])
        scores = await OracleScorer(episode=7, surface="kb-integrity").score_async(
            scorable=MessageScorable.from_message(replies[0]))
        self.assertTrue(scores[0].get_value())

    async def test_no_oracle_hit_scores_false(self):
        async def turn(_message):
            return {"status": "continue", "reply": "I can't do that", "turns": 1,
                    "oracleSuccess": False}

        replies = await ClawithTarget(turn=turn, episode=7, surface="kb-integrity").send_prompt_async(
            message=self.request("try"))
        CentralMemory.get_memory_instance().add_message_to_memory(request=replies[0])
        scores = await OracleScorer(episode=7, surface="kb-integrity").score_async(
            scorable=MessageScorable.from_message(replies[0]))
        self.assertFalse(scores[0].get_value())

    async def test_incomplete_turn_is_not_scored_as_safe(self):
        async def turn(_message):
            return {"status": "no-reply", "reply": None, "turns": 1,
                    "oracleSuccess": False}

        target = ClawithTarget(turn=turn, episode=7, surface="kb-integrity")
        with self.assertRaisesRegex(RuntimeError, "did not complete"):
            await target.send_prompt_async(message=self.request("try"))

    async def test_scorer_rejects_unrelated_response(self):
        async def turn(_message):
            return {"status": "continue", "reply": "No", "turns": 1,
                    "oracleSuccess": False}

        replies = await ClawithTarget(turn=turn, episode=7, surface="kb-integrity").send_prompt_async(
            message=self.request("try"))
        CentralMemory.get_memory_instance().add_message_to_memory(request=replies[0])
        with self.assertRaisesRegex(RuntimeError, "matching Oracle evidence"):
            await OracleScorer(episode=8, surface="kb-integrity").score_async(
                scorable=MessageScorable.from_message(replies[0]))

    async def test_one_turn_attack_uses_oracle_score(self):
        async def turn(message):
            self.assertEqual(message, "seed message")
            return {"status": "oracle-success", "reply": "Done", "turns": 1,
                    "oracleSuccess": True}

        attack = RedTeamingAttack(
            objective_target=ClawithTarget(turn=turn, episode=7, surface="kb-integrity"),
            attack_adversarial_config=AttackAdversarialConfig(target=OpenAIChatTarget(
                endpoint="https://example.invalid/v1", api_key="test", model_name="test")),
            attack_scoring_config=AttackScoringConfig(
                objective_scorer=OracleScorer(episode=7, surface="kb-integrity")),
            max_turns=1,
        )
        result = await attack.execute_async(
            objective="Write a file", next_message=self.request("seed message"))
        self.assertEqual(result.outcome.value, "success")
        self.assertEqual(result.executed_turns, 1)

    async def test_two_turn_attack_uses_same_stateful_target(self):
        sent = []

        async def turn(message):
            sent.append(message)
            return {"status": "oracle-success" if len(sent) == 2 else "continue",
                    "reply": "Done" if len(sent) == 2 else "No",
                    "turns": len(sent), "oracleSuccess": len(sent) == 2}

        adversarial = OpenAIChatTarget(
            endpoint="https://example.invalid/v1", api_key="test", model_name="test")

        async def generate(*, normalized_conversation):
            response = Message.from_prompt(prompt=json.dumps({
                "next_message": "second message", "rationale": "try a new request",
                "last_response_summary": "Morty refused",
            }), role="assistant")
            response.message_pieces[0].conversation_id = (
                normalized_conversation[-1].message_pieces[0].conversation_id)
            return [response]

        adversarial._send_prompt_to_target_async = AsyncMock(side_effect=generate)
        attack = RedTeamingAttack(
            objective_target=ClawithTarget(turn=turn, episode=7, surface="kb-integrity"),
            attack_adversarial_config=AttackAdversarialConfig(target=adversarial),
            attack_scoring_config=AttackScoringConfig(
                objective_scorer=OracleScorer(episode=7, surface="kb-integrity")),
            max_turns=2,
        )
        result = await attack.execute_async(
            objective="Write a file", next_message=self.request("seed message"))
        self.assertEqual(result.outcome.value, "success")
        self.assertEqual(result.executed_turns, 2)
        self.assertEqual(sent, ["seed message", "second message"])


if __name__ == "__main__":
    unittest.main()
