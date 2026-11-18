"""Provider-compatible wait schemas and human-only mention normalization."""
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.services.agent_runtime.model_step_service import (
    _RUNTIME_WAIT_TOOL_DEFINITION, _parse_step, _resolve_group_mentions,
)
from app.services.llm.single_step import LLMCompletionStep

ALICE, MORTY, OTHER = (str(uuid.UUID(int=n)) for n in (1, 2, 3))


class GroupMentionTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, content, ids=(), members=None):
        db = SimpleNamespace(execute=AsyncMock(return_value=Mock(all=Mock(return_value=
            members or [(ALICE, "alice", "user"), (MORTY, "Morty", "agent")]))))
        state = {"snapshots": SimpleNamespace(initial_input={"group_id": OTHER})}
        return await _resolve_group_mentions(db, state=state, content=content, mention_participant_ids=ids)

    async def test_staged_human_gets_visible_mention(self):
        content, ids, missing_ids, missing_text = await self.resolve("Here are the details.", (ALICE,))
        self.assertEqual(content, "@alice Here are the details.")
        self.assertEqual(ids, (ALICE,))
        self.assertEqual((missing_ids, missing_text), ((), ()))

    async def test_visible_human_resolves_without_a_model_routing_call(self):
        content, ids, missing_ids, missing_text = await self.resolve("@alice please confirm.")
        self.assertEqual(content, "@alice please confirm.")
        self.assertEqual(ids, (ALICE,))
        self.assertEqual((missing_ids, missing_text), ((), ()))

    async def test_human_resolution_is_idempotent(self):
        first = await self.resolve("@alice please confirm.", (ALICE,))
        self.assertEqual(await self.resolve(first[0], first[1]), first)

    async def test_agent_requires_explicit_routing_and_visible_request(self):
        self.assertEqual((await self.resolve("@Morty please respond."))[2], ("Morty",))
        self.assertEqual((await self.resolve("Here are the details.", (MORTY,)))[3], ("Morty",))
        self.assertEqual((await self.resolve("@Morty please respond.", (MORTY,)))[2:], ((), ()))

    async def test_mixed_mentions_do_not_implicitly_wake_agents(self):
        content, ids, missing_ids, _ = await self.resolve("@alice ask @Morty for details.")
        self.assertEqual(ids, (ALICE,))
        self.assertEqual(missing_ids, ("Morty",))

    async def test_ambiguous_names_require_explicit_participant_ids(self):
        members = [(ALICE, "alice", "user"), (OTHER, "alice", "user")]
        _, ids, missing, _ = await self.resolve("@alice please confirm.", members=members)
        self.assertEqual(ids, ())
        self.assertEqual(missing, ("alice",))

    async def test_email_code_and_unknown_names_do_not_create_mentions(self):
        for content in ("Contact person@alice.example", "Use `@alice` in the example.",
                        "```text\n@alice\n```", "@unknown please confirm."):
            self.assertEqual((await self.resolve(content))[1:], ((), (), ()))


class WaitTests(unittest.TestCase):
    def parse(self, arguments, allow_user_wait=True):
        step = LLMCompletionStep(content=None, reasoning_content=None, retry_instruction=None, usage=None,
            tool_calls=({"id": "wait-call", "type": "function", "function": {
                "name": "wait", "arguments": json.dumps(arguments)}},))
        return _parse_step({"lifecycle": {}}, SimpleNamespace(run_id=OTHER), step,
            allowed_tool_names=frozenset({"wait"}), allow_user_wait=allow_user_wait, allow_group_handoff=False)

    def test_schema_has_no_provider_forbidden_top_level_combinators(self):
        parameters = _RUNTIME_WAIT_TOOL_DEFINITION["function"]["parameters"]
        self.assertEqual(parameters["type"], "object")
        self.assertTrue(set(parameters).isdisjoint({"oneOf", "anyOf", "allOf", "enum", "const", "not"}))

    def test_user_wait_still_requires_a_question_at_runtime(self):
        result = self.parse({"waiting_type": "user", "reason": "Need approval"})
        self.assertEqual(result.repair_code, "invalid_wait")
        result = self.parse({"waiting_type": "user", "reason": "Need approval", "question": "Approve?"})
        self.assertEqual(result.intent, "wait")
        self.assertEqual(result.waiting_request["question"], "Approve?")

    def test_agent_wait_does_not_require_a_user_question(self):
        result = self.parse({"waiting_type": "agent", "reason": "Waiting for the other agent"})
        self.assertEqual(result.intent, "wait")

    def test_group_user_wait_remains_disallowed(self):
        result = self.parse({"waiting_type": "user", "reason": "Need approval", "question": "Approve?"}, False)
        self.assertEqual(result.intent, "text")
        self.assertIn("cannot enter waiting_user", result.repair_instruction)


if __name__ == "__main__":
    unittest.main()
